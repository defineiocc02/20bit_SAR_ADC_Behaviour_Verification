"""具名 profile 注册表：目标、拓扑与实验参数。

本模块只放**数据与来源**，不放算法分支。运行时必须显式选一个 profile，
并把 ``profile_id``/``topology_id`` 落到结果里；禁止静默回退。

当前只注册 ADI 主 profile 与工程 10MS/s profile。专利七片例是
``patent_examples``，只用于机制试验，不作为整芯片配置。
"""

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
        topology_id="adi_isscc2024_40m_6vpp",
        profile_id="adi_isscc2024_40m_6vpp",
        n_slices_pool=18,
        n_slices_active=8,
        n_main=63,
        n_sub=8,
        c_unit_f=40.0390625e-15,
        c_bridge_f=45.75892857142857e-15,
        c_feedback_f=640.625e-15,
        codebook_id="uniform512",
        source_ids=("PAPER-01", "PAPER-02", "PAPER-03"),
    ),
    fs_hz=40e6,
    v_fs_peak_v=3.0,
    v_span_v=6.0,
    adc2_bits=12,
    description="ISSCC 2024 9.8：40 MS/s、±3 V、20b、18/8/8/2、9b 前级",
    source_ids=("PAPER-01", "PAPER-02", "SLIDE-01"),
)

ENG_10M = Profile(
    profile_id="engineering_10m_10vpp",
    topology=TopologySpec(
        topology_id="engineering_10m_10vpp",
        profile_id="engineering_10m_10vpp",
        n_slices_pool=18,
        n_slices_active=8,
        n_main=17,
        n_sub=5,
        c_unit_f=1e-15,
        c_bridge_f=1e-15,
        c_feedback_f=0.0,
        codebook_id="task_uniform512",
        normalization_only=True,
        source_ids=("TASK-10M-01",),
    ),
    fs_hz=10e6,
    v_fs_peak_v=5.0,
    v_span_v=10.0,
    adc2_bits=12,
    description="工程 10 MS/s 归一化机制例：10 Vpp、17+5、1 fF 单位电容、Cf=0（不可构建物理 RA）",
    source_ids=("TASK-10M-01",),
)

PATENT_FIG19 = Profile(
    profile_id="patent_fig19_7slice",
    topology=TopologySpec(
        topology_id="patent_fig19_7slice",
        profile_id="patent_fig19_7slice",
        n_slices_pool=7,
        n_slices_active=7,
        n_main=7,
        n_sub=1,
        c_unit_f=1e-15,
        c_bridge_f=0.0,
        c_feedback_f=0.0,
        codebook_id="fig19_seven",
        normalization_only=True,
        source_ids=("PAT-D-03", "PAT-D-04", "PAT-D-05"),
    ),
    fs_hz=10e6,
    v_fs_peak_v=5.0,
    v_span_v=10.0,
    adc2_bits=12,
    description="US10505561B2 Fig.19 七片机制例；只用于 dither 机制试验",
    source_ids=("PAT-D-03", "PAT-D-04", "PAT-D-05"),
)

PROFILES: dict[str, Profile] = {
    p.profile_id: p for p in (ADI_40M, ENG_10M, PATENT_FIG19)
}

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
