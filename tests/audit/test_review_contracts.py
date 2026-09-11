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
those markers). They are *not* the reviewer's numbering; each group below is
indexed against the adjudication document for its own round, so the two can be
read side by side.

Round 1 — second external review of v7.0.0, indexed against
``docs/review_response_2026-09-11.md``:

    local  there      contract                              test class
    R1     R1         sample ownership across cycles        TestR1SampleOwnership
    R2     R2         the physical pool is on the main path TestR2PhysicalPoolOnMainPath
    R3     R2 / R2b   provenance sees the active fit        TestR3ProvenanceSeesTheActiveFit
    R4     R6         config takes effect at the runner     TestR4ConfigTakesEffectAtTheRunner
    R5     R5         numeric FAIL fails the command        TestR5HardGateBindsToExitCode
    R6     R7         sub-corner flicker power is present   TestR6FlickerDriftBackfill

That document's R3 (the ``7 + 2 = 9`` wording), R4 (KTC as an ideal digital
observation bound) and R8 (an untrue ADR 0005 claim) have **no contract test
here**: none of them is a testable behaviour contract — they are wording,
architecture scope, and documentation, and they are tracked as such in that
document.

Round 2 — third external review, of v7.0.1; the labels continue the local
sequence, and the second column is the section of
``docs/review_response_2026-09-11b.md`` (that round's adjudication) which each
one settles:

    local  there                   contract                                test class
    R7     §2.1                    the gate sees the config self-checks     TestR7GateCoversConfigChecks
    R8     §2.4                    an overridden value cannot pass require  TestR8OverrideIsEnforced
    R9     §2.5                    the pool contract is behavioural         (inside TestR2)
    R10    §3.1  (self-found)      dem_mode is declared but read nowhere    TestR10DemModeIsInert
    R11    §2.2 + §3.3 (self-found) the one live FAIL is a registered limit  TestR11KnownLimits
    —      §2.3                    an illegal config is refused at the entry (inside TestR4)

Note that ``§3.3`` above means §3.3 **of that adjudication document**, which is
the disposition of the KTC bandwidth limit. Where an ``xfail`` reason cites
"§3.3 of the third review" it means the reviewer's own §3 item 3 (the
source-string test), which is §2.5 of the adjudication document — the two
numberings are different and are spelled out per citation.
"""

from __future__ import annotations

import inspect
import pathlib

import numpy as np
import pytest

from adi_model import (
    LEGAL_VALUES,
    Config,
    ConfigError,
    audit_provenance,
    resolve_ra_noise,
    run_sim_split,
    sine_input,
)
from adi_model.acceptance import (
    ACCEPTANCE_CONTAINERS,
    KNOWN_LIMITS,
    MIN_RECORDS,
    acceptance_records,
    gate,
    hard_failures,
)
from adi_model.pipeline import run_pipeline
from adi_model.provenance import GradingError, SourceGrade, annotate_config
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
    """A fix that lives only in a helper is not a system-level fix.

    The first version of this class asserted ``assert "PhysicalSlicePool" in
    inspect.getsource(...)`` — a check that an unused import or even a *comment*
    would satisfy. The reviewer's objection is exact: that is not evidence that
    the pool participates in producing a result. A source scan can only ever pin
    the text; the contract has to be about behaviour. The behavioural contract
    is below and still fails; the control above it proves the contract is not
    vacuous.
    """

    def test_the_selection_information_exists_and_is_causal(self):
        """Positive control: per-sample slice selections are already available.

        Without this control, the xfail below could pass for the wrong reason —
        e.g. if the information never existed at all, the fix would have to
        *invent* it rather than *bind* it. The scheduler already hands back
        explicit index arrays for both chains, and they satisfy the cross-cycle
        ownership relation, so what is missing is the binding, not the data.
        """
        cfg = _lean_cfg()
        sched = Scheduler(cfg)
        conv, acq = sched.reserve_dual(64, np.random.default_rng(0))
        conv_a, acq_a = np.asarray(conv), np.asarray(acq)
        assert conv_a.shape == (64, 8), f"conversion groups are not per-sample: {conv_a.shape}"
        assert acq_a.shape == (64, 8), f"acquisition groups are not per-sample: {acq_a.shape}"
        assert all(
            set(conv_a[i]) == set(acq_a[i - 1]) for i in range(1, 64)
        ), "precondition failed: even the default scheduler does not preserve ownership"

    @pytest.mark.xfail(
        strict=True,
        reason="Open defect (review R2; re-raised as T3, §2.5 of "
        "docs/review_response_2026-09-11b.md): "
        "PhysicalSlicePool is referenced by __init__ and by two test modules but "
        "by NO runner (pipeline.py, sim_split.py, sim.py) and no experiment. "
        "ADR 0005 nonetheless states that both signal chains obtain their "
        "physical quantities 'from the same pool'. This contract is behavioural "
        "(a sample must be traceable to the slices that converted it); it "
        "replaces a source-string scan that an unused import or a comment would "
        "have satisfied. Remove this marker when the pool is wired in.",
    )
    def test_an_output_sample_is_traceable_to_the_slices_that_converted_it(self):
        """A result must be attributable to the physical capacitors behind it.

        The reviewer's acceptance question, adopted verbatim: perturb the
        capacitors that actually convert a sample, and the internal charge, DAC
        weight or residue gain for *that sample* must move accordingly; the
        converted sample must come from the charge those capacitors hold. Note
        the final output is deliberately **not** required to change — a mismatch
        can cancel through the charge relation or the digital reconstruction —
        so the contract is on the internal quantities and their traceability.
        """
        cfg = _lean_cfg()
        res = run_sim_split(cfg, sine_input(0.5 * cfg.v_fs, _coherent(cfg.fs, N_SMALL)), N_SMALL)
        missing = [
            attr
            for attr in ("sample_id", "conv_slice_ids", "acq_slice_ids", "held_sample")
            if not hasattr(res, attr)
        ]
        assert not missing, (
            "SimResult cannot answer 'which physical slices produced this sample?': "
            f"missing {missing}. Without this, the DAC error, the residue gain and "
            "the converted sample cannot be tied to the same capacitor set."
        )

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
        """A typo must be rejected, not silently degraded to the default.

        Two distinct claims, and the second one is the one that matters: a
        ``validate()`` table saying FAIL is *reporting*, while refusing to run
        is *enforcement*. The third review noted that only the first existed —
        ``gain_vector`` still fell through to the capacitor-ratio path for any
        non-``"fixed"`` string, so an illegal config still produced numbers.
        """
        key = "RA 增益口径 ra_gain_model ∈ {charge, fixed}"
        checks = _lean_cfg(ra_gain_model="Fixd").validate(verbose=False)
        assert key in checks
        assert checks[key][2] is False, "an unknown gain model passed validation"
        assert _lean_cfg(ra_gain_model="charge").validate(verbose=False)[key][2] is True

    @pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
    def test_an_illegal_config_is_refused_at_the_entry(self, runner):
        """The runner must refuse, not report-and-continue.

        The reviewer's contract: a config must take effect where it is declared,
        or be explicitly refused. Refusal has to happen *at the entry*, because
        that is the only place a caller cannot get a plausible-looking result
        from a configuration that means nothing.
        """
        cfg = _lean_cfg(ra_gain_model="Fixd")
        inp = sine_input(0.5 * cfg.v_fs, _coherent(cfg.fs, N_SMALL))
        with pytest.raises(ConfigError) as ei:
            runner(cfg, inp, 64)
        assert "ra_gain_model" in str(
            ei.value
        ), f"{runner.__name__} refused the config but did not say which field was wrong"

    def test_an_illegal_enum_is_refused_for_every_declared_field(self):
        """Every field in ``LEGAL_VALUES`` must be enforced, not just the one
        that happened to have a validate() record.
        """
        for field_name, allowed in LEGAL_VALUES.items():
            bad = _lean_cfg(**{field_name: "__no_such_value__"})
            violations = bad.legality_violations()
            assert any(field_name in v for v in violations), (
                f"{field_name} accepts an undeclared value silently; "
                f"declared options are {allowed}"
            )


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
        """``run_all`` must consult the rule and exit non-zero — statically.

        This one *is* a source check, and deliberately so: ``run_all`` is a
        script whose import runs every experiment, so it cannot be exercised in
        a unit test. The split is intentional — the rule itself is tested
        behaviourally above (and in :class:`TestR7GateCoversConfigChecks`), and
        only the two-line binding is pinned by text here.
        """
        src = pathlib.Path("tools/run_all.py").read_text(encoding="utf-8")
        assert "gate(R)" in src, "run_all does not evaluate the hard gate"
        assert (
            "sys.exit(0 if _GATE.ok() else 1)" in src
        ), "run_all reports FAIL but never binds it to the exit code"


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


# ===========================================================================
# R7 — the gate must see the configuration self-checks
# ===========================================================================
class TestR7GateCoversConfigChecks:
    """A gate that skips a whole table is a gate with a hole in it.

    The first version enumerated only ``results[name]["PASS"]`` and
    ``results[name + "_summary"]``. ``run_all`` also writes
    ``results["validate"][check]["PASS"]`` and
    ``results["validate_ktc"][check]["PASS"]``, and *neither* was collected —
    so a failing configuration check could not turn the command red. The
    counterexample below is the reviewer's, reproduced against the real rule.
    """

    def test_a_failing_config_check_is_collected(self):
        """A FAIL inside the config self-check tables must be a hard failure."""
        results = {
            "validate": {"invalid_config": {"PASS": False}},
            "validate_ktc": {"bw": {"PASS": False}},
        }
        assert hard_failures(results) == ["validate.invalid_config", "validate_ktc.bw"]

    def test_the_explicit_lists_are_the_ones_run_all_actually_writes(self):
        """The container list must not be a guess about someone else's schema."""
        assert set(ACCEPTANCE_CONTAINERS) <= {"validate", "validate_ktc"}
        src = pathlib.Path("tools/run_all.py").read_text(encoding="utf-8")
        for container in ACCEPTANCE_CONTAINERS:
            assert (
                f'R["{container}"]' in src
            ), f"the rule collects {container!r} but run_all.py never writes it"

    def test_collecting_a_container_must_not_swallow_switch_values(self):
        """Widening the scope must not re-introduce "any False is a failure"."""
        results = {
            "validate": {"margin_v": {"PASS": True, "actual": None}},
            "calibration_control": {"dem_enable": False, "uncal_vs_cal_db": -6.0},
        }
        assert hard_failures(results) == []


