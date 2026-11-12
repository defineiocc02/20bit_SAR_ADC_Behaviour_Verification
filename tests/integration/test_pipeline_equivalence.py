"""检查唯一入口的确定性与组件边界，不使用旧运行器别名。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.simulation.config import ConfigError
from adi_model.simulation.pipeline import run_pipeline
from adi_model.behavioral.sadc import build_first_stage_quantizer
from adi_model.behavioral.sampler import dc_input, sine_input


def test_repeated_explicit_run_is_bitwise_reproducible(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    source = sine_input(1.0, cfg.fs * 3 / 16, 0.2)
    before = cfg.to_dict()
    first = run_pipeline(cfg, source, 16, **controls)
    second = run_pipeline(cfg, source, 16, **controls)
    for name in ("coarse", "k", "vra", "adc2_code", "out", "err", "main_masks", "conv_slice_ids"):
        np.testing.assert_array_equal(getattr(first, name), getattr(second, name))
    assert cfg.to_dict() == before
    assert cfg.dac_n_main is None and cfg.dac_n_sub is None  # 实际几何由独立输入提供。


def test_explicit_threshold_constructor_is_independent_of_rdac_truth(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    adc = build_first_stage_quantizer(cfg)
    expected = np.asarray(cfg.sadc_thresholds_v)
    assert adc.n_code == 512 and len(expected) == 513
    np.testing.assert_array_equal(adc.thresholds, expected)
    assert not adc.thresholds.flags.writeable
    with pytest.raises(TypeError):
        build_first_stage_quantizer(cfg, controls["pool"].chip)


def test_backend_quantization_error_is_bounded_by_the_explicit_step(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    result = run_pipeline(cfg, sine_input(1.0, cfg.fs * 3 / 16, 0.2), 16, **controls)
    assert not result.analog_overflow.any()
    expected = (result.fine - result.vra) / 32
    np.testing.assert_allclose(result.err, expected, rtol=0, atol=2e-12)
    assert np.max(abs(expected)) <= cfg.delta2 / 64 + 1e-15


def test_uint32_wrap_is_preserved_without_restart_or_implicit_ids(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    ids = np.array([2**32 - 2, 2**32 - 1, 0, 1], dtype=np.uint32)
    result = run_pipeline(cfg, dc_input(0.2), 4, **{**controls, "sample_ids": ids})
    np.testing.assert_array_equal(result.sample_id, ids)
    np.testing.assert_array_equal(result.digital_observation.sample_ids, ids)
    np.testing.assert_array_equal(result.held_sample_ids, np.broadcast_to(ids[:, None], (4, 8)))


@pytest.mark.parametrize("ids", [[0, 0, 1, 2], [0, 2, 1, 3], [-1, 0, 1, 2], [0.5, 1, 2, 3]])
def test_invalid_sample_identity_cannot_be_repaired(explicit_pipeline_factory, ids):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    with pytest.raises(ValueError):
        run_pipeline(cfg, dc_input(0.2), 4, **{**controls, "sample_ids": np.array(ids)})


def test_unsupported_dynamic_acquisition_does_not_degrade_to_point_settling(
    explicit_pipeline_factory,
):
    from adi_model.behavioral.input_network import InputNetworkParameters

    cfg, controls = explicit_pipeline_factory(
        n_samples=4,
        config_changes={
            "dyn_input_settling": True,
            "c_sadc": 2.5e-12,
            "dyn_r_source": 30.0,
            "dyn_r_on": 20.0,
            "dyn_ron_code_coeff": 0.0,
            "input_network": InputNetworkParameters(filter_cap_f=0.0),
        },
    )
    with pytest.raises((ValueError, NotImplementedError), match="binding|phase|implemented"):
        run_pipeline(cfg, dc_input(0.2), 4, **controls)


def test_invalid_target_bits_are_rejected_instead_of_becoming_a_legacy_runner(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    for bits in (6, 7):
        with pytest.raises(ConfigError, match="b1"):
            run_pipeline(replace(cfg, b1=bits), dc_input(0.2), 4, **controls)
