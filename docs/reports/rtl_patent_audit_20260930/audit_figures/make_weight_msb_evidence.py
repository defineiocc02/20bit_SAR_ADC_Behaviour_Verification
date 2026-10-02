#!/usr/bin/env python3
"""Plot archived weight-MSB proof, baseline FDRE witnesses, and measured test counts.

Rebuild with --evidence-root /path/to/repo/docs/evidence/20260930.
No simulation, synthesis, or inferred cycle waveform is produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle


def sha256(path: Path) -> str:
    """Return the SHA256 digest for an input or generated output."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_archive(folder: Path) -> None:
    """Validate the existing archive index without altering the evidence."""
    for relative, digest in json.loads((folder / "sha256.json").read_text()).items():
        if sha256(folder / relative) != digest:
            raise ValueError(f"Archive hash mismatch: {folder.name}/{relative}")


def proof_box(axis, x: float, y: float, width: float, height: float, text: str) -> None:
    """Draw a proof premise, not a measured waveform or synthesized cell."""
    axis.add_patch(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.015",
            facecolor="#edf3f7",
            edgecolor="#a5b7c4",
            linewidth=1,
        )
    )
    axis.text(
        x + width / 2, y + height / 2, text, ha="center", va="center", fontsize=11, color="#1a2e3d"
    )


