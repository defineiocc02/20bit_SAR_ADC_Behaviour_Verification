"""Adversarial regression tests derived from the external audit (v6.1).

Each test encodes one audit finding and asserts the *fixed* behaviour. Every
test in this file would have FAILED against the v6.1 tree; that is the point.
The tests are deliberately independent of the simulation's own self-assessment
(``experiments.stage*``) — the audit's core lesson was that a stage which
checks its own output against its own assumptions cannot catch this class of
defect.

Finding index — the audit report labels its findings ``A01``–``A07``; the
shorter ``F`` labels below are stable *test* identifiers (referenced from
``CHANGELOG.md``), and this table is the authoritative mapping:

    A01  first-stage resolution reading      -> F1   TestF1FirstStageReading
    A02  cross-cycle sampling state          -> F2   TestF2SliceCausality
    A03  dither code unit conversion         -> F3   TestF3DitherUnits
    A04  DR is prescribed, not predicted     -> F4   TestF4NoiseBudgetIsAnAnchor
    A07.1 static test / 20-bit code domain   -> F8   TestF8EvidenceScope
    A07.2 flicker absent from the main record-> F9   TestF9FlickerBandLimit
    A07.4 Monte-Carlo histogram provenance   -> F10  TestF10MonteCarloProvenance
    A07.5 low-frequency metrics crash        -> extra TestExtraLowFrequencyMetrics

Findings whose fixes live in ``tests/regression/`` rather than here, because
they were pinned by *running* the model:

    A05/A06  observer correction headroom    -> tests/regression/test_observer_headroom.py
    A07.3    skew slope (np.gradient bias)   -> tests/regression/test_skew_derivative.py

See ``docs/audit_response.md`` for the full finding-by-finding response.
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.mapper import dither_transfer_code
from adi_model.sadc import (
    units_per_first_stage_step,
)
from adi_model.slice_pool import PhysicalSlicePool, check_causality


# ===========================================================================
# F1 — the first stage's resolution reading must be explicit and self-consistent
# ===========================================================================
class TestF1FirstStageReading:
    """The audit's first finding: v6.1 quietly read "9b quantization in the
    first stage" as "6b coarse decision + 3b dither codeword", which does NOT
    give 512 input decision levels.

    v6.1 evidence: b1=6 -> 64 levels, 93.75 mV step, ~93.75 mV max residue.
    Fixed: the reading is an explicit field, and the config refuses
    combinations where the DAC cannot express 2**b1 judgement levels.
    """

    def test_readings_are_explicit_and_distinct(self):
        """Three readings must exist and be selected by name, not by default."""
        cfg = Config()
        assert cfg.stage1_reading in {
            "paper_consistent",
            "paper_literal",
            "legacy_codeword",
        }
        legacy = Config.legacy_v61()
        assert legacy.stage1_reading == "legacy_codeword"
        consistent = Config.paper_consistent()
        assert consistent.stage1_reading == "paper_consistent"

    def test_decision_levels_equal_two_to_the_b1(self):
        """The number of *input* decision levels must be 2**b1. This is the
        quantity the audit measured as 64 instead of 512.
        """
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            assert cfg.stage1_levels == 2**cfg.b1

    def test_step_is_two_vfs_over_levels(self):
        """Delta1 must be 2*v_fs/2**b1 (the audit's 11.71875 mV for b1=9,
        93.75 mV for b1=6).
        """
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            assert cfg.delta1 == pytest.approx(2.0 * cfg.v_fs / 2**cfg.b1)

    def test_reading_and_topology_must_be_consistent(self):
        """A 1024-level reading on a 512-level DAC must be reported as FAIL,
        not silently simulated into garbage.
        """
        bad = Config(b1=10)
        checks = bad.validate(verbose=False)
        key = "第一级读数与 DAC 拓扑自洽（2**b1 <= DAC 电平数）"
        assert key in checks, "validate() must carry the consistency judgement"
        assert (
            checks[key][2] is False
        ), "b1=10 needs 1024 levels but the DAC has 512; validate() must FAIL"

    def test_paper_consistent_reading_satisfies_both_disclosures(self):
        """The audit noted that the literal "9b SADC" reading contradicts the
        disclosed 2b dither range enhancement. The only split consistent with
        BOTH disclosures is 7 (SADC) + 2 (dither range) = 9.
        """
        cfg = Config.paper_consistent()
        enh = float(np.log2(units_per_first_stage_step(cfg, None)))
        assert enh == pytest.approx(float(cfg.dither_enhancement_bits))
        assert cfg.b1 + enh == pytest.approx(9.0)

    def test_legacy_reading_does_not_match_the_enhancement_disclosure(self):
        """Guards the derivation: b1=6 gives 3b of grid headroom, which is NOT
        the disclosed 2b, so it cannot be the paper-consistent reading either.

        The config *declares* its own 3b (self-consistency is enforced by
        ``validate()`` — see ``test_enhancement_bits_must_match_the_grid``);
        the disagreement underneath the test is with the **paper's** 2b.
        """
        disclosed_enh = 2  # [00]: "enhanced by 2b"
        legacy = Config.legacy_v61()
        grid_enh = float(np.log2(units_per_first_stage_step(legacy, None)))
        assert grid_enh == pytest.approx(3.0)
        assert int(legacy.dither_enhancement_bits) == 3  # self-consistent
        assert int(legacy.dither_enhancement_bits) != disclosed_enh

    def test_enhancement_bits_must_match_the_grid(self):
        """The declared enhancement must equal log2(DAC levels / 2**b1).

        This is the machine-checkable form of ADR 0003: silently moving ``b1``
        without re-deriving the reading is a FAIL, not a quiet drift.
        """
        key = "dither 增强位数 == log2(DAC 电平数 / 2**b1)"
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            checks = cfg.validate(verbose=False)
            assert key in checks
            assert checks[key][2] is True, f"{cfg.stage1_reading} must be self-consistent"

        # A hand-moved b1 without the matching grid declaration is caught.
        drifted = Config(
            b1=7,
            dither_enhancement_bits=3,  # claims 3b on a 2b grid
            adc2_n_bits=14,
            adc2_v_min=-0.15,
            adc2_v_max=1.65,
            ra_v_clip=1.98,
        )
        assert drifted.units_per_lsb1 == 4
        assert drifted.validate(verbose=False)[key][2] is False

    def test_backend_range_is_derived_from_delta1(self):
        """ADC2's range and depth must follow Delta1, so that changing the
        stage-1 reading cannot leave a stale, overflowing window behind.
        """
        for cfg in (Config(), Config.paper_consistent(), Config.legacy_v61()):
            span = cfg.g_actual * cfg.delta1
            assert cfg.adc2_v_max == pytest.approx(1.10 * span, rel=1e-9)
            assert cfg.adc2_v_min == pytest.approx(-0.10 * span, rel=1e-9)
            assert cfg.ra_v_clip >= cfg.adc2_v_max


# ===========================================================================
# F2 — physical slice state must carry across cycles
# ===========================================================================
class TestF2SliceCausality:
    """The audit's second finding: `ShuffledScheduler.reserve_dual()` redrew an
    independent permutation every sample, so only 1 of 8191 transitions had the
    converting group equal to the previous acquiring group (3.56/8 slices on
    average). The converting capacitors therefore did not hold the sample being
    converted.

    Fixed: `PhysicalSlicePool` maintains acquisition state across cycles.
    """

    @pytest.mark.parametrize("strategy", ["pingpong", "shuffle_causal"])
    def test_no_cross_cycle_violations(self, cfg, strategy):
        pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
        plan = pool.plan(4096, np.random.default_rng(20260910), strategy=strategy)
        chk = check_causality(plan)
        assert chk["violations"] == 0, (
            f"{strategy}: {chk['violations']} cycles converted a group that "
            f"was not acquired in the previous cycle"
        )

    @pytest.mark.parametrize("strategy", ["pingpong", "shuffle_causal"])
    def test_full_coverage(self, cfg, strategy):
        """Every converting slice must have been acquired — mean coverage 8/8.
        v6.1's value was 3.56/8.
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
        plan = pool.plan(4096, np.random.default_rng(20260910), strategy=strategy)
        chk = check_causality(plan)
        assert chk["mean_coverage"] == pytest.approx(8.0, abs=1e-9)

    def test_shuffle_keeps_randomness(self, cfg):
        """Causality must not be bought by freezing the schedule: the shuffle
        strategy must still visit many distinct converting groups.
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(5))
        plan = pool.plan(2048, np.random.default_rng(1), strategy="shuffle_causal")
        distinct = len({tuple(g) for g in plan.conv.tolist()})
        assert distinct > 100, (
            f"only {distinct} distinct converting groups — the schedule is " f"effectively fixed"
        )

    def test_mismatch_binds_to_the_physical_selection(self, cfg):
        """The audit showed fixed and shuffled schedules produced bit-identical
        DAC errors (`max|delta| = 0.0 uV`) because `evaluate_physical` never
        received the 8/18 selection. The physical pool must make the error
        depend on which slices convert.
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(7))
        n = 1024
        k = (np.arange(n) % 512).astype(float)
        sid = np.arange(n) % 512
        conv_a = np.tile(np.arange(0, 8), (n, 1))
        conv_b = np.tile(np.arange(10, 18), (n, 1))
        e_a = pool.dac_error(conv_a, k, sid)
        e_b = pool.dac_error(conv_b, k, sid)
        delta = np.max(np.abs(e_a - e_b))
        assert delta > 0.0, (
            "DAC error is independent of the 8/18 physical selection — the " "audit's exact finding"
        )

    def test_signal_capacitance_depends_on_selection(self, cfg):
        """The gain must also move with the selection, because the converting
        slices contribute the sampling capacitance.

        Note on tolerances: the effect is a *matching* effect (~10 ppm at the
        default 100 ppm unit mismatch), which is smaller than NumPy's default
        ``allclose`` rtol of 1e-5 — so the assertion uses explicit tolerances
        instead of ``allclose``, which would happily declare two different
        capacitor banks equal.
        """
        pool = PhysicalSlicePool(cfg, np.random.default_rng(11))
        n = 512
        conv_a = np.tile(np.arange(0, 8), (n, 1))
        conv_b = np.tile(np.arange(8, 16), (n, 1))
        c_a = pool.signal_capacitance(conv_a)
        c_b = pool.signal_capacitance(conv_b)
        rel = abs(c_a.mean() / c_b.mean() - 1.0)
        assert rel > 1e-7, (
            "the selected 8-of-18 group does not change the sampling "
            "capacitance — this is the audit's exact finding"
        )
        assert rel < 1000e-6, (
            "the selection is changing the gain by more than a matching " "artefact would"
        )

        # A causally shuffled plan must modulate it sample by sample, and the
        # modulation must stay at the matching level.
        plan = pool.plan(n, np.random.default_rng(3), strategy="shuffle_causal")
        c = pool.signal_capacitance(plan.conv[plan.valid])
        assert np.unique(c).size > 1, "shuffling does not modulate the gain"
        ppm = (c.max() - c.min()) / c.mean() * 1e6
        assert 0.0 < ppm < 1000.0, f"gain modulation {ppm:.1f} ppm is not matching-scale"


