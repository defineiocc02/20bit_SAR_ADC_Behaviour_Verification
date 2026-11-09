# =============================================================================
# 文件名     : test_adc2_dither.py
# 模块名     : test_adc2_dither
# 版本       : v1.0
# 功能       : Verify the ideal ADC2 dither diagnostic with small, independent references.
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Verify the ideal ADC2 dither diagnostic with small, independent references."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.capacitors import ExperimentConfig
from task1.experiments import adc2_dither_study as study
from task1.experiments import inl_dnl
from task1.experiments.adctoolbox_calibration import CalibrationConfig, fixed_ab_plan
from task1.experiments.inl_dnl import Intervals
from task1.model.codebook import map_record


# =========================================================================
# 1. 函数组：_ideal_ramp / test_dither_code_density_matches_direct_uniform_ramp / test_dither_exposes_overrange_without_publishing_dnl
# =========================================================================
# -------------------------------------------------------------------------
# A fine step spanning four output codes fills only with equal-weight dither words.
# An otherwise in-range analog ramp crosses both ADC2 rails for some dither words.
# -------------------------------------------------------------------------
def _ideal_ramp(monkeypatch: pytest.MonkeyPatch, gain: float) -> dict:
    # Keep production interval integration, dither probabilities, and code density.
    # Replace only the analog front end with two identical, analytically linear states.
    monkeypatch.setattr(study, "OUTPUT_BITS", 4)
    monkeypatch.setattr(inl_dnl, "OUTPUT_BITS", 4)
    monkeypatch.setattr(study, "PLOT_BITS", 3)
    monkeypatch.setattr(study, "VIEW_CODES", slice(0, 16))
    monkeypatch.setattr(study, "codebook", lambda *_args: np.array([-1.0]))
    monkeypatch.setattr(
        study,
        "_build_intervals",
        lambda *_args, **_kwargs: Intervals(
            np.full(2, -1.0),
            np.full(2, 1.0),
            np.full(2, -gain),
            np.full(2, gain),
            np.full(2, gain),
            np.full(2, gain),
        ),
    )
    return {
        "nominal_threshold_v": np.array([-1.0]),
        "fixed_ab_lms_only_frozen_gain": np.full(2, gain),
        "fixed_ab_lms_only_frozen_equivalent_switch_weight_v": np.zeros(40),
    }


