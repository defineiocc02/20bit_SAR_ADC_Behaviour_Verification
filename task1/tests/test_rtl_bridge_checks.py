# =============================================================================
# 文件名     : test_rtl_bridge_checks.py
# 模块名     : test_rtl_bridge_checks
# 版本       : v1.0
# 功能       : 验证桥接前缀补偿在名义重构中只消费一次。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""验证桥接前缀补偿在名义重构中只消费一次。"""

from fractions import Fraction

import pytest

from task1.model.fig21_bridge import map_fig21_bridge
from task1.tools.run_bridge_checks import Q32_SCALE, _reference_bridge


# =========================================================================
# 1. 函数组：test_nominal_reconstruction_rejects_missing_or_double_correction
# =========================================================================
# -------------------------------------------------------------------------
# 非零跨段差在 Fig.11 码本只加一次，按实际位权时不加。
# -------------------------------------------------------------------------
@pytest.mark.parametrize("code", [0x004, 0x008, 0x1FF])
def test_nominal_reconstruction_rejects_missing_or_double_correction(code: int) -> None:
    """非零跨段差在 Fig.11 码本只加一次，按实际位权时不加。"""
    _, _, _, delta = _reference_bridge(code, 15, 7)
    correction = Fraction(map_fig21_bridge(code, 15, 7).correction_q32, Q32_SCALE)
    half_lsb = Fraction(1, 2 * Q32_SCALE)
    assert delta > half_lsb

    # 用一个任意的名义余量，隔离纯数字消费规则。
    nominal_residue = Fraction(-3, 16)
    migrated_residue = nominal_residue - delta
    assert abs(migrated_residue + correction - nominal_residue) <= half_lsb
    assert abs(migrated_residue - nominal_residue) > half_lsb
    assert abs(migrated_residue + 2 * correction - nominal_residue) > half_lsb
    actual_weight_reconstruction = migrated_residue + delta
    assert actual_weight_reconstruction == nominal_residue
    assert abs(actual_weight_reconstruction + correction - nominal_residue) > half_lsb
