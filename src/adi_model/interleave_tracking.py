"""interleave_tracking.py -- 专利 [12] US 10,707,889 B1 的跟踪相位机制。

============================================================================
专利披露的机制（机制级转述，原文不入库；见 NOTICE 引用原则）
============================================================================
[12] 面向 time-interleaved SAR ADC：多颗 sub-ADC 交替经历
ACQUISITION -> CONVERSION -> IDLE（或 TRACKING）三个相位，相位错开、
顺序可随机化。IDLE 相位有两种用法：

* **reset-to-midscale**（传统做法）：转换结束后把电容阵列复位到中点，
  重新进入采集时驱动器需提供 Q = A·C_IN（信号电平相对中点）的电荷；
  复位的动机是快速信号下"保持上一拍"反而更差——对 Nyquist 输入，
  保持前拍需 Q = 2·A·C_IN（[12] 背景段自己给出的算术）。
* **TRACKING（本专利核心）**：IDLE 相位改为**跟踪**——用**另一颗
  sub-ADC 的最近一次转换结果**（可叠加一个随机数字值做低-K 电容
  介电吸收的预失真；也可对最近 N 次转换加权，披露例 N=3、权重
  10%/30%/60%）更新本颗 DAC 的电容阵列。由于交织相位错开，"另一颗
  最近一拍"距今只有一个复合采样周期，进入采集时电容上已保持在前
  一样本电平附近 -> kickback 大幅减小 -> 驱动器/滤波器带宽可以放低
  -> 采样噪声更低、驱动功耗更低。

============================================================================
本实现的口径（勿夸大）
============================================================================
* 这是**机制级电荷核算模型**：核算"进入采集瞬间驱动器要补多少电荷"。
  **带宽/噪声收益不按电荷比例换算**（第八份外部复核订正）：相对建立
  约束下 f_min = ln(1/ε)/(2πT) 与电荷无关；绝对残差约束下电荷只进
  对数项（见 ``filter_bw_relative`` / ``filter_bw_absolute``）。
  电荷减少是带宽/噪声收益的**必要条件，不是换算系数**。本模块不模拟
  SAR 位判决回路（转换结果 = 该 sub-ADC 采样时刻的理想输入 + 可选
  量化噪声）。
* 只对复合采样率 f_s 建模；sub-ADC 采样率 = f_s / n_adcs。
* 参数分级：机制结构 = [披露]（[12]）；C_IN 默认锚到本仓库活跃采样
  电容（[推导]，c_active_nominal）；随机化幅度、预失真幅度、权重
  组合等数值全部 [假设]（[12] 只给了 N=3/10-30-60 这个例子）。
* 本模块是独立电路技术模型，**未接入** pipeline 主链路——接入与否
  属于发版纪律里"移动已发布数值"的另一件事（R1/R2 物理池先行）。

Examples:
-------
>>> import numpy as np
>>> from adi_model.config import Config
>>> from adi_model.interleave_tracking import InterleavedSAR, TrackPolicy
>>> cfg = Config()
>>> def slow_sine(t):
...     return 0.5 * np.sin(2 * np.pi * cfg.fs * t / 64)
>>> pol = TrackPolicy(mode="track_other", randomize=False)
>>> sim = InterleavedSAR(cfg, n_adcs=3, policy=pol, input_fn=slow_sine, n_cycles=96, seed=0)
>>> res = sim.run()
>>> res.summary["charge_ratio_vs_reset"] < 0.2
True
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .provenance import SourceGrade

__all__ = [
    "TrackPolicy",
    "TrackRunResult",
    "InterleavedSAR",
    "TRACK_GRADES",
    "filter_bw_relative",
    "filter_bw_absolute",
    "noise_ratio_from_bw",
]

TRACK_GRADES = {
    "mechanism": (SourceGrade.DISCLOSED, "[12] US 10,707,889 B1: tracking phase update"),
    "n_adcs": (SourceGrade.DISCLOSED, "[12] FIG.4A (N=4) / FIG.5A (N=3) examples"),
    "weights_example": (SourceGrade.DISCLOSED, "[12]: '10%, 30%, and 60%' N=3 example"),
    "c_dac_anchor": (SourceGrade.DERIVED, "c_active_nominal() of this repository"),
    "random_predistortion": (SourceGrade.ASSUMED, "[12] discloses the knob, no amplitude"),
    "quantization_noise": (SourceGrade.ASSUMED, "behavioural, not disclosed"),
}


# LEGAL_VALUES 契约：本模块的模式在入口显式拒绝非法取值（宁可拒绝不静默）。
TRACK_MODES = ("reset_mid", "track_own", "track_other", "track_other_weighted")


@dataclass(frozen=True)
class TrackPolicy:
    """IDLE 相位的处置策略（[12] 的机制旋钮）。

    Attributes:
        mode: 下列之一（非法值在 run() 入口拒绝）：
            ``"reset_mid"``           传统复位到中点（对照组）。
            ``"track_own"``           跳过复位、保持自己上一拍（[12] 背景：快信号更差）。
            ``"track_other"``         **专利核心**：另一颗 sub-ADC 的最近转换结果。
            ``"track_other_weighted"`` 最近 N 次转换加权组合（披露例 N=3、10/30/60%）。
        n_weighted: 加权模式的窗口长度 N（[披露] 例子 N=3）。
        weights: 加权模式的权重（时间倒序；[披露] 例 0.6/0.3/0.1，
            此处按"最近 -> 更早"排列）。
        predistortion_sigma_v: 叠加在跟踪值上的随机数字值（介电吸收预失真）
            的标准差 [V]；0 = 关闭。机制 [披露]，幅度 [假设]。
        taud_noise_sigma_v: 转换结果里叠加的行为级量化噪声 sigma [V]；
            0 = 理想转换（电荷核算不依赖它）。
        randomize: 是否启用 [12] 的相位随机化（相邻两拍以 0.25 概率交换）。
            False = 纯轮转（确定性时序，供精确期望的测试使用）。
    """

    mode: str = "reset_mid"
    n_weighted: int = 3
    weights: tuple[float, ...] = (0.6, 0.3, 0.1)
    predistortion_sigma_v: float = 0.0
    taud_noise_sigma_v: float = 0.0
    randomize: bool = True

    def validated(self) -> TrackPolicy:
        """拒绝非法模式/权重组合（入口显式拒绝，不静默退化）。

        Returns:
            TrackPolicy: 原样返回（校验通过时）。

        Raises:
            ValueError: mode 不在 TRACK_MODES，或加权模式权重与窗口不配。
        """
        if self.mode not in TRACK_MODES:
            raise ValueError(
                f"TrackPolicy.mode={self.mode!r} 不是已实现取值"
                f"（可选：{', '.join(TRACK_MODES)}）"
            )
        if self.mode == "track_other_weighted":
            if self.n_weighted < 1:
                raise ValueError(f"n_weighted={self.n_weighted} 必须 >= 1")
            if len(self.weights) < self.n_weighted:
                raise ValueError(f"weights={self.weights} 至少要有 n_weighted={self.n_weighted} 个")
            w = np.asarray(self.weights[: self.n_weighted], dtype=float)
            if w.sum() <= 0 or np.any(w < 0):
                raise ValueError(f"weights={self.weights} 必须非负且不全为零")
        return self


@dataclass
class TrackRunResult:
    """一次跟踪机制核算的输出。

    Attributes:
        charge: ``(N,)`` float64 —— 每周期进入采集的 sub-ADC 从驱动器/滤波器
            抽取的电荷 [C]，= c_dac·|x(t_n) − v_hold|。
        v_hold: ``(N,)`` float64 —— 进入采集瞬间 DAC 保持的电压 [V]。
        source_adc: ``(N,)`` int64 —— v_hold 的信息来源 sub-ADC 编号；
            reset_mid 恒为 −1（无来源）。
        source_age: ``(N,)`` int64 —— 来源结果距今的复合周期数；reset_mid 为 −1。
        acquired_adc: ``(N,)`` int64 —— 本周期处于采集相的 sub-ADC 编号。
        summary: 汇总标量（见 run() 返回说明）。

    电荷 -> 带宽/噪声的换算**不在 summary 里**（第八份复核订正：旧
    "带宽比=电荷比"量纲不成立）。需要换算时用
    :meth:`bw_ratio_absolute` / :meth:`driver_noise_ratio_absolute`，
    并显式给出 C_f 与绝对残差容差（绝对误差契约，电荷进对数项）。
    """

    charge: np.ndarray
    v_hold: np.ndarray
    source_adc: np.ndarray
    source_age: np.ndarray
    acquired_adc: np.ndarray
    summary: dict = field(default_factory=dict)

    def bw_ratio_absolute(self, c_f: float, v_err_max: float) -> float:
        """绝对误差契约下（跟踪 vs 复位）的最低带宽比（[推导]）。

        f_min(q) = max(0, ln(q/(C_f·v_err_max)))/(2π·T)，电荷进对数项；
        两侧任一 q 低于阈值（无约束，f_min=0）时本比值无定义，返回 nan。

        Args:
            c_f: 滤波电容 [F]（[假设]，两侧相同）。
            v_err_max: 采集节点绝对残差容差 [V]（[假设]）。

        Returns:
            float: 带宽下界之比；任一侧无约束时 nan。
        """
        q_t = float(self.summary.get("charge_mean", np.nan))
        q_r = float(self.summary.get("charge_reset_mean", np.nan))

        def _fmin(q: float) -> float | None:
            if not np.isfinite(q) or q <= 0:
                return None
            arg = q / (c_f * v_err_max)
            return 0.0 if arg <= 1.0 else float(np.log(arg))

        a, b = _fmin(q_t), _fmin(q_r)
        if a is None or b is None or b <= 0.0:
            return float("nan")
        return a / b

    def driver_noise_ratio_absolute(self, c_f: float, v_err_max: float) -> float:
        """绝对误差契约下的驱动噪声 rms 比 = sqrt(带宽比)（[推导]）。

        Args:
            c_f: 滤波电容 [F]（[假设]，两侧相同）。
            v_err_max: 采集节点绝对残差容差 [V]（[假设]）。

        Returns:
            float: 噪声比（sqrt(带宽比)；任一侧无约束时 nan）。
        """
        r = self.bw_ratio_absolute(c_f, v_err_max)
        return float(np.sqrt(r)) if np.isfinite(r) else float("nan")


def filter_bw_relative(t_cycle: float, eps: float = 0.01) -> float:
    """相对建立约束的最低滤波器带宽 [Hz]（[推导]，量纲自洽）。

    一阶系统在 t_cycle 内把初始扰动建立到相对比例 eps：
    exp(-T/(R_f C_f)) <= eps  ->  f_3dB >= ln(1/eps)/(2π·t_cycle)。

    **电荷 q 不进入此式**：相对建立条件与扰动大小无关（第八份外部
    复核订正；旧版 `kickback_filter_bw` 的 q/(T·a_tol) 量纲为 F/s，
    不是 Hz，已撤回）。

    Args:
        t_cycle: 建立可用时间（复合采样周期）[s]。
        eps: 相对建立残差目标 [无量纲]。

    Returns:
        float: 最低 3dB 带宽 [Hz]。

    Raises:
        ValueError: 非正输入。
    """
    if t_cycle <= 0 or not 0 < eps < 1:
        raise ValueError(f"非法输入: t={t_cycle}, eps={eps}")
    return float(np.log(1.0 / eps) / (2.0 * np.pi * t_cycle))


def filter_bw_absolute(q_kick: float, c_f: float, v_err_max: float, t_cycle: float) -> float:
    """绝对误差约束的最低滤波器带宽 [Hz]（[推导]，量纲自洽）。

    电荷 q 在滤波电容上造成初始扰动 ΔV0 = q/C_f，要求一个周期后
    绝对残差 ΔV0·exp(-T/(R_f C_f)) <= v_err_max：
    f_3dB >= max(0, ln(q/(C_f·v_err_max))) / (2π·t_cycle)。

    **电荷进对数项，不成立 f ∝ q**：q 低于 C_f·v_err_max 时无带宽
    约束（返回 0）；q 每加倍，f_min 只增加 1/(2πT)·ln2（第八份
    外部复核订正）。注意这只是单次扰动恢复约束，不含信号带宽等
    其他要求，不可直接当设计带宽用。

    Args:
        q_kick: 单次采集的 kickback 电荷 [C]。
        c_f: 滤波电容 [F]（固定，噪声-负载权衡的另一自由度）。
        v_err_max: 采集节点允许的绝对残差 [V]。
        t_cycle: 建立可用时间 [s]。

    Returns:
        float: 最低 3dB 带宽 [Hz]（q 低于阈值时为 0）。

    Raises:
        ValueError: 非正输入。
    """
    if q_kick <= 0 or c_f <= 0 or v_err_max <= 0 or t_cycle <= 0:
        raise ValueError(f"非法输入: q={q_kick}, c_f={c_f}, v_err_max={v_err_max}, t={t_cycle}")
    arg = q_kick / (c_f * v_err_max)
    if arg <= 1.0:
        return 0.0
    return float(np.log(arg) / (2.0 * np.pi * t_cycle))


def noise_ratio_from_bw(bw: float, bw_ref: float) -> float:
    """平坦噪声经一阶 RC 的输出 rms 噪声比 = sqrt(bw/bw_ref)（[推导]）。

    一阶 RC + 平坦输入噪声谱的输出噪声 = en·sqrt(π/2·f_3dB)，
    因此噪声比只依赖带宽比（**不**依赖电荷比）。

    Args:
        bw: 待评带宽 [Hz]。
        bw_ref: 参照带宽 [Hz]。

    Returns:
        float: 噪声 rms 比（sqrt(带宽比)）。

    Raises:
        ValueError: 负输入。
    """
    if bw < 0 or bw_ref <= 0:
        raise ValueError(f"非法输入: bw={bw}, bw_ref={bw_ref}")
    return float(np.sqrt(bw / bw_ref))


class InterleavedSAR:
    """n_adcs 颗 sub-ADC 的交织时序 + IDLE 相位处置的电荷核算（[12]）。

    Attributes:
        cfg: 模型配置（只用 fs 与 c_active_nominal 锚点）。
        policy: 跟踪策略（TrackPolicy）。
        c_dac: 每颗 sub-ADC 的 DAC 输入电容 [F]。
        n_adcs: sub-ADC 数量（[披露] 例 3 或 4）。
    """

    def __init__(
        self,
        cfg: Config,
        n_adcs: int,
        policy: TrackPolicy,
        input_fn,
        n_cycles: int,
        seed: int = 0,
        c_dac: float | None = None,
    ) -> None:
        """绑定时序、策略与输入。

        Args:
            cfg: 模型配置；fs 为复合采样率 [Hz]。
            n_adcs: sub-ADC 数量（>=2；[披露] 例 3/4）。
            policy: IDLE 相位策略；非法取值在构造时即拒绝。
            input_fn: ``t -> x``，复合采样时刻的输入 [V]。
            n_cycles: 仿真周期数 [无量纲]。
            seed: 随机源（相位随机化 + 预失真噪声）。
            c_dac: 每 sub-ADC 的 DAC 电容 [F]；None = 用
                ``c_active_nominal()/n_active`` 锚定（[推导]）。

        Raises:
            ValueError: n_adcs < 2。
        """
        if n_adcs < 2:
            raise ValueError(f"n_adcs={n_adcs} 必须 >= 2（交织至少两颗）")
        self.cfg = cfg
        self.n_adcs = int(n_adcs)
        self.policy = policy.validated()
        self.input_fn = input_fn
        self.n_cycles = int(n_cycles)
        self.rng = np.random.default_rng(seed)
        self.c_dac = float(c_dac) if c_dac is not None else cfg.c_active_nominal() / cfg.n_active

    # ------------------------------------------------------------------
    def run(self) -> TrackRunResult:
        """逐周期推进交织时序并核算 kickback 电荷。

        时序（相位错开的流水线，[12] FIG.5B/6 的结构）：每周期恰好一颗
        sub-ADC 处于采集相；顺序按 (n mod n_adcs) 轮转，并按 [12] 的
        随机化选项以概率 0.25 交换相邻两拍的顺序（随机化影响"谁是最近
        一拍"，从而影响 track_other 的信息来源——这正是专利要它工作的
        场景）。

        Returns:
            TrackRunResult: 逐周期数组 + summary：
                ``charge_mean``（平均 kickback 电荷 [C]）、
                ``charge_ratio_vs_reset``（对 reset_mid 基线的电荷比）。
                带宽/噪声换算不在此处（见 TrackRunResult 的
                ``bw_ratio_absolute``，第八份复核订正后口径）。

        Raises:
            ValueError: policy 非法（由 validated() 抛出）。
        """
        pol = self.policy
        n = self.n_cycles
        t_grid = np.arange(n) / self.cfg.fs
        x = np.asarray(self.input_fn(t_grid), dtype=float)
        x = np.broadcast_to(x, (n,)).copy()

        # 相位随机化：偶发交换相邻两拍的 sub-ADC 顺序（[12] randomization）。
        if pol.randomize:
            order = np.tile(np.arange(self.n_adcs), n // self.n_adcs + 1)[:n]
            swap = self.rng.random(n - 1) < 0.25
            for j in np.nonzero(swap)[0]:
                order[j], order[j + 1] = order[j + 1], order[j]
        else:
            order = np.tile(np.arange(self.n_adcs), n // self.n_adcs + 1)[:n]

        charge = np.zeros(n)
        v_hold = np.zeros(n)
        src_adc = np.full(n, -1, dtype=np.int64)
        src_age = np.full(n, -1, dtype=np.int64)

        last_result_adc: dict[int, tuple[int, float]] = {}  # adc -> (cycle, x̂)
        recent: list[tuple[int, int, float]] = []  # (cycle, adc, x̂) 最近在前

        for i in range(n):
            k = int(order[i])
            # ---- IDLE 相位处置：决定进入采集时的保持电压 ----
            if pol.mode == "reset_mid":
                v_h, s_adc, s_age = 0.0, -1, -1
            elif pol.mode == "track_own":
                own = last_result_adc.get(k)
                if own is None:
                    v_h, s_adc, s_age = 0.0, -1, -1
                else:
                    v_h, s_adc, s_age = own[1], k, i - own[0]
            elif pol.mode == "track_other":
                others = [(c, a, v) for (c, a, v) in recent if a != k]
                if others:
                    c0, a0, v0 = others[0]
                    v_h, s_adc, s_age = v0, a0, i - c0
                else:
                    v_h, s_adc, s_age = 0.0, -1, -1
            else:  # track_other_weighted
                others = [(c, a, v) for (c, a, v) in recent if a != k]
                w = np.asarray(pol.weights[: pol.n_weighted], dtype=float)
                w = w / w.sum()
                if len(others) >= pol.n_weighted:
                    v_h = float(np.dot(w, [o[2] for o in others[: pol.n_weighted]]))
                    s_adc, s_age = others[0][1], i - others[0][0]
                elif others:
                    ww = w[: len(others)]
                    v_h = float(np.dot(ww / ww.sum(), [o[2] for o in others]))
                    s_adc, s_age = others[0][1], i - others[0][0]
                else:
                    v_h, s_adc, s_age = 0.0, -1, -1
            if pol.predistortion_sigma_v > 0 and s_adc >= 0:
                v_h += float(self.rng.normal(0.0, pol.predistortion_sigma_v))

            v_hold[i] = v_h
            src_adc[i] = s_adc
            src_age[i] = s_age
            charge[i] = self.c_dac * abs(x[i] - v_h)

            # ---- 转换相：本拍采集者的转换结果在下一拍可用 ----
            # 相位错开流水：sub-ADC 在拍 i 采样（采集相），其 SAR 转换在
            # 拍 i+1 完成并登记——因此拍 i+1 的采集者看到的"最近一次
            # 别人的转换结果"恰是 x[i]（距今一个复合采样周期，[12] 的
            # 时机主张）。行为级：无位判决回路，x̂ = x[i] + 可选噪声。
            xhat: float = float(x[i]) + float(self.rng.normal(0.0, pol.taud_noise_sigma_v))
            last_result_adc[k] = (i, xhat)
            recent.insert(0, (i, k, xhat))
            del recent[8:]

        # reset_mid 基线是解析式：v_hold 恒为 0（中点），与随机时序无关，
        # 因此直接对同一输入序列计算，不需要重跑一遍仿真。
        q0 = self.c_dac * np.abs(x)  # reset_mid: v_hold = 0
        q_reset = float(np.mean(q0))
        q_mean = float(np.mean(charge))
        ratio = q_mean / q_reset if q_reset > 0 else float("nan")
        summary = {
            "charge_mean": q_mean,
            "charge_reset_mean": q_reset,
            "charge_ratio_vs_reset": ratio,
            "mode": pol.mode,
        }
        return TrackRunResult(
            charge=charge,
            v_hold=v_hold,
            source_adc=src_adc,
            source_age=src_age,
            acquired_adc=order,
            summary=summary,
        )
