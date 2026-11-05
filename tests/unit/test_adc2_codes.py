"""检查独立 ADC2 的显式码本与节点有效性边界。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.adc2 import ADC2
from adi_model.config import Config, ConfigError


@pytest.mark.parametrize("bits", [1, 2, 3, 4, 12, 20])
def test_all_backend_codes_decode_and_reencode_exactly(adc2_config_factory, bits):
    # 改变位宽只检查数值子模块，不改变目标 ADC 架构。
    adc = ADC2(adc2_config_factory(bits=bits, lo=-1.0, hi=1.0, endpoint="half_open"))
    codes = np.arange(2**bits, dtype=np.int64)
    voltage = adc.decode_codes(codes)
    actual, over = adc.quantize_codes(voltage)
    np.testing.assert_array_equal(actual, codes)
    assert not over.any()
    np.testing.assert_allclose(voltage, -1 + (codes + 0.5) * 2 / 2**bits, rtol=0, atol=0)


@pytest.mark.parametrize("endpoint,upper_over", [("closed", False), ("half_open", True)])
def test_endpoint_policy_changes_validity_without_dropping_codes(
    adc2_config_factory, endpoint, upper_over
):
    adc = ADC2(adc2_config_factory(bits=4, lo=-1.0, hi=1.0, endpoint=endpoint))
    code, over = adc.quantize_codes(np.array([-1.01, -1, 1, 1.01]))
    np.testing.assert_array_equal(code, [0, 0, 15, 15])
    np.testing.assert_array_equal(over, [True, False, upper_over, True])


def test_raw_code_and_analog_range_flags_precede_any_digital_correction(adc2_config_factory):
    adc = ADC2(adc2_config_factory(bits=4, lo=-1.0, hi=1.0, endpoint="half_open"))
    raw, over = adc.quantize_codes(np.array([-0.7, 0.2, 1.1]))
    saved_raw, saved_over = raw.copy(), over.copy()
    corrected = adc.decode_codes(raw) - np.array([100.0, -100.0, 0.0])
    assert np.any(abs(corrected) > 1)
    np.testing.assert_array_equal(raw, saved_raw)
    np.testing.assert_array_equal(over, saved_over)
    np.testing.assert_array_equal(over, [False, False, True])


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_nonfinite_analog_input_cannot_become_a_valid_code(adc2_config_factory, value):
    adc = ADC2(adc2_config_factory(bits=4, lo=-1.0, hi=1.0, endpoint="closed"))
    with pytest.raises(ValueError, match="finite"):
        adc.quantize_codes([value])


@pytest.mark.parametrize("code", [[-1], [16], [1.5], [np.nan], [True]])
def test_malformed_digital_buffers_are_refused(adc2_config_factory, code):
    adc = ADC2(adc2_config_factory(bits=4, lo=-1.0, hi=1.0, endpoint="closed"))
    with pytest.raises(ValueError, match="codes must be integers"):
        adc.decode_codes(code)


@pytest.mark.parametrize(
    "field", ["adc2_n_bits", "adc2_v_min", "adc2_v_max", "adc2_codebook", "adc2_endpoint_policy"]
)
def test_each_consumed_backend_input_is_mandatory(adc2_config_factory, field):
    cfg = adc2_config_factory(bits=4, lo=-1.0, hi=1.0, endpoint="closed")
    with pytest.raises(ConfigError, match=field):
        ADC2(replace(cfg, **{field: None}))


def test_source_output_bits_do_not_imply_backend_bits():
    cfg = Config()
    assert cfg.n_bits_target == 20 and cfg.adc2_n_bits is None
    with pytest.raises(ConfigError, match="adc2_n_bits"):
        ADC2(cfg)
