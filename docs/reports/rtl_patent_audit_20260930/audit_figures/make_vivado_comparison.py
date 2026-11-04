#!/usr/bin/env python3
"""Plot only complete synthesis runs without the observed undriven-net defect."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def stored_path(path: Path) -> Path:
    """Reports may be archived losslessly to satisfy the repository size limit."""
    if path.is_file():
        return path
    compressed = path.with_name(path.name + ".gz")
    if path.suffix == ".rpt" and compressed.is_file():
        return compressed
    raise FileNotFoundError(path)


def evidence_bytes(path: Path) -> bytes:
    """Use original report bytes, rejecting malformed compressed evidence."""
    stored = stored_path(path)
    value = stored.read_bytes()
    return gzip.decompress(value) if stored != path else value


def digest(path: Path) -> str:
    """Return the digest of one original synthesis artifact."""
    return hashlib.sha256(evidence_bytes(path)).hexdigest()


def match(pattern: str, value: str) -> str:
    """Read a required report field without supplying a fallback value."""
    found = re.search(pattern, value, re.MULTILINE)
    if found is None:
        raise ValueError(f"Missing report field: {pattern}")
    return found.group(1)


def read_run(label: str, root: Path) -> dict:
    """Reject incomplete or invalid runs before extracting comparable FPGA metrics."""
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest["status"] not in {"SYNTH_COMPLETE_TIMING_MET", "SYNTH_COMPLETE_TIMING_NOT_MET"}:
        raise ValueError(f"Run is incomplete or rejected: {root}: {manifest['status']}")
    log = evidence_bytes(root / "vivado.log").decode("utf-8")
    if re.search(r"\[Synth 8-3848\].*does not have driver", log):
        raise ValueError(f"Refusing an undriven netlist as PPA evidence: {root}")
    reviewed = root / "reviewed_result.json"
    if reviewed.exists() and not json.loads(reviewed.read_text())["valid_ppa_comparison"]:
        raise ValueError(f"Independent review invalidated this run: {root}")
    status = dict(line.split("=", 1) for line in (root / "out/status.txt").read_text().splitlines())
    util = evidence_bytes(root / "out/utilization.rpt").decode("utf-8")
    timing = evidence_bytes(root / "out/timing_paths.rpt").decode("utf-8")
    power = evidence_bytes(root / "out/power_vectorless.rpt").decode("utf-8")
    resources = {}
    for name, row in {
        "lut": "Slice LUTs*",
        "ff": "Register as Flip Flop",
        "latch": "Register as Latch",
        "dsp": "DSPs",
        "bram_tiles": "Block RAM Tile",
    }.items():
        resources[name] = float(match(r"^\|\s*" + re.escape(row) + r"\s*\|\s*([\d.]+)", util))
    record = {
        "label": label,
        "artifact": str(root.resolve()),
        "part": status["PART"],
        "vivado_version": status["VIVADO_VERSION"],
        "requested_period_ns": float(status["PERIOD_NS"]),
        "actual_period_ns": float(match(r"period=([\d.]+)ns", timing)),
        "stages": int(status["P_RECON_STAGES"]),
        "setup_wns_ns": float(status["WNS_NS"]),
        "data_path_ns": float(match(r"Data Path Delay:\s*([\d.]+)ns", timing)),
        "logic_levels": int(match(r"Logic Levels:\s*(\d+)", timing)),
        "source": match(r"Source:\s*([^\r\n]+)", timing).strip(),
        "destination": match(r"Destination:\s*([^\r\n]+)", timing).strip(),
        "vectorless_power_w": float(match(r"Total On-Chip Power \(W\)\s*\|\s*([\d.]+)", power)),
        "resources": resources,
        "sha256": {
            name: digest(root / name)
            for name in (
                "manifest.json",
                "vivado.log",
                "out/status.txt",
                "out/utilization.rpt",
                "out/utilization_hier.rpt",
                "out/timing_paths.rpt",
                "out/timing_summary.rpt",
                "out/check_timing.rpt",
                "out/drc.rpt",
                "out/power_vectorless.rpt",
            )
        },
        "sources_sha256": manifest["sources_sha256"],
        "scope": "FPGA post-synthesis OOC screening; vectorless power; no ASIC or board signoff",
    }
    if not all(
        math.isfinite(record[k]) for k in ("setup_wns_ns", "data_path_ns", "vectorless_power_w")
    ):
        raise ValueError("Nonfinite metric")
    record["stored_inputs"] = {
        name: {
            "path": str(stored_path(root / name)),
            "sha256": hashlib.sha256(stored_path(root / name).read_bytes()).hexdigest(),
            "original_sha256": original_sha,
        }
        for name, original_sha in record["sha256"].items()
    }
    return record


def main() -> None:
    """Render only the explicitly provided, compatible synthesis runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, help="LABEL=artifact directory")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = [
        read_run(label, Path(path)) for label, path in (item.split("=", 1) for item in args.run)
    ]
    for key in ("part", "vivado_version", "requested_period_ns", "actual_period_ns"):
        if len({record[key] for record in runs}) != 1:
            raise ValueError(f"Comparison has inconsistent {key}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(runs, indent=2) + "\n")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig, axes = plt.subplots(2, 2, figsize=(14, 8.5), layout="constrained")
    names = [record["label"] for record in runs]
    colors = ["#63788a", "#197c91", "#267f52", "#c08030"][: len(runs)]
    for ax, title, values, unit in (
        (axes[0, 0], "Mapped LUTs", [r["resources"]["lut"] for r in runs], "LUT"),
        (axes[0, 1], "Flip-flops", [r["resources"]["ff"] for r in runs], "FF"),
        (
            axes[1, 0],
            f"Setup WNS at {runs[0]['requested_period_ns']:g} ns requested",
            [r["setup_wns_ns"] for r in runs],
            "ns",
        ),
        (axes[1, 1], "Worst reported data path", [r["data_path_ns"] for r in runs], "ns"),
    ):
        bars = ax.bar(names, values, color=colors)
        ax.bar_label(
            bars, labels=[f"{v:,.0f}" if unit != "ns" else f"{v:.3f}" for v in values], padding=5
        )
        ax.set_title(title, weight="bold")
        ax.set_ylabel(unit)
        ax.grid(axis="y", alpha=0.18)
        ax.set_axisbelow(True)
        ax.margins(y=0.22)
        ax.spines[["top", "right"]].set_visible(False)
    axes[1, 0].axhline(0, color="#b53f45", linewidth=1)
    fig.suptitle(
        "COMPLETE OOC SYNTHESIS | REPAIRED RTL\n"
        f"{runs[0]['part']} | Vivado {runs[0]['vivado_version']} | "
        f"Actual clock period {runs[0]['actual_period_ns']:.3f} ns\n"
        "Negative slack is a measured gap, not timing closure. No routed or ASIC PPA claim.",
        fontsize=14,
        weight="bold",
    )
    fig.savefig(args.output, dpi=160)
    plt.close(fig)
    print(args.output)


if __name__ == "__main__":
    main()
