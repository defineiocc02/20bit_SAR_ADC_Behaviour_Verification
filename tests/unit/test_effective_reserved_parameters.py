"""检查有效参数由实际消费者决定，旧预留字段不再静默接受。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.simulation.config import Config
from adi_model.simulation.pipeline import run_pipeline
from adi_model.behavioral.sampler import capture, dc_input


def test_retired_sadc_ratio_is_rejected_rather_than_reported_as_an_accepted_override():
    with pytest.raises(TypeError):
        Config.from_dict({"sadc_cap_ratio": 0.9})


def test_unused_sadc_capacitance_is_recorded_without_changing_static_conversion(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    small = run_pipeline(replace(cfg, c_sadc=1e-12), dc_input(0.2), 16, **controls)
    large = run_pipeline(replace(cfg, c_sadc=4e-12), dc_input(0.2), 16, **controls)
    np.testing.assert_array_equal(small.adc2_code, large.adc2_code)
    np.testing.assert_array_equal(small.out, large.out)
    for result in (small, large):
        metadata = result.effective_config
        assert metadata["requested"]["c_sadc"] == result.cfg.c_sadc
        assert metadata["inactive_overrides"]["c_sadc"] == (
            "no sampling-noise or input-network consumer selected"
        )


def test_active_sadc_noise_uses_its_explicit_capacitance_with_a_separate_rdac_source():
    n = 64
    cfg = Config(
        c_sadc=1e-12,
        enable_sampling_noise=True,
        sampling_noise_distribution="gaussian_white",
        temperature_k=300.0,
        chi=2.0,
        seed=72,
    )
    arguments = {"sample_ids": np.arange(n), "rdac_noise_cap_f": np.full(n, 20e-12)}
    small = capture(cfg, dc_input(0.2), n, **arguments)
    large = capture(replace(cfg, c_sadc=4e-12), dc_input(0.2), n, **arguments)
    np.testing.assert_array_equal(small.input_v, large.input_v)
    np.testing.assert_array_equal(small.rdac_noise_v, large.rdac_noise_v)
    np.testing.assert_allclose(large.sadc_noise_v, small.sadc_noise_v / 2, atol=1e-20, rtol=0)
    assert np.any(small.sadc_noise_v != large.sadc_noise_v)
