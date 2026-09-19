#!/usr/bin/env python3
r"""SystemVerilog 子集变异注入器（VeriBugBench 算子的**转用**实现）。

## 这是什么

按 ``docs/rtl/MUTATION_AUDIT_VS_VERIBUGBENCH.md`` §6.1 自写的注入器：每条算子 =
**matcher + guard + transform** 三件套，只改**副本**（把 ``rtl/core`` + ``rtl/top``
拷到 ``<out-root>/<name>/rtl/`` 再改），**原 ``rtl/`` 一个字节都不动**。

## 转用声明（与审计文档 §0 同句搬运，不得删）

论文（VeriBugBench, arXiv:2609.18022）的测度描写的是"基准构造与产物质量，
**而非某个调试技术的性能**"。我们拿它的算子分类与保留判据来量**我们自己 TB 的
故障检出强度**，属**转用**；**不得**反过来声称"我们的 TB 达到了该基准的某个水平"。

## ⚠️ 与论文的算子实现**不可逐条对照**（论文 §5.6 / 本文件 §"算子边界"）

论文前端是 **Pyverilog（Verilog 子集）**，本项目 ``rtl/`` 是 **SystemVerilog**
（``logic`` / ``always_ff`` / ``'0`` / ``$signed`` / 打包数组），Pyverilog 解析不了。
故本注入器**不是**论文算子的等价实现，而是：在**本项目 SV 子集**内用
**行内联文本匹配 + 显式守卫**做出的近似；每条算子的 ``BOUNDS`` 里写明了它在本子集下
的适用边界与已知不覆盖情形。**可对照的只有"分类"与"保留判据"，不是实现细节。**

## 用法

    # 清单：枚举合法注入点 + 每个算子的机会总数（含按文件分解）
    python tools/mutate_rtl.py --list
    python tools/mutate_rtl.py --json > inventory.json

    # 精确注入一个已知位点（端到端验证用）
    python tools/mutate_rtl.py --op ExprUpdate --file rtl/core/calib_regs.sv --line 107 \\
        --name cb_le_eq --out-root sim/artifacts/mut

    # 均匀抽样 b 个（论文 b=20；Phase 1a 只验机制，不跑 campaign）
    python tools/mutate_rtl.py --op ExprDelete --sample 3 --seed 20260919
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RTL_ROOT = REPO_ROOT / "rtl"
SRC_SUBDIRS = ("core", "top")
DEFAULT_OUT_ROOT = REPO_ROOT / "sim" / "artifacts" / "mut"

# ---------------------------------------------------------------------------
# 站点与算子
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """一个合法注入点：``rel`` 第 ``line`` 行的 ``[col, col+len(span))`` 换成 ``repl``。"""

    op: str
    family: str
    action: str
    rel: str
    line: int
    col: int
    span: str
    repl: str
    note: str = ""

    @property
    def key(self) -> str:
        """本注入点的稳定唯一键（用于去重与"同一变异复现"）。"""
        return f"{self.rel}:{self.line}:{self.col}:{self.op}"


@dataclass(frozen=True)
class Operator:
    """一条变异算子 = **matcher + guard + transform** 三件套 + 本 SV 子集下的边界声明。"""

    name: str
    family: str
    action: str
    bounds: str
    find: Callable[[str, list[str]], list[Site]]


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

_LITERAL_RE = re.compile(r"^(?:\d+'[sSbBoOdDhH][0-9a-fA-F_xXzZ]+|\d+)$")
_IDENT_RE = re.compile(r"^[A-Za-z_]\w*$")


def _code(line: str) -> str:
    """去掉行尾 ``//`` 注释；本 RTL 无字符串字面量，故朴素切割安全。"""
    i = line.find("//")
    return line if i < 0 else line[:i]


def _is_literal(tok: str) -> bool:
    return bool(_LITERAL_RE.match(tok.strip()))


def _is_ident(tok: str) -> bool:
    return bool(_IDENT_RE.match(tok.strip()))


def _lhs_tokens_before(line: str, idx: int, width: int = 48) -> str:
    return line[max(0, idx - width) : idx]


def _is_stmt_nba(line: str) -> bool:
    """粗略判定：本行是**语句级**非阻塞赋值（``lhs <= ...``），不是比较 ``<=``。

    判据：行首（去空白）起是"简单左值 + 可选位选"后紧跟 ``<=``，且末尾为 ``;``。
    本 RTL 的非阻塞赋值都是一行一条、左值形式规整，故该判据足够；边界见各算子 BOUNDS。
    """
    code = _code(line).rstrip()
    if not code.endswith(";"):
        return False
    m = re.match(r"^\s*([A-Za-z_]\w*(?:\s*\[[^\]]*\])*)\s*<=\s*(.+);\s*$", code)
    if not m:
        return False
    lhs = m.group(1)
    if lhs.startswith(("if", "while", "for", "assert")):
        return False
    return "(" not in lhs


