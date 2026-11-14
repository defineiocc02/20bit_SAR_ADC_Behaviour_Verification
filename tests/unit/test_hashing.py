"""`adi_model.hashing` 的字节摘要与语义摘要。

## 为什么这个模块需要测试

它此前**既没有被任何模块引用，也没有任何测试**（本轮扫描发现的孤儿模块之一）。
现在 `tools/check_engineering.py` 的 C4 门禁依赖"语义摘要"这一判据——
用来区分"只改了注释"与"改了行为"——但那个工具跑在系统 python 3.9 下、
无法 import 本包，所以它内联了一份等价实现。

**本文件同时守住两件事**：
1. `hashing.py` 自身的行为正确；
2. 它与 `check_engineering._semantic_digest_of_python` 的判据一致
   （两份实现若分歧，C4 会开始误报或漏报）。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from adi_model.hashing import semantic_digest, semantic_digest_of_text, sha256_file

ROOT = pathlib.Path(__file__).resolve().parents[2]

_ORIGINAL = 'def f():\n    """doc"""\n    return 1\n'
#: 只改注释与 docstring —— 语义摘要必须不变
_COMMENT_ONLY = 'def f():\n    """doc changed"""\n    # a new comment\n    return 1\n'
#: 改了返回值 —— 语义摘要必须变
_BEHAVIOUR_CHANGED = 'def f():\n    """doc"""\n    return 2\n'


def test_byte_digest_changes_for_any_edit(tmp_path):
    """字节摘要对"只改注释"也会变——这是它与语义摘要的分工。"""
    first = tmp_path / "a.py"
    first.write_text(_ORIGINAL)
    second = tmp_path / "b.py"
    second.write_text(_COMMENT_ONLY)
    assert sha256_file(first) != sha256_file(second)


def test_semantic_digest_ignores_comments_and_docstrings(tmp_path):
    """只改注释或 docstring 时，语义摘要必须相同。"""
    first = tmp_path / "a.py"
    first.write_text(_ORIGINAL)
    second = tmp_path / "b.py"
    second.write_text(_COMMENT_ONLY)
    assert semantic_digest(first) == semantic_digest(second)
    assert semantic_digest_of_text(_ORIGINAL, ".py") == semantic_digest_of_text(
        _COMMENT_ONLY, ".py"
    )


def test_semantic_digest_detects_behaviour_change(tmp_path):
    """改返回值时，语义摘要必须变化。"""
    first = tmp_path / "a.py"
    first.write_text(_ORIGINAL)
    third = tmp_path / "c.py"
    third.write_text(_BEHAVIOUR_CHANGED)
    assert semantic_digest(first) != semantic_digest(third)


def test_semantic_digest_of_text_matches_semantic_digest(tmp_path):
    """文本版与路径版对同一内容必须给出同一摘要。"""
    path = tmp_path / "x.py"
    path.write_text(_ORIGINAL)
    assert semantic_digest_of_text(_ORIGINAL, ".py") == semantic_digest(path)


def test_gate_helper_agrees_with_the_library():
    """`check_engineering._semantic_digest_of_python` 与本模块判据一致。

    工具的版本是内联复制的（工具跑在 py3.9，无法 import 本包）。
    两份实现必须在三类输入上给出相同的**等价关系**：
    完全相同、仅注释不同、行为不同。
    """
    tool_path = ROOT / "tools/check_engineering.py"
    spec = importlib.util.spec_from_file_location("_gate_probe", tool_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    gate = module._semantic_digest_of_python

    # 相同输入 → 相同
    assert gate(_ORIGINAL) == gate(_ORIGINAL)
    # 仅注释不同 → 相同（关键）
    assert gate(_ORIGINAL) == gate(_COMMENT_ONLY)
    # 行为不同 → 不同（关键）
    assert gate(_ORIGINAL) != gate(_BEHAVIOUR_CHANGED)


def test_hostile_input_does_not_crash_semantic_digest(tmp_path):
    """语法错误的源码应抛 SyntaxError，而不是静默返回摘要。"""
    with pytest.raises(SyntaxError):
        semantic_digest_of_text("def broken(:\n", ".py")
