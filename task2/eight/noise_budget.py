# =============================================================================
# 文件名     : noise_budget.py
# 模块名     : task2.eight.noise_budget
# 版本       : v1.0
# 功能       : 从论文的公开数反推噪声总量，并给出逐源分配、独占 DR 与灵敏度。
#
# 设计说明   :
#   * 唯一有出处的关系是密度自洽：9.3 nV/√Hz × √20 MHz = 41.59 µV，折回
#     94.15 dB，与论文 94.2 dB 差 0.05 dB。这说明「DR 94.2 + NSD 9.3 + BW 20 MHz」
#     三者互不矛盾，可以拿来做反推的锚点。
#   * 逐源分配（20.10/30.00/20.00/3.00 µV）**全部是 S5_假设**，只有合计值受
#     论文约束。分配的唯一用途是给出三条可证伪斜率（改一个源、看 DR 怎么动），
#     不是声称芯片里真是这个比例。
#   * 满幅口径必须显式写死：论文原文是 "±3V differential input range" 与
#     "6Vpp input range"，即差分半幅 3 V。模型默认 full_scale_peak_v=5.0
#     （差分 10 Vpp）比论文大 1.667 倍，同一个 41.39 µV 会给出高 4.44 dB 的 DR。
#     所以本模块强制要求调用方传满幅，不提供「默认值」来掩盖这个差。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M7。
# =============================================================================

