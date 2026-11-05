"""检查变异位点的合法性、替换保真性和来源追溯。

断言针对词法边界、分号、跨行区间、原源码只读及换行保留等性质。
不以位点数量规模代替正确性；历史注入器曾产生非法表达式，并将 CRLF 归一为
LF，这些回归用于约束对应边界，而不是声称每个枚举产物已编译。
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
    """检查每个位点的 span 在其绝对字符偏移处逐字匹配源码。

    NSubMove 的区间跨两行，因此先按行、列求绝对偏移，不能只比较单行。
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
    """检查 ExprUpdate 不把 && 中的 & 等短算子误识别为独立目标，避免生成 |&。"""
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


def _code_line(rel, needle):
    matches = [
        i
        for i, line in enumerate(
            (mrt.RTL_ROOT.parent / rel).read_text(encoding="utf-8").splitlines(), 1
        )
        if needle in line and not line.lstrip().startswith("//")
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("rel", "source", "source_line", "term", "expect_line"),
    [
        pytest.param(
            "rtl/core/weight_store.sv",
            "module documented_capacity_guard(\n"
            "    input wire wr_en, clear_load, cfg_ready, idx_ok, w_ok,\n"
            "    input wire [63:0] sum_new, SUM_MAX,\n"
            "    output wire accept\n"
            ");\n"
            "  assign accept    = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok && (sum_new < SUM_MAX);\n"
            "endmodule\n",
            "  assign accept    = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok && (sum_new < SUM_MAX);",
            5,
            "  assign accept    = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok;",
            id="frozen_documented_capacity_guard",
        ),
        pytest.param(
            "rtl/core/status_regs.sv",
            "module documented_sticky_guard(\n"
            "    input wire clk, ev_acc_ovf,\n"
            "    output logic acc_ovf_sticky\n"
            ");\n"
            "  always_ff @(posedge clk) begin\n"
            "        acc_ovf_sticky    <= acc_ovf_sticky    | ev_acc_ovf;\n"
            "  end\n"
            "endmodule\n",
            "        acc_ovf_sticky    <= acc_ovf_sticky    | ev_acc_ovf;",
            0,
            "        acc_ovf_sticky    <= ev_acc_ovf;",
            id="frozen_documented_sticky_self_hold",
        ),
    ],
)
def test_expr_delete_reproduces_documented_mutants(
    rel, source, source_line, term, expect_line, tmp_path
):
    """用冻结单行赋值夹具检查审计文档 §2 #10/#11 的语法形式。

    当前 accept 为跨行表达式，有限行内匹配器不覆盖；文档容量条件也未包含后加的
    STATIC_SUM_SAFE。因此这里仅用历史完整单行形态，约束连接词删除与末尾分号保留，
    不将当前排版行号视为注入合同。
    """
    rtl_root = tmp_path / "documented_fixture" / "rtl"
    for sub in ("core", "top"):
        (rtl_root / sub).mkdir(parents=True)
    original = rtl_root.parent / rel
    original.write_bytes(source.encode("utf-8"))
    assert source.splitlines().count(source_line) == 1
    line = source.splitlines().index(source_line) + 1
    sites = [
        site
        for site in mrt.enumerate_sites(rtl_root)
        if site.op == "ExprDelete" and site.rel == rel and site.line == line
    ]
    assert len(sites) == (6 if term == 5 else 2)
    destination = mrt.inject(sites=[sites[term]], name="t", out_root=tmp_path, rtl_root=rtl_root)
    got = (destination / rel).read_bytes()
    assert got == source.replace(source_line, expect_line).encode("utf-8")
    got_line = got.splitlines()[line - 1].decode("utf-8")
    assert got_line == expect_line
    assert got_line.endswith(";")
    assert "&& ;" not in got_line and "wr_en  (!clear_load)" not in got_line
    assert original.read_bytes() == source.encode("utf-8")


def test_expr_delete_removes_current_sticky_self_hold(tmp_path):
    """当前生产状态寄存器仍支持单行自保持删除，按符号定位而不绑定旧行号。"""
    rel = "rtl/core/status_regs.sv"
    line = _code_line(rel, "<= acc_ovf_sticky")
    sites = [
        site
        for site in mrt.enumerate_sites(mrt.RTL_ROOT)
        if site.op == "ExprDelete" and site.rel == rel and site.line == line
    ]
    assert len(sites) == 2
    before = _hash_tree(mrt.RTL_ROOT)
    destination = mrt.inject(sites=[sites[0]], name="t", out_root=tmp_path, rtl_root=mrt.RTL_ROOT)
    got = (destination / rel).read_text(encoding="utf-8").splitlines()
    assert got[line - 1] == "        acc_ovf_sticky    <= ev_acc_ovf;"
    assert _hash_tree(mrt.RTL_ROOT) == before


def test_injection_is_byte_faithful_outside_the_site(tmp_path):
    """检查目标行以外逐字节不变，目标行的换行形式也保持原样。

    检出策略可产生 LF，旧工作树也可能保留 CRLF；本断言不选择哪一种正确，只要求
    注入不改变来源换行。仅比较归一化后的文本不能证明该约束。
    """
    before = _hash_tree(mrt.RTL_ROOT)
    site = next(
        s
        for s in mrt.enumerate_sites(mrt.RTL_ROOT)
        if s.op == "ExprDelete"
        and s.rel == "rtl/core/weight_store.sv"
        and s.line == _code_line("rtl/core/weight_store.sv", "assign w_ok")
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
            assert a != b, "命中行必须真的被改"
            a_eol = a[len(a.rstrip(b"\r\n")) :]
            b_eol = b[len(b.rstrip(b"\r\n")) :]
            assert a_eol == b_eol, f"命中行的行尾被顺手改了：{a_eol!r} -> {b_eol!r}"
            assert a_eol in (b"\n", b"\r\n"), f"意外的行尾：{a_eol!r}"
        else:
            assert a == b, f"第 {i + 1} 行被顺手改了"
    assert _hash_tree(mrt.RTL_ROOT) == before


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


def test_rtl_files_match_the_compilation_manifest():
    """检查生产模块同时列入编译清单和变异范围。"""
    rels = [p.relative_to(mrt.RTL_ROOT.parent).as_posix() for p in mrt.rtl_files(mrt.RTL_ROOT)]
    manifest = (mrt.RTL_ROOT / "rtl_sources.f").read_text().splitlines()
    assert sorted(rels) == sorted(manifest)
    assert len(manifest) == len(set(manifest))
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
