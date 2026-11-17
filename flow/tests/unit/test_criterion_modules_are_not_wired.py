"""限定判据模块的消费方，防止默认仿真在未说明时启用专利机制。

五个判据模块不参加主链。cross_slice_mux 只接入独立数字参考链
digital_chain；默认行为仿真不由这条参考链驱动。新增消费方应先说明
接口边界并准备对用例有判别力的行为测试。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTROL = ROOT / "src/adi_model/control"
TESTS = ROOT / "flow/tests"

# [边界] 独立数字参考链确实使用 Fig.20–21 判据；这是唯一允许的消费方。
# 保留精确路径，避免允许该判据后连带放开默认模拟流水线。
_DECLARED_IMPORTERS = {"cross_slice_mux": {"src/adi_model/digital_chain.py"}}

CRITERION_MODULES = (
    "reserved_column",
    "aux_input",
    "reference_buffer",
    "redundancy",
    "shuffle_gate",
    "cross_slice_mux",
)


@pytest.mark.parametrize("module", CRITERION_MODULES)
def test_criterion_module_is_not_imported_by_the_main_chain(module):
    """消费方必须与声明一致，默认模拟流水线不在允许集合内。"""
    importers: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        if path.stem == module:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if f"control.{module}" in text or f"control import {module}" in text:
            importers.add(path.relative_to(ROOT).as_posix())
    declared = _DECLARED_IMPORTERS.get(module, set())
    assert importers == declared, (
        f"{module} has importers {sorted(importers)}; expected {sorted(declared)}. "
        "Document the boundary and verify the new integration before changing this test."
    )

@pytest.mark.parametrize("module", CRITERION_MODULES)
def test_criterion_module_documents_that_it_is_not_wired(module):
    """该模块的 docstring 必须声明"不参与主链仿真"。"""
    path = CONTROL / f"{module}.py"
    assert path.is_file(), f"{path} missing"
    text = path.read_text(encoding="utf-8")
    docstring = ast.get_docstring(ast.parse(text)) or ""
    # 说明可以写在 docstring 里，也可以写在文件头的中文注释横幅里（本仓库两种风格并存）
    combined = docstring + "\n" + text[:2000]
    assert "不参与主链" in combined or "不改主链" in combined, (
        f"{module}.py does not state that it is not wired into the main chain; "
        "readers will assume it runs in simulation"
    )


#: 原本"既无引用也无单测"的两个模块——2026-11-13 已各自补上单测，
#: 因此不再是孤儿。它们仍不是主链依赖，但那一点写在各自模块的 docstring 里。
_FORMERLY_ORPHAN = (
    "src/adi_model/hashing.py",
    "src/adi_model/behavioral/reference_mux.py",
)


@pytest.mark.parametrize("relative_path", _FORMERLY_ORPHAN)
def test_formerly_orphan_modules_are_now_tested_and_declare_their_status(relative_path):
    """这两个模块曾既无引用也无单测；现在必须有单测并在 docstring 里说明定位。

    2026-11-13：扫描发现 `hashing.py`(123 行) 与 `reference_mux.py`(204 行)
    完全没有被使用、也没有被测试。本轮给两者补了单测
    （`test_hashing.py` 6 例、`test_reference_mux.py` 12 例），
    并把"不是主链依赖"这一事实写进各自 docstring。

    本测试防止它们**退回到无人问津的状态**：docstring 必须仍然说明
    "src/ 内没有模块 import 它"。
    """
    import ast

    path = ROOT / relative_path
    assert path.is_file(), f"{relative_path} missing"
    stem = path.stem
    assert (TESTS / "unit" / f"test_{stem}.py").is_file(), (
        f"{relative_path} lost its unit test file"
    )
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    assert "没有模块 import 它" in docstring or "没有任何模块 import" in docstring, (
        f"{relative_path} does not state that it is not a main-chain dependency"
    )


def test_no_new_silently_unused_modules_appear():
    """不得出现**新的**"零引用 + 零测试"模块而不加声明。

    这是上一轮"代码存在且有测试 ≠ 代码在用"的推广：
    本测试先把**已知**的两个孤儿模块列入白名单，
    之后任何新增的孤儿模块都会让它失败。
    """
    import ast

    root = ROOT / "src/adi_model"
    stems = {p.stem: p for p in root.rglob("*.py") if p.stem not in ("__init__", "__main__")}

    # 收集 src/ 内的 import 名
    imported: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8", errors="ignore"))):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
                if node.module:
                    imported.add(node.module.split(".")[-1])
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[-1] for alias in node.names)

    # 收集 tests/ 里的出现
    tests_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in TESTS.rglob("*.py")
    )
    init_text = (root / "__init__.py").read_text(encoding="utf-8")

    orphans: list[str] = []
    for stem, path in sorted(stems.items()):
        if stem in imported or stem in tests_text or stem in init_text:
            continue
        relatives = str(path.relative_to(ROOT))
        orphans.append(relatives)

    assert not orphans, (
        "these modules are neither imported by src/ nor referenced by any test, "
        "and they do not declare that status:\n  " + "\n  ".join(orphans)
    )
