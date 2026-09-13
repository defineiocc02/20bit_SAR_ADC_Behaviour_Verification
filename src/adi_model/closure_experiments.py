"""Acceptance experiments for the physical and digital closure milestones.

These protocols keep fabrication, independent noise, training and validation
identities explicit. They produce numerical evidence and exact boolean gates;
no fitted paper target is mislabeled as a silicon performance prediction.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .benchmarks import PAPER_BENCHMARK, SLIDES_BENCHMARK
from .low_frequency_noise import BandLimitedFlicker
from .metrics import power_spectral_density, sine_fit_metrics


def long_record_noise() -> dict:
    """Verify 40 Hz state over 64 seconds using sparse actual 40 MS/s timestamps."""
    from scipy.special import sici

    fs, hop, samples, modes, count = 40e6, 156250, 16384, 128, 32
    probe_fs = fs / hop
    s0, fc, fl = (8.8e-9) ** 2, 40.0, 0.1
    indices = np.arange(samples, dtype=np.int64) * hop
    segment = 1024
    ps_flicker = np.zeros(segment // 2 + 1)
    ps_total = np.zeros_like(ps_flicker)
    segments = 0
    for seed in range(count):
        rng = np.random.default_rng(24001 + seed)
        state = BandLimitedFlicker.create(s0, fc, fl, rng, modes=modes)
        noise = state.at_adc_indices(indices, fs)
        # Explicit ideal antialias observation channel of width probe_fs/2.
        # This is not unfiltered white noise decimated from the fast ADC output.
        white = rng.normal(0, np.sqrt(s0 * probe_fs / 2), samples)
        for start in range(0, samples - segment + 1, segment // 2):
            a = power_spectral_density(noise[start : start + segment], probe_fs, window="hann")
            b = power_spectral_density(
                (noise + white)[start : start + segment], probe_fs, window="hann"
            )
            ps_flicker += a["density_v2_hz"]
            ps_total += b["density_v2_hz"]
            segments += 1
    ps_flicker /= segments
    ps_total /= segments
    f = a["frequency_hz"]
    band = (f >= 2) & (f <= 30)
    slope = float(np.polyfit(np.log10(f[band]), 10 * np.log10(ps_flicker[band]), 1)[0])
    band = (f >= 2) & (f <= 20)
    target = np.sum(s0 * fc / f[band]) * probe_fs / segment
    measured = ps_flicker[band].sum() * probe_fs / segment
    corner_band = (f >= 32) & (f <= 38)
    corner_error_db = float(
        10 * np.log10(np.sum(ps_total[corner_band]) / np.sum(s0 * (1 + fc / f[corner_band])))
    )
    above = (f >= 80) & (f <= 110)
    white_error_db = float(10 * np.log10(np.mean(ps_total[above]) / s0))
    leakage_db = float(10 * np.log10(np.mean(ps_flicker[above]) / s0))
    # Mode-density convergence compares conditional covariance quadrature to
    # an independent continuous cosine-integral oracle, without fitting data.
    lag = np.linspace(0.00001, 0.1, 257)
    variance = s0 * fc * np.log(fc / fl)
    covariance = s0 * fc * (sici(2 * np.pi * fc * lag)[1] - sici(2 * np.pi * fl * lag)[1])
    convergence = []
    for m in (32, 128, 512):
        estimates = []
        for seed in range(16):
            state = BandLimitedFlicker.create(
                s0, fc, fl, np.random.default_rng(731 + seed), modes=m
            )
            quadrature = variance * np.mean(
                np.cos(2 * np.pi * lag[:, None] * state.frequency_hz), axis=1
            )
            estimates.append(np.mean(((quadrature - covariance) / variance) ** 2))
        convergence.append(
            {"modes": m, "covariance_relative_rms": float(np.sqrt(np.mean(estimates)))}
        )
    checks = {
        "physical_clock_and_duration": fs == 40e6 and indices[-1] / fs > 63.99,
        "flicker_slope": abs(slope + 10) < 0.6,
        "flicker_integrated_band": abs(float(10 * np.log10(measured / target))) < 0.6,
        "corner_and_white_floor": abs(corner_error_db) < 0.6 and abs(white_error_db) < 0.3,
        "no_extra_high_frequency_white": leakage_db < -40,
        "mode_covariance_convergence": convergence[-1]["covariance_relative_rms"] < 0.001
        and convergence[-1]["covariance_relative_rms"] < convergence[0]["covariance_relative_rms"],
    }
    return {
        "PASS": all(checks.values()),
        "checks": {key: bool(value) for key, value in checks.items()},
        "adc_fs_hz": fs,
        "probe_fs_hz": probe_fs,
        "physical_sample_stride": hop,
        "duration_s": samples / probe_fs,
        "record_samples": samples,
        "low_cutoff_hz": fl,
        "corner_hz": fc,
        "modes": modes,
        "realizations": count,
        "welch_segments_total": segments,
        "psd_resolution_hz": probe_fs / segment,
        "slope_db_decade": slope,
        "band_power_v2": float(measured),
        "band_prediction_v2": float(target),
        "corner_error_db": corner_error_db,
        "white_error_db": white_error_db,
        "above_corner_flicker_db": leakage_db,
        "mode_convergence": convergence,
        "scope": "stationary slow component and ideal antialias observation; not full fast-chain noise or AZ validation",
    }


def noisy_weight_holdout() -> dict:
    """Train two noisy sizes on two full physical chips and score frozen final words."""
    from .sampler import sine_input
    from .weight_calibration import run_with_split_calibration

    rows: list[dict[str, Any]] = []
    checks = {}
    n_val = 16384
    for seed in (42, 99):
        cfg = PAPER_BENCHMARK.configuration(
            seed=seed,
            mismatch_sigma0=0.001,
            dither_mode="off",
            dem_enable=True,
            dem_mode="permute",
            calibration="weights",
        )
        fin = 307 * cfg.fs / n_val
        for n_cal in (8192, 16384):
            r = run_with_split_calibration(cfg, sine_input(2.2, fin, 0.5), n_val, n_cal=n_cal)
            model = r.state.weight_calibration
            codes = r.to_codes()
            error = codes.voltage - r.x_ref
            before = float(np.std(r.uncalibrated_out - r.x_ref))
            after = float(np.std(error))
            metrics = sine_fit_metrics(codes.voltage, cfg.fs, fin)
            row = {
                "chip_seed": seed,
                "training_samples": n_cal,
                "validation_samples": n_val,
                "rank": model.rank,
                "condition": model.condition,
                "coefficient_se_rms": float(np.sqrt(np.mean(model.standard_error[:-1] ** 2))),
                "before_error_rms_v": before,
                "after_error_rms_v": after,
                "fixed_vs_float_peak_v": float(np.max(np.abs(codes.voltage - r.out))),
                "SNDR_dB": metrics["SNDR_dB"],
                "SFDR_dB": metrics["SFDR_dB"],
                "ENOB": metrics["ENOB"],
                "NSD_V_rtHz": metrics["NSD_V_rtHz"],
                "accumulator_bits": codes.peak_accumulator_bits,
                "training_digest": model.training_digest,
                "same_pool": r.calibration_report["same_physical_pool"],
                "overflow_count": int(
                    np.count_nonzero(codes.clipped_low | codes.clipped_high | codes.analog_overflow)
                ),
            }
            rows.append(row)
            checks[f"chip_{seed}_n_{n_cal}"] = bool(
                model.rank == 1279
                and row["same_pool"]
                and row["overflow_count"] == 0
                and after < 65e-6
                and after < 0.8 * before
                and row["fixed_vs_float_peak_v"] < 0.51 * cfg.lsb_target
            )
        checks[f"uncertainty_decreases_chip_{seed}"] = (
            rows[-1]["coefficient_se_rms"] < 0.9 * rows[-2]["coefficient_se_rms"]
        )
    return {
        "PASS": all(checks.values()),
        "checks": {key: bool(value) for key, value in checks.items()},
        "rows": rows,
        "benchmarks": [PAPER_BENCHMARK.to_dict(), SLIDES_BENCHMARK.to_dict()],
        "scope": "18 slices, 8 active, 63+8 candidate; static controlled noisy training; independent holdout; final fixed-point words; reference-source error excluded",
    }


def signed_reference_loading(c0, n=16384) -> dict:
    """Verify signed rail-charge recurrence and joint settling on actual conversions."""
    from dataclasses import replace

    from .config import Config
    from .conversion import ConversionParameters
    from .pipeline import run_pipeline
    from .sampler import sine_input

    cfg = Config.paper_literal(
        seed=c0.seed,
        dither_mode="off",
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dyn_ref_settling=True,
        dyn_tau_ref=20e-9,
        dyn_t_conv_frac=0.4,
        conversion=ConversionParameters(
            fine_reference_tau_s=3e-9,
            ra_bandwidth_hz=150e6,
            adc2_wide_bandwidth_hz=300e6,
            adc2_narrow_bandwidth_hz=80e6,
        ),
    )
    fin = 173 * cfg.fs / n
    fn = sine_input(2.1, fin)
    rows, streams = [], []
    for bitwise in (False, True):
        cc = replace(cfg, rdac_bitwise_loading=bitwise)
        r = run_pipeline(cc, fn, n)
        trace = r.conversion_trace
        # Independent discrete-event reconstruction, including cross-sample
        # recovery, actual signed loads and a separate fine recovery constant.
        rail = np.zeros(2)
        peak = 0.0
        fine_duration = cc.dyn_t_conv_frac / cc.fs
        idle = 1 / cc.fs - cc.conversion.quantizer_time_s - fine_duration
        for i in range(n):
            rail *= np.exp(-idle / cc.dyn_tau_ref) if i else 1
            previous = 0.0
            for e, instant in enumerate(trace.event_times_s):
                rail = (
                    rail * np.exp(-(instant - previous) / cc.dyn_tau_ref)
                    - trace.reference_charge_c[i, e] / cc.dyn_c_decouple
                )
                peak = max(peak, float(np.max(np.abs(rail - trace.reference_event_error_v[i, e]))))
                previous = instant
            rail *= np.exp(-fine_duration / (cc.conversion.fine_reference_tau_s or cc.dyn_tau_ref))
            peak = max(peak, float(np.max(np.abs(rail - trace.reference_error_v[i]))))
        words = r.to_codes()
        rows.append(
            {
                "bitwise": bitwise,
                "events_per_conversion": len(trace.event_times_s),
                "signed_event_oracle_peak_v": peak,
                "positive_rail_charge_min_c": float(trace.reference_charge_c[:, :, 0].min()),
                "positive_rail_charge_max_c": float(trace.reference_charge_c[:, :, 0].max()),
                "negative_rail_charge_min_c": float(trace.reference_charge_c[:, :, 1].min()),
                "peak_rail_error_v": float(trace.reference_peak_v.max()),
                "peak_rail_fraction": float(trace.reference_peak_v.max() / cc.v_fs),
                "adc2_vs_ra_peak_v": float(np.max(np.abs(r.adc2_input_voltage - r.vra))),
                "final_word_SNDR_dB": sine_fit_metrics(words.voltage, cc.fs, fin)["SNDR_dB"],
                "analog_overflow_count": int(np.count_nonzero(words.analog_overflow)),
            }
        )
        streams.append(r)
    # Switching control must be inert when reference dynamics are disabled.
    a = run_pipeline(replace(cfg, dyn_ref_settling=False, rdac_bitwise_loading=False), fn, 512)
    b = run_pipeline(replace(cfg, dyn_ref_settling=False, rdac_bitwise_loading=True), fn, 512)
    checks = {
        "signed_recovery_recurrence": max(x["signed_event_oracle_peak_v"] for x in rows) < 1e-12,
        "real_loading_sequences": rows[0]["events_per_conversion"] == 1
        and rows[1]["events_per_conversion"] == 2 * cfg.b1 + 1,
        "signed_charge_not_fixed_binary_proxy": rows[1]["negative_rail_charge_min_c"]
        < 0
        < rows[1]["positive_rail_charge_max_c"],
        "small_droop_domain": max(x["peak_rail_fraction"] for x in rows) < 0.01,
        "adc2_samples_joint_response": min(x["adc2_vs_ra_peak_v"] for x in rows) > 1e-8,
        "loading_changes_physical_result": float(np.max(np.abs(streams[0].out - streams[1].out)))
        > 1e-8,
        "control_inactive_without_reference": bool(np.array_equal(a.out, b.out)),
        "backend_headroom": all(x["analog_overflow_count"] == 0 for x in rows),
    }
    return {
        "PASS": all(checks.values()),
        "checks": checks,
        "rows": rows,
        "判据": "实际有符号电荷事件与独立跨周期恢复递推一致；逐位序列改变参考/RA/ADC2 联合响应；分别报告两种装载的最终整数码 SNDR。具体 SAR 试探次序与相位时间为假设，不预设逐位必然降低峰值或噪声。",
    }


def independent_dither_range(c0, n=16384) -> dict:
    """Test known dither range scaling independently of unknown-input decisions."""
    from .config import Config
    from .pipeline import run_pipeline
    from .sampler import sine_input

    fin = 173 * c0.fs / n
    rows: list[dict[str, Any]] = []
    for mode in ("off", "quantizer", "sampling"):
        cfg = Config.paper_literal(
            seed=c0.seed,
            fs=c0.fs,
            dither_mode=mode,
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_mismatch_enable=False,
            sadc_rdac_gain_mismatch=0,
        )
        r = run_pipeline(cfg, sine_input(2.1, fin), n)
        words = r.to_codes()
        row = {
            "mode": mode,
            "decision_bits": cfg.b1,
            "nominal_decision_levels": cfg.stage1_levels,
            "rdac_grid_steps_per_decision": cfg.delta1 / cfg.rdac_step,
            "dither_port_scale": cfg.dither_rdac_ratio,
            "raw_error_peak_v": float(np.max(np.abs(r.err))),
            "word_error_peak_v": float(np.max(np.abs(words.voltage - r.x_ref))),
            "adc2_overflow_count": int(np.count_nonzero(words.analog_overflow)),
            "residue_range_v": [float(r.residue.min()), float(r.residue.max())],
        }
        if mode == "quantizer":
            # The sampler records nominal dQ in dither and the separate known
            # RDAC injection in rdac_dither. Scaling does not create decisions.
            row["port_scale_error_v"] = float(
                np.max(np.abs(r.sample.rdac_dither - 4 * r.sample.dither))
            )
        rows.append(row)
    checks = {
        "nine_independent_decisions": all(
            x["decision_bits"] == 9 and x["nominal_decision_levels"] == 512 for x in rows
        ),
        "separate_fourfold_range": rows[1]["port_scale_error_v"] < 1e-15
        and rows[1]["dither_port_scale"] == 4,
        "no_code_grid_information_claim": all(
            abs(x["rdac_grid_steps_per_decision"] - 1) < 1e-12 for x in rows
        ),
        "paired_charge_and_digital_correction": all(
            x["word_error_peak_v"] < 1.2 * cfg.lsb_target and x["adc2_overflow_count"] == 0
            for x in rows
        ),
    }
    return {
        "PASS": all(checks.values()),
        "checks": checks,
        "rows": rows,
        "判据": "9 位未知输入判决与已知 dither 的 4 倍端口幅度分开验证；63+8 候选中 RDAC 栅格/粗步长比为 1，不能用 7+2 解释为 9 位判决。三种注入方式的实际电荷与最终整数码校正配对；4 倍理想端口为公开结构的候选实现假设。",
    }


def ron_step_oracle() -> dict:
    """Compare code-dependent Ron against an independent common-mode RC formula."""
    from .input_network import InputNetworkParameters, track_interval
    from .sampler import dc_input

    rows: list[dict[str, Any]] = []
    for rho in (0.0, 0.5, 2.0):
        for voltage in (-2.5, 1.0, 2.5):
            n, cap, ron, rs, dt = 8, 2.5e-12, 20.0, 30.0, 0.8e-9
            tau = (n * rs + ron * (1 + rho * (voltage / 3) ** 2)) * cap
            actual, _ = track_interval(
                np.full(n, cap),
                np.full(n, ron),
                rs,
                np.zeros(n),
                0.0,
                dt,
                dc_input(voltage),
                parameters=InputNetworkParameters(),
                ron_coefficient=rho,
                voltage_scale=3.0,
            )
            expected = voltage * (1 - np.exp(-dt / tau))
            rows.append(
                {
                    "rho": rho,
                    "input_v": voltage,
                    "tau_s": tau,
                    "oracle_peak_error_v": float(np.max(np.abs(actual - expected))),
                }
            )
    return {"PASS": max(r["oracle_peak_error_v"] for r in rows) < 1e-12, "rows": rows}
