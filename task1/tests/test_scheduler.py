# =============================================================================
# 文件名     : test_scheduler.py
# 模块名     : test_scheduler
# 版本       : v1.0
# 功能       : 调度参考的边界、释放资格与事件语义验证。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""调度参考的边界、释放资格与事件语义验证。"""

from dataclasses import replace
from random import Random

import pytest

from task1.model.scheduler import DEFAULT_SEED, FULL_MASK, SchedulerOutput, SliceScheduler


# =========================================================================
# -------------------------------------------------------------------------
# 首沿只有采集资格，第二沿按原顺序晋升。
# 释放不足整拍拒绝，补足后可重试。
# 核对零种子、冲突、随机关闭和清空优先级。
# -------------------------------------------------------------------------
def test_first_request_primes_only_and_next_request_promotes_ordered_group() -> None:
    """首沿只有采集资格，第二沿按原顺序晋升。"""
    scheduler = SliceScheduler()
    first = scheduler.step(advance=True)
    assert first.slot_accepted and first.acq_valid and not first.conv_valid
    assert first.acq_ids == tuple(range(8))
    assert first.acq_sample_id == 0

    second = scheduler.step(advance=True)
    assert second.slot_accepted and second.conv_valid
    assert second.conv_ids == first.acq_ids
    assert second.conv_sample_id == first.acq_sample_id
    assert second.acq_ids == tuple(range(8, 16))
    assert second.acq_sample_id == 1
    assert second.acq_mask & second.conv_mask == 0


def test_unreleased_group_rejection_preserves_every_state_field() -> None:
    """释放不足整拍拒绝，补足后可重试。"""
    scheduler = SliceScheduler()
    first = scheduler.step(advance=True)
    eligible = FULL_MASK & ~((1 << 8) | (1 << 9) | (1 << 10))
    rejected = scheduler.step(advance=True, eligible=eligible, shuffle=True)
    assert rejected == replace(first, slot_accepted=False, stall=True)

    retry = scheduler.step(advance=True, eligible=eligible | (1 << 10), shuffle=True)
    assert retry.slot_accepted
    assert set(retry.acq_ids) == set(range(10, 18))
    assert retry.conv_ids == first.acq_ids
    assert retry.conv_sample_id == 0
    assert retry.lfsr_state != first.lfsr_state
    assert scheduler.step() == replace(retry, slot_accepted=False)


def test_seed_and_cancel_priorities() -> None:
    """核对零种子、冲突、随机关闭和清空优先级。"""
    scheduler = SliceScheduler()
    seeded = scheduler.step(seed_load=True, seed=0x11000001)
    assert seeded.lfsr_state == 0x11000001
    assert not seeded.acq_valid
    assert scheduler.step(seed_load=True) == replace(seeded, seed_error=True)
    assert scheduler.step(seed_load=True, seed=2, advance=True) == replace(seeded, seed_error=True)

    first = scheduler.step(advance=True, shuffle=True)
    assert first.acq_ids == (17, 0, 1, 2, 3, 4, 5, 6)
    assert first.lfsr_state == 0xDB27C002
    fixed = scheduler.step(advance=True, shuffle=False)
    assert fixed.acq_ids == tuple(range(7, 15))
    assert fixed.lfsr_state == first.lfsr_state

    for control in ({"clear": True}, {"rst_n": False}, {"enable": False}):
        assert scheduler.step(advance=True, seed_load=True, seed=1, **control) == SchedulerOutput()
        assert scheduler.step(advance=True).acq_sample_id == 0
    assert scheduler.state.lfsr_state == DEFAULT_SEED


# =========================================================================
# 2. 函数组：test_sparse_eligibility_and_seed_replay_over_mixed_requests / test_out_of_range_inputs
# =========================================================================
# -------------------------------------------------------------------------
# 混合空拍与稀疏资格，检查选片不变量和重放。
# 超端口位宽的模型输入报错。
# -------------------------------------------------------------------------
def test_sparse_eligibility_and_seed_replay_over_mixed_requests() -> None:
    """混合空拍与稀疏资格，检查选片不变量和重放。"""
    first = SliceScheduler()
    second = SliceScheduler()
    rng = Random(20261106)
    for _ in range(400):
        eligible = rng.getrandbits(18)
        advance = bool(rng.getrandbits(1))
        kwargs = {"advance": advance, "eligible": eligible, "shuffle": True}
        old = first.state
        out = first.step(**kwargs)
        assert out == second.step(**kwargs)
        expected_accept = advance and (eligible & ~old.acq_mask).bit_count() >= 8
        assert out.slot_accepted == expected_accept
        if expected_accept:
            assert len(set(out.acq_ids)) == 8
            assert out.acq_mask & ~eligible == 0
            assert out.acq_mask & out.conv_mask == 0
            assert out.conv_ids == old.acq_ids
            assert out.conv_valid == old.acq_valid
            assert out.conv_sample_id == old.acq_sample_id
        else:
            assert out == replace(old, slot_accepted=False, stall=advance, seed_error=False)


@pytest.mark.parametrize("kwargs", [{"eligible": -1}, {"eligible": 1 << 18}, {"seed": 1 << 32}])
def test_out_of_range_inputs(kwargs: dict[str, int]) -> None:
    """超端口位宽的模型输入报错。"""
    with pytest.raises(ValueError):
        SliceScheduler().step(**kwargs)
