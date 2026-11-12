"""显式分段电容数据与静态电荷算子。

几何、制造电容、耦合/寄生/反馈电容和名义码表均由调用者提供。
本模块不再从20.5pF、DR、元件数或名义增益猜测器件值，也不生成DEM或分数电容开关。
Q/C_signal表示输入等效参考贡献；Q/C_eff才是指定净电荷的浮置顶板响应。静态RA的Q/Cf算子另行给出，
不替代真实放大器、初始状态或建立过程。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.config import ConfigError
from adi_model.dem import (
    REF_HIGH,
    REF_LOW,
    SIGNAL,
    MaskBinding,
    SamplingConnections,
    SplitSwitchCommand,
    SplitSwitchGeometry,
    _integer_array,
    split_switch_command,
)


def _finite_array(value, name: str, *, ndim: int, positive: bool) -> np.ndarray:
    if value is None:
        raise ConfigError(f"{name} [F] is required by the active physical charge operator")
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf":
        raise ConfigError(f"{name} [F] must contain real numeric values")
    array = raw.astype(float)
    if array.ndim != ndim or np.any(~np.isfinite(array)):
        raise ConfigError(f"{name} [F] must be a finite {ndim}-D array")
    if np.any(array <= 0 if positive else array < 0):
        qualifier = "positive" if positive else "nonnegative"
        raise ConfigError(f"{name} [F] must be {qualifier}")
    result = array.copy()
    result.flags.writeable = False
    return result


def _sample_values(value, shape: tuple[int, int], name: str) -> np.ndarray:
    if value is None:
        raise ConfigError(f"{name} [V] is required by the selected physical connection")
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf":
        raise ConfigError(f"{name} [V] must contain real numeric values")
    array = raw.astype(float)
    if array.ndim == 0:
        array = np.full(shape, float(array))
    if array.shape != shape or np.any(~np.isfinite(array)):
        raise ConfigError(f"{name} [V] must be finite scalar or shape {shape}")
    return array


def _active_reference(value, shape: tuple[int, int], name: str, active: bool) -> np.ndarray:
    # 未连接轨使用的只是未消费占位，不表示虚构了零伏参考源。
    return _sample_values(value, shape, name) if active else np.zeros(shape)


@dataclass(frozen=True)
class NominalCodebook:
    """数字已知的显式码→输入等效名义电压表；不从实际制造电容生成。"""

    code_min: int
    code_max: int
    voltage_v: np.ndarray

    def __post_init__(self) -> None:
        """校验显式整数码域及每码名义电压，不产生物理器件参数。"""
        for name in ("code_min", "code_max"):
            value = getattr(self, name)
            if isinstance(value, bool | np.bool_) or not isinstance(value, int | np.integer):
                raise ConfigError(f"nominal_codebook.{name} must be an integer")
            object.__setattr__(self, name, int(value))
        if self.code_max < self.code_min:
            raise ConfigError("nominal codebook has an empty integer code domain")
        raw_voltage = np.asarray(self.voltage_v)
        if raw_voltage.dtype.kind not in "iuf":
            raise ConfigError("nominal_codebook.voltage_v [V] must contain real numbers")
        voltage = raw_voltage.astype(float)
        if voltage.shape != (self.code_max - self.code_min + 1,) or np.any(~np.isfinite(voltage)):
            raise ConfigError(
                "nominal_codebook.voltage_v [V] must specify every legal integer code"
            )
        voltage = voltage.copy()
        voltage.flags.writeable = False
        object.__setattr__(self, "voltage_v", voltage)

    @property
    def levels(self) -> int:
        """返回显式名义表包含的合法整数码数。"""
        return len(self.voltage_v)

    def evaluate(self, code) -> np.ndarray:
        """逐整数码查询数字已知的输入等效名义电压[V]。"""
        values = _integer_array(code, "nominal RDAC code")
        if np.any((values < self.code_min) | (values > self.code_max)):
            raise ConfigError("RDAC code is outside the nominal codebook; no implicit clipping")
        return self.voltage_v[values - self.code_min]


@dataclass(frozen=True)
class SplitChip:
    """固定制造状态；C_main/C_sub为(P,M/S)，其余逐片元件为(P,)，单位F。

    不要求随机sigma，因为调用者已给实际电容。主寄生仅在真实浮置顶板/采样节点电荷计算时必需；
    反馈电容仅在静态RA的Q/Cf计算时必需。
    """

    C_main: np.ndarray
    C_sub: np.ndarray
    C_bridge: np.ndarray
    c_p_sub: np.ndarray
    c_p_main: np.ndarray | None = None
    C_feedback_true: float | None = None

    def __post_init__(self) -> None:
        """校验并固定实际电容数组；缺少非活跃可选参数时保持None。"""
        main = _finite_array(self.C_main, "C_main", ndim=2, positive=True)
        sub = _finite_array(self.C_sub, "C_sub", ndim=2, positive=True)
        if main.shape[0] != sub.shape[0] or min(*main.shape, *sub.shape) < 1:
            raise ConfigError("split physical arrays require matching nonempty slice dimensions")
        bridge = _finite_array(self.C_bridge, "C_bridge", ndim=1, positive=True)
        parasitic = _finite_array(self.c_p_sub, "c_p_sub", ndim=1, positive=False)
        if bridge.shape != (len(main),) or parasitic.shape != (len(main),):
            raise ConfigError("bridge/sub-parasitic capacitances must identify each physical slice")
        object.__setattr__(self, "C_main", main)
        object.__setattr__(self, "C_sub", sub)
        object.__setattr__(self, "C_bridge", bridge)
        object.__setattr__(self, "c_p_sub", parasitic)
        if self.c_p_main is not None:
            cp = _finite_array(self.c_p_main, "c_p_main", ndim=1, positive=False)
            if cp.shape != (len(main),):
                raise ConfigError("main parasitic capacitance must identify each physical slice")
            object.__setattr__(self, "c_p_main", cp)
        if self.C_feedback_true is not None:
            if isinstance(self.C_feedback_true, bool | np.bool_):
                raise ConfigError("C_feedback_true [F] cannot be a boolean")
            cf = float(self.C_feedback_true)
            if not np.isfinite(cf) or cf <= 0:
                raise ConfigError("C_feedback_true [F] must be finite and positive")
            object.__setattr__(self, "C_feedback_true", cf)

    @property
    def n_slices(self) -> int:
        """返回实际电容数据中的物理slice数。"""
        return self.C_main.shape[0]

    @property
    def n_main(self) -> int:
        """返回每个slice明确给出的主阵列物理电容数。"""
        return self.C_main.shape[1]

    @property
    def n_sub(self) -> int:
        """返回每个slice明确给出的子阵列物理电容数。"""
        return self.C_sub.shape[1]

    def require_main_parasitic(self) -> np.ndarray:
        """要求实际节点电荷求解所需的主节点寄生[F]。"""
        if self.c_p_main is None:
            raise ConfigError(
                "c_p_main [F] is required for sampled-node or floating-topplate charge"
            )
        return self.c_p_main

    def require_feedback(self) -> float:
        """要求静态RA电荷算子所需的反馈电容[F]。"""
        if self.C_feedback_true is None:
            raise ConfigError(
                "C_feedback_true [F] is required for the static RA charge/Cf operator"
            )
        return self.C_feedback_true


def build_split_chip(
    *,
    main_cap_f,
    sub_cap_f,
    bridge_cap_f,
    sub_parasitic_f,
    main_parasitic_f=None,
    feedback_cap_f: float | None = None,
) -> SplitChip:
    """仅打包显式器件值，不从Config制造随机芯片或反推缺少的元件。"""
    return SplitChip(
        C_main=main_cap_f,
        C_sub=sub_cap_f,
        C_bridge=bridge_cap_f,
        c_p_sub=sub_parasitic_f,
        c_p_main=main_parasitic_f,
        C_feedback_true=feedback_cap_f,
    )


def _geometry_for(chip: SplitChip, slice_ids) -> tuple[SplitSwitchGeometry, np.ndarray]:
    ids = _integer_array(slice_ids, "physical slice IDs", ndim=2)
    if ids.shape[1] < 1:
        raise ConfigError("physical charge requires a nonempty active slice group")
    geometry = SplitSwitchGeometry(chip.n_main, chip.n_sub, chip.n_slices, ids.shape[1])
    return geometry, geometry.validate_slice_ids(ids)


def split_capacitance_coefficients(chip: SplitChip, slice_ids) -> dict[str, np.ndarray | None]:
    """明确两节点拓扑的线性系数；不把一个Ctotal同时用作信号/负载/噪声电容。

    对每个独立sub节点，D=Cb+B+Cp_sub，beta=Cb/D。C_signal=A+beta*B是全输入连接时的残差归一化系数。
    C_eff=A+Cp_main+Cb*(B+Cp_sub)/D是浮置主节点系数，主寄生未知则保持None。
    """
    _, ids = _geometry_for(chip, slice_ids)
    a = chip.C_main[ids].sum(axis=-1)
    b = chip.C_sub[ids].sum(axis=-1)
    cb = chip.C_bridge[ids]
    cp_sub = chip.c_p_sub[ids]
    denominator = cb + b + cp_sub
    beta = cb / denominator
    c_slice = a + beta * b
    c_eff_slice = None
    if chip.c_p_main is not None:
        c_eff_slice = a + chip.c_p_main[ids] + cb * (b + cp_sub) / denominator
    return {
        "beta": beta,
        "c_slice": c_slice,
        "c_signal": c_slice.sum(axis=-1),
        "c_eff_slice": c_eff_slice,
        "c_eff": None if c_eff_slice is None else c_eff_slice.sum(axis=-1),
    }


@dataclass(frozen=True)
class DACCharge:
    """参考底板驱动的电荷项，不是包含初始自由电荷的真实顶板电压。"""

    charge_c: np.ndarray
    slice_charge_c: np.ndarray
    signal_capacitance_f: np.ndarray
    effective_topplate_capacitance_f: np.ndarray | None
    beta: np.ndarray
    input_equivalent_v: np.ndarray


def charge_from_masks(
    chip: SplitChip,
    slice_ids,
    command: SplitSwitchCommand,
    *,
    reference_high_v=None,
    reference_low_v=None,
) -> DACCharge:
    """实际整数mask到Q_D=S_main+beta*S_sub，单位C。

    参考电压相对调用者声明的节点坐标；不从输入满幅v_fs推导。只在实际使用某轨时要求该轨。
    input_equivalent_v=Q_D/C_signal，不能称为真实保持顶板电压。
    """
    geometry, ids = _geometry_for(chip, slice_ids)
    if not isinstance(command, SplitSwitchCommand):
        raise ConfigError("physical reference charge requires SplitSwitchCommand masks")
    masks = command.validated(geometry, len(ids))
    shape = ids.shape
    use_high = bool(np.any(masks.main_on) or np.any(masks.sub_on))
    use_low = bool(np.any(~masks.main_on) or np.any(~masks.sub_on))
    high = _active_reference(reference_high_v, shape, "reference_high_v", use_high)
    low = _active_reference(reference_low_v, shape, "reference_low_v", use_low)
    main_v = np.where(masks.main_on, high[..., None], low[..., None])
    sub_v = np.where(masks.sub_on, high[..., None], low[..., None])
    coefficient = split_capacitance_coefficients(chip, ids)
    beta = coefficient["beta"]
    c_signal = coefficient["c_signal"]
    assert isinstance(beta, np.ndarray) and isinstance(c_signal, np.ndarray)
    q_main = (chip.C_main[ids] * main_v).sum(axis=-1)
    q_sub = (chip.C_sub[ids] * sub_v).sum(axis=-1)
    q_slice = q_main + beta * q_sub
    charge = q_slice.sum(axis=-1)
    c_eff = coefficient["c_eff"]
    assert c_eff is None or isinstance(c_eff, np.ndarray)
    return DACCharge(charge, q_slice, c_signal, c_eff, beta, charge / c_signal)


@dataclass(frozen=True)
class SamplingCharge:
    """采样闭合沿的节点电荷；node_charge_c最后一维为主/sub节点。

    signal_capacitance_f仅为Signal连接的底板驱动系数；它不包含采样top随输入变化时的额外传递。
    normalization_capacitance_f是全输入连接归一化量，二者不代替实际动态信号增益、负载或噪声电容。
    """

    node_charge_c: np.ndarray
    effective_node_charge_c: np.ndarray
    bottom_drive_charge_c: np.ndarray
    main_bottom_v: np.ndarray
    sub_bottom_v: np.ndarray
    sampled_top_v: np.ndarray
    sampled_subnode_v: np.ndarray
    signal_capacitance_f: np.ndarray
    normalization_capacitance_f: np.ndarray


def charge_from_sampling_connections(
    chip: SplitChip,
    slice_ids,
    connections: SamplingConnections,
    *,
    input_voltage_v,
    sampled_top_v,
    sampled_subnode_v,
    reference_high_v=None,
    reference_low_v=None,
) -> SamplingCharge:
    """由显式Signal/RefLo/RefHi连接及实际采样节点电压计算守恒自由电荷。

    所有V采用同一明确坐标；sampled_top_v/subnode_v均必填，不默认为零。
    本算子不推进RC、不产生噪声、不据dither字猜测连接，也不声称完整开关电路。
    """
    geometry, ids = _geometry_for(chip, slice_ids)
    if not isinstance(connections, SamplingConnections):
        raise ConfigError("sampled charge requires explicit SamplingConnections")
    connection = connections.validated(geometry, len(ids))
    shape = ids.shape
    signal_used = bool(np.any(connection.main == SIGNAL) or np.any(connection.sub == SIGNAL))
    signal = _active_reference(input_voltage_v, shape, "input_voltage_v", signal_used)
    high_used = bool(np.any(connection.main == REF_HIGH) or np.any(connection.sub == REF_HIGH))
    low_used = bool(np.any(connection.main == REF_LOW) or np.any(connection.sub == REF_LOW))
    high = _active_reference(reference_high_v, shape, "reference_high_v", high_used)
    low = _active_reference(reference_low_v, shape, "reference_low_v", low_used)
    top = _sample_values(sampled_top_v, shape, "sampled_top_v")
    subnode = _sample_values(sampled_subnode_v, shape, "sampled_subnode_v")
    main_v = np.where(
        connection.main == SIGNAL,
        signal[..., None],
        np.where(connection.main == REF_HIGH, high[..., None], low[..., None]),
    )
    sub_v = np.where(
        connection.sub == SIGNAL,
        signal[..., None],
        np.where(connection.sub == REF_HIGH, high[..., None], low[..., None]),
    )
    c_main = chip.C_main[ids]
    c_sub = chip.C_sub[ids]
    cb = chip.C_bridge[ids]
    cp_main = chip.require_main_parasitic()[ids]
    cp_sub = chip.c_p_sub[ids]
    q_main_node = (c_main * (top[..., None] - main_v)).sum(axis=-1)
    q_main_node += cb * (top - subnode) + cp_main * top
    q_sub_node = (c_sub * (subnode[..., None] - sub_v)).sum(axis=-1)
    q_sub_node += cb * (subnode - top) + cp_sub * subnode
    coefficient = split_capacitance_coefficients(chip, ids)
    beta = coefficient["beta"]
    normalization = coefficient["c_signal"]
    assert isinstance(beta, np.ndarray) and isinstance(normalization, np.ndarray)
    q_bottom = (c_main * main_v).sum(axis=-1) + beta * (c_sub * sub_v).sum(axis=-1)
    input_cap = (c_main * (connection.main == SIGNAL)).sum(axis=-1)
    input_cap += beta * (c_sub * (connection.sub == SIGNAL)).sum(axis=-1)
    return SamplingCharge(
        np.stack((q_main_node, q_sub_node), axis=-1),
        (q_main_node + beta * q_sub_node).sum(axis=-1),
        q_bottom.sum(axis=-1),
        main_v,
        sub_v,
        top.copy(),
        subnode.copy(),
        input_cap.sum(axis=-1),
        normalization,
    )


def topplate_voltage_from_effective_charge(chip: SplitChip, slice_ids, effective_charge_c):
    """给定净等效电荷[C]后的浮置顶板响应Q/C_eff；不生成初始电荷。"""
    coefficient = split_capacitance_coefficients(chip, slice_ids)
    c_eff = coefficient["c_eff"]
    if c_eff is None:
        raise ConfigError("c_p_main [F] is required for floating topplate voltage")
    charge = np.asarray(effective_charge_c, dtype=float)
    if charge.shape != c_eff.shape or np.any(~np.isfinite(charge)):
        raise ConfigError("effective charge [C] must match the active sample groups")
    return charge / c_eff


class SplitDAC:
    """显式物理电荷与独立数字名义表；不提供aggregate轮转或隐式几何。"""

    def __init__(
        self,
        cfg,
        chip: SplitChip | None,
        *,
        nominal_codebook: NominalCodebook | None = None,
        mask_binding: MaskBinding | None = None,
        geometry: SplitSwitchGeometry | None = None,
    ):
        """绑定显式物理对象与独立数字名义表；不推断缺少的绑定。"""
        self.cfg = cfg
        self.chip = chip
        self.nominal_codebook = nominal_codebook
        self.mask_binding = mask_binding
        self._geometry = geometry

    @property
    def geometry(self) -> SplitSwitchGeometry:
        """返回显式数字尺寸和码域；缺数据时拒绝码到mask求值。"""
        if self._geometry is not None:
            return self._geometry
        if self.chip is None:
            raise ConfigError("explicit digital geometry is required for code-to-mask selection")
        book = self.nominal_codebook
        return SplitSwitchGeometry(
            self.chip.n_main,
            self.chip.n_sub,
            self.chip.n_slices,
            self.cfg.n_active,
            None if book is None else book.code_min,
            None if book is None else book.code_max,
        )

    @property
    def levels(self) -> int:
        """返回显式名义码表的电平数，不从物理单元数推导。"""
        if self.nominal_codebook is None:
            raise ConfigError(
                "nominal RDAC codebook is required; levels are not inferred from counts"
            )
        return self.nominal_codebook.levels

    def evaluate_nominal(self, code) -> np.ndarray:
        """查询数字已知名义V，不读取实际制造电容。"""
        if self.nominal_codebook is None:
            raise ConfigError(
                "nominal_codebook [digital-known V] is required by nominal reconstruction"
            )
        return self.nominal_codebook.evaluate(code)

    def select(self, code, state, *, slice_ids) -> SplitSwitchCommand:
        """调用显式数字绑定生成同笔实际物理mask。"""
        return split_switch_command(
            self.geometry, code, state, slice_ids=slice_ids, binding=self.mask_binding
        )

    def charge_from_masks(self, slice_ids, command: SplitSwitchCommand, **references) -> DACCharge:
        """求给定实际mask的静态参考电荷[C]及明确节点派生量。"""
        if self.chip is None:
            raise ConfigError("actual physical capacitances are required for charge evaluation")
        return charge_from_masks(self.chip, slice_ids, command, **references)

    def evaluate_physical(self, code, state, *, slice_ids, **references) -> np.ndarray:
        """返回参考电荷的输入等效V，不是含初始电荷的真实顶板电压。"""
        command = self.select(code, state, slice_ids=slice_ids)
        return self.charge_from_masks(slice_ids, command, **references).input_equivalent_v

    def static_ra_output_from_charge(self, residue_charge_c) -> np.ndarray:
        """指定RA静态电荷合同下Q/Cf；不推导闭环噪声、摆幅或动态建立。"""
        if self.chip is None:
            raise ConfigError("physical feedback capacitance is required for static RA output")
        charge = np.asarray(residue_charge_c, dtype=float)
        if np.any(~np.isfinite(charge)):
            raise ConfigError("RA residue charge [C] must be finite")
        return charge / self.chip.require_feedback()
