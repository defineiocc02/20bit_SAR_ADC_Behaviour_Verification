"""检查采样时偏使用的输入导数（外部审计 A07.3）。

v6.1 在样本网格上用 np.gradient 求导，其中心差分响应为
H(f)=sin(2*pi*f/fs)/(2*pi*f/fs)。fs=40 MHz 时，5/10/19 MHz 对应
0.9003/0.6366/0.0524，接近 Nyquist 时斜率被低估达 25.6 dB，不能将低频
时偏结果直接外推。

主线 input_derivative 优先使用输入自带解析导数，普通可调用输入则使用周期
谱求导。本文件检查该回退路径及其实际调用位置。
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
    """保留 v6.1 中心差分估计器，作为反例对照。"""
    return np.gradient(x, dt)


class TestSkewDerivative:
    """检查采样时偏使用的斜率在接近 Nyquist 时仍准确。"""

    @pytest.mark.parametrize("fin", FIN_LIST)
    def test_analytic_derivative_is_exact(self, fin):
        """检查 sine_input 自带的解析导数在所列频率准确。"""
        n = 4096
        t = np.arange(n) / FS
        amp = 2.7
        fn = sine_input(amp, fin)
        got = input_derivative(fn, t)
        want = 2 * np.pi * fin * amp * np.cos(2 * np.pi * fin * t)
        assert np.allclose(got, want, rtol=0, atol=1e-9)

    @pytest.mark.parametrize("fin", FIN_LIST)
    def test_spectral_derivative_beats_central_difference(self, fin):
        """检查无解析导数时的 FFT 求导幅度接近单位增益。"""
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
        """检查 fs=40 MHz、fin=19 MHz 时中心差分响应为约 0.0524，即 -25.6 dB（A07.3）。"""
        fin, fs = 19e6, FS
        x = fin / fs
        ratio = np.sin(2 * np.pi * x) / (2 * np.pi * x)
        assert ratio == pytest.approx(0.0524, abs=5e-4)
        assert 20 * np.log10(ratio) == pytest.approx(-25.6, abs=0.2)

    def test_derivative_is_invariant_to_frequency_scaling(self):
        """检查正弦导数幅度随频率成比例；中心差分接近 Nyquist 时不满足此性质。"""
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
        """检查无 derivative 属性的普通可调用输入使用主线谱求导回退。"""
        t = np.arange(2048) / FS
        fin, amp = 3e6, 1.5
        plain = lambda tt: amp * np.sin(2 * np.pi * fin * np.asarray(tt))  # noqa: E731
        got = input_derivative(plain, t)
        want = 2 * np.pi * fin * amp * np.cos(2 * np.pi * fin * t)
        # Non-coherent record -> small edge/leakage error, but the gain is right.
        assert abs(float(np.std(got)) / float(np.std(want)) - 1.0) < 0.05


class TestSkewEntersThePipeline:
    """检查流水线消费准确导数，不自行使用 np.gradient。"""

    def test_pipeline_source_does_not_use_np_gradient(self):
        """仅检查可执行词法单元中不存在 np.gradient 调用。

        源码 docstring 中可能提到该禁用规则；tokenize 排除注释和字符串，避免把说明
        文字当成调用位置。
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
        """用相同记录对照主线导数与重新注入的 np.gradient。

        检查时偏效应的抑制符合中心差分 H(fin) 响应，重现 A07.3 的估计器问题；
        结果针对该求导路径，不代替 ADC 性能结论。
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
