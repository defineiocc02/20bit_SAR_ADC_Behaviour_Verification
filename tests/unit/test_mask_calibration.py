"""DAC 校准模块的单元测试：逐码修正可用、逐单元权值在本观测下不可辨识。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.simulation.config import ConfigError
from adi_model.calibration.mask_calibration import (
    CodeCorrection,
    _mask_matrix,
    fit_code_correction,
    fit_mask_weights,
)
from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation


def _spec(n_active: int, n_main: int, n_sub: int, n_slices: int = 3) -> CalibrationSpec:
    # ADC2 用 24 bit：测试要的是"模型成立时能精确反解"，量化要小到不干扰判据。
    return CalibrationSpec(
        n_slices, n_active, n_main, n_sub, 3.0, 24, -1.0, 1.0, "uniform_midrise", "half_open"
    )


def _observation(masks_main, masks_sub, ids, fine_v, *, overflow=None) -> DigitalObservation:
    """按目标 fine_v 反算 ADC2 码；`fine_v` 是属性，不能直接塞进构造器。"""
    n = len(fine_v)
    n_main, n_sub = masks_main.shape[-1], masks_sub.shape[-1]
    spec = _spec(np.asarray(masks_main).shape[1], n_main, n_sub)
    code = np.floor((np.asarray(fine_v, dtype=float) - spec.adc2_v_min) / spec.adc2_step_v)
    code = np.clip(code, 0, 2**spec.adc2_n_bits - 1).astype(np.int64)
    return DigitalObservation(
        spec=spec,
        slice_ids=np.asarray(ids, dtype=np.int64),
        adc2_code=code,
        main_masks=np.asarray(masks_main, dtype=bool),
        sub_masks=np.asarray(masks_sub, dtype=bool),
        signal_mask=np.ones((n, np.asarray(masks_main).shape[1], n_main + n_sub), dtype=bool),
        known_voltage_term_v=np.zeros((n, np.asarray(masks_main).shape[1], n_main + n_sub)),
        overflow=np.zeros(n, dtype=bool) if overflow is None else overflow,
        sample_ids=np.arange(n, dtype=np.int64),
        static_charge_domain=True,
    )


def test_code_correction_recovers_constant_per_code_error():
    codes = np.repeat(np.arange(4), 5)
    truth = np.array([0.0, 1e-3, -2e-3, 3e-3])
    estimated = truth[codes] + 0.25
    table = fit_code_correction(codes, estimated, np.full(codes.size, 0.25), n_codes=4)
    assert np.allclose(table.table_v, truth, atol=1e-12)
    assert table.residual_rms_v == pytest.approx(0.0, abs=1e-12)
    assert table.covered_codes == 4
    assert table.min_count == 5


def test_code_correction_rejects_unvisited_codes():
    codes = np.array([0, 0, 2, 2])
    with pytest.raises(ConfigError, match="never visited"):
        fit_code_correction(codes, np.zeros(4), np.zeros(4), n_codes=4)


def test_code_correction_apply_and_bounds():
    codes = np.tile(np.arange(4), 3)
    table = fit_code_correction(codes, np.array([0.1, 0.2, 0.3, 0.4] * 3), np.zeros(12), n_codes=4)
    corrected = table.apply(np.array([0, 1, 2, 3]), np.array([0.1, 0.2, 0.3, 0.4]))
    assert np.allclose(corrected, 0.0, atol=1e-12)
    with pytest.raises(ConfigError):
        table.apply(np.array([4]), np.array([0.0]))
    with pytest.raises(ConfigError):
        table.apply(np.array([0, 1]), np.array([0.0]))


def test_code_correction_rejects_negative_counts():
    with pytest.raises(ConfigError):
        CodeCorrection(np.zeros(3), np.array([1, -1, 1]), 3, 1, 0.0, 3)


def test_mask_matrix_layout_is_slice_major():
    ids = np.array([[0, 1]])
    main = np.zeros((1, 2, 3), dtype=bool)
    main[0, 1, 2] = True
    sub = np.zeros((1, 2, 2), dtype=bool)
    sub[0, 0, 1] = True
    matrix = _mask_matrix(_observation(main, sub, ids, np.zeros(1)))
    assert matrix.shape == (1, 2 * (3 + 2))
    assert matrix[0, 1 * 5 + 2] == 1.0  # slice 1, main unit 2
    assert matrix[0, 0 * 5 + 3 + 1] == 1.0  # slice 0, sub unit 1
    assert matrix.sum() == 2.0


def test_mask_weights_are_exact_when_the_model_holds():
    generator = np.random.default_rng(7)
    n, n_slices = 400, 3
    n_main, n_sub = 4, 2
    ids = generator.integers(0, n_slices, (n, 1))
    main = generator.integers(0, 2, (n, 1, n_main))
    sub = generator.integers(0, 2, (n, 1, n_sub))
    sub[:, 0, -1] = 0  # 末位恒低，模拟不可辨识列
    x = generator.uniform(-3, 3, n)
    # 系数要让 fine 落在 ADC2 的 [-0.05,0.45] 内，否则观测被夹码，拟合无意义
    a_true = 0.05
    b_true = generator.uniform(1e-3, 3e-3, n_slices * (n_main + n_sub))
    matrix = _mask_matrix(_observation(main, sub, ids, np.zeros(n)))
    fine = a_true * x + matrix @ b_true + 0.01
    observation = _observation(main, sub, ids, fine)
    calibration = fit_mask_weights(observation, x)
    assert calibration.dropped_columns == n_slices  # 每片的末位 sub 单元
    assert calibration.gain_v_per_v == pytest.approx(a_true, rel=1e-5)
    # 残差只剩 24bit ADC2 的量化（约 30 nV）
    assert calibration.residual_rms_v < 1e-7
    live = np.ptp(matrix, axis=0) > 0
    assert np.allclose(calibration.weights_v[live], b_true[live], atol=1e-7)
    rebuilt = calibration.reconstruct(observation)
    assert np.allclose(rebuilt, x, atol=2e-5)


def test_mask_weights_reject_rank_deficient_design():
    n, n_slices = 40, 2
    ids = np.zeros((n, 1), dtype=np.int64)
    main = np.zeros((n, 1, 2), dtype=bool)  # 全部恒 0：无信息
    sub = np.zeros((n, 1, 2), dtype=bool)
    with pytest.raises(ConfigError, match="cannot identify|do not identify"):
        fit_mask_weights(
            _observation(main, sub, ids, np.zeros(n)), np.zeros(n)
        )


def test_mask_weights_reject_overflow_and_small_records():
    n = 10
    ids = np.zeros((n, 1), dtype=np.int64)
    main = np.ones((n, 1, 2), dtype=bool)
    sub = np.zeros((n, 1, 2), dtype=bool)
    overflow = np.zeros(n, dtype=bool)
    overflow[3] = True
    with pytest.raises(ConfigError, match="clipped"):
        fit_mask_weights(_observation(main, sub, ids, np.zeros(n), overflow=overflow), np.zeros(n))
    with pytest.raises(ConfigError, match="identify"):
        fit_mask_weights(_observation(main, sub, ids, np.zeros(n)), np.zeros(n))
