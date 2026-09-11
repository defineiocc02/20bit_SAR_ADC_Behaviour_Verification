"""Behaviour contracts from the 2026-09-11 external review of v7.0.0.

Distinct from ``test_audit_findings.py``: that file pins findings against a
*frozen baseline* (v6.1) and asserts they stay fixed. This file pins findings
against **the current tree** — it encodes what an independent reviewer said the
repository must be able to demonstrate, and it deliberately distinguishes:

* contracts that HOLD today (plain assertions), and
* contracts that are demonstrably **not yet** satisfied, which are marked
  ``xfail(strict=True)`` so that the defect is visible in the test report and
  the marker is *forced* off the moment someone fixes it (a strict xfail turns
  into a failure on XPASS — you cannot leave a stale marker behind).

Motivation — the review's central charge was not "the code is wrong" but
*"the fix landed in a helper and a test, not in the path that produces the
result"*. A test that exercises the helper proves the helper. The tests below
therefore always go through the real entry points (``run_sim_split``,
``run_pipeline``, ``audit_provenance``, ``run_all``'s gate rule) rather than
through the unit under discussion.

Finding index. The ``R`` labels below are **local to this file and stable**
(they appear in the ``xfail`` reasons, so renumbering them would invalidate
those markers). They are *not* the same numbering as
``docs/review_response_2026-09-11.md``, which follows the reviewer's own chapter
order; the third column gives the corresponding ID there so the two documents
can be read side by side.

    local  adjudication  contract                              test class
    R1     R1            sample ownership across cycles        TestR1SampleOwnership
    R2     R2            the physical pool is on the main path TestR2PhysicalPoolOnMainPath
    R3     R2 / R2b      provenance sees the active fit        TestR3ProvenanceSeesTheActiveFit
    R4     R6            config takes effect at the runner     TestR4ConfigTakesEffectAtTheRunner
    R5     R5            numeric FAIL fails the command        TestR5HardGateBindsToExitCode
    R6     R7            sub-corner flicker power is present   TestR6FlickerDriftBackfill

The adjudication doc's R3 (the ``7 + 2 = 9`` wording), R4 (KTC as an ideal
digital observation bound) and R8 (an untrue ADR 0005 claim) have **no contract
test here**: none of them is a testable behaviour contract — they are wording,
architecture scope, and documentation, and they are tracked as such in that
document.
"""

from __future__ import annotations

import inspect
import pathlib

import numpy as np
import pytest

from adi_model import Config, audit_provenance, resolve_ra_noise, run_sim_split, sine_input
from adi_model.acceptance import hard_failures
from adi_model.pipeline import run_pipeline
from adi_model.ra import flicker_series
from adi_model.scheduler import Scheduler, ShuffledScheduler

N_SMALL = 2048


def _coherent(fs: float, n: int, cycles: int = 97) -> float:
    """Coherent input frequency for an ``n``-sample record at ``fs``.

    Args:
        fs: Sampling rate [Hz].
        n: Record length [samples].
        cycles: Integer number of signal cycles in the record.

    Returns:
        Input frequency [Hz] that lands exactly on an FFT bin.
    """
    return fs * cycles / n


def _lean_cfg(**over: object) -> Config:
    """A fast, deterministic configuration with the slow mechanisms disabled.

    Args:
        **over: Field overrides applied on top of the lean base.

    Returns:
        A :class:`Config` suitable for short record-length contract tests.
    """
    base: dict[str, object] = {
        "dac_arch": "split",
        "dither_mode": "off",
        "ktc_enable": False,
        "mismatch_enable": False,
        "enable_sampling_noise": False,
        "ra_enable_noise": False,
        "dyn_input_settling": False,
        "dyn_ref_settling": False,
        "dyn_crosstalk": False,
        "sadc_mismatch_enable": False,
        "sadc_offset": 0.0,
        "sadc_rdac_gain_mismatch": 0.0,
    }
    base.update(over)
    return Config(**base)  # type: ignore[arg-type]


