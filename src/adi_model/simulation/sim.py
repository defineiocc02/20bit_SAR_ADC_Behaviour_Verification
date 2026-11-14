"""唯一物理pipeline的结果合同；本模块不再提供unary或聚合运行入口。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import numpy as np

from adi_model.behavioral.sampler import SampleCapture
from adi_model.calibration.reconstruction import DigitalState
from adi_model.simulation.config import Config

if TYPE_CHECKING:
    from adi_model.behavioral.conversion import ConversionResult
    from adi_model.calibration.weight_calibration import DigitalObservation, FrozenCalibration
    from adi_model.contracts.fixed_point import FixedPointFormat
    from adi_model.control.dither import Fig19CommandTrace, Fig19DitherProgram
    from adi_model.simulation.slice_pool import PhysicalSlicePool


@dataclass
class SimResult:
    """显式控制下的raw结果；物理诊断与数字学习记录分开。

    电压[V]与电荷[C]字段均按名字标识节点；sampled_node_charge_c为(N,A,2)主/sub自由电荷，
    residue=−(Qfree_eff+Qdac)/Csignal加显式样本域噪声。out在未装载冻结权重时只是名义标量重构诊断，
    不是20bit性能输出。to_codes必须提供定点格式和已冻结/显式系数，只消费digital_observation。
    static_domain_reasons只说明训练模型资格；名义诊断另有available/reasons。
    制造/参考误差与clip不会因为训练资格失败就从未校正诊断中消失。
    """

    cfg: Config
    pool: PhysicalSlicePool
    state: DigitalState
    sample: SampleCapture
    sample_id: np.ndarray
    # --- 片池相关（三个字段容易混淆，这里写清语义）---
    # conv_slice_ids     : 本笔**转换**用哪 8 片（= plan.conv）。
    # acq_slice_ids      : 本笔**采样**用哪 8 片。两相交织下，本笔采的样本正是在
    #                      本笔被转换的，因此它 == conv_slice_ids（这一点此前容易被
    #                      误读成"采样与转换重叠是个 bug"，实际是交织的正确行为）。
    # next_acq_slice_ids : **下一笔**采样用哪 8 片（= plan.acq），与 conv 不相交 ——
    #                      两相交错的证据在这里，不在 acq_slice_ids。
    conv_slice_ids: np.ndarray
    acq_slice_ids: np.ndarray
    next_acq_slice_ids: np.ndarray
    held_sample_ids: np.ndarray
    plan_valid: np.ndarray
    coarse: np.ndarray
    sadc_over: np.ndarray
    k: np.ndarray
    sid: np.ndarray
    main_masks: np.ndarray
    sub_masks: np.ndarray
    sampling_main_connections: np.ndarray
    sampling_sub_connections: np.ndarray
    sampled_node_charge_c: np.ndarray
    sampled_effective_charge_c: np.ndarray
    residue_charge_c: np.ndarray
    signal_capacitance_f: np.ndarray  # (N,)实际Signal连接的∂Qsample/∂Vin[F]。
    normalization_capacitance_f: np.ndarray  # (N,)全Signal时Cnorm[F]，不是Cload或Cnoise。
    g_vec: np.ndarray  # (N,)Cnorm/Cf；有限A0/带宽响应前的电荷增益，不是所有节点的信号增益。
    vd0: np.ndarray
    vd_true: np.ndarray
    residue: np.ndarray
    vra: np.ndarray
    adc2_input_voltage: np.ndarray
    adc2_code: np.ndarray
    fine: np.ndarray
    ra_sat: np.ndarray
    adc2_over: np.ndarray
    out: np.ndarray
    err: np.ndarray
    x_ref: np.ndarray
    static_domain_reasons: tuple[str, ...]
    nominal_diagnostic_available: bool
    nominal_diagnostic_reasons: tuple[str, ...]
    binding_metadata: dict
    digital_observation: DigitalObservation | None
    dither_program: Fig19DitherProgram | None = None
    dither_trace: Fig19CommandTrace | None = None
    conversion_trace: ConversionResult | None = None
    calibration_applied: tuple[str, ...] = ()
    uncalibrated_out: np.ndarray | None = None
    calibration_report: dict | None = None

    def to_codes(self, *, format: FixedPointFormat, calibration: FrozenCalibration | None = None):
        """由真实数字观测和显式定点格式生成20bit整数流。

        Args:
            format: 必填FixedPointFormat，所有非披露位宽均由调用者提供。
            calibration: 显式FrozenCalibration；None只允许state已有冻结模型。

        Returns:
            CodeStream，模拟越界与最终整数夹码分别保留。

        Raises:
            ValueError: 缺观测/真实binding/冻结系数或非法格式。

        Side effects: 不学习、不读取C/G/heldV、不以名义weights回填。
        """
        from adi_model.calibration.weight_calibration import DigitalObservation
        from adi_model.contracts.fixed_point import FixedPointReconstructor

        decoder = FixedPointReconstructor.from_result(self, format=format, calibration=calibration)
        return decoder.reconstruct(DigitalObservation.from_result(self))

    @property
    def rdac_over(self) -> np.ndarray:
        """原始最终RDAC码是否越显式码域；入口通常在选择mask前已拒绝。"""
        book = self.pool.nominal_codebook
        if book is None:
            raise ValueError("result has no explicit nominal RDAC codebook")
        return (self.k < book.code_min) | (self.k > book.code_max)

    @property
    def analog_overflow(self) -> np.ndarray:
        """保留SADC、RA、ADC2与无效控制事务的逐raw联合标志。"""
        return self.sadc_over | self.ra_sat | self.adc2_over | self.rdac_over | ~self.plan_valid

    @property
    def effective_config(self) -> dict:
        """记录请求、实际binding和未消费字段，不作数字估计输入。"""
        inactive = {}
        for flag, names in (
            (not self.cfg.ra_enable_noise, ("ra_out_noise_rms", "ra_noise_distribution")),
            (
                not self.cfg.enable_sampling_noise,
                ("temperature_k", "chi", "sampling_noise_distribution"),
            ),
            (
                not self.cfg.dyn_input_settling,
                ("input_network", "dyn_r_source", "dyn_r_on", "dyn_ron_code_coeff"),
            ),
            (not self.cfg.dyn_ref_settling, ("dyn_c_decouple", "dyn_tau_ref")),
            (
                not self.cfg.dyn_crosstalk,
                ("dyn_c_xtalk_common", "dyn_c_xtalk_unit", "dyn_v_digital"),
            ),
        ):
            if flag:
                for name in names:
                    if getattr(self.cfg, name) is not None:
                        inactive[name] = "consumer disabled; supplied value was not applied"
        if (
            not self.cfg.enable_sampling_noise
            and not self.cfg.dyn_input_settling
            and self.cfg.c_sadc is not None
        ):
            inactive["c_sadc"] = "no sampling-noise or input-network consumer selected"
        if (
            not (
                self.cfg.enable_sampling_noise
                or self.cfg.ra_enable_noise
                or self.cfg.driver_noise_rms is not None
            )
            and self.cfg.seed is not None
        ):
            inactive["seed"] = "all random sources disabled"
        if not self.cfg.dyn_input_settling and self.pool.tau_rel is not None:
            inactive["pool.tau_rel"] = "continuous input-settling model disabled"
        return {
            "runner": "run_pipeline",
            "requested": asdict(self.cfg),
            "geometry": {
                "n_slices": self.pool.geometry.n_slices,
                "n_active": self.pool.geometry.n_active,
                "n_main": self.pool.geometry.n_main,
                "n_sub": self.pool.geometry.n_sub,
            },
            "execution_profile": self.cfg.execution_profile,
            "bindings": dict(self.binding_metadata),
            "source_dither_status": (
                "FIG19_STATIC_EQUIVALENT_ONLY; complete source capacitor/switch network unmodeled"
                if self.dither_program is not None
                else "DISABLED; no explicit lower/upper program"
            ),
            "static_charge_domain": self.digital_observation.static_charge_domain
            if self.digital_observation is not None
            else False,
            "static_domain_reasons": list(self.static_domain_reasons),
            "output_kind": "floating_frozen_weights"
            if self.calibration_applied
            else (
                "nominal_scalar_diagnostic"
                if self.nominal_diagnostic_available
                else "unavailable_without_coefficients"
            ),
            "nominal_diagnostic_available": self.nominal_diagnostic_available,
            "nominal_diagnostic_reasons": list(self.nominal_diagnostic_reasons),
            "calibration_applied": list(self.calibration_applied),
            "inactive_overrides": inactive,
            "raw_samples": len(self.adc2_code),
            "raw_invalid": int(np.count_nonzero(self.analog_overflow)),
            "physical_gain_min": float(np.min(self.g_vec)),
            "physical_gain_max": float(np.max(self.g_vec)),
            "physical_diagnostics_are_not_digital_features": True,
        }
