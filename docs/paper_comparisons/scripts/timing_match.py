#!/usr/bin/env python3
"""Run paper-inspired 40 MS/s timing-skew ablation with frozen physical pools.

Only the paper's 40 MS/s, 18/8 allocation and 10 ps label are disclosed here.
The 10 ps RMS interpretation, input amplitude/frequency, FFT length/window and
seed are declared assumptions; this is not a reconstruction of the authors' run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from adctoolbox.spectrum import compute_spectrum

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
from adi_model import Config, sine_input  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402
from adi_model.slice_pool import PhysicalSlicePool  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--out", type=Path, required=True)
p.add_argument("--n", type=int, default=16384)
p.add_argument("--seed", type=int, default=42)
p.add_argument("--amplitude", type=float, default=2.85)
p.add_argument("--skew-model", choices=["normal_rms", "bank_peak_to_peak"], default="normal_rms")
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=False)
fs = 40e6
k = round(4e6 * a.n / fs)
k += 1 if k % 2 == 0 else 0
fin = fs * k / a.n
truth = sine_input(a.amplitude, fin, 0.5)
result_rows = []
for mode in ("rotate", "permute"):
    for enabled in (False, True):
        cfg = Config.paper_literal(
            fs=fs,
            seed=a.seed,
            dem_enable=True,
            dem_mode=mode,
            mismatch_enable=False,
            sadc_rdac_gain_mismatch=0,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            slice_timing_skew_s=10e-12,
            dyn_input_settling=False,
            dyn_ref_settling=False,
            dyn_crosstalk=False,
            dither_mode="off",
        )
        pool = PhysicalSlicePool(cfg, np.random.default_rng(cfg.seed + 8101))
        original_skew = pool.t_skew.copy()
        if a.skew_model == "bank_peak_to_peak":
            pool.t_skew[:] = np.r_[np.full(8, -5e-12), np.full(8, 5e-12), np.zeros(2)]
        if not enabled:
            pool.t_skew[:] = 0
        started = time.monotonic()
        r = run_pipeline(cfg, truth, a.n, pool=pool)
        quant = r.to_codes()
        x = r.sample.x1_clean
        y = quant.voltage
        err = y - x
        if not np.all(np.isfinite(y)):
            raise RuntimeError("non-finite output")
        fft = np.fft.rfft(y - y.mean()) / a.n
        power = np.abs(fft) ** 2
        power[1:-1] *= 2
        power /= cfg.v_fs**2 / 2
        t = compute_spectrum(
            y,
            fs=fs,
            max_scale_range=(-cfg.v_fs, cfg.v_fs),
            win_type="boxcar",
            side_bin=0,
            max_harmonic=10,
        )
        toolkit_power = 10 ** (np.asarray(t["plot_data"]["power_spectrum_db_plot"]) / 10)
        # Tiny bins are floored by the toolkit; compare nonzero bins on linear power.
        relative = float(
            np.max(np.abs(power[1:] - toolkit_power[1:])) / max(float(np.max(power[1:])), 1e-30)
        )
        overflow = int(np.count_nonzero(r.rdac_over | r.adc2_over | r.ra_sat))
        clips = int(np.count_nonzero(quant.clipped_low | quant.clipped_high))
        name = mode + ("_10ps" if enabled else "_zero")
        np.savez_compressed(
            a.out / (name + ".npz"),
            x_v=x,
            out_v=y,
            out_code=quant.code,
            error_v=err,
            spectrum_power=power,
            slice_ids=r.conv_slice_ids,
            skew_s=pool.t_skew,
            skew_draw_s=original_skew,
            overflow=(r.rdac_over | r.adc2_over | r.ra_sat),
        )
        metrics = t["metrics"]
        row = {
            "case": name,
            "fs_hz": fs,
            "fin_hz": fin,
            "n": a.n,
            "k": k,
            "amplitude_v_peak": a.amplitude,
            "seed": a.seed,
            "skew_model": a.skew_model,
            "skew_parameter_s": 1e-11 if enabled else 0,
            "realized_slice_skew_std_s": float(np.std(pool.t_skew)),
            "fullscale_peak_v": cfg.v_fs,
            "scheduler": mode,
            "noise_disabled": True,
            "dem_enabled": True,
            "dither": "off",
            "window": "boxcar",
            "fft_bin_normalization": "one-sided dBFS/bin; 3 V peak full-scale sine",
            "SNDR_dB": float(metrics["sndr_dbc"]),
            "SFDR_dBc": float(metrics["sfdr_dbc"]),
            "error_rms_v": float(np.sqrt(np.mean(err**2))),
            "error_std_v": float(np.std(err)),
            "overflow_samples": overflow,
            "output_clips": clips,
            "independent_vs_adctoolbox_max_linear_relative_to_peak": relative,
            "signal_fundamental_bin": int(t["plot_data"]["fundamental_bin"]),
            "seconds": time.monotonic() - started,
            "config": cfg.to_dict(),
            "record_sha256": hashlib.sha256((a.out / (name + ".npz")).read_bytes()).hexdigest(),
        }
        result_rows.append(row)
        print(
            name,
            "SNDR",
            round(row["SNDR_dB"], 3),
            "RMS_uV",
            round(row["error_rms_v"] * 1e6, 3),
            "ovf",
            overflow,
            "sec",
            round(row["seconds"], 1),
            flush=True,
        )
        (a.out / "metrics.json").write_text(
            json.dumps({"cases": result_rows}, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        )
print("TIMING_RUN_COMPLETE", len(result_rows), flush=True)
