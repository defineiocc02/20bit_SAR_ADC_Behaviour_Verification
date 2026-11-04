"""Independent checks of structural sample, bridge and reference contracts."""

import csv
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from adi_model import Config, ConfigError, sine_input
from adi_model.closure_experiments import signed_reference_loading
from adi_model.mapper import dem_state_sequence
from adi_model.pipeline import run_pipeline
from adi_model.reference_charge import resolved_loading_codes


def test_all_nine_bit_resolved_events_have_flash_prefix_and_no_trial_loads():
    for code in range(512):
        expected = [((code >> shift) << shift) for shift in range(6, -1, -1)]
        actual = resolved_loading_codes(code, bits=9, units=1, dither=0)
        np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(
        resolved_loading_codes(173, bits=9, units=1, dither=-2),
        [126, 158, 158, 166, 170, 170, 171],
    )


def test_reference_loading_acceptance_uses_resolved_events_and_charge_oracle():
    result = signed_reference_loading(Config(), n=512)
    assert [row["events_per_conversion"] for row in result["rows"]] == [1, 7]
    assert all(result["checks"].values())
    assert result["PASS"]


def test_reference_event_trace_rejects_a_repeated_nonadjacent_sample(tmp_path):
    cfg = Config.rtl_structural(dem_enable=True)
    states = dem_state_sequence(3, cfg, np.arange(3) % 2)[1:]

    def group(target):
        return [
            (0, target + 2, target, target, 0, phase, phase * 25 / 16, int(code), states[target])
            for phase, code in zip(
                range(3, 10), resolved_loading_codes(target, 9, 1, 0), strict=True
            )
        ]

    trace = tmp_path / "events.csv"
    with trace.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "mode",
                "sample_id",
                "target",
                "coarse",
                "dither",
                "phase",
                "elapsed_ns",
                "command",
                "sid",
            ]
        )
        writer.writerows(group(0) + group(1) + group(0) + group(1) * 1533)
    runner = Path(__file__).resolve().parents[2] / "tools/verify_rdac_events.py"
    result = subprocess.run(
        [sys.executable, str(runner), str(trace)], capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "duplicated RDAC sample group" in result.stderr


def test_rtl16_profile_reports_actual_phase_times_and_sample_ownership():
    cfg = Config.rtl_structural(
        dyn_ref_settling=True,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
    )
    result = run_pipeline(cfg, sine_input(1, 100e3), 8)
    np.testing.assert_array_equal(result.sample_id, np.arange(2, 10))
    np.testing.assert_array_equal(result.held_sample, np.tile(np.arange(2, 10)[:, None], (1, 8)))
    np.testing.assert_allclose(
        result.conversion_trace.event_times_s,
        np.arange(3, 10) / (16 * cfg.fs),
        rtol=0,
        atol=1e-23,
    )
    assert result.conversion_trace.reference_charge_c.shape == (8, 7, 2)
    assert result.conversion_trace.phase_times_s[0] == pytest.approx(10 / (16 * cfg.fs))
    assert result.conversion_trace.phase_times_s[-1] == pytest.approx(1 / cfg.fs)


def test_structural_profile_rejects_silently_incompatible_timing_or_geometry():
    cfg = Config.rtl_structural()
    with pytest.raises(ConfigError, match="rtl16 timing"):
        replace(cfg, b1=7).check_legal()
    with pytest.raises(ConfigError, match="rtl16 timing"):
        replace(cfg, dyn_t_conv_frac=0.4).check_legal()
    with pytest.raises(ConfigError, match="rtl16 timing"):
        replace(cfg, rdac_bitwise_loading=False).check_legal()
    round_trip = Config.from_dict(cfg.to_dict())
    assert round_trip == cfg
