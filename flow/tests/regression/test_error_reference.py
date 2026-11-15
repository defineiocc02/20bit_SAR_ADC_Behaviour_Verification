"""在唯一显式流水线中分离干净输入与带驱动噪声的参考域。

内部转换误差可以相对 input_v 诊断，但公开 err 必须相对 input_clean_v；
不能因输出与带噪参考相减而遗漏公共输入噪声。夹具不代表器件噪声预测。
"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.behavioral.sampler import capture, sine_input
from adi_model.metrics.metrics import sine_fit_metrics
from adi_model.simulation.config import Config, ConfigError
from adi_model.simulation.pipeline import run_pipeline


def test_driver_noise_cannot_cancel_from_the_reported_error(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=256)
    source = sine_input(1.8, cfg.fs * 11 / 256, 0.2)
    noisy = run_pipeline(
        replace(
            cfg,
            driver_noise_rms=1e-3,
            driver_noise_distribution="gaussian_white",
            seed=4,
        ),
        source,
        256,
        **controls,
    )
    internal = noisy.out - noisy.sample.input_v
    entire_chain = noisy.out - noisy.sample.input_clean_v
    np.testing.assert_array_equal(noisy.err, entire_chain)
    np.testing.assert_array_equal(noisy.x_ref, noisy.sample.input_clean_v)
    np.testing.assert_allclose(
        entire_chain - internal, noisy.sample.driver_noise_v, atol=4e-16, rtol=0
    )
    assert np.std(entire_chain) > 100 * np.std(internal)
    assert np.max(abs(internal)) <= cfg.delta2 / (2 * 32) + 2e-12
    assert not noisy.analog_overflow.any()
    assert len(noisy.err) == 256


def test_without_driver_noise_the_two_reference_domains_are_identical(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    result = run_pipeline(cfg, sine_input(1.5, cfg.fs * 3 / 32), 32, **controls)
    np.testing.assert_array_equal(result.sample.input_clean_v, result.sample.input_v)
    np.testing.assert_array_equal(result.sample.driver_noise_v, 0)
    np.testing.assert_array_equal(result.err, result.out - result.sample.input_v)


def test_capture_records_clean_input_before_an_exact_replay_of_the_declared_noise():
    n, seed = 64, 702
    cfg = Config(driver_noise_rms=1e-3, driver_noise_distribution="gaussian_white", seed=seed)
    source = sine_input(0.5, cfg.fs * 3 / n, 0.2)
    ids = np.arange(n)
    result = capture(cfg, source, n, sample_ids=ids)
    expected_noise = np.random.default_rng(seed).normal(0, 1e-3, n)
    np.testing.assert_array_equal(result.input_clean_v, source(result.aperture_s))
    np.testing.assert_array_equal(result.driver_noise_v, expected_noise)
    np.testing.assert_array_equal(result.input_v, result.input_clean_v + expected_noise)
    np.testing.assert_array_equal(result.x_sadc, result.input_v)
    np.testing.assert_array_equal(result.sample_ids, ids)
    assert not result.input_clean_v.flags.writeable


def test_disabled_sampling_sources_do_not_consume_the_supplied_random_stream():
    cfg, n = Config(), 16
    generator = np.random.default_rng(902)
    capture(cfg, sine_input(0.5, cfg.fs / 8), n, sample_ids=np.arange(n), rng=generator)
    np.testing.assert_array_equal(
        generator.normal(size=8), np.random.default_rng(902).normal(size=8)
    )


def test_output_spectrum_retains_driver_noise_in_the_same_declared_toy_pipeline(
    explicit_pipeline_factory,
):
    n, k = 512, 31
    cfg, controls = explicit_pipeline_factory(n_samples=n)
    source = sine_input(1.8, cfg.fs * k / n, 0.2)
    quiet = run_pipeline(cfg, source, n, **controls)
    noisy = run_pipeline(
        replace(cfg, driver_noise_rms=1e-3, driver_noise_distribution="gaussian_white", seed=4),
        source,
        n,
        **controls,
    )
    quiet_sndr = sine_fit_metrics(quiet.out, cfg.fs, cfg.fs * k / n)["SNDR_dB"]
    noisy_sndr = sine_fit_metrics(noisy.out, cfg.fs, cfg.fs * k / n)["SNDR_dB"]
    assert noisy_sndr < quiet_sndr - 20
    assert not quiet.analog_overflow.any() and not noisy.analog_overflow.any()
    np.testing.assert_array_equal(quiet.x_ref, noisy.x_ref)


def test_noise_law_and_scale_cannot_be_implicitly_inferred():
    cfg = Config(driver_noise_rms=1e-3, seed=1)
    with pytest.raises(ConfigError, match="gaussian_white"):
        capture(cfg, sine_input(0.5, 1e6), 4, sample_ids=np.arange(4))
    cfg = Config(driver_noise_distribution="gaussian_white", seed=1)
    with pytest.raises(ConfigError, match="driver noise RMS"):
        capture(cfg, sine_input(0.5, 1e6), 4, sample_ids=np.arange(4))