# ===========================================================================
# R8 — an overridden value must not pass a DISCLOSED check
# ===========================================================================
class TestR8OverrideIsEnforced:
    """Reporting an override and enforcing it are different guarantees.

    v7.0.1 added ``audit_provenance(...)["overridden"]``, which a *reader* of
    the report can act on. But ``annotate_config`` still attached the grade to
    the field name, so
    ``annotate_config(Config(fs=80e6))["fs"].require(DISCLOSED)`` returned
    ``8e7`` — a downstream caller could obtain a hand-edited number under the
    paper's authority. The grade must follow the value, not the name.
    """

    def test_an_overridden_value_is_not_quotable_as_disclosed(self):
        ann = annotate_config(Config(fs=80e6))
        assert ann["fs"].overridden is True, "the override is not recorded on the Graded"
        with pytest.raises(GradingError) as ei:
            ann["fs"].require(SourceGrade.DISCLOSED)
        assert "differs from the stock default" in str(
            ei.value
        ), "refused, but without saying why the declared source no longer applies"

    def test_the_unmodified_field_is_still_quotable(self):
        """The enforcement must not turn into "nothing is ever quotable"."""
        assert Config().fs == 40e6
        ann = annotate_config(Config())
        assert ann["fs"].overridden is False
        assert ann["fs"].require(SourceGrade.DISCLOSED) == 40e6

    def test_effective_grade_demotes_only_overridden_disclosed_or_derived(self):
        ann_ovr = annotate_config(Config(fs=80e6))
        ann_ok = annotate_config(Config())
        assert ann_ovr["fs"].grade is SourceGrade.DISCLOSED
        assert ann_ovr["fs"].effective_grade is SourceGrade.ASSUMED
        assert ann_ok["fs"].effective_grade is SourceGrade.DISCLOSED
        # 非披露/派生字段的等级不因覆盖而改变（例如 pdk_sigma_est_ppm 本就是假设）
        assumed = annotate_config(Config(pdk_sigma_est_ppm=2000.0))["pdk_sigma_est_ppm"]
        assert assumed.grade is SourceGrade.ASSUMED
        assert assumed.effective_grade is SourceGrade.ASSUMED

    def test_map_preserves_the_override_flag(self):
        """A derived quantity must not launder an overridden ancestor."""
        ann = annotate_config(Config(fs=80e6))
        derived = ann["fs"].map(lambda v: v / 1e6)
        assert derived.overridden is True
        with pytest.raises(GradingError):
            derived.require(SourceGrade.DISCLOSED)


