#!/usr/bin/env python3
"""在本项目 SystemVerilog 子集中枚举和注入变异。

按 docs/rtl/MUTATION_AUDIT_VS_VERIBUGBENCH.md §6.1，将匹配、守卫与变换组合为
各个算子。注入只修改 <out-root>/<name>/<source-root-name>/ 中的 SV 副本，输入保持只读。
输出为显式库外新目录；已有结果拒绝覆盖。清单和抽样选择不表示产物已经编译或被检出。

历史审计将分类对应到 VeriBugBench（arXiv:2609.18022）；本工具只枚举并生成文本
变异体，不执行编译保留或测试台检出判定，也不复现该论文的基准构造和性能测度。

论文前端使用 Pyverilog 的 Verilog 子集；本项目还使用 logic、always_ff、'0、
$signed 和打包数组。因此本工具采用行内文本匹配及显式守卫，只是本 SV 子集
中的近似实现，不能与论文算子逐条等同。各算子的 BOUNDS 声明适用范围和未覆盖
形式；可比较的范围限于分类与保留判据，详见原论文 §5.6 和审计文档。

--list 或 --json 枚举位点及逐文件机会数；注入通过 --op 配合 --file/--line、
--index 或 --sample 选择。--sample 对合法位点均匀抽样，--seed 控制复现；
每个位点写一个独立变异体。论文的 b=20 与本工具的抽样参数不是测试覆盖保证。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RTL_ROOT = REPO_ROOT / "rtl"

# ---------------------------------------------------------------------------
# 站点与算子
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """记录一个合法注入点及替换内容。

    Attributes:
        rel: 源文件相对路径。
        line: 从 1 开始的源行号。
        col: 从 0 开始的字符列号；替换区间为 [col, col + len(span))。
        span: 必须与源内容精确匹配的文本，可跨行。
        repl: 用于替换 span 的文本。
    """

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
        """返回由文件、行、列和算子组成的稳定键，用于去重与复现。"""
        return f"{self.rel}:{self.line}:{self.col}:{self.op}"


@dataclass(frozen=True)
class Operator:
    """组合算子名称、分类、动作、适用边界及位点枚举函数。"""

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
    """移除行尾 // 注释；此简化切分只适用于不含相关字符串字面量的输入子集。"""
    i = line.find("//")
    return line if i < 0 else line[:i]


def _is_literal(tok: str) -> bool:
    return bool(_LITERAL_RE.match(tok.strip()))


def _is_ident(tok: str) -> bool:
    return bool(_IDENT_RE.match(tok.strip()))


def _lhs_tokens_before(line: str, idx: int, width: int = 48) -> str:
    return line[max(0, idx - width) : idx]


