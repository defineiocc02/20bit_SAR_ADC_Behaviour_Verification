"""注入器两端验证（Phase 1a 的验收门）：**必被检出**端与**必然等价**端。

审计文档 §6.5 把这一步定成"不做完不许跑 campaign"：给不出这两端，
campaign 的变异分数就不可信（一个"恒 killed"的判据会虚高，一个"恒 unobserved"的会虚低）。

本测试**默认不跑**（需要远程 EDA VM + VCS，分钟级）。两种开启方式：

    RTL_SIM_TWONECK=1 PYTHONPATH=src python -m pytest tests/integration/test_mutation_twoneck.py -q

它做的事：
  1. 跑干净设计拿**金标** trace；
  2. 端 1：注入 `ExprUpdate` @ `div_floor.sv` 的 `quo << P_STAGES`（`<<` -> `>>`）⇒ **必须 killed**；
  3. 端 2：注入 `ExprDelete` @ `weight_store.sv` 的容量条件项（§2 的 #10 负对照形态）
     ⇒ **必须 unobserved**。

两端的**先验理由**写在下面各自的测试里 —— 不是"跑出来看结果再挑一个"。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "sim" / "artifacts" / "twoneck"
PY = sys.executable

pytestmark = [pytest.mark.slow, pytest.mark.integration]

requires = pytest.mark.skipif(
    os.environ.get("RTL_SIM_TWONECK") != "1",
    reason="两端验证需要远程 EDA VM + VCS；设 RTL_SIM_TWONECK=1 开启",
)

SIM_SRC = ["rtl/core", "rtl/top", "rtl/params", "sim/vectors"]


def _run(
    cmd: list[str], what: str, allow_rc: tuple[int, ...] = (0, 1)
) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, errors="replace")
    if cp.returncode not in allow_rc:
        raise AssertionError(
            f"{what} 失败 rc={cp.returncode}\n{cp.stdout[-4000:]}\n{cp.stderr[-2000:]}"
        )
    return cp


def _inject(op: str, rel: str, line: int, name: str, term: int | None = None) -> Path:
    cmd = [
        PY,
        "tools/mutate_rtl.py",
        "--op",
        op,
        "--file",
        rel,
        "--line",
        str(line),
        "--name",
        name,
        "--out-root",
        str(ART / "mut"),
    ]
    if term is not None:
        cmd += ["--term", str(term)]
    _run(cmd, f"注入 {op}@{rel}:{line}", allow_rc=(0,))
    return ART / "mut" / name


def _sim(name: str, rtl_prefix: Path | None) -> Path:
    """跑一次仿真并把 trace 取回；`rtl_prefix=None` 表示干净设计。

    TB may report mismatches only after completing every expected observation.
    Compilation, timeout, incomplete execution and stale traces are never verdicts.
    """
    trace = REPO / "sim" / "artifacts" / name / "p3_trace.txt"
    trace.unlink(missing_ok=True)
    src = (
        SIM_SRC
        if rtl_prefix is None
        else [
            str(rtl_prefix / "core"),
            str(rtl_prefix / "top"),
            "rtl/params",
            "sim/vectors",
        ]
    )
    cmd = [
        PY,
        "tools/run_rtl_sim.py",
        "--top",
        "p3_top_tb",
        "--name",
        name,
        "--tb",
        "sim/tb/p3_top_tb.sv",
        "--src",
        *src,
        "--incdir",
        "rtl/params",
        "--fresh",
        "--sim-arg",
        "+vdir=sim/vectors",
        "+injdith",
        "+trace=p3_trace.txt",
        "--fetch",
        "p3_trace.txt",
    ]
    cp = _run(cmd, f"仿真 {name}", allow_rc=(0, 3, 4))
    assert trace.is_file(), f"{name} 没有取回 trace（编译失败或 TB 没跑到落盘）"
    _complete_trace(trace, cp.returncode)
    return trace


def _expected_rows() -> int:
    data = (REPO / "sim/vectors/p2_link_stim.hex").read_text(encoding="utf-8")
    rows = data.split("#DATA", 1)[1].splitlines()
    return sum(bool(line.strip()) and not line.lstrip().startswith("//") for line in rows) - 1


def _complete_trace(path: Path, returncode: int = 0) -> None:
    assert returncode in (0, 3, 4), f"run failed before completion: rc={returncode}"
    expected = _expected_rows()
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    marker = f"# P3_TRACE_COMPLETE rows={expected}"
    assert lines and lines[-1] == marker, "missing final completion marker"
    assert lines.count(marker) == 1, "duplicate completion marker"
    rows = [line.split() for line in lines if not line.startswith("#")]
    assert len(rows) == expected and expected > 0, "empty/incomplete/extra trace rows"
    assert all(len(row) == 4 for row in rows), "malformed trace"
    assert [row[0] for row in rows] == [
        str(i) for i in range(expected)
    ], "invalid sample index sequence"


def _verdict(golden: Path, mutant: Path) -> tuple[str, int]:
    _complete_trace(golden)
    _complete_trace(mutant)
    cp = _run(
        [PY, "tools/trace_compare.py", "--golden", str(golden), "--mutant", str(mutant), "--json"],
        "比较",
    )
    result = json.loads(cp.stdout)
    assert not result["truncated"] and result["n_golden"] == result["n_mutant"] > 0
    return result["verdict"], cp.returncode


def _site(rel: str, needle: str) -> int:
    matches = [
        i
        for i, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), 1)
        if needle in line and not line.lstrip().startswith("//")
    ]
    assert len(matches) == 1, f"mutation site is ambiguous or missing: {rel} {needle}"
    return matches[0]


@requires
def test_clean_design_trace_is_not_empty():
    """先证明金标 trace 有内容 —— 否则"两端都 unobserved"会假过。"""
    g = _sim("twoneck_golden", None)
    rows = [x for x in g.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) > 4000, f"金标 trace 只有 {len(rows)} 行，判据没有观测面"
    assert rows[0].split()[0] == "0"


@requires
def test_neck_kill_must_be_detected():
    """端 1（必被检出）：`q_next = (quo << P_STAGES) | …` 的 `<<` 改成 `>>`。

    先验理由：`quo` 是 MSB 优先累积的商，每个时钟把最新算出的 `P_STAGES` 位
    **左移 P_STAGES** 后并进去；换成右移等于把每一组都搬到错误的位置 ⇒ 商错到
    与真值差好几个数量级 ⇒ 20 位输出上远超 1 LSB ⇒ 必然可观测。
    （这**不是**"跑完再挑"：本条的形状选自审计文档 §2 里 div_floor 那条已知缺陷族。）
    """
    rtl = (
        _inject(
            "ExprUpdate",
            "rtl/core/div_floor.sv",
            _site("rtl/core/div_floor.sv", "q_next = (quo <<"),
            "twoneck_divshift",
        )
        / "rtl"
    )
    g = _sim("twoneck_golden", None)
    m = _sim("twoneck_divshift", rtl)
    verdict, _ = _verdict(g, m)
    assert verdict == "killed", f"端 1 没被检出（verdict={verdict}）—— 比较器或注入器有一端是坏的"


@requires
def test_neck_capacity_guard_removal_must_not_be_detected():
    """端 2（必然等价/无观测）：删掉 `weight_store` 的容量条件项（审计文档 §2 的 #10 负对照）。

    先验理由：该守卫要求 `ΣW + 新值 >= 2^60`；冻结尺寸下全库上界只有
    `1278 × (2^47−1) ≈ 2^57.4`，且 `wr_slice`/`wr_unit` 的位宽把可寻址格数限死在
    `32×128 = 4096` 格（`4096 × (2^47−1)` 仍 < 2^60）⇒ 这个条件**结构性不可达**，
    删掉它不改变任何输出 ⇒ 必须 unobserved。这一端用来证明"断言不是无条件变红"。
    """
    rtl = (
        _inject(
            "ExprDelete",
            "rtl/core/weight_store.sv",
            _site("rtl/core/weight_store.sv", "assign accept"),
            "twoneck_capoff",
            term=5,
        )
        / "rtl"
    )
    g = _sim("twoneck_golden", None)
    m = _sim("twoneck_capoff", rtl)
    verdict, _ = _verdict(g, m)
    assert verdict == "unobserved", (
        f"端 2 被判成了 {verdict} —— 要么容量条件其实可达（那先验理由就是错的），"
        f"要么比较器把『没差异』误判成差异"
    )
