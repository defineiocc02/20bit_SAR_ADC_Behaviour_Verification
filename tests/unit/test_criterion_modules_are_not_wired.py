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
