"""八片负注入模型的迁移冒烟测试：只验证接口/形状/符号，不验证性能。"""

from __future__ import annotations

import numpy as np

from adi_model.behavioral.eight_slice import (
    coarse_decision,
    decode,
    native_step_v,
    words,
)
from adi_model.capacitors import ExperimentConfig


def test_dither_decode_zero_is_zero_injection():
    d = decode(np.array([0, 1, 63], dtype=np.uint8))
    assert d.word.shape == (3,)
    assert np.all(d.total_eighth[0] == 0)
    assert d.total_eighth[2] > 0


def test_dither_words_are_six_bit_and_reproducible():
    enabled = np.ones(16, dtype=bool)
    a = words(enabled, seed=12345)
    b = words(enabled, seed=12345)
    np.testing.assert_array_equal(a, b)
    assert a.dtype == np.uint8
    assert np.all(a < 64)


def test_coarse_decision_returns_same_length_and_valid_flags():
    cfg = ExperimentConfig()
    thresholds = np.linspace(-cfg.full_scale_peak_v, cfg.full_scale_peak_v, 513)
    d = decode(np.zeros(4, dtype=np.uint8))
    code, command, valid = coarse_decision(
        np.zeros(4), d, thresholds, native_step_v(cfg)
    )
    assert code.shape == command.shape == valid.shape == (4,)
    assert valid.dtype == bool
