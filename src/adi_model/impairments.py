# =============================================================================
# 文件名     : impairments.py
# 模块名     : adi_model.impairments
# 版本       : v1.0
# 功能       : 非理想效应的统一开关面板——一处声明、一行开关、默认全关。
#
# 设计说明   :
#   * 这个模块**不实现任何物理**，只把散在 NoiseConfig / ReferenceConfig 里的
#     开关收成一份可读的清单。物理在各自模块里，这里只做装配。
#   * 分两类，且必须分开：已实现的能真开，未实现的开到就报错。把未实现的
#     也标成「可开」会让实验悄悄跑出一条什么都没发生的曲线——那比没有开关更坏。
#   * 默认全关且每个开关独立：任开一项，其它项的抽样必须逐位不受影响。
#     这条由 NoiseConfig 的 SeedSequence([seed, tag]) 派生保证，见 noise.py。
#   * σ 的数值不写在这里。impairments 只回答「开不开」，不回答「开多大」；
#     数值由调用方给（对标用 task2/eight/noise_budget.py 的预算表）。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M10a。
# =============================================================================

"""非理想效应的统一开关面板。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from adi_model.capacitors import ExperimentConfig
from adi_model.noise import NoiseConfig
from adi_model.reference import (
    PRECHARGE_FRACTION_FROM_PAPER,
    RESIDUAL_ACCURACY_RATIO,
    SETTLE_FRACTION_OF_RA_PHASE,
    ReferenceConfig,
    required_tau,
)

# 已经接到主路径、打开就真生效的项。
IMPLEMENTED = (
    "ktc",
    "reference_noise",
    "comparator_noise",
    "adc2_noise",
    "ra_offset",
    "reference_settling",
)

# 有纯函数或频域模型、但尚未接进 experiment.run() 的项。
# 列在这里是为了让「还没做」可见，而不是让它们悄悄不生效。
PLANNED = (
    "ra_nonlinearity",
    "timing_mismatch",
    "dac_bandwidth",
    "c_v_nonlinearity",
)

# 未实现项各自缺什么，报错时直接说清楚，避免使用者以为是开关写错了。
_PLANNED_REASON = {
    "ra_nonlinearity": "ra_transfer 已有纯函数，但它是输出满幅口径（10 V），"
    "而 _capture 里的残差是输入域（±0.1 V），差一个 RA 增益；接入点待定",
    "timing_mismatch": "TimingConfig 与 run_effects 已用，但 experiment.run() "
    "的采样序列不带逐片时偏，需要先把时偏叠进采样相位",
    "dac_bandwidth": "dac_pole_attenuation 只给频域幅度，接进电荷域需要给每片"
    "电容一个复频响应，超出当前模型范围",
    "c_v_nonlinearity": "尚无实现；且实测表明当前 DNL 的 rms 已优于论文、"
    "峰值由少数离群码决定，加 C-V 只会增加系统性弯曲，未必改善",
}


@dataclass(frozen=True)
class ImpairmentSet:
    """一份非理想效应的开关清单。默认全关。

    每个字段对应一个物理机制，互相独立。``apply`` 把它变成一份
    ``ExperimentConfig``；未实现的项若被打开，``apply`` 会直接报错。

    Attributes:
        ktc: 采样网络 kT/C 热噪声（每侧 20.5 pF，差分 √2 倍）。
        reference_noise: 参考路径（RA + REF_IN + 预充残差）的输入等效噪声。
        comparator_noise: SADC 比较器的输入等效噪声。
        adc2_noise: 二级量化与额外抖动的输入等效噪声。
        ra_offset: RA 常数失调，残差域。
        reference_settling: 参考动态建立（RA 相位内未建立完的剩余误差）。
        ra_nonlinearity: RA 传输非线性（未接）。
        timing_mismatch: 片间采样时偏（未接）。
        dac_bandwidth: DAC 有限带宽（未接）。
        c_v_nonlinearity: 电容电压系数（未实现）。
    """

    ktc: bool = False
    reference_noise: bool = False
    comparator_noise: bool = False
    adc2_noise: bool = False
    ra_offset: bool = False
    reference_settling: bool = False
    ra_nonlinearity: bool = False
    timing_mismatch: bool = False
    dac_bandwidth: bool = False
    c_v_nonlinearity: bool = False

    def enabled_names(self) -> tuple[str, ...]:
        """返回所有被打开的项名，按声明顺序。"""
        return tuple(name for name in (*IMPLEMENTED, *PLANNED) if getattr(self, name))

    def planned_enabled(self) -> tuple[str, ...]:
        """返回被打开但尚未实现的项名。"""
        return tuple(name for name in PLANNED if getattr(self, name))

    def to_noise(self, budget: dict[str, float]) -> NoiseConfig:
        """按开关与给定 σ 预算装配 ``NoiseConfig``。

        Args:
            budget: 各源的输入等效 σ，单位 V。键名取 ``reference`` /
                ``comparator`` / ``adc2`` / ``ra_offset``。没有对应开关时该值被忽略。

        Returns:
            装配好的噪声配置。全关时返回默认（全零）配置。
        """
        any_on = (
            self.ktc
            or self.reference_noise
            or self.comparator_noise
            or self.adc2_noise
            or self.ra_offset
        )
        # 全关时原样返回默认对象：不能返回一个 enabled=False 但 include_thermal=False
        # 的等价物——字段不等会让 ExperimentConfig 的相等判断失败，也会让
        # asdict 出来的归档多出一处与历史不同的字段。
        if not any_on:
            return NoiseConfig()
        return NoiseConfig(
            enabled=True,
            include_thermal=self.ktc,
            reference_sigma_v=budget.get("reference", 0.0) if self.reference_noise else 0.0,
            comparator_sigma_v=budget.get("comparator", 0.0) if self.comparator_noise else 0.0,
            adc2_sigma_v=budget.get("adc2", 0.0) if self.adc2_noise else 0.0,
            ra_offset_v=budget.get("ra_offset", 0.0) if self.ra_offset else 0.0,
        )

    def to_reference(self, t_ra_s: float = 12.5e-9) -> ReferenceConfig:
        """按开关装配 ``ReferenceConfig``。

        ``tau`` 由论文两个锚点反解（残差起点 15b、20b 建立占 65% 的 RA 相位），
        不是拍出来的；``t_ra_s`` 在本模型没有显式时序，默认取 40 MS/s 的半周期。

        Args:
            t_ra_s: RA 相位时长，单位 s。

        Returns:
            装配好的参考配置；本项关闭时返回默认（关闭）配置。
        """
        if not self.reference_settling:
            return ReferenceConfig()
        return ReferenceConfig(
            enabled=True,
            t_ra_s=t_ra_s,
            tau_s=required_tau(t_ra_s, SETTLE_FRACTION_OF_RA_PHASE, RESIDUAL_ACCURACY_RATIO),
            precharge_frac=PRECHARGE_FRACTION_FROM_PAPER,
        )

    def apply(self, base: ExperimentConfig, budget: dict[str, float] | None = None):
        """把开关套到一份基础配置上，返回新的 ``ExperimentConfig``。

        Args:
            base: 基础配置。
            budget: σ 预算，见 ``to_noise``。None 视为全零。

        Returns:
            已装配噪声与参考两部分的配置。

        Raises:
            NotImplementedError: 打开了 ``PLANNED`` 里的任一项。报错里带上
                该项缺什么，避免使用者以为是自己开关写错。
        """
        missing = self.planned_enabled()
        if missing:
            detail = "；".join(f"{name}: {_PLANNED_REASON[name]}" for name in missing)
            raise NotImplementedError(f"these impairments are not wired into run() yet — {detail}")
        return replace(
            base,
            noise=self.to_noise(budget or {}),
            reference=self.to_reference(),
        )


def only(name: str) -> ImpairmentSet:
    """只打开一项，其余全关。用于「单独开一个源看它自己贡献多少」。

    Args:
        name: 项名，取 ``IMPLEMENTED`` 或 ``PLANNED`` 中的值。

    Returns:
        仅该项为 True 的开关集。

    Raises:
        ValueError: 名字不在清单里。
    """
    if name not in (*IMPLEMENTED, *PLANNED):
        raise ValueError(f"unknown impairment: {name}; known: {(*IMPLEMENTED, *PLANNED)}")
    return replace(ImpairmentSet(), **{name: True})


def all_of(names) -> ImpairmentSet:
    """打开给定的一组项，其余全关。

    Args:
        names: 项名序列。

    Returns:
        合并后的开关集。
    """
    result = ImpairmentSet()
    for name in names:
        result = replace(result, **{name: True})
    return result