def _is_stmt_nba(line: str) -> bool:
    """识别单行的简单非阻塞赋值，区分赋值符号与关系运算符 <=。

    支持简单左值和可选位选，要求语句以分号结束。多行赋值及更复杂语法的适用性
    由各算子的 BOUNDS 限定，不能由此谓词推断支持。
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
    """将语句级非阻塞赋值拆为缩进、左值和右值；不匹配时返回 None。"""
    if not _is_stmt_nba(line):
        return None
    code = _code(line)
    indent = code[: len(code) - len(code.lstrip())]
    body = code.strip().rstrip(";")
    lhs, rhs = body.split("<=", 1)
    return indent, lhs.strip(), rhs.strip()


def _top_level_terms(expr: str, joiner: str) -> list[str]:
    """按顶层 joiner 切分表达式，保留括号内部的连接词。"""
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
    """返回顶层连接词的 [start, end) 字符区间，索引相对于 expr。

    匹配单字符 &、| 或 ^ 时排除较长连接词的组成部分，避免把 && 计作两个 &。
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
    """返回顶层语句级 = 或 <= 后的位置；未找到赋值时返回 0。

    只在赋值右侧枚举操作数，避免将 assign w_ok = wr_en 等语句前缀误当作
    可删除的首个表达式项。
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
    """保留原始换行形式读取 UTF-8 文本，不将 CRLF 归一为 LF。

    注入副本要求除目标区间外逐字节保真；普通文本读取的换行归一化会破坏该约束。
    此处通过 newline="" 保留源文件换行，兼容 LF 与 CRLF 检出。
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
    """返回 code[i] 所在的最长连续算子词法单元，例如 &&、<= 或 >>>。

    该守卫避免将短算子匹配到长算子内部，否则可能生成 |& 等非法文本。
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
    """替换同族二元运算符，包括关系运算符；跳过非阻塞赋值本身的 <=。"""
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
        # 跳过语句级非阻塞赋值的 <=，其余关系运算符仍可枚举。
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
    """删除赋值右侧的一个顶层项，支持 &&、||、&、| 和 ^ 连接词。

    首项删除 term0 及其后连接词，其他项删除其前连接词及 term_k。
    要求至少一个连接词、两个非空项；拒绝归约运算的空首项，并保留末尾分号。
    该形式覆盖审计文档 §2 的 ws_capoff 与 sr_nonsticky 类单行表达式。
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
                    span_s -= 1  # 连同目标项前的空白删除，避免留下 w_ok ;。
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
    """在支持的非阻塞赋值右值前插入一元 ~，仅匹配本文件声明的位向量标识符。"""
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
            continue  # 仅处理同文件声明的位向量标识符，排除类型名和函数名。
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
    """交换位段 [A:B] 的两个边界；端口位宽由 IOFlip 单独处理。"""
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
    """将支持的位段 [A:B] 缩减为其 A 端单比特访问。"""
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
    """对非纯字面量的单索引 [X] 作受限 ±1 扰动，复杂索引见 BOUNDS。"""
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
                    continue  # 此有限匹配器不处理复杂索引，适用边界见 BOUNDS。
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
    """将支持的 lhs <= rhs 右值改为按位取反 ~(rhs)。"""
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
    """将整条非阻塞赋值注释在原行，保持行数供注入位置追溯。"""
    nb = [ln for ln, raw in enumerate(lines, 1) if _is_stmt_nba(raw)]
    if len(nb) < 2:
        return []  # 文件内匹配赋值少于两条时不枚举删除；检查范围见 BOUNDS。
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
    """交换相邻两行且缩进相同的非阻塞赋值，保持行数。"""
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
    """交换单沿 always_ff 事件控制中的 posedge 与 negedge。"""
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
    """在支持的单沿事件控制中插入异步复位沿，要求源文本包含 rst_n。"""
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
    """仅从多沿事件控制中删除一个沿。

    单沿 always_ff 删除唯一事件后会成为非法 SV，因此不生成此类位点。
    原说明的冻结源集曾登记 16/16 为单沿、机会数为 0；该历史计数不替代当前枚举。
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
    """交换端口位宽边界，或翻转 input/output 方向。

    方向翻转可能产生多驱动或悬空，此类产物须由编译保留判据排除；枚举机会数
    不表示该变异已经成为可保留的有效测试案例。
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
        "（避免明显改变赋值目标位宽）。生成后仍须另行编译检查位宽和语法。",
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
        "只交换**相邻两行且缩进相同**的非阻塞赋值语句。"
        "不跨 `if/else` 分支搬移、不跨 `begin/end`、不做远距离重排。",
        _nsub_move,
    ),
    Operator(
        "EdgeFlip",
        "Timing",
        "update",
        "只翻 `always_ff @(posedge X)` 的沿（`posedge` <-> `negedge`）。仅 `always_ff`；"
        "不处理 `always @(...)` 与 `always_comb`（无事件控制）。",
        _edge_flip,
    ),
    Operator(
        "EdgeInsert",
        "Timing",
        "insert",
        "给**单沿** `always_ff` 事件控制插入 ` or negedge rst_n`，且仅当该文件确实有 `rst_n`。"
        "该插入改变事件触发条件；原输入是否采用同步复位须由调用者核对。",
        _edge_insert,
    ),
    Operator(
        "EdgeDelete",
        "Timing",
        "delete",
        "只删**多沿**事件控制里的一个沿。机会数按当前显式源集枚举，不继承历史零计数；"
        "没有多沿形式时不生成位点；删掉唯一沿会产生非法 always_ff，因此排除该形式。",
        _edge_delete,
    ),
    Operator(
        "IOFlip",
        "Port",
        "update",
        "两种模式：① 端口**位宽**边界对调 `[A:B]` -> `[B:A]`；② 端口**方向** `input` <-> `output`"
        "（`clk`/`rst_n` 跳过）。方向翻转可能造成多驱动或悬空，须另行编译检查；"
        "报数时『机会数』与『可保留数』必须分开说，不得混为一谈。",
        _io_flip,
    ),
)


