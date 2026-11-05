"""检查请求扰动幅度与结构接口实际生成的扰动可以区分。"""

from dataclasses import replace

import numpy as np

from adi_model import Config, dc_input
from adi_model.pipeline import run_pipeline


def _capture(factory, amplitude):
    cfg = factory(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dither_mode="quantizer",
        dither_amplitude_lsb1=amplitude,
        seed=42,
    )
    return run_pipeline(cfg, dc_input(0.7), 128)


def test_structural_fixed_sequence_reports_inactive_request_and_actual_voltage():
    low = _capture(Config.rtl_structural, 0.25)
    high = _capture(Config.rtl_structural, 4)
    np.testing.assert_array_equal(low.sample.dither, high.sample.dither)
    np.testing.assert_array_equal(low.adc2_code, high.adc2_code)
    np.testing.assert_array_equal(low.to_codes().code, high.to_codes().code)
    for raw in (low, high):
        metadata = raw.effective_config
        assert metadata["requested"]["dither_amplitude_lsb1"] == raw.cfg.dither_amplitude_lsb1
        assert "fixed D=2 LFSR" in metadata["inactive_overrides"]["dither_amplitude_lsb1"]
        actual = metadata["dither_sequence"]
        assert actual["source"] == "rtl16_fixed_D2_lfsr"
        assert actual["code_abs_bound"] == 2
        assert actual["voltage_node"] == "SADC input injection d_Q"
        assert actual["nominal_voltage_abs_bound_v"] == 2 * raw.cfg.nominal_rdac_step
        assert actual["nominal_voltage_abs_bound_lsb1"] == 2
        assert actual["observed_voltage_min_v"] == raw.sample.dither.min()
        assert actual["observed_voltage_max_v"] == raw.sample.dither.max()
        assert np.max(np.abs(raw.sample.dither)) <= actual["nominal_voltage_abs_bound_v"]
    # The inspection API also tolerates an empty diagnostic voltage record.
    empty = replace(low, sample=replace(low.sample, dither=np.empty(0)))
    actual = empty.effective_config["dither_sequence"]
    assert actual["observed_voltage_min_v"] is None
    assert actual["observed_voltage_max_v"] is None


def test_generic_quantizer_request_enters_sampler_before_discrete_grid_rounding():
    low = _capture(Config.paper_literal, 0.25)
    high = _capture(Config.paper_literal, 4)
    assert not np.any(low.sample.dither)
    assert np.max(np.abs(high.sample.dither)) > 0
    assert not np.array_equal(low.sample.dither, high.sample.dither)
    for raw in (low, high):
        metadata = raw.effective_config
        assert "dither_amplitude_lsb1" not in metadata["inactive_overrides"]
        assert metadata["dither_sequence"] is None
        assert metadata["requested"]["dither_amplitude_lsb1"] == raw.cfg.dither_amplitude_lsb1
