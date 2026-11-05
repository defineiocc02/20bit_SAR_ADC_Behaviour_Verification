"""保留旧 P3 接口的变异注入正、负对照验证（Phase 1a）。

审计文档 §6.5 要求先检查明确可检出的变异与有先验理由的无观测变异，
避免比较器恒判 killed 或恒判 unobserved 仍生成误导分数。
该测试保留原先远程 EDA VM／VCS 路径和显式 RTL_SIM_TWONECK=1 开关；
默认跳过；启用后按所列入口核对完整执行及轨迹。

流程先取得未变异设计轨迹，再检查 div_floor.sv 的 quo << P_STAGES 改为右移
应被检出，以及 weight_store.sv 的容量条件删除在所述冻结边界下应无观测差异。
两类先验理由分别写在测试中；不能根据运行结果事后选择对照。
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
    """运行旧测试台并读取轨迹；rtl_prefix=None 表示未变异设计。

    允许的失配返回码仅在全部预期观测完成后用于判定。编译失败、超时、执行
    不完整或旧轨迹均不能作为变异比较结论。
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
    """先检查未变异轨迹非空，避免空观测使两类对照都被判为 unobserved。"""
    g = _sim("twoneck_golden", None)
    rows = [x for x in g.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) > 4000, f"金标 trace 只有 {len(rows)} 行，判据没有观测面"
    assert rows[0].split()[0] == "0"


@requires
def test_neck_kill_must_be_detected():
    """正对照：将 q_next=(quo<<P_STAGES)|… 中的左移改为右移。

    商按最高位优先累积，每周期将已算商左移 P_STAGES 后并入新位；改为右移会
    破坏组位位置，在所选 20 位输出夹具上产生超过 1 LSB 的可观测误差。
    变异类型来自审计文档 §2 的 div_floor 缺陷族，不是根据本次输出事后挑选。
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
    """负对照：删除审计文档 §2 #10 的 weight_store 容量条件。

    先验边界为 ΣW+新值>=2^60；旧冻结库上界为 1278*(2^47-1)≈2^57.4。
    wr_slice、wr_unit 位宽限制的 32*128=4096 个地址全部写满，也仍有
    4096*(2^47-1)<2^60，因此在该声明边界内条件不可达。该对照检查比较器不会
    无条件判差异；它不是对其他几何或当前所有状态的无界等价证明。
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
