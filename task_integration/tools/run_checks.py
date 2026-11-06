#!/usr/bin/env python3
"""Compile and run the digital integration test on an isolated output path."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.tools.run_rtl_checks import STRICT_LINT  # noqa: E402
from tools.run_open_rtl import (  # noqa: E402
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    verilator_compiler_args,
    write_json,
)

TOP = "sar20_digital_top"
BENCH = "sar20_integration_tb"
RTL = (
    "task1/rtl/slice_scheduler.sv",
    "task1/rtl/fig11_dem.sv",
    "task1/rtl/module1_top.sv",
    "task1/rtl/fig21_bridge.sv",
    "task1/rtl/module1_bridge_top.sv",
    "task2/rtl/dither_word_gen.sv",
    "task2/eight_rtl/rtl/dither8_top.sv",
    "task3/rtl/caaz20_pair.sv",
    "task_integration/rtl/sar20_digital_top.sv",
)
SOURCES = (*RTL, f"task_integration/tb/{BENCH}.sv")
FIELDS = (
    "cycles",
    "checks",
    "steady",
    "bypass",
    "pairs",
    "mismatched_dither_pairs",
    "rejections",
    "late_returns",
    "command_cases",
    "headroom_failures",
    "macro_faults",
)


def main() -> None:
    """运行外接重构顶层回归并生成输入与日志哈希。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = new_output(args.out)
    command = command_for("verilator")
    env = dict(os.environ)
    version = checked_run(
        [*command, "-V"], cwd=ROOT, env=env, log=out / "verilator.log", timeout=30
    )
    with tempfile.TemporaryDirectory(prefix="sar20_top_") as work_dir:
        work = Path(work_dir)
        alias_runtime(work, env, version)
        checked_run(
            [*command, "--lint-only", "--top-module", TOP, *STRICT_LINT, *RTL],
            cwd=ROOT,
            env=env,
            log=out / "lint.log",
            timeout=60,
        )
        checked_run(
            [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "-j",
                "2",
                "--top-module",
                BENCH,
                "--Mdir",
                str(work / "obj"),
                *verilator_compiler_args(),
                *STRICT_LINT,
                *SOURCES,
            ],
            cwd=ROOT,
            env=env,
            log=out / "compile.log",
            timeout=480,
        )
        run = checked_run(
            [str(work / "obj" / f"V{BENCH}")],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=60,
        )
    counts = completion(run, "SAR20_INTEGRATION_COMPLETE", FIELDS)
    if counts["steady"] != 4096 or counts["pairs"] != 2048 or counts["bypass"] != 64:
        raise RuntimeError(f"unexpected throughput or output counts: {counts}")
    inputs = (
        *SOURCES,
        "tools/run_open_rtl.py",
        "task1/tools/run_rtl_checks.py",
        "task_integration/tools/run_checks.py",
        "task_integration/SPEC.md",
        "task_integration/microarchitecture.md",
        "task_integration/verification_plan.yaml",
    )
    write_json(
        out / "result.json",
        {
            "scope": "digital_top_external_reconstruction_fixture",
            "status": "PASS",
            "counts": counts,
            "source_sha256": {name: digest(ROOT / name) for name in inputs},
            "log_sha256": {
                name: digest(out / name)
                for name in ("verilator.log", "lint.log", "compile.log", "run.log")
            },
        },
    )
    print(run.splitlines()[0])


if __name__ == "__main__":
    main()