def _nba_parts(line: str) -> tuple[str, str, str] | None:
    """返回 ``(缩进, 左值, 右值)``；不是语句级非阻塞赋值则 ``None``。"""
    if not _is_stmt_nba(line):
        return None
    code = _code(line)
    indent = code[: len(code) - len(code.lstrip())]
    body = code.strip().rstrip(";")
    lhs, rhs = body.split("<=", 1)
    return indent, lhs.strip(), rhs.strip()


def _top_level_terms(expr: str, joiner: str) -> list[str]:
    """按**顶层** ``joiner`` 切分（不切括号内的）。"""
    parts, depth, cur = [], 0, []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0 and expr.startswith(joiner, i):
            parts.append("".join(cur))
            cur = []
            i += len(joiner)
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _find_top_joiners(expr: str, joiner: str) -> list[tuple[int, int]]:
    """返回该表达式里**顶层** ``joiner`` 的 ``[(start, end)]``（相对 expr 的下标）。

    单字符连接词（``&`` / ``|`` / ``^``）必须排除它是 ``&&`` / ``||`` 一半的情形，
    否则 ``&&`` 会被数成两个 ``&`` —— 那正是第一版切错顶层项的根因之一。
    """
    out, depth, i = [], 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if depth == 0 and expr.startswith(joiner, i):
            if len(joiner) == 1:
                prev = expr[i - 1] if i > 0 else ""
                nxt = expr[i + 1] if i + 1 < len(expr) else ""
                if prev == joiner or nxt == joiner:
                    i += 1
                    continue
            out.append((i, i + len(joiner)))
            i += len(joiner)
            continue
        i += 1
    return out


def _expr_region_start(code: str) -> int:
    """返回本行**表达式起点**：顶层语句级赋值号（``=`` 或 ``<=``）之后的位置；无则 0。

    为什么需要它：本注入器是行级的，若直接对整行找 ``&&``，则"第 0 项"其实是
    ``assign w_ok = wr_en`` 这一整段前缀 —— 删掉它会毁行（第一版就产出过
    ``wr_en  (!cfg_ready) && …`` 这种非法语法）。限定在赋值号右侧后，
    "第 k 项"才是真正的操作数。
    """
    depth, last, i = 0, -1, 0
    while i < len(code):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in _OPCHARS:
            tok = _operator_token_at(code, i)
            if tok in ("=", "<="):
                last = i + len(tok)
            i += max(len(tok), 1)
            continue
        i += 1
    return last if last >= 0 else 0


def _read_text(path: Path) -> str:
    """按**原始字节**读文本：不把 CRLF 归一成 LF。

    为什么必须这样读：本仓库在 Windows worktree 上的检出是 **CRLF**（提交里的 blob 是 LF，
    见 ``git show HEAD:sim/run_vcs.sh``），而"副本除注入点外与原文件逐字节相同"这条
    保真性声明要求**行尾也不能被顺手改掉**。用 ``Path.read_text()`` 会把 CRLF 归一成 LF，
    于是所有变异副本都会静默地把整个文件的行尾改掉 —— 那会让"只改了一处"变成假话。
    """
    with path.open("r", encoding="utf-8", newline="") as fh:
        return fh.read()


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _detect_nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _port_decl(line: str) -> bool:
    return bool(re.match(r"^\s*(input|output|inout)\b", _code(line)))


_PARTSELECT_RE = re.compile(r"\[([^\[\]:]+):([^\[\]:]+)\]")
_INDEX_RE = re.compile(r"\[([^\[\]:]+)\]")


# ---------------------------------------------------------------------------
# 算子实现
# ---------------------------------------------------------------------------


_OPCHARS = set("<>=!&|^~+*/%-")
_OPTOKEN_RE = re.compile(r"[<>=!&|^~+*/%-]+")


def _operator_token_at(code: str, i: int) -> str:
    """返回 ``code[i]`` 所在的那个**极大算子 token**（如 ``&&`` / ``<=`` / ``>>>``）。

    这是 ExprUpdate 的关键守卫：否则 ``&`` 会匹配到 ``&&`` 里、``<`` 会匹配到 ``<=`` 里，
    产出 ``|&`` 这种非法语法（本注入器第一版就这么错过，实测产出不可编译产物）。
    """
    if i < 0 or i >= len(code) or code[i] not in _OPCHARS:
        return ""
    a = i
    while a > 0 and code[a - 1] in _OPCHARS:
        a -= 1
    b = i
    while b < len(code) - 1 and code[b + 1] in _OPCHARS:
        b += 1
    return code[a : b + 1]


