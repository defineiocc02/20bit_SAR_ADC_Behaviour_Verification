"""Reproduce the 2026-10-02 arithmetic/structure calculations in a fresh directory.

This does not run RTL, synthesis, STA or analog simulation. Captured scripts and
logs remain unchanged; repository source hashes must match the frozen manifest.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    evidence = repo / "docs/evidence/20261002/principle_reaudit"
    manifest = json.loads((evidence / "input_manifest.json").read_text())
    for row in manifest["rtl"]:
        actual = hashlib.sha256((repo / row["path"]).read_bytes()).hexdigest()
        if actual != row["sha256"]:
            raise ValueError(f"Source differs from frozen audit: {row['path']}")
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location(
        "captured_calibration_math", evidence / "calibration_math_check.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load captured calculation script")
    module = importlib.util.module_from_spec(spec)
    bytecode_disabled = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = bytecode_disabled
    # Relocate only runtime I/O paths; the captured source remains byte-identical.
    module.REPO, module.HERE = repo, out
    with (out / "calibration_math_check.log").open("w") as log, contextlib.redirect_stdout(log):
        module.main()
    for name in (
        "calibration_math_result.json",
        "calibration_domain_nominal_mirror.json",
        "calibration_domain_scaled_2pow20_mirror.json",
    ):
        if (out / name).read_bytes() != (evidence / name).read_bytes():
            raise ValueError(f"Arithmetic reproduction differs: {name}")
    command = [
        sys.executable,
        str(evidence / "count_synthesis_structure.py"),
        "--repo",
        str(repo),
        "--out",
        str(out / "synthesis_structure_counts.json"),
    ]
    with (out / "synthesis_structure_counts.log").open("w") as log:
        subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT, check=True)
    actual = json.loads((out / "synthesis_structure_counts.json").read_text())
    expected = json.loads((evidence / "synthesis_structure_counts.json").read_text())
    # Documentation commits may change HEAD, never RTL identity or calculations.
    expected["head"] = actual["head"]
    if actual != expected:
        raise ValueError("Structure reproduction differs beyond documentation HEAD")
    result = {
        "status": "PASS",
        "scope": "EXACT_CALCULATION_AND_SOURCE_FORMULAS_NOT_RTL_NOT_EDA_NOT_FORMAL",
        "frozen_source_head": manifest["head"],
        "reproduction_head": actual["head"],
        "rtl_source_hashes_checked": len(manifest["rtl"]),
        "arithmetic_outputs_byte_identical": 3,
        "structure_equal_except_documentation_head": True,
        "payloads": [
            {
                "path": p.name,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "bytes": p.stat().st_size,
            }
            for p in sorted(out.iterdir())
            if p.is_file()
        ],
    }
    (out / "reproduction_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print("PRINCIPLE_REAUDIT_CALCULATION_REPRODUCED sources=26 exact_math=3 structure=PASS")


if __name__ == "__main__":
    main()
