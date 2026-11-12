# =============================================================================
# 文件名     : reference.py
# 模块名     : adi_model.reference
# 版本       : v1.0
# 功能       : 参考节点动态建立的剩余误差与 RA 传输非线性的纯函数。
#
# 设计说明   :
#   * 只做纯计算，不 import 任何 task* 模块，也不持有状态：给定参数就能算出
#     误差，便于在扫描实验里成千次调用而不必构造整个模型。
#   * 时间常数 τ 与非线性系数 ppm 都是 S5_假设，不是硅数据。本模块只回答
#     「若有这个量会怎样」，不对任何具体版图的 RC 或运放线性度做断言。
#   * 唯一有出处的关系是论文 ISSCC2024 9.8 的两句话：RA 相位开始时残差只有
#     约 15b 精度，参考完全建立（20b）需要 RA 相位的约 65%。这两句给出的是
#     精度与时间锚点；把它们换成同一指数误差的 1/32 衰减及 τ，是额外工程假设。
#   * 预充相位与 RA 相位共用同一个 τ 的口径：粗预充缓冲器把参考充到某个比例，
#     多路开关切到 REF_IN 之后只剩残差要建立。若两者 τ 不同，本模块的
#     precharge_frac 就该由调用方按各自的 τ 先算好再传进来。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M4a。
# =============================================================================