def _expr_update(rel: str, lines: list[str]) -> list[Site]:
    """Expression / update：把**二元算子**换成同族的另一个（含关系算子）。"""
    menu = [
        (">>>", ">>"),
        ("<<", ">>"),
        (">>", "<<"),
        ("+", "-"),
        ("&", "|"),
        ("^", "&"),
        ("<=", "<"),
        ("<", "<="),
        (">=", ">"),
        (">", ">="),
    ]
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        if not code.strip() or _port_decl(code):
            continue
        # 语句级非阻塞赋值里的那一个 `<=` 是赋值算子，不是关系算子 -> 只跳过它
        nba_le = code.index("<=") if _is_stmt_nba(code) else -1
        for op, new in menu:
            hit = False
            for m in _OPTOKEN_RE.finditer(code):
                if m.group(0) != op:
                    continue
                i, j = m.start(), m.end()
                if i == nba_le:
                    continue
                left_txt = code[:i]
                rt = re.findall(
                    r"[A-Za-z_]\w*|\d+'[sSbBoOdDhH][0-9a-fA-F_xXzZ]+|\d+",
                    code[j : j + 40],
                )
                lt = re.findall(
                    r"[A-Za-z_]\w*|\d+'[sSbBoOdDhH][0-9a-fA-F_xXzZ]+|\d+", left_txt[-40:]
                )
                if not lt or not rt:
                    continue
                if _is_literal(lt[-1]) and _is_literal(rt[0]):
                    continue  # 纯字面量表达式（论文把这类排除在 ExprUpdate 之外）
                out.append(
                    Site(
                        "ExprUpdate",
                        "Expression",
                        "update",
                        rel,
                        ln,
                        i,
                        op,
                        new,
                        f"{lt[-1]} {op} {rt[0]} -> {new}",
                    )
                )
                hit = True
                break
            if hit:
                break  # 每行每算子只取一个位点，避免重叠编辑
    return out


def _expr_delete(rel: str, lines: list[str]) -> list[Site]:
    """Expression / delete：删掉一个**顶层**项（``ws_capoff`` / ``sr_nonsticky`` 形态）。

    只在**赋值号右侧**的表达式里切（见 ``_expr_region_start``），连接词覆盖
    ``&&`` / ``||`` / ``&`` / ``|`` / ``^``（后者使 ``acc_ovf_sticky | ev_acc_ovf``
    这类"自保持项 + 事件项"也能被删，即审计文档 §2 的 #11 形态）。

    删第 ``k`` 项的切法：``k == 0`` 删 ``term0 + joiner0``；否则删 ``joiner_{k-1} + term_k``。
    守卫：① 连接词 >= 1 且项 >= 2；② **所有项都非空**（否则 ``assign x = |main_sw;``
    这类归约算子的空首项会被删成 ``assign x = ;``）；③ 末项的删除不吞行尾分号。
    """
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        if not code.strip():
            continue
        stripped = code.rstrip()
        limit = len(stripped) - 1 if stripped.endswith(";") else len(stripped)
        rs = _expr_region_start(code)
        if rs >= limit:
            continue
        body = code[rs:limit]
        for joiner in ("&&", "||", "|", "&", "^"):
            js = _find_top_joiners(body, joiner)
            if not js:
                continue
            terms = _top_level_terms(body, joiner)
            if len(terms) < 2 or not all(t.strip() for t in terms):
                continue
            n = len(terms)
            for k in range(n):
                if k == 0:
                    span_s, span_e = rs, rs + js[0][1]
                else:
                    t_end = js[k][0] if k < n - 1 else (limit - rs)
                    span_s, span_e = rs + js[k - 1][0], rs + t_end
                while span_s > rs and code[span_s - 1] == " ":
                    span_s -= 1  # 顺手吞掉前导空白，免得留下 `w_ok ;`
                out.append(
                    Site(
                        "ExprDelete",
                        "Expression",
                        "delete",
                        rel,
                        ln,
                        span_s,
                        code[span_s:span_e],
                        "",
                        f"删顶层第 {k} 项（共 {n} 项，连接词 {joiner}）：{terms[k].strip()[:44]!r}",
                    )
                )
            break  # 每行只按一种连接词切（按上面的优先级）
    return out


_BV_DECL_RE = re.compile(r"\blogic\s+(?:signed\s+)?\[[^\]]+\]\s+([A-Za-z_]\w*)")


def _expr_insert(rel: str, lines: list[str]) -> list[Site]:
    """Expression / insert（**有限**形式）：在非阻塞赋值右值前插入一元 ``~``。"""
    decls = set()
    for raw in lines:
        for m in _BV_DECL_RE.finditer(_code(raw)):
            decls.add(m.group(1))
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        parts = _nba_parts(raw)
        if parts is None:
            continue
        _indent, _lhs, rhs = parts
        rhs_c = _code(raw)
        i = rhs_c.rfind(rhs)
        if i < 0:
            continue
        if rhs.startswith("~") or rhs.startswith("!") or rhs.startswith("-"):
            continue
        head = re.match(r"^([A-Za-z_]\w*)", rhs)
        if not head or head.group(1) not in decls:
            continue  # 只对同文件里声明过的位向量标识符做，避免类型/函数误伤
        out.append(
            Site(
                "ExprInsert",
                "Expression",
                "insert",
                rel,
                ln,
                i,
                rhs,
                f"~({rhs})",
                f"在右值 {rhs[:32]!r} 前插入一元 ~",
            )
        )
    return out