# ===========================================================================
# R1 — a converting slice must hold a charge it actually acquired
# ===========================================================================
class TestR1SampleOwnership:
    """The review's headline finding.

    ``conv[n] ∩ acq[n] = ∅`` only proves the two groups do not collide *within*
    one cycle. Physical sample ownership needs ``conv[n] = acq[n-1]``: a
    capacitor cannot convert a charge it never sampled.

    Both statements are checked separately, because conflating them is exactly
    how v6.1 shipped a scheduler that passed its own invariant while ~55% of
    conversions used the wrong capacitors.
    """

    def test_default_scheduler_preserves_sample_ownership(self):
        """The default ping-pong scheduler must convert the group it acquired.

        This is the path ``run_pipeline`` takes when no scheduler is injected,
        so it is the one that produces the committed reference results.
        """
        cfg = Config()
        n = 4096
        conv, acq = Scheduler(cfg).reserve_dual(n, np.random.default_rng(20260911))
        overlap = max(len(set(conv[i]) & set(acq[i])) for i in range(n))
        assert overlap == 0, f"within-cycle collision: {overlap} slices"

        full = sum(1 for i in range(1, n) if set(conv[i]) == set(acq[i - 1]))
        assert full == n - 1, (
            f"only {full}/{n - 1} conversions used the previously acquired group; "
            "within-cycle disjointness is necessary but not sufficient"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="Open defect (review R1): ShuffledScheduler redraws an independent "
        "permutation per cycle, so conv[n] != acq[n-1] on every transition "
        "(measured 0/8191). PhysicalSlicePool.shuffle_causal fixes the policy "
        "but is not wired into any runner. Remove this marker when the main "
        "path uses a causal schedule.",
    )
    def test_shuffled_scheduler_preserves_sample_ownership(self):
        """The shuffled scheduler must also honour sample ownership.

        Expected to FAIL until the main path adopts a causal shuffle. The
        measured coverage (~3.55/8, versus E|C∩A| = 8·8/18 ≈ 3.556 for two
        independent 8-of-18 draws) is the fingerprint of an *independent* draw,
        which is why this cannot be dismissed as an unlucky seed.
        """
        cfg = Config()
        n = 8192
        conv, acq = ShuffledScheduler(cfg, np.random.default_rng(20260911)).reserve_dual(n)
        full = sum(1 for i in range(1, n) if set(conv[i]) == set(acq[i - 1]))
        assert full == n - 1, f"sample ownership held for only {full}/{n - 1} transitions"


# ===========================================================================
# R2 — the physical pool must be on the path that produces results
# ===========================================================================
class TestR2PhysicalPoolOnMainPath:
    """A fix that lives only in a helper is not a system-level fix."""

    @pytest.mark.xfail(
        strict=True,
        reason="Open defect (review R2): PhysicalSlicePool is referenced by "
        "__init__ and by two test modules but by NO runner (pipeline.py, "
        "sim_split.py, sim.py) and no experiment. ADR 0005 nonetheless states "
        "that both signal chains obtain their physical quantities 'from the "
        "same pool'. Remove this marker when the pool is wired in.",
    )
    def test_runners_use_the_physical_slice_pool(self):
        """Both signal chains must obtain the slice selection from one pool."""
        for mod in ("pipeline.py", "sim_split.py"):
            src = inspect.getsource(__import__(f"adi_model.{mod[:-3]}", fromlist=["_"]))
            assert "PhysicalSlicePool" in src, f"{mod} does not use PhysicalSlicePool"

    def test_the_dac_error_does_not_see_the_selection(self):
        """Pin the *current* coupling state of the main path.

        ``SplitDAC.evaluate_physical`` takes ``(k_eq, sid)`` and no conversion
        group, so in the main path the DAC error cannot depend on which 8 of 18
        slices converted. This test documents that coupling as absent; it is
        written to fail (and be rewritten) when the argument is added.
        """
        from adi_model.dac_arch import SplitDAC

        params = list(inspect.signature(SplitDAC.evaluate_physical).parameters)
        assert params == ["self", "k_eq", "sid"], (
            "evaluate_physical gained a parameter — if it is now the conversion "
            "group, the slice selection reaches the DAC error and this test "
            "should assert that it does"
        )


# ===========================================================================
# R3 — provenance must see fitted parameters that are actually in use
# ===========================================================================
class TestR3ProvenanceSeesTheActiveFit:
    """``None`` means "a resolver supplies the value", not "switched off"."""

    def test_default_config_raises_the_fit_into_fitted_in_use(self):
        """The default RA-noise fit must appear in the report.

        ``ra_out_noise_rms=None`` selects ``resolve_ra_noise``, so the fit is
        active; filtering fitted parameters on ``value is not None`` hid exactly
        the number a reviewer needs to see first.
        """
        cfg = Config()
        assert resolve_ra_noise(cfg) > 0.0, "precondition: the resolver is active"
        report = audit_provenance(cfg)
        assert (
            "ra_out_noise_rms" in report["fitted_in_use"]
        ), "the active RA-noise fit is missing from fitted_in_use"
        assert report["resolved_sentinels"], "the resolved value must be reported"

    def test_overriding_a_disclosed_field_is_reported(self):
        """A changed value must not keep claiming its original source.

        ``fs`` is graded DISCLOSED with source "[00] abstract: 40 MS/s". Setting
        ``fs=80e6`` leaves that source string untouched, so the report must
        flag the field separately instead of quoting a source that no longer
        describes the number in use.
        """
        assert "fs" not in audit_provenance(Config())["overridden"]
        report = audit_provenance(Config(fs=80e6))
        assert "fs" in report["overridden"], (
            "fs=80e6 still carries a source that says 40 MS/s, and the report "
            "did not flag the override"
        )


