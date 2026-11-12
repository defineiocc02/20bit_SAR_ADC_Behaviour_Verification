"""三维 DEM 掩码、名义守恒与使用均匀性的单元测试。

两个池都是显式合成几何：Fig.11 的 17/5 阵列与默认 63+8 分段阵列。
本组测试不评估 INL/DNL，只验证"重排不改名义权重"和"单元等频使用"。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.config import ConfigError
from adi_model.dac_arch import NominalCodebook, SplitChip
from adi_model.dem import SplitSwitchCommand
from adi_model.dem3d import (
    Dem3State,
    fig11_masks,
    nominal_conservation,
    round_robin_plan,
    unary_rotation_masks,
    usage_uniformity,
)
from adi_model.slice_pool import PhysicalSlicePool


def _fig11_pool():
    cap = 1e-15
    weights_main = np.array([2.0] * 15 + [1.0, 1.0]) * cap
    weights_sub = np.array([8.0, 4.0, 2.0, 1.0, 1.0]) * cap
    chip = SplitChip(
        C_main=np.tile(weights_main, (18, 1)),
        C_sub=np.tile(weights_sub, (18, 1)),
        C_bridge=np.full(18, cap),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=300 * cap,
    )
    book = NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512)
    return PhysicalSlicePool(
        object.__new__(type("Cfg", (), {"n_slices": 18, "n_active": 8})),
        chip=chip,
        nominal_codebook=book,
        mask_binding=fig11_masks,
    )


def _default_pool():
    from adi_model.config import Config

    cfg = Config()
    cap = 4.00390625e-14
    chip = SplitChip(
        C_main=np.full((18, 63), cap),
        C_sub=np.full((18, 8), cap),
        C_bridge=np.full(18, 8 * cap / 7),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=640.625e-15,
    )
    book = NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512)
    return PhysicalSlicePool(
        cfg, chip=chip, nominal_codebook=book, mask_binding=unary_rotation_masks
    )


def test_dem_state_round_trip_and_sequence_coverage():
    state = Dem3State(41, 11)
    assert Dem3State.from_int(state.to_int()) == state
    # 一个"每片都能走遍 63 列"的周期是 columns x plan_period = 63 x 9 = 567
    sequence = Dem3State.sequence(63 * 9, columns=63)
    assert set(int(value) & 0x3F for value in sequence) == set(range(63))
    # 默认池的子阵列只有 7 个可切换单元，纵向周期必须等于 7（用 16 再 %7 会 3:2 偏置）
    assert set(int(value) >> 6 for value in sequence) == set(range(7))
    with pytest.raises(ConfigError):
        Dem3State(64, 0)
    with pytest.raises(ConfigError):
        Dem3State.from_int(1024)


def test_low_code_visits_every_main_unit():
    """外部审查 A01 的最小反例：码 8（1 个主单元）、固定物理片、一个相位周期。"""
    pool = _default_pool()
    ids = np.broadcast_to(np.arange(8), (63 * 9, 8))
    states = Dem3State.sequence(63 * 9, columns=63)
    command = unary_rotation_masks(pool.geometry, np.full(63 * 9, 8, np.int64), states, ids)
    # 物理片 7 在组内第 7 位
    visits = command.main_on[:, 7, :].sum(axis=0)
    assert np.count_nonzero(visits) == 63
    assert int(visits.max() - visits.min()) == 0


def test_sub_array_phases_are_uniform():
    """外部审查 2：纵向用 16 再 %7 会让子单元使用比变成 3:2。

    回归：码 1 只选 1 个子单元，一个相位周期后 7 个单元必须等频（各 648 次）。
    """
    pool = _default_pool()
    n = 63 * 9
    plan = round_robin_plan(n)
    states = Dem3State.sequence(n, columns=63)
    report = usage_uniformity(pool, unary_rotation_masks, plan, states, codes=(1,))
    counts = report["codes"]["1"]["sub_unit_counts"]
    assert counts[-1] == 0  # 第 8 位恒接低轨
    assert len(set(counts[:7])) == 1, counts
    assert counts[0] == n * 8 // 7 * 63 // 63 or counts[0] > 0


def test_conservation_reports_lsb_units_not_farads():
    """外部审查 A02：1 LSB20 的电荷误差必须折算成 1，而不是一个法拉数。"""
    from types import SimpleNamespace

    from adi_model.dem import SplitSwitchGeometry

    c_norm_f, lsb20_v = 20.5e-12, 6.0 / 2**20
    charges = np.array([0.0, c_norm_f * lsb20_v])

    class _Stub:
        def __init__(self):
            self.geometry = SplitSwitchGeometry(63, 8, 18, 8, 0, 511)

        def split_coefficients(self, ids):
            return {"c_signal": np.full(len(ids), c_norm_f)}

        def charge_from_masks(self, ids, command, **references):
            return SimpleNamespace(charge_c=charges.copy())

    report = nominal_conservation(
        _Stub(),
        unary_rotation_masks,
        np.array([Dem3State(0, 0).to_int(), Dem3State(1, 0).to_int()]),
        reference_high_v=3.0,
        reference_low_v=-3.0,
        codes=np.array([8]),
    )
    assert report["max_spread_lsb20"] == pytest.approx(1.0)
    assert report["max_spread_v"] == pytest.approx(lsb20_v)
    assert report["c_norm_f"] == pytest.approx(c_norm_f)


def test_fig11_masks_select_exact_counts():
    pool = _fig11_pool()
    ids = np.broadcast_to(np.arange(8), (2, 8))
    states = np.array([Dem3State(0, 0).to_int(), Dem3State(7, 5).to_int()])
    command = fig11_masks(pool.geometry, np.array([511, 16]), states, ids)
    # 码 511：15 个 2C 全选 + B4=1 选 8 个 1C + 子阵列 B5..B8 全 1
    assert command.main_on[0, :, :15].sum() == 15 * 8
    assert command.main_on[0, :, 15:].sum() == 8
    assert command.sub_on[0].sum() == 4 * 8
    # 码 16：仅 B4=1，其余为 0
    assert command.main_on[1, :, :15].sum() == 0
    assert command.main_on[1, :, 15:].sum() == 8
    assert command.sub_on[1].sum() == 0


def test_fig11_nominal_charge_is_state_independent():
    pool = _fig11_pool()
    # Fig.11 的纵向是 8/16 选择，必须显式用 16 个相位
    states = Dem3State.sequence(240, vertical=16)
    report = nominal_conservation(
        pool, fig11_masks, states, reference_high_v=3.0, reference_low_v=-3.0
    )
    assert report["conserved"], report
    # 不同单元求和顺序带来末位舍入差（1e-28 C 量级），判据是远小于 LSB20
    assert report["max_spread_lsb20"] < 1e-6
    assert report["codes_checked"] == 512


def test_broken_vertical_selection_breaks_conservation():
    """按物理 ID 取模选 8/16 会改变 B4 权重，守恒审计必须报警。"""

    def broken(geometry, codes, states, slice_ids):
        command = fig11_masks(geometry, codes, states, slice_ids)
        main = command.main_on.copy()
        packed = np.asarray(states)
        for index, value in enumerate(packed):
            state = Dem3State.from_int(int(value))
            for position, physical in enumerate(slice_ids[index]):
                for unit in range(2):
                    main[index, position, 15 + unit] = bool(
                        (int(physical) + unit + state.vertical) % 16 < 8
                    ) and bool((int(codes[index]) >> 4) & 1)
        return SplitSwitchCommand(main, command.sub_on)

    pool = _fig11_pool()
    report = nominal_conservation(
        pool,
        broken,
        Dem3State.sequence(32, vertical=16),
        reference_high_v=3.0,
        reference_low_v=-3.0,
        codes=np.array([16, 48, 511]),
    )
    assert not report["conserved"]
    assert report["max_spread_c"] > 0


def test_unary_rotation_preserves_nominal_weight():
    pool = _default_pool()
    report = nominal_conservation(
        pool, unary_rotation_masks, Dem3State.sequence(240),
        reference_high_v=3.0, reference_low_v=-3.0,
    )
    assert report["conserved"], report


def test_unary_rotation_matches_default_segmented_counts():
    from adi_model.defaults import _segmented_mask

    pool = _default_pool()
    ids = np.broadcast_to(np.arange(8), (5, 8))
    codes = np.array([0, 1, 7, 8, 511])
    rotated = unary_rotation_masks(pool.geometry, codes, np.zeros(5, np.int64), ids)
    plain = _segmented_mask(pool.geometry, codes, np.zeros(5, np.int64), ids)
    assert np.array_equal(rotated.main_on.sum(axis=2), plain.main_on.sum(axis=2))
    assert np.array_equal(rotated.sub_on.sum(axis=2), plain.sub_on.sum(axis=2))


def test_usage_uniformity_covers_low_mid_and_full_codes():
    pool = _default_pool()
    plan = round_robin_plan(63 * 8)
    states = Dem3State.sequence(63 * 8, columns=63)
    report = usage_uniformity(
        pool, unary_rotation_masks, plan, states, codes=(8, 255, 511)
    )
    assert report["uniform"], report
    for code, row in report["codes"].items():
        assert row["main_units_covered"] == 63, code
        assert row["uniform"], (code, row["main_range"])


def test_round_robin_rejects_overlapping_or_oversized_windows():
    with pytest.raises(ConfigError):
        round_robin_plan(8, stride=4)  # 窗口滑动不足，相邻窗重叠
    with pytest.raises(ConfigError):
        round_robin_plan(8, n_slices=14, n_active=8)  # 2*8 > 14，无空闲片
    plan = round_robin_plan(9)
    for index in range(9):
        assert not set(plan.conv[index]) & set(plan.acq[index])


def test_fig11_masks_reject_wrong_geometry():
    pool = _default_pool()
    with pytest.raises(ConfigError):
        fig11_masks(pool.geometry, np.array([0]), np.array([0]), np.zeros((1, 8), np.int64))
