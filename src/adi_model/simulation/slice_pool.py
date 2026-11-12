"""显式物理slice池：固定电容、真实采集连接和样本所有权。

本模块只求给定器件与实际控制下的静态电荷。
不推断63+8几何、DEM序列、参考电压、末2D扰动电容或制造分布；未绑定的码→mask/采集控制明确拒绝。
RC建立、开关噪声和真实RA过程由相应显式物理模型负责，不能从本池的数学电荷闭合推定。
Q/C_signal是输入等效量，不是保持顶板电压。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from adi_model.control.dem import (
    MaskBinding,
    SamplingConnections,
    SplitSwitchCommand,
    SplitSwitchGeometry,
    _integer_array,
)
from adi_model.control.timing import ScheduleBinding, SlicePlan, build_slice_plan
from adi_model.simulation.config import ConfigError
from adi_model.topology.dac_arch import (
    DACCharge,
    NominalCodebook,
    SamplingCharge,
    SplitChip,
    SplitDAC,
    charge_from_masks,
    charge_from_sampling_connections,
    split_capacitance_coefficients,
    topplate_voltage_from_effective_charge,
)

AcquisitionBinding = Callable[
    [SplitSwitchGeometry, np.ndarray, np.ndarray, np.ndarray | None], SamplingConnections
]

__all__ = ["PhysicalSlicePool", "SlicePlan", "check_causality"]


def check_causality(plan: SlicePlan) -> dict:
    """只检查相邻采集/转换组归属，不证明模拟保持、建立或精度。"""
    conv = _integer_array(plan.conv, "conversion IDs", ndim=2)
    acq = _integer_array(plan.acq, "acquisition IDs", ndim=2)
    if conv.shape != acq.shape:
        raise ConfigError("causality requires matching acquisition/conversion records")
    transitions = max(len(conv) - 1, 0)
    coverage: np.ndarray = np.zeros(transitions, dtype=int)
    for index in range(transitions):
        coverage[index] = len(set(conv[index + 1].tolist()) & set(acq[index].tolist()))
    violation = int(np.count_nonzero(coverage != conv.shape[1]))
    return {
        "transitions": transitions,
        "violations": violation,
        "mean_coverage": float(coverage.mean()) if transitions else float("nan"),
        "causal": violation == 0,
    }


class PhysicalSlicePool:
    """固定制造数据及实际节点电荷；不通过RNG生成未披露物理真值。"""

    tau_rel: np.ndarray | None  # 显式逐片建立系数，无量纲；未知保持None。
    t_skew: np.ndarray | None  # 显式逐片孔径时偏[s]，不自动产生随机值。
    v_os: np.ndarray | None  # 显式逐片输入等效失调[V]，由活跃消费者要求。

    def __init__(
        self,
        cfg,
        *,
        chip: SplitChip,
        nominal_codebook: NominalCodebook | None = None,
        mask_binding: MaskBinding | None = None,
        acquisition_binding: AcquisitionBinding | None = None,
        schedule_binding: ScheduleBinding | SlicePlan | None = None,
        tau_rel=None,
        t_skew=None,
        v_os=None,
    ):
        """保存显式制造数据与控制提供者，不生成未知电气初态。"""
        if not isinstance(chip, SplitChip):
            raise ConfigError("PhysicalSlicePool requires an explicit SplitChip physical object")
        self.cfg = cfg
        self.chip = chip
        self.nominal_codebook = nominal_codebook
        self.mask_binding = mask_binding
        self.acquisition_binding = acquisition_binding
        self.schedule_binding = schedule_binding
        self.n_slices = chip.n_slices
        # 几何用**物理位宽**（端口 5 位），不是装配列数；未声明时退回装配列数。
        _declared_sub = getattr(cfg, "dac_n_sub", None)
        self.geometry = SplitSwitchGeometry(
            chip.n_main,
            chip.n_sub if _declared_sub is None else int(_declared_sub),
            chip.n_slices,
            cfg.n_active,
            None if nominal_codebook is None else nominal_codebook.code_min,
            None if nominal_codebook is None else nominal_codebook.code_max,
        )
        self.n_active = self.geometry.n_active
        # 可切换子单元数：物理列数可能大于它（保留恒接低轨的哑单元位置）。
        from adi_model.topology.capacitors import SUB_WEIGHTS

        self._switched_sub_units = min(chip.n_sub, len(SUB_WEIGHTS))
        # 主阵列同理：S4 那个 1C 的编码规则图上未给，不进名义分母。
        from adi_model.topology.capacitors import FIG11_SWITCHED_MAIN_UNITS

        self._switched_main_units = min(chip.n_main, FIG11_SWITCHED_MAIN_UNITS)
        if chip.n_slices != cfg.n_slices:
            raise ConfigError("actual physical pool size conflicts with the source architecture")
        # 子阵列允许"物理位宽 > 装配列数"：FIG.11 第 5 个 C 是虚线占位（§11），
        # 端口仍是 5 位、器件只装 4 块。反向（装配列数多于物理位宽）仍然拒绝。
        _declared = getattr(cfg, "dac_n_sub", None)
        if _declared is not None and chip.n_sub > int(_declared):
            raise ConfigError("assembled sub columns exceed the declared physical width")
        for field, actual in (("dac_n_main", chip.n_main),):
            configured = getattr(cfg, field, None)
            if configured is not None and (
                isinstance(configured, bool | np.bool_)
                or not isinstance(configured, int | np.integer)
            ):
                raise ConfigError(f"{field} must be an explicit integer physical dimension")
            if configured is not None and configured != actual:
                raise ConfigError(
                    f"actual physical {field} conflicts with the supplied configuration"
                )
        self.dac = SplitDAC(
            cfg,
            chip,
            nominal_codebook=nominal_codebook,
            mask_binding=mask_binding,
            geometry=self.geometry,
        )
        for name, value in (("tau_rel", tau_rel), ("t_skew", t_skew), ("v_os", v_os)):
            if value is None:
                setattr(self, name, None)
                continue
            array = np.asarray(value, dtype=float)
            if array.shape != (self.n_slices,) or np.any(~np.isfinite(array)):
                raise ConfigError(f"{name} must explicitly identify all physical slices")
            if name == "tau_rel" and np.any(array <= 0):
                raise ConfigError("tau_rel must be positive")
            array = array.copy()
            array.flags.writeable = False
            setattr(self, name, array)
        self.reset_electrical_state()

    def reset_electrical_state(self) -> None:
        """清资格，不把未知模拟节点或预充电压设为零。"""
        self._sampled_node_charge_c = np.full((self.n_slices, 2), np.nan)
        self.held_sample: np.ndarray = np.full(self.n_slices, -1, dtype=np.int64)
        self.held_valid: np.ndarray = np.zeros(self.n_slices, dtype=bool)
        self.n_conversions: np.ndarray = np.zeros(self.n_slices, dtype=np.int64)

    def require_slice_parameter(self, name: str, unit: str) -> np.ndarray:
        """供实际动态/孔径消费者检查活跃物理输入，不额外生成随机参数。"""
        if name not in ("tau_rel", "t_skew", "v_os"):
            raise ConfigError(f"unsupported physical slice parameter {name}")
        value = getattr(self, name)
        if value is None:
            raise ConfigError(f"physical pool {name} [{unit}] is required by the active consumer")
        return value

    def plan(
        self, n_samples: int, *, binding: ScheduleBinding | SlicePlan | None = None
    ) -> SlicePlan:
        """读取调用者或构造时提供的显式采集控制计划。"""
        selected = self.schedule_binding if binding is None else binding
        return build_slice_plan(self.cfg, n_samples, binding=selected)

    def select(self, code, state, *, slice_ids) -> SplitSwitchCommand:
        """实际控制记录必须绑定；DEM关闭也不暗中补造未知静态开关映射。"""
        return self.dac.select(code, state, slice_ids=slice_ids)

    def split_coefficients(self, slice_ids) -> dict[str, np.ndarray | None]:
        """只给实际两节点电荷系数；不提供猜测的C_load或C_noise替身。

        子阵列的可切换单元数取 `min(物理列数, 权重表长度)`：物理列数由几何决定（5），
        权重表只是**可切换**单元（4），末列哑单元不计入 β 分母（见 split_capacitance_coefficients）。
        """
        return split_capacitance_coefficients(
            self.chip,
            slice_ids,
            switched_sub_units=self._switched_sub_units,
            switched_main_units=self._switched_main_units,
        )

    def signal_capacitance(self, slice_ids) -> np.ndarray:
        """返回全输入连接时的残差归一化系数C_signal[F]，不是任意采样mask系数。"""
        value = self.split_coefficients(slice_ids)["c_signal"]
        assert isinstance(value, np.ndarray)
        return value

    def charge_from_masks(self, slice_ids, command: SplitSwitchCommand, **references) -> DACCharge:
        """无需controller即可检查给定真实整数mask的静态参考电荷。"""
        return charge_from_masks(
            self.chip,
            slice_ids,
            command,
            switched_sub_units=self._switched_sub_units,
            switched_main_units=self._switched_main_units,
            **references,
        )

    def dac_voltage(self, slice_ids, code, state, *, nominal: bool = False, **references):
        """返回名义表或参考电荷的输入等效V；不代表保持顶板真实节点。"""
        if nominal:
            return self.dac.evaluate_nominal(code)
        return self.dac.evaluate_physical(code, state, slice_ids=slice_ids, **references)

    def topplate_from_effective_charge(self, slice_ids, effective_charge_c):
        """真实浮置拓扑系数下的指定净电荷响应；净电荷由显式节点状态给出。"""
        return topplate_voltage_from_effective_charge(self.chip, slice_ids, effective_charge_c)

    @staticmethod
    def _one_group_voltage(value, n_active: int):
        if value is None:
            return None
        array = np.asarray(value, dtype=float)
        return array[None, :] if array.shape == (n_active,) else value

    def acquire(
        self,
        acq,
        input_voltage_v,
        *,
        sample_id: int,
        sampled_top_v,
        sampled_subnode_v,
        connections: SamplingConnections | None = None,
        control=None,
        reference_high_v=None,
        reference_low_v=None,
    ) -> SamplingCharge:
        """保存闭合沿实际自由电荷，不自行积分RC或施加组平均时偏。

        acq是(A,)实际物理ID；input及两采样节点为同坐标的实际孔径电压。
        connections或acquisition_binding必须给出全部主/子Signal/RefLo/RefHi连接。
        binding只收到数字几何、sample ID、slice IDs和数字control，不收到制造真值。
        """
        group = _integer_array(acq, "acquisition physical IDs", ndim=1)
        ids = self.geometry.validate_slice_ids(group[None, :], n_samples=1)
        if (
            isinstance(sample_id, bool | np.bool_)
            or not isinstance(sample_id, int | np.integer)
            or not np.iinfo(np.int64).min <= sample_id <= np.iinfo(np.int64).max
        ):
            raise ConfigError("sample_id must be an explicit integer")
        if np.any(self.held_valid[group]):
            raise ConfigError("acquisition would overwrite a physical group before consumption")
        selected = connections
        if selected is None:
            if self.acquisition_binding is None:
                raise ConfigError(
                    "sampling connections are unbound; no tail-capacitor default exists"
                )
            selected = self.acquisition_binding(
                self.geometry,
                np.array([sample_id], dtype=np.int64),
                ids.copy(),
                None if control is None else _integer_array(control, "acquisition digital control"),
            )
        if not isinstance(selected, SamplingConnections):
            raise ConfigError("acquisition binding must return SamplingConnections")
        values = {
            name: self._one_group_voltage(value, self.n_active)
            for name, value in (
                ("input_voltage_v", input_voltage_v),
                ("sampled_top_v", sampled_top_v),
                ("sampled_subnode_v", sampled_subnode_v),
                ("reference_high_v", reference_high_v),
                ("reference_low_v", reference_low_v),
            )
        }
        # 采样阶段所有物理电容都接信号（含 S4 与子阵列哑位），
        # 因此归一化必须用**全部列**；只有转换阶段才排除不参与码的单元。
        # 2026-11-13：若这里也用切换单元数，采样归一化会小 3.3%，
        # 残差出现与输入成正比的偏置（实测 slope 0.0306）。
        packet = charge_from_sampling_connections(self.chip, ids, selected, **values)
        self._sampled_node_charge_c[group] = packet.node_charge_c[0]
        self.held_sample[group] = int(sample_id)
        self.held_valid[group] = True
        return packet

    def held_node_charge(self, conv, *, sample_id: int) -> np.ndarray:
        """读取同笔采样闭合沿的主/sub自由电荷记录[C]，不查询模拟实时节点。"""
        group = _integer_array(conv, "conversion physical IDs", ndim=1)
        self.geometry.validate_slice_ids(group[None, :], n_samples=1)
        if (
            isinstance(sample_id, bool | np.bool_)
            or not isinstance(sample_id, int | np.integer)
            or not np.iinfo(np.int64).min <= sample_id <= np.iinfo(np.int64).max
        ):
            raise ConfigError("conversion sample_id must be an explicit integer")
        if not np.all(self.held_valid[group]) or np.any(self.held_sample[group] != sample_id):
            raise ConfigError("conversion group lacks the matching prior acquisition")
        return self._sampled_node_charge_c[group].copy()

    def held_effective_node_charge(self, conv, *, sample_id: int) -> float:
        """返回同笔实际组的sum(Q主+beta*Q子)[C]，不等同输入电压。"""
        group = _integer_array(conv, "conversion physical IDs", ndim=1)
        node = self.held_node_charge(group, sample_id=sample_id)
        beta = self.split_coefficients(group[None, :])["beta"]
        assert isinstance(beta, np.ndarray)
        return float(np.sum(node[:, 0] + beta[0] * node[:, 1]))

    def release(self, conv, *, sample_id: int) -> None:
        """消费资格后允许重新采集；保留已有电荷记录，不假定模拟复位。"""
        group = _integer_array(conv, "released physical IDs", ndim=1)
        self.held_node_charge(group, sample_id=sample_id)
        self.held_valid[group] = False
        self.n_conversions[group] += 1
