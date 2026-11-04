#!/usr/bin/env python3
"""Regenerate the 40 MS/s interleaving plot inputs in a new output directory."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from adi_model import Config, sine_input  # noqa: E402
from adi_model.pipeline import run_pipeline  # noqa: E402


def main():
    """Run the four physical-pool scheduling comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="A new output directory")
    parser.add_argument(
        "--compare-frozen", action="store_true", help="Compare against README arrays"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
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
    slice_gain = {
        "mismatch_enable": True,
        "mismatch_sigma0": 0.001,
        "mismatch_split": (0, 1, 0),
        "dac_bridge_mismatch_sigma": 0,
        "dac_parasitic_spread": 0,
    }
    cases = {
        "offset_rotate": {"slice_offset_sigma_v": 50e-6, "dem_mode": "rotate"},
        "offset_permute": {"slice_offset_sigma_v": 50e-6, "dem_mode": "permute"},
        "slice_gain_rotate": dict(slice_gain, dem_mode="rotate"),
        "slice_gain_permute": dict(slice_gain, dem_mode="permute"),
    }
    arrays, rows = {}, []
    for name, overrides in cases.items():
        cfg = replace(base, **overrides)
        result = run_pipeline(cfg, signal, n)
        error = result.out - result.sample.x1_clean
        overflow = result.rdac_over | result.adc2_over | result.ra_sat
        power = np.abs(np.fft.rfft(error - error.mean()) / n) ** 2
        power[1:-1] *= 2
        norm = cfg.v_fs**2 / 2
        arrays[name] = error
        rows.append(
            {
                "condition": name,
                "n": n,
                "k": k,
                "fs_hz": cfg.fs,
                "fin_hz": cfg.fs * k / n,
                "seed": cfg.seed,
                "error_std_v": float(error.std()),
                "error_rms_v": float(np.sqrt(np.mean(error**2))),
                "spur_fs2_dbfs_bin": float(10 * np.log10(max(power[n // 2] / norm, 1e-300))),
                "spur_fs2_minus_fin_dbfs_bin": float(
                    10 * np.log10(max(power[n // 2 - k] / norm, 1e-300))
                ),
                "analog_overflow_count": int(overflow.sum()),
            }
        )
    np.savez_compressed(args.out / "interleave_40m.npz", **arrays)
    (args.out / "metrics.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    if args.compare_frozen:
        frozen = np.load(ROOT / "docs/readme/data/interleave_40m.npz")
        for name, value in arrays.items():
            if not np.allclose(value, frozen[name], rtol=0, atol=1e-14):
                raise SystemExit(f"Behavioral trace differs from frozen result: {name}")
    print(f"Saved {len(rows)} behavioral cases to {args.out}; no mismatch estimator was used.")


if __name__ == "__main__":
    main()
