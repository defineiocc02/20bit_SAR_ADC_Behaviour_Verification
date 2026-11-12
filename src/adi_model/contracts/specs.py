"""跨模块公共合同：拓扑、事件、样本、命令、观测、冻结系数。

本模块只放**数据结构与校验**，不放电路公式、不放仿真流程。
所有字段名遵循单位后缀：``_v`` 电压[V]、``_f`` 电容[F]、``_c`` 电荷[C]、
``_s`` 时间[s]、``_hz`` 频率[Hz]。无后缀的整数字段是计数/码/ID。

证据层级：本文件是 L0 结构合同；它不证明任何模拟性能。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class TopologySpec:
    """一次运行使用的几何与码本身份。

    ``topology_id`` 与 ``profile_id`` 必须同时出现；禁止静默把一个 profile
    的几何套到另一个 profile 的系数上。
    """

    topology_id: str
    profile_id: str
    n_slices_pool: int = 18
    n_slices_active: int = 8
    n_main: int = 63
    n_sub: int = 8
    c_unit_f: float = 40.0390625e-15
    c_bridge_f: float = 45.75892857142857e-15
    c_feedback_f: float = 640.625e-15
    codebook_id: str = "uniform512"
    mask_bit_order: str = "slice_major_lsb_first"
    slot_to_slice_rule: str = "ordered_active_slots"
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.topology_id), "topology_id must not be empty")
        _require(bool(self.profile_id), "profile_id must not be empty")
        _require(self.n_slices_pool > 0, "n_slices_pool must be positive")
        _require(0 < self.n_slices_active <= self.n_slices_pool,
                 "n_slices_active must be in (0, n_slices_pool]")
        _require(self.n_main > 0 and self.n_sub > 0, "n_main/n_sub must be positive")
        _require(self.c_unit_f > 0, "c_unit_f must be positive [F]")
        _require(self.c_bridge_f >= 0 and self.c_feedback_f >= 0,
                 "c_bridge_f/c_feedback_f must be non-negative [F]")


@dataclass(frozen=True)
class EventRecord:
    """一笔样本上的一个时序事件。

    ``order_key`` 用于同刻排序：先比较第一个分量，再比较第二个。
    """

    time_s: float
    domain: str
    phase: str
    sample_id: int
    epoch: int
    owner: str
    kind: str
    order_key: tuple[int, int] = (0, 0)
    tick: int | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.time_s >= 0.0, "event time must be non-negative [s]")
        _require(bool(self.domain) and bool(self.phase), "domain/phase must not be empty")
        _require(self.sample_id >= 0, "sample_id must be non-negative")
        _require(self.epoch >= 0, "epoch must be non-negative")
        _require(bool(self.owner) and bool(self.kind), "owner/kind must not be empty")


@dataclass(frozen=True)
class SampleContext:
    """一笔样本从接受到退休的身份与冻结控制。"""

    seq: int
    sample_id: int
    epoch: int
    quantizer_id: int
    physical_slice_ids: tuple[int, ...]
    logical_slots: tuple[int, ...]
    dither_word: int = 0
    dither_word_valid: bool = False
    dem_state: int = 0
    accept_time_s: float = 0.0
    aperture_time_s: float = 0.0
    conversion_sample_id: int = 0
    fine_sample_id: int = 0
    calibration_id: str = ""
    valid: bool = True
    error_code: int = 0

    def __post_init__(self) -> None:
        _require(self.seq >= 0 and self.sample_id >= 0, "seq/sample_id must be non-negative")
        _require(self.epoch >= 0, "epoch must be non-negative")
        _require(bool(self.physical_slice_ids), "physical_slice_ids must not be empty")
        _require(len(set(self.physical_slice_ids)) == len(self.physical_slice_ids),
                 "physical_slice_ids must be unique")
        _require(len(self.physical_slice_ids) == len(self.logical_slots),
                 "physical_slice_ids and logical_slots must have equal length")
        _require(0 <= self.dither_word < 64, "dither_word must be 6-bit")


@dataclass(frozen=True)
class AcquisitionCommand:
    """采样相命令；与转换 mask 分开。"""

    sample_id: int
    slice_ids: tuple[int, ...]
    sample_main: tuple[int, ...]
    sample_sub: tuple[int, ...]
    signal_connect: tuple[int, ...]
    binding_id: str
    auxiliary_charge_c: float = 0.0

    def __post_init__(self) -> None:
        _require(bool(self.slice_ids), "slice_ids must not be empty")
        _require(len(self.sample_main) == len(self.sample_sub),
                 "sample_main/sample_sub must have equal length")
        _require(set(self.sample_main) <= {0, 1}, "sample_main must be 0/1")
        _require(set(self.sample_sub) <= {0, 1}, "sample_sub must be 0/1")
        _require(bool(self.binding_id), "binding_id must not be empty")


@dataclass(frozen=True)
class SwitchCommand:
    """转换相命令；``feature`` 允许 2，表示采样/转换重叠。"""

    sample_id: int
    conversion_mask: tuple[int, ...]
    sampling_mask: tuple[int, ...]
    level: str
    feature: int
    binding_id: str

    def __post_init__(self) -> None:
        _require(len(self.conversion_mask) == len(self.sampling_mask),
                 "conversion_mask/sampling_mask must have equal length")
        _require(set(self.conversion_mask) <= {0, 1}, "conversion_mask must be 0/1")
        _require(set(self.sampling_mask) <= {0, 1}, "sampling_mask must be 0/1")
        _require(self.feature in (0, 1, 2), "feature must be 0, 1 or 2")
        _require(bool(self.binding_id), "binding_id must not be empty")


@dataclass(frozen=True)
class DigitalObservation:
    """训练/重构可见的数字观测；不含 plant 真值。"""

    sample_id: int
    coarse_code: int
    adc2_code: int
    dither_word: int
    dem_mask: tuple[int, ...]
    control_snapshot: Mapping[str, Any]
    flags: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require(self.sample_id >= 0, "sample_id must be non-negative")
        _require(self.coarse_code >= 0 and self.adc2_code >= 0, "codes must be non-negative")
        _require(0 <= self.dither_word < 64, "dither_word must be 6-bit")


@dataclass
class PlantTrace:
    """调试/独立参考用；禁止传给训练或重构。"""

    sample_id: int
    node_v: dict[str, Any] = field(default_factory=dict)
    charge_c: dict[str, Any] = field(default_factory=dict)
    cap_f: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FrozenCalibration:
    """冻结系数身份；错误身份必须拒绝，不得“尽量用”。"""

    calibration_id: str
    topology_id: str
    codebook_id: str
    feature_order: tuple[str, ...]
    binding_id: str
    v_range_v: tuple[float, float]
    v_domain: str
    adc2_bin_edges: tuple[int, ...]
    coefficient_unit: str
    sign_convention: str
    q_format: str
    rounding: str
    overflow: str
    training_set_id: str
    mode_id: str

    def __post_init__(self) -> None:
        _require(bool(self.calibration_id) and bool(self.topology_id),
                 "calibration_id/topology_id must not be empty")
        _require(bool(self.codebook_id) and bool(self.binding_id),
                 "codebook_id/binding_id must not be empty")
        _require(len(self.v_range_v) == 2 and self.v_range_v[0] < self.v_range_v[1],
                 "v_range_v must be an increasing pair [V]")
        _require(bool(self.feature_order), "feature_order must not be empty")
        _require(self.rounding in ("floor", "nearest", "trunc"),
                 "rounding must be floor/nearest/trunc")
        _require(self.overflow in ("saturate", "reject"), "overflow must be saturate/reject")

    def accepts(self, topology: TopologySpec) -> bool:
        """系数身份是否与当前拓扑兼容。"""
        return (self.topology_id == topology.topology_id
                and self.codebook_id == topology.codebook_id)
