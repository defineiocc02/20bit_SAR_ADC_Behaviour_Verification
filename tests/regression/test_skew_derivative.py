"""Regression for the timing-skew derivative (external audit A07.3).

The audit measured that v6.1 computed ``dx/dt`` with ``np.gradient`` on the
sample grid. A central difference has amplitude response

    H(f) = sin(2*pi*f/fs) / (2*pi*f/fs)

which is 0.9003 at 5 MHz, 0.6366 at 10 MHz and **0.0524** at 19 MHz for
fs = 40 MHz — i.e. the slope is underestimated by up to **25.6 dB** near
Nyquist. Every effect proportional to ``dx/dt`` (interleaving timing skew is
exactly that) is therefore silently suppressed at high input frequencies, and
low-frequency skew results cannot be extrapolated.

The fix is :func:`adi_model.sampler.input_derivative`: use the analytic
derivative when the input supplies one (``sine_input`` does), else a spectral
derivative with unit response to ``fs/2``.
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config
from adi_model.sampler import (
    dc_input,
    input_derivative,
    sine_input,
    spectral_derivative,
)

FS = 40e6
FIN_LIST = [1.25e6, 5e6, 10e6, 15e6, 19e6, 19.9e6]


def _central_difference(x: np.ndarray, dt: float) -> np.ndarray:
    """The v6.1 estimator, kept here as the counterfactual."""
    return np.gradient(x, dt)


class TestSkewDerivative:
    """The slope used by timing skew must be accurate up to Nyquist."""

    @pytest.mark.parametrize("fin", FIN_LIST)
    def test_analytic_derivative_is_exact(self, fin):
        """``sine_input`` carries its own derivative: exact at any fin."""
        n = 4096
        t = np.arange(n) / FS
        amp = 2.7
        fn = sine_input(amp, fin)
        got = input_derivative(fn, t)
        want = 2 * np.pi * fin * amp * np.cos(2 * np.pi * fin * t)
        assert np.allclose(got, want, rtol=0, atol=1e-9)

    @pytest.mark.parametrize("fin", FIN_LIST)
    def test_spectral_derivative_beats_central_difference(self, fin):
        """Even without an analytic source, the FFT estimator has ~unit gain."""
        n = 4096
        t = np.arange(n) / FS
        amp = 2.7
        x = amp * np.sin(2 * np.pi * fin * t)
        want_rms = 2 * np.pi * fin * amp / np.sqrt(2)

        got_rms = float(np.std(spectral_derivative(x, 1.0 / FS)))
        old_rms = float(np.std(_central_difference(x, 1.0 / FS)))

        assert (
            abs(got_rms / want_rms - 1.0) < 0.02
        ), f"spectral derivative gain {got_rms / want_rms:.4f} at fin={fin/1e6} MHz"
        if fin > 10e6:
            assert old_rms < 0.7 * want_rms, (
                "counterfactual guard: the central difference must be visibly "
                "wrong this close to Nyquist, otherwise this test is vacuous"
            )

    def test_nineteen_mhz_bias_is_the_audited_25_6_db(self):
        """Pin the exact number the audit reported (A07.3).

        sin(2*pi*f/fs)/(2*pi*f/fs) at 19 MHz with fs = 40 MHz -> 0.0524,
        i.e. -25.6 dB.
        """
        fin, fs = 19e6, FS
        x = fin / fs
        ratio = np.sin(2 * np.pi * x) / (2 * np.pi * x)
        assert ratio == pytest.approx(0.0524, abs=5e-4)
        assert 20 * np.log10(ratio) == pytest.approx(-25.6, abs=0.2)

    def test_derivative_is_invariant_to_frequency_scaling(self):
        """A useful sanity property: d/dt of A*sin(2*pi*f*t) scales with f.

        The central difference does not satisfy this near Nyquist; both of our
        estimators do.
        """
        n = 4096
        t = np.arange(n) / FS
        amp = 1.0
        r1 = float(np.std(input_derivative(sine_input(amp, 5e6), t)))
        r2 = float(np.std(input_derivative(sine_input(amp, 10e6), t)))
        assert r2 / r1 == pytest.approx(2.0, rel=1e-6)

    def test_dc_input_has_zero_slope(self):
        t = np.arange(512) / FS
        d = input_derivative(dc_input(0.7), t)
        assert np.all(d == 0.0)

    def test_fallback_handles_a_plain_lambda(self):
        """A callable without ``.derivative`` must still work (spectral path)."""
        t = np.arange(2048) / FS
        fin, amp = 3e6, 1.5
        plain = lambda tt: amp * np.sin(2 * np.pi * fin * np.asarray(tt))  # noqa: E731
        got = input_derivative(plain, t)
        want = 2 * np.pi * fin * amp * np.cos(2 * np.pi * fin * t)
        # Non-coherent record -> small edge/leakage error, but the gain is right.
        assert abs(float(np.std(got)) / float(np.std(want)) - 1.0) < 0.05


class TestSkewEntersThePipeline:
    """The pipeline must consume the accurate slope, not its own np.gradient."""

    def test_pipeline_source_does_not_use_np_gradient(self):
        """Static guard: ``np.gradient`` must not come back into the pipeline.

        The guard inspects **code only**. `run_pipeline` carries a "do not
        reintroduce np.gradient" warning inside its docstring, and a naive
        line scan (`"np.gradient" in line and not line.startswith("#")`)
        trips over it — the warning is documentation, not a call site. We
        therefore let :mod:`tokenize` drop every COMMENT and STRING token and
        match against what is left, which is exactly the executable surface.
        """
        import inspect
        import io
        import tokenize

        import adi_model.pipeline as pipeline_mod

        src = inspect.getsource(pipeline_mod)
        lines = src.splitlines()
        code_by_row: dict[int, list[str]] = {}
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            code_by_row.setdefault(tok.start[0], []).append(tok.string)

        offenders = [
            (row, lines[row - 1].strip())
            for row, parts in sorted(code_by_row.items())
            # Tokens are concatenated without whitespace, so `np . gradient`
            # can never masquerade as a call site.
            if "np.gradient" in "".join(parts)
        ]
        assert not offenders, (
            "np.gradient is back in the signal path — its near-Nyquist gain is "
            f"0.052 at 19 MHz (audit A07.3): {offenders}"
        )

    @pytest.mark.parametrize(
        "fin,min_ratio",
        [
            (5e6, 1.05),  # |H| = 0.9003 -> 1.11x
            (10e6, 1.40),  # |H| = 0.6366 -> 1.57x
            (15e6, 2.80),  # |H| = 0.3001 -> 3.33x
            (18e6, 7.00),  # |H| = 0.1093 -> 9.15x
            (19e6, 12.0),  # |H| = 0.0524 -> 19.1x
        ],
    )
    def test_np_gradient_would_have_suppressed_the_effect(self, monkeypatch, fin, min_ratio):
        """Counterfactual, driven through the real signal path.

        Run the same record twice: once with the shipped estimator, once with
        ``np.gradient`` substituted back in. The measured suppression must be
        at least ``1/|H(fin)|``, where ``H`` is the central-difference
        amplitude response. This is audit finding A07.3 reproduced end to end
        -- not a claim about the numbers, a claim about the estimator.
        """
        import adi_model.pipeline as pipeline_mod
        from adi_model.experiments import _clone

        n = 2**13
        cfg = _clone(
            Config(),
            # Isolate timing skew: continuous RC phase lag is an independent,
            # much larger error and must not be attributed to slope estimation.
            dyn_input_settling=False,
            dyn_ron_code_coeff=0.0,
            slice_timing_skew_s=10e-12,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            mismatch_enable=False,
            dem_enable=False,
            dither_mode="off",
        )
        fn = sine_input(0.7 * cfg.v_fs, fin)

        good = (
            float(
                np.std(
                    np.asarray(
                        pipeline_mod.run_pipeline(cfg, fn, n, rng=np.random.default_rng(5)).err
                    )
                )
            )
            * 1e6
        )

        monkeypatch.setattr(
            pipeline_mod,
            "input_derivative",
            lambda f, t: np.gradient(np.asarray(f(t), dtype=float), t),
        )
        old = (
            float(
                np.std(
                    np.asarray(
                        pipeline_mod.run_pipeline(cfg, fn, n, rng=np.random.default_rng(5)).err
                    )
                )
            )
            * 1e6
        )

        assert old > 0.0, "counterfactual produced no skew error at all"
        ratio = good / old
        assert ratio >= min_ratio, (
            f"at fin={fin/1e6:.1f} MHz the accurate slope gives {good:.2f} uV "
            f"vs {old:.2f} uV for np.gradient (ratio {ratio:.2f}, expected "
            f">= {min_ratio:.2f}); the near-Nyquist suppression (A07.3) is not "
            "reproduced, so this test has lost its counterfactual"
        )
