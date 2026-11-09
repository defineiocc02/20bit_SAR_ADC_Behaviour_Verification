# =============================================================================
# 文件名     : test_import_smoke.py
# 模块名     : task_reconstruction.tests.test_import_smoke
# 版本       : v1.0
# 功能       : task_reconstruction 重构层的导入冒烟，只证明「在测试环境里能 import 到」
#
# 设计说明   :
#   * 此处只检查公共入口可导入。数值合同另由独立整数 oracle、双使能定向
#     向量和 Verilator 的全量同池重放验收；导入通过不能代表重构已通过。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""task_reconstruction 重构层的导入冒烟。"""

from __future__ import annotations


def test_fixed_reconstruction_public_names_are_importable() -> None:
    """重构模块能被导入，且公开入口没有被改名。"""
    from task_reconstruction import fixed

    for name in ("Coefficients", "from_training", "decode_midpoint", "sum_charge", "reconstruct"):
        assert hasattr(fixed, name), f"task_reconstruction.fixed 缺少公开名 {name}"


def test_full_scale_code_is_shared_with_the_codes_module():
    """满幅码数从 adi_model.codes 取，不在这里另写字面量。

    放在 task 层的测试里而不是 src/tests：库层测试导入 task 层会被
    check_layering 判成反向依赖（R1）。
    """
    from adi_model.codes import OUTPUT_CODES
    from task_reconstruction.fixed import FULL_SCALE_CODE

    assert FULL_SCALE_CODE == OUTPUT_CODES
