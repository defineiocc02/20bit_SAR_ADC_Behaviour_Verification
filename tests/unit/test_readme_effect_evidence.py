"""Check that the public plots' claims are backed by the retained sample arrays."""

import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "docs/readme/data"


def test_calibration_plot_metrics_match_retained_samples():
    metadata = json.loads((DATA / "calibration_40m.json").read_text())
    with np.load(DATA / "calibration_40m.npz") as samples:
        for name, case in metadata["cases"].items():
            assert case["validation"]["fs_hz"] == 40e6
            assert case["overflow_count"] == 0
            x = samples[name + "_x_clean_v"]
            assert len(x) == case["validation"]["n"]
            for kind in ("before", "after"):
                y = samples[f"{name}_{kind}_v"]
                stats = case[kind + "_integer"]
                assert float(np.std(y - x)) == pytest.approx(stats["error_std_v"], abs=1e-14)
                power = np.abs(np.fft.rfft(y - y.mean()) / len(y)) ** 2
                power[1:-1] *= 2
                k = stats["fundamental_bin"]
                mask = np.ones(len(power), dtype=bool)
                mask[0] = mask[k] = False
                noise_and_distortion = power[mask].sum()
                sndr = 10 * np.log10(power[k] / noise_and_distortion)
                assert float(sndr) == pytest.approx(stats["SNDR_dB"], abs=1e-6)


def test_interleave_plot_spurs_and_rms_match_retained_samples():
    metadata = json.loads((DATA / "interleave_40m.json").read_text())
    with np.load(DATA / "interleave_40m.npz") as samples:
        for row in metadata["rows"]:
            assert row["fs_hz"] == 40e6
            assert row["analog_overflow_count"] == 0
            error = samples[row["condition"]]
            assert len(error) == row["n"]
            assert float(np.sqrt(np.mean(error**2))) == pytest.approx(row["error_rms_v"], abs=1e-14)
            power = np.abs(np.fft.rfft(error - error.mean()) / len(error)) ** 2
            power[1:-1] *= 2
            for key, index in (
                ("spur_fs2_dbfs_bin", len(error) // 2),
                ("spur_fs2_minus_fin_dbfs_bin", len(error) // 2 - row["k"]),
            ):
                spur = 10 * np.log10(max(power[index] / (3.0**2 / 2), 1e-300))
                assert float(spur) == pytest.approx(row[key], abs=1e-8)