# ===========================================================================
# R10 — a declared field that is read nowhere
# ===========================================================================
class TestR10DemModeIsInert:
    """``dem_mode`` is declared, documented, and read by nobody.

    Found while building the legality table: the field is present in ``Config``
    and in ``PARAM_GRADES``, but no module compares against it. Which scheduling
    behaviour you get is decided by *which ``Scheduler`` subclass the caller
    instantiates* (``Scheduler`` vs ``ShuffledScheduler``). This is the same
    class of defect as ``ra_gain_model`` in v7.0.0 — a name that promises a
    mechanism that is not wired. Pin it so the pin fails when it is wired.
    """

    def test_dem_mode_does_not_reach_the_runner(self):
        """Changing ``dem_mode`` must currently change nothing — a pinned defect."""
        cfg_a = _lean_cfg(dem_mode="rotate")
        cfg_b = _lean_cfg(dem_mode="permute")
        inp = sine_input(0.5 * cfg_a.v_fs, _coherent(cfg_a.fs, N_SMALL))
        out_a = run_sim_split(cfg_a, inp, N_SMALL, rng=np.random.default_rng(11)).out
        out_b = run_sim_split(cfg_b, inp, N_SMALL, rng=np.random.default_rng(11)).out
        assert np.array_equal(out_a, out_b), (
            "dem_mode now changes the output — it has been wired in, so this "
            "test must be rewritten to assert that it takes effect"
        )


