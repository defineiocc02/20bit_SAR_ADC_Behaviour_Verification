"""普通逐笔 AZ 的单元测试：N→N、关闭逐位、噪声可复现、droop 需时间。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral import OrdinaryAZConfig, apply_ordinary_az


def test_disabled_is_identity_and_keeps_length():
    x = np.array([0.1, -0.2, 0.3, 0.0, 0.5])
    r = apply_ordinary_az(x, np.arange(5), OrdinaryAZConfig(enabled=False))
    np.testing.assert_array_equal(r.input_v, x)
    np.testing.assert_array_equal(r.error_v, np.zeros(5))
    assert r.input_v.size == 5


def test_offset_is_subtracted_per_sample_and_odd_length_works():
    x = np.arange(5, dtype=float) * 0.1
    r = apply_ordinary_az(x, np.arange(5), OrdinaryAZConfig(enabled=True, offset_v=0.01))
    np.testing.assert_allclose(r.input_v, x - 0.01)
    assert r.input_v.size == 5  # 没有偶奇配对，不降采样


def test_noise_is_reproducible_with_seed():
    x = np.zeros(16)
    ids = np.arange(16)
    a = apply_ordinary_az(x, ids, OrdinaryAZConfig(enabled=True, noise_v=1e-3, seed=7))
    b = apply_ordinary_az(x, ids, OrdinaryAZConfig(enabled=True, noise_v=1e-3, seed=7))
    np.testing.assert_array_equal(a.input_v, b.input_v)
    assert np.any(a.input_v != 0.0)


def test_droop_requires_sample_time():
    with pytest.raises(ValueError):
        apply_ordinary_az(
            np.zeros(4), np.arange(4),
            OrdinaryAZConfig(enabled=True, droop_v_per_s=1e-3),
        )


def test_droop_is_proportional_to_time():
    t = np.array([0.0, 1e-6, 2e-6, 3e-6])
    r = apply_ordinary_az(
        np.zeros(4), np.arange(4),
        OrdinaryAZConfig(enabled=True, droop_v_per_s=1e-3),
        sample_time_s=t,
    )
    np.testing.assert_allclose(r.error_v, 1e-3 * t)


def test_nonfinite_input_is_rejected():
    with pytest.raises(ValueError):
        apply_ordinary_az(
            np.array([0.0, np.nan]), np.arange(2), OrdinaryAZConfig(enabled=True)
        )