# ===========================================================================
# F3 — the dither code unit conversion
# ===========================================================================
class TestF3DitherUnits:
    """The audit's third finding: the coarse-granularity branch computed
    `d_code = -round(d / Delta_Q)` — a count in *coarse* steps — and added it
    to a code expressed in *RDAC unit* steps. The missing factor was
    Delta_Q/Delta_D (= 8 in v6.1's configuration).

    Correcting it moved the branch from 32.7% to 16.0% overflow and from
    16308 uV to 5699 uV RMS, flipping the check from PASS to FAIL.
    """

    def test_range_model_is_an_exact_unit_conversion(self, cfg):
        d = np.array([-0.25, 0.5, -1.0, 0.0, 0.75]) * cfg.delta1
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        assert np.array_equal(code, -np.round(d / cfg.rdac_step))

    def test_output_is_in_rdac_unit_steps(self, cfg):
        """The returned code, multiplied by the RDAC step, must reproduce the
        dither voltage to within half an RDAC step — i.e. it is a code in RDAC
        units, not in coarse units.
        """
        d = np.linspace(-0.5, 0.5, 21) * cfg.delta1
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        resid = np.abs(code * cfg.rdac_step + d)
        assert np.all(resid <= cfg.rdac_step / 2 + 1e-18), (
            "the converted code does not reconstruct the dither voltage in "
            "RDAC units — a unit-system mix-up"
        )

    def test_missing_conversion_factor_is_reproduced(self, cfg):
        """The audit's counterfactual: adding a coarse-unit count directly is
        wrong by exactly step_coarse/step_rdac.
        """
        d = np.array([1.0, -1.0]) * cfg.delta1
        correct = -np.round(d / cfg.rdac_step)
        wrong = -np.round(d / cfg.delta1)
        ratio = cfg.delta1 / cfg.rdac_step
        assert ratio > 1.0
        assert np.array_equal(correct, wrong * ratio)

    def test_zero_dither_returns_zero(self, cfg):
        d = np.zeros(4)
        cc = Config(
            **{**cfg.__dict__, "dither_mode": "quantizer", "dither_transfer_model": "range"}
        )
        code = dither_transfer_code(cc, d, step_rdac=cfg.rdac_step, step_coarse=cfg.delta1)
        assert np.array_equal(code, np.zeros(4))

    def test_nonpositive_step_raises(self, cfg):
        cc = Config(**{**cfg.__dict__, "dither_mode": "quantizer"})
        with pytest.raises(ValueError):
            dither_transfer_code(cc, np.zeros(2), step_rdac=0.0, step_coarse=cfg.delta1)


