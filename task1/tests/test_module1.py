# =============================================================================
# 文件名     : test_module1.py
# 模块名     : test_module1
# 版本       : v1.0
# 功能       : 覆盖调度器与 DEM 的跨模块事务边界。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""覆盖调度器与 DEM 的跨模块事务边界。"""

from task1.model.fig11_dem import map_fig11
from task1.model.module1 import DEFAULT_DEM_SEED, Module1, Module1Output, dem_snapshot


# =========================================================================
# -------------------------------------------------------------------------
# 连续请求仍按本笔配置映射，并拒绝前缀的错配 ID。
# 切片不足、载入冲突和零种子保留两个随机状态。
# 新种子从下一笔采集生效，清空同时取消在途分配。
# -------------------------------------------------------------------------
def test_consecutive_allocations_preserve_phase_and_sample_owner() -> None:
    """连续请求仍按本笔配置映射，并拒绝前缀的错配 ID。"""
    dut = Module1()
    assert dut.step(advance=True, dem_enable=True) == Module1Output()

    first = dut.step(advance=True, dem_enable=False, code_valid=True, code=287)
    assert first.schedule.slot_accepted and first.schedule.acq_sample_id == 0
    assert not first.schedule.conv_valid
    assert first.code_error and first.masks.main_flat == 0

    second = dut.step(code_valid=True, code=287, code_sample_id=0)
    assert second.schedule.slot_accepted and second.schedule.conv_valid
    assert second.schedule.conv_ids == first.schedule.acq_ids
    assert second.schedule.acq_sample_id == 1 and second.schedule.conv_sample_id == 0
    assert second.conversion_dem == dem_snapshot(DEFAULT_DEM_SEED, True)
    assert not second.acquisition_dem.enabled
    assert not second.code_error and second.masks.valid

    prefix = dut.step(code_valid=True, code=320, code_sample_id=0, dem_enable=False)
    assert not prefix.schedule.slot_accepted
    assert prefix.conversion_dem == second.conversion_dem
    phase = first.acquisition_dem
    assert prefix.masks == map_fig11(320, True, phase.horizontal_phase, phase.vertical_phase)

    stale = dut.step(code_valid=True, code=320, code_sample_id=1)
    assert stale.code_error and not stale.masks.valid
    assert stale.masks.main_flat == 0 and stale.masks.sub_flat == 0


def test_stall_and_bad_seed_do_not_advance_either_random_stream() -> None:
    """切片不足、载入冲突和零种子保留两个随机状态。"""
    dut = Module1()
    dut.step(advance=True, shuffle=True, dem_enable=True)
    first = dut.step(advance=True, eligible=0, shuffle=True, dem_enable=True)
    stalled = dut.step(seed_load=True, advance=True, slice_seed=25, dem_seed=47)
    rejected = dut.step(seed_load=True, slice_seed=25, dem_seed=0)
    rejected_zero = dut.step()

    assert stalled.schedule.stall and not stalled.schedule.slot_accepted
    assert rejected.schedule.seed_error and rejected_zero.schedule.seed_error
    for result in (stalled, rejected, rejected_zero):
        assert not result.schedule.slot_accepted
        assert result.schedule.acq_ids == first.schedule.acq_ids
        assert result.schedule.acq_sample_id == first.schedule.acq_sample_id
        assert result.schedule.lfsr_state == first.schedule.lfsr_state
        assert result.dem_rng_state == first.dem_rng_state
        assert result.acquisition_dem == first.acquisition_dem


def test_reseed_keeps_frozen_phase_and_clear_cancels_pending_request() -> None:
    """新种子从下一笔采集生效，清空同时取消在途分配。"""
    dut = Module1()
    dut.step(advance=True, dem_enable=True)
    first = dut.step(seed_load=True, slice_seed=25, dem_seed=47)
    assert first.acquisition_dem == dem_snapshot(DEFAULT_DEM_SEED, True)

    seeded = dut.step(advance=True, dem_enable=False)
    assert seeded.schedule.lfsr_state == 25 and seeded.dem_rng_state == 47
    assert seeded.acquisition_dem == first.acquisition_dem

    second = dut.step(advance=True, dem_enable=True)
    assert second.conversion_dem == first.acquisition_dem
    assert second.acquisition_dem == dem_snapshot(47, False)

    assert dut.step(clear=True, code_valid=True) == Module1Output()
    assert dut.step() == Module1Output()

    dut.step(advance=True)
    assert dut.step(enable=False, code_valid=True) == Module1Output()
    assert dut.step(rst_n=False, code_valid=True) == Module1Output()