def test_dither_code_density_matches_direct_uniform_ramp(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fine step spanning four output codes fills only with equal-weight dither words."""
    data = _ideal_ramp(monkeypatch, gain=0.5)
    experiment = ExperimentConfig(full_scale_peak_v=1)
    cfg = replace(CalibrationConfig(), adc2_bits=3, adc2_half_range_v=1)
    words = study._dither_words(0.25)
    expected_words = np.arange(-15, 16, 2) / 128
    np.testing.assert_array_equal(words, expected_words)
    report, traces = study._density(data, "fixed_ab_lms_only", experiment, cfg, words)

    vin = (np.arange(4096) + 0.5) / 2048 - 1
    for mode, shifts in (("off", np.array([0.0])), ("on", expected_words)):
        adc2_codes = np.floor((0.5 * vin + shifts[:, None] + 1) * 4)
        outputs = 2 * ((adc2_codes + 0.5) / 4 - 1 - shifts[:, None])
        codes = np.clip(np.floor((outputs + 1) * 8).astype(int), 0, 15)
        expected_width = np.bincount(codes.ravel(), minlength=16) / (2048 * len(shifts))
        np.testing.assert_array_equal(traces[f"{mode}_central20_width_v"], expected_width)
        assert report[mode]["adc2_below_input_width_v"] == 0
        assert report[mode]["adc2_above_input_width_v"] == 0

    assert report["off"]["20bit"]["zero_width_interior_codes"] == 10
    assert report["on"]["20bit"]["zero_width_interior_codes"] == 0


def test_dither_exposes_overrange_without_publishing_dnl(monkeypatch: pytest.MonkeyPatch) -> None:
    """An otherwise in-range analog ramp crosses both ADC2 rails for some dither words."""
    data = _ideal_ramp(monkeypatch, gain=0.95)
    cfg = replace(CalibrationConfig(), adc2_bits=3, adc2_half_range_v=1)
    experiment = ExperimentConfig(full_scale_peak_v=1)
    with pytest.raises(ValueError, match="uniform ramp overloads ADC2"):
        study._density(data, "fixed_ab_lms_only", experiment, cfg, study._dither_words(0.25))


# =========================================================================
# 2. 函数组：test_headroom_uses_half_open_bounds_and_keeps_overloads / test_dither_replay_subtracts_once_in_adc2_units
# =========================================================================
# -------------------------------------------------------------------------
# At ±50 mV distinguish edge cases from overrange at every allowed word.
# The same held samples and words preserve the subtraction sign and the frozen gain.
# -------------------------------------------------------------------------
def test_headroom_uses_half_open_bounds_and_keeps_overloads() -> None:
    """At ±50 mV distinguish edge cases from overrange at every allowed word."""
    cfg = replace(CalibrationConfig(), adc2_bits=12)
    residues = np.array([-0.051, -0.05, 0.0, 0.05, 0.051])
    headroom = study._headroom(residues, cfg, peak=5)
    window = headroom["50mV"]
    assert window["overload_without_dither"] == 3
    assert window["at_least_one_dither_word_overloads"] == 4
    assert window["every_dither_word_overloads"] == 2
    assert window["sample_word_pairs_overloaded"] == 48
    assert window["sample_word_pairs"] == 80
    assert headroom["100mV"]["at_least_one_dither_word_overloads"] == 0


def test_dither_replay_subtracts_once_in_adc2_units(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same held samples and words preserve the subtraction sign and the frozen gain."""
    # This test checks exact time-domain values. The spectrum analyzer needs a longer capture.
    monkeypatch.setattr(study, "calibration_metrics", lambda *_args: {})
    arm = "fixed_ab_lms_only"
    cfg = replace(CalibrationConfig(), adc2_bits=3, adc2_half_range_v=1)
    experiment = ExperimentConfig(full_scale_peak_v=1)
    residues = np.array([0.0375, -0.03, 0.125, -0.106, 0.007, 0.09, -0.14, -0.01])
    codes = np.full(len(residues), 256, dtype=np.uint16)
    phases = np.zeros(len(residues), dtype=np.uint8)
    main, sub = map_record(codes, (phases, phases), dem_enable=False)
    threshold = np.arange(512) / 256 - 1
    q32 = np.full(len(residues), 1 << 28)
    gain = np.tile([1.25, 0.75], len(residues) // 2)
    fine = np.floor((residues + 1) * 4).astype(np.uint16)
    baseline = (1 + 1 / 16 + (fine + 0.5) / 4 - 1) / gain - 1
    data = {
        "nominal_threshold_v": threshold,
        "reference_holdout_v": baseline,
        f"{arm}_holdout_plan": fixed_ab_plan(len(residues)),
        f"{arm}_holdout_coarse_code": codes,
        f"{arm}_holdout_main_mask": main,
        f"{arm}_holdout_sub_mask": sub,
        f"{arm}_holdout_residue_v": residues,
        f"{arm}_holdout_correction_q32": q32,
        f"{arm}_frozen_gain": np.array([1.25, 0.75]),
        f"{arm}_frozen_equivalent_switch_weight_v": np.zeros(40),
        f"{arm}_holdout_adc2_code": fine,
        f"{arm}_holdout_calibrated_v": baseline,
    }
    words = study._dither_words(0.25)
    _, first = study._tone(data, arm, experiment, cfg, words)
    _, second = study._tone(data, arm, experiment, cfg, words)
    np.testing.assert_array_equal(first["dither_word_index"], second["dither_word_index"])
    np.testing.assert_array_equal(
        first["on_repeat16_post_export_v"], second["on_repeat16_post_export_v"]
    )
    injected = words[first["dither_word_index"]]
    fine_dithered = np.floor((residues + injected + 1) * 4)
    assert np.any(fine_dithered != fine)
    assert np.any(injected > 0) and np.any(injected < 0)
    expected = baseline + ((fine_dithered - fine) / 4 - injected) / gain
    scale = 1 << 19
    export_codes = np.clip(np.floor((expected + 1) * scale), 0, 2 * scale - 1)
    export_centers = (export_codes + 0.5) / scale - 1
    for count in (1, 4, 16):
        np.testing.assert_allclose(
            first[f"on_repeat{count}_pre_export_v"],
            expected[:count].mean(axis=0),
            rtol=0,
            atol=1e-15,
        )
        np.testing.assert_array_equal(
            first[f"on_repeat{count}_post_export_v"], export_centers[:count].mean(axis=0)
        )
