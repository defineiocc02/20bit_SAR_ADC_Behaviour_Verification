# =============================================================================
# 文件名     : hashing.py
# 模块名     : adi_model.hashing
# 版本       : v1.0
# 功能       : 文件内容摘要，供收据与归档清单共用。
#
# 设计说明   :
#   * 收据里记的是「这份字节」，不是「这个文件名」。同名不同内容必须能被区分，
#     否则归档结论会悄悄指向另一份源码。
#   * 同一函数原先在 inl_dnl.py 与 full20_linearity_plots.py 各写一遍，
#     两处实现相同但各自演进，属重复定义；此处合并为一处。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# [归位] 由 task1/experiments/{inl_dnl,full20_linearity_plots}.py 的 _sha 合并而来。
# [新增] semantic_digest：把「字节摘要」与「语义摘要」分开，供收据区分注释改造与功能改动。
# =============================================================================

"""文件内容摘要工具。
    **当前状态（2026-11-13 核实）：**
    * `src/` 内**没有任何模块 import 它**（它不是主链的一部分）；
    * 有单测覆盖：`tests/unit/test_hashing.py`；
    * `tools/check_engineering.py` 的 C4 门禁需要"忽略注释与 docstring 的语义摘要"，
      但该工具跑在系统 python 3.9 下、无法 import 本包，因此**内联了一份等价实现**
      （`_semantic_digest_of_python`）。两份实现的判据一致性由
      `test_gate_helper_agrees_with_the_library` 守着。

    ⇒ 所以本模块**不是死代码**，但它也**不是主链依赖**。
    如果将来主链要用文件/源码摘要，应当 import 这里而不是再写一遍。
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    """返回文件内容的 SHA-256 十六进制摘要。

    按字节读，不做换行或编码归一化：收据要能发现任何字节级改动，
    包括只改了一个注释字符这种情况。
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_python(text: str) -> str:
    """去掉注释与文档字符串后的 AST 摘要，用于判断「语义是否变了」。"""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def _strip_systemverilog(text: str) -> str:
    """去掉注释与空行后的逻辑行序列。

    不做词法分析，只处理块注释、行注释与字符串字面量三种情况；
    行尾注释连同其前的空白一起去掉，使「只改注释」不产生差异。
    """
    out, i, n = [], 0, len(text)
    in_string = in_block = in_line = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_line:
            if ch == "\n":
                in_line = False
                out.append("\n")
            i += 1
            continue
        if in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                i += 2
                continue
            if ch == "\n":
                out.append("\n")
            i += 1
            continue
        if in_string:
            out.append(ch)
            if ch == "\\":
                out.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    lines = (line.rstrip() for line in "".join(out).splitlines())
    return "\n".join(line for line in lines if line)


def semantic_digest_of_text(text: str, suffix: str) -> str:
    """对**内存中的文本**返回语义摘要（忽略注释与文档字符串）。

    与 `semantic_digest(path)` 的区别只是输入形态：本函数接受字符串，
    便于比较"两个提交里的同一文件"——那时两边内容都在内存里
    （旧版来自 `git show`，新版来自工作树），不落盘。

    Args:
        text: 源码文本。
        suffix: 语言判据（`.py` / `.sv` / 其他）。

    Returns:
        十六进制摘要；只改注释或 docstring 时与改动前相同。
    """
    if suffix == ".py":
        payload = _strip_python(text)
    elif suffix in {".sv", ".v", ".svh", ".vh"}:
        payload = _strip_systemverilog(text)
    else:
        payload = text
    return hashlib.sha256(payload.encode()).hexdigest()


def semantic_digest(path: Path) -> str:
    """返回「忽略注释与文档字符串」的语义摘要。

    与 sha256_file 成对使用：字节摘要回答「还是不是同一份文件」，
    语义摘要回答「改动有没有越过注释层」。只改注释、格式或 docstring 时
    字节摘要必然变化而语义摘要不变，这正是把「注释改造」与「功能改动」
    分开记账所需的区分度。
    """
    return semantic_digest_of_text(path.read_text(errors="replace"), path.suffix)