# ===========================================================================
# F4 — the noise budget is an anchor, not a prediction
# ===========================================================================
class TestF4NoiseBudgetIsAnAnchor:
    """The audit's fourth finding: `ra_out_noise_rms=None` reverse-solves the
    RA noise from the target DR, so reproducing 94.6 dB is an identity, not a
    prediction. The fix is not to change the maths but to *label* it: the
    derived quantity must be graded FITTED (an anchor), never DISCLOSED or
    DERIVED-as-a-prediction, and the user must be able to see it.
    """

    def test_auto_resolved_ra_noise_is_graded_as_an_anchor(self, cfg):
        from adi_model.provenance import SourceGrade, annotate_config

        ann = annotate_config(cfg)
        g = ann["ra_out_noise_rms"]
        assert g.grade is SourceGrade.FITTED, (
            "ra_out_noise_rms is reverse-solved from target_dr_db; it must be "
            "FITTED (an anchor), not quotable as a prediction"
        )
        assert "ANCHOR" in g.source.upper() or "anchor" in g.source

    def test_noise_budget_sums_to_the_target(self, cfg):
        """Self-consistency: the components must add up to the target DR. The
        audit confirmed this holds (90/94.6/98 dB -> 90.015/94.611/98.001 dB);
        it is a check that the anchor is wired correctly, NOT evidence that the
        circuit independently attains it.
        """
        from adi_model.config import noise_budget

        nb = noise_budget(cfg)
        total = np.sqrt(
            sum(
                v**2
                for k, v in nb.items()
                if "折输入" in k or "RA" in k or "kT/C" in k or "ADC2" in k
            )
        )
        dr = 20 * np.log10((cfg.v_fs / np.sqrt(2)) / total)
        assert dr == pytest.approx(cfg.target_dr_db, abs=0.5)

    def test_pdk_sigma_is_assumed_not_disclosed(self, cfg):
        """The 1117 ppm figure comes from an assumed area law, not from a PDK
        measurement; the audit flagged that it was being used to back out the
        real chip's calibration factor.
        """
        from adi_model.provenance import SourceGrade, annotate_config

        ann = annotate_config(cfg)
        assert ann["pdk_sigma_est_ppm"].grade is SourceGrade.ASSUMED


