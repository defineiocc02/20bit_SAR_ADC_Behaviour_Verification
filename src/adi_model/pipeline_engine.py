"""Physical split-pipeline execution with explicit sample and capacitor ownership.

The vectorized electrical limit and stateful acquisition path share charge
equations. Clock scheduling, physical arrays and nominal digital reconstruction
remain separate. Continuous-network/RA-reference dynamics build on this layer.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .adc2 import ADC2
from .config import Config, ConfigError
from .conversion import ConversionEngine
from .dac_arch import SplitDAC
from .dynamics import apply_dynamics, crosstalk_error
from .input_network import OffsetWaveform, PassiveTrackingNetwork, track_interval
from .ktc import KTCBranch
from .mapper import dem_state_sequence, dither_transfer_code, make_dither_state
from .pretracking import InputAssistTrace, QuantizedPretracker
from .ra import ResidueAmplifier
from .reconstruction import DigitalState, initialize_state, reconstruct
from .sadc import build_first_stage_quantizer, units_per_first_stage_step
from .sampler import capture, input_derivative
from .sampling_charge import sampling_dither_injection
from .scheduler import Scheduler, make_scheduler
from .sim import SimResult
from .sim_split import xtalk_profile
from .slice_pool import PhysicalSlicePool


def execute_split(
    cfg: Config,
    input_fn,
    n_samples: int,
    *,
    chip=None,
    state: DigitalState | None = None,
    rng: np.random.Generator | None = None,
    scheduler: Scheduler | None = None,
    pool: PhysicalSlicePool | None = None,
    derivative_fn=input_derivative,
    runner: str = "run_pipeline",
) -> SimResult:
    """Execute one independently primed physical split-pipeline record.

    Args:
        cfg: Configuration. A split entry resolves the legacy default topology
            label to split; all physical quantities use the resolved config.
        input_fn: Input voltage callable, with times in seconds.
        n_samples: Positive record length.
        chip: Optional aggregate realization to distribute across slices.
        state: Frozen digital coefficients; physical truth is never copied here.
        rng: Sampling/noise generator; fabrication and scheduling are independent.
        scheduler: Explicit compatible scheduler, or one selected by config.
        pool: Persistent fabricated pool; electrical state is reset for this record.
        derivative_fn: Aperture-slope provider, separately testable.
        runner: Public entry-point name for metadata.

    Returns:
        Result with actual slice, held-sample and stored-charge traces.

    Raises:
        ConfigError: Invalid length or incompatible physical topology.
    """
    cfg.check_legal()
    cfg = replace(cfg, dac_arch="split")
    cfg.check_legal()
    if n_samples < 1:
        raise ConfigError("n_samples must be positive")
    if pool is None:
        pool = PhysicalSlicePool(cfg, np.random.default_rng(cfg.seed + 8101), chip=chip)
    if not pool.is_split or (pool.cfg.dac_n_main, pool.cfg.dac_n_sub, pool.cfg.n_slices) != (
        cfg.dac_n_main,
        cfg.dac_n_sub,
        cfg.n_slices,
    ):
        raise ConfigError("pool and runner require identical physical topology")
    chip = pool.template_chip
    assert chip is not None
    pool.v_top[:] = 0
    pool.held_valid[:] = False
    pool.held_sample[:] = -1
    pool.n_conversions[:] = 0
    pool.v_top_q[:] = 0
    state = initialize_state(cfg) if state is None else state
    sample_rng = np.random.default_rng(cfg.seed) if rng is None else rng
    schedule_rng = np.random.default_rng(cfg.seed + 8201)
    sched = make_scheduler(cfg, schedule_rng, scheduler)
    conv, acq = sched.reserve_dual(n_samples)
    if n_samples > 1 and not np.array_equal(np.sort(conv[1:]), np.sort(acq[:-1])):
        raise ConfigError("scheduler violates cross-cycle sample ownership")
    if np.any(
        np.sort(np.concatenate((conv, acq), axis=1))[:, 1:]
        == np.sort(np.concatenate((conv, acq), axis=1))[:, :-1]
    ):
        raise ConfigError("conversion and acquisition slices collide")
    bank = np.arange(n_samples, dtype=np.int64) % 2
    sid = dem_state_sequence(n_samples, cfg, bank)

    coeff = pool.split_coefficients(conv, cfg=cfg)
    c_sig, c_load, c_noise = coeff["c_signal"], coeff["c_load"], coeff["c_noise"]
    ra = ResidueAmplifier(cfg)
    gain = ra.gain_vector(c_sig, chip.C_feedback_true)
    dac = SplitDAC(cfg, chip)
    step = cfg.nominal_rdac_step
    units = units_per_first_stage_step(cfg, dac)
    sadc = build_first_stage_quantizer(cfg, dac)
    sample = capture(
        cfg,
        input_fn,
        n_samples,
        sample_rng,
        c_active=c_noise,
        dither_rng=np.random.default_rng(cfg.seed + 8202),
    )
    if cfg.dither_mode == "sampling":
        sampling_dither_injection(cfg, chip, sample, step, chip.c_sig_true())
        assert sample.dither_bank_code is not None
        sampled, injection = pool.split_sampling_charge(
            conv, sample.x1, sample.dither_bank_code, cfg=cfg
        )
        sample.x_rdac = sampled + sample.n_R
        sample.dither = injection
        sample.signal_alpha = coeff["alpha"]

    d_code = dither_transfer_code(
        cfg,
        sample.dither,
        step_rdac=step,
        step_coarse=units * step,
        dither_code_sampling=sample.dither_code,
    )
    e_input = np.zeros(n_samples)
    e_sadc = np.zeros(n_samples)
    held = np.broadcast_to(np.arange(n_samples)[:, None], conv.shape).copy()
    joint = (
        ConversionEngine(cfg, pool, int(units), n_samples)
        if cfg.dyn_ref_settling or cfg.conversion.dynamic
        else None
    )
    xtalk = xtalk_profile(cfg, cfg.dac_n_units, np.random.default_rng(cfg.seed + 8204))
    active_dynamics = (
        cfg.dyn_input_settling
        or cfg.slice_timing_skew_s > 0
        or cfg.slice_offset_sigma_v > 0
        or joint is not None
    )
    coarse = sadc.convert(sample.x_sadc)
    assist_parameters = cfg.input_network
    assist = (
        InputAssistTrace.allocate(n_samples, cfg.n_active)
        if cfg.dyn_input_settling
        and (assist_parameters.pretrack_mode != "residue" or assist_parameters.auxiliary_cap_f > 0)
        else None
    )
    tracker = (
        QuantizedPretracker(
            cfg.n_slices,
            cfg.b1,
            -cfg.v_fs,
            units * step,
            cfg.dither_alpha,
            assist_parameters.pretrack_weights,
        )
        if assist is not None and assist_parameters.pretrack_mode != "residue"
        else None
    )
    auxiliary_history = np.zeros(cfg.n_slices)
    acquisition_voltage = None
    source_charge = None
    bus_voltage = None
    if active_dynamics:
        slope = (
            derivative_fn(input_fn, sample.t1)
            if cfg.slice_timing_skew_s > 0
            else np.zeros(n_samples)
        )
        tacq = cfg.dyn_t_sample_frac / cfg.fs
        acquisition_voltage = np.empty((n_samples, cfg.n_active))
        source_charge = np.zeros(n_samples)
        bus_voltage = np.zeros(n_samples)
        bus = 0.0
        has_bus = cfg.input_network.filter_cap_f > 0 and cfg.dyn_r_source > 0
        idle_network = (
            PassiveTrackingNetwork([cfg.input_network.filter_cap_f], [cfg.dyn_r_source], 0)
            if has_bus
            else None
        )
        for n, ids in enumerate(conv):
            # This acquisition belongs to the group converting sample n; it
            # spans the preceding clock interval, including the explicit prime.
            source_fn = (
                OffsetWaveform(input_fn, sample.dither[n])
                if cfg.dither_mode == "analog"
                else input_fn
            )
            source_endpoint = float(source_fn(sample.t1[n]))
            perturbation = pool.v_os[ids] + pool.t_skew[ids] * slope[n]
            if cfg.dyn_input_settling:
                t1, t0 = sample.t1[n], sample.t1[n] - tacq
                if tracker is not None:
                    assert assist is not None
                    begin = t0 - assist_parameters.pretrack_time_s
                    prediction = tracker.predict(ids, begin, assist_parameters.pretrack_mode)
                    assist.pretrack_start_s[n] = begin
                    assist.pretrack_target_v[n] = prediction.voltage_v
                    assist.pretrack_source_ids[n] = prediction.source_ids
                    assist.pretrack_available_s[n] = prediction.ready_times_s
                    tau_pre = (
                        assist_parameters.pretrack_source_ohm + cfg.dyn_r_on * pool.tau_rel[ids]
                    ) * coeff["load_slice"][n]
                    after_pre = prediction.voltage_v + (
                        pool.v_top[ids] - prediction.voltage_v
                    ) * np.exp(-assist_parameters.pretrack_time_s / tau_pre)
                    q_pre = float(np.dot(coeff["load_slice"][n], after_pre - pool.v_top[ids]))
                    pool.v_top[ids] = after_pre
                    q_target = float(np.mean(prediction.voltage_v))
                    q_after = q_target + (pool.v_top_q[n % 2] - q_target) * np.exp(
                        -assist_parameters.pretrack_time_s
                        / ((assist_parameters.pretrack_source_ohm + cfg.dyn_r_on) * cfg.c_sadc)
                    )
                    assist.pretrack_source_charge_c[n] = q_pre + cfg.c_sadc * (
                        q_after - pool.v_top_q[n % 2]
                    )
                    pool.v_top_q[n % 2] = q_after
                if idle_network is not None and n > 0 and cfg.dyn_t_sample_frac < 1:
                    bus = float(idle_network.advance([bus], sample.t1[n - 1], t0, input_fn)[0])
                previous = np.r_[pool.v_top[ids], pool.v_top_q[n % 2]]
                caps = np.r_[coeff["load_slice"][n], cfg.c_sadc]
                switches = cfg.dyn_r_on * np.r_[pool.tau_rel[ids], 1.0]
                auxiliary_cap = assist_parameters.auxiliary_cap_f
                auxiliary_on_bus = auxiliary_cap > 0 and not assist_parameters.auxiliary_bypass
                if auxiliary_cap > 0:
                    assert assist is not None
                    # A clock-driven reset happens while the parasitic branch
                    # is disconnected from both signal sources, before tracking.
                    assist.auxiliary_reset_charge_c[n] = -auxiliary_cap * np.mean(
                        auxiliary_history[ids]
                    )
                if auxiliary_on_bus:
                    caps = np.r_[caps, auxiliary_cap]
                    switches = np.r_[switches, assist_parameters.auxiliary_resistance_ohm]
                    previous = np.r_[previous, 0.0]
                if has_bus:
                    previous = np.r_[previous, bus]
                voltages, source_charge[n] = track_interval(
                    caps,
                    switches,
                    cfg.dyn_r_source,
                    previous,
                    t0,
                    t1,
                    source_fn,
                    parameters=cfg.input_network,
                    ron_coefficient=cfg.dyn_ron_code_coeff,
                    voltage_scale=cfg.v_fs,
                )
                values = voltages[: cfg.n_active] + perturbation
                if auxiliary_cap > 0:
                    assert assist is not None
                    if auxiliary_on_bus:
                        auxiliary_v = voltages[cfg.n_active + 1]
                    else:
                        auxiliary_state, _ = track_interval(
                            [auxiliary_cap],
                            [assist_parameters.auxiliary_resistance_ohm],
                            0,
                            [0.0],
                            t0,
                            t1,
                            source_fn,
                            parameters=replace(assist_parameters, filter_cap_f=0),
                        )
                        auxiliary_v = auxiliary_state[0]
                        assist.auxiliary_source_charge_c[n] = auxiliary_cap * auxiliary_v
                    assist.auxiliary_voltage_v[n] = auxiliary_v
                    assist.auxiliary_branch_charge_c[n] = auxiliary_cap * auxiliary_v
                    auxiliary_history[ids] = auxiliary_v
                # Nominal SADC attenuation matches the sampling-mask signal
                # scale. Its continuous tracking error passes through it too.
                e_sadc[n] = cfg.dither_alpha * (voltages[cfg.n_active] - source_endpoint)
                pool.v_top_q[n % 2] = voltages[cfg.n_active]
                coarse[n] = sadc.convert(np.array([sample.x_sadc[n] + e_sadc[n]]))[0]
                if has_bus:
                    bus = float(voltages[-1])
                elif cfg.dyn_r_source > 0:
                    ron = switches.copy()
                    ron *= 1 + cfg.dyn_ron_code_coeff * (source_endpoint / cfg.v_fs) ** 2
                    bus = float(
                        (source_endpoint / cfg.dyn_r_source + np.sum(voltages / ron))
                        / (1 / cfg.dyn_r_source + np.sum(1 / ron))
                    )
                else:
                    bus = source_endpoint
                bus_voltage[n] = bus
            else:
                values = source_endpoint + perturbation
            acquisition_voltage[n] = values
            pool.v_top[ids] = values
            pool.held_valid[ids] = True
            pool.held_sample[ids] = n
            if not np.all(pool.held_sample[ids] == n):
                raise RuntimeError("conversion does not own its sampled charge")
            e_input[n] = np.dot(coeff["signal_slice"][n], values - source_endpoint) / c_sig[n]
            command = coarse[n] * units + d_code[n]
            if tracker is not None:
                tracker.record(
                    n,
                    sample.t1[n] + cfg.conversion.quantizer_time_s,
                    ids,
                    coarse[n],
                    sample.dither[n] if cfg.dither_mode in ("analog", "quantizer") else 0.0,
                )
            vd = pool.split_dac_voltage(
                conv[n : n + 1], np.array([command]), sid[n : n + 1], cfg=cfg
            )[0]
            vd += crosstalk_error(
                cfg,
                np.array([command]),
                sid[n : n + 1],
                dac.levels,
                cfg.dac_n_units,
                xtalk,
                dac.full_order,
                c_sig[n],
            )[0]
            residue_n = sample.x_rdac[n] + e_input[n] - vd
            if joint is not None:
                reference_error = joint.step(
                    n,
                    ids,
                    coarse[n],
                    command,
                    sid[n],
                    values,
                    sample.dither_bank_code[n] if sample.dither_bank_code is not None else 0.0,
                    residue_n,
                    gain[n],
                )
                residue_n -= reference_error
            pool.v_top[ids] = residue_n
            pool.release(ids)

    k = coarse * units + d_code
    vd0 = dac.evaluate_nominal(k)
    vd_true = pool.split_dac_voltage(conv, k, sid, cfg=cfg)
    dyn = apply_dynamics(
        replace(cfg, dyn_input_settling=False, dyn_ref_settling=False),
        x=sample.x_rdac,
        v_prev=np.zeros(n_samples),
        v_nominal=vd0,
        code=k,
        sid=sid,
        c_active=c_load,
        n_levels=dac.levels,
        n_units=cfg.dac_n_units,
        xtalk_profile=xtalk,
        perm_fn=dac.full_order,
        c_xtalk_out=c_sig,
        c_load_ref=float(c_load.mean()),
    )
    vd_true += dyn.e_dac
    if joint is not None:
        vd_true += joint.result.dac_reference_error_v
    stored = sample.x_rdac + e_input
    residue = stored - vd_true
    if joint is None:
        vra, ra_sat = ra.evaluate(residue, sample_rng, g=gain)
        adc2_input = vra
    else:
        # Existing noise is output-equivalent at the aperture; do not filter it
        # by signal bandwidth and also apply a phenomenological noise reduction.
        noise, noise_sat = ra.evaluate(np.zeros(n_samples), sample_rng, g=gain)
        vra = joint.result.ra_v + noise
        ra_sat = joint.result.ra_sat | noise_sat | (np.abs(vra) >= cfg.ra_v_clip)
        vra = np.clip(vra, -cfg.ra_v_clip, cfg.ra_v_clip)
        adc2_input = (
            vra if cfg.conversion.adc2_wide_bandwidth_hz is None else joint.result.adc2_v + noise
        )
    ktc = KTCBranch(cfg)
    alpha_physical = coeff["alpha"] if cfg.dither_mode == "sampling" else 1.0
    vnc, ktc_sat = ktc.observe(sample.n_R, alpha_physical * sample.dx, sample_rng)
    backend = ADC2(cfg)
    adc2_code, adc2_over = backend.quantize_codes(adc2_input)
    fine = backend.decode_codes(adc2_code) - state.kappa * vnc
    dither = make_dither_state(cfg, sample.dither)
    if cfg.dither_mode == "sampling":
        dither.digital_correction = sample.dither_code * step
    elif cfg.dither_mode == "quantizer":
        dither.digital_correction = np.asarray(sample.rdac_dither)
    out = reconstruct(vd0, fine, state.estimated_gain, dither.digital_correction, cfg.dither_alpha)
    x_ref = sample.x1 + (sample.dx if cfg.ktc_enable else 0)
    # Ideal electrical limit is vectorized, but the final physical state is
    # still the actual last conversion of each slice, not an unrelated template.
    if not active_dynamics:
        for physical_id in range(cfg.n_slices):
            uses = np.flatnonzero(np.any(conv == physical_id, axis=1))
            if uses.size:
                pool.v_top[physical_id] = residue[uses[-1]]
                pool.held_sample[physical_id] = uses[-1]
                pool.n_conversions[physical_id] = uses.size
    return SimResult(
        runner=runner,
        out=out,
        err=out - x_ref,
        x_ref=x_ref,
        sample=sample,
        coarse=coarse,
        k=k,
        vd0=vd0,
        vd_true=vd_true,
        e_dac=vd_true - vd0,
        residue=residue,
        vra=vra,
        vnc=vnc,
        fine=fine,
        ra_sat=ra_sat,
        adc2_over=adc2_over,
        ktc_sat=ktc_sat,
        bank=bank,
        sid=sid,
        cfg=cfg,
        chip=chip,
        state=state,
        err_to_x1=out - sample.x1,
        err_to_x2=out - sample.x2,
        err_vs_clean=out - np.asarray(sample.x1_clean),
        g_vec=gain,
        c_active=c_noise,
        pool=pool,
        conv_slice_ids=conv,
        acq_slice_ids=acq,
        held_sample=held,
        sample_id=np.arange(n_samples),
        stored_charge=stored * c_sig,
        acquisition_error=e_input,
        sadc_acquisition_error=e_sadc,
        acquisition_start=sample.t1 - cfg.dyn_t_sample_frac / cfg.fs,
        acquisition_voltage=acquisition_voltage,
        input_source_charge_c=source_charge,
        input_bus_voltage=bus_voltage,
        conversion_trace=joint.result if joint is not None else None,
        adc2_input_voltage=adc2_input,
        adc2_code=adc2_code,
        input_assist_trace=assist,
    )