# ---------------------------------------------------------------------------
# 枚举 / 注入
# ---------------------------------------------------------------------------


def _source_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.is_dir() or not root.name:
        raise ValueError("an explicit existing source root is required")
    return root


def rtl_files(rtl_root: Path) -> list[Path]:
    """Enumerate the explicitly selected SV subtree, rejecting external symlinks."""
    root = _source_root(rtl_root)
    files = sorted(p for p in root.rglob("*.sv") if p.is_file())
    if not files:
        raise ValueError("selected source root contains no .sv files")
    if any(
        not p.resolve().is_relative_to(root) or p.stat().st_size >= 300 * 1024**2 for p in files
    ):
        raise ValueError("SV input escapes the selected root or exceeds 300 MiB")
    return files


def enumerate_sites(rtl_root: Path) -> list[Site]:
    """Enumerate sites without compiling or declaring any mutant retained."""
    rtl_root = _source_root(rtl_root)
    sites = []
    for path in rtl_files(rtl_root):
        rel = path.relative_to(rtl_root.parent).as_posix()
        lines = _read_text(path).splitlines()
        for operator in OPERATORS:
            sites.extend(operator.find(rel, lines))
    return sites


def inventory(rtl_root: Path) -> dict:
    """Report actual opportunities in the declared source subtree only."""
    rtl_root = _source_root(rtl_root)
    sites = enumerate_sites(rtl_root)
    per_op = {}
    for operator in OPERATORS:
        selected = [site for site in sites if site.op == operator.name]
        by_file = {}
        for site in selected:
            by_file[site.rel] = by_file.get(site.rel, 0) + 1
        per_op[operator.name] = {
            "family": operator.family,
            "action": operator.action,
            "opportunities": len(selected),
            "by_file": dict(sorted(by_file.items())),
            "bounds": operator.bounds,
        }
    return {
        "status": "SOURCE_SITE_INVENTORY_ONLY",
        "compiled": False,
        "rtl_root": rtl_root.as_posix(),
        "files": [p.relative_to(rtl_root.parent).as_posix() for p in rtl_files(rtl_root)],
        "operators": per_op,
        "total_opportunities": len(sites),
    }


_MANIFEST = "mutant.json"


def _external_container(path: Path, source: Path) -> Path:
    if path.expanduser().is_symlink():
        raise ValueError("mutation output container cannot be a symbolic-link alias")
    out = path.expanduser().resolve()
    if out == source or out.is_relative_to(source):
        raise ValueError("mutation output must be outside its source subtree")
    if any((p / ".git").exists() for p in (out, *out.parents)):
        raise ValueError("mutation output must be outside Git working trees")
    return out


def _headers(paths: list[Path] | tuple[Path, ...]) -> list[Path]:
    result = [path.expanduser().resolve() for path in paths]
    if len(result) != len(set(result)):
        raise ValueError("duplicate declared header")
    if any(
        not p.is_file() or p.suffix not in {".vh", ".svh"} or p.stat().st_size >= 300 * 1024**2
        for p in result
    ):
        raise ValueError("declare existing .vh/.svh headers smaller than 300 MiB")
    return result