def _partselect_update(rel: str, lines: list[str]) -> list[Site]:
    """Bit-vector & access / update：``[A:B]`` 边界对调。"""
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        if not code.strip() or _port_decl(code):
            continue  # 端口位宽归 IOFlip，保持构族不重叠
        m = _PARTSELECT_RE.search(code)
        if not m:
            continue
        a, b = m.group(1).strip(), m.group(2).strip()
        if a == b:
            continue
        out.append(
            Site(
                "PartselectUpdate",
                "Bit-vector & access",
                "update",
                rel,
                ln,
                m.start(1),
                f"{m.group(1)}:{m.group(2)}",
                f"{b}:{a}",
                f"[{a}:{b}] -> [{b}:{a}]",
            )
        )
    return out


def _partselect_delete(rel: str, lines: list[str]) -> list[Site]:
    """Bit-vector & access / delete：``[A:B]`` 收缩成最高位单比特 ``[A:A]``。"""
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        if not code.strip() or _port_decl(code):
            continue
        m = _PARTSELECT_RE.search(code)
        if not m:
            continue
        a = m.group(1).strip()
        # 赋值左值上的位选不动（宽度失配必然编译失败，属"被判据①丢弃"而非本算子目标）
        before = code[: m.start()]
        if before.strip() and "," not in before and "=" not in before and "<=" not in before:
            continue
        out.append(
            Site(
                "PartselectDelete",
                "Bit-vector & access",
                "delete",
                rel,
                ln,
                m.start(),
                m.group(0),
                f"[{a}]",
                f"[{m.group(1)}:{m.group(2)}] -> [{a}]",
            )
        )
    return out


def _pointer_update(rel: str, lines: list[str]) -> list[Site]:
    """Bit-vector & access / update：单索引 ``[X]`` 做 ±1 扰动（X 非纯字面量）。"""
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        if not code.strip() or _port_decl(code):
            continue
        for m in _INDEX_RE.finditer(code):
            x = m.group(1).strip()
            if _is_literal(x) or not x:
                continue
            if _is_ident(x):
                new = f"{x} + 1"
            else:
                mm = re.match(r"^([A-Za-z_]\w*)\s*([+-])\s*(\d+)$", x)
                if not mm:
                    continue  # 复杂索引留给人工，边界见 BOUNDS
                base, sign, k = mm.group(1), mm.group(2), int(mm.group(3))
                new = f"{base} {sign} {k + 1}" if sign == "+" else f"{base} - {max(k - 1, 0)}"
            out.append(
                Site(
                    "PointerUpdate",
                    "Bit-vector & access",
                    "update",
                    rel,
                    ln,
                    m.start(1),
                    m.group(1),
                    new,
                    f"[{x}] -> [{new}]",
                )
            )
            break
    return out


def _nsub_update(rel: str, lines: list[str]) -> list[Site]:
    """Assignment / update：``lhs <= rhs`` -> ``lhs <= ~(rhs)``。"""
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        parts = _nba_parts(raw)
        if parts is None:
            continue
        _indent, lhs, rhs = parts
        if lhs in ("clk", "rst_n") or rhs.startswith("~("):
            continue
        if re.search(r"\w\s*\(", rhs) and not rhs.startswith("("):
            continue  # 右值是函数调用时不动（类型未知），边界见 BOUNDS
        code = _code(raw)
        i = code.rfind(rhs)
        if i < 0:
            continue
        out.append(
            Site(
                "NSubUpdate",
                "Assignment",
                "update",
                rel,
                ln,
                i,
                rhs,
                f"~({rhs})",
                f"{lhs} <= {rhs[:32]!r} 取按位反",
            )
        )
    return out


def _nsub_delete(rel: str, lines: list[str]) -> list[Site]:
    """Assignment / delete：整条非阻塞赋值语句注释掉（**行数不变**，便于回溯）。"""
    nb = [ln for ln, raw in enumerate(lines, 1) if _is_stmt_nba(raw)]
    if len(nb) < 2:
        return []  # 只剩 0/1 条赋值的 always 块不删，避免把块删空（边界见 BOUNDS）
    out: list[Site] = []
    for ln in nb:
        raw = lines[ln - 1]
        out.append(
            Site(
                "NSubDelete",
                "Assignment",
                "delete",
                rel,
                ln,
                0,
                raw,
                f"// [MUT NSubDelete] {raw.strip()}",
                "整条非阻塞赋值被注释掉",
            )
        )
    return out