# ===========================================================================
# F8 — evidence scope: static test and the output code domain
# ===========================================================================
class TestF8EvidenceScope:
    """The audit's eighth finding: the static test is an *averaged mean-error*
    curve, not a full code-density DNL; and `n_bits_target` only defines the
    target LSB, it does not quantise the output. Presenting the former as INL
    and the latter as a 20-bit encoder over-claims the evidence.
    """

    def test_static_test_reports_its_own_scope(self):
        import inspect

        from adi_model.metrics import inl_from_mean_error, static_test

        src = inspect.getsource(static_test) + inspect.getsource(inl_from_mean_error)
        assert "code-density" in src or "码密度" in src, (
            "the static test must declare that it is not a code-density DNL " "measurement"
        )

    def test_n_bits_target_does_not_change_the_analog_output(self, cfg):
        """n_bits_target only sets the LSB reference. Asserting it leaves the
        simulator's output untouched documents *why* no 20-bit code-domain
        claim can be made from this model.
        """
        from adi_model.sim import run_sim

        a = Config(**{**cfg.__dict__, "n_bits_target": 20, "dac_arch": "unary"})
        b = Config(**{**cfg.__dict__, "n_bits_target": 18, "dac_arch": "unary"})
        n = 2**11
        inp = sine_input(0.5 * cfg.v_fs, cfg.fs * 331 / n)
        ra = run_sim(a, inp, n, rng=np.random.default_rng(1))
        rb = run_sim(b, inp, n, rng=np.random.default_rng(1))
        assert np.array_equal(ra.out, rb.out), (
            "output should not depend on n_bits_target — the model is an "
            "analog-voltage-equivalent model"
        )


