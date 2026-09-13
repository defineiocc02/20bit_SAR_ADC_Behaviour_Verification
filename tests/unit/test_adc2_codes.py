"""Real backend-code range and digital observer boundaries."""

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.adc2 import ADC2
from adi_model.pipeline import run_pipeline
from adi_model.sim import run_sim
from adi_model.sim_split import run_sim_split_reference


def test_all_backend_codes_decode_and_reencode_exactly():
    adc = ADC2(Config.paper_literal())
    codes = np.arange(adc.n_code, dtype=np.int64)
    values = adc.decode_codes(codes)
    actual, over = adc.quantize_codes(values)
    np.testing.assert_array_equal(actual, codes)
    assert not np.any(over)
    edges = np.array([adc.vmin - adc.step, adc.vmin, adc.vmax, adc.vmax + adc.step])
    actual, over = adc.quantize_codes(edges)
    np.testing.assert_array_equal(actual, [0, 0, adc.n_code - 1, adc.n_code - 1])
    np.testing.assert_array_equal(over, [True, False, False, True])


@pytest.mark.parametrize("runner", [run_pipeline, run_sim, run_sim_split_reference])
def test_raw_code_is_observable_before_digital_correction(runner):
    cfg = Config(ktc_enable=True, ktc_noise_n=1e-5)
    r = runner(cfg, sine_input(1.2, cfg.fs * 31 / 1024), 1024)
    assert np.issubdtype(r.adc2_code.dtype, np.integer)
    np.testing.assert_allclose(
        ADC2(r.cfg).decode_codes(r.adc2_code) - r.state.kappa * r.vnc, r.fine, atol=0, rtol=0
    )


@pytest.mark.parametrize("v", [np.nan, np.inf, -np.inf])
def test_nonfinite_analog_input_cannot_become_a_valid_code(v):
    with pytest.raises(ValueError, match="finite"):
        ADC2(Config()).quantize_codes([v])


@pytest.mark.parametrize("code", [[0.0], [-1], [2**14], [True]])
def test_malformed_digital_buffers_are_refused(code):
    with pytest.raises(ValueError, match="codes must be integers"):
        ADC2(Config()).decode_codes(code)
