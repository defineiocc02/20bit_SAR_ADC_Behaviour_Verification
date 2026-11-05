#!/usr/bin/env python3
"""在本项目 SystemVerilog 子集中枚举和注入变异。

按 docs/rtl/MUTATION_AUDIT_VS_VERIBUGBENCH.md §6.1，将匹配、守卫与变换组合为
各个算子。注入只修改 <out-root>/<name>/rtl/ 中的生产 SV 副本，原 rtl/ 保持只读。
同名变异体目录按现有注入逻辑重建；清单和抽样选择不表示产物已经编译或被检出。

本实现转用 VeriBugBench（arXiv:2609.18022）的算子分类和保留判据，衡量本项目
测试台对所注入故障的检出情况。原论文的测度针对基准构造及产物质量，不是调试
技术性能；这里的结果不能解释为达到该基准的某个性能水平。

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
DEFAULT_OUT_ROOT = REPO_ROOT / "sim" / "artifacts" / "mut"

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
    """递归枚举生产 SV 文件，不按责任子目录排除模块。"""
    return sorted(rtl_root.rglob("*.sv"))


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
    """将 sites 注入 <out_root>/<name>/rtl/ 副本并返回变异体根目录。

    原 rtl/ 仅供读取。同名目标目录会按当前逻辑重建；各站点在写入前精确核对
    原文本。替换以绝对字符偏移计算，以支持 NSubMove 跨两行的区间。
    """
    dst_root = out_root / name
    if dst_root.exists():
        shutil.rmtree(dst_root)
    (dst_root / "rtl").mkdir(parents=True)
    for source in rtl_files(rtl_root):
        destination = dst_root / "rtl" / source.relative_to(rtl_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

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
            # 枚举时 splitlines() 移除了原行尾，站点 span/repl 统一以 \n 表示换行；
            # 应用前还原为文件实际换行，使跨行 NSubMove 能精确匹配。
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