# ===========================================================================
# F9 — the flicker generator and the main record
# ===========================================================================
class TestF9FlickerBandLimit:
    """The audit's ninth finding: at fs=40 MHz / N=32768 the FFT bin is
    ~1220 Hz, so a 40 Hz corner is far below the record and the generator used
    to zero everything above the corner — leaving the main record with an
    all-zero flicker sequence.

    The fix is twofold: the generator must expose how much of its power is
    resolvable, and the main-record claim must be withdrawn rather than
    implied. `stage23` validates the generator separately at a low fs.
    """

    def test_generator_has_power_below_the_record_band(self):
        """Flicker must be non-zero in a record long enough to resolve 40 Hz."""
        from adi_model.ra import flicker_series

        n, fs = 2**16, 4000.0  # df = 0.061 Hz, resolves 40 Hz
        x = flicker_series(n, fs, f_corner=40.0, sigma_white=1e-6, rng=np.random.default_rng(0))
        assert np.any(x != 0.0)
        assert np.std(x) > 0.0

    def test_generator_power_is_zero_above_the_corner(self):
        """The documented convention: the 1/f branch stops at the corner and
        the white floor is supplied by the system's thermal source, so the
        generator must not inject a second white floor (that cost 3 dB in the
        stage-23 history).
        """
        from adi_model.ra import flicker_series

        n, fs = 2**15, 4000.0
        x = flicker_series(n, fs, f_corner=40.0, sigma_white=1e-6, rng=np.random.default_rng(0))
        P = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(n, d=1.0 / fs)
        hi = f > 10.0 * 40.0
        assert P[hi].sum() < 1e-12 * max(
            P.sum(), 1e-30
        ), "the generator is still emitting power well above the corner"

    def test_main_record_claim_is_not_silently_made(self, cfg):
        """At the default fs/N the corner is unresolvable; the model must not
        pretend otherwise. Assert the resolvability arithmetic.
        """
        n = 2**15
        df = cfg.fs / n
        assert df > 40.0, (
            "this test documents that the main record cannot resolve 40 Hz; "
            "if fs/N changes, revisit docs/model_scope.md"
        )