"""从论文公开数反推噪声总量，并给出逐源分配与灵敏度。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.noise import NoiseConfig

# 论文 ISSCC2024 9.8 的公开数，逐条对应原文。
PAPER_DIFFERENTIAL_HALF_RANGE_V = 3.0  # "±3V differential input range"
PAPER_DYNAMIC_RANGE_DB = 94.2
PAPER_SNR_DB = 93.7
PAPER_NSD_NV_PER_SQRT_HZ = 9.3  # 标题里的 9.3nV/rtHz
PAPER_BANDWIDTH_HZ = 20e6

# 源文内部散差：DR 94.2（论文）与 94.6（讲稿）、NSD 9.3（标题）与 8.8（实测）。
# 所以 ±0.5 dB 的窗口来自论文自己，不是给模型放宽。
PAPER_SPREAD_DB = 0.5


@dataclass(frozen=True)
class SourceBudget:
    """一个噪声源的分配值与它在预算里的占比。

    Attributes:
        name: 源名，与 NoiseConfig 的字段对应。
        sigma_v: 输入等效标准差，单位 V。
        evidence: 证据等级。分配值统一标 S5_假设，只有 NTOTAL 那行是论文数。
    """

    name: str
    sigma_v: float
    evidence: str = "S5_假设"

    @property
    def power_v2(self) -> float:
        """方差，单位 V²。"""
        return self.sigma_v**2


# 四源分配。合计 41.39 µV 在 ±3 V 差分满幅下给出 94.19 dB，与论文 94.2 自洽。
# 数值取自计划 §7.2，来源是「按 94.2 dB 反推总量后按经验比例拆开」，不是硅数据。
DEFAULT_BUDGET: tuple[SourceBudget, ...] = (
    SourceBudget("sampling_ktc", 20.10e-6),
    SourceBudget("reference", 30.00e-6),
    SourceBudget("comparator", 20.00e-6),
    SourceBudget("adc2", 3.00e-6),
)


def differential_full_scale_rms(half_range_v: float) -> float:
    """给出差分满幅的 RMS 值，单位 V。

    ``±half_range`` 的差分摆幅是 ``2*half_range`` 峰峰，正弦 RMS 再除 ``2*sqrt(2)``，
    合起来就是 ``half_range/sqrt(2)``。

    Args:
        half_range_v: 差分半幅（单侧摆幅），单位 V。论文是 3.0。

    Returns:
        满幅正弦的 RMS，单位 V。
    """
    if half_range_v <= 0:
        raise ValueError("differential half range must be positive")
    return float(half_range_v * np.sqrt(2.0) / 2.0)


def exclusive_dr(half_range_v: float, sigma_v: float) -> float:
    """给出单个噪声源独占时的动态范围，单位 dB。

    Args:
        half_range_v: 差分半幅，单位 V。
        sigma_v: 该源的输入等效标准差，单位 V。

    Returns:
        ``20*log10(V_FS,rms / sigma)``。
    """
    if sigma_v <= 0:
        raise ValueError("sigma must be positive")
    return float(20.0 * np.log10(differential_full_scale_rms(half_range_v) / sigma_v))


def combine(sigmas) -> float:
    """把若干独立源的标准差合成，单位 V。

    Args:
        sigmas: 各源标准差，单位 V，可迭代。

    Returns:
        合成标准差 ``sqrt(sum(sigma^2))``。
    """
    values = np.asarray(list(sigmas), dtype=np.float64)
    if values.size == 0:
        raise ValueError("need at least one source")
    if np.any(values <= 0):
        raise ValueError("sigma must be positive")
    return float(np.sqrt(np.sum(values**2)))


def required_total_noise(half_range_v: float, dynamic_range_db: float) -> float:
    """反推达到给定 DR 所需的总输入等效噪声，单位 V。

    Args:
        half_range_v: 差分半幅，单位 V。
        dynamic_range_db: 目标动态范围，单位 dB。

    Returns:
        总噪声标准差，单位 V。
    """
    return float(differential_full_scale_rms(half_range_v) / 10.0 ** (dynamic_range_db / 20.0))


def budget_table(half_range_v: float, sources=DEFAULT_BUDGET) -> list[dict]:
    """给出逐源的 σ、功率、占比与独占 DR。

    Args:
        half_range_v: 差分半幅，单位 V。
        sources: ``SourceBudget`` 序列，默认用 ``DEFAULT_BUDGET``。

    Returns:
        每源一行的字典列表，另加一行合计（``name="total"``）。
    """
    total_power = sum(source.power_v2 for source in sources)
    total_sigma = combine(source.sigma_v for source in sources)
    rows = [
        {
            "name": source.name,
            "sigma_v": source.sigma_v,
            "power_v2": source.power_v2,
            "share": source.power_v2 / total_power,
            "exclusive_dr_db": exclusive_dr(half_range_v, source.sigma_v),
            "evidence": source.evidence,
        }
        for source in sources
    ]
    rows.append(
        {
            "name": "total",
            "sigma_v": total_sigma,
            "power_v2": total_power,
            "share": 1.0,
            "exclusive_dr_db": exclusive_dr(half_range_v, total_sigma),
            "evidence": "论文 DR 反推",
        }
    )
    return rows


def noise_config(sources=DEFAULT_BUDGET, **overrides) -> NoiseConfig:
    """把预算表转成一份全开的 ``NoiseConfig``。

    kT/C 的 σ 由 ``thermal_sigma`` 从采样电容算出，不是直接给值；预算表里的
    20.10 µV 只用来校核，不作为输入。这样改电容时噪声会跟着变，
    「20.5 → 10.25 pF 使 DR 降 3 dB」那条斜率才有意义。

    Args:
        sources: 预算序列，默认 ``DEFAULT_BUDGET``。
        **overrides: 覆盖 ``NoiseConfig`` 的其它字段。

    Returns:
        已启用的噪声配置。
    """
    lookup = {source.name: source.sigma_v for source in sources}
    fields = {
        "enabled": True,
        "reference_sigma_v": lookup["reference"],
        "comparator_sigma_v": lookup["comparator"],
        "adc2_sigma_v": lookup["adc2"],
    }
    fields.update(overrides)
    return NoiseConfig(**fields)


def expected_dr_change(ratio: float, share: float) -> float:
    """给出某个源按 ``ratio`` 缩放后的 DR 变化，单位 dB。

    用于把「改一个源」的实测斜率与解析值对照。推导：该源功率占比为 ``share``，
    缩放后总功率变为 ``(1-share) + share*ratio^2`` 倍，DR 变化即
    ``-10*log10(那个倍数)``。

    Args:
        ratio: 该源 σ 的新旧比。
        share: 该源在总功率里的占比，[0, 1]。

    Returns:
        DR 的变化量，单位 dB（变差为负）。
    """
    if ratio <= 0:
        raise ValueError("ratio must be positive")
    if not 0.0 <= share <= 1.0:
        raise ValueError("share must lie in [0, 1]")
    return float(-10.0 * np.log10((1.0 - share) + share * ratio**2))