# ===========================================================================
# R4 — a declared config field must take effect where it is declared to
# ===========================================================================
class TestR4ConfigTakesEffectAtTheRunner:
    """Test the real entry points, not the method that implements the option."""

    @pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
    def test_ra_gain_model_fixed_reaches_both_runners(self, runner):
        """``ra_gain_model="fixed"`` must change the output of both chains.

        ``ResidueAmplifier.gain_vector`` implemented the branch, but both
        runners built the gain vector inline from capacitor ratios and passed it
        explicitly, bypassing the option entirely — so the fixed-gain control
        experiment silently never ran. ``gain_error`` is set so that the two
        modes differ even with mismatch disabled.
        """
        cfg_charge = _lean_cfg(ra_gain_model="charge")
        cfg_fixed = _lean_cfg(ra_gain_model="fixed", gain_error=0.01)
        inp = sine_input(0.5 * cfg_charge.v_fs, _coherent(cfg_charge.fs, N_SMALL))

        r_charge = runner(cfg_charge, inp, N_SMALL, rng=np.random.default_rng(7))
        r_fixed = runner(cfg_fixed, inp, N_SMALL, rng=np.random.default_rng(7))

        assert r_fixed.g_vec.mean() == pytest.approx(cfg_fixed.g_actual, rel=1e-12)
        assert not np.array_equal(r_charge.out, r_fixed.out), (
            f"{runner.__name__} ignores ra_gain_model — the fixed-gain control "
            "does not reach the output"
        )

    def test_unknown_ra_gain_model_is_rejected(self):
        """A typo must be rejected, not silently degraded to the default."""
        key = "RA 增益口径 ra_gain_model ∈ {charge, fixed}"
        checks = _lean_cfg(ra_gain_model="Fixd").validate(verbose=False)
        assert key in checks
        assert checks[key][2] is False, "an unknown gain model passed validation"
        assert _lean_cfg(ra_gain_model="charge").validate(verbose=False)[key][2] is True


# ===========================================================================
# R5 — a numeric FAIL must fail the command
# ===========================================================================
class TestR5HardGateBindsToExitCode:
    """Determinism is not correctness: a green CI must mean the checks passed."""

    def test_pass_records_and_summaries_are_collected(self):
        """Acceptance records must be found under both shapes used by run_all."""
        results = {
            "pipeline": {"PASS": False},
            "flicker": {"PASS": True},
            "s12_summary": {"a": True, "b": False},
        }
        assert hard_failures(results) == ["pipeline.PASS", "s12_summary.b"]

    def test_non_acceptance_dicts_are_ignored(self):
        """Counter-example rows and switch values are not acceptance failures."""
        results = {
            "calibration_control": {"uncalibrated_sndr_db": -6.0, "dem_enable": False},
            "s7_dem_off": {"SNDR_dB": 90.0},
        }
        assert hard_failures(results) == []

    def test_run_all_binds_the_rule_to_a_nonzero_exit(self):
        """``run_all`` must consult the rule and exit non-zero — statically."""
        src = pathlib.Path("tools/run_all.py").read_text(encoding="utf-8")
        assert "hard_failures(R)" in src, "run_all does not evaluate the hard gate"
        assert "sys.exit(1)" in src, "run_all reports FAIL but never exits non-zero"


# ===========================================================================
# R6 — sub-corner flicker power must reach a short record
# ===========================================================================
class TestR6FlickerDriftBackfill:
    """The function documents a back-fill it can never reach."""

    @pytest.mark.xfail(
        strict=True,
        reason="Open defect (review R6 here; R7 in docs/review_response_2026-09-11.md): the guard is `if f_corner <= f_min or "
        "f_min <= f_low: return x`, so the branch is skipped precisely when the "
        "corner sits below the record's resolution limit — the only case the "
        "back-fill was written for. Measured: 0/32768 non-zero samples for "
        "fs=40 MHz, n=32768, fc=40 Hz, t_obs=10 s. Remove this marker when the "
        "guard is corrected.",
    )
    def test_short_record_contains_the_unresolved_low_frequency_power(self):
        """A 40 Hz corner in a 10 s record must not produce an all-zero series.

        ``f_min = fs/n = 1220.7 Hz`` exceeds the corner, so the truncated
        spectrum is empty; the power below ``f_min`` is unresolvable rather than
        absent, and the drift model exists to represent it.
        """
        x = flicker_series(
            32768,
            40e6,
            40.0,
            1e-6,
            np.random.default_rng(0),
            t_obs=10.0,
            include_drift=True,
        )
        assert np.count_nonzero(x) > 0, "the drift back-fill produced all zeros"