def inject(
    *,
    sites: list[Site],
    name: str,
    out_root: Path,
    rtl_root: Path,
    headers: list[Path] | tuple[Path, ...],
) -> Path:
    """Write one fresh mutant from immutable input bytes; never delete old results.

    Every site and header is explicit. The output records generated source only;
    compilation, semantic retention, simulation and trace completeness are not run.
    """
    if not sites or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("nonempty sites and a simple fresh mutant name required")
    rtl_root = _source_root(rtl_root)
    container = _external_container(out_root, rtl_root)
    destination = container / name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing existing mutant output: {destination}")
    source_files = rtl_files(rtl_root)
    header_files = _headers(headers)
    data = {p.relative_to(rtl_root.parent).as_posix(): p.read_bytes() for p in source_files}
    original_sha = {rel: hashlib.sha256(value).hexdigest() for rel, value in data.items()}
    known_operators = {op.name for op in OPERATORS}
    groups = {}
    for site in sites:
        if site.op not in known_operators or site.rel not in data:
            raise ValueError("site must name a declared operator and selected SV input")
        if type(site.line) is not int or site.line < 1 or type(site.col) is not int or site.col < 0:
            raise ValueError("site line/column must be explicit nonnegative integer coordinates")
        if (
            not isinstance(site.span, str)
            or not isinstance(site.repl, str)
            or site.span == site.repl
        ):
            raise ValueError("site must change a declared text interval")
        groups.setdefault(site.rel, []).append(site)
    mutated = {}
    applied = []
    for rel, group in sorted(groups.items()):
        text = data[rel].decode("utf-8")
        newline = _detect_nl(text)
        keep = text.splitlines(keepends=True)
        starts, offset = [], 0
        for line in keep:
            starts.append(offset)
            offset += len(line)
        edits = []
        for site in group:
            if site.line > len(keep) or site.col > len(keep[site.line - 1].rstrip("\r\n")):
                raise ValueError(f"site coordinate outside source: {rel}:{site.line}:{site.col}")
            start = starts[site.line - 1] + site.col
            span = site.span.replace("\n", newline) if newline != "\n" else site.span
            if text[start : start + len(span)] != span:
                raise ValueError(
                    f"site text changed before injection: {rel}:{site.line}:{site.col}"
                )
            edits.append((start, start + len(span), site))
        ordered = sorted(edits, key=lambda item: (item[0], item[1]))
        for previous, current in pairwise(ordered):
            if current[0] < previous[1] or current[0] == previous[0]:
                raise ValueError("overlapping mutation intervals are unsupported")
        for start, end, site in reversed(ordered):
            replacement = site.repl.replace("\n", newline) if newline != "\n" else site.repl
            text = text[:start] + replacement + text[end:]
            applied.append(
                {
                    "op": site.op,
                    "family": site.family,
                    "action": site.action,
                    "rel": site.rel,
                    "line": site.line,
                    "col": site.col,
                    "from": site.span,
                    "to": site.repl,
                    "note": site.note,
                }
            )
        mutated[rel] = text.encode("utf-8")
    support = {}
    for header in header_files:
        rel = (
            (Path(rtl_root.name) / header.relative_to(rtl_root)).as_posix()
            if header.is_relative_to(rtl_root)
            else (Path("include") / header.name).as_posix()
        )
        if rel in support:
            raise ValueError("declared headers would shadow the same output path")
        support[rel] = (header, header.read_bytes())
    targets = [*data, *support]
    if len({path.casefold() for path in targets}) != len(targets):
        raise ValueError("declared inputs shadow a case-insensitive output path")
    destination.mkdir(parents=True, exist_ok=False)
    for rel, raw in data.items():
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(mutated.get(rel, raw))
    for rel, (_header, raw) in support.items():
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    if any(
        hashlib.sha256(p.read_bytes()).hexdigest()
        != original_sha[p.relative_to(rtl_root.parent).as_posix()]
        for p in source_files
    ):
        raise RuntimeError("input source changed during mutant generation")
    if any(header.read_bytes() != raw for header, raw in support.values()):
        raise RuntimeError("declared header changed during mutant generation")
    record = {
        "schema_version": 1,
        "status": "GENERATED_NOT_COMPILED",
        "scope": "DECLARED_SOURCE_MUTATION_NOT_RETENTION_OR_RTL_VERIFICATION",
        "name": name,
        "source_root": str(rtl_root),
        "source_sha256": original_sha,
        "header_sha256": {
            str(header): hashlib.sha256(raw).hexdigest() for header, raw in support.values()
        },
        "artifact_sha256": {
            rel: hashlib.sha256((destination / rel).read_bytes()).hexdigest()
            for rel in [*data, *support]
        },
        "include_dirs": sorted({str((destination / rel).parent) for rel in support}),
        "sites": applied,
        "compiled": False,
        "simulation_executed": False,
    }
    (destination / _MANIFEST).write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return destination


