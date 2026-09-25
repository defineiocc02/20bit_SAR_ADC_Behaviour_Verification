"""ref_track.py -- 专利 [14] US 10,826,519 B1 的低功耗参考方案（A06 缺口）。

============================================================================
专利披露的机制（机制级转述，原文不入库；见 NOTICE 引用原则）
============================================================================
问题：高精度 SAR 的 DAC/RA 需要参考在转换期间"足够稳"；直接用高速
参考缓冲器功耗大。[14] 给出两个替代安排：

**安排 A（FIG.1/13/15/16）**：低带宽（慢）放大器 + 大电容。
* FIG.1：10 µF 稳定电容 C1 上的电荷经高带宽 Amp1 维持 Vref_internal；
  'convert' 开关在残差生成前把外部 Vref 直接接入，'sample' 开关断开
  ——外部参考只需补很少的电荷，因此建立快、功耗低。
* FIG.13：慢速低带宽放大器 + 输出端 ~100 nF 电池电容 C3；'sample'
  相期间外部 Vref 被断开、慢放大器给 C3 充电（C3 作"电池"）；
  'convert' 相 C3 直接把 Vref_internal 连到 Vref。

**安排 B（FIG.2-12，比较器 + 阈值调整）**：本模块的仿真对象。
* Comp1 反相输入接外部 Vref，另一输入接 Vref_internal（DAC1 的参考
  输入）；输出驱动 MP1 从 VDD1 给 Vref_internal 充电（只能源电流）。
* **Threshold_adjust**：比较器阈值经 MN4 偏移，调整信号按 bit-trial
  或按 DAC 负载给出；控制回路**跨转换周期积分误差**（运行和 ->
  数字积分器），"drives the comparator threshold towards zero"。
* 有限比较器带宽 -> 检测与充电响应有一阶滞后；[14] 披露：系统通常
  能在 **5 或 6 个转换周期内**整定；逐 bit-trial 调整使 Σ(Q1..Qn) = 0
  （电荷误差之和为零）。
* 转换末 S1 闭合，Vref_internal 由外部 Vref 直接收尾——残差误差 E
  由外部参考补偿（外部只需 C·E 的电荷，很小 -> 省电）。

============================================================================
本实现的口径（勿夸大）
============================================================================
* 行为级**电荷-阈值回路**（无晶体管级/频响细节），逐位试推进：
    - 位试载荷：SAR 二进制权重的码跳变按 ΔQ = C_dac·ΔV 从参考节点
      抽取（结构性质 [推导]）；
    - MP1 充电：只能源电流；每位试最多补充
      rec = I_max·T_trial/C_int（限流），响应比例于调节点偏差且乘以
      一阶伺服增益 k = 1 − exp(−T_trial/τ_cmp)（有限比较器带宽的
      行为级近似，[假设]）；
    - 调节点 v_reg = v_ext − θ：阈值积分 θ ← θ − g·E
      （E = 周期末 S1 闭合前残差）使 θ 逐周期几何收敛（因子 1−g）、
      E ≈ θ + 跟随滞后 -> 周期末残差收敛——与 [14] "阈值逼零 +
      ΣQ → 0"的描述同构；
    - S1 闭合：以增益 s1_topup_gain 把节点拉向 v_ext（真实开关有限
      时间/电阻的行为级近似），外部电荷 q = C·|ΔV_S1|。
  整定周期数由 g 与初值共同决定（第八份复核实测：g=0.9 -> 6、
  g=0.6 -> 12、g=0.3 -> 25，从零计数）：本参数组（g=0.9 [假设]）
  整定 6 周期与 [14] 披露的"5 or 6"**量级一致——但这是该组假设
  参数的演示结果，不构成对披露整定行为的独立验证**。g、初值、
  S1 增益均为 [假设]。
* **A06 的回答边界（勿夸大）**：本模块回答的是"参考节点在预设位试
  载荷与阈值更新下的补充电荷/恢复行为"这一个独立行为模型；A06 所
  要求的"动态参考 — RA — ADC2 联合建立"（RA 相位中 RDAC 参考仍
  在建立 + ADC2 宽带建立/窄带采样）**仍未验证**——本模块没有 RA
  状态、RA 有限带宽、ADC2 采样窗口。**末位试参考误差很小 ≠ RA 对
  整个参考扰动过程的积分/跟踪误差很小**。
* 与 [00] 主链的耦合点（A06 的机制层）：参考误差 δ_j 在位试 j 上以
  ``e_D = δ_j·(2k_j/N − 1)`` 进入 DAC 减法。粗判决（前几位）对参考
  误差的容忍度取主配置残差量程契约（ADC2 余量/G0 = 4.6875 mV）；
  只有最后几个低位试需要 20b 级参考。这给出"参考 ~15b 时 RA 即可
  起动、转换尾段才到 20b"的机制层解释（数值随配置，[推导]+[假设]）。
* 参数分级：结构/整定行为量级/C 取值例（10 µF、~100 nF、100 pF）
  [披露]（[14]）；I_max、比较器带宽、阈值增益、转换相时长、初值、
  S1 增益 [假设]。

Examples:
-------
>>> from adi_model.ref_track import RefTrackSim, RefTrackConfig
>>> sim = RefTrackSim(RefTrackConfig())
>>> run = sim.run()
>>> run.settle_cycle is not None and run.settle_cycle <= 8
True
>>> run.err_final_bits(3.0) > 19.0
True
>>> run.a06["window_margin_ratio"] > 1.0
True
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .provenance import SourceGrade

__all__ = [
    "RefTrackConfig",
    "RefTrackRun",
    "RefTrackSim",
    "REF_GRADES",
    "reference_precision_bits",
]

REF_GRADES = {
    "mechanism": (
        SourceGrade.DISCLOSED,
        "[14] US 10,826,519 B1: comparator + threshold adjust + switch reference",
    ),
    "settle_5_or_6": (
        SourceGrade.DISCLOSED,
        "[14]: 'usually able to settle within 5 or 6 conversion cycles'",
    ),
    "cap_examples": (
        SourceGrade.DISCLOSED,
        "[14]: C1=10uF (FIG.1), C3~100nF (FIG.13), C1=100pF (FIG.2)",
    ),
    "sar_load_shape": (SourceGrade.DERIVED, "SAR binary-weighted code transitions"),
    "i_charge_max": (SourceGrade.ASSUMED, "MP1 saturation current; no published number"),
    "cmp_bw_hz": (SourceGrade.ASSUMED, "comparator finite bandwidth; no published number"),
    "threshold_gain": (SourceGrade.ASSUMED, "integrator gain; no published number"),
    "t_conv": (SourceGrade.ASSUMED, "conversion-phase duration; behavioural"),
    "v_init_frac": (SourceGrade.ASSUMED, "power-up initial condition; behavioural"),
    "s1_topup_gain": (SourceGrade.ASSUMED, "S1 switch top-up strength; behavioural"),
}


def reference_precision_bits(err_rms: float, span_v: float) -> float:
    """参考误差 rms -> 等效精度位数 = log2(span / (√12·σ))（[推导]）。

    把参考误差当作 span 上的均匀量化误差：σ = Δ/√12 -> Δ = √12·σ，
    N 位量化的 Δ = span/2^N，反解 N = log2(span/(√12·σ))。
    口径声明：这是"这个大小的 RMS 误差等效于几位"的换算，不是任何
    实测精度；入参必须是 **RMS**，平均绝对误差不可不加换算直接代入
    （第八份外部复核订正：旧版多了因子 2，少算整整 1 bit）。

    Args:
        err_rms: 参考误差 rms [V]（> 0）。
        span_v: 参考满摆幅（全范围，非半幅）[V]（> 0）。

    Returns:
        float: 等效精度位数（bit）。

    Raises:
        ValueError: 非正输入。
    """
    # 独立审查 2026-09-25：同时拒绝非有限输入
    if not math.isfinite(err_rms) or err_rms <= 0 or not math.isfinite(span_v) or span_v <= 0:
        raise ValueError(f"err_rms={err_rms}, span_v={span_v} 必须为正有限值")
    return float(np.log2(span_v / (np.sqrt(12.0) * err_rms)))


@dataclass(frozen=True)
class RefTrackConfig:
    """参考跟踪回路参数（安排 B；安排 A 以 c_internal 尺寸区分，同框架）。

    Attributes:
        v_ext: 外部参考电压 [V]。
        c_internal: Vref_internal 节点总电容 [F]（[披露] 例：10 µF/100 nF/100 pF）。
        n_bits: 转换相的位试数（SAR 二进制权重载荷）。
        c_dac_load: DAC 阵列呈现给参考节点的总电容 [F]（安排 B 里是
            粗级 ADC1 的小 DAC，[假设]）。
        v_fs_dac: DAC 满摆幅（**全范围**，非半幅）[V]（码值
            (2k/N−1)·v_fs_dac 与 LSB20 均按全范围口径；本仓库 ±3.0 V
            差分满幅 -> 6.0 V，[推导]）。
        i_charge_max: MP1 最大充电电流 [A]（[假设]）。
        cmp_bw_hz: 比较器有限带宽 [Hz]（τ_cmp = 1/(2π·f)，伺服增益用，[假设]）。
        threshold_gain: 阈值积分增益（θ ← θ − g·E，[假设]）。
        t_conv: 转换相时长 [s]（[假设]；本仓库口径 0.4/fs = 10 ns 量级）。
        v_init_frac: 上电初值 v_int(0)/v_ext [无量纲]（[假设]，<1 给出起动瞬态）。
        s1_topup_gain: 周期末 S1 闭合把节点拉向 v_ext 的增益 [无量纲]（[假设]）。
        tol_first_v: 粗判决位试可容忍的参考误差 [V]（[推导]，取自本仓库
            主配置残差量程契约：ADC2 余量 0.15 V / 级间增益 G0=32 =
            4.6875 mV；**不是** Δ1/2——自愈窗口由残差实际范围、RA/ADC2
            限制等共同决定，第八份外部复核订正）。
        n_cycles: 仿真转换周期数。
        settle_eps_v: 判定整定的残差门限 [V]。
    """

    v_ext: float = 3.0
    c_internal: float = 100e-12  # [披露] FIG.2 例：100 pF
    n_bits: int = 16
    c_dac_load: float = 0.1e-12  # 安排 B 的粗级小 DAC（[假设]）
    v_fs_dac: float = 6.0
    i_charge_max: float = 0.2e-3
    cmp_bw_hz: float = 100.0e6
    threshold_gain: float = 0.9
    t_conv: float = 10e-9
    v_init_frac: float = 0.99
    s1_topup_gain: float = 0.6
    tol_first_v: float = 4.6875e-3
    n_cycles: int = 40
    settle_eps_v: float = 1.0e-6

    def validated(self) -> RefTrackConfig:
        """拒绝非法参数（入口显式拒绝，不静默退化）。

        Returns:
            RefTrackConfig: 原样返回。

        Raises:
            ValueError: 非正参数、位试/周期数 < 1 或增益出界。
        """
        for name, v in (
            ("v_ext", self.v_ext),
            ("c_internal", self.c_internal),
            ("c_dac_load", self.c_dac_load),
            ("v_fs_dac", self.v_fs_dac),
            ("i_charge_max", self.i_charge_max),
            ("cmp_bw_hz", self.cmp_bw_hz),
            ("t_conv", self.t_conv),
            ("tol_first_v", self.tol_first_v),
            ("n_bits", float(self.n_bits)),
            ("n_cycles", float(self.n_cycles)),
        ):
            # 独立审查 2026-09-25：0 < x 写法拦不住 nan，这里补上有限性
            if not math.isfinite(v) or v <= 0:
                raise ValueError(f"{name}={v!r} 必须为正有限值")
        if not 0 < self.v_init_frac <= 1:
            raise ValueError(f"v_init_frac={self.v_init_frac!r} 必须在 (0, 1]")
        if not 0 <= self.s1_topup_gain <= 1:
            raise ValueError(f"s1_topup_gain={self.s1_topup_gain!r} 必须在 [0, 1]")
        if not 0 < self.threshold_gain <= 1:
            raise ValueError(
                f"threshold_gain={self.threshold_gain!r} 必须在 (0, 1]："
                ">1 的整定增益会过冲振荡（独立审查 2026-09-25）"
            )
        return self


@dataclass
class RefTrackRun:
    """一次参考跟踪仿真的输出。

    Attributes:
        err_end_of_cycle: ``(M,)`` float64 —— 每周期转换结束时
            v_ext − v_int 的残差 [V]（S1 闭合前）。
        err_per_trial: ``(M, n_bits)`` float64 —— 每个位试时刻的参考误差 [V]。
        threshold: ``(M,)`` float64 —— 每周期使用的比较器阈值 [V]
            （逼零轨迹，[14] "drives the threshold towards zero"）。
        charge_from_ext: ``(M,)`` float64 —— S1 闭合时外部参考提供的电荷 [C]
            （专利的低功耗论据：只有 C·E，很小）。
        settle_cycle: 首个 |E| < settle_eps_v 且此后保持的周期号（0 起）；
            未整定为 None。
        a06: A06 分析（见 :meth:`RefTrackSim.a06_analysis`）。
    """

    err_end_of_cycle: np.ndarray
    err_per_trial: np.ndarray
    threshold: np.ndarray
    charge_from_ext: np.ndarray
    settle_cycle: int | None
    a06: dict = field(default_factory=dict)

    def _window_bits(self, sl: slice, span_v: float) -> float:
        """窗口内周期末误差 rms 的等效精度位数（[推导] 换算）。

        Args:
            sl: 周期轴切片。
            span_v: 参考满摆幅 [V]。

        Returns:
            float: 精度位数（bit）。
        """
        w = self.err_end_of_cycle[sl]
        std = float(np.std(w))
        # 零误差 → 无穷大位数（与 a06_analysis 的口径一致；此前此处对同样的
        # 零误差直接 raise，同一类输入两种行为，独立审查 2026-09-25）
        return reference_precision_bits(std, span_v) if std > 0 else float("inf")

    def err_final_bits(self, span_v: float) -> float:
        """末段（最后 3/4 周期）参考误差的等效精度位数（[推导]）。

        Args:
            span_v: 参考满摆幅 [V]。

        Returns:
            float: 精度位数（bit）。
        """
        m = len(self.err_end_of_cycle)
        return self._window_bits(slice(m // 4, m), span_v)

    def err_early_bits(self, span_v: float) -> float:
        """首段（前 1/4 周期，含未整定瞬态）的等效精度位数（[推导]）。

        Args:
            span_v: 参考满摆幅 [V]。

        Returns:
            float: 精度位数（bit）。
        """
        m = len(self.err_end_of_cycle)
        return self._window_bits(slice(0, max(m // 4, 1)), span_v)


class RefTrackSim:
    """[14] 安排 B 的行为级仿真：比较器 + 阈值逼零积分 + 限流充电。

    Attributes:
        cfg: 已校验的 RefTrackConfig。
    """

    def __init__(self, cfg: RefTrackConfig | None = None) -> None:
        """保存配置（非法取值在构造时拒绝）。

        Args:
            cfg: 回路参数；None = 默认（FIG.2 口径 100 pF）。
        """
        self.cfg = (cfg or RefTrackConfig()).validated()

    # ------------------------------------------------------------------
    def _sar_load_profile(self) -> np.ndarray:
        """位试载荷：SAR 二进制权重的码跳变 -> 参考节点电荷抽取序列。

        标准 SAR 收敛（结构性质 [推导]）：试 j 的码跳变幅度 ∝ 2^(−j)，
        DAC 摆幅变化 ΔV_j = v_fs_dac·2^(−j)/2 -> 参考节点抽取
        ΔQ_j = C_dac·ΔV_j。

        Returns:
            np.ndarray: ``(n_bits,)`` 每位试的 ΔQ [C]（正 = 从参考抽取）。
        """
        c = self.cfg
        dv = 0.5 * c.v_fs_dac * 2.0 ** (-np.arange(c.n_bits, dtype=float))
        return c.c_dac_load * dv

    def run(self) -> RefTrackRun:
        """逐周期仿真参考回路（安排 B，FIG.8 的时序结构）。

        每周期：从上周期 S1 泄放后的节点电压出发；逐位试先抽取 ΔQ_j，
        再由 MP1 向调节点 v_reg = v_ext − θ 限流充电（比例于偏差、
        乘一阶伺服增益、封顶 rec，只能源电流）；周期末记录残差 E，
        S1 以增益增益拉向 v_ext（外部补 C·|ΔV|），阈值 θ ← θ − g·E
        逼零。

        Returns:
            RefTrackRun: 逐周期/逐位试数组与整定周期号。
        """
        c = self.cfg
        m = int(c.n_cycles)
        sag = self._sar_load_profile() / c.c_internal  # 每位试的压降 [V]
        t_trial = c.t_conv / c.n_bits
        rec = c.i_charge_max / c.c_internal * t_trial  # 每位试可用恢复 [V]
        tau_cmp = 1.0 / (2.0 * math.pi * c.cmp_bw_hz)
        k_servo = 1.0 - math.exp(-t_trial / tau_cmp)  # 一阶伺服增益（有限带宽）

        err_trial = np.zeros((m, c.n_bits))
        err_end = np.zeros(m)
        thr_out = np.zeros(m)
        q_ext = np.zeros(m)
        theta = 0.0
        settle: int | None = None
        v = c.v_init_frac * c.v_ext

        for k in range(m):
            v_reg = c.v_ext - theta
            for j in range(c.n_bits):
                v -= sag[j]
                d = v_reg - v
                if d > 0:  # MP1 只能源电流：低于调节点才充电
                    v += min(k_servo * d, rec)
                err_trial[k, j] = c.v_ext - v
            e_k = c.v_ext - v
            err_end[k] = e_k
            # S1 闭合：外部参考把节点拉向 v_ext（增益受限的行为级近似）
            v_before = v
            v += c.s1_topup_gain * (c.v_ext - v)
            q_ext[k] = c.c_internal * abs(v - v_before)
            # 阈值逼零积分（[14]：threshold driven towards zero；ΣQ -> 0）
            theta = theta - c.threshold_gain * e_k
            thr_out[k] = theta
            if (
                settle is None
                and k >= 2
                and np.all(np.abs(err_end[max(k - 2, 0) : k + 1]) < c.settle_eps_v)
            ):
                settle = k - 2

        a06 = self.a06_analysis(err_trial)
        return RefTrackRun(
            err_end_of_cycle=err_end,
            err_per_trial=err_trial,
            threshold=thr_out,
            charge_from_ext=q_ext,
            settle_cycle=settle,
            a06=a06,
        )

    # ------------------------------------------------------------------
    def a06_analysis(self, err_trial: np.ndarray) -> dict:
        """A06 的答案：参考精度需求在转换内如何分布（[推导]+[假设]）。

        原理（机制级）：
        * 粗判决（SADC/前几位）对参考误差的容忍度取自本仓库主配置的
          残差量程契约（ADC2 余量 0.15 V / G0=32 = 4.6875 mV，[推导]；
          自愈窗口由残差实际范围与 RA/ADC2 限制共同决定，不是普适的
          Δ1/2——第八份外部复核订正）；对应"参考 ~15b 时 RA 即可
          起动"的结构解释。
        * 最低几位试的 DAC 减法直接乘上参考误差：e_out = δ_j·(2k/N − 1)；
          要不伤 20b 输出，|δ| 必须在 LSB20 量级 -> 参考在转换尾段需要
          ~20b。这就是"~65% RA 相位才到 20b"的定性结构（数值随配置，
          [推导]；[14]/[00_1] 均未披露该百分比的本模型口径）。

        统计窗口：取后 3/4 周期（剔除起动瞬态）——A06 问的是稳态转换
        里的参考精度分布，不是上电过程。

        Args:
            err_trial: ``(M, n_bits)`` 每位试参考误差 [V]。

        Returns:
            dict: ``bits_needed_first_trial`` / ``bits_needed_last_trial`` /
            ``window_margin_ratio``（粗判决容忍误差 / 首试实际误差）与
            口径注记。
        """
        c = self.cfg
        w = err_trial[len(err_trial) // 4 :]  # 稳态窗口（剔除起动瞬态）
        tol_first = c.tol_first_v  # 主配置残差量程契约（ADC2 余量/G0），非 Δ1/2
        # A06 换算口径用 RMS（函数契约即 RMS；mean-abs 不可直接代入，
        # 第八份外部复核订正）
        e_first = float(np.sqrt(np.mean(np.square(w[:, 0]))))
        e_last = float(np.sqrt(np.mean(np.square(w[:, -1]))))
        lsb20 = c.v_fs_dac / 2.0**20
        return {
            "bits_needed_first_trial": (
                reference_precision_bits(e_first, c.v_ext) if e_first > 0 else float("inf")
            ),
            "bits_needed_last_trial": (
                reference_precision_bits(e_last, c.v_ext) if e_last > 0 else float("inf")
            ),
            "window_margin_ratio": tol_first / max(e_first, 1e-18),
            "tolerance_first_trial_v": tol_first,
            "lsb20_v": lsb20,
            "note": (
                "粗判决容忍度 = 主配置残差量程契约（ADC2 余量 0.15 V / G0=32 ="
                " 4.6875 mV，[推导]），非 Δ1/2 口径；统计用 RMS；数值随配置，"
                "勿当披露值引用"
            ),
        }
