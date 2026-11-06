#!/usr/bin/env python3
"""Run a standalone Yosys standard-cell mapping and record its inputs."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

DESIGN = "module1_top"
SOURCES = ("slice_scheduler.sv", "fig11_dem.sv", "module1_top.sv")


def sha256(path: Path) -> str:
    """Hash the bytes of a synthesis input or artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(yosys: list, liberty: Path, output: Path) -> None:
    """Map the module with the requested Yosys and Liberty inputs."""
    task = Path(__file__).resolve().parents[1]
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    if not liberty.is_file():
        raise ValueError(f"Liberty file is missing: {liberty}")
    output.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="task1-synth-") as work:
        directory = Path(work)
        for name in SOURCES:
            shutil.copy2(task / "rtl" / name, directory / name)
        shutil.copy2(liberty, directory / "cells.lib")
        (directory / "abc.constr").write_text("set_driving_cell BUF_X1\nset_load 5.0\n")
        script = (
            "read_liberty -lib cells.lib\n"
            f"read_verilog -sv {' '.join(SOURCES)}\n"
            f"hierarchy -check -top {DESIGN}\n"
            f"synth -top {DESIGN} -flatten -noabc\n"
            "dfflibmap -liberty cells.lib\n"
            "abc -liberty cells.lib -constr abc.constr -D 6250\n"
            "clean\n"
            f"hierarchy -check -top {DESIGN}\n"
            "check -assert\n"
            "tee -o synthesis.rpt stat -liberty cells.lib\n"
            f"write_verilog -noattr {DESIGN}.v\n"
        )
        (directory / "synth.ys").write_text(script)
        version = subprocess.run(
            [*yosys, "-V"], cwd=directory, check=True, text=True, capture_output=True
        ).stdout.strip()
        with (output / "synthesis.log").open("w") as log:
            subprocess.run(
                [*yosys, "-s", "synth.ys"],
                cwd=directory,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        for name in ("synth.ys", "abc.constr", "synthesis.rpt", f"{DESIGN}.v"):
            shutil.copy2(directory / name, output / name)

    report = output / "synthesis.rpt"
    stats = "\n".join(line.rstrip() for line in report.read_text().splitlines()).rstrip() + "\n"
    report.write_text(stats)
    area = re.search(r"Chip area[^:]*:\s*([0-9.]+)", stats)
    if area is None or float(area.group(1)) <= 0:
        raise ValueError("No positive mapped chip area was reported")
    receipt = {
        "claim": "PUBLIC_PROXY_SYNTHESIS",
        "top": DESIGN,
        "library": "Nangate45 typical",
        "mapped_cell_area_um2": float(area.group(1)),
        "yosys": version,
        "liberty_sha256": sha256(liberty),
        "sources": {name: sha256(task / "rtl" / name) for name in SOURCES},
        "artifacts": {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()},
        "scope": "Technology mapping only. Timing constraints are used by ABC as a mapping target.",
    }
    (output / "synthesis.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Nangate45 proxy mapped cell area: {area.group(1)} um^2")


def main() -> None:
    """Parse the local technology-mapping inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yosys", default="yosys", help="Yosys executable")
    parser.add_argument("--yosys-arg", action="append", default=[], help="Prefix argument")
    parser.add_argument("--liberty", required=True, type=Path, help="Nangate45 typical Liberty")
    parser.add_argument("--out", required=True, type=Path, help="Empty output directory")
    args = parser.parse_args()
    run([args.yosys, *args.yosys_arg], args.liberty, args.out)


if __name__ == "__main__":
    main()
