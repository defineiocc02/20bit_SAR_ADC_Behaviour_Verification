"""公开 API 必须带文档字符串 —— 静态守卫。

## 为什么需要

本仓库的约定是"每个公开函数都写下单位/范围/依据"（见 AGENTS 注释规范）。
2026-11-13 测量：374 个公开函数里 359 个有 docstring（**96.0%**），
缺的 15 个中 11 个是嵌套的局部辅助函数、4 个是真正的模块级公开 API
（`cli.error`、`EventLog.add/for_sample/all`）。已补齐这 4 个。

## 本测试守什么

**只要求模块级与被引用的公开方法有 docstring**，不要求嵌套闭包——
闭包的职责已由其外层函数的 docstring 覆盖，逐个要求只会制造噪声。

判定"模块级"的方式：遍历 `tree.body` 与类体的直接成员，不 `ast.walk` 到函数内部。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src" / "adi_model"


def _module_level_public_nodes(tree: ast.Module):
    """产出模块级与被顶层类直接包含的公开函数/方法（不含嵌套闭包）。"""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                yield node
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # dunder 方法（__init__ 等）由类 docstring 覆盖，不单独要求
                    if not member.name.startswith("_"):
                        yield member


def test_module_level_public_functions_have_docstrings():
    """模块级公开函数与方法必须带 docstring。"""
    missing: list[str] = []
    total = 0
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _module_level_public_nodes(tree):
            total += 1
            if not ast.get_docstring(node):
                missing.append(f"{path.relative_to(ROOT)}::{node.name}")
    assert total > 200, f"expected a large public surface, counted only {total}"
    assert not missing, (
        f"{len(missing)} module-level public functions lack a docstring:\n  "
        + "\n  ".join(missing)
        + "\n(add a docstring stating units/range/evidence; nested closures are exempt)"
    )


def test_public_surface_coverage_is_high():
    """含嵌套函数在内，整体 docstring 覆盖率应保持在高位（当前 96%）。

    嵌套闭包不强制要求，但覆盖率不应因此明显下滑——本测试给出下限 90%，
    防止有人用"加一堆无文档的辅助函数"稀释可读性。
    """
    total = 0
    documented = 0
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                total += 1
                if ast.get_docstring(node):
                    documented += 1
    coverage = documented / total
    assert coverage >= 0.90, (
        f"public docstring coverage dropped to {coverage:.1%} "
        f"({documented}/{total}); keep it above 90%"
    )