def _nsub_move(rel: str, lines: list[str]) -> list[Site]:
    """Assignment / move：交换**相邻两行**同缩进的非阻塞赋值语句。"""
    out: list[Site] = []
    for ln in range(1, len(lines)):
        a, b = lines[ln - 1], lines[ln]
        if not (_is_stmt_nba(a) and _is_stmt_nba(b)):
            continue
        ia = len(a) - len(a.lstrip())
        ib = len(b) - len(b.lstrip())
        if ia != ib:
            continue
        out.append(
            Site(
                "NSubMove",
                "Assignment",
                "move",
                rel,
                ln,
                0,
                a + "\n" + b,
                b + "\n" + a,
                "相邻两条非阻塞赋值互换位置",
            )
        )
    return out


_EDGE_RE = re.compile(r"always_ff\s*@\s*\(\s*(posedge|negedge)\s+([A-Za-z_]\w*)\s*\)")


def _edge_flip(rel: str, lines: list[str]) -> list[Site]:
    """Timing / update：``always_ff @(posedge clk)`` -> ``@(negedge clk)``。"""
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        m = _EDGE_RE.search(code)
        if not m:
            continue
        edge = m.group(1)
        new = "negedge" if edge == "posedge" else "posedge"
        i = code.index(edge, m.start())
        out.append(
            Site(
                "EdgeFlip",
                "Timing",
                "update",
                rel,
                ln,
                i,
                edge,
                new,
                f"@({edge} {m.group(2)}) -> @({new} {m.group(2)})",
            )
        )
    return out


def _edge_insert(rel: str, lines: list[str]) -> list[Site]:
    """Timing / insert：给单沿事件控制插入一条异步复位沿（仅当模块确有 rst_n 端口）。"""
    if not any(re.search(r"\brst_n\b", ln) for ln in lines):
        return []
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        m = _EDGE_RE.search(code)
        if not m:
            continue
        inner = m.group(0)
        close = inner.rfind(")")  # 事件控制里最后一个 ')' 之前插入
        if close < 0:
            continue
        i = m.start() + close
        out.append(
            Site(
                "EdgeInsert",
                "Timing",
                "insert",
                rel,
                ln,
                i,
                "",
                " or negedge rst_n",
                f"{inner} -> {inner[:close]} or negedge rst_n)",
            )
        )
    return out


_MULTIEDGE_RE = re.compile(r"@\s*\(\s*[^)]*\bor\b[^)]*\)")


def _edge_delete(rel: str, lines: list[str]) -> list[Site]:
    """Timing / delete：从**多沿**事件控制里删掉一个沿。

    本子集里全部是单沿 ``always_ff @(posedge clk)``（实测 16/16），删掉唯一一个沿后
    ``always_ff`` 没有事件控制 = 非法 SV，故**机会数为 0** —— 这是"不可达"而非"漏测"。
    """
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        m = _MULTIEDGE_RE.search(code)
        if not m:
            continue
        inner = m.group(0)
        body = inner[inner.index("(") + 1 : inner.rfind(")")]
        parts = [p.strip() for p in re.split(r"\bor\b", body)]
        if len(parts) < 2:
            continue
        if " or " not in inner:
            continue
        i = code.index(inner) + inner.index(" or ")
        out.append(
            Site(
                "EdgeDelete",
                "Timing",
                "delete",
                rel,
                ln,
                i,
                " or " + parts[-1],
                "",
                f"删掉事件控制里的一个沿：{parts[-1]}",
            )
        )
    return out


_PORT_W_RE = re.compile(
    r"^\s*(?P<dir>input|output|inout)\s+(?P<attr>logic\s+(?:signed\s+)?)\[(?P<a>[^\[\]:]+):(?P<b>[^\[\]:]+)\]\s+"
)


def _io_flip(rel: str, lines: list[str]) -> list[Site]:
    """Port / update：端口**位宽**边界对调，或**方向** input<->output。

    边界：方向翻转在本子集里多数会造成多驱动/悬空 -> 被保留判据①（编译）丢弃；
    这不是算子的缺陷，而是"该构族在本设计上大多不可保留"的事实，故两种模式都枚举、都报数。
    """
    out: list[Site] = []
    for ln, raw in enumerate(lines, 1):
        code = _code(raw)
        m = _PORT_W_RE.match(code)
        if m:
            a, b = m.group("a").strip(), m.group("b").strip()
            if a != b:
                out.append(
                    Site(
                        "IOFlip",
                        "Port",
                        "update",
                        rel,
                        ln,
                        m.start("a"),
                        f"{m.group('a')}:{m.group('b')}",
                        f"{b}:{a}",
                        f"端口位宽 [{a}:{b}] -> [{b}:{a}]",
                    )
                )
                continue
        d = re.match(r"^\s*(input|output)\b", code)
        if d:
            cname = re.findall(r"[A-Za-z_]\w*", code.split("//")[0])
            if any(x in ("clk", "rst_n") for x in cname):
                continue
            word = d.group(1)
            new = "output" if word == "input" else "input"
            i = code.index(word)
            out.append(
                Site(
                    "IOFlip",
                    "Port",
                    "update",
                    rel,
                    ln,
                    i,
                    word,
                    new,
                    f"端口方向 {word} -> {new}（多数会被编译淘汰，见 BOUNDS）",
                )
            )
    return out


