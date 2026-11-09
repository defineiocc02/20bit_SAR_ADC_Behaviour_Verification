# =============================================================================
# 文件名     : test_module1_bridge.py
# 模块名     : test_module1_bridge
# 版本       : v1.0
# 功能       : 覆盖 Fig.21 模式的同笔事务和名义补偿接口。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""覆盖 Fig.21 模式的同笔事务和名义补偿接口。"""

from fractions import Fraction

from task1.model.fig21_bridge import map_fig21_bridge
from task1.model.module1 import Module1
from task1.model.module1_bridge import Module1Bridge, Module1BridgeOutput


# =========================================================================
# -------------------------------------------------------------------------
# 桥接模式随采集组晋升，活动前缀只更新掩码和补偿。
# 资源不足、种子冲突和错 ID 保留或取消事务资格。
# 关闭桥接时逐拍保持原顶层的配置、状态和掩码。
# -------------------------------------------------------------------------
def test_consecutive_allocations_freeze_bridge_and_phases_for_every_prefix() -> None:
    """桥接模式随采集组晋升，活动前缀只更新掩码和补偿。"""
    dut = Module1Bridge()
    assert dut.step(advance=True, bridge_enable=True) == Module1BridgeOutput()
    first = dut.step(advance=True, bridge_enable=False)
    assert first.module1.schedule.acq_sample_id == 0
    assert first.acquisition_bridge_enabled and not first.conversion_bridge_enabled

    second = dut.step(advance=True, bridge_enable=True, code_valid=True, code=8)
    assert second.module1.schedule.conv_sample_id == 0
    assert second.conversion_bridge_enabled and not second.acquisition_bridge_enabled
    assert second.correction_q32 == round(Fraction(2 << 26, 17))
    assert second.module1.conversion_dem == first.module1.acquisition_dem
    assert not second.module1.conversion_dem.enabled

    third = dut.step(code_valid=True, code=8, code_sample_id=1)
    assert third.module1.schedule.conv_sample_id == 1
    assert third.acquisition_bridge_enabled and not third.conversion_bridge_enabled
    assert third.module1.masks.valid and third.correction_q32 == 0

    dut.step(advance=True, bridge_enable=False)
    bridge_sample = dut.step()
    assert bridge_sample.module1.schedule.conv_sample_id == 2
    assert bridge_sample.conversion_bridge_enabled
    snapshot = bridge_sample.module1.conversion_dem

    for code, scale in ((16, 0), (20, 1), (24, 2), (28, 3)):
        result = dut.step(code_valid=True, code=code, code_sample_id=2)
        expected = map_fig21_bridge(code, snapshot.vertical_phase, snapshot.horizontal_phase & 7)
        assert result.module1.conversion_dem == snapshot
        assert result.module1.masks == expected.masks
        assert result.correction_q32 == round(Fraction(scale << 26, 17))


def test_reject_and_clear_do_not_publish_a_bridge_update() -> None:
    """资源不足、种子冲突和错 ID 保留或取消事务资格。"""
    dut = Module1Bridge()
    dut.step(advance=True, bridge_enable=True, dem_enable=True)
    dut.step(advance=True, bridge_enable=False)
    published = dut.step(advance=True, eligible=0, bridge_enable=True)
    assert published.conversion_bridge_enabled and not published.acquisition_bridge_enabled

    stalled = dut.step(advance=True, seed_load=True, slice_seed=11, dem_seed=31, bridge_enable=True)
    assert stalled.module1.schedule.stall
    assert stalled.conversion_bridge_enabled and not stalled.acquisition_bridge_enabled
    wrong_id = dut.step(code_valid=True, code=12, code_sample_id=11)
    assert wrong_id.module1.schedule.seed_error and wrong_id.module1.code_error
    assert not wrong_id.module1.masks.valid and wrong_id.correction_q32 == 0
    assert wrong_id.module1.masks.main_flat == wrong_id.module1.masks.sub_flat == 0

    dut.step(advance=True, bridge_enable=True)
    assert dut.step(clear=True, code_valid=True, code=12) == Module1BridgeOutput()
    assert dut.step() == Module1BridgeOutput()

    dut.step(advance=True, bridge_enable=True)
    assert dut.step(enable=False, code_valid=True, code=12) == Module1BridgeOutput()
    assert dut.step(rst_n=False, code_valid=True, code=12) == Module1BridgeOutput()


def test_nonbridge_wrapper_preserves_baseline() -> None:
    """关闭桥接时逐拍保持原顶层的配置、状态和掩码。"""
    base, bridge = Module1(), Module1Bridge()
    for edge in range(48):
        advance = edge % 4 == 0
        kwargs = {
            "advance": advance,
            "shuffle": True,
            "dem_enable": edge % 3 == 0,
            "code_valid": True,
            "code": (edge * 73) % 512,
            "code_sample_id": max((edge - 5) // 4, 0),
        }
        result = bridge.step(**kwargs)
        assert result.module1 == base.step(**kwargs)
        assert not result.acquisition_bridge_enabled and not result.conversion_bridge_enabled
        assert result.correction_q32 == 0
