"""显式物理数据与数字binding驱动的唯一pipeline实现。

每笔用真实采集组保存主/sub自由电荷，随后同一笔转换组消费它；跨周期组与slot重排都按SlicePlan核对。
残差输入等效r=−(Qfree_eff+QDAC)/Cnorm；Cnorm是全Signal连接的归一化电容，
实际Signal连接系数另由采集mask给出。理想RA输出为(Cnorm/Cf)*(r+显式RA输入误差)。
浮置top的参考响应另乘−Ceff/Cnorm；有限A0的噪声增益为1+Ceff/Cf，不能由Cnorm/Cf代替。
制造值仅用于plant、资格检查和事后评分，不进入数字学习feature。
"""

from __future__ import annotations

import numpy as np

from .adc2 import ADC2
from .config import Config, ConfigError
from .dac_arch import NominalCodebook
from .dem import REF_HIGH, REF_LOW, SIGNAL, SamplingConnections, SplitSwitchCommand, _integer_array
from .dither import Fig19DitherProgram, complete_flash3_sar6_prefix_table
from .mapper import MappedCodes, Mapper
from .ra import ResidueAmplifier
from .reconstruction import DigitalState, initialize_state, reconstruct
from .sadc import build_first_stage_quantizer
from .sampler import capture, finite_record
from .sampling_charge import digital_connection_terms
from .sim import SimResult
from .slice_pool import PhysicalSlicePool
from .weight_calibration import CalibrationSpec, DigitalObservation, apply_frozen_calibration


def _reference_record(value, n: int, name: str):
    return None if value is None else finite_record(value, (n,), name)


def _reference_at(value, index: int):
    return None if value is None else float(value[index])


def _binding_name(value) -> str:
    if value is None:
        return "UNBOUND"
    return f"{getattr(value, '__module__', type(value).__module__)}.{getattr(value, '__qualname__', type(value).__qualname__)}"


def _same_book(left: NominalCodebook, right: NominalCodebook) -> bool:
    return (
        left.code_min == right.code_min
        and left.code_max == right.code_max
        and np.array_equal(left.voltage_v, right.voltage_v)
    )