OPERATORS: tuple[Operator, ...] = (
    Operator(
        "ExprUpdate",
        "Expression",
        "update",
        "只替换**二元算子**（算术/移位/位/关系）；**排除纯字面量表达式**（论文同款排除）。"
        "不处理一元算子、不处理 `?:`、不处理 `case` 项。每行每算子只取第一个位点。",
        _expr_update,
    ),
    Operator(
        "ExprDelete",
        "Expression",
        "delete",
        "只删**顶层**（未被括号包住）`&&`/`||` 的一个项，且要求顶层项数 >= 2。"
        "不处理嵌套在括号内的合取项、不处理 `?:` 分支。每行每算子只取第一个位点。",
        _expr_delete,
    ),
    Operator(
        "ExprInsert",
        "Expression",
        "insert",
        "**有限形式**：只在*语句级非阻塞赋值*的右值前插入一元 `~`，且右值首标识符必须"
        "是**同文件内**声明过的位向量（`logic [..]`）。不做任意子表达式插入 —— "
        "本子集没有类型推断，任意插入会大量产生不可编译产物（那是噪声不是覆盖）。",
        _expr_insert,
    ),
    Operator(
        "PartselectUpdate",
        "Bit-vector & access",
        "update",
        "只对 `[A:B]` 边界对调；排除端口声明行（归 IOFlip，保持构族不重叠）。"
        "不处理 `+:`/`-:` 定宽位选、不处理多维位选链。每行只取第一个位点。",
        _partselect_update,
    ),
    Operator(
        "PartselectDelete",
        "Bit-vector & access",
        "delete",
        "把 `[A:B]` 收缩成最高位单比特 `[A]`；排除端口声明行与『显然在赋值左值上』的位选"
        "（那类宽度失配必然编译失败）。已知副作用：仍有一部产物会因宽度失配被判据①丢弃。",
        _partselect_delete,
    ),
    Operator(
        "PointerUpdate",
        "Bit-vector & access",
        "update",
        "只对单索引 `[X]` 做 `+1`（或 `id-k` -> `id-(k-1)`）扰动，且 X **不是纯字面量**；"
        "复杂索引表达式（含函数调用/多算子）不处理。越界读会产生 X —— 按论文判据 X 计为已观测值。",
        _pointer_update,
    ),
    Operator(
        "NSubUpdate",
        "Assignment",
        "update",
        "只改*语句级非阻塞赋值*的右值为 `~(rhs)`；右值是函数调用（含 `(`）时不改；"
        "左值为 `clk`/`rst_n` 时跳过。不处理 `<= #delay`、不处理多左值。",
        _nsub_update,
    ),
    Operator(
        "NSubDelete",
        "Assignment",
        "delete",
        "只删*单行、语句级*的非阻塞赋值；本文件同类语句 < 2 条时不删（避免把 always 块删空）。"
        "删除以**注释形式**落回原行，故**行号不变**（便于按行回溯）。",
        _nsub_delete,
    ),
    Operator(
        "NSubMove",
        "Assignment",
        "move",
        "只交换**相邻两行且缩进相同**的非阻塞赋值语句（即 `recon_core` 溢出锁存那类缺陷的形态）。"
        "不跨 `if/else` 分支搬移、不跨 `begin/end`、不做远距离重排。",
        _nsub_move,
    ),
    Operator(
        "EdgeFlip",
        "Timing",
        "update",
        "只翻 `always_ff @(posedge X)` 的沿（`posedge` <-> `negedge`）。仅 `always_ff`；"
        "不处理 `always @(...)`（本子集为 0 处）与 `always_comb`（无事件控制）。",
        _edge_flip,
    ),
    Operator(
        "EdgeInsert",
        "Timing",
        "insert",
        "给**单沿** `always_ff` 事件控制插入 ` or negedge rst_n`，且仅当该文件确实有 `rst_n`。"
        "本子集的复位是同步的，故该插入改变的是复位语义（这正是它作为一种注入的价值）。",
        _edge_insert,
    ),
    Operator(
        "EdgeDelete",
        "Timing",
        "delete",
        "只删**多沿**事件控制里的一个沿。⚠️ 本子集 `always_ff` 全是单沿 ⇒ **机会数实测 0**，"
        "这是『不可达（删掉唯一沿后 `always_ff` 非法）』而不是『漏测』；与 IOFlip 的方向翻转同类。",
        _edge_delete,
    ),
    Operator(
        "IOFlip",
        "Port",
        "update",
        "两种模式：① 端口**位宽**边界对调 `[A:B]` -> `[B:A]`；② 端口**方向** `input` <-> `output`"
        "（`clk`/`rst_n` 跳过）。边界：方向翻转多数造成多驱动/悬空，会被保留判据①（编译）丢弃 —— "
        "报数时『机会数』与『可保留数』必须分开说，不得混为一谈。",
        _io_flip,
    ),
)


