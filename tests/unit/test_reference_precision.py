"""检查 RMS 与等效位数的换算，不依赖已退役的参考递推模型。"""

import numpy as np
import pytest

from adi_model.metrics import reference_precision_bits


def test_reference_precision_bits_identity():
    sigma = 3.0 / (np.sqrt(12) * 2**20)
    assert reference_precision_bits(sigma, 3.0) == pytest.approx(20)
    assert reference_precision_bits(8 * sigma, 3.0) == pytest.approx(17)
    assert reference_precision_bits(1e-6, 3.0) == pytest.approx(19.724, abs=1e-3)


def test_constant_error_is_retained_by_rms_when_std_is_zero():
    errors = np.full(16, 1e-3)
    rms = np.sqrt(np.mean(errors**2))
    assert errors.std() == 0 and rms == pytest.approx(1e-3)
    expected = np.log2(3 / (np.sqrt(12) * rms))
    assert reference_precision_bits(rms, 3) == pytest.approx(expected)


@pytest.mark.parametrize(
    "sigma,span",
    [
        (0, 3),
        (-1e-6, 3),
        (1e-6, 0),
        (1e-6, -3),
        (np.nan, 3),
        (np.inf, 3),
        (1e-6, np.nan),
        (1e-6, np.inf),
    ],
)
def test_nonpositive_or_nonfinite_precision_inputs_are_refused(sigma, span):
    with pytest.raises(ValueError):
        reference_precision_bits(sigma, span)


def test_voltage_rescaling_does_not_change_equivalent_bits():
    assert reference_precision_bits(4e-6, 12) == pytest.approx(reference_precision_bits(1e-6, 3))