"""参考节点动态建立与 RA 传输非线性的纯函数。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 论文给的锚点：参考完全建立（20b）占 RA 相位的比例。
# ISSCC2024 9.8 原文 "Complete (20b) settling of the reference, takes approx.
# 65% of the RA phase"。这是本模块唯一不是假设的数字。
SETTLE_FRACTION_OF_RA_PHASE = 0.65

# RA 相位开始时残差的精度（位）。原文 "the residue is only ~15b accurate"。
RESIDUAL_ACCURACY_BITS_AT_RA_START = 15

# 若两种精度采用同一误差标度，15b 到 20b 可等效为误差衰减 32 倍；
# 这不是对论文参考电流波形或真实多极点建立过程的识别。
RESIDUAL_ACCURACY_RATIO = 1 << (20 - RESIDUAL_ACCURACY_BITS_AT_RA_START)

# 工程等效：把15b残差精度换成相对全摆幅2^-15的预充误差；
# 论文没有直接披露这个参考节点电压比例，不得作为真实电路初值引用。
PRECHARGE_FRACTION_FROM_PAPER = 1.0 - 2.0**-RESIDUAL_ACCURACY_BITS_AT_RA_START


@dataclass(frozen=True)
class ReferenceConfig:
    """参考动态建立与 RA 传输非线性的配置。默认全关，关闭时结果逐位不变。

    Attributes:
        enabled: 总开关。False 时 ``_capture`` 完全不走参考路径。
        t_ra_s: RA 相位时长，单位 s。
        tau_s: 参考节点时间常数，单位 s。0 表示理想参考。
        precharge_frac: 预充结束时已建立的比例，[0, 1]。
            语义是「RA 相位开始时误差还剩全摆幅的 (1-precharge_frac)」，
            论文口径下等于 ``PRECHARGE_FRACTION_FROM_PAPER``。
        t_precharge_s: 预充时长，单位 s。0 表示不校验它与 precharge_frac 的自洽性。
        ppm2: RA 二阶非线性系数，ppm（相对差分满幅）。
        ppm3: RA 三阶非线性系数，ppm（相对差分满幅）。

    这些参数均需独立的模拟依据；``from_paper_anchor`` 的 τ 只在假定单极点指数建立、
    且两项精度使用同一误差标度时由披露锚点换算，并非论文披露的器件时间常数。
    """

    enabled: bool = False
    t_ra_s: float = 0.0
    tau_s: float = 0.0
    precharge_frac: float | None = None
    t_precharge_s: float = 0.0
    ppm2: float = 0.0
    ppm3: float = 0.0

    def __post_init__(self) -> None:
        """拒绝物理上无意义的取值，避免把错误参数静默带进扫描。"""
        if self.t_ra_s < 0:
            raise ValueError("RA phase duration must not be negative")
        if self.tau_s < 0:
            raise ValueError("time constant must not be negative")
        if self.precharge_frac is None:
            if self.enabled and self.tau_s > 0.0:
                raise ValueError(
                    "precharge fraction must be explicit when reference settling is enabled"
                )
            object.__setattr__(self, "precharge_frac", 0.0)
        if not 0.0 <= self.precharge_frac <= 1.0:
            raise ValueError("precharge fraction must lie in [0, 1]")
        if self.t_precharge_s < 0:
            raise ValueError("precharge duration must not be negative")
        if not self.enabled:
            return
        if self.t_ra_s == 0.0 and self.tau_s > 0.0:
            raise ValueError("a settling time constant without an RA phase does nothing")


    @classmethod
    def from_paper_anchor(cls, t_ra_s: float, **overrides) -> ReferenceConfig:
        """按论文两个锚点构造：残差起点 15b，20b 建立占 65% 的 RA 相位。

        Args:
            t_ra_s: RA 相位时长，单位 s。
            **overrides: 覆盖其它字段，例如 ``ppm2=10.0``。

        Returns:
            已启用的配置，``tau_s`` 由 ``required_tau`` 反解。
        """
        fields = {
            "enabled": True,
            "t_ra_s": t_ra_s,
            "tau_s": required_tau(t_ra_s, SETTLE_FRACTION_OF_RA_PHASE, RESIDUAL_ACCURACY_RATIO),
            "precharge_frac": PRECHARGE_FRACTION_FROM_PAPER,
        }
        fields.update(overrides)
        return cls(**fields)


def settle_error(tau_s: float, t_s: float, swing_v: float) -> float:
    """给出一阶 RC 建立 ``t_s`` 秒后的剩余误差，单位 V。

    Args:
        tau_s: 时间常数，单位 s。取 0 表示理想参考，返回 0。
        t_s: 已建立的时长，单位 s。
        swing_v: 需要建立的摆幅，单位 V。

    Returns:
        剩余误差 ``swing_v * exp(-t_s / tau_s)``；``tau_s <= 0`` 或 ``t_s < 0``
        按物理边界处理（理想参考 0，未开始建立则等于整个摆幅）。
    """
    if tau_s < 0:
        raise ValueError("time constant must not be negative")
    if swing_v < 0:
        raise ValueError("swing must not be negative")
    if tau_s == 0.0:
        return 0.0
    if t_s <= 0.0:
        return float(swing_v)
    return float(swing_v * np.exp(-t_s / tau_s))


def precharge_fraction(tau_s: float, t_precharge_s: float) -> float:
    """给出预充相位结束时参考已建立的比例，无量纲，取值 [0, 1)。

    Args:
        tau_s: 时间常数，单位 s。
        t_precharge_s: 预充时长，单位 s。

    Returns:
        ``1 - exp(-t_precharge_s / tau_s)``；``tau_s == 0`` 时为 0（理想参考
        不需要预充，调用方应直接用 0 误差而不是依赖这个返回值）。
    """
    if tau_s <= 0:
        return 0.0
    if t_precharge_s <= 0:
        return 0.0
    return float(1.0 - np.exp(-t_precharge_s / tau_s))


def dynamic_reference(
    precharge_frac: float,
    tau_s: float,
    t_precharge_s: float,
    t_ra_s: float,
    swing_v: float,
) -> float:
    """给出 RA 相位结束时参考节点的剩余误差，单位 V。

    模型：预充相位结束时已建立 ``precharge_frac``，剩余 ``(1-precharge_frac)``
    的摆幅在多路开关切到 REF_IN 之后按同一个 τ 继续建立 ``t_ra_s``。

    ``t_precharge_s`` 只用于与 ``precharge_frac`` 互相校验：若两者不自洽，说明
    调用方把「粗缓冲器的建立」与「REF_IN 的建立」混用了两个不同的 τ，结果会
    悄悄偏乐观或偏悲观。这里选择直接报错而不是容忍。

    Args:
        precharge_frac: 预充结束时已建立的比例，[0, 1]。
        tau_s: 时间常数，单位 s。0 表示理想参考。
        t_precharge_s: 预充时长，单位 s。
        t_ra_s: RA 相位时长，单位 s。
        swing_v: 总摆幅，单位 V。可以是标量或逐样本数组——参考要驱动的电荷量
            随转换码变化，逐样本摆幅正是参考建立产生 INL 的机制。

    Returns:
        剩余误差电压，单位 V。标量输入给标量，数组输入给同形状数组。
    """
    if not 0.0 <= precharge_frac <= 1.0:
        raise ValueError("precharge fraction must lie in [0, 1]")
    if tau_s < 0:
        raise ValueError("time constant must not be negative")
    if np.any(np.asarray(swing_v) < 0):
        raise ValueError("swing must not be negative")
    if tau_s == 0.0:
        return 0.0 if np.ndim(swing_v) == 0 else np.zeros_like(np.asarray(swing_v, float))
    if t_precharge_s > 0.0:
        implied = precharge_fraction(tau_s, t_precharge_s)
        if not np.isclose(implied, precharge_frac, rtol=1e-9, atol=1e-12):
            raise ValueError(
                "precharge fraction and precharge time disagree under one tau: "
                f"given {precharge_frac:g}, implied {implied:g}"
            )
    if t_ra_s < 0:
        raise ValueError("RA phase duration must not be negative")
    residual = (1.0 - precharge_frac) * np.asarray(swing_v, dtype=np.float64)
    residual = residual * np.exp(-t_ra_s / tau_s)
    return float(residual) if np.ndim(swing_v) == 0 else residual


def required_tau(t_ra_s: float, settle_fraction: float, accuracy_ratio: float) -> float:
    """反解时间常数：要求在 ``settle_fraction * t_ra_s`` 内把误差压到 1/accuracy_ratio。

    由 ``E(t) = E0 * exp(-t/tau)``，令 ``E(f*T) = E0/ratio`` 得
    ``tau = f*T / ln(ratio)``。

    论文给的是 f=0.65、ratio=2^5（15b 起、20b 止），对应 ``tau = 0.1876*T``。
    这是单极点假设下的派生示例，不是论文披露或电路测得的时间常数。

    Args:
        t_ra_s: RA 相位时长，单位 s。
        settle_fraction: 达到目标精度的时刻占 RA 相位的比例，(0, 1]。
        accuracy_ratio: 初始误差与目标误差之比，必须大于 1。

    Returns:
        时间常数，单位 s。
    """
    if t_ra_s <= 0:
        raise ValueError("RA phase duration must be positive")
    if not 0.0 < settle_fraction <= 1.0:
        raise ValueError("settle fraction must lie in (0, 1]")
    if accuracy_ratio <= 1.0:
        raise ValueError("accuracy ratio must exceed one")
    return float(settle_fraction * t_ra_s / np.log(accuracy_ratio))


def ra_transfer(x, ppm2: float = 0.0, ppm3: float = 0.0, full_scale_v: float = 1.0):
    """给出 RA 的二阶/三阶传输非线性，输入输出同为电压。

    ``y = x * (1 + ppm2*1e-6*u + ppm3*1e-6*u^2)``，其中 ``u = x/full_scale_v``。
    系数按**满幅相对值**给，所以 ``ppm2=10`` 在 ``x = full_scale_v`` 处产生
    ``10e-6 * full_scale_v`` 的绝对偏差。

    二阶项在差分信号上是偶函数，不会被差分抵消，表现为抛物线形 INL（两端同号）；
    三阶项是奇函数，表现为 S 形。两者都要留着，因为它们对 INL 形状的作用不同。

    Args:
        x: 输入电压，标量或数组，单位 V。
        ppm2: 二阶系数，ppm（相对满幅）。
        ppm3: 三阶系数，ppm（相对满幅）。
        full_scale_v: 归一化用的满幅，单位 V，必须为正。

    Returns:
        与 ``x`` 同形状的输出电压，单位 V。
    """
    if full_scale_v <= 0:
        raise ValueError("full scale must be positive")
    values = np.asarray(x, dtype=np.float64)
    u = values / full_scale_v
    scaled = values * (1.0 + ppm2 * 1e-6 * u + ppm3 * 1e-6 * u * u)
    return float(scaled) if np.isscalar(x) or values.ndim == 0 else scaled