# ===========================================================================
# R11 — the published reference output must satisfy the gate
# ===========================================================================
class TestR11KnownLimits:
    """The shipped ``results.json`` is itself subject to the rule.

    Leaving this unchecked is how a gate silently drifts away from the artefact
    it is supposed to be gating. Two things are pinned: the reference output
    passes, and the known-limits ledger is neither stale nor unexplained.
    """

    @staticmethod
    def _reference_results() -> dict:
        """The published reference output, as written by ``run_all.py``."""
        path = pathlib.Path("tools/results/results.json")
        assert path.exists(), "the reference results.json is missing"
        import json

        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_shipped_results_pass_the_gate(self):
        verdict = gate(self._reference_results())
        assert (
            verdict.failures == []
        ), f"the shipped results.json fails the gate: {verdict.failures}"
        assert (
            verdict.stale_exemptions == []
        ), f"the known-limits ledger is stale: {verdict.stale_exemptions}"
        assert verdict.ok(), "the gate rejects the shipped reference output"

    def test_the_ledger_is_short_and_every_entry_states_a_reason(self):
        """An exemption without a reason is just a hidden failure."""
        assert KNOWN_LIMITS, "all exemptions were removed — delete the machinery too"
        for name, reason in KNOWN_LIMITS.items():
            assert len(reason.strip()) > 40, f"exemption {name!r} has no real justification"

    def test_coverage_floor_is_met_without_being_vacuous(self):
        """The floor must be tight: a record class disappearing must be caught."""
        actual = len(acceptance_records(self._reference_results()))
        assert actual == MIN_RECORDS, (
            f"MIN_RECORDS={MIN_RECORDS} but the reference output has {actual} records; "
            "update the floor deliberately, in the same commit that changes the records"
        )
