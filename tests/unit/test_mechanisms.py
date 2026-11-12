"""机制内核入口的可用性测试：默认关闭、显式开启、显式 0 不被拒绝。"""

from __future__ import annotations

import numpy as np

from adi_model.mechanisms import (
    DynamicConfig,
    ImpairmentSet,
    NoiseConfig,
    ReferenceConfig,
    TimingConfig,
    settle_error,
    thermal_sigma,
)


def test_defaults_are_disabled():
    assert not NoiseConfig().enabled
    assert not ReferenceConfig().enabled
    assert DynamicConfig().period_s > 0.0
    assert isinstance(ImpairmentSet(), ImpairmentSet)


def test_reference_explicit_zero_precharge_is_accepted():
    cfg = ReferenceConfig(enabled=True, t_ra_s=20e-9, tau_s=1e-9, precharge_frac=0.0)
    assert cfg.precharge_frac == 0.0
    assert settle_error(cfg.tau_s, cfg.t_ra_s, 1.0) >= 0.0


def test_timing_and_noise_kernels_are_finite():
    cfg = TimingConfig(rms_jitter_s=1e-12)
    assert np.isfinite(cfg.rms_jitter_s)
    assert thermal_sigma(1e-12, 300.0) > 0.0
