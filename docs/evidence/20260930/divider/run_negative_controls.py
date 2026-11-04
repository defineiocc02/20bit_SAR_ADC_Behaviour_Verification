#!/usr/bin/env python3
"""Re-run the independent integer oracle against two isolated divider defects."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--repo", required=True, type=Path)
parser.add_argument("--output-dir", required=True, type=Path)
args = parser.parse_args()
repo = args.repo.resolve()
out = args.output_dir.resolve()
out.mkdir(parents=True, exist_ok=True)
(out / "mutations.json").unlink(missing_ok=True)
files = [
    "rtl/core/cal_residue_mac.sv",
    "rtl/core/adc2_dec.sv",
    "rtl/core/div_floor.sv",
    "rtl/core/cal_output_stage.sv",
    "sim/tb/arithmetic_audit_tb.sv",
    "rtl/params/rtl_params.vh",
    "tools/audit_rtl_arithmetic.py",
]
mutations = [
    (
        "truncate_high_denominator",
        "if (!dv_large && !trial_difference[W_R]) begin",
        "if (!trial_difference[W_R]) begin",
    ),
    ("omit_negative_floor_correction", "(r_next != 0)", "1'b0"),
]
records = []
for name, old, new in mutations:
    case_out = out / name
    case_out.mkdir(exist_ok=True)
    for stale in ("run.log", "runner.log", "manifest.json"):
        (case_out / stale).unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="sar-divider-negative-") as tmp:
        snap = Path(tmp)
        for file in files:
            target = snap / file
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / file, target)
        divider = snap / "rtl/core/div_floor.sv"
        source = divider.read_text()
        if source.count(old) != 1:
            raise RuntimeError(f"mutation must have one exact target: {name}")
        divider.write_text(source.replace(old, new))
        command = [
            sys.executable,
            str(snap / "tools/audit_rtl_arithmetic.py"),
            "--output-dir",
            str(case_out),
        ]
        result = subprocess.run(
            command, capture_output=True, text=True, env=os.environ.copy(), timeout=240, check=False
        )
        (case_out / "runner.log").write_text(result.stdout + result.stderr)
        if not (case_out / "run.log").is_file():
            raise RuntimeError(f'oracle did not run for {name}; inspect {case_out / "runner.log"}')
        log = (case_out / "run.log").read_text()
        caught = result.returncode != 0 and "DIV mismatch" in log
        if (case_out / "manifest.json").exists() or not caught:
            raise RuntimeError(
                f"mutation not detected by divider oracle: {name}: {result.returncode}: {log}"
            )
        records.append(
            {
                "mutation": name,
                "status": "DETECTED",
                "old": old,
                "new": new,
                "runner_returncode": result.returncode,
                "divider_original_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "divider_mutated_sha256": hashlib.sha256(divider.read_bytes()).hexdigest(),
                "failure_line": next(line for line in log.splitlines() if "DIV mismatch" in line),
                "log_sha256": hashlib.sha256(log.encode()).hexdigest(),
                "command": command,
            }
        )
        print(records[-1]["failure_line"], flush=True)
manifest = {
    "status": "PASS",
    "seed": 20260930,
    "scope": "Negative controls: production source is not modified.",
    "sources_sha256": {
        file: hashlib.sha256((repo / file).read_bytes()).hexdigest() for file in files
    },
    "mutations": records,
}
(out / "mutations.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("DIVIDER_NEGATIVE_CONTROLS_COMPLETE detected=2")
