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
    R9     §2.5                    the pool contract is causal, not textual (inside TestR2)
    R10    §3.1  (self-found)      dem_mode decides the scheduler           TestR10DemModeIsWired
    R11    §2.2 + §3.3 (self-found) the one live FAIL is a registered limit  TestR11KnownLimits
    —      §2.3                    an illegal config is refused at the entry (inside TestR4)

Note that ``§3.3`` above means §3.3 **of that adjudication document**, which is
the disposition of the KTC bandwidth limit. Where an ``xfail`` reason cites
"§3.3 of the third review" it means the reviewer's own §3 item 3 (the
source-string test), which is §2.5 of the adjudication document — the two
numberings are different and are spelled out per citation.

Round 3 — fourth external review, of v7.0.2; indexed against
``docs/review_response_2026-09-11c.md``:

    local  there                   contract                                  test class
    R12    K1                      f_max is bound to the observed node       TestR12KtcMaxUsesTheObservedNode
    R13    K2                      an output sample is attributable to its   (inside TestR2)
                                   capacitors — by cause, not by attribute
    R14    K3                      dem_mode reaches every entry point        (inside TestR10)
    R15    K4                      a non-bool verdict is refused, and the    TestR15GateRefusesNonBooleanPass
                                   required set cannot be trimmed away

Round 3's K1 and K4 are also covered where they are cheapest to state: K1 in
``TestR12KtcMaxUsesTheObservedNode`` (the bound is a number, so it is pinned as
a number) and K4 in ``TestR15GateRefusesNonBooleanPass``. K3's
*partial* state — bound at one entry, inert at two — is an ``xfail(strict)``
inside ``TestR10DemModeIsWired``; see that class for why it is not closed here.

Round 4 — fifth external review, of v7.0.4; indexed against
``docs/review_response_2026-09-11d.md``:

    local  there    contract                                    test class
    R16    §4.2     the DEM permutation space is 64, not 512    TestR16DemPermutationSpace
    R17    §5.2     the aggregate-τ "conservative" claim is     TestR17CommonModeTau
                    qualified by the common source impedance
    R18    §11      kappa_optimal with observer noise is the    TestR18KappaOptimalWithObserverNoise
                    minimiser of the *total* residual noise

