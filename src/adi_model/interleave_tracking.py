"""交织 SAR 跟踪相位的独立机制基准，不是主链路校准器。

[12] US 10,707,889 B1 描述多 sub-ADC 的 ACQUISITION/CONVERSION/IDLE
交错相位：IDLE 可复位中点、保持本颗上次结果，或用另一颗最近结果预置
DAC；还给出三次结果 60%/30%/10% 加权及数字随机预失真的例子。

本模块只核算进入采集时的电荷 q=C_dac*abs(x-v_hold) [C]。转换结果
采用理想输入加可选行为噪声，不含实际 SAR 判决、转换延迟分布或有限
预充电建立，因此只能作受专利启发的机制对照。主 split 链使用
pretracking.QuantizedPretracker，从按时间可用的实际 coarse code 估计，
这里的理想输入结果不会进入生产预跟踪，也不能作为低频学习的运行时观测。

相位随机化是相邻顺序交换的项目假设，不是物理 18/8 自由集合分配器。
电荷比不能直接写成带宽比或噪声比：相对建立的 f_min 与 q 无关；绝对
残差约束下 q 只进入对数项。C_dac 默认锚到本项目单片电容；预失真/噪声
幅度为假设，电荷核算不能预测器件驱动噪声、功耗或带宽收益。

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


# 模式在入口验证；这些策略属于独立电荷基准，不是生产调度器选择。
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
        taud_noise_sigma_v: 转换结果里叠加的假设高斯扰动 sigma [V]；
            0 = 理想转换，不是实际 SAR 量化误差的位级求解。
        randomize: 是否随机化相位；相邻两拍以 0.25 概率交换是本项目假设。
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
            # 加权组合要求有限非负且总权重为正，避免 NaN 传播或零分母。
            if not np.all(np.isfinite(w)) or w.sum() <= 0 or np.any(w < 0):
                raise ValueError(f"weights={self.weights} 必须有限、非负且不全为零")
        return self


@dataclass
class TrackRunResult:
    """保存每个复合周期的电荷核算及来源信息。

    Attributes:
        charge: (N,) 从输入驱动节点补入的电荷幅值 [C]，
            等于 c_dac*abs(x(t_n)-v_hold)，不是有符号净电荷。
        v_hold: (N,) 进入采集前的 DAC 预置/保持电压 [V]。
        source_adc: (N,) 来源 sub-ADC 编号，复位/无历史时 -1；
            加权模式仅记录最新贡献者，不代表全部来源列表。
        source_age: (N,) 所记来源距今的复合周期数，缺省 -1。
        acquired_adc: (N,) 本周期采集的 sub-ADC 编号，不是 slot 或 sample ID。
        summary: 电荷均值 [C]、相同输入复位基线均值 [C] 和无量纲电荷比。

    Notes:
        summary 不把电荷比转换为带宽/噪声比。辅助推导需显式输入滤波电容
        和绝对容差；带宽要求还受信号频率和实际电路约束。
    """

    charge: np.ndarray
    v_hold: np.ndarray
    source_adc: np.ndarray
    source_age: np.ndarray
    acquired_adc: np.ndarray
    summary: dict = field(default_factory=dict)

    def bw_ratio_absolute(self, c_f: float, v_err_max: float) -> float:
        """按同一绝对建立容差估计跟踪/复位的最低带宽比。

        Args:
            c_f: 两侧相同的滤波电容 [F]，调用方应保证有限且正。
            v_err_max: 采集节点绝对残差容差 [V]，调用方应保证有限且正。

        Returns:
            max(0,ln(q_track/(c_f*v_err_max))) 与复位侧对应量之比；共同的
            2*pi*T 因子相消。复位侧无正带宽下界或电荷无效时返回 NaN；
            跟踪侧低于阈值而复位侧受限时可返回 0。

        Notes:
            使用平均电荷代入单次恢复公式，仅作机制估算，不保证峰值残差或
            目标带宽。此 helper 不执行独立参数校验，也不消费随机流。
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
        """在相同白噪声和一阶滤波假设下取带宽比的平方根。

        Args:
            c_f: 两侧相同的滤波电容 [F]，有限且正。
            v_err_max: 相同采集节点绝对残差容差 [V]，有限且正。

        Returns:
            sqrt(bw_ratio_absolute) 的 RMS 噪声比；带宽比无定义时 NaN。
            不是从电荷比直接开方，也不包含真实驱动器的非白噪声和信号带宽。
        """
        r = self.bw_ratio_absolute(c_f, v_err_max)
        return float(np.sqrt(r)) if np.isfinite(r) else float("nan")


def filter_bw_relative(t_cycle: float, eps: float = 0.01) -> float:
    """计算一阶相对建立约束对应的最低 3 dB 带宽 [Hz]。

    exp(-T/(R_f*C_f))<=eps 给出 f_min=ln(1/eps)/(2*pi*T)。
    电荷不进入相对误差比例；减小初始电荷不自动减小这个带宽下界。

    Args:
        t_cycle: 可用建立时间 T [s]，有限且正。
        eps: 目标相对残留比例，无量纲，严格位于 (0,1)。

    Returns:
        一阶模型的最低 3 dB 带宽 [Hz]，不含信号通带等其他约束。

    Raises:
        ValueError: 非法建立时间或残留比例。
    """
    # 恢复指数需要有限正时间，eps 则是严格小于 1 的比例。
    if not np.isfinite(t_cycle) or t_cycle <= 0 or not 0 < eps < 1:
        raise ValueError(f"非法输入: t={t_cycle}, eps={eps}（须为有限值）")
    return float(np.log(1.0 / eps) / (2.0 * np.pi * t_cycle))


