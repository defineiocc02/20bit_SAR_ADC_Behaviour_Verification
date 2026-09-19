"""``tools/mutate_rtl.py`` 的门禁：合法注入点必须**真的**合法，且保真性不能被悄悄破坏。

这里刻意不放"注入器能跑出 1000 个位点"这类规模断言 —— 那种断言对正确性毫无约束。
放的每一条都是**能在第一版注入器上真的变红**的性质（第一版确实产出过
``wr_en  (!cfg_ready) && …`` 这类非法语法，也把整个文件的行尾从 CRLF 改成了 LF）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mrt = _load("mutate_rtl_under_test", TOOLS / "mutate_rtl.py")

OP_NAMES = {op.name for op in mrt.OPERATORS}


def _hash_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*.sv")):
        out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture()
def sites():
    return mrt.enumerate_sites(mrt.RTL_ROOT)


def test_operator_set_and_declared_bounds():
    """13 个算子齐备，且每个都写了"本 SV 子集下的适用边界"。"""
    expect = {
        "ExprUpdate",
        "ExprDelete",
        "ExprInsert",
        "PartselectUpdate",
        "PartselectDelete",
        "PointerUpdate",
        "NSubUpdate",
        "NSubDelete",
        "NSubMove",
        "EdgeFlip",
        "EdgeInsert",
        "EdgeDelete",
        "IOFlip",
    }
    assert expect == OP_NAMES
    for op in mrt.OPERATORS:
        assert len(op.bounds) > 30, f"{op.name} 的边界声明太短，等于没写"


def test_edge_delete_is_declared_unreachable(sites):
    """`EdgeDelete` 在本子集机会为 0 —— 这是"不可达"，必须诚实地报 0 而不是伪造机会。"""
    inv = mrt.inventory(mrt.RTL_ROOT)
    assert inv["operators"]["EdgeDelete"]["opportunities"] == 0
    assert inv["operators"]["EdgeFlip"]["opportunities"] > 0
    assert inv["operators"]["EdgeInsert"]["opportunities"] > 0


def test_every_site_actually_matches_source(sites):
    """每个枚举出来的位点，其 span 必须在源文件里按 (line, col) 换算出的**绝对偏移**逐字命中。

    这条是"清单不是凭空编的"的最低凭据：序号漂了、列算错了，这里就红。
    ⚠️ 必须按绝对偏移比而不是"按行比"：`NSubMove` 的 span **跨两行**，按行比必假红
    （本测试第一版就这么错过）。
    """
    cache: dict[str, str] = {}
    assert sites
    for s in sites:
        if s.rel not in cache:
            cache[s.rel] = mrt._read_text(mrt.RTL_ROOT.parent / s.rel)
        text = cache[s.rel]
        keep = text.splitlines(keepends=True)
        off = sum(len(x) for x in keep[: s.line - 1]) + s.col
        span = s.span.replace("\n", mrt._detect_nl(text))
        assert text[off : off + len(span)] == span, f"{s.rel}:{s.line}:{s.col} {s.span!r}"


def test_expr_update_never_splits_a_wider_operator(sites):
    """`ExprUpdate` 的改名结果不能落在更长算子里。

    第一版把 `&&` 里的 `&` 当成位与算子，产出 `|&` 这种非法语法 —— 这条断言就是为它设的。
    """
    bad = []
    for s in sites:
        if s.op != "ExprUpdate":
            continue
        if s.span in ("&", "|", "<", ">", "+", "^"):
            # 复核：命中处左右相邻字符不能是同类算子字符（否则它就是更长算子的一部分）
            line = (
                (mrt.RTL_ROOT.parent / s.rel)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines()[s.line - 1]
            )
            prev = line[s.col - 1] if s.col > 0 else ""
            nxt = line[s.col + 1] if s.col + 1 < len(line) else ""
            if prev in "<>|=!&^~+-" or nxt in "<>|=!&^~+-":
                bad.append((s.rel, s.line, s.col, s.span, prev, nxt))
    assert bad == []


@pytest.mark.parametrize(
    ("rel", "line", "term", "op", "expect_line"),
    [
        (
            "rtl/core/weight_store.sv",
            101,
            4,
            "ExprDelete",
            "  assign accept    = wr_en && (!cfg_ready) && idx_ok && w_ok;",
        ),
        (
            "rtl/core/status_regs.sv",
            82,
            0,
            "ExprDelete",
            "        acc_ovf_sticky    <= ev_acc_ovf;",
        ),
    ],
)
def test_expr_delete_reproduces_documented_mutants(rel, line, term, op, expect_line, tmp_path):
    """删"容量条件项"与删"自保持项"必须逐字复现审计文档 §2 的 #10/#11 两条形态。

    第一版删错过两处：① 删掉 `&&` 却留着两个项 -> `wr_en  (!cfg_ready) && …` 非法；
    ② 删末项时把行尾 `;` 一起吃掉。这条断言把两处都钉住。
    """
    sites = [
        s
        for s in mrt.enumerate_sites(mrt.RTL_ROOT)
        if s.op == op and s.rel == rel and s.line == line
    ]
    assert len(sites) > term, f"{rel}:{line} 上 {op} 只有 {len(sites)} 个位点"
    mrt.inject(sites=[sites[term]], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    got = (tmp_path / "t" / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    assert got[line - 1] == expect_line


def test_injection_is_byte_faithful_outside_the_site(tmp_path):
    """副本除"命中那一行"外必须逐字节相同 —— **包括行尾**。

    第一版用 ``Path.read_text()``/``write_text()``，把整个文件的 CRLF 静默换成 LF，
    于是"只改了一处"就成了假话。本仓库 blob 是 LF，但 Windows worktree 检出是 CRLF，
    所以这条断言必须真的比较字节而不是比较"文本相等"。
    """
    before = _hash_tree(mrt.RTL_ROOT)
    site = next(
        s
        for s in mrt.enumerate_sites(mrt.RTL_ROOT)
        if s.op == "ExprDelete" and s.rel == "rtl/core/weight_store.sv" and s.line == 101
    )
    dst = mrt.inject(sites=[site], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)

    changed = []
    for rel, h in before.items():
        got = hashlib.sha256((dst / "rtl" / rel).read_bytes()).hexdigest()
        if got != h:
            changed.append(rel)
    assert changed == ["core/weight_store.sv"]

    src_lines = (mrt.RTL_ROOT / "core" / "weight_store.sv").read_bytes().splitlines(keepends=True)
    new_lines = (dst / "rtl/core/weight_store.sv").read_bytes().splitlines(keepends=True)
    assert len(src_lines) == len(new_lines)
    for i, (a, b) in enumerate(zip(src_lines, new_lines, strict=False)):
        if i == site.line - 1:
            assert a != b and a.endswith(b"\r\n") and b.endswith(b"\r\n")
        else:
            assert a == b, f"第 {i + 1} 行被顺手改了"


def test_injection_never_touches_the_original_rtl(tmp_path):
    """注入**不得**改动原 `rtl/`（这是"原 rtl 一个字节不动"的可执行凭据）。"""
    before = _hash_tree(mrt.RTL_ROOT)
    mrt.enumerate_sites(mrt.RTL_ROOT)
    sites = mrt.enumerate_sites(mrt.RTL_ROOT)[:5]
    mrt.inject(sites=sites, name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    assert _hash_tree(mrt.RTL_ROOT) == before


def test_nsub_delete_keeps_line_numbering(tmp_path):
    """`NSubDelete` 以注释形式落回原行 ⇒ 行数不变，行号可用作回溯坐标。"""
    sites = [s for s in mrt.enumerate_sites(mrt.RTL_ROOT) if s.op == "NSubDelete"]
    assert sites
    s = sites[0]
    dst = mrt.inject(sites=[s], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    src_n = len(
        (mrt.RTL_ROOT.parent / s.rel).read_text(encoding="utf-8", errors="replace").splitlines()
    )
    new = (dst / s.rel).read_text(encoding="utf-8", errors="replace").splitlines()
    assert len(new) == src_n
    assert new[s.line - 1].strip().startswith("// [MUT NSubDelete]")


def test_nsub_move_swaps_two_adjacent_lines(tmp_path):
    """`NSubMove` 交换相邻两条同缩进非阻塞赋值，且**不改变行数**。"""
    sites = [s for s in mrt.enumerate_sites(mrt.RTL_ROOT) if s.op == "NSubMove"]
    assert sites
    s = sites[0]
    src = (mrt.RTL_ROOT.parent / s.rel).read_text(encoding="utf-8", errors="replace").splitlines()
    assert "\n" in s.span, "move 的 span 必须跨两行"
    dst = mrt.inject(sites=[s], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    new = (dst / s.rel).read_text(encoding="utf-8", errors="replace").splitlines()
    assert len(new) == len(src)
    assert new[s.line - 1] == src[s.line] and new[s.line] == src[s.line - 1]


def test_uniform_sampling_is_seeded_and_deduplicated():
    """同一 seed 抽同样的位点；抽样不重复（一次注入 = 一个位点）。"""
    sites = mrt.enumerate_sites(mrt.RTL_ROOT)
    a = mrt._pick(
        sites, op_name="ExprUpdate", rel=None, line=None, term=None, index=None, sample=8, seed=42
    )
    b = mrt._pick(
        sites, op_name="ExprUpdate", rel=None, line=None, term=None, index=None, sample=8, seed=42
    )
    c = mrt._pick(
        sites, op_name="ExprUpdate", rel=None, line=None, term=None, index=None, sample=8, seed=43
    )
    assert [x.key for x in a] == [x.key for x in b]
    assert len({x.key for x in a}) == 8
    assert [x.key for x in a] != [x.key for x in c]


def test_manifest_records_every_site(tmp_path):
    """`mutant.json` 必须逐条记下 from/to/op/位置，否则 campaign 无法回链证据。"""
    import json

    s = next(x for x in mrt.enumerate_sites(mrt.RTL_ROOT) if x.op == "EdgeFlip")
    dst = mrt.inject(sites=[s], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    man = json.loads((dst / "mutant.json").read_text(encoding="utf-8"))
    assert man["sites"][0]["op"] == "EdgeFlip"
    assert man["sites"][0]["rel"] == s.rel
    assert man["sites"][0]["line"] == s.line
    assert man["sites"][0]["from"] == s.span
    assert man["sites"][0]["to"] == s.repl


def test_rtl_files_are_the_expected_16():
    """被变异的文件集固定为 ``rtl/core`` + ``rtl/top`` 的 16 个 .sv（不含 params/*.vh）。"""
    rels = [p.relative_to(mrt.RTL_ROOT.parent).as_posix() for p in mrt.rtl_files(mrt.RTL_ROOT)]
    assert len(rels) == 16
    assert all(r.endswith(".sv") for r in rels)
    assert not any("params" in r for r in rels)


def test_shutil_rmtree_guard_is_used(tmp_path):
    """同名变异体目录会被干净重建（不会把上一次的残留混进来）。"""
    s = next(x for x in mrt.enumerate_sites(mrt.RTL_ROOT) if x.op == "EdgeFlip")
    d1 = mrt.inject(sites=[s], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    (d1 / "STALE").write_text("x", encoding="utf-8")
    d2 = mrt.inject(sites=[s], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    assert d1 == d2
    assert not (d2 / "STALE").exists()
    shutil.rmtree(tmp_path / "t", ignore_errors=True)