def main() -> None:
    """Validate and render the three distinct evidence levels in one figure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    evidence = args.evidence_root.resolve()
    report = args.report.resolve()
    weight = evidence / "weight_msb"
    structure = evidence / "vivado_structure"
    verify_archive(weight)
    verify_archive(structure)
    paths = {
        "verification_manifest": weight / "verification_manifest.json",
        "equivalence_log": weight / "equiv.run.log",
        "baseline_witness": structure / "baseline_weight_msb_witness.json",
        "baseline_archive_manifest": structure / "archive_manifest.json",
        "before_source": weight / "weight_store.before.sv",
        "after_source": weight / "weight_store.after.sv",
        "testbench": weight / "weight_msb_equiv_tb.sv",
        "weight_hash_index": weight / "sha256.json",
        "structure_hash_index": structure / "sha256.json",
    }
    metadata = json.loads(paths["verification_manifest"].read_text())
    witness = json.loads(paths["baseline_witness"].read_text())
    if metadata["tests"] != "PASS" or not metadata["area_result"].startswith("UNMEASURED:"):
        raise ValueError("Expected functional PASS and explicitly unmeasured PPA result")
    for source, field in (
        ("before_source", "before_sha256"),
        ("after_source", "after_sha256"),
        ("testbench", "directed_tb_sha256"),
    ):
        if sha256(paths[source]) != metadata[field]:
            raise ValueError(f"Source hash mismatch: {source}")
    log = paths["equivalence_log"].read_text()
    if sha256(paths["equivalence_log"]) != metadata["sha256"]["equiv.run.log"]:
        raise ValueError("Equivalence log differs from the verification manifest")
    matches = re.findall(
        r"WEIGHT_MSB_CASE_PASS NS=(\d+) NU=(\d+) steps=(\d+) accepted=(\d+) rejected=(\d+)", log
    )
    rows = [
        dict(
            zip(("slices", "units", "steps", "accepted", "rejected"), map(int, values), strict=True)
        )
        for values in matches
    ]
    if len(rows) != 3 or rows != metadata["directed_cases"]:
        raise ValueError("Expected exactly the three logged geometry results")
    if log.count("WEIGHT_MSB_EQUIV_COMPLETE geometries=3") != 1 or "%Fatal" in log:
        raise ValueError("Missing successful full completion")
    totals = {key: sum(row[key] for row in rows) for key in ("steps", "accepted", "rejected")}
    if totals != {"steps": 3023, "accepted": 1517, "rejected": 1184}:
        raise ValueError("Unexpected logged totals")
    visits = sum(row["steps"] * row["slices"] * row["units"] for row in rows)
    if (
        visits != metadata["directed_word_comparisons_each_against_old_and_new"]
        or visits != 2411234
    ):
        raise ValueError("Word-visit count is inconsistent with geometry and step counts")
    if witness["weight_msb47_ff"] != 1278 or witness["weight_ff"] != 18 * 71 * 48:
        raise ValueError("Unexpected baseline weight-register counts")
    if not witness["examples"] or any(
        '][47]"' not in example["text"] or "cellref FDRE " not in example["text"]
        for example in witness["examples"]
    ):
        raise ValueError("Missing actual bit47 FDRE witnesses")
    source = paths["after_source"].read_text()
    if "wr_data & (W_MAX - W_BITS'(1))" not in source:
        raise ValueError("The archived source no longer contains the proved write mask")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    ink, muted, blue, green, amber = "#1a2e3d", "#546472", "#216495", "#16816c", "#ba793e"
    figure = plt.figure(figsize=(14, 9.8))
    grid = figure.add_gridspec(
        2,
        2,
        left=0.065,
        right=0.965,
        top=0.835,
        bottom=0.20,
        hspace=0.47,
        wspace=0.18,
        height_ratios=(1, 1.05),
    )
    figure.suptitle(
        "Weight-store MSB: proof, baseline witness, and verification",
        x=0.065,
        ha="left",
        y=0.969,
        color=ink,
        fontsize=21,
        weight="bold",
    )
    figure.text(
        0.065,
        0.92,
        "The accepted-write mask exposes an existing post-reset invariant; it preserves the 48-bit interface and accepted precision.",
        fontsize=11.5,
        color=muted,
    )

    proof = figure.add_subplot(grid[0, 0])
    proof.set_axis_off()
    proof.set_xlim(0, 1)
    proof.set_ylim(0, 1)
    proof.set_title("A  Mathematical invariant", loc="left", pad=15, weight="bold", color=ink)
    proof_box(proof, 0.025, 0.76, 0.565, 0.16, "Reset: stored word = 0")
    proof_box(
        proof,
        0.025,
        0.40,
        0.565,
        0.24,
        "Accepted write: $0 < W_{in} < 2^{47}$\nstore input & (W_MAX - 1)",
    )
    proof_box(
        proof,
        0.025,
        0.12,
        0.565,
        0.16,
        "Other paths, including clear_load:\nretain the stored word",
    )
    proof.add_patch(
        FancyBboxPatch(
            (0.755, 0.34),
            0.215,
            0.37,
            boxstyle="round,pad=0.02",
            facecolor="#deefe9",
            edgecolor=green,
            linewidth=1.5,
        )
    )
    proof.text(
        0.8625,
        0.525,
        "W[47] = 0\n\nafter reset",
        ha="center",
        va="center",
        weight="bold",
        color=green,
        fontsize=12,
    )
    for y in (0.84, 0.52, 0.20):
        proof.add_patch(
            FancyArrowPatch(
                (0.61, y),
                (0.738, 0.525),
                arrowstyle="-|>",
                mutation_scale=12,
                color=muted,
                linewidth=1.2,
            )
        )
    proof.text(
        0.01,
        0.018,
        "Low 47 bits are retained exactly; invalid-write rejection is unchanged.",
        color=muted,
        fontsize=10,
    )

    baseline = figure.add_subplot(grid[0, 1])
    baseline.set_axis_off()
    baseline.set_xlim(0, 1)
    baseline.set_ylim(0, 1)
    baseline.set_title(
        "B  Repaired-baseline netlist evidence", loc="left", pad=15, weight="bold", color=ink
    )
    baseline.text(
        0.01, 0.73, f"{witness['weight_msb47_ff']:,} FDRE", fontsize=28, weight="bold", color=green
    )
    baseline.text(0.01, 0.61, "Actual bit47 instances: w_q_reg[*][*][47]", color=ink, fontsize=11)
    total_ff = witness["weight_ff"]
    msb_ff = witness["weight_msb47_ff"]
    lower_fraction = (total_ff - msb_ff) / total_ff
    baseline.add_patch(
        Rectangle(
            (0.01, 0.405), 0.94 * lower_fraction, 0.105, facecolor="#cddae3", edgecolor="white"
        )
    )
    baseline.add_patch(
        Rectangle(
            (0.01 + 0.94 * lower_fraction, 0.405),
            0.94 * (1 - lower_fraction),
            0.105,
            facecolor=green,
            edgecolor=green,
        )
    )
    baseline.text(
        0.01, 0.31, f"{total_ff:,} weight-value FFs = 18 x 71 x 48", color=ink, fontsize=11
    )
    baseline.text(
        0.01,
        0.21,
        "Green segment: the 1 / 48 high-bit share of this baseline.",
        color=muted,
        fontsize=10,
    )
    example = witness["examples"][0]
    baseline.text(
        0.01,
        0.085,
        f"EDIF line {example['line']:,}: w_q_reg[0][0][47] -> FDRE",
        color=muted,
        fontsize=9.8,
    )
    baseline.text(
        0.01,
        0.015,
        "This is a pre-change count, not measured post-change removal.",
        color=amber,
        fontsize=10,
    )

    coverage = figure.add_subplot(grid[1, :])
    positions = np.arange(len(rows))[::-1]
    for offset, key, color, label in (
        (0.22, "steps", blue, "Checked steps"),
        (0, "accepted", green, "Accepted-write count"),
        (-0.22, "rejected", amber, "Rejected-write count"),
    ):
        values = [row[key] for row in rows]
        coverage.barh(positions + offset, values, height=0.19, color=color, label=label)
        for position, value in zip(positions + offset, values, strict=True):
            coverage.text(
                value + 18,
                position,
                f"{value:,}",
                va="center",
                color=color,
                fontsize=10.5,
                weight="bold",
            )
    coverage.set_yticks(positions, [f"{row['slices']} x {row['units']}" for row in rows])
    coverage.set_ylabel("Slices x cells")
    coverage.set_xlim(0, 2050)
    coverage.set_ylim(-0.55, 2.55)
    coverage.set_xlabel("Recorded counts (three distinct counters; bars are not stacked)")
    coverage.set_title(
        "C  Three geometries: old RTL and new RTL checked against an independent state model",
        loc="left",
        pad=36,
        weight="bold",
        color=ink,
        fontsize=12,
    )
    coverage.legend(
        loc="lower left", bbox_to_anchor=(-0.008, 1.01), ncol=3, frameon=False, fontsize=10
    )
    coverage.grid(axis="x", color="#d9e1e7", linewidth=0.7)
    coverage.set_axisbelow(True)
    figure.text(
        0.065,
        0.127,
        f"3,023 steps  |  {visits:,} word visits, each checked against both implementations  |  completion marker confirmed",
        color=ink,
        fontsize=11.5,
    )
    figure.text(
        0.065,
        0.083,
        "Actual FF/LUT/timing savings remain unmeasured in this archive and require synthesis of the changed source snapshot.",
        color=amber,
        fontsize=11.5,
        weight="bold",
    )
    figure.text(
        0.065,
        0.043,
        "Evidence levels are kept separate: mathematical invariant, baseline primitive instances, and finite functional regression. No cycle waveform is inferred.",
        color=muted,
        fontsize=9.8,
    )
    output = report / "figures/40_weight_msb_evidence.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=190, facecolor="white")
    vector = output.with_suffix(".pdf")
    figure.savefig(vector, facecolor="white", metadata={"CreationDate": None, "ModDate": None})
    plt.close(figure)
    manifest = {
        "purpose": "Separate the post-reset MSB invariant, observed baseline FFs, and logged regression counts; no measured PPA saving is claimed.",
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()
        },
        "script_sha256": sha256(Path(__file__)),
        "logged_geometry_rows": rows,
        "logged_totals": totals,
        "word_visits_derived_as_sum_steps_times_geometry": visits,
        "baseline_weight_ff": total_ff,
        "baseline_msb47_fdre": msb_ff,
        "baseline_high_bit_fraction": "1278 / 61344 = 1 / 48; not a post-change resource measurement",
        "ppa_status": metadata["area_result"],
        "outputs": {path.name: sha256(path) for path in (output, vector)},
        "rebuild_command": "python audit_figures/make_weight_msb_evidence.py --evidence-root /path/to/repo/docs/evidence/20260930",
    }
    (report / "audit_figures/40_source_hashes.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
