# =============================================================================
# 文件名     : test_adctoolbox_calibration.py
# 模块名     : test_adctoolbox_calibration
# 版本       : v1.0
# 功能       : 验证前台校准的码仓边界、秩约束与 NLMS 留出行为。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""验证前台校准的码仓边界、秩约束与 NLMS 留出行为。"""

from dataclasses import replace

import numpy as np
import pytest

from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    SwitchState,
    _fit_gain,
    _fit_nlms,
    _schedule_report,
    decode_switches,
    fixed_ab_plan,
    fixed_switch_bits,
    quantize,
    range_report,
)
from task1.model.codebook import map_record
from task1.model.scheduler import GROUP_SLICES, POOL_SLICES


# =========================================================================
# -------------------------------------------------------------------------
# 最上侧有效点属于最大码仓，上边界本身报错。
# 同一模拟观察在默认窗口计数越界，在另一个候选窗口保持零越界。
# 逐笔学习后的系数在独立开关序列上保持收敛。
# -------------------------------------------------------------------------
def test_residue_observation_keeps_valid_code_range() -> None:
    """最上侧有效点属于最大码仓，上边界本身报错。"""
    half = 0.05
    values = np.array([-half, np.nextafter(half, -np.inf)])
    codes = quantize(values, 12, half)
    assert codes.tolist() == [0, 4095]
    np.testing.assert_allclose(
        np.abs(decode_switches(codes, 12, half) - values), half / 4096, atol=1e-17
    )
    with pytest.raises(ValueError, match="out of range"):
        quantize(np.array([half]), 12, half)


def test_adc2_headroom_preserves_distinct_window_outcomes() -> None:
    """同一模拟观察在默认窗口计数越界，在另一个候选窗口保持零越界。"""
    voltage = np.array([-0.051, -0.05, 0.04, 0.05, 0.06])
    default = range_report(voltage, 0.05)
    candidate = range_report(voltage, 0.1)
    assert default["samples"] == candidate["samples"] == 5
    assert default["min_v"] == candidate["min_v"] == -0.051
    assert default["max_v"] == candidate["max_v"] == 0.06
    assert (default["below_range"], default["at_or_above_range"]) == (1, 2)
    assert (candidate["below_range"], candidate["at_or_above_range"]) == (0, 0)


def test_nlms_reconstructs_independent_switch_patterns() -> None:
    """逐笔学习后的系数在独立开关序列上保持收敛。"""
    training_rng = np.random.default_rng(18)
    train = training_rng.integers(0, 2, size=(256, 4))
    known_v = np.array([90.0, -40.0, 25.0, -50.0]) * 1e-6
    target_v = train @ known_v
    actual_v, rank, learning = _fit_nlms(train, target_v, CalibrationConfig())
    held_out = np.random.default_rng(22).integers(0, 2, size=(128, 4))
    assert rank["rank"] == 4
    assert learning[-1]["training_residual_rms_v"] < learning[0]["training_residual_rms_v"] / 1000
    assert np.max(np.abs(held_out @ (actual_v - known_v))) < 1e-8


# =========================================================================
# 2. 函数组：test_nlms_refuses_inseparable_mask_columns / test_gain_fit_refuses_a_single_repeated_slice_group / test_fixed_ab_only_observes_two_groups
# =========================================================================
# -------------------------------------------------------------------------
# 两个始终同开同关的单元不产生各自可验收的权重。
# 重复两点采集并不能分离一个固定组的片增益。
# 固定 A/B 的增益激励只有两个独立组，备用片从未进入观察。
# -------------------------------------------------------------------------
def test_nlms_refuses_inseparable_mask_columns() -> None:
    """两个始终同开同关的单元不产生各自可验收的权重。"""
    toggled = np.tile((0, 1), 32)
    bits = np.column_stack((toggled, toggled))
    with pytest.raises(ValueError, match="does not identify"):
        _fit_nlms(bits, toggled * 1e-3, CalibrationConfig())


def test_gain_fit_refuses_a_single_repeated_slice_group() -> None:
    """重复两点采集并不能分离一个固定组的片增益。"""
    cfg = replace(CalibrationConfig(), adc2_half_range_v=0.1)
    plan = np.tile(np.arange(GROUP_SLICES), (POOL_SLICES * 2, 1))
    high_v = np.full(len(plan), 0.02)
    low_v = -high_v
    high = quantize(high_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    low = quantize(low_v, cfg.adc2_bits, cfg.adc2_half_range_v)
    with pytest.raises(ValueError, match="does not identify"):
        _fit_gain(plan, low_v, high_v, low, high, cfg)


def test_fixed_ab_only_observes_two_groups() -> None:
    """固定 A/B 的增益激励只有两个独立组，备用片从未进入观察。"""
    fixed = np.arange(GROUP_SLICES)[None, :] + (np.arange(64) % 2)[:, None] * GROUP_SLICES
    result = _schedule_report(fixed)
    assert result["observed_slice_ids"] == list(range(2 * GROUP_SLICES))
    assert result["gain_observability_from_plan_alone"]["rank"] == 2


# =========================================================================
# 3. 函数组：test_no_dem_switch_training_identifies_group_aggregate_columns
# =========================================================================
# -------------------------------------------------------------------------
# 无 DEM 固定组选通可拟合 40 个组权重，且须拒绝独立片位图。
# -------------------------------------------------------------------------
def test_no_dem_switch_training_identifies_group_aggregate_columns() -> None:
    """无 DEM 固定组选通可拟合 40 个组权重，且须拒绝独立片位图。"""
    codes = np.repeat(np.arange(512, dtype=np.uint16), 2)
    phases = np.zeros(len(codes), dtype=np.uint8)
    main, sub = map_record(codes, (phases, phases), dem_enable=False)
    state = SwitchState(fixed_ab_plan(len(codes)), codes, main, sub)
    features = fixed_switch_bits(state)
    assert features.shape == (1024, 40)
    assert np.linalg.matrix_rank(features) == 40

    main = main.copy()
    main[0, 0] = 1
    with pytest.raises(ValueError, match="same mask"):
        fixed_switch_bits(replace(state, main=main))
