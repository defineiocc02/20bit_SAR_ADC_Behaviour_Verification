"""检查量化后的数字重构不改变模拟码，以及给定观测矩阵的可辨识性。

旧 KTC 观察器及 7/6 位扫描秩公式已退役。保留其一般性目标：数字修正不占
ADC2 窗口、异常标志不得由校准抹除、秩必须由实际激励推导而非用架构标签指定。
"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.adc2 import ADC2
from adi_model.calib import group_aggregate, observability, required_samples
from adi_model.fixed_point import FixedPointReconstructor


def test_large_post_quantization_offset_does_not_rewrite_backend_or_analog_flags(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=8)
    original_codes = data.adc2_code.copy()
    original_flags = data.overflow.copy()
    baseline = FixedPointReconstructor.from_weights(
        data.spec, np.array([[0.25, 0.35, 0.4]]), 0.0, format=explicit_format
    ).reconstruct(data)
    corrected = FixedPointReconstructor.from_weights(
        data.spec, np.array([[0.25, 0.35, 0.4]]), 100.0, format=explicit_format
    ).reconstruct(data)
    np.testing.assert_array_equal(data.adc2_code, original_codes)
    np.testing.assert_array_equal(data.overflow, original_flags)
    np.testing.assert_array_equal(corrected.analog_overflow, original_flags)
    assert not baseline.clipped_low.any() and not baseline.clipped_high.any()
    assert corrected.clipped_low.all()
    assert not corrected.clipped_high.any()
    assert len(corrected.code) == len(original_codes)


def test_analog_bad_samples_remain_visible_under_digital_weight_application(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=3)
    bad = np.zeros(len(data.adc2_code), dtype=bool)
    bad[[0, -1]] = True
    observed = replace(data, overflow=bad, static_charge_domain=False)
    decoder = FixedPointReconstructor.from_weights(
        data.spec, np.array([[0.25, 0.35, 0.4]]), 0.0, format=explicit_format
    )
    words = decoder.reconstruct(observed)
    np.testing.assert_array_equal(words.analog_overflow, bad)
    assert len(words.code) == len(bad)
    assert not words.clipped_low.any() and not words.clipped_high.any()


def test_backend_overflow_is_measured_at_its_input_before_digital_processing(adc2_config_factory):
    adc = ADC2(adc2_config_factory(bits=8, lo=-1.0, hi=1.0, endpoint="closed"))
    voltage = np.array([-1.1, -1.0, 0.0, 1.0, 1.1])
    codes, overflow = adc.quantize_codes(voltage)
    np.testing.assert_array_equal(overflow, [True, False, False, False, True])
    np.testing.assert_array_equal(codes, [0, 0, 128, 255, 255])
    np.testing.assert_array_equal(voltage, [-1.1, -1.0, 0.0, 1.0, 1.1])


def test_rank_follows_the_actual_design_matrix_not_a_slice_count_formula():
    design = np.array([[1.0, 0.0, 1.0, 0.0], [0.0, 1.0, 0.0, 1.0]])
    full = observability(design)
    assert full.n_parameters == 4 and full.rank == 2
    assert full.identifiable_fraction == 0.5 and np.isinf(full.cond)
    assert full.as_dict()["n_null_directions"] == 2
    grouped = group_aggregate(design, np.array([0, 1, 0, 1]))
    np.testing.assert_array_equal(grouped, [[2.0, 0.0], [0.0, 2.0]])
    aggregate = observability(grouped)
    assert aggregate.rank == aggregate.n_parameters == 2
    assert aggregate.cond == pytest.approx(1.0)
    assert full.rank < full.n_parameters


def test_missing_excitation_cannot_be_fixed_by_more_repetitions():
    for repetitions in (1, 8):
        design = np.tile([[1.0, 1.0]], (repetitions, 1))
        result = required_samples(design, 0.1, 0.01, oversample=2.0, sample_rate_hz=40e6)
        assert not result["feasible"]
        assert result["rank"] == 1 and result["n_parameters"] == 2
        assert np.isinf(result["recommended_samples"])


def test_conditional_sample_budget_matches_independent_covariance_scale():
    design = np.array([[1.0, 1.0], [1.0, -1.0]])
    result = required_samples(design, 0.1, 0.01, oversample=1.0, sample_rate_hz=40e6)
    assert result["feasible"]
    assert result["noise_bound_samples"] == pytest.approx(100.0)
    assert result["duration_s"] == pytest.approx(100 / 40e6)
    repeated = required_samples(
        np.tile(design, (4, 1)), 0.1, 0.01, oversample=1.0, sample_rate_hz=40e6
    )
    assert repeated["noise_bound_samples"] == pytest.approx(result["noise_bound_samples"])
    noisy = required_samples(design, 0.2, 0.01, oversample=1.0, sample_rate_hz=40e6)
    assert noisy["noise_bound_samples"] == pytest.approx(4 * result["noise_bound_samples"])


@pytest.mark.parametrize("bad", [np.array([1.0, 2.0]), np.array([[1.0, np.nan]])])
def test_invalid_observation_matrix_is_rejected(bad):
    with pytest.raises(ValueError):
        observability(bad)
