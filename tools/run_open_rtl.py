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
        "sar_trial_tb": "SAR_TRIAL_COMPLETE",
        "calibration_recovery_tb": "CALIBRATION_RECOVERY_COMPLETE",
        "structural_adc_tb": "STRUCTURAL_ADC_COMPLETE",
        "calibration_physical_tb": "CALIBRATION_PHYSICAL_COMPLETE",
        "review_top_protocol_tb": "REVIEW_TOP_PROTOCOL_COMPLETE",
        "review_leaf_tb": "REVIEW_LEAF_COMPLETE",
        "review_recon_protocol_tb": "REVIEW_RECON_PROTOCOL_COMPLETE",
        "review_dither_tb": "REVIEW_DITHER_COMPLETE",
        "review_config_tb": "REVIEW_CONFIG_COMPLETE",
        "p2_tb": "P2 RESULT: PASS",
        "p2_oracle_tb": "P2_ORACLE_COMPLETE codes=1048576 errors=0",
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
    sources = [
        REPO / line for line in (REPO / "rtl/rtl_sources.f").read_text().splitlines() if line
    ]
    if not sources or any(not p.is_file() for p in sources):
        raise RuntimeError("RTL source manifest contains missing files")
    version = subprocess.run([*command, "--version"], check=True, capture_output=True, text=True)
    (out / "version.log").write_text(version.stdout + version.stderr, encoding="utf-8")
    # A production physical reduction has 4095 named nodes; the 32x128
    # coefficient-store maximum needs 8191. Verilator 5.020 defaults to 1024.
    # This explicit finite elaboration budget is not a warning suppression.
    elaboration_args = ["--unroll-count", "8192"]
    # Lint production hierarchies separately from testbench stimulus widths.
    # Treat structural and arithmetic diagnostics as errors, without hiding them
    # behind the simulation compile's allowance for testbench warnings.
    for lint_top, profile, parameters in (
        ("sar20_digital_core", "structural", []),
        ("sar20_digital_core", "compatibility", ["-GP_STRUCTURAL=0"]),
        ("sadc_enc", "standalone", []),
    ):
        with (out / f"{lint_top}.{profile}.lint.log").open("w", encoding="utf-8") as log:
            lint_result = subprocess.run(
                [
                    *command,
                    "--lint-only",
                    *elaboration_args,
                    "--top-module",
                    lint_top,
                    "-Irtl/params",
                    *parameters,
                    "-Werror-WIDTH",
                    "-Werror-LATCH",
                    "-Werror-MULTIDRIVEN",
                    "-Werror-UNOPTFLAT",
                    "-Werror-CASEINCOMPLETE",
                    "-Werror-PINMISSING",
                    "-Werror-SELRANGE",
                    *[str(p.relative_to(REPO)) for p in sources],
                ],
                cwd=REPO,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120,
            )
        if lint_result.returncode:
            print((out / f"{lint_top}.{profile}.lint.log").read_text(), flush=True)
            lint_result.check_returncode()
    for top, marker in benches.items():
        if selected and top not in selected:
            continue
        (out / f"{top}.run.log").unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="sar_rtl_") as build:
            args = [
                *command,
                "--binary",
                *elaboration_args,
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
            # Only delegate T5 when the independent full-code bench is selected.
            delegate_oracle = top == "p2_tb" and (not selected or "p2_oracle_tb" in selected)
            extra_args = ["+skip_oracle"] if delegate_oracle else []
            run_log = out / f"{top}.run.log"
            with run_log.open("w", encoding="utf-8") as log:
                cp = subprocess.run(
                    [
                        str(Path(build) / f"V{top}"),
                        f"+vdir={REPO / 'sim/vectors'}",
                        f"+outdir={out}",
                        "+injdith",
                        "+trace=p3_trace.txt",
                        *extra_args,
                    ],
                    cwd=out,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    # A standalone legacy P2 run still includes the full oracle.
                    timeout=900 if top == "p2_tb" and not delegate_oracle else 120,
                )
            output = run_log.read_text(encoding="utf-8")
            if cp.returncode:
                print(output, end="", flush=True)
            cp.check_returncode()
            if marker not in output:
                raise RuntimeError(f"{top}: missing completion marker")
            print(output, end="", flush=True)


if __name__ == "__main__":
    main()
