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
from .dac_arch import SplitDAC
from .dynamics import apply_dynamics
from .ktc import KTCBranch
from .mapper import dem_state_sequence, dither_transfer_code, make_dither_state
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

    coeff = pool.split_coefficients(conv)
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
        sampled, injection = pool.split_sampling_charge(conv, sample.x1, sample.dither_bank_code)
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
    active_dynamics = (
        cfg.dyn_input_settling or cfg.slice_timing_skew_s > 0 or cfg.slice_offset_sigma_v > 0
    )
    coarse = sadc.convert(sample.x_sadc)
    if active_dynamics:
        slope = (
            derivative_fn(input_fn, sample.t1)
            if cfg.slice_timing_skew_s > 0
            else np.zeros(n_samples)
        )
        tacq = cfg.dyn_t_sample_frac / cfg.fs
        for n, ids in enumerate(conv):
            # This acquisition belongs to the group converting sample n; it
            # spans the preceding clock interval, including the explicit prime.
            target = sample.x1[n] + pool.v_os[ids] + pool.t_skew[ids] * slope[n]
            if cfg.dyn_input_settling:
                tau = (
                    cfg.dyn_r_source * c_load[n] + cfg.dyn_r_on * coeff["load_slice"][n]
                ) * pool.tau_rel[ids]
                tau *= 1 + cfg.dyn_ron_code_coeff * (sample.x1[n] / cfg.v_fs) ** 2
                if np.any(tau <= 0):
                    raise ConfigError("physical sampling time constants must be positive")
                values = target - np.exp(-tacq / tau) * (target - pool.v_top[ids])
            else:
                values = target
            pool.v_top[ids] = values
            pool.held_valid[ids] = True
            pool.held_sample[ids] = n
            if not np.all(pool.held_sample[ids] == n):
                raise RuntimeError("conversion does not own its sampled charge")
            e_input[n] = np.dot(coeff["signal_slice"][n], values - sample.x1[n]) / c_sig[n]
            # Quantizer aperture is distinct. Input dynamic matching is refined
            # by the continuous-network model; no other group's held value is used.
            if cfg.dyn_input_settling:
                tau_q = cfg.dyn_r_source * c_load[n] + cfg.dyn_r_on * cfg.c_sadc
                eps_q = np.exp(-tacq / tau_q) if tau_q > 0 else 0.0
                e_sadc[n] = -eps_q * (sample.x_sadc[n] - pool.v_top_q[n % 2])
                coarse[n] = sadc.convert(np.array([sample.x_sadc[n] + e_sadc[n]]))[0]
                pool.v_top_q[n % 2] = sample.x_sadc[n] + e_sadc[n]
            command = coarse[n] * units + d_code[n]
            vd = pool.split_dac_voltage(conv[n : n + 1], np.array([command]), sid[n : n + 1])[0]
            residue_n = sample.x_rdac[n] + e_input[n] - vd
            pool.v_top[ids] = residue_n
            pool.release(ids)

    k = coarse * units + d_code
    vd0 = dac.evaluate_nominal(k)
    vd_true = pool.split_dac_voltage(conv, k, sid)
    dyn = apply_dynamics(
        replace(cfg, dyn_input_settling=False),
        x=sample.x_rdac,
        v_prev=np.zeros(n_samples),
        v_nominal=vd0,
        code=k,
        sid=sid,
        c_active=c_load,
        n_levels=dac.levels,
        n_units=cfg.dac_n_units,
        xtalk_profile=xtalk_profile(cfg, cfg.dac_n_units, np.random.default_rng(cfg.seed + 8204)),
        perm_fn=dac.full_order,
        c_xtalk_out=c_sig,
        c_load_ref=float(c_load.mean()),
    )
    vd_true += dyn.e_dac
    stored = sample.x_rdac + e_input
    residue = stored - vd_true
    vra, ra_sat = ra.evaluate(residue, sample_rng, g=gain)
    ktc = KTCBranch(cfg)
    alpha_physical = coeff["alpha"] if cfg.dither_mode == "sampling" else 1.0
    vnc, ktc_sat = ktc.observe(sample.n_R, alpha_physical * sample.dx, sample_rng)
    fine, adc2_over = ADC2(cfg).quantize_with_correction(vra, state.kappa * vnc)
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
        acquisition_start=sample.t1 - cfg.dyn_t_sample_frac / cfg.fs,
    )
