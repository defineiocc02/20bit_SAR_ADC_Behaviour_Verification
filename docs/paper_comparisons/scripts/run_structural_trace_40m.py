#!/usr/bin/env python3
"""Run structural timing trace with Verilator; record functional event difference."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--verilator", default=shutil.which("verilator"))
args = parser.parse_args()
if not args.verilator:
    raise SystemExit("Verilator not found: supply --verilator or add it to PATH")
repo = Path(__file__).resolve().parents[3]
out = args.out.resolve()
out.mkdir(parents=True, exist_ok=False)
with tempfile.TemporaryDirectory(prefix="sar_struct_") as temp:
    t = Path(temp)
    (t / "repo").symlink_to(repo, target_is_directory=True)
    (t / "tb.sv").symlink_to(Path(__file__).resolve().with_name("structural_trace_40m_tb.sv"))
    (t / "out").symlink_to(out, target_is_directory=True)
    tool = Path(args.verilator).resolve()
    env = os.environ.copy()
    if (tool.parent.parent / "include").exists():
        (t / "tool").symlink_to(tool.parent.parent, target_is_directory=True)
        env["VERILATOR_ROOT"] = str(t / "tool")
        tool = t / "tool" / "bin" / tool.name
    (t / "bin").mkdir()
    wrapper = t / "bin" / "python"
    wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 "$@"\n')
    wrapper.chmod(0o755)
    env["PATH"] = str(t / "bin") + os.pathsep + env.get("PATH", "")
    env["CXXFLAGS"] = "--std=c++20 -DVL_TIME_CONTEXT"
    src = [v.strip() for v in (repo / "rtl/rtl_sources.f").read_text().splitlines() if v.strip()]
    src += [str(p.relative_to(repo)) for p in sorted((repo / "ad4080_dsp/rtl").glob("*.sv"))]
    top = "structural_trace_40m_tb"
    build = t / "build"
    cmd = [
        str(tool),
        "--binary",
        "--trace",
        "--unroll-count",
        "8192",
        "--timing",
        "--assert",
        "-j",
        "1",
        "-Wno-fatal",
        "-Werror-PINMISSING",
        "-Werror-SELRANGE",
        "--top-module",
        top,
        "-Irtl/params",
        "--Mdir",
        str(build),
        "-MAKEFLAGS",
        "CFG_CXXFLAGS_PCH_I=-include-pch CFG_GCH_IF_CLANG=.gch OPT_FAST=-O0 OPT_SLOW=-O0",
        *src,
        str(t / "tb.sv"),
    ]
    with (out / "build.log").open("wb") as f:
        subprocess.run(
            cmd,
            cwd=t / "repo",
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=True,
        )
    with (out / "run.log").open("wb") as f:
        subprocess.run(
            [str(build / ("V" + top)), "+outdir=" + str(t / "out")],
            cwd=t / "repo",
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=True,
        )
if "STRUCTURAL_TRACE_40M_COMPLETE" not in (out / "run.log").read_text(errors="replace"):
    raise RuntimeError("simulation completion marker missing")
rows = [
    r
    for r in csv.DictReader((out / "structural_trace.csv").open())
    if r["mode"] == "0" and r["current_id"] == "2" and r["rdac_valid"] == "1"
]
rtl = [int(r["rdac_nominal_command"]) for r in rows]
if len(rtl) != 7:
    raise RuntimeError(f"expected seven observed RTL events, got {len(rtl)}")
sys.path.insert(0, str(repo / "src"))
from adi_model.reference_charge import sar_loading_codes  # noqa: E402

py = sar_loading_codes(173, 9, 1, 0).tolist()
report = {
    "rtl_codes": rtl,
    "python_trial_reject_codes": py,
    "rtl_clock_hz": 640000000,
    "sample_rate_target_hz": 40000000,
    "scope": "functional simulation only; trial/reject and resolved events have different contracts, not a reference-current measurement",
}
(out / "event_comparison.json").write_text(json.dumps(report, indent=2) + "\n")
print("EVENT_CONTRACT_DIFF", len(py), len(rtl), rtl)
