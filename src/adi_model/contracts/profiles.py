# =============================================================================
# 文件名     : profiles.py
# 模块名     : adi_model.contracts.profiles
# 功能       : 登记当前 ADC 的设计标识、采样速率、默认拓扑与 ADC2 位数。
# 设计说明   :
#   * 数据标识用于绑定拓扑、运行记录和校准结果。
#   * 查找配置时按完整标识匹配，无法匹配的输入立即报错。
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass

from adi_model.contracts.specs import TopologySpec


@dataclass(frozen=True)
class Profile:
    """一次运行的具名目标。"""

    profile_id: str
    topology: TopologySpec
    fs_hz: float
    v_fs_peak_v: float
    v_span_v: float
    adc2_bits: int
    caaz: bool = False
    description: str = ""
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.fs_hz <= 0:
            raise ValueError("fs_hz must be positive [Hz]")
        if self.v_fs_peak_v <= 0:
            raise ValueError("v_fs_peak_v must be positive [V]")
        if abs(self.v_span_v - 2.0 * self.v_fs_peak_v) > 1e-12:
            raise ValueError("v_span_v must equal 2*v_fs_peak_v [V]")
        if not 8 <= self.adc2_bits <= 16:
            raise ValueError("adc2_bits must be in [8, 16]")
        if self.caaz:
            raise ValueError("CAAZ is an extension, not a main profile")


ADI_40M = Profile(
    profile_id="adi_isscc2024_40m_6vpp",
    topology=TopologySpec(
        topology_id="adc_digital_ip_v21",
        profile_id="adi_isscc2024_40m_6vpp",
        n_slices_pool=18,
        n_slices_active=8,
        # 数字端寻址十六个主半单元和四个子支路，读取 Ceff/Cf。
        # 绝对法拉值由模拟宏集成方提供，不能把专利示例的电容带到生产 IP。
        n_main=16,
        n_sub=4,
        normalization_only=True,
        codebook_id="rdac_0_to_520",
        source_ids=("PAPER-01", "PAPER-02", "PAPER-03"),
    ),
    fs_hz=40e6,
    v_fs_peak_v=3.0,
    v_span_v=6.0,
    # 工程合同规定 ADC2 窗口 [-0.1875, +0.5625) V。
    # 位数同时决定 RTL 原码口宽度和 Q24 解码中心的步长。
    adc2_bits=13,
    description="ISSCC 2024 9.8：40 MS/s、±3 V、20b、18/8/8/2、9b 前级",
    source_ids=("PAPER-01", "PAPER-02", "SLIDE-01"),
)

# 保留配置查找接口，当前仓库登记一项可执行的 ADC 目标。
PROFILES: dict[str, Profile] = {ADI_40M.profile_id: ADI_40M}

DEFAULT_PROFILE_ID = ADI_40M.profile_id


def load_profile(profile_id: str) -> Profile:
    """按名字取 profile；未知名字必须报错，不得回退默认。"""
    try:
        return PROFILES[profile_id]
    except KeyError as exc:
        raise KeyError(
            f"unknown profile {profile_id!r}; available: {sorted(PROFILES)}"
        ) from exc


def assert_caaz_free() -> None:
    """主 profile 不得携带 CAAZ；扩展必须另建 profile。"""
    bad = [p.profile_id for p in PROFILES.values() if p.caaz]
    if bad:
        raise ValueError(f"main profiles must be CAAZ-free: {bad}")
