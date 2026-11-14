"""三维 DEM 掩码、名义守恒与使用均匀性的单元测试。

两个池都是显式合成几何：Fig.11 的 17/5 阵列与默认 63+8 分段阵列。
本组测试不评估 INL/DNL，只验证"重排不改名义权重"和"单元等频使用"。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.dem import SplitSwitchCommand
from adi_model.control.dem3d import (
    Dem3State,
    fig11_masks,
    nominal_conservation,
    round_robin_plan,
    fig11_masks,
    usage_uniformity,
)
from adi_model.simulation.config import ConfigError
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook, SplitChip


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
        # 桩必须声明物理位宽：slice_pool 现在用 cfg.dac_n_sub 建几何
        # （子阵列端口 5 位，器件可只装配 4 列），缺这个属性会 AttributeError。
        object.__new__(
            type("Cfg", (), {"n_slices": 18, "n_active": 8, "dac_n_main": 17, "dac_n_sub": 5})
        ),
        chip=chip,
        nominal_codebook=book,
        mask_binding=fig11_masks,
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
    """低码下每片都要走遍全部 15 个 2C 列（A01 反例的 17/5 版本）。

    17/5 阵列里可旋转的是 15 个 2C 列；一个相位周期 = columns × plan_period = 15 × 9 = 135。
    码 32 在主段选中 1 个 2C（B5=1），固定物理片 7 在一个周期后应恰好等频访问 15 列。
    注意：码 8 在 17/5 下只选子阵列（B3），主单元覆盖必为 0——这与 63+8 的
    "code//8 个主单元"完全不同，是位分配的差别，不是缺陷。
    """
    pool = _fig11_pool()
    n = 15 * 9
    ids = np.broadcast_to(np.arange(8), (n, 8))
    states = Dem3State.sequence(n, columns=15)
    command = fig11_masks(pool.geometry, np.full(n, 32, np.int64), states, ids)
    visits = command.main_on[:, 7, :15].sum(axis=0)
    assert np.count_nonzero(visits) == 15
    assert int(visits.max() - visits.min()) == 0
    assert int(visits[0]) == n // 15  # 15 列均分：每列恰好 n/15 次


def test_sub_array_is_binary_so_it_is_not_rotated():
    """子阵列是二进制权，**不能**按相位轮转（轮转即改权重）。

    旧实现把子阵列当成 7 位一元温度计并轮转，是 63+8 的读法。
    FIG.11 的 8C/4C/2C/1C 是二进制，因此同一码在任何 DEM 状态下子阵列选通集合必须相同。
    """
    pool = _fig11_pool()
    ids = np.broadcast_to(np.arange(8), (16, 8))
    codes = np.array([1, 2, 4, 8, 15] * 3 + [9], np.int64)
    assert len(codes) == 16
    states = np.array([Dem3State(0, 0).to_int(), Dem3State(5, 7).to_int()])
    on = fig11_masks(pool.geometry, codes, np.zeros(len(codes), np.int64), ids)
    moved = fig11_masks(pool.geometry, codes, np.full(len(codes), states[1], np.int64), ids)
    assert np.array_equal(on.sub_on, moved.sub_on)
    # 且子阵列选通数 = 码的低 4 位中 1 的个数（二进制，不是温度计）
    assert np.array_equal(on.sub_on.sum(axis=-1)[:, 0], np.array([bin(c).count("1") for c in codes]))


def test_conservation_reports_lsb_units_not_farads():
    """外部审查 A02：1 LSB20 的电荷误差必须折算成 1，而不是一个法拉数。"""
    from types import SimpleNamespace

    from adi_model.control.dem import SplitSwitchGeometry

    c_norm_f, lsb20_v = 20.5e-12, 6.0 / 2**20
    charges = np.array([0.0, c_norm_f * lsb20_v])

    class _Stub:
        def __init__(self):
            self.geometry = SplitSwitchGeometry(17, 5, 18, 8, 0, 511)

        def split_coefficients(self, ids):
            return {"c_signal": np.full(len(ids), c_norm_f)}

        def charge_from_masks(self, ids, command, **references):
            return SimpleNamespace(charge_c=charges.copy())

    report = nominal_conservation(
        _Stub(),
        fig11_masks,
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
    pool = _fig11_pool()
    report = nominal_conservation(
        pool, fig11_masks, Dem3State.sequence(240),
        reference_high_v=3.0, reference_low_v=-3.0,
    )
    assert report["conserved"], report


def test_usage_uniformity_covers_low_mid_and_full_codes():
    """17/5 下的使用均匀性：15 个 2C 必须逐片逐单元等频，B4/S4 两个 1C 不对称。

    实测（码 255、n=135、物理片聚合）：
    * 15 个 2C 单元各 504 次 —— 严格等频，A01 类混叠在 17/5 上不成立；
    * B4 的 1C（unit 15）1080 次、S4 的 1C（unit 16）0 次 —— 因为原件 FIG.17 把 B4
      画成**固定步**（不随相位轮转），而 S4 的编码规则图上未给（本仓库标"待确认"，不启用）。
    这两条是真实结构差异，不是缺陷；`uniform=False` 正是它被如实报出来的方式。
    """
    pool = _fig11_pool()
    n = 15 * 9
    plan = round_robin_plan(n)
    states = Dem3State.sequence(n, columns=15)
    report = usage_uniformity(pool, fig11_masks, plan, states, codes=(32, 255, 511))

    twos = report["codes"]["255"]["main_unit_counts"][:15]
    assert len(set(twos)) == 1, twos
    # 504 = n/15 × 56 = 9 × 56，与"每个 2C 单元被 56 个码级周期覆盖"一致；
    # 写死实测值是为了让任何"偷偷改相位步长"的改动立刻失败。
    assert twos[0] == 504, twos
    assert report["codes"]["255"]["main_unit_counts"][15] == 1080
    assert report["codes"]["255"]["main_unit_counts"][15] > twos[0]
    assert report["codes"]["255"]["main_unit_counts"][16] == 0
    assert report["codes"]["255"]["uniform"] is False

    assert report["codes"]["511"]["uniform"] is True
    assert report["codes"]["511"]["main_units_covered"] == 16


def test_round_robin_rejects_overlapping_or_oversized_windows():
    with pytest.raises(ConfigError):
        round_robin_plan(8, stride=4)  # 窗口滑动不足，相邻窗重叠
    with pytest.raises(ConfigError):
        round_robin_plan(8, n_slices=14, n_active=8)  # 2*8 > 14，无空闲片
    plan = round_robin_plan(9)
    for index in range(9):
        assert not set(plan.conv[index]) & set(plan.acq[index])


def test_fig11_masks_reject_wrong_geometry():
    pool = _fig11_pool()
    with pytest.raises(ConfigError):
        fig11_masks(pool.geometry, np.array([0]), np.array([0]), np.zeros((1, 8), np.int64))


def test_physical_slice_audit_is_not_slot_aggregation():
    """Counterexample: per-slot aggregation claims full coverage that does not exist.

    Each physical slice only ever uses 21 of its 63 main units, but different
    slices use *different* 21. Rotating eight slices per sample means every
    logical slot eventually sees all 18 slices, so summing over the sample axis
    (the old `command.main_on.sum(axis=0)`) reports 63/63 used. Summing by
    physical slice id reports the truth: 21/63 everywhere.

    The assertion is on the audit result, not on a diagnostic field, so a
    regression here fails the test the review asked for.
    """
    from adi_model.control.dem import SplitSwitchGeometry
    from adi_model.control.dem3d import _per_physical_slice_counts

    geometry = SplitSwitchGeometry(n_main=63, n_sub=8, n_slices=18, n_active=8)
    n_samples = 36
    ids = np.array([[(n + lane) % 18 for lane in range(8)] for n in range(n_samples)])
    main_on = np.zeros((n_samples, 8, 63), dtype=np.int64)
    sub_on = np.zeros((n_samples, 8, 8), dtype=np.int64)
    window = 21
    for n in range(n_samples):
        for lane in range(8):
            sid = ids[n, lane]
            start = (sid * 7) % 63
            for offset in range(window):
                main_on[n, lane, (start + offset) % 63] = 1

    per_slice_main, _ = _per_physical_slice_counts(main_on, sub_on, ids, geometry)
    per_slice_coverage = np.count_nonzero(per_slice_main, axis=1)
    assert per_slice_main.shape == (18, 63)
    assert np.all(per_slice_coverage == window), per_slice_coverage

    # The aggregation the review rejected would have reported full coverage.
    per_slot = main_on.sum(axis=0)  # (active_slot, unit)
    assert np.all(np.count_nonzero(per_slot, axis=1) == 63)


def test_slice_ids_reach_every_physical_row_once_per_rotation():
    """A slice that is never selected must stay a zero row, not disappear."""
    from adi_model.control.dem import SplitSwitchGeometry
    from adi_model.control.dem3d import _per_physical_slice_counts

    geometry = SplitSwitchGeometry(n_main=63, n_sub=8, n_slices=18, n_active=8)
    n_samples = 4
    ids = np.array([[(n + lane) % 18 for lane in range(8)] for n in range(n_samples)])
    main_on = np.ones((n_samples, 8, 63), dtype=np.int64)
    sub_on = np.ones((n_samples, 8, 8), dtype=np.int64)
    per_slice_main, per_slice_sub = _per_physical_slice_counts(main_on, sub_on, ids, geometry)
    covered = np.count_nonzero(per_slice_main, axis=1)
    assert per_slice_main.shape[0] == geometry.n_slices
    # Only slices 0..10 appear in this short window; 11..17 must remain as zero
    # rows rather than being dropped from the report.
    assert np.all(covered[:11] > 0)
    assert np.all(covered[11:] == 0)
    assert np.all(per_slice_main[11:] == 0)
    # Mass conservation: every lane visit is attributed exactly once.
    assert int(per_slice_main.sum()) == int(main_on.sum())
    assert int(per_slice_sub.sum()) == int(sub_on.sum())


def test_beta_expands_along_the_sub_unit_axis_not_the_sample_axis():
    """Pin the broadcast axis for the bridge attenuation.

    `beta` is [sample, active_slice]. `C_sub[ids]` is [sample, active_slice,
    sub_unit]. The only correct product is `beta[..., None] * sub`, which scales
    every unit of a slice by that slice's beta.

    Two coincidences can hide a wrong axis:

    * samples == active slices, where `beta[:, None] * sub` still broadcasts but
      indexes beta by sample instead of by slice;
    * active slices == sub units, where a transposed scatter has compatible
      extents.

    Both are exercised here, and the ground truth is explicit indexing rather
    than another broadcast expression.
    """
    from adi_model.defaults import default_model
    from adi_model.topology.dac_arch import split_capacitance_coefficients

    model = default_model()
    chip = model.chip
    n_sub = chip.C_sub.shape[-1]
    n_slices = chip.C_main.shape[0]
    configured_active = int(model.cfg.n_active)
    # 2026-11-13：19 世纪 8-片/8-子单元的"巧合"随 17/5 几何消失（子阵列跨桥后是 4 个可切换单元，
    # 物理位 5 含恒低哑位）。原测试用它作为"最容易广播错轴的形状"，现在改为显式断言
    # "子单元数 != 活动片数"，并保留对两个不同 n_active 的维度检查。
    assert n_sub != configured_active, "the beta broadcast test needs distinct dimensions"

    # 2026-11-13：第二个形状从 n_active=5 改为 3 —— 4 单元子阵列下
    # "活动片数 == 子单元数" 会再次撞出危险巧合（4 == 4），
    # 而 3 既与子单元数不同、也与配置的活动片数不同，才是真正独立的对照形状。
    for n_active in (configured_active, 3):
        n_samples = n_active if n_active == configured_active else n_active + 3
        ids = np.arange(n_samples * n_active, dtype=np.int64).reshape(n_samples, n_active)
        ids = ids % n_slices
        beta = split_capacitance_coefficients(chip, ids)["beta"]
        sub = chip.C_sub[ids]
        assert beta.shape == ids.shape
        assert sub.shape == (*ids.shape, n_sub)

        effective = beta[..., None] * sub
        assert effective.shape == (n_samples, n_active, n_sub)

        # Ground truth: explicit double loop, no broadcasting at all.
        for s in range(n_samples):
            for a in range(n_active):
                for u in range(n_sub):
                    assert effective[s, a, u] == beta[s, a] * sub[s, a, u]

        # 被拒绝的写法：当 n_samples == n_active 时 `beta[:, None] * sub` 不会报错，
        # 而是按 sample 索引 beta，静默算出一个"看起来对"的结果。
        # 2026-11-13：17/5 几何下该形状根本广播不起来（8×1×8 vs 8×8×5），
        # 那也是被抓住的一种方式；只在能广播时检查"确实算错"。
        if n_samples == n_active:
            try:
                wrong = beta[:, None] * sub
            except ValueError:
                wrong = None
            if wrong is not None:
                assert wrong.shape == effective.shape
                differs = np.any(wrong != effective)
                assert differs or np.all(beta == beta[0]), (
                    "beta is sample/slice symmetric here, so this case cannot reveal the bug"
                )
