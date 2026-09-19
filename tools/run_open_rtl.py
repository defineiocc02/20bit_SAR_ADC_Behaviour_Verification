#!/usr/bin/env python3
"""Compile and execute portable RTL regressions; missing tools are failures.

Requires Verilator 5.x with timing support and a C++20 toolchain. Set VERILATOR
as a shell-style command (e.g. 'python -m verilator' for a local wheel).
Builds use temporary paths; reproducible logs are saved outside generated RTL.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    benches = {
        "review_dither_tb": "REVIEW_DITHER_COMPLETE",
        "review_config_tb": "REVIEW_CONFIG_COMPLETE",
        "p2_tb": "P2 RESULT: PASS",
        "p2_periph_tb": "p2_periph PASS",
        "p2_smoke_tb": "p2_smoke PASS",
        "p1_tb": "P1 RESULT: PASS",
        "p3_top_tb": "P3 TOP RESULT: PASS",
    }
    parser.add_argument(
        "--tops", nargs="+", choices=list(benches), help="Run selected testbenches only"
    )
    selected = parser.parse_args().tops
    command = shlex.split(os.environ.get("VERILATOR", "verilator"))
    if not command or shutil.which(command[0]) is None:
        raise SystemExit("Verilator is required; install it or set VERILATOR")
    out = REPO / "sim/artifacts/open_rtl"
    out.mkdir(parents=True, exist_ok=True)
    sources = sorted((REPO / "rtl/core").glob("*.sv")) + sorted((REPO / "rtl/top").glob("*.sv"))
    version = subprocess.run([*command, "--version"], check=True, capture_output=True, text=True)
    (out / "version.log").write_text(version.stdout + version.stderr, encoding="utf-8")
    for top, marker in benches.items():
        if selected and top not in selected:
            continue
        (out / f"{top}.run.log").unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="sar_rtl_") as build:
            args = [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "-j",
                "2",
                "-Wno-fatal",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
                "--top-module",
                top,
                "-Irtl/params",
                "--Mdir",
                build,
            ]
            args += [str(p.relative_to(REPO)) for p in sources]
            args += [f"sim/tb/{top}.sv"]
            with (out / f"{top}.build.log").open("w") as log:
                subprocess.run(
                    args, cwd=REPO, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300
                )
            run_log = out / f"{top}.run.log"
            with run_log.open("w", encoding="utf-8") as log:
                cp = subprocess.run(
                    [
                        str(Path(build) / f"V{top}"),
                        f"+vdir={REPO / 'sim/vectors'}",
                        f"+outdir={out}",
                        "+injdith",
                        "+trace=p3_trace.txt",
                    ],
                    cwd=out,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    # P2 includes the complete 2^20-code oracle (~6 min locally).
                    timeout=900 if top == "p2_tb" else 120,
                )
            output = run_log.read_text(encoding="utf-8")
            cp.check_returncode()
            if marker not in output:
                raise RuntimeError(f"{top}: missing completion marker")
            print(output, end="", flush=True)


if __name__ == "__main__":
    main()
