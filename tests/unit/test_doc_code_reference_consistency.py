"""**统一**的"文档 → 代码"引用一致性检查。

## 为什么合并成一个文件

2026-11-13 本会话陆续发现三类失效引用，每次都是**偶然撞上**的：

| 类型 | 例子 | 发现轮次 |
|:---|:---|:---:|
| 文件路径 | `behavioral/follower.py`（实际在 `control/`）| 83 |
| 属性锚点 | `config.c_total0`（属性不存在）| 74 |
| 函数引用 | `mapper.dither_transfer_code()`（函数已删）| 74 |
| 测试文件 | `tests/unit/test_patent14_ref_track.py`（不存在）| 84 |

分散的守卫只能防止"已发现的形态"回流，**下一类仍然会漏**。
本文件把检查集中起来，一次覆盖四类。

## 设计约束

* **只检查现行文档**（排除 history / evidence / reports / reviews / 迁移记录）。
  那些文档记录的是当时的状态，引用旧路径是**正确的**。
* **只对该仓库自己的标识符**做检查——外部概念（`AttributeError`、
  `VERILATOR`、`ELABORATED_ONLY`）不是本仓库的符号，不做校验。
* 出现"失效是已知且有说明"时（文档显式写了"已不存在/已改名/已移除"），
  该文档豁免——那是有意的历史记录。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 历史/过程文档：引用旧路径是**正确**的，不参与检查。
_HISTORICAL_PREFIXES = (
    "docs/history/",
    "docs/evidence/",
    "docs/reports/",
    "docs/reviews/",
    "docs/restructure/migration_table.md",
    "docs/migration.md",
)
_HISTORICAL_CONTAINS = ("review_response", "audit_response")


def _current_documents() -> list[Path]:
    """README 与 docs/ 下的现行文档。"""
    documents = [ROOT / "README.md", ROOT / "README_EN.md"]
    for path in sorted((ROOT / "docs").rglob("*.md")):
        rel = str(path.relative_to(ROOT))
        if rel.startswith(_HISTORICAL_PREFIXES) or any(tag in rel for tag in _HISTORICAL_CONTAINS):
            continue
        documents.append(path)
    return [path for path in documents if path.is_file()]


def _is_self_exempted(text: str) -> bool:
    """文档是否显式声明了"某些引用已失效"（有意的历史记录）。"""
    markers = ("已不存在", "已改名", "已移除", "已删除", "不再存在", "已废弃")
    return any(marker in text for marker in markers)


def _source_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "src").rglob("*.py")
    )


@pytest.fixture(scope="module")
def corpus():
    documents = [(p, p.read_text(encoding="utf-8")) for p in _current_documents()]
    return documents, _source_text()


def test_file_paths_referenced_by_documents_exist(corpus):
    """文档里 `xxx/yyy.py` 形式的路径必须能在仓库里解析到。"""
    documents, _ = corpus
    pattern = re.compile(r"`((?:src|tests|tools|sim|verification|experiments)/[\w/]+\.py)`")
    missing: list[str] = []
    for path, text in documents:
        if _is_self_exempted(text):
            continue
        for match in pattern.finditer(text):
            target = match.group(1)
            if not (ROOT / target).is_file():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, "documents reference files that do not exist:\n  " + "\n  ".join(missing)


def test_function_references_in_documents_exist(corpus):
    """文档里 `module.func()` 引用的函数必须存在于 `src/`。"""
    documents, source = corpus
    pattern = re.compile(r"`([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\(\)`")
    missing: list[str] = []
    for path, text in documents:
        if _is_self_exempted(text):
            continue
        for match in pattern.finditer(text):
            function = match.group(2)
            if function not in source:
                missing.append(f"{path.relative_to(ROOT)} -> {function}()")
    assert not missing, "documents reference functions absent from src/:\n  " + "\n  ".join(missing)


def test_attribute_anchors_in_documents_exist(corpus):
    """文档里 `config.attr` / `chip.attr` 形式的代码锚点必须存在于 `src/`。"""
    documents, source = corpus
    pattern = re.compile(
        r"`(?:config|cfg|chip|model|DefaultModel|DefaultParameters|pool)\.([a-z_][a-z0-9_]*)`"
    )
    missing: list[str] = []
    for path, text in documents:
        if _is_self_exempted(text):
            continue
        for match in pattern.finditer(text):
            attribute = match.group(1)
            if attribute not in source:
                missing.append(f"{path.relative_to(ROOT)} -> .{attribute}")
    assert not missing, "documents reference attributes absent from src/:\n  " + "\n  ".join(missing)


def test_test_files_named_by_documents_exist(corpus):
    """文档里点名的 `tests/**.py` 必须存在。

    2026-11-13：`fig19_fig17_decode.md` 与 `reproduction_results.md`
    都点名了不存在的测试文件（`test_fig11_codebook_units.py`、
    `test_patent14_ref_track.py`），声称某性质"由该测试钉住"——
    实际上那个测试不存在。
    """
    documents, _ = corpus
    pattern = re.compile(r"`(tests/[\w/]+\.py)`")
    missing: list[str] = []
    for path, text in documents:
        if _is_self_exempted(text):
            continue
        for match in pattern.finditer(text):
            target = match.group(1)
            if not (ROOT / target).is_file():
                missing.append(f"{path.relative_to(ROOT)} -> {target}")
    assert not missing, (
        "documents claim a property is pinned by a test file that does not exist:\n  "
        + "\n  ".join(missing)
    )