def _print_list(rtl_root: Path) -> None:
    report = inventory(rtl_root)
    print(
        f"SOURCE_SITE_INVENTORY files={len(report['files'])} opportunities={report['total_opportunities']} compiled=NOT_RUN"
    )
    for name, info in report["operators"].items():
        print(f"{name}: opportunities={info['opportunities']} bounds={info['bounds']}")


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
    selected = [site for site in sites if site.op == op_name]
    if not selected:
        raise ValueError(f"operator {op_name} has no legal sites in this source set")
    if (rel is None) != (line is None):
        raise ValueError("--file and --line must be supplied together")
    modes = int(rel is not None) + int(index is not None) + int(sample is not None)
    if modes != 1:
        raise ValueError("select exactly one of --file/--line, --index, or --sample")
    if rel is not None:
        choices = [site for site in selected if site.rel == rel and site.line == line]
        if not choices:
            raise ValueError("no site matches the exact file/line")
        if term is None and len(choices) != 1:
            raise ValueError("multiple sites share this line; explicit --term required")
        if term is not None and (type(term) is not int or not 0 <= term < len(choices)):
            raise ValueError("--term is outside the exact line's site set")
        return [choices[0 if term is None else term]]
    if term is not None:
        raise ValueError("--term requires --file/--line")
    if index is not None:
        if type(index) is not int or not 0 <= index < len(selected):
            raise ValueError("--index outside actual operator sites")
        return [selected[index]]
    if type(sample) is not int or not 1 <= sample <= len(selected):
        raise ValueError("--sample must select a nonzero available number of sites")
    if type(seed) is not int or seed < 0:
        raise ValueError("sampling requires an explicit nonnegative --seed")
    return random.Random(seed).sample(selected, sample)


def main(argv: list[str] | None = None) -> int:
    """Generate fresh out-of-tree source artifacts, or list opportunities read-only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--json", action="store_true")
    mode.add_argument("--op", choices=tuple(op.name for op in OPERATORS))
    parser.add_argument("--headers", type=Path, nargs="*")
    parser.add_argument("--out-root", type=Path)
    parser.add_argument("--file")
    parser.add_argument("--line", type=int)
    parser.add_argument("--index", type=int)
    parser.add_argument("--term", type=int)
    parser.add_argument("--sample", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--name")
    args = parser.parse_args(argv)
    try:
        root = _source_root(args.rtl_root)
        if args.list or args.json:
            if any(
                value is not None
                for value in (
                    args.out_root,
                    args.headers,
                    args.file,
                    args.line,
                    args.index,
                    args.term,
                    args.sample,
                    args.seed,
                    args.name,
                )
            ):
                raise ValueError("inventory mode does not accept mutation selectors or outputs")
            if args.json:
                print(json.dumps(inventory(root), indent=2, ensure_ascii=False))
            else:
                _print_list(root)
            return 0
        if args.out_root is None or args.headers is None:
            raise ValueError(
                "mutation requires explicit --out-root and --headers (an empty list is valid)"
            )
        picked = _pick(
            enumerate_sites(root),
            op_name=args.op,
            rel=args.file,
            line=args.line,
            term=args.term,
            index=args.index,
            sample=args.sample,
            seed=args.seed,
        )
        if args.name and len(picked) != 1:
            raise ValueError("--name is only valid for one selected site")
        if args.name and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.name):
            raise ValueError("--name must be a simple mutant name")
        if args.seed is not None and args.sample is None:
            raise ValueError("--seed is only consumed by --sample")
        out = _external_container(args.out_root, root)
        if out.exists() or out.is_symlink():
            raise FileExistsError("mutation batch output already exists")
        _headers(args.headers)
        out.mkdir(parents=True, exist_ok=False)
        made = []
        for number, site in enumerate(picked):
            stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(site.rel).stem)
            name = args.name or f"{args.op.lower()}_{stem}_L{site.line}_{number}"
            made.append(
                inject(sites=[site], name=name, out_root=out, rtl_root=root, headers=args.headers)
            )
        if len(made) != len(picked) or not made:
            raise RuntimeError("mutation generation did not complete all declared sites")
        (out / "generation.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "GENERATION_COMPLETE_NOT_COMPILED",
                    "mutants": [str(p) for p in made],
                    "compiled": False,
                    "simulation_executed": False,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(
            f"MUTATION_GENERATION_COMPLETE mutants={len(made)} compiled=NOT_RUN simulation=NOT_RUN"
        )
        return 0
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        print(f"mutate_rtl: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
