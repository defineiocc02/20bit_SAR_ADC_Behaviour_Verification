# =============================================================================
# 文件名     : test_import_smoke.py
# 模块名     : task_integration.tests.test_import_smoke
# 版本       : v1.0
# 功能       : task_integration 工具层的导入冒烟，只证明「在测试环境里能 import 到」
#
# 设计说明   :
#   * task_integration 此前零测试。本目录下的 run_checks.py 是脚本形态，
#     导入它不应有任何副作用（没有模块级 I/O、没有模块级 np 计算），
#     这里顺带把这条约束钉住：如果将来有人在模块级加了重活，这个冒烟会变慢
#     但仍为绿，所以真正的守卫是下面那条「main 可调用」的断言。
#   * 不断言任何数值或集成时序结论。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""task_integration 工具层的导入冒烟。"""

from __future__ import annotations


def test_run_checks_imports_without_side_effects() -> None:
    """集成检查脚本能被导入，且入口仍是可调用的 main。"""
    from task_integration.tools import run_checks

    assert callable(run_checks.main), "task_integration.tools.run_checks 缺少可调用的 main"