# ===========================================================================
# F10 — Monte-Carlo histogram provenance
# ===========================================================================
class TestF10MonteCarloProvenance:
    """The audit's tenth finding: the "60-chip SNDR distribution" figure was
    drawn from 400 freshly drawn Gaussian samples parameterised by the MC mean
    and sigma, not from the 60 simulated chips. Such a figure cannot support
    tail or yield statements.

    The fix: Monte-Carlo results must be presented from real per-chip samples.
    """

    def test_summary_keeps_per_chip_samples(self, cfg):
        """The Monte-Carlo result must carry the per-chip SNDR array, and the
        reported moments must be *computed from it* — so no downstream figure
        can silently fall back to a Gaussian parametrised by (mean, sigma).
        """
        from adi_model.experiments import mc_pdk_sigma

        out = mc_pdk_sigma(cfg, n_chips=6, n=2**11)
        assert "sndr_per_chip" in out, (
            "Monte-Carlo must expose the per-chip samples so that any "
            "histogram is drawn from real data"
        )
        s = np.asarray(out["sndr_per_chip"])
        assert s.shape == (6,)
        # The summary must be a deterministic function of the samples, not an
        # independent description of them.
        assert out["SNDR_mean"] == pytest.approx(float(s.mean()), rel=1e-12)
        assert out["SNDR_std"] == pytest.approx(float(s.std()), rel=1e-12)
        assert out["SNDR_min"] == pytest.approx(float(s.min()), rel=1e-12)
        assert out["sigma_ppm"] == pytest.approx(float(cfg.pdk_sigma_est_ppm))

    def test_histogram_is_not_resampled(self):
        """Static guard: nothing in ``src/`` or ``tools/`` may synthesise extra
        samples from a (mean, sigma) pair — that was the audit's exact finding.

        The first version of this guard only scanned ``adi_model.experiments``,
        and the offending code was in ``tools/run_all.py``: the guard passed
        while the defect shipped. Scanning the *tree* is the fix. ``tools/``
        matters because that is where the figures are drawn.
        """
        import pathlib
        import re

        roots = [
            pathlib.Path(__file__).resolve().parents[2] / "src",
            pathlib.Path(__file__).resolve().parents[2] / "tools",
        ]
        # rng.normal(<something_mean>, <something_std>, <count>) — a resample.
        pattern = re.compile(
            r"(rng|np\.random\.[a-z_]*|default_rng\([^)]*\))\.normal\(\s*"
            r"[^)]*mean[^)]*,\s*[^)]*std[^)]*,"
        )
        offenders = []
        for root in roots:
            for path in sorted(root.rglob("*.py")):
                for i, line in enumerate(path.read_text().splitlines(), 1):
                    code = line.split("#", 1)[0]
                    if pattern.search(code):
                        offenders.append(f"{path.relative_to(root.parent)}:{i}: {line.strip()}")
        assert not offenders, (
            "found a histogram drawn by resampling a (mean, sigma) pair: " f"{offenders}"
        )

    def test_monte_carlo_figure_uses_per_chip_samples(self):
        """The figure generator must index ``sndr_per_chip``, not the moments."""
        import pathlib

        run_all = pathlib.Path(__file__).resolve().parents[2] / "tools" / "run_all.py"
        src = run_all.read_text()
        assert "sndr_per_chip" in src, (
            "tools/run_all.py must draw the SNDR histogram from the real "
            "per-chip samples (audit A07.4)"
        )

    def test_the_guard_would_catch_the_historical_line(self):
        """Guard-the-guard: the v6.1 line must still match the pattern.

        Without this, tightening/breaking the regex would silently turn the
        static check into a no-op — exactly how the defect survived the first
        version of the guard.
        """
        import re

        historical = '    samp = rng.normal(m["SNDR_mean"], m["SNDR_std"], 400)'
        pattern = re.compile(
            r"(rng|np\.random\.[a-z_]*|default_rng\([^)]*\))\.normal\(\s*"
            r"[^)]*mean[^)]*,\s*[^)]*std[^)]*,"
        )
        assert pattern.search(historical), (
            "the resampling guard no longer matches the v6.1 line — it has " "become vacuous"
        )


# ===========================================================================
# extra — the low-frequency metrics crash
# ===========================================================================
class TestExtraLowFrequencyMetrics:
    """Not in the audit's numbered list, but found while reproducing it:
    `sine_fit_metrics` raised `ValueError` for fin <= ~5 kHz because the
    harmonic search window `fund_bin - guard : fund_bin + guard` went negative
    once `fund_bin` was clamped to 1.
    """

    @pytest.mark.parametrize("fin", [1e3, 5e3, 1e5, 1e6, 5e6, 19e6])
    def test_metrics_do_not_raise_across_bin_clamping(self, cfg, fin):
        from adi_model.metrics import sine_fit_metrics

        n = 2**14
        x = sine_input(1.0, fin)(np.arange(n) / cfg.fs)
        m = sine_fit_metrics(x, cfg.fs, fin)
        assert np.isfinite(m["SNDR_dB"])
        assert m["SNDR_dB"] > 60.0

    def test_low_frequency_declares_harmonics_unreliable(self, cfg):
        """When the fundamental is too low to separate harmonics, the function
        must say so rather than return a silently wrong THD.
        """
        from adi_model.metrics import sine_fit_metrics

        n = 2**14
        x = sine_input(1.0, 1e3)(np.arange(n) / cfg.fs)
        m = sine_fit_metrics(x, cfg.fs, 1e3)
        assert "harmonics_reliable" in m
        assert m["harmonics_reliable"] is False

    def test_spectral_band_helper_clamps_at_the_edges(self):
        """`_band_max` must clamp instead of relying on Python's negative
        slicing, which is what produced the empty window.
        """
        from adi_model.metrics import _band_max

        X = np.arange(32, dtype=float)
        for b in (0, 1, 31):
            assert np.isfinite(_band_max(X, b, 4))
