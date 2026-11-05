"""检查预留比例与当前生效的 SADC 电容分开报告。"""

from dataclasses import replace

import numpy as np

from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.slice_pool import PhysicalSlicePool


def test_reserved_sadc_ratio_is_inactive_while_c_sadc_changes_physical_sampling_load():
    cfg = Config.paper_literal(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dyn_input_settling=True,
        dyn_r_source=70,
        sadc_cap_ratio=0.01,
        seed=42,
    )
    pool = PhysicalSlicePool(cfg, np.random.default_rng(91))
    fn = sine_input(0.7, cfg.fs * 3 / 128)
    low = run_pipeline(cfg, fn, 128, pool=pool, rng=np.random.default_rng(92))
    high = run_pipeline(
        replace(cfg, sadc_cap_ratio=0.9), fn, 128, pool=pool, rng=np.random.default_rng(92)
    )
    np.testing.assert_array_equal(low.adc2_code, high.adc2_code)
    np.testing.assert_array_equal(low.to_codes().code, high.to_codes().code)
    np.testing.assert_array_equal(low.acquisition_voltage, high.acquisition_voltage)
    for result in (low, high):
        metadata = result.effective_config
        assert metadata["requested"]["sadc_cap_ratio"] == result.cfg.sadc_cap_ratio
        assert metadata["inactive_overrides"]["sadc_cap_ratio"] == (
            "reserved; SADC noise and physical split sampling load use c_sadc"
        )
    active = run_pipeline(
        replace(cfg, c_sadc=4e-12), fn, 128, pool=pool, rng=np.random.default_rng(92)
    )
    assert not np.array_equal(low.acquisition_voltage, active.acquisition_voltage)
    assert not np.array_equal(low.adc2_code, active.adc2_code)
    assert not np.array_equal(low.to_codes().code, active.to_codes().code)
