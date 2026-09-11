"""Regressions for the v6.2 refactor.

Each test here pins a defect that was *found by running the model*, not by
reading it. They exist so that the failure mode cannot come back silently.

1. ``TestObserverCorrectionIsDigital`` — the KTC observer correction used to be
   subtracted **ahead** of ADC2's quantiser (``quantize(vra - kappa*v_N)``),
   which put a frequency-dependent term inside the ADC2 range. At 5 MHz single
   tone that was already 10.3 % overflow and a 1.19 mV RMS residual, and it
   grew without bound towards Nyquist. See ``docs/adr/0006``.
2. ``TestFirstStageObservableRank`` — the calibration-observability rank law
   ``rank(U) = n_units_sig / n_active`` must hold for *every* reading, and the
   code sweep must be derived from ``2**b1`` rather than hard-coded 64.
3. ``TestSubWeightInducedINL`` — with the 7b + 2b-dither reading the sub-DAC is
   code-modulated, so the lumped sub-node parasitics produce a deterministic
   (period-2) INL that DEM cannot average. The analytic value must match the
   measurement.
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config
from adi_model.adc2 import ADC2
from adi_model.chip import build_chip
from adi_model.sampler import sine_input
from adi_model.sim import initialize_state, run_sim


class TestObserverCorrectionIsDigital:
    """The observer correction must not consume ADC2 headroom."""

    def test_zero_correction_is_identity(self, cfg):
        adc2 = ADC2(cfg)
        v = np.linspace(cfg.adc2_v_min, cfg.adc2_v_max, 257)
        q_plain, over_plain = adc2.quantize(v)
        q_corr, over_corr = adc2.quantize_with_correction(v, np.zeros_like(v))
        assert np.array_equal(q_plain, q_corr)
        assert np.array_equal(over_plain, over_corr)

    def test_huge_correction_does_not_overflow(self, cfg):
        """A correction far larger than the window must not clip ADC2."""
        adc2 = ADC2(cfg)
        v = np.linspace(cfg.adc2_v_min, cfg.adc2_v_max, 257)
        huge = np.full_like(v, 100.0 * (cfg.adc2_v_max - cfg.adc2_v_min))
        _, over = adc2.quantize_with_correction(v, -huge)
        assert not over.any(), "correction must be applied after quantisation"

    def test_plain_quantize_would_have_overflowed(self, cfg):
        """Guard the counterfactual: the old formulation is genuinely broken."""
        adc2 = ADC2(cfg)
        v = np.full(16, cfg.adc2_v_max)
        corr = np.full(16, -0.5)  # |kappa*v_N| ~ 0.3 V in practice
        _, over_old = adc2.quantize(v - corr)  # the v6.1 form
        assert over_old.all()
        _, over_new = adc2.quantize_with_correction(v, corr)
        assert not over_new.any()

    @pytest.mark.parametrize("fin_div", [2.0, 8.0])
    def test_no_overflow_with_ktc_at_worst_case(self, cfg, fin_div):
        """KTC on, full-scale input, near Nyquist: ADC2 must stay in range."""
        from adi_model.experiments import _clone

        c = _clone(
            cfg,
            ktc_enable=True,
            ktc_beta_error=0.2,
            mismatch_enable=False,
            dem_enable=False,
            calibration="none",
            ra_enable_noise=True,
            enable_sampling_noise=True,
        )
        n = 2**13
        r = run_sim(
            c,
            sine_input(1.0 * c.v_fs, c.fs / fin_div),
            n,
            chip=build_chip(c),
            state=initialize_state(c),
            rng=np.random.default_rng(3),
        )
        assert float(np.mean(r.adc2_over)) == 0.0
        assert float(np.mean(r.ra_sat)) == 0.0

    def test_beta_calibration_recovers_the_target(self, cfg):
        """The defect made this read 10.7 %; it must be < 1 %."""
        from adi_model.experiments import _clone
        from adi_model.sim import run_with_calibration

        n = 2**13
        c = _clone(
            cfg,
            ktc_enable=True,
            ktc_beta_error=0.2,
            mismatch_enable=False,
            dem_enable=False,
            calibration="gain_beta",
            enable_sampling_noise=False,
            ra_enable_noise=False,
        )
        rb = run_with_calibration(
            c, sine_input(0.9 * c.v_fs, c.fs * 1024 / n), n, rng=np.random.default_rng(21)
        )
        target = (cfg.g0 / cfg.ktc_gain_n) / 1.2
        assert abs(rb.state.kappa / target - 1.0) < 0.01


class TestFirstStageObservableRank:
    """The rank law must be derived, not hard-coded to one reading."""

    @pytest.mark.parametrize("constructor", ["default", "legacy_v61"])
    def test_rank_equals_intra_slice_positions(self, constructor):
        from adi_model.mapper import N_DEM_STATES as NS
        from adi_model.mapper import unit_rank_arrays

        cfg = Config() if constructor == "default" else Config.legacy_v61()
        slice_rank, unit_idx = unit_rank_arrays(cfg)
        n_u = cfg.n_units_sig
        codes = np.arange(0, 2**cfg.b1)  # derived, not 64
        U = np.zeros((codes.size * NS, n_u), dtype=np.float32)
        row = 0
        for k_code in codes:
            ku = int(k_code * cfg.units_per_lsb1)
            for sid in range(NS):
                sr, ui = slice_rank[sid], unit_idx[sid]
                U[row, sr[:ku] * cfg.n_unit_per_slice + ui[:ku]] = 1.0
                row += 1
        assert int(np.linalg.matrix_rank(U)) == n_u // cfg.n_active

    def test_code_sweep_must_span_the_reading(self):
        """With b1 = 7 a sweep of only 64 codes reports a wrong rank."""
        from adi_model.mapper import N_DEM_STATES as NS
        from adi_model.mapper import unit_rank_arrays

        cfg = Config()
        slice_rank, unit_idx = unit_rank_arrays(cfg)
        n_u = cfg.n_units_sig

        def rank_for(n_codes):
            U = np.zeros((n_codes * NS, n_u), dtype=np.float32)
            row = 0
            for k_code in range(n_codes):
                ku = int(k_code * cfg.units_per_lsb1)
                for sid in range(NS):
                    sr, ui = slice_rank[sid], unit_idx[sid]
                    U[row, sr[:ku] * cfg.n_unit_per_slice + ui[:ku]] = 1.0
                    row += 1
            return int(np.linalg.matrix_rank(U))

        assert rank_for(2**cfg.b1) == n_u // cfg.n_active
        assert rank_for(64) != n_u // cfg.n_active  # the stale constant


class TestSubWeightInducedINL:
    """The sub-node parasitic INL must match its analytic prediction."""

    def test_prediction_matches_measurement(self, cfg):
        from adi_model.experiments import stage13_dynamics

        out = stage13_dynamics(cfg, n=2**12, n_lvl=33)["sub_weight_inl"]
        assert out["sub_code_modulated"] is True
        assert out["ratio"] == pytest.approx(1.0, abs=0.25)

    def test_legacy_reading_does_not_excite_the_sub_dac(self):
        from adi_model.experiments import stage13_dynamics

        out = stage13_dynamics(Config.legacy_v61(), n=2**12, n_lvl=33)["sub_weight_inl"]
        assert out["sub_code_modulated"] is False
        assert out["measured_dac_only_INL_max_LSB20"] == pytest.approx(0.0, abs=1e-9)