# ---------------------------------------------------------------------------
# 枚举 / 注入
# ---------------------------------------------------------------------------


def rtl_files(rtl_root: Path) -> list[Path]:
    out: list[Path] = []
    for sub in SRC_SUBDIRS:
        out.extend(sorted((rtl_root / sub).glob("*.sv")))
    return out


def enumerate_sites(rtl_root: Path = RTL_ROOT) -> list[Site]:
    sites: list[Site] = []
    for path in rtl_files(rtl_root):
        rel = path.relative_to(rtl_root.parent).as_posix()
        lines = _read_text(path).splitlines()
        for op in OPERATORS:
            sites.extend(op.find(rel, lines))
    return sites


def inventory(rtl_root: Path = RTL_ROOT) -> dict:
    sites = enumerate_sites(rtl_root)
    per_op: dict[str, dict] = {}
    for op in OPERATORS:
        mine = [s for s in sites if s.op == op.name]
        by_file: dict[str, int] = {}
        for s in mine:
            by_file[s.rel] = by_file.get(s.rel, 0) + 1
        per_op[op.name] = {
            "family": op.family,
            "action": op.action,
            "opportunities": len(mine),
            "by_file": dict(sorted(by_file.items())),
            "bounds": op.bounds,
        }
    return {
        "rtl_root": rtl_root.as_posix(),
        "files": [p.relative_to(rtl_root.parent).as_posix() for p in rtl_files(rtl_root)],
        "operators": per_op,
        "total_opportunities": len(sites),
    }


_MANIFEST = "mutant.json"


