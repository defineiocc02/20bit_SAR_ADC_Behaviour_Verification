#!/usr/bin/env python3
"""Plot observed arithmetic/protocol audit counts; no simulated waveforms are invented."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def digest(path: Path) -> str:
    """Return the digest of an existing evidence or output file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Read the historical audit and render its unchanged count definitions."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--arithmetic",
        type=Path,
        default=report.parent / "sar_adc_calibration_audit_20260930/reproducible_run",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=report.parent
        / "sar_adc_review_20260919/calibration-closure/sim/artifacts/open_rtl",
    )
    args = parser.parse_args()
    paths = {
        "arithmetic_manifest": args.arithmetic / "manifest.json",
        "arithmetic_run": args.arithmetic / "run.log",
        "protocol_manifest": args.protocol / "structural_protocol.negative_controls.json",
        "protocol_run": args.protocol / "structural_protocol_tb.run.log",
        "baseline_failure": args.protocol / "structural_protocol.baseline.negative.log",
        "cancel_mutant_failure": args.protocol / "structural_protocol.cancel_mutant.negative.log",
    }
    arithmetic = json.loads(paths["arithmetic_manifest"].read_text())
    negatives = json.loads(paths["protocol_manifest"].read_text())
    arithmetic_log = paths["arithmetic_run"].read_text()
    protocol_log = paths["protocol_run"].read_text()
    match = re.search(
        r"ARITHMETIC_AUDIT_COMPLETE mac=(\d+) adc=(\d+) div=(\d+) flag_sequences=(\d+)",
        arithmetic_log,
    )
    if arithmetic["status"] != "PASS" or match is None or "Verilog $finish" not in arithmetic_log:
        raise ValueError("Arithmetic completion evidence is absent")
    counts = dict(
        zip(("mac", "adc", "div", "flag_sequences"), map(int, match.groups()), strict=True)
    )
    if any(arithmetic["counts"][key] != value for key, value in counts.items()):
        raise ValueError("Arithmetic manifest and run log disagree")
    match = re.search(
        r"STRUCTURAL_PROTOCOL_COMPLETE frames=(\d+) launches=(\d+) expected_drops=(\d+) "
        r"checks=(\d+) decoder_perturbations=(\d+)",
        protocol_log,
    )
    if match is None or "Verilog $finish" not in protocol_log:
        raise ValueError("Protocol completion evidence is absent")
    frames, launches, drops, checks, perturbations = map(int, match.groups())
    expected = negatives["fixed"]
    if (
        [frames, launches, drops, checks, perturbations]
        != [
            expected[key]
            for key in (
                "frames",
                "launches",
                "expected_drops",
                "clock_ticks",
                "phase_perturbations",
            )
        ]
        or frames != launches + drops
        or expected["exit_code"] != 0
    ):
        raise ValueError("Protocol manifest and run log disagree")
    for label, key in (
        ("baseline_failure", "baseline"),
        ("cancel_mutant_failure", "cancel_mutant"),
    ):
        raw = paths[label].read_text()
        if (
            negatives[key]["exit_code"] == 0
            or "analog enable changed between clock edges" not in raw
        ):
            raise ValueError(f"Negative control lacks the expected failure: {key}")
        # The logs print the 1ps simulation time unit; the manifest stores ns.
        tick = re.search(r"^\[(\d+)\] %Fatal:", raw, re.MULTILINE)
        if tick is None or int(tick.group(1)) != negatives[key]["failure_time_ns"] * 1000:
            raise ValueError(f"Negative-control failure timestamp disagrees: {key}")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    ink, muted, green, blue, amber, red = (
        "#152d45",
        "#526579",
        "#087f6a",
        "#2563a6",
        "#c68612",
        "#b23c3c",
    )
    fig = plt.figure(figsize=(15.4, 10.1), facecolor="#f6f8fb")
    fig.text(0.06, 0.945, "RTL AUDIT | OBSERVED EVIDENCE", fontsize=23, weight="bold", color=ink)
    fig.text(
        0.06,
        0.905,
        "2026-09-30  |  Real Verilator logs + frozen source hashes  |  Arithmetic seed: "
        f"{arithmetic['seed']}",
        fontsize=11.5,
        color=muted,
    )
    grid = fig.add_gridspec(
        2,
        2,
        left=0.12,
        right=0.94,
        bottom=0.18,
        top=0.84,
        height_ratios=[1.02, 1.12],
        hspace=0.62,
        wspace=0.29,
    )
    ax = fig.add_subplot(grid[0, 0], facecolor="white")
    values = [counts[key] for key in ("mac", "adc", "div")]
    ax.barh([2, 1, 0], values, color=[green, blue, "#687aa4"], height=0.55)
    ax.set_yticks([2, 1, 0], ["Residue MAC", "ADC2 decode", "Floor divider"])
    ax.set_xlim(0, max(values) * 1.21)
    for y, value in zip([2, 1, 0], values, strict=True):
        ax.text(value + 650, y, f"{value:,}", va="center", color=ink, weight="bold")
    ax.set_xticks([0, 20000, 40000], ["0", "20,000", "40,000"])
    ax.set_xlabel("Oracle-checked input vectors", color=muted)
    ax.set_title(
        f"A   {sum(values):,} arithmetic vectors: PASS",
        loc="left",
        pad=16,
        fontsize=14,
        weight="bold",
        color=ink,
    )
    ax.text(
        0,
        -0.27,
        f"+ {counts['flag_sequences']} directed output-flag sequences: PASS",
        transform=ax.transAxes,
        color=green,
        fontsize=11.5,
    )
    ax.grid(axis="x", alpha=0.13)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax = fig.add_subplot(grid[0, 1], facecolor="white")
    ax.set_title(
        f"B   {frames} directed protocol frames: PASS",
        loc="left",
        pad=16,
        fontsize=14,
        weight="bold",
        color=ink,
    )
    ax.barh([0], [launches], height=0.45, color=green)
    ax.barh([0], [drops], left=[launches], height=0.45, color=amber)
    ax.text(
        launches / 2,
        0,
        f"{launches}\nValid launches",
        ha="center",
        va="center",
        weight="bold",
        color="white",
        fontsize=12,
    )
    ax.text(
        launches + drops / 2,
        0,
        f"{drops}\nExpected drops",
        ha="center",
        va="center",
        weight="bold",
        color="white",
        fontsize=12,
    )
    ax.set_xlim(0, frames)
    ax.set_ylim(-0.7, 0.7)
    ax.set_yticks([])
    ax.set_xticks([0, 30, 60, 90, 120])
    ax.set_xlabel("Frames; expected drops are required rejection behavior", color=muted)
    ax.text(
        0,
        -0.27,
        f"{checks:,} checks  |  {perturbations} phase-decoder perturbations",
        transform=ax.transAxes,
        color=blue,
        fontsize=11.5,
    )
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    ax = fig.add_subplot(grid[1, :])
    ax.axis("off")
    ax.text(
        0,
        1.05,
        "C   Negative controls demonstrate that the protocol assertion detects the injected defect",
        fontsize=13.5,
        weight="bold",
        color=ink,
        transform=ax.transAxes,
    )
    cards = [
        (
            "OLD BASELINE",
            "FAIL (expected)",
            "Source decoder restored",
            f"Assertion at {negatives['baseline']['failure_time_ns']} ns  |  Exit 1",
            red,
        ),
        (
            "CANCEL MUTANT",
            "FAIL (expected)",
            "Only combinational cancel restored",
            f"Assertion at {negatives['cancel_mutant']['failure_time_ns']} ns  |  Exit 1",
            red,
        ),
        (
            "FIXED RTL",
            "PASS",
            "Registered control implementation",
            f"{frames} frames complete  |  Exit 0",
            green,
        ),
    ]
    for index, (name, verdict, description, outcome, color) in enumerate(cards):
        x = 0.009 + index * 0.331
        ax.add_patch(
            FancyBboxPatch(
                (x, 0.09),
                0.312,
                0.73,
                boxstyle="round,pad=0.009,rounding_size=0.02",
                facecolor="white",
                edgecolor="#d6e0e9",
                transform=ax.transAxes,
            )
        )
        ax.text(
            x + 0.018, 0.68, name, fontsize=12, weight="bold", color=ink, transform=ax.transAxes
        )
        ax.text(
            x + 0.018,
            0.48,
            verdict,
            fontsize=17,
            weight="bold",
            color=color,
            transform=ax.transAxes,
        )
        ax.text(x + 0.018, 0.31, description, fontsize=10.5, color=muted, transform=ax.transAxes)
        ax.text(x + 0.018, 0.17, outcome, fontsize=10.5, color=ink, transform=ax.transAxes)
    ax.text(
        0,
        -0.07,
        "Failure observed in both controls: analog enable changed between clock edges.",
        fontsize=11,
        color=muted,
        transform=ax.transAxes,
    )

    fig.text(
        0.06,
        0.09,
        "Scope: block arithmetic + directed digital protocol; source-level fault injection.",
        fontsize=12,
        weight="bold",
        color=ink,
    )
    fig.text(
        0.06,
        0.059,
        "Counts do not prove complete functional coverage. No analog waveform, gate-delay measurement, STA or formal proof.",
        fontsize=10.7,
        color=muted,
    )
    fig.text(
        0.06,
        0.032,
        "Source manifests and raw-log SHA-256 digests: audit_figures/30_source_hashes.json",
        fontsize=10,
        color=muted,
    )
    image = report / "figures/30_arithmetic_protocol_audit.png"
    image.parent.mkdir(exist_ok=True)
    fig.savefig(image, dpi=190, facecolor=fig.get_facecolor())
    plt.close(fig)
    provenance = {
        "scope": "Data-derived count and result visualization, not invented waveforms or completeness proof.",
        "inputs": {key: {"path": str(path), "sha256": digest(path)} for key, path in paths.items()},
        "script": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__))},
        "parsed_counts": {
            **counts,
            "arithmetic_total": sum(values),
            "protocol_frames": frames,
            "protocol_launches": launches,
            "protocol_expected_drops": drops,
            "protocol_checks": checks,
            "protocol_perturbations": perturbations,
        },
        "protocol_source_hashes": {
            key: negatives[key]
            for key in ("baseline_source_sha256", "fixed_source_sha256", "bench_sha256")
        },
        "output": {"path": str(image), "sha256": digest(image)},
    }
    (report / "audit_figures/30_source_hashes.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    print(image)


if __name__ == "__main__":
    main()