R16 is a **passing** pin, not an xfail: 512 digital state labels collapsing
into 64 distinct physical rotations is a fact of the current coupled-rotation
design, not a bug to be fixed by rewiring — the pin exists so that the number
cannot drift silently and so that no DEM sweep result is misread as the
paper's three-dimensional mechanism. R17 and R18 are passing pins on the
*qualified* statements this round added to the code comments.
"""

from __future__ import annotations

import inspect
import math
import pathlib

import numpy as np
import pytest

from adi_model import (
    LEGAL_VALUES,
    Config,
    ConfigError,
    SplitDAC,
    audit_provenance,
    build_split_chip,
    resolve_ra_noise,
    run_sim_split,
    sine_input,
)
from adi_model.acceptance import (
    ACCEPTANCE_CONTAINERS,
    KNOWN_LIMITS,
    MIN_RECORDS,
    REQUIRED_RECORDS,
    acceptance_records,
    gate,
    hard_failures,
)
from adi_model.noise_phase import kappa_optimal, sigma_res_analytic
from adi_model.pipeline import run_pipeline
from adi_model.provenance import GradingError, SourceGrade, annotate_config
from adi_model.ra import flicker_series
from adi_model.scheduler import Scheduler, ShuffledScheduler, make_scheduler
from adi_model.slice_pool import PhysicalSlicePool

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

    Two earlier versions of this class were rejected, and the reasons bound the
    shape of what replaced them.

    The first asserted ``assert "PhysicalSlicePool" in inspect.getsource(...)``:
    an unused import, or even a comment, satisfies a text scan. The second
    asserted that the result object carried four attribute *names*
    (``hasattr(res, "sample_id")`` and friends). That is a statement about the
    shape of an object, not about a physical cause — an attribute can be added
    and filled with a constant, and the assertion still passes.

    The contract is therefore stated as a **causal relation**: perturb the
    capacitors of the slices that converted a sample, and the internal quantity
    reported for *that* sample must move — while the samples whose conversion
    group does not contain those slices must stay put. The second half is what
    makes the relation causal rather than merely sensitive: a change applied to
    every sample alike would satisfy the first half on its own.

    Two positive controls sit above the contract. They pass today, and they
    exist so the ``xfail`` cannot succeed for the wrong reason: they establish
    that the causal relation is well defined and sharp *on the pool*, so what is
    missing in the runner is the binding, not the physics.
    """

    def test_the_selection_information_exists_and_is_causal(self):
        """Control 1: per-sample slice selections are already on hand.

        Without this control the ``xfail`` below could pass for the wrong
        reason — if the information never existed, the fix would have to
        *invent* it rather than *bind* it. The scheduler already hands back
        explicit index arrays for both chains, and they satisfy the cross-cycle
        ownership relation.
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

    def test_the_pool_ties_a_slice_to_exactly_the_samples_that_use_it(self):
        """Control 2: the causal relation is well defined, and it is sharp.

        Perturb the capacitors of one slice; the converting capacitance of
        exactly the samples whose conversion group contains that slice must
        change — no more and no fewer. Had this control been weaker ("the number
        moves somehow"), the contract below could be satisfied by a global
        sensitivity, and it would prove nothing about attribution.
        """
        cfg = _lean_cfg()
        pool = PhysicalSlicePool(cfg, np.random.default_rng(2))
        # Cycle 0 has not acquired anything yet, so its group is not a physical
        # conversion; drop it, as the pool's own planner marks it invalid.
        conv = pool.plan(48, np.random.default_rng(3)).conv[1:]
        before = pool.signal_capacitance(conv)

        victim = 0
        pool.unit_caps[victim] *= 1.10
        pool.c_slice_total[victim] = pool.unit_caps[victim].sum()
        after = pool.signal_capacitance(conv)

        uses = np.array([victim in group for group in conv])
        assert (
            uses.any() and not uses.all()
        ), "degenerate control: slice 0 either converts every sample or none"
        assert np.all(before[uses] != after[uses]), "a user of slice 0 did not move"
        assert np.all(
            before[~uses] == after[~uses]
        ), "a non-user of slice 0 moved — the coupling is not per-slice"

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

    @pytest.mark.xfail(
        strict=True,
        reason="Open defect (review K2, local R13; the R2/R9 item of the earlier "
        "rounds re-raised, §2.5 of docs/review_response_2026-09-11b.md): "
        "PhysicalSlicePool is referenced by __init__ and by two test modules but "
        "by NO runner (pipeline.py, sim_split.py, sim.py) and no experiment, "
        "while ADR 0005 states that both signal chains obtain their physical "
        "quantities 'from the same pool'. The contract is causal — perturbing "
        "the capacitors that converted sample i must move the internal quantity "
        "reported for sample i, and only for the samples that used them. Today "
        "the runner neither accepts a pool nor records which slices converted a "
        "sample, so the relation cannot even be stated. Remove this marker when "
        "the pool is wired in.",
    )
    def test_perturbing_the_capacitors_behind_a_sample_moves_that_sample(self):
        """The reviewer's acceptance question, executed rather than paraphrased.

        Perturb the capacitors of the slices that actually converted sample
        ``i``: the internal charge, DAC weight or residue gain reported *for
        sample ``i``* must move accordingly, and the samples that did not use
        those slices must not. The final output is deliberately **not** required
        to change — a mismatch can cancel through the charge relation or the
        digital reconstruction — so the contract is on the internal quantities
        and on their attribution.

        The body is written against the target interface so that wiring the pool
        makes it *mean* something rather than merely turning green: the
        attribution step is the only reason it does not run end to end today.
        """
        cfg = _lean_cfg()
        x = sine_input(0.5 * cfg.v_fs, _coherent(cfg.fs, N_SMALL))
        res = run_sim_split(cfg, x, N_SMALL)

        # Step 1 — attribution. Name the capacitors behind each sample. Without
        # this the perturbation in step 2 cannot be aimed at anything, so the
        # missing link is reported by name rather than as an AttributeError.
        trace = {
            name: getattr(res, name, None)
            for name in ("conv_slice_ids", "acq_slice_ids", "held_sample", "pool")
        }
        missing = [name for name, value in trace.items() if value is None]
        assert not missing, (
            "SimResult cannot attribute an output to physical capacitors: "
            f"{missing} absent. With no per-sample slice trace there is no way to "
            "name the 8-of-18 capacitors that converted sample i, so e_dac and "
            "c_active cannot be tied to the capacitor set that produced them, and "
            "a perturbation cannot be aimed."
        )

        # Step 2 — causality. Reachable only once step 1 passes: the caller can
        # hand the runner the pool it draws from, and perturbing that pool's
        # capacitors then moves e_dac for exactly the samples whose conversion
        # group contains the perturbed slices.
        pool = trace["pool"]
        conv = np.asarray(trace["conv_slice_ids"])
        victim = int(conv[1, 0])
        before = np.array(res.e_dac)
        pool.unit_caps[victim] *= 1.10
        pool.c_slice_total[victim] = pool.unit_caps[victim].sum()
        rerun = run_sim_split(cfg, x, N_SMALL, **{"pool": pool})
        after = np.array(rerun.e_dac)

        uses = np.array([victim in group for group in conv])
        assert np.any(before[uses] != after[uses]), (
            "the capacitors that converted sample i were perturbed and e_dac did "
            "not move — the pool is not on the path that produces the result"
        )
        assert np.all(before[~uses] == after[~uses]), (
            "a sample that did not use the perturbed slices moved — the coupling "
            "is not per-sample"
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
# R10 — a declared field that is read nowhere (round 2) / reaches one entry only (round 3)
# ===========================================================================
class TestR10DemModeIsWired:
    """``dem_mode`` must decide the mechanism, at every entry that consumes one.

    Round 2 recorded the field as *declared and read by nobody*: which scheduling
    behaviour you got was decided by which ``Scheduler`` subclass the caller
    happened to instantiate. v7.0.2 routed all three entries through
    ``make_scheduler``, so the name is now bound — and the tests below show that
    this is only half the contract.

    The binding is real at ``run_pipeline``, which consumes the per-cycle
    ``(conv, acq)`` groups from :meth:`Scheduler.reserve_dual` — that method
    *is* overridden by :class:`ShuffledScheduler`. It is **inert at**
    ``run_sim`` and ``run_sim_split``: those call ``reserve`` only, which
    ``ShuffledScheduler`` inherits unchanged from the base class, so selecting
    the shuffled scheduler there still produces ping-pong slices. ``dem_mode``
    therefore changes the mechanism at one entry out of three.

    That gap is not closed here on purpose. Making ``ShuffledScheduler.reserve``
    shuffled means deciding *which* shuffle — and the causal one
    (``conv[n] == acq[n-1]``, as in ``PhysicalSlicePool.shuffle_causal``) is
    exactly the R1 fix, which moves published stage-19 numbers. That is a
    physical main-path change and is tracked as its own decision, not smuggled
    in behind a switch that was supposed to be cosmetic. Hence the ordinary
    assertions for what holds, and one ``xfail(strict)`` for what does not.
    """

    def test_dem_mode_selects_the_scheduler_class(self):
        """The binding itself: ``rotate`` -> base, ``permute`` -> shuffled."""
        rng = np.random.default_rng(0)
        rotate = make_scheduler(_lean_cfg(dem_mode="rotate"), rng)
        permute = make_scheduler(_lean_cfg(dem_mode="permute"), rng)
        assert type(rotate) is Scheduler, f"rotate gave {type(rotate).__name__}"
        assert type(permute) is ShuffledScheduler, f"permute gave {type(permute).__name__}"

    def test_an_explicit_scheduler_that_contradicts_the_config_is_refused(self):
        """Two statements about which schedule runs is one too many."""
        cfg = _lean_cfg(dem_mode="permute")
        with pytest.raises(ConfigError, match="dem_mode"):
            run_sim_split(cfg, sine_input(0.5 * cfg.v_fs, 1e6), 64, scheduler=Scheduler(cfg))

    def test_dem_mode_changes_the_pipeline_result(self):
        """Positive evidence at the one entry that consumes the shuffle.

        ``dyn_input_settling`` is what puts the per-cycle groups into the loop
        body (pipeline.py); without it the schedule is drawn and never read, so
        the two modes agree for a reason that has nothing to do with
        ``dem_mode``. Asserting on that configuration is the difference between
        testing the binding and testing a coincidence.
        """
        inp = sine_input(0.5 * _lean_cfg().v_fs, _coherent(_lean_cfg().fs, N_SMALL))
        common = {"dyn_input_settling": True, "slice_bw_spread": 0.3}
        out_a = run_pipeline(
            _lean_cfg(dem_mode="rotate", **common),
            inp,
            N_SMALL,
            rng=np.random.default_rng(11),
        ).out
        out_b = run_pipeline(
            _lean_cfg(dem_mode="permute", **common),
            inp,
            N_SMALL,
            rng=np.random.default_rng(11),
        ).out
        assert not np.array_equal(
            np.asarray(out_a), np.asarray(out_b)
        ), "dem_mode no longer reaches the pipeline — the binding was removed"

    @pytest.mark.xfail(
        strict=True,
        reason="Half-open (review K3, local R14): dem_mode is bound at "
        "run_pipeline but inert at run_sim and run_sim_split. Those entries call "
        "Scheduler.reserve, which ShuffledScheduler does not override, so a "
        "config that says 'permute' still gets ping-pong slice groups there. "
        "Closing it requires choosing the shuffle policy — the causal one is the "
        "R1 fix and moves published stage-19 numbers, so it is a separate "
        "physical-main-path decision. Remove this marker when reserve honours "
        "the mode at all three entries.",
    )
    def test_dem_mode_changes_the_result_at_every_entry(self):
        """The switch must mean the same thing whichever entry you call."""
        base = _lean_cfg()
        x = sine_input(0.5 * base.v_fs, _coherent(base.fs, N_SMALL))
        common = {"mismatch_enable": True, "dyn_input_settling": True}
        out_a = run_sim_split(
            _lean_cfg(dem_mode="rotate", **common), x, N_SMALL, rng=np.random.default_rng(11)
        ).out
        out_b = run_sim_split(
            _lean_cfg(dem_mode="permute", **common), x, N_SMALL, rng=np.random.default_rng(11)
        ).out
        assert not np.array_equal(np.asarray(out_a), np.asarray(out_b)), (
            "run_sim_split ignores dem_mode: ShuffledScheduler inherits "
            "reserve() from Scheduler, so the selected class does not change the "
            "slice groups this entry actually uses"
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

    def test_the_ledger_is_either_empty_or_explained_in_full(self):
        """An exemption without a reason is a hidden failure. None is also fine.

        v7.0.2 carried exactly one entry, for the KTC ``f_max`` bound. That entry
        was the *consequence of a wrong-node judgement*, not of a circuit limit,
        so the correct ledger today is empty — and the previous assertion
        ("the ledger must be non-empty") had turned into a demand to keep a
        false limitation alive in order to keep a test green. The machinery is
        not deleted with the last entry, because it is the device that makes an
        exemption impossible to hide; its behaviour is pinned directly below,
        against synthetic input, instead of through production data.
        """
        for name, reason in KNOWN_LIMITS.items():
            assert len(reason.strip()) > 40, f"exemption {name!r} has no real justification"

    def test_an_exemption_moves_a_failure_out_of_failures_and_is_reported(self):
        """Exercise the ledger without requiring a real limitation to exist."""
        results = {"pipeline": {"PASS": False}}
        assert "pipeline.PASS" in gate(results).failures, "precondition: it fails"

        registry = {"pipeline.PASS": "Synthetic entry: long enough to clear the reason floor."}
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", registry)
            verdict = gate(results)
        assert verdict.failures == [], "an exempted failure still counted as a failure"
        assert verdict.exempted == ["pipeline.PASS"], "the exemption was not surfaced"
        assert (
            verdict.missing_required
        ), "precondition: the synthetic input omits every required record"
        assert not verdict.ok(), (
            "ok() must still be false — it is the conjunction of no unregistered "
            "failure, no stale exemption, and no missing required record; the "
            "absent required records are not excused by an unrelated exemption"
        )

    def test_a_stale_exemption_is_an_error_not_a_silent_no_op(self):
        """An entry that stops failing must be removed, not left to rot."""
        registry = {"pipeline.PASS": "Synthetic entry: long enough to clear the reason floor."}
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", registry)
            verdict = gate({"pipeline": {"PASS": True}})
        assert verdict.stale_exemptions == ["pipeline.PASS"]
        assert not verdict.ok(), "a stale exemption was accepted"

    def test_an_exemption_without_a_reason_is_refused(self):
        """A blank justification must raise, not pass quietly."""
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("adi_model.acceptance.KNOWN_LIMITS", {"pipeline.PASS": "   "})
            with pytest.raises(ValueError, match="no reason"):
                gate({"pipeline": {"PASS": False}})

    def test_coverage_floor_is_met_without_being_vacuous(self):
        """The floor must be tight: a record class disappearing must be caught."""
        actual = len(acceptance_records(self._reference_results()))
        assert actual == MIN_RECORDS, (
            f"MIN_RECORDS={MIN_RECORDS} but the reference output has {actual} records; "
            "update the floor deliberately, in the same commit that changes the records"
        )


# ===========================================================================
# R12 — the KTC bandwidth bound must be read at the node it constrains
# ===========================================================================
class TestR12KtcMaxUsesTheObservedNode:
    """K1: ``f_max`` was a false failure produced by reading the wrong node.

    v7.0.2 evaluated the bound against the **second stage's input range**:
    ``f_max = (adc2_v_max - G·Δ1) / (2π·v_fs·G0·Δt)``, which on the default
    configuration is 2.5465 MHz against a 5 MHz requirement, and registered
    that number in the known-limits ledger. The premise is wrong. ADR 0006
    removes the correction term in the *digital* domain
    (``quantize(v_ra) − κ·v_N``), so ``κ·v_N`` never occupies second-stage
    range regardless of its size; the quantity that is actually band-limited is
    the **observation** path, whose usable swing is ``ra_v_clip`` and whose
    observed step is scaled by ``ktc_gain_n``:

        f_max = (ra_v_clip / ktc_gain_n) / (2π · v_fs · Δt)  = 134.45 MHz

    Both numbers are computed here, from the same configuration object, so that
    the口径 difference is pinned as arithmetic rather than as prose — the defect
    *was* a number, and a test that only asserted "a check exists" would have
    passed at either node.
    """

    NAME = "KTC 观测通路摆幅上限 f_max (满幅)"

    def test_the_bound_is_the_observation_path_swing_over_the_observed_step(self):
        cfg = Config(ktc_enable=True)
        checks = cfg.validate(verbose=False)
        assert self.NAME in checks, f"the bound is missing from validate(): {sorted(checks)}"
        measured, limit, passed, unit = checks[self.NAME]
        expected = (cfg.ra_v_clip / cfg.ktc_gain_n) / (2 * math.pi * cfg.v_fs * cfg.ktc_dt())
        assert measured == pytest.approx(expected, rel=1e-12), (
            "the bound is no longer computed from the observation-path swing — "
            f"measured {measured!r}, observation-node expression {expected!r}"
        )
        assert unit == "Hz"
        assert passed and measured >= limit, (
            f"the observation-node bound fails at {measured / 1e6:.4f} MHz "
            f"(limit {limit / 1e6:.1f} MHz) — if this is intended, it belongs in "
            "acceptance.KNOWN_LIMITS with a reason and an exit condition"
        )

    def test_the_second_stage_node_would_fail_and_is_not_the_criterion(self):
        """The counterfactual, so the wrong node cannot quietly come back.

        Read at the second stage's input range, the same requirement fails by a
        factor of ~50. Pinning the counterfactual is what distinguishes "the
        bound passes" from "the bound passes *because it is read correctly*".
        """
        cfg = Config(ktc_enable=True)
        g = cfg.g_actual
        adc2_margin = min(cfg.adc2_v_max - g * cfg.delta1, -cfg.adc2_v_min)
        wrong_node = adc2_margin / (2 * math.pi * cfg.v_fs * cfg.g0 * cfg.ktc_dt())
        correct_node = (cfg.ra_v_clip / cfg.ktc_gain_n) / (2 * math.pi * cfg.v_fs * cfg.ktc_dt())
        assert wrong_node < 5e6, (
            f"the counterfactual no longer fails ({wrong_node / 1e6:.4f} MHz) — the "
            "parameters moved, so the two nodes are no longer distinguishable and "
            "this test can no longer tell them apart"
        )
        assert (
            correct_node > 10 * wrong_node
        ), "the two nodes are too close to be told apart; the pin is vacuous"
        assert self.NAME in cfg.validate(verbose=False)


# ===========================================================================
# R15 — the gate must refuse a verdict it would have to guess at
# ===========================================================================
class TestR15GateRefusesNonBooleanPass:
    """K4: ``bool("False")`` is ``True``, and that is how a FAIL became a pass.

    v7.0.2 normalised every verdict with ``bool(raw)``. A single serialisation
    change — writing ``"False"`` instead of ``False`` — therefore read as a
    pass, and the gate had no way to notice, because it had already thrown the
    type away. ``acceptance._verdict`` now refuses anything that is not a
    boolean; refusing to read is the only behaviour that cannot be silently
    wrong.

    v7.0.4 narrows the refusal by exactly one type: ``numpy.bool_``. The
    in-memory sweep builds records straight from numpy comparisons, and
    ``s10_summary.C_F->增益(电荷一致)`` was born as ``np.True_`` — a *real*
    verdict that the JSON round-trip would have turned into ``bool`` anyway.
    Refusing it did not protect anything; it broke the very sweep the gate
    exists to judge. The impostors the contract exists to catch ("False",
    ``0``, ``1.0``, ``None``) are still refused — pinned below, unchanged.
    """

    def test_a_stringified_false_is_refused_not_read_as_true(self):
        with pytest.raises(ValueError, match="not a bool"):
            acceptance_records({"validate": {"x": {"PASS": "False"}}})

    def test_every_non_boolean_type_is_refused(self):
        """The ints matter: ``bool(0)`` and ``bool(1)`` are plausible mistakes."""
        for raw in ("True", "False", 0, 1, 0.0, 1.0, None, [], {}):
            with pytest.raises(ValueError, match="not a bool"):
                acceptance_records({"pipeline": {"PASS": raw}})

    def test_a_real_boolean_is_returned_unchanged(self):
        """Control: the strictness must not reject the correct type."""
        assert acceptance_records({"pipeline": {"PASS": False}}) == {"pipeline.PASS": False}
        assert acceptance_records({"pipeline": {"PASS": True}}) == {"pipeline.PASS": True}

    def test_a_numpy_boolean_is_a_verdict_not_an_impostor(self):
        """np.bool_ is accepted and normalised, and stays False when False.

        Regression pin for the v7.0.3 CI failure: the py3.12 acceptance sweep
        died on ``s10_summary.C_F->增益(电荷一致)`` carrying ``np.True_``. The
        normalisation must be value-preserving in *both* directions — a numpy
        ``False`` must not come out as ``True`` on the far side.
        """
        np_bool = type(np.True_)
        assert acceptance_records({"pipeline": {"PASS": np.True_}}) == {"pipeline.PASS": True}
        assert acceptance_records({"pipeline": {"PASS": np_bool(False)}}) == {
            "pipeline.PASS": False
        }
        # a numpy *float* is still refused: truthiness is not a verdict
        with pytest.raises(ValueError, match="not a bool"):
            acceptance_records({"pipeline": {"PASS": np.float64(1.0)}})

    def test_the_required_set_is_enforced_by_id_not_by_count(self):
        """Deleting one required record and adding another must not pass.

        This is the reason ``REQUIRED_RECORDS`` exists next to ``MIN_RECORDS``:
        the floor counts, so it can be satisfied by a swap; the set names the
        conclusions that would otherwise lose their only evidence.
        """
        verdict = gate({"pipeline": {"PASS": True}})
        assert verdict.missing_required, "the required set is not enforced at all"
        assert not verdict.ok(), "a result missing a required record was accepted"
        assert REQUIRED_RECORDS - set(acceptance_records({"pipeline": {"PASS": True}})) == set(
            verdict.missing_required
        )


# ===========================================================================
# R16-R18 — fifth external review (v7.0.4): permutation space, τ topology,
#           optimal cancellation under observer noise
# ===========================================================================
class TestR16DemPermutationSpace:
    """§4.2: 512 digital state labels collapse into 64 physical rotations.

    ``mapper.N_DEM_STATES`` is ``8·8·8 = 512`` — a name that invites reading
    the digital state counter as three-dimensional DEM diversity. The DAC's
    rotations are both driven by the *same* ``sid``:

        om  = roll(arange(n_m), (sid·331) mod 64)
        os_ = roll(arange(n_s), (sid·173) mod 8)

    so the joint permutation is determined by the pair ((sid·331) mod 64,
    (sid·173) mod 8), which has period lcm(64, 8) = 64 — a 331 that is coprime
    to 64 and a 173 that is coprime to 8 make each rotation individually
    full-cycle, but the *joint* cycle is the lcm, not the product. Measured
    (this file, seed-free enumeration): 64 distinct joint permutations, each
    main rotation paired with exactly one sub rotation.

    This is pinned as a **passing** test on purpose. It is not a defect to be
    fixed by rewiring — choosing the real three-dimensional mechanism (8/18
    slice selection, lateral + vertical shuffling, binary-to-unary bridging)
    is an architecture decision that moves published numbers. The pin exists
    so that (a) the number cannot drift silently and (b) no DEM sweep in this
    repo can be quoted as the paper's three-dimensional benefit.
    """

    def test_the_joint_permutation_space_is_64_not_512(self):
        from adi_model.dac_arch import _A_MAIN, _A_SUB
        from adi_model.mapper import N_DEM_STATES

        cfg = _lean_cfg()
        n_m, n_s = cfg.dac_n_main, cfg.dac_n_sub
        assert N_DEM_STATES == 512, "mapper state count changed — re-derive this pin"
        assert (n_m, n_s) == (64, 8), "array sizes changed — re-derive this pin"
        joint = {((s * _A_MAIN) % n_m, (s * _A_SUB) % n_s) for s in range(N_DEM_STATES)}
        assert len(joint) == 64, (
            f"expected the 512 sid labels to collapse to 64 distinct joint "
            f"rotations (lcm(64,8)), found {len(joint)} — if this is now larger, "
            "the permutation driver changed and the DEM scope statements in "
            "docs/model_scope.md must be revisited"
        )

    def test_each_main_rotation_is_paired_with_exactly_one_sub_rotation(self):
        from adi_model.dac_arch import _A_MAIN, _A_SUB

        cfg = _lean_cfg()
        n_m, n_s = cfg.dac_n_main, cfg.dac_n_sub
        pairs: dict[int, set[int]] = {}
        for s in range(512):
            pairs.setdefault((s * _A_MAIN) % n_m, set()).add((s * _A_SUB) % n_s)
        assert all(len(v) == 1 for v in pairs.values()), (
            "the two rotations decoupled — the selection of the main rotation no "
            "longer determines the sub rotation; update the scope statements"
        )
        assert len({next(iter(v)) for v in pairs.values()}) == n_s, (
            "each sub rotation should appear exactly 64/8 = 8 times across the " "64 main rotations"
        )

    def test_the_dac_realises_the_same_space_through_its_public_interface(self):
        """End-to-end check through SplitDAC, not just the roll arithmetic.

        ``_lean_cfg`` keeps ``dem_enable=False`` (identity order), so this
        test enables DEM explicitly — the claim under test is about the DEM
        space itself.
        """
        cfg = Config(dem_enable=True, dac_arch="split")
        dac = SplitDAC(cfg, build_split_chip(cfg))
        orders = {tuple(dac.full_order(sid).tolist()) for sid in range(512)}
        assert len(orders) == 64, (
            f"SplitDAC.full_order yields {len(orders)} distinct physical orders "
            "over the 512 states — expected 64"
        )


class TestR17CommonModeTau:
    """§5.2: the aggregate τ is *not* uniformly conservative.

    The header of ``pipeline.py`` used to say the per-slice τ is "~8× smaller,
    so the aggregate single-node RC is a conservative upper bound". That is
    true only for the branch-switch term. In a star network — common source
    impedance R_s feeding N branches of (R_on + C_slice) — the common-mode
    time constant is

        τ_common = R_s·C_total + R_on·C_slice,

    so the R_s·C_load term of the aggregate formula does *not* shrink with the
    slice partition. With the reviewer's numbers (N=8, C_total = 20.5 pF,
    R_s = 30 Ω, R_on = 20 Ω): aggregate 1.025 ns, common-mode 0.666 ns (only
    1.54× smaller), all-per-slice 0.128 ns. The header now states this
    qualification; this test pins the arithmetic so the numbers in the comment
    cannot rot.
    """

    def test_the_star_network_common_mode_time_constant(self):
        n, rs, ron = 8, 30.0, 20.0
        c_total = 20.5e-12
        c_slice = c_total / n
        tau_aggregate = (rs + ron) * c_total
        tau_common = (n * rs + ron) * c_slice  # = rs·c_total + ron·c_slice
        tau_all_per_slice = (rs + ron) * c_slice
        assert tau_aggregate == pytest.approx(1.025e-9)
        assert tau_common == pytest.approx(0.666e-9, rel=1e-3)
        assert tau_all_per_slice == pytest.approx(0.128e-9, rel=1e-3)
        # the ratio that the "~8×" claim silently assumed away:
        assert tau_aggregate / tau_common < 2.0, (
            "with these source/switch numbers the aggregate bound is within 2× "
            "of the star-network common mode, not 8× — the conservative claim "
            "is qualified, as pipeline.py's header now states"
        )
        # algebraic identity the qualification rests on:
        assert (n * rs + ron) * c_slice == pytest.approx(rs * c_total + ron * c_slice)


class TestR18KappaOptimalWithObserverNoise:
    """§11: deepest cancellation ≠ minimum total noise.

    ``noise_phase.kappa_optimal`` used to return aᵀΣb / bᵀΣb — the minimiser
    of the *sampling-noise residual only*. But ``sigma_res_analytic``, the
    function that reports the final number, also charges κ²·σ_eN² for the
    observer path. Minimising one while reporting the other is an internal
    inconsistency the fifth review exposed by writing the correct joint
    optimum: κ* = aᵀΣb / (bᵀΣb + σ_eN²). ``kappa_optimal`` now takes
    ``sigma_eN`` (default 0 = historical behaviour); this pin holds it to the
    joint optimum, in both directions.
    """

    def _state(self) -> dict:
        rng = np.random.default_rng(5)
        a = rng.normal(0.0, 1.0, 6)
        b = rng.normal(0.0, 1.0, 6)
        return {"Sigma_diag": rng.uniform(0.5, 2.0, 6) ** 2, "a": a, "b": b}

    def test_with_observer_noise_it_minimises_the_total_residual(self):
        st = self._state()
        for sigma_eN in (0.0, 0.3, 1.0, 3.0):
            k = kappa_optimal(st, sigma_eN)
            # optimality: total residual at κ* must not exceed any neighbour
            grid = np.linspace(0.0, 2.0, 2001)
            totals = np.array([sigma_res_analytic(st, float(x), sigma_eN) for x in grid])
            at_k = sigma_res_analytic(st, k, sigma_eN)
            assert at_k <= totals.min() + 1e-12, (
                f"κ* = {k:.4f} does not minimise the total residual for "
                f"σ_eN = {sigma_eN} — the closed form and sigma_res_analytic "
                "disagree"
            )

    def test_observer_noise_shrinks_the_coefficient_toward_zero(self):
        st = self._state()
        k0 = kappa_optimal(st, 0.0)
        assert 0.0 < kappa_optimal(st, 1.0) < k0 < 1.0, (
            "adding observer noise must pull the optimal coefficient toward 0 "
            "— deepest cancellation is not minimum total noise"
        )

    def test_default_argument_preserves_the_historical_behaviour(self):
        st = self._state()
        assert kappa_optimal(st) == pytest.approx(kappa_optimal(st, 0.0))
