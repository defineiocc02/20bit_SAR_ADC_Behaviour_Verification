# =============================================================================
# 文件名     : test_residue_control.py
# 模块名     : test_residue_control
# 版本       : v1.0
# 功能       : Check the digital command selector and ADC2 evidence boundaries.
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Check the digital command selector and ADC2 evidence boundaries."""

import numpy as np
import pytest

from task1.experiments import residue_control_study as study
from task1.experiments.adctoolbox_calibration import fixed_ab_plan


# =========================================================================
# 1. 函数组：_arm / _phases / test_exact_tie_keeps_original_coarse_command
# =========================================================================
# -------------------------------------------------------------------------
# A midpoint with equally close adjacent thresholds keeps the input code.
# -------------------------------------------------------------------------
def _arm(gain: tuple[float, float]) -> study.FrozenArm:
    # Dyadic spacing makes the tie between c and c+1 exact in floating point.
    threshold = np.arange(-256, 257) / 64
    return study.FrozenArm(
        study.MapperTable.build(bridge=False), threshold, np.array(gain), np.zeros(40)
    )


def _phases(count: int) -> tuple[np.ndarray, np.ndarray]:
    zero = np.zeros(count, dtype=np.uint8)
    return zero, zero


def test_exact_tie_keeps_original_coarse_command() -> None:
    """A midpoint with equally close adjacent thresholds keeps the input code."""
    coarse = np.array([0, 20, 256, 511], dtype=np.uint16)
    frozen = _arm((1.0, 1.0))
    result, correction = frozen.select(coarse, fixed_ab_plan(len(coarse)), _phases(len(coarse)))
    np.testing.assert_array_equal(result.codes, coarse)
    np.testing.assert_array_equal(correction, 0)


# =========================================================================
# -------------------------------------------------------------------------
# Known gain shifts drive an interior winner, a radius limit, and both code edges.
# Command selection can finish with only the frozen arm and observed digital fields.
# The lower ADC2 endpoint is legal and the upper endpoint is excluded.
# -------------------------------------------------------------------------
def test_nearest_command_respects_search_radius_and_code_edges() -> None:
    """Known gain shifts drive an interior winner, a radius limit, and both code edges."""
    coarse = np.array([20, 25, 0, 510], dtype=np.uint16)
    frozen = _arm((0.875, 1.125))
    result, _ = frozen.select(coarse, fixed_ab_plan(len(coarse)), _phases(len(coarse)))
    # Predicted targets in threshold steps are 17.9375, 28.6875, 0.4375, and 574.3125.
    np.testing.assert_array_equal(result.codes, [18, 28, 0, 511])


def test_tone_selects_before_accessing_physical_holdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Command selection can finish with only the frozen arm and observed digital fields."""
    frozen = _arm((0.875, 1.125))
    coarse = np.array([20, 25], dtype=np.uint16)
    plan = fixed_ab_plan(len(coarse))
    phases = _phases(len(coarse))
    direct, correction = frozen.table.lookup(coarse, plan, phases)
    arm = "fixed_ab_lms_only"
    prefix = arm + "_holdout_"
    observed = {
        prefix + "coarse_code": coarse,
        prefix + "plan": plan,
        prefix + "horizontal": phases[0],
        prefix + "vertical": phases[1],
        prefix + "main_mask": direct.main,
        prefix + "sub_mask": direct.sub,
        prefix + "correction_q32": correction,
    }
    selected = False
    original_select = study.FrozenArm.select

    def observe_selection(self, *args):
        nonlocal selected
        result = original_select(self, *args)
        np.testing.assert_array_equal(result[0].codes, [18, 28])
        selected = True
        return result

    class ReachedPhysicalHoldoutError(Exception):
        pass

    class HoldoutProbe:
        def __getitem__(self, key):
            if key == "reference_holdout_v":
                assert selected, "the selector accessed the true input before returning a command"
                raise ReachedPhysicalHoldoutError
            return observed[key]

    monkeypatch.setattr(study.FrozenArm, "select", observe_selection)
    # The physical pool and ADC2 data are deliberately unavailable before this boundary.
    with pytest.raises(ReachedPhysicalHoldoutError):
        study._tone(
            HoldoutProbe(),
            arm,
            object(),
            study.ExperimentConfig(),
            study.CalibrationConfig(),
            frozen,
            0.01953125,
        )


@pytest.mark.parametrize("overload", [False, True])
def test_spectral_evidence_separates_overload_diagnostics(
    monkeypatch: pytest.MonkeyPatch, overload: bool
) -> None:
    """The lower ADC2 endpoint is legal and the upper endpoint is excluded."""
    monkeypatch.setattr(
        study, "calibration_metrics", lambda values, reference, experiment: {"samples": len(values)}
    )
    half = 0.125
    upper_valid = np.nextafter(half, -np.inf)
    lower = np.nextafter(-half, -np.inf) if overload else -half
    upper = half if overload else upper_valid
    report = study._spectral_evidence(
        np.array([lower, upper]),
        np.zeros(2),
        np.full(2, 5.0),
        np.ones(2),
        study.ExperimentConfig(),
        12,
        half,
    )
    assert report["below_range"] == report["at_or_above_range"] == int(overload)
    assert report["status"] == ("adc2_overload" if overload else "no_adc2_overload")
    rejected = "valid_capture" if overload else "saturating_adc2_diagnostic"
    accepted = "saturating_adc2_diagnostic" if overload else "valid_capture"
    assert report[rejected] is None
    assert report[accepted]["after_20bit_export"] == {"samples": 2}
