"""使用显式系数、选通和数值格式检查整数算术。

三权重观测是独立译码恒等式，不是三切片 ADC 架构，也不表示物理后级为 20 位。
"""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from adi_model.contracts.fixed_point import FixedPointFormat, FixedPointReconstructor, round_even_divide
from adi_model.calibration.weight_calibration import DigitalObservation


@pytest.mark.parametrize(
    "n,expected", [(-7, -4), (-5, -2), (-3, -2), (-1, 0), (1, 0), (3, 2), (5, 2), (7, 4)]
)
def test_signed_ties_to_even(n, expected):
    assert round_even_divide(n, 2) == expected


@pytest.mark.parametrize("denominator", [0, -1])
def test_rounding_rejects_a_nonpositive_denominator(denominator):
    with pytest.raises(ValueError, match="positive"):
        round_even_divide(7, denominator)


def test_every_twenty_bit_word_and_code_width_without_a_huge_analog_record(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=20)
    model = FixedPointReconstructor.from_weights(
        data.spec, [[0.5, 0.25, 0.25]], 0.0, format=explicit_format
    )
    result = model.reconstruct(data)
    np.testing.assert_array_equal(result.code, np.arange(2**20))
    assert np.all(np.diff(result.voltage) == 6 / 2**20)
    assert result.voltage[0] == -3 + 3 / 2**20
    assert result.voltage[-1] == 3 - 3 / 2**20
    assert not np.any(result.clipped_low | result.clipped_high | result.analog_overflow)
    assert result.peak_accumulator_bits <= explicit_format.accumulator_bits


def test_output_saturation_is_separate_from_analog_saturation(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=4)
    term = np.zeros((16, 1, 3))
    term[0], term[-1] = 0.4, -0.4
    flags = np.zeros(16, bool)
    flags[8] = True
    model = FixedPointReconstructor.from_weights(
        data.spec, [[0.5, 0.25, 0.25]], 0.0, format=explicit_format
    )
    result = model.reconstruct(replace(data, known_voltage_term_v=term, overflow=flags))
    assert np.flatnonzero(result.clipped_low).tolist() == [0]
    assert np.flatnonzero(result.clipped_high).tolist() == [15]
    assert np.flatnonzero(result.analog_overflow).tolist() == [8]
    assert result.code[0] == 0 and result.code[-1] == 2**20 - 1


def test_accumulator_overflow_is_an_error_instead_of_a_wrapped_word(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=4)
    model = FixedPointReconstructor.from_weights(
        data.spec, [[0.5, 0.25, 0.25]], 0.0, format=explicit_format
    )
    with pytest.raises(OverflowError, match="accumulator"):
        replace(model, format=replace(explicit_format, accumulator_bits=64)).reconstruct(data)


def test_register_image_roundtrip_and_illegal_widths(identity_record_factory, explicit_format):
    data = identity_record_factory(bits=4)
    model = FixedPointReconstructor.from_weights(
        data.spec, [[0.5, 0.25, 0.25]], 0.0, format=explicit_format
    )
    restored = FixedPointReconstructor.from_dict(model.to_dict())
    np.testing.assert_array_equal(restored.reconstruct(data).code, model.reconstruct(data).code)
    with pytest.raises(ValueError, match="weights"):
        replace(model, weights_q=np.zeros((1, 3), np.int64))
    with pytest.raises(TypeError):
        FixedPointFormat()
    with pytest.raises(ValueError):
        replace(explicit_format, output_bits=18)


def test_format_and_offset_cannot_be_supplied_by_hidden_defaults(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=4)
    with pytest.raises(TypeError):
        FixedPointReconstructor.from_weights(data.spec, [[0.5, 0.25, 0.25]])
    with pytest.raises(TypeError):
        FixedPointReconstructor.from_weights(data.spec, [[0.5, 0.25, 0.25]], 0.0)
    with pytest.raises(ValueError, match="explicit"):
        FixedPointReconstructor.from_weights(data.spec, [[0.5, 0.25, 0.25]], 0.0, format=None)
    assert explicit_format.output_bits == 20


def test_unknown_result_cannot_generate_nominal_coefficients(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=4)
    result = SimpleNamespace(
        digital_observation=data,
        adc2_code=data.adc2_code,
        sample_id=data.sample_ids,
        state=SimpleNamespace(weight_calibration=None),
    )
    with pytest.raises(ValueError, match="frozen effective"):
        FixedPointReconstructor.from_result(result, format=explicit_format)
    with pytest.raises(ValueError, match="explicitly bound"):
        DigitalObservation.from_result(SimpleNamespace())


def test_each_voltage_term_is_quantized_before_weighted_sum(identity_record_factory):
    data = identity_record_factory(bits=4)
    # 显式粗电压算术使逐单元舍入差异可观测。
    fmt = FixedPointFormat(8, 4, 16, 16, 64)
    term = np.full((16, 1, 3), 0.49 * 3 / 16)
    model = FixedPointReconstructor.from_weights(data.spec, [[1.0, 1.0, 1.0]], 0.0, format=fmt)
    result = model.reconstruct(replace(data, known_voltage_term_v=term))
    # 各项独立舍入均为零；先折叠 3*0.49 会得到不同结果。
    expected_voltage = data.fine_v / 3
    expected = np.floor((expected_voltage / 3 + 1) * 2**19).astype(np.int64)
    np.testing.assert_array_equal(result.code, expected)
    assert round(3 * 0.49) != 3 * round(0.49)


def test_zero_recorded_signal_gain_is_not_a_valid_denominator(
    identity_record_factory, explicit_format
):
    data = identity_record_factory(bits=4)
    model = FixedPointReconstructor.from_weights(
        data.spec, [[0.5, 0.25, 0.25]], 0.0, format=explicit_format
    )
    with pytest.raises(ValueError, match="signal gain"):
        model.reconstruct(replace(data, signal_mask=np.zeros((16, 1, 3), bool)))


@pytest.mark.parametrize(
    "missing", ["main_masks", "sub_masks", "signal_mask", "known_voltage_term_v"]
)
def test_observation_never_infers_missing_connection_records(identity_record_factory, missing):
    data = identity_record_factory(bits=4)
    values = {name: getattr(data, name) for name in data.__dataclass_fields__}
    values.pop(missing)
    with pytest.raises(TypeError):
        DigitalObservation(**values)