def execute_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    pool: PhysicalSlicePool,
    mapper: Mapper,
    sample_ids,
    dem_states,
    sampled_top_v,
    sampled_subnode_v,
    reference_high_v,
    reference_low_v,
    nominal_reference_high_v,
    nominal_reference_low_v,
    static_charge_domain: bool,
    state: DigitalState | None = None,
    rng=None,
    ra_input_error_v=None,
    rdac_noise_cap_f=None,
    sampling_control=None,
    initial_reference_error_v=None,
    initial_ra_output_v=None,
    initial_adc2_input_v=None,
    dither_program: Fig19DitherProgram | None = None,
) -> SimResult:
    """执行一段显式priming的记录，不修改Config或制造电容。

    Args:
        cfg: 冻结schema的显式运行合同，不执行profile/架构重写。
        input_fn: 单孔径输入[V]，时间[s]；每记录时间原点为0。
        n_samples: 正整数raw数，所有无效/饱和事务均保留。
        pool: 显式chip、nominal_codebook与实际mask/acquisition/schedule binding。
        mapper: 512粗码到RDAC整数码的明确表；参考动态另需明确逐事件表。
        sample_ids: (N,)实际连续uint32身份，保留回绕，不从ID制造启动相位。
        dem_states: (N,)实际整数状态；DEM关闭时必须保持同一状态。
        sampled_top_v: 显式标量或(N,A)采样主节点V，列按本笔实际acquisition组顺序；
            首笔外部预采集使用conv[0]顺序。
        sampled_subnode_v: 同顺序的显式采样sub节点V，不默填零。
        reference_high_v: 实际全局高参考驱动基准V，标量或(N,)，未连接轨可None；
            动态轨误差另由Engine状态叠加，不将基准称为已包含droop的采集节点。
        reference_low_v: 实际全局低参考驱动基准V，标量或(N,)，不从v_fs推导。
        nominal_reference_high_v: 独立数字名义高轨V，标量或(N,)。
        nominal_reference_low_v: 独立数字名义低轨V，只有名义轨进入学习term。
        static_charge_domain: 请求静态学习资格；引擎还须检查物理/动态条件，
            True不是绕过资格检查的声明。
        state: 可选已有数字状态/冻结权重；None仅用来源名义g0作float诊断。
        rng: 可选显式噪声Generator；活跃噪声无此对象时使用明确cfg.seed。
        ra_input_error_v: 可选(N,)RA输入等效[V]，不注入数字学习项。
        rdac_noise_cap_f: 采样噪声启用时必填(N,)显式噪声等效电容[F]。
        sampling_control: 原样传给数字采集binding的明确控制数据，无物理truth。
        initial_reference_error_v: 活跃动态参考所需显式初始两轨误差V。
        initial_ra_output_v: 活跃动态RA所需显式初始输出V。
        initial_adc2_input_v: 活跃动态ADC2所需显式初始输入V，不默填0。
        dither_program: 同一raw的Fig19静态等效Q32控制。SADC采x-L、RDAC存x-T，
            各resolved命令减U；数字公共项为-T，不再额外纠正U。

    Returns:
        SimResult；未装载冻结权重时out只为名义标量诊断，整数输出另调用to_codes。

    Raises:
        ConfigError: 缺绑定、控制冲突、未支持电路域或缺活跃物理参数。

    Side effects: 重置并消费pool采集资格；消费启用噪声RNG，不学习系数。
    """
    if not isinstance(pool, PhysicalSlicePool) or not isinstance(mapper, Mapper):
        raise ConfigError("run_pipeline requires an explicit PhysicalSlicePool and Mapper")
    cfg.check_legal(geometry=pool.geometry)
    if bool(cfg.dither_enable) != (dither_program is not None):
        raise ConfigError("dither_enable requires an explicit Fig19 dither_program, and vice versa")
    if dither_program is not None and not isinstance(dither_program, Fig19DitherProgram):
        raise ConfigError("dither_program must be Fig19DitherProgram")
    if isinstance(n_samples, bool) or not isinstance(n_samples, int | np.integer) or n_samples < 1:
        raise ConfigError("n_samples must be a positive integer")
    n_samples = int(n_samples)
    if type(static_charge_domain) is not bool:
        raise ConfigError("static_charge_domain must be bool and is independently qualified")
    for name in ("nominal_codebook", "mask_binding", "acquisition_binding", "schedule_binding"):
        if getattr(pool, name, None) is None:
            raise ConfigError(f"physical pipeline requires explicitly bound pool.{name}")
    assert pool.nominal_codebook is not None
    if not _same_book(pool.nominal_codebook, mapper.nominal_codebook):
        raise ConfigError("pool and coarse Mapper nominal codebooks conflict")
    if len(mapper.rdac_code_by_coarse) != 512:
        raise ConfigError("source-target Mapper needs 512 explicitly supplied coarse rows")
    if dither_program is not None:
        if dither_program.words.shape != (n_samples,) or dither_program.spec.v_fs != cfg.v_fs:
            raise ConfigError("Fig19 program raw length/Vfs conflicts with the actual record")
        if (
            cfg.dem_enable
            or cfg.dyn_input_settling
            or cfg.dyn_ref_settling
            or cfg.dyn_crosstalk
            or cfg.conversion is not None
            or cfg.enable_sampling_noise
            or (cfg.ra_enable_noise and cfg.ra_out_noise_rms != 0)
            or cfg.driver_noise_rms not in (None, 0)
            or cfg.sampling_tau_mismatch not in (None, 0)
        ):
            raise ConfigError(
                "Fig19 static v1 requires explicit zero-noise, no dynamics/response approximation "
                "and no DEM combination; requested features are not silently disabled"
            )
        expected_book = -cfg.v_fs + np.arange(512) * dither_program.spec.coarse_step_v
        if (
            mapper.nominal_codebook.code_min != 0
            or mapper.nominal_codebook.code_max != 511
            or not np.array_equal(mapper.rdac_code_by_coarse, np.arange(512))
            or not np.allclose(
                mapper.nominal_codebook.voltage_v,
                expected_book,
                rtol=0,
                atol=32 * np.finfo(float).eps * cfg.v_fs,
            )
            or mapper.event_codes_by_coarse is None
            or not np.array_equal(mapper.event_codes_by_coarse, complete_flash3_sar6_prefix_table())
        ):
            raise ConfigError(
                "Fig19 v1 needs the explicit uniform512 codebook/identity decoder and complete "
                "Flash3/SAR6 seven-event table; no prefix or codebook fallback"
            )
    if cfg.dyn_input_settling:
        raise ConfigError(
            "continuous acquisition requires a complete SADC/RDAC phase and initial-node binding; "
            "this static connection runner does not supply it or shorten the full-Ts RDAC window"
        )
    if cfg.dyn_crosstalk:
        raise ConfigError(
            "crosstalk needs an explicit edge/coupling circuit binding; legacy code-distance proxy removed"
        )
    if cfg.input_network is not None and (
        cfg.input_network.pretrack_mode != "off"
        or cfg.input_network.auxiliary_cap_f not in (None, 0)
    ):
        raise ConfigError("pretracking/AUX requires a complete active acquisition-state binding")
    plan = pool.plan(n_samples)
    acquisition_ids = np.vstack((plan.conv[0:1], plan.acq[:-1]))
    if dither_program is not None:
        used = np.unique(acquisition_ids)
        if any(
            getattr(pool, name) is not None and np.any(getattr(pool, name)[used] != 0)
            for name in ("t_skew", "v_os")
        ):
            raise ConfigError("Fig19 static v1 has no active-slice offset/skew phase binding")
    sid = _integer_array(dem_states, "actual DEM states", ndim=1)
    if sid.shape != (n_samples,) or np.any(sid < 0):
        raise ConfigError("DEM states must explicitly identify every raw with nonnegative integers")
    if not cfg.dem_enable and np.any(sid != sid[0]):
        raise ConfigError("DEM disabled requires a held actual state, not a varying sequence")
    ids = _integer_array(sample_ids, "sample IDs", ndim=1)
    if ids.shape != (n_samples,):
        raise ConfigError("sample IDs must explicitly identify all raw records")
    if len(ids) > 1 and np.any((np.diff(ids) & 0xFFFF_FFFF) != 1):
        raise ConfigError(
            "this waveform producer requires continuous raw IDs; do not hide missing periods"
        )
    top = finite_record(sampled_top_v, (n_samples, cfg.n_active), "sampled top node [V]")
    subnode = finite_record(sampled_subnode_v, top.shape, "sampled sub node [V]")
    injected = (
        None
        if ra_input_error_v is None
        else finite_record(ra_input_error_v, (n_samples,), "RA input error [V]", scalar=False)
    )
    if dither_program is not None and (
        np.any(top != 0) or (injected is not None and np.any(injected != 0))
    ):
        raise ConfigError(
            "Fig19 v1 requires explicit zero top reset and no additional RA disturbance"
        )
    feedback = pool.chip.require_feedback()
    pool.chip.require_main_parasitic()
    coefficients = pool.split_coefficients(plan.conv)
    c_effective = coefficients["c_eff"]
    if not isinstance(c_effective, np.ndarray):
        raise ConfigError("actual main parasitic capacitance is required for sampled-node charge")
    random_active = (
        cfg.ra_enable_noise or cfg.enable_sampling_noise or cfg.driver_noise_rms is not None
    )
    if rng is not None and not isinstance(rng, np.random.Generator):
        raise ConfigError("rng must be a Generator")
    if rng is None and random_active:
        rng = np.random.default_rng(cfg.seed)
    sample = capture(
        cfg,
        input_fn,
        n_samples,
        sample_ids=ids,
        rng=rng,
        rdac_noise_cap_f=rdac_noise_cap_f,
        dither_program=dither_program,
    )
    sadc = build_first_stage_quantizer(cfg)
    backend = ADC2(cfg)
    ra = ResidueAmplifier(cfg)
    state = initialize_state(cfg) if state is None else state
    if not isinstance(state, DigitalState):
        raise ConfigError(
            "state must be DigitalState; physical truth is not a digital coefficient source"
        )
    coarse, sadc_over = sadc.convert_codes(sample.x_sadc)
    mapped = mapper.encode(coarse)
    dither_trace = None
    if dither_program is not None:
        assert mapped.event_codes is not None
        dither_trace = dither_program.commands(
            coarse,
            mapped.event_codes,
            code_min=mapper.nominal_codebook.code_min,
            code_max=mapper.nominal_codebook.code_max,
        )
        mapped = MappedCodes(dither_trace.rdac_code, dither_trace.rdac_event_codes)
    assert pool.acquisition_binding is not None
    sampled = pool.acquisition_binding(
        pool.geometry, ids.copy(), acquisition_ids.copy(), sampling_control
    )
    if not isinstance(sampled, SamplingConnections):
        raise ConfigError("acquisition binding must return actual SamplingConnections")
    sampled = sampled.validated(pool.geometry, n_samples)
    if dither_program is not None and (
        np.any(sampled.main != SIGNAL) or np.any(sampled.sub != SIGNAL)
    ):
        raise ConfigError(
            "Fig19 static v1 common-voltage equivalence requires actual allSIGNAL acquisition"
        )
    if cfg.dyn_ref_settling and (np.any(sampled.main != SIGNAL) or np.any(sampled.sub != SIGNAL)):
        raise ConfigError(
            "reference-connected acquisition requires a shared sampling/reference event binding; dynamic reference currently requires allSIGNAL acquisition"
        )
    command = pool.select(mapped.codes, sid, slice_ids=plan.conv)
    high_used = (
        np.any(sampled.main == REF_HIGH, axis=(1, 2))
        | np.any(sampled.sub == REF_HIGH, axis=(1, 2))
        | np.any(command.main_on, axis=(1, 2))
        | np.any(command.sub_on, axis=(1, 2))
    )
    low_used = (
        np.any(sampled.main == REF_LOW, axis=(1, 2))
        | np.any(sampled.sub == REF_LOW, axis=(1, 2))
        | np.any(~command.main_on, axis=(1, 2))
        | np.any(~command.sub_on, axis=(1, 2))
    )
    # A reference-event plant consumes its explicit two-rail driver contract;
    # static endpoint arithmetic only consumes rails actually connected here.
    high_needed = bool(np.any(high_used) or cfg.dyn_ref_settling)
    low_needed = bool(np.any(low_used) or cfg.dyn_ref_settling)
    high = (
        _reference_record(reference_high_v, n_samples, "actual high reference [V]")
        if high_needed
        else None
    )
    low = (
        _reference_record(reference_low_v, n_samples, "actual low reference [V]")
        if low_needed
        else None
    )
    nominal_high = (
        _reference_record(nominal_reference_high_v, n_samples, "nominal high reference [V]")
        if np.any(high_used)
        else None
    )
    nominal_low = (
        _reference_record(nominal_reference_low_v, n_samples, "nominal low reference [V]")
        if np.any(low_used)
        else None
    )
    used_physical_ids = np.unique(acquisition_ids)
    reasons = []
    if not static_charge_domain:
        reasons.append("caller did not request static training qualification")
    for name in ("t_skew", "v_os"):
        value = getattr(pool, name)
        if value is not None and np.any(value[used_physical_ids] != 0):
            reasons.append(f"active physical {name} is outside the static training equation")
    if injected is not None and np.any(injected != 0):
        reasons.append("external RA-input disturbance is not an observed static-training term")
    if np.any(top != 0):
        reasons.append("nonzero sampled top charge includes unmodeled parasitic/reset terms")
    for name, actual, nominal, used in (
        ("high", high, nominal_high, high_used),
        ("low", low, nominal_low, low_used),
    ):
        if (
            actual is not None
            and nominal is not None
            and not np.array_equal(actual[used], nominal[used])
        ):
            reasons.append(f"actual {name} rail differs from the declared digital reference")
    joint = None
    if cfg.dyn_ref_settling or cfg.conversion is not None:
        if cfg.conversion is None:
            raise ConfigError("active conversion requires explicit ConversionParameters")
        active_state_dynamics = cfg.dyn_ref_settling or cfg.conversion.dynamic
        if active_state_dynamics:
            reasons.append(
                "nonideal reference/RA/ADC2 response is outside static unit-weight training"
            )
        if active_state_dynamics and (
            (cfg.ra_enable_noise and cfg.ra_out_noise_rms != 0) or cfg.enable_sampling_noise
        ):
            raise ConfigError(
                "sample-domain white-noise composition with this nonideal conversion is not implemented; no noise transfer is inferred from signal response"
            )
        if cfg.dyn_ref_settling and mapped.event_codes is None:
            raise ConfigError(
                "reference recovery requires explicit per-coarse RDAC event-code records"
            )
        from .conversion import ConversionEngine

        joint = ConversionEngine(
            cfg,
            n_samples,
            initial_reference_error_v=initial_reference_error_v,
            initial_ra_output_v=initial_ra_output_v,
            initial_adc2_input_v=initial_adc2_input_v,
        )
    elif any(
        value is not None
        for value in (initial_reference_error_v, initial_ra_output_v, initial_adc2_input_v)
    ):
        raise ConfigError(
            "dynamic initial states were supplied without an active stateful consumer"
        )
    pool.reset_electrical_state()
    shape = (n_samples, cfg.n_active)
    recorded_main = np.empty((n_samples, cfg.n_active, pool.geometry.n_main), dtype=np.int64)
    recorded_sub = np.empty((n_samples, cfg.n_active, pool.geometry.n_sub), dtype=np.int64)
    node_charge = np.empty((*shape, 2))
    held = np.empty(shape, dtype=np.int64)
    q_free = np.empty(n_samples)
    q_residue = np.empty(n_samples)
    c_input = np.empty(n_samples)
    c_normalize = np.empty(n_samples)
    gains = np.empty(n_samples)
    vd_true = np.empty(n_samples)
    residue = np.empty(n_samples)
    vra = np.empty(n_samples)
    adc2_input = np.empty(n_samples)
    ra_sat: np.ndarray = np.empty(n_samples, dtype=bool)
    for index, converting in enumerate(plan.conv):
        group = acquisition_ids[index]
        values = np.full(cfg.n_active, sample.rdac_input_v[index])
        if pool.t_skew is not None and np.any(pool.t_skew[group] != 0):
            values = (
                finite_record(
                    input_fn(sample.aperture_s[index] + pool.t_skew[group]),
                    (cfg.n_active,),
                    "per-slice skewed input [V]",
                )
                + sample.driver_noise_v[index]
            )
        if pool.v_os is not None:
            values += pool.v_os[group]
        connection = SamplingConnections(
            sampled.main[index : index + 1], sampled.sub[index : index + 1]
        )
        packet = pool.acquire(
            group,
            values,
            sample_id=int(ids[index]),
            sampled_top_v=top[index],
            sampled_subnode_v=subnode[index],
            connections=connection,
            reference_high_v=_reference_at(high, index),
            reference_low_v=_reference_at(low, index),
        )
        order = np.array([int(np.flatnonzero(group == physical)[0]) for physical in converting])
        recorded_main[index] = sampled.main[index, order]
        recorded_sub[index] = sampled.sub[index, order]
        node_charge[index] = pool.held_node_charge(converting, sample_id=int(ids[index]))
        held[index] = pool.held_sample[converting]
        q_free[index] = pool.held_effective_node_charge(converting, sample_id=int(ids[index]))
        c_input[index] = packet.signal_capacitance_f[0]
        c_normalize[index] = packet.normalization_capacitance_f[0]
        gains[index] = ra.gain_vector(c_normalize[index : index + 1], feedback)[0]
        conversion_mask = SplitSwitchCommand(
            command.main_on[index : index + 1], command.sub_on[index : index + 1]
        )
        drive = pool.charge_from_masks(
            converting[None, :],
            conversion_mask,
            reference_high_v=_reference_at(high, index),
            reference_low_v=_reference_at(low, index),
        )
        vd_true[index] = drive.input_equivalent_v[0]
        q_residue[index] = (
            -q_free[index] - drive.charge_c[0] + c_normalize[index] * sample.rdac_noise_v[index]
        )
        residue[index] = q_residue[index] / c_normalize[index]
        if joint is None:
            error = None if injected is None else injected[index : index + 1]
            output, saturated = ra.evaluate(
                residue[index : index + 1], rng, gains[index : index + 1], input_error_v=error
            )
            vra[index], adc2_input[index], ra_sat[index] = output[0], output[0], saturated[0]
        else:
            assert cfg.conversion is not None
            program = None
            if cfg.dyn_ref_settling:
                from .reference_charge import physical_reference_program

                assert mapped.event_codes is not None and cfg.conversion is not None
                event_codes = mapped.event_codes[index]
                events = pool.select(
                    event_codes,
                    np.full(len(event_codes), sid[index], dtype=np.int64),
                    slice_ids=np.broadcast_to(converting, (len(event_codes), cfg.n_active)),
                )
                if not np.array_equal(
                    events.main_on[-1], conversion_mask.main_on[0]
                ) or not np.array_equal(events.sub_on[-1], conversion_mask.sub_on[0]):
                    raise ConfigError(
                        "reference event binding does not end at the actual final conversion mask"
                    )
                program = physical_reference_program(
                    slice_ids=converting,
                    main_caps_f=pool.chip.C_main[converting],
                    sub_caps_f=pool.chip.C_sub[converting],
                    bridge_caps_f=pool.chip.C_bridge[converting],
                    main_parasitic_f=pool.chip.require_main_parasitic()[converting],
                    sub_parasitic_f=pool.chip.c_p_sub[converting],
                    main_on=events.main_on,
                    sub_on=events.sub_on,
                    times_s=cfg.conversion.rdac_event_times_s,
                    sampled_main_bottom_v=packet.main_bottom_v[0, order],
                    sampled_sub_bottom_v=packet.sub_bottom_v[0, order],
                    sampled_top_v=top[index, order],
                    sampled_subnode_v=subnode[index, order],
                    ref_high_v=_reference_at(high, index),
                    ref_low_v=_reference_at(low, index),
                )
            reference_correction = joint.step(
                index,
                program=program,
                residue_v=residue[index] + (0.0 if injected is None else injected[index]),
                gain=gains[index],
                reference_to_residue_scale=(-c_effective[index] / c_normalize[index])
                if cfg.dyn_ref_settling
                else None,
                sample_time_s=float(sample.aperture_s[index]),
                feedback_noise_gain=(1.0 + c_effective[index] / feedback)
                if cfg.conversion.ra_dc_gain != "ideal"
                else None,
            )
            residue[index] -= reference_correction
            q_residue[index] = residue[index] * c_normalize[index]
            vd_true[index] += reference_correction
            vra[index] = joint.result.ra_v[index]
            adc2_input[index] = joint.result.adc2_v[index]
            ra_sat[index] = joint.result.ra_sat[index]
            if not cfg.dyn_ref_settling and not cfg.conversion.dynamic:
                error = None if injected is None else injected[index : index + 1]
                point_output, point_sat = ra.evaluate(
                    residue[index : index + 1], rng, gains[index : index + 1], input_error_v=error
                )
                vra[index] = point_output[0]
                adc2_input[index] = point_output[0]
                ra_sat[index] |= point_sat[0]
        pool.release(converting, sample_id=int(ids[index]))
    adc2_code, adc2_over = backend.quantize_codes(adc2_input)
    fine = backend.decode_codes(adc2_code)
    vd0 = mapper.nominal_codebook.evaluate(mapped.codes)
    if np.any(ra_sat) or np.any(adc2_over):
        reasons.append("RA/ADC2 clipping violates the linear training equation")
    actual_sampling = SamplingConnections(recorded_main, recorded_sub)
    signal_mask, term = digital_connection_terms(
        pool.geometry,
        actual_sampling,
        command,
        nominal_reference_high_v=None
        if nominal_high is None
        else nominal_high[:, None] * np.ones((1, cfg.n_active)),
        nominal_reference_low_v=None
        if nominal_low is None
        else nominal_low[:, None] * np.ones((1, cfg.n_active)),
    )
    if dither_program is not None:
        # 同一显式数字program定义已知采样公共项；不读取实际电容/保持电压。
        term = term - dither_program.total_v[:, None, None]
    diagnostic_reasons = []
    if cfg.dyn_ref_settling or (
        cfg.conversion is not None
        and (
            cfg.conversion.ra_response_model == "single_pole"
            or cfg.conversion.adc2_response_model in ("single_pole", "wide_narrow")
        )
    ):
        diagnostic_reasons.append(
            "stateful reference/RA/ADC2 response is outside this scalar diagnostic subset"
        )
    if np.any(top != 0):
        diagnostic_reasons.append(
            "nonzero sampled top terms have no explicit nominal scalar correction"
        )
    if not np.all(signal_mask):
        diagnostic_reasons.append(
            "sampling signal_mask is not allSIGNAL; no scalar alpha is inferred"
        )
    try:
        diagnostic_gain = float(finite_record(state.estimated_gain, (), "nominal scalar gain [1]"))
        if diagnostic_gain <= 0:
            raise ConfigError("nominal scalar gain must be positive")
    except ConfigError:
        diagnostic_reasons.append("a finite positive nominal scalar gain is required")
        diagnostic_gain = None
    # Actual C/G/reference errors and clipping remain visible in the nominal
    # output. They are never used to normalize it or select error-free records.
    diagnostic_available = not diagnostic_reasons
    if diagnostic_available:
        assert diagnostic_gain is not None
        correction = np.zeros(n_samples) if dither_program is None else -dither_program.total_v
        out = reconstruct(vd0, fine, diagnostic_gain, correction, 1.0)
    else:
        out = np.full(n_samples, np.nan)
    bad = sadc_over | ra_sat | adc2_over | ~plan.valid
    observation = DigitalObservation(
        CalibrationSpec.from_config(cfg, geometry=pool.geometry),
        plan.conv,
        adc2_code,
        command.main_on,
        command.sub_on,
        signal_mask,
        term,
        bad,
        ids,
        static_charge_domain and not reasons,
    )
    dither_metadata = None
    if dither_program is not None:
        assert dither_trace is not None
        dither_metadata = {
            **dither_program.spec.to_dict(),
            "prefix_events": int(dither_trace.rdac_event_codes.shape[1]),
            "event_flag_scope": "resolved RDAC integer range; no per-event analog settling claim",
            "stored_rdac_input": "sample.rdac_input_v = input_v - T; actual pool.acquire",
            "sadc_input": "sample.x_sadc = input_v - L in this zero-noise v1",
            "digital_known_common_q": "-total_q; no second upper correction",
        }
    result = SimResult(
        cfg=cfg,
        pool=pool,
        state=state,
        sample=sample,
        sample_id=ids.copy(),
        conv_slice_ids=plan.conv.copy(),
        acq_slice_ids=acquisition_ids.copy(),
        next_acq_slice_ids=plan.acq.copy(),
        held_sample_ids=held,
        plan_valid=plan.valid.copy(),
        coarse=coarse,
        sadc_over=sadc_over,
        k=mapped.codes,
        sid=sid,
        main_masks=command.main_on,
        sub_masks=command.sub_on,
        sampling_main_connections=recorded_main,
        sampling_sub_connections=recorded_sub,
        sampled_node_charge_c=node_charge,
        sampled_effective_charge_c=q_free,
        residue_charge_c=q_residue,
        signal_capacitance_f=c_input,
        normalization_capacitance_f=c_normalize,
        g_vec=gains,
        vd0=vd0,
        vd_true=vd_true,
        residue=residue,
        vra=vra,
        adc2_input_voltage=adc2_input,
        adc2_code=adc2_code,
        fine=fine,
        ra_sat=ra_sat,
        adc2_over=adc2_over,
        out=out,
        err=out - sample.input_clean_v,
        x_ref=sample.input_clean_v,
        static_domain_reasons=tuple(dict.fromkeys(reasons)),
        nominal_diagnostic_available=diagnostic_available,
        nominal_diagnostic_reasons=tuple(diagnostic_reasons),
        binding_metadata={
            "mask": _binding_name(pool.mask_binding),
            "acquisition": _binding_name(pool.acquisition_binding),
            "schedule": _binding_name(pool.schedule_binding),
            "schedule_strategy": plan.strategy,
            "source_alignment": "EXPLICIT_USER_BINDINGS; not independently proved source mapping",
            "acquisition_window": "full raw period; point-domain endpoint for this runner",
            "continuous_acquisition_status": "NOT_IMPLEMENTED without complete phase/initial-node binding",
            "event_code_table_applied": bool(cfg.dyn_ref_settling),
            "inactive_runtime_inputs": (
                (
                    ["initial_reference_error_v"]
                    if initial_reference_error_v is not None and not cfg.dyn_ref_settling
                    else []
                )
                + (["reference_high_v"] if reference_high_v is not None and not high_needed else [])
                + (["reference_low_v"] if reference_low_v is not None and not low_needed else [])
                + (
                    ["nominal_reference_high_v"]
                    if nominal_reference_high_v is not None and not np.any(high_used)
                    else []
                )
                + (
                    ["nominal_reference_low_v"]
                    if nominal_reference_low_v is not None and not np.any(low_used)
                    else []
                )
            ),
            "unused_slice_parameter_ids": {
                name: np.setdiff1d(
                    np.flatnonzero(getattr(pool, name) != 0), used_physical_ids
                ).tolist()
                for name in ("t_skew", "v_os")
                if getattr(pool, name) is not None
            },
            "RA_error_node": "RA input-equivalent V before actual gain/clipping/ADC2",
            "dither": dither_metadata,
        },
        digital_observation=observation,
        dither_program=dither_program,
        dither_trace=dither_trace,
        conversion_trace=None if joint is None else joint.result,
    )
    if state.weight_calibration is not None:
        apply_frozen_calibration(result, state.weight_calibration)
    return result
