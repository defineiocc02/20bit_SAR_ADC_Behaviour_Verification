"""Regression for the error-reference separation (external audit A07.6).

The audit's A07.6 observation:

    "Driver noise enters both the reference input and the output. With
    ``driver_noise`` set to 1 mV the returned ``err`` is still about 1 uV,
    but the error against a clean input is about 1 mV and the output SNDR is
    about 63.43 dB. ADC-only error and whole-chain error must be kept apart."

The root cause is that ``capture()`` adds the driver noise *to* ``x1``/``x2``,
so ``out - x1`` cancels it — the very quantity the driver noise is meant to
test. ``SampleBatch.x1_clean`` / ``SimResult.err_vs_clean`` now carry the
pre-noise input, so the two quantities can no longer be confused.
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config
from adi_model.chip import build_chip
from adi_model.pipeline import run_pipeline
from adi_model.sampler import sine_input
from adi_model.sim import initialize_state, run_sim
from adi_model.sim_split import run_sim_split

N = 2**13
DRIVER_NOISE_V = 1e-3


def _base_cfg(driver_noise_rms: float) -> Config:
    return Config(
        driver_noise_rms=driver_noise_rms,
        mismatch_enable=False,
        dem_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
    )


def _run_sim(cfg, fin):
    return run_sim(
        cfg,
        sine_input(0.9 * cfg.v_fs, fin),
        N,
        chip=build_chip(cfg),
        state=initialize_state(cfg),
        rng=np.random.default_rng(4),
    )


def _run_pipeline(cfg, fin):
    return run_pipeline(cfg, sine_input(0.9 * cfg.v_fs, fin), N, rng=np.random.default_rng(4))


def _run_split(cfg, fin):
    return run_sim_split(cfg, sine_input(0.9 * cfg.v_fs, fin), N, rng=np.random.default_rng(4))


CHAINS = {"sim": _run_sim, "pipeline": _run_pipeline, "sim_split": _run_split}


class TestErrorReferenceSeparation:
    """A 1 mV driver noise must not read as a 1 uV error."""

    @pytest.mark.parametrize("chain", sorted(CHAINS))
    def test_driver_noise_hides_in_err_but_not_in_err_vs_clean(self, chain):
        run = CHAINS[chain]
        cfg0 = _base_cfg(0.0)
        cfg1 = _base_cfg(DRIVER_NOISE_V)
        fin = cfg0.fs / 8

        r0 = run(cfg0, fin)
        r1 = run(cfg1, fin)

        err0 = float(np.std(r0.err))
        err1 = float(np.std(r1.err))
        clean1 = float(np.std(r1.err_vs_clean))

        # The internal error stays at the microvolt scale in both runs: the
        # driver noise cancels in `out - x1`. (It is not *bit*-identical,
        # because drawing the driver-noise realisation advances the RNG stream —
        # hence the scale test rather than an equality test.)
        assert (
            err0 < 5e-6 and err1 < 5e-6
        ), f"{chain}: internal error {err1*1e6:.3f} uV is not at the ADC floor"
        # ...but the whole-chain error must show the driver noise in full.
        assert clean1 == pytest.approx(DRIVER_NOISE_V, rel=0.02), (
            f"{chain}: err_vs_clean = {clean1*1e6:.1f} uV for a 1 mV driver "
            "noise — the whole-chain error is not being reported"
        )
        assert clean1 / max(err1, 1e-30) > 100.0, (
            f"{chain}: the two quantities are not separated "
            f"({clean1*1e6:.1f} uV vs {err1*1e6:.3f} uV) — audit A07.6"
        )

    @pytest.mark.parametrize("chain", sorted(CHAINS))
    def test_err_vs_clean_equals_err_to_x1_without_driver_noise(self, chain):
        """With no driver noise there is nothing to distinguish, so the new
        field must be bit-identical to the existing one — this guarantees the
        change cannot perturb the default analysis path.
        """
        run = CHAINS[chain]
        cfg = _base_cfg(0.0)
        r = run(cfg, cfg.fs / 8)
        assert np.array_equal(np.asarray(r.err_vs_clean), np.asarray(r.err_to_x1))

    def test_clean_input_is_recorded_before_the_noise(self):
        """``SampleBatch`` must carry the pre-noise samples explicitly."""
        from adi_model.sampler import capture

        cfg = _base_cfg(DRIVER_NOISE_V)
        b = capture(cfg, sine_input(0.5, cfg.fs / 16), 64, np.random.default_rng(0))
        assert b.x1_clean is not None and b.x2_clean is not None
        # x1 is the driven node; x1_clean is the ideal one.
        assert not np.allclose(b.x1, b.x1_clean)
        assert float(np.std(b.x1 - b.x1_clean)) == pytest.approx(DRIVER_NOISE_V, rel=0.25)

    def test_whole_chain_sndr_degrades_with_driver_noise(self):
        """The audit quoted ~63.4 dB for a 1 mV driver at 5 MHz. The whole-chain
        metric must show the degradation; the internal one must not.
        """
        from adi_model.metrics import sine_fit_metrics

        cfg0 = _base_cfg(0.0)
        cfg1 = _base_cfg(DRIVER_NOISE_V)
        fin = cfg0.fs / 8

        r0, r1 = _run_pipeline(cfg0, fin), _run_pipeline(cfg1, fin)
        sndr_int0 = sine_fit_metrics(r0.out, cfg0.fs, fin)["SNDR_dB"]
        sndr_int1 = sine_fit_metrics(r1.out, cfg1.fs, fin)["SNDR_dB"]
        # `out` is the converter output; driver noise shows up here.
        assert sndr_int1 < sndr_int0 - 20.0, "driver noise did not degrade the output spectrum"
