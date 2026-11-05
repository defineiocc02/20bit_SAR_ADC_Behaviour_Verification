#!/usr/bin/env python3
"""重新仿真40 MS/s交织实验，并严格核对当前冻结数据。

保留四个历史设置的内部浮点误差数组，另比较固定A/B、generic空闲池随机选择和
结构RTL allocator的Python合同。三策略共享seed42、同一18-slice静态高斯时偏、
相干正弦与20位输出量化；不训练/补偿失配，不改变默认模型或RTL。

输出interleave_40m.json、四条误差的interleave_40m.npz、三条最终整数码的
interleave_timing_codes_40m.npz和绘图功率谱interleave_timing_40m.npz。
--compare-frozen保留原电压误差rtol=0/atol=1e-14；新增整数码和功率谱精确比较。
正常退出表示生成/一致性检查通过，不表示随机调度改善SNDR或芯片动态性能达标。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from adi_model import Config, sine_input  # noqa: E402
from adi_model.metrics import sine_fit_metrics  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402
from adi_model.slice_pool import PhysicalSlicePool  # noqa: E402

DATA = ROOT / "docs/readme/data"
SOURCE_FILES = (
    "src/adi_model/config.py",
    "src/adi_model/pipeline_engine.py",
    "src/adi_model/sim.py",
    "src/adi_model/fixed_point.py",
    "src/adi_model/dem.py",
    "src/adi_model/mapper.py",
    "src/adi_model/scheduler.py",
    "src/adi_model/timing.py",
    "src/adi_model/slice_pool.py",
    "src/adi_model/metrics.py",
    "docs/readme/reproduce_interleave_40m.py",
)


def _digest(array: np.ndarray) -> str:
    """Hash numerical bytes without nondeterministic container metadata."""
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def _spectrum(output_v: np.ndarray, fs_hz: float, v_fs: float) -> tuple[np.ndarray, np.ndarray]:
    """One-sided rectangular-window power, normalized to a v_fs-peak sine."""
    n = len(output_v)
    power = np.abs(np.fft.rfft(output_v - output_v.mean()) / n) ** 2
    power[1:-1] *= 2
    power /= v_fs**2 / 2
    return np.fft.rfftfreq(n, 1 / fs_hz), power


def _output_metrics(result, output_v: np.ndarray, k: int) -> dict:
    """Keep reference error separate from a fitted-fundamental metric residual.

    The 1/sin/cos fit is a reporting projection, not a calibration applied to
    the output. Positive phase error means advance; equivalent delay is -dphi/w.
    No overflow or clipped sample is dropped from the arrays or metrics.
    """
    cfg = result.cfg
    reference_v = result.sample.x1_clean
    n = len(output_v)
    omega = 2 * np.pi * k / n
    phase = omega * np.arange(n)
    design = np.column_stack((np.ones(n), np.sin(phase), np.cos(phase)))
    coefficient = np.linalg.lstsq(design, output_v, rcond=None)[0]
    ref_coefficient = np.linalg.lstsq(design, reference_v, rcond=None)[0]
    amplitude = float(np.hypot(coefficient[1], coefficient[2]))
    ref_amplitude = float(np.hypot(ref_coefficient[1], ref_coefficient[2]))
    phase_error = float(
        np.angle(
            np.exp(
                1j
                * (
                    np.arctan2(coefficient[2], coefficient[1])
                    - np.arctan2(ref_coefficient[2], ref_coefficient[1])
                )
            )
        )
    )
    fitted_residual = output_v - design @ coefficient
    error = output_v - reference_v
    _, power = _spectrum(output_v, cfg.fs, cfg.v_fs)
    remaining = power.copy()
    remaining[[0, k]] = 0
    sndr = float(10 * np.log10(power[k] / remaining.sum()))
    sfdr = float(10 * np.log10(power[k] / remaining.max()))
    fit = sine_fit_metrics(output_v, cfg.fs, cfg.fs * k / n)
    delta_db = abs(float(fit["SNDR_dB"]) - sndr)
    if delta_db > 1e-5:
        raise RuntimeError("coherent FFT SNDR disagrees with independent production LSQ")
    return {
        "output_domain": "final_20_bit_offset_binary_bin_center_V",
        "SNDR_dB": sndr,
        "SFDR_dBc": sfdr,
        "error_mean_v": float(error.mean()),
        "error_std_v": float(error.std()),
        "error_rms_v": float(np.sqrt(np.mean(error**2))),
        "fitted_fundamental_gain": amplitude / ref_amplitude,
        "fitted_fundamental_phase_error_rad": phase_error,
        "equivalent_delay_s": -phase_error / (2 * np.pi * cfg.fs * k / n),
        "fitted_offset_v": float(coefficient[0]),
        "fitted_fundamental_rms_v": amplitude / np.sqrt(2),
        "fit_residual_rms_v": float(np.sqrt(np.mean(fitted_residual**2))),
        "fft_vs_lsq_sndr_delta_db": delta_db,
        "spur_fs2_minus_fin_dbfs_bin": float(10 * np.log10(max(power[n // 2 - k], 1e-300))),
        "rdac_overflow_count": int(result.rdac_over.sum()),
        "ra_saturation_count": int(result.ra_sat.sum()),
        "adc2_overflow_count": int(result.adc2_over.sum()),
        "analog_overflow_count": int(
            np.count_nonzero(result.rdac_over | result.adc2_over | result.ra_sat)
        ),
    }


def _legacy_cases() -> tuple[dict, list[dict], dict]:
    """Preserve the original four settings and their internal-float array names."""
    base = Config.paper_literal(
        fs=40e6,
        seed=42,
        mismatch_enable=False,
        dem_enable=True,
        sadc_rdac_gain_mismatch=0,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dyn_ron_code_coeff=0,
    )
    n, k = 2048, 151
    signal = sine_input(2.2, base.fs * k / n, 0.5)
    gain = {
        "mismatch_enable": True,
        "mismatch_sigma0": 0.001,
        "mismatch_split": (0, 1, 0),
        "dac_bridge_mismatch_sigma": 0,
        "dac_parasitic_spread": 0,
    }
    cases = {
        "offset_rotate": {"slice_offset_sigma_v": 50e-6, "dem_mode": "rotate"},
        "offset_permute": {"slice_offset_sigma_v": 50e-6, "dem_mode": "permute"},
        "slice_gain_rotate": dict(gain, dem_mode="rotate"),
        "slice_gain_permute": dict(gain, dem_mode="permute"),
    }
    arrays, rows = {}, []
    for name, overrides in cases.items():
        cfg = replace(base, **overrides)
        result = run_pipeline(cfg, signal, n)
        error = result.out - result.sample.x1_clean
        _, power = _spectrum(error, cfg.fs, cfg.v_fs)
        words = result.to_codes()
        arrays[name] = error
        rows.append(
            {
                "condition": name,
                "n": n,
                "k": k,
                "fs_hz": cfg.fs,
                "fin_hz": cfg.fs * k / n,
                "seed": cfg.seed,
                "array_domain": "internal_float_reconstruction_error_V",
                "error_std_v": float(error.std()),
                "error_rms_v": float(np.sqrt(np.mean(error**2))),
                "error_mean_v": float(error.mean()),
                "spur_fs2_dbfs_bin": float(10 * np.log10(max(power[n // 2], 1e-300))),
                "spur_fs2_minus_fin_dbfs_bin": float(10 * np.log10(max(power[n // 2 - k], 1e-300))),
                "analog_overflow_count": int(
                    np.count_nonzero(result.rdac_over | result.adc2_over | result.ra_sat)
                ),
                "integer_output_metrics": {
                    **_output_metrics(result, words.voltage, k),
                    "digital_clip_count": int(
                        np.count_nonzero(words.clipped_low | words.clipped_high)
                    ),
                },
            }
        )
    return (
        arrays,
        rows,
        {
            "base_config": base.to_dict(),
            "case_overrides": cases,
            "amplitude_v_peak": 2.2,
            "phase_rad": 0.5,
        },
    )


def _timing_cases() -> tuple[dict, dict, dict]:
    """Compare three explicit allocator paths using identical physical skew bytes.

    fixed: paper_literal/rotate -> Scheduler -> build_slice_plan(pingpong).
    generic_random: paper_literal/permute -> ShuffledScheduler ->
        build_slice_plan(shuffle_causal), randomly choosing from the free pool.
    rtl_allocator: rtl_structural -> build_rtl_slice_plan(shuffle=True), using
        the structural cursor/scan contract. This row is Python, not a new SV run.
    """
    n, k, fs_hz = 16384, 1639, 40e6
    overrides = {
        "fs": fs_hz,
        "seed": 42,
        "dem_enable": True,
        "mismatch_enable": False,
        "sadc_rdac_gain_mismatch": 0,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "slice_timing_skew_s": 10e-12,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "dither_mode": "off",
    }
    choices = (
        (
            "fixed",
            Config.paper_literal,
            "rotate",
            "make_scheduler -> Scheduler -> build_slice_plan(pingpong)",
        ),
        (
            "generic_random",
            Config.paper_literal,
            "permute",
            "make_scheduler -> ShuffledScheduler -> build_slice_plan(shuffle_causal)",
        ),
        ("rtl_allocator", Config.rtl_structural, "permute", "build_rtl_slice_plan(shuffle=True)"),
    )
    signal = sine_input(2.85, fs_hz * k / n, 0.5)
    plot_arrays, code_arrays, rows, configs = {}, {}, [], {}
    for name, factory, mode, call in choices:
        cfg = factory(**overrides, dem_mode=mode)
        pool = PhysicalSlicePool(cfg, np.random.default_rng(cfg.seed + 8101))
        skew_hash = _digest(pool.t_skew)
        result = run_pipeline(cfg, signal, n, pool=pool)
        words = result.to_codes()
        frequency_hz, power = _spectrum(words.voltage, fs_hz, cfg.v_fs)
        plot_arrays["frequency_hz"] = frequency_hz
        plot_arrays[name + "_power"] = power
        code_arrays[name] = np.asarray(words.code, dtype=np.uint32)
        configs[name] = cfg.to_dict()
        rows.append(
            {
                "condition": name,
                "profile": "Config.rtl_structural()"
                if name == "rtl_allocator"
                else "Config.paper_literal()",
                "allocator_call": call,
                "n": n,
                "k": k,
                "fs_hz": fs_hz,
                "fin_hz": fs_hz * k / n,
                "amplitude_v_peak": 2.85,
                "phase_rad": 0.5,
                "seed": 42,
                "skew_sigma_parameter_s": 10e-12,
                "realized_skew_std_s": float(pool.t_skew.std()),
                "skew_sha256": skew_hash,
                "clean_reference_sha256": _digest(result.sample.x1_clean),
                "output_code_sha256": _digest(code_arrays[name]),
                "physical_exposures": np.bincount(
                    result.conv_slice_ids.ravel(), minlength=18
                ).tolist(),
                **_output_metrics(result, words.voltage, k),
                "digital_clip_count": int(np.count_nonzero(words.clipped_low | words.clipped_high)),
            }
        )
        print(
            f"{name}: SNDR={rows[-1]['SNDR_dB']:.6f} dB; SFDR={rows[-1]['SFDR_dBc']:.6f} dBc; reference RMS={rows[-1]['error_rms_v']*1e6:.6f} uV; fit RMS={rows[-1]['fit_residual_rms_v']*1e6:.6f} uV",
            flush=True,
        )
    if (
        len({row["skew_sha256"] for row in rows}) != 1
        or len({row["clean_reference_sha256"] for row in rows}) != 1
    ):
        raise RuntimeError("strategies do not share the same skew pool and clean reference")
    return (
        plot_arrays,
        code_arrays,
        {
            "rows": rows,
            "resolved_configs": configs,
            "scope": "Current Python full ADC; the RTL allocator row uses its structural Python contract, not a newly executed SV DUT.",
            "noise": "Stochastic sampling/RA noise and capacitor mismatch disabled; static Gaussian per-physical-slice skew remains enabled.",
            "seed_selection": "Fixed declared seed42, not chosen by searching for favorable SFDR/SNDR.",
            "calibration": "No mismatch estimator or correction; the fundamental fit is used for metrics only.",
        },
    )


def _compare(path: Path, arrays: dict, *, atol: float) -> None:
    """Compare all keys/shapes; never delete a disagreeing case or relax tolerance."""
    with np.load(path) as frozen:
        if set(frozen.files) != set(arrays):
            raise SystemExit(f"Frozen array keys differ: {path.name}")
        for name, value in arrays.items():
            if value.shape != frozen[name].shape or not np.allclose(
                value, frozen[name], rtol=0, atol=atol
            ):
                raise SystemExit(
                    f"Behavioral trace differs from frozen result: {name} ({path.name})"
                )


def main() -> None:
    """Generate a new directory; optional comparison is against current public freezes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="A new output directory")
    parser.add_argument(
        "--compare-frozen", action="store_true", help="Strictly compare all current README arrays"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    legacy_arrays, rows, legacy_config = _legacy_cases()
    plot_arrays, code_arrays, timing = _timing_cases()
    np.savez_compressed(args.out / "interleave_40m.npz", **legacy_arrays)
    np.savez_compressed(args.out / "interleave_timing_40m.npz", **plot_arrays)
    np.savez_compressed(args.out / "interleave_timing_codes_40m.npz", **code_arrays)
    metadata = {
        "artifact": "interleave_40m_current",
        "schema_version": 2,
        "description": "Current behavioral baselines; four internal-float error cases and three final-integer timing comparisons are explicitly separated.",
        "source_head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_worktree_dirty": bool(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
            ).strip()
        ),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES
        },
        "numpy_version": np.__version__,
        "historical_frozen": {
            "source_head": "d809da6cfe35a6ad02d109dbb6144259469900d6",
            "json": "historical/interleave_40m_d809da6.json",
            "arrays": "historical/interleave_40m_d809da6.npz",
            "reason": "Before the current sid[8] zero-sum bridge gate; retained unchanged rather than rolling back correct code.",
        },
        "legacy_probe": legacy_config,
        "rows": rows,
        "timing_comparison": timing,
        "metric_definitions": {
            "reference_error": "Final 20-bit bin-center voltage minus sample.x1_clean; reference RMS retains mean, gain and phase error.",
            "fit_residual": "Residual after a known-frequency 1/sin/cos LSQ fit; fitted DC, gain and phase are removed only for reporting metrics.",
            "SNDR_dB": "Coherent rectangular FFT: fundamental-bin power / sum(all other non-DC bins), full Nyquist bandwidth; independently checked against production LSQ SNDR.",
            "SFDR_dBc": "Fundamental-bin power / largest other non-DC bin; harmonics remain candidates; no window, smoothing or spur deletion.",
            "phase_and_delay": "phase_error=phase(output fit)-phase(clean-reference fit); positive means advance. equivalent_delay=-phase_error/(2*pi*fin).",
            "spectrum_power": "One-sided linear power divided by V_FS^2/2, with doubled interior bins; dBFS/bin=10*log10(power), not dBFS/Hz.",
            "error_std_vs_rms": "std removes only the error mean; reference RMS retains it. Neither removes fitted gain/phase as SNDR does.",
            "overflow": "Report RDAC, RA and ADC2 flags separately plus union; final digital clipping separately. No sample is filtered out.",
        },
        "frozen_comparison": {
            "legacy_voltage_atol_v": 1e-14,
            "rtol": 0,
            "timing_integer_code_and_power_atol": 0,
        },
    }
    (args.out / "interleave_40m.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    if args.compare_frozen:
        _compare(DATA / "interleave_40m.npz", legacy_arrays, atol=1e-14)
        _compare(DATA / "interleave_timing_codes_40m.npz", code_arrays, atol=0)
        _compare(DATA / "interleave_timing_40m.npz", plot_arrays, atol=0)
    print(
        f"Saved 4 legacy-setting probes and 3 current timing strategies in {time.monotonic()-started:.3f}s; no mismatch estimator was used."
    )


if __name__ == "__main__":
    main()
