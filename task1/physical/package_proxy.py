#!/usr/bin/env python3
"""Package one completed public proxy run and bind it to its inputs."""

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
from pathlib import Path

FLOW_PATH = Path("nangate45/task1_module1/base")
EXPECTED_DESIGN = "module1_top"
FINAL_FILES = {
    "6_final.gds": "module1_top.gds",
    "6_final.v": "module1_top.v",
    "6_final.def": "module1_top.def",
    "6_final.odb": "module1_top.odb",
    "6_final.sdc": "module1_top.sdc",
    "6_final.spef": "module1_top.spef",
}
INPUT_FILES = (
    "rtl/slice_scheduler.sv",
    "rtl/fig11_dem.sv",
    "rtl/module1_top.sv",
    "physical/config.mk",
    "physical/constraint.sdc",
    "physical/post_route.tcl",
    "physical/run_proxy.sh",
    "physical/package_proxy.py",
)


def describe_file(path: Path) -> dict:
    """Return byte count, modification time and SHA-256 for one artifact."""
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Empty artifact: {path}")
    return {
        "bytes": len(data),
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def verify_gds(path: Path) -> dict:
    """Check stream records, top-cell identity, hierarchy and drawing content."""
    data = path.read_bytes()
    offset = 0
    cells = set()
    referenced = set()
    elements = 0
    seen_end = False
    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("Truncated GDS record header")
        size, record_type, _ = struct.unpack_from(">HBB", data, offset)
        if size < 4 or size % 2 or offset + size > len(data):
            raise ValueError(f"Invalid GDS record at byte {offset}")
        if offset == 0 and record_type != 0x00:
            raise ValueError("GDS header record is missing")
        if record_type in {0x06, 0x12}:  # STRNAME and SNAME.
            name = data[offset + 4 : offset + size].rstrip(b"\x00").decode("ascii")
            (cells if record_type == 0x06 else referenced).add(name)
        if record_type in {0x08, 0x09, 0x2D}:  # BOUNDARY, PATH and BOX.
            elements += 1
        offset += size
        if record_type == 0x04:  # ENDLIB. Some writers align with zero padding.
            seen_end = True
            if any(data[offset:]):
                raise ValueError("Unexpected data after GDS ENDLIB")
            break
    tops = sorted(cells - referenced)
    if not seen_end or tops != [EXPECTED_DESIGN] or not elements:
        raise ValueError(f"Unusable GDS: tops={tops}, elements={elements}, end={seen_end}")
    if referenced - cells:
        raise ValueError(f"Unresolved GDS cells: {sorted(referenced - cells)}")
    return {"tops": tops, "cells": len(cells), "geometry_records": elements}


def package(output: Path, source: Path) -> None:
    """Validate a completed run and collect the final files for delivery."""
    delivery = output / "delivery"
    final = output / "flow/results" / FLOW_PATH
    logs = output / "flow/logs" / FLOW_PATH
    sta_report = (delivery / "post_route_sta.rpt").read_text()
    if "TASK1_POST_ROUTE_STA_COMPLETE" not in sta_report or "Error:" in sta_report:
        raise ValueError("Post-route timing report did not complete")
    timing = {}
    for mode, sense, ending in (
        ("Setup", "max", "=== Hold, extracted RC ==="),
        ("Hold", "min", "=== Final proxy power-grid connectivity ==="),
    ):
        start = f"=== {mode}, extracted RC ==="
        section = sta_report.partition(start)[2].partition(ending)[0]
        if "Startpoint:" not in section:
            raise ValueError(f"No analyzed {mode} path in timing report")
        values = {}
        for label, key in (("worst slack", "worst_slack_ns"), ("tns", "tns_ns")):
            match = re.search(rf"^{label} {sense} ([+-]?[0-9.]+)$", section, re.MULTILINE)
            if match is None or not math.isfinite(float(match.group(1))):
                raise ValueError(f"Missing finite {mode} {label} in timing report")
            values[key] = float(match.group(1))
        timing[mode.lower()] = values
    if not re.fullmatch(r"[0-9a-f]{40}", (delivery / "rtl_commit.txt").read_text().strip()):
        raise ValueError("Invalid RTL commit identity")
    if not re.search(r"@sha256:[0-9a-f]{64}", (delivery / "container.txt").read_text()):
        raise ValueError("Container digest is missing")

    for source_name, destination_name in FINAL_FILES.items():
        describe_file(final / source_name)
        shutil.copy2(final / source_name, delivery / destination_name)
    shutil.copy2(source / "physical/constraint.sdc", delivery / "constraint.sdc")
    shutil.copy2(output / "flow.log", delivery / "flow.log")
    for report in sorted(logs.glob("*.json")):
        if report.stat().st_size > 0:
            metrics = delivery / "metrics"
            metrics.mkdir(exist_ok=True)
            shutil.copy2(report, metrics / report.name)
    gds = verify_gds(delivery / "module1_top.gds")

    manifest = {
        "claim": "PUBLIC_PROXY_IMPLEMENTATION",
        "platform": "Nangate45 / FreePDK45",
        "corner": "typical",
        "clock_period_ns": 6.25,
        "rtl_commit": (delivery / "rtl_commit.txt").read_text().strip(),
        "container_digest": (delivery / "container.txt").read_text().strip(),
        "gds": gds,
        "timing": timing,
        "inputs": {name: describe_file(source / name) for name in INPUT_FILES},
        "artifacts": {
            path.relative_to(delivery).as_posix(): describe_file(path)
            for path in sorted(delivery.rglob("*"))
            if path.is_file()
        },
        "scope": {
            "rtl_to_gds": "completed",
            "post_route_setup_and_hold": "single typical corner with extracted SPEF",
            "target_process": "unspecified",
            "foundry_drc_lvs_em_ir": "not executed",
            "logical_equivalence": "not asserted by this flow",
        },
    }
    (delivery / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified GDS top {EXPECTED_DESIGN} with {gds['cells']} cells and timing report")


def main() -> None:
    """Parse paths for the post-flow packaging step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("task_source", type=Path)
    args = parser.parse_args()
    package(args.output, args.task_source)


if __name__ == "__main__":
    main()
