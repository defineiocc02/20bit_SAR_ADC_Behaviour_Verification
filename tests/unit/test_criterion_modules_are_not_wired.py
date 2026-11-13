"""六个"判据模块"必须**不**被主链接入——这是有意的设计决定。

## 背景

`reserved_column`、`aux_input`、`reference_buffer`、`redundancy`、
`shuffle_gate`、`cross_slice_mux` 六个模块把专利里的结构约束写成可核对的判据，
各有 6–7 个单测（合计 39 个用例）。

**但它们都没有接入主链**：`src/` 内没有任何地方 import 它们。
原因是原件没有给出"本芯片是否启用这些机制"的依据——
接入与否是工程选择，应由人决定，不靠猜。

## 这个测试守什么

1. 这六个模块确实**没有被 `src/` 的其他模块 import**（防止有人顺手接进去
   而没有留下决策记录）；
2. 每个模块的 docstring 里**明确写了"不参与主链仿真"**（防止读者
   误以为它们在仿真路径上，就像本会话第 85 轮发现的那样）。

## 如果将来真的决定接入

那时应该：
* 同步更新 README §6.4 的说明；
* 在本文件里把该模块从未接清单中移除，并写明决策依据；
* 补端到端测试证明它确实影响了输出。

本测试失败时，请先确认上述三件事做了没有。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "src/adi_model/control"

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
    """该模块不得被 `src/` 的其他文件 import。"""
    importers: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.stem == module:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if f"control.{module}" in text or f"control import {module}" in text:
            importers.append(str(path.relative_to(ROOT)))
    assert not importers, (
        f"{module} is now imported by the main chain ({importers}); "
        "if that is intentional, update README §6.4 and this test file with the rationale"
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


#: 已知的"已实现但既无引用、也无单测"的模块（每条都要有 docstring 声明）。
_UNUSED_MODULES = (
    "src/adi_model/hashing.py",
    "src/adi_model/behavioral/reference_mux.py",
)


@pytest.mark.parametrize("relative_path", _UNUSED_MODULES)
def test_unused_modules_declare_their_status(relative_path):
    """已知未使用的模块必须在 docstring 里说明"既未被引用、也无单测覆盖"。

    2026-11-13：扫描发现 `hashing.py`（123 行）与 `behavioral/reference_mux.py`
    （204 行）**既没有任何模块 import，也没有任何单测**——327 行"孤儿代码"。

    它们不是垃圾：`architecture.md` 把 `reference_mux.py` 列在模块树里，
    `hashing.py` 有完整的设计说明。但它们**确实没在跑**。
    若不声明，读者看到模块树会以为这些机制在工作。

    本测试要求它们**自己说清楚**自己的状态。
    """
    import ast

    path = ROOT / relative_path
    assert path.is_file(), f"{relative_path} missing"
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
    assert "既未被" in docstring and "没有单测" in docstring, (
        f"{relative_path} does not declare that it is neither imported nor tested; "
        "readers will assume it is in use"
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
        for p in (ROOT / "tests").rglob("*.py")
    )
    init_text = (root / "__init__.py").read_text(encoding="utf-8")

    orphans: list[str] = []
    for stem, path in sorted(stems.items()):
        if stem in imported or stem in tests_text or stem in init_text:
            continue
        relatives = str(path.relative_to(ROOT))
        # 已知的孤儿（且已加声明）不算新增
        declared = (
            "既未被" in (ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or "")
        )
        if relatives in _UNUSED_MODULES and declared:
            continue
        orphans.append(relatives)

    assert not orphans, (
        "these modules are neither imported by src/ nor referenced by any test, "
        "and they do not declare that status:\n  " + "\n  ".join(orphans)
    )