def filter_bw_absolute(q_kick: float, c_f: float, v_err_max: float, t_cycle: float) -> float:
    """计算一次电荷扰动在绝对残差约束下的最低 3 dB 带宽。

    初始滤波节点扰动为 q_kick/c_f [V]；要求经过 T 后不大于 v_err_max，
    f_min=max(0,ln(q_kick/(c_f*v_err_max)))/(2*pi*T) [Hz]。
    电荷只在对数中出现，不成立 f_min 与电荷成正比。

    Args:
        q_kick: 单次采集电荷幅值 [C]，有限且正。
        c_f: 滤波电容 [F]，有限且正。
        v_err_max: 采集节点允许的绝对残差 [V]，有限且正。
        t_cycle: 可用建立时间 T [s]，有限且正。

    Returns:
        最低 3 dB 带宽 [Hz]；q_kick<=c_f*v_err_max 时返回 0。
        仅是单次扰动恢复下界，不能直接作为完整滤波器设计带宽。

    Raises:
        ValueError: 任一参数非有限或非正。
    """
    # 对数自变量必须由有限正的电荷、电容和电压组成。
    if (
        not np.isfinite(q_kick)
        or q_kick <= 0
        or not np.isfinite(c_f)
        or c_f <= 0
        or not np.isfinite(v_err_max)
        or v_err_max <= 0
        or not np.isfinite(t_cycle)
        or t_cycle <= 0
    ):
        raise ValueError(
            f"非法输入: q={q_kick}, c_f={c_f}, v_err_max={v_err_max}, t={t_cycle}（须为有限值）"
        )
    arg = q_kick / (c_f * v_err_max)
    if arg <= 1.0:
        return 0.0
    return float(np.log(arg) / (2.0 * np.pi * t_cycle))


def noise_ratio_from_bw(bw: float, bw_ref: float) -> float:
    """按同一平坦输入噪声和一阶 RC 模型计算输出 RMS 噪声比。

    Args:
        bw: 待评 3 dB 带宽 [Hz]，有限且非负。
        bw_ref: 参照 3 dB 带宽 [Hz]，有限且正。

    Returns:
        sqrt(bw/bw_ref)，因为一阶滤波后的电压噪声 RMS 为
        e_n*sqrt(pi*bw/2)。不依赖电荷比，也不证明器件噪声是白噪声。

    Raises:
        ValueError: 非有限或超出上述范围的带宽。
    """
    # 允许待评带宽为零；参照带宽必须为有限正值。
    if not np.isfinite(bw) or bw < 0 or not np.isfinite(bw_ref) or bw_ref <= 0:
        raise ValueError(f"非法输入: bw={bw}, bw_ref={bw_ref}（须为有限值）")
    return float(np.sqrt(bw / bw_ref))


class InterleavedSAR:
    """独立的多 sub-ADC 预置机制核算器，不参与最终 ADC 码重构。

    Attributes:
        cfg: 只使用 fs 和单片电容锚点，不读取 slice 失配真值。
        policy: IDLE 预置策略。
        c_dac: 每 sub-ADC 在输入节点的电容 [F]。
        n_adcs: 独立机制基准中的 sub-ADC 数，不是生产 18-slice 池的数量。
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
        """绑定机制基准的复合时钟、策略和理想激励。

        Args:
            cfg: fs 为复合采样率 [Hz]，每 sub-ADC 平均采样率为 fs/n_adcs。
            n_adcs: sub-ADC 数量，至少 2；专利示例为 3 或 4。
            policy: IDLE 预置策略，构造时验证模式和加权组合。
            input_fn: t->x(t)，t [s]，输出 [V]；支持标量广播或 (N,) 记录。
            n_cycles: 复合采样周期数 N。
            seed: 相位交换、预失真与行为转换噪声的随机种子。
            c_dac: 每 sub-ADC 电容 [F]；None 使用
                cfg.c_active_nominal()/cfg.n_active，不等同 18 片总池电容。

        Raises:
            ValueError: n_adcs<2，或策略模式/加权组合非法。
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
        """按复合周期先选择历史预置，再核算本次采集电荷。

        每周期一颗 sub-ADC 采集。轮转序列可按项目假设以 0.25 概率相邻交换；
        本周期 x[i]+行为噪声在本周期核算结束后写入历史，因此下一周期才可用。
        这是一步延迟的理想机制模型，没有模拟位判决完成时间或忙状态。

        Returns:
            TrackRunResult；charge_mean、charge_reset_mean 为电荷幅值均值 [C]，
            charge_ratio_vs_reset 为同一输入对 reset_mid 的比值，不是降噪系数。
            reset 基线直接用 c_dac*abs(x) 解析计算，无须重新抽随机数。

        Side effects:
            每次调用重建本次历史和组序列，但继续消费 self.rng。历史最多保存
            8 次结果；加权模式使用其中可用的其他 ADC 结果，缺历史时复位中点。
            这里的理想 xhat 仅供电荷基准，不允许进入运行时校准/低频学习接口。
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