def inject(
    *,
    sites: list[Site],
    name: str,
    out_root: Path = DEFAULT_OUT_ROOT,
    rtl_root: Path = RTL_ROOT,
) -> Path:
    """把 ``sites`` 写进 ``<out_root>/<name>/rtl/`` 的**副本**里；返回该目录。

    原 ``rtl/`` 只读：本函数只读它、只写副本。

    位置用**绝对字符偏移**计算（不是行数组替换），因为 ``NSubMove`` 的 span 可以跨两行。
    """
    dst_root = out_root / name
    if dst_root.exists():
        shutil.rmtree(dst_root)
    (dst_root / "rtl").mkdir(parents=True)
    for sub in SRC_SUBDIRS:
        shutil.copytree(rtl_root / sub, dst_root / "rtl" / sub)

    by_file: dict[str, list[Site]] = {}
    for s in sites:
        by_file.setdefault(s.rel, []).append(s)

    applied: list[dict] = []
    for rel, group in sorted(by_file.items()):
        path = dst_root / rel
        text = _read_text(path)
        nl = _detect_nl(text)
        keep = text.splitlines(keepends=True)
        starts: list[int] = []
        acc = 0
        for line in keep:
            starts.append(acc)
            acc += len(line)

        edits: list[tuple[int, int, Site]] = []
        for s in group:
            if s.line < 1 or s.line > len(keep):
                raise SystemExit(f"注入点行号越界：{rel}:{s.line}")
            off = starts[s.line - 1] + s.col
            # 站点里的 span/repl 用 `\n` 表达（因为枚举时 `splitlines()` 去掉了行尾），
            # 这里按该文件**实际的行尾**还原 —— 跨行的 NSubMove 靠这一步才匹配得上。
            span = s.span.replace("\n", nl) if nl != "\n" else s.span
            got = text[off : off + len(span)]
            if got != span:
                raise SystemExit(
                    f"注入点失配（源码已变？）{rel}:{s.line}:{s.col} " f"期望 {span!r} 实得 {got!r}"
                )
            edits.append((off, off + len(span), s))
        for off, end, s in sorted(edits, key=lambda x: x[0], reverse=True):
            repl = s.repl.replace("\n", nl) if nl != "\n" else s.repl
            text = text[:off] + repl + text[end:]
            applied.append(
                {
                    "op": s.op,
                    "family": s.family,
                    "action": s.action,
                    "rel": s.rel,
                    "line": s.line,
                    "col": s.col,
                    "from": s.span,
                    "to": s.repl,
                    "note": s.note,
                }
            )
        _write_text(path, text)

    (dst_root / _MANIFEST).write_text(
        json.dumps({"name": name, "sites": applied}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return dst_root


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_list(rtl_root: Path) -> None:
    inv = inventory(rtl_root)
    print(f"rtl_root = {inv['rtl_root']}   ({len(inv['files'])} 个 .sv)")
    print(f"{'算子':<20}{'构族':<22}{'动作':<8}{'机会数':>7}")
    print("-" * 62)
    for name, info in inv["operators"].items():
        print(f"{name:<20}{info['family']:<22}{info['action']:<8}{info['opportunities']:>7}")
    print("-" * 62)
    print(f"{'合计':<20}{'':<22}{'':<8}{inv['total_opportunities']:>7}")
    print()
    for name, info in inv["operators"].items():
        if not info["by_file"]:
            print(f"[{name}] 机会 0 —— 见该算子的 BOUNDS 说明")
            continue
        print(f"[{name}] 机会 {info['opportunities']}")
        for rel, n in info["by_file"].items():
            print(f"    {n:>4}  {rel}")


def _pick(
    sites: list[Site],
    *,
    op_name: str,
    rel: str | None,
    line: int | None,
    term: int | None,
    index: int | None,
    sample: int | None,
    seed: int | None,
) -> list[Site]:
    mine = [s for s in sites if s.op == op_name]
    if not mine:
        raise SystemExit(f"算子 {op_name} 在本 rtl_root 下没有合法注入点")
    if rel is not None or line is not None:
        sel = [s for s in mine if s.rel == rel and s.line == line]
        if not sel:
            raise SystemExit(f"在 {rel}:{line} 找不到 {op_name} 的注入点")
        if term is not None:
            if not 0 <= term < len(sel):
                raise SystemExit(f"--term 越界：0..{len(sel) - 1}")
            return [sel[term]]
        if len(sel) > 1:
            print(
                f"注意：{rel}:{line} 上 {op_name} 有 {len(sel)} 个位点，"
                f"默认取**最后**一个（--term 0..{len(sel) - 1} 可指定）"
            )
        return [sel[-1]]
    if index is not None:
        if not 0 <= index < len(mine):
            raise SystemExit(f"--index 越界：0..{len(mine) - 1}")
        return [mine[index]]
    if sample is not None:
        rng = random.Random(seed)
        k = min(sample, len(mine))
        return rng.sample(mine, k)
    raise SystemExit("需要 --file/--line、--index 或 --sample 之一")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rtl-root", default=str(RTL_ROOT), help="被变异的 rtl 根（默认仓库 rtl/）")
    ap.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT), help="变异产物根目录")
    ap.add_argument("--list", action="store_true", help="打印注入点清单与机会总数")
    ap.add_argument("--json", action="store_true", help="打印机器可读清单")
    ap.add_argument("--op", help="算子名")
    ap.add_argument("--file", help="精确注入：相对仓库的文件路径，如 rtl/core/calib_regs.sv")
    ap.add_argument("--line", type=int, help="精确注入：1-based 行号")
    ap.add_argument("--index", type=int, help="精确注入：在该算子的机会列表里的下标")
    ap.add_argument(
        "--term",
        type=int,
        help="精确注入：同一行上有多个位点时的下标（如 ExprDelete 的顶层项序号）",
    )
    ap.add_argument("--sample", type=int, help="均匀抽样 b 个（每个位点产出独立变异体）")
    ap.add_argument("--seed", type=int, default=0, help="抽样随机种子")
    ap.add_argument("--name", help="精确注入时的变异体名（默认由算子+位点生成）")
    args = ap.parse_args(argv)

    rtl_root = Path(args.rtl_root)
    if args.json:
        print(json.dumps(inventory(rtl_root), indent=2, ensure_ascii=False))
        return 0
    if args.list or not args.op:
        _print_list(rtl_root)
        return 0

    sites = enumerate_sites(rtl_root)
    picked = _pick(
        sites,
        op_name=args.op,
        rel=args.file,
        line=args.line,
        term=args.term,
        index=args.index,
        sample=args.sample,
        seed=args.seed,
    )
    out_root = Path(args.out_root)
    made: list[Path] = []
    for n, site in enumerate(picked):
        if args.name and len(picked) == 1:
            name = args.name
        else:
            stem = Path(site.rel).stem
            name = f"{args.op.lower()}_{stem}_L{site.line}" + (f"_{n}" if len(picked) > 1 else "")
        made.append(inject(sites=[site], name=name, out_root=out_root, rtl_root=rtl_root))
        print(f"注入 {site.op} @ {site.rel}:{site.line}:{site.col}  {site.note}")
        print(f"  产物 -> {made[-1]}")
    print(f"共 {len(made)} 个变异体，位于 {out_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
