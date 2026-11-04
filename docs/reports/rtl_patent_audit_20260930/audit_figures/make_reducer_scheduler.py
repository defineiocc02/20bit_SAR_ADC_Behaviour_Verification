#!/usr/bin/env python3
"""Plot real reducer log values and the shared-input negative control."""

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

FIELDS = ("T", "G", "R")
TRACE = re.compile(
    r"SCALAR_INPUT_CHECK check=(\d+) weight00=([0-9a-f]+) "
    r"T=([0-9a-f]+) G=([0-9a-f]+) R=([0-9a-f]+) "
    r"expectedT=([0-9a-f]+) expectedG=([0-9a-f]+) expectedR=([0-9a-f]+)"
)


def sha256(path: Path) -> str:
    """Return the file digest used to bind the figure to its inputs."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decode(value: str, width: int, *, signed: bool = False) -> int:
    """Decode valid SV bits only; ignore padding in the simulator's storage words."""
    number = int(value, 16) & ((1 << width) - 1)
    if signed and number & (1 << (width - 1)):
        number -= 1 << width
    return number


def read_trace(path: Path) -> list[dict]:
    """Read the printed raw-input, DUT and scalar values without interpolation."""
    rows = []
    for match in TRACE.finditer(path.read_text(encoding="utf-8")):
        check, weight, *values = match.groups()
        actual = [decode(values[i], 66 if i == 2 else 64, signed=i == 2) for i in range(3)]
        expected = [decode(values[i + 3], 66 if i == 2 else 64, signed=i == 2) for i in range(3)]
        rows.append(
            {"check": int(check), "weight00": int(weight, 16), "actual": actual, "oracle": expected}
        )
    return rows


def main() -> None:
    """Validate archived evidence and draw the numerical comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    report = args.report.resolve()
    index = json.loads((evidence / "sha256.json").read_text())
    for name, digest in index.items():
        if sha256(evidence / name) != digest:
            raise ValueError(f"Archived input hash mismatch: {name}")
    metadata = json.loads((evidence / "manifest.json").read_text())
    paths = {
        "5.020": evidence / "after_5020.run.log",
        "5.49": evidence / "after_549.run.log",
        "mutation": evidence / "common_stale_mutation_5020.run.log",
        "manifest": evidence / "manifest.json",
        "hash_index": evidence / "sha256.json",
        "mutation_fixture": evidence / "fixtures/common_stale_mutation.sv",
    }
    traces = {version: read_trace(paths[version]) for version in ("5.020", "5.49")}
    for version, rows in traces.items():
        if [row["check"] for row in rows] != list(range(1, 17)):
            raise ValueError(f"{version}: expected exactly checks 1 through 16")
        if any(row["actual"] != row["oracle"] for row in rows):
            raise ValueError(f"{version}: printed DUT/scalar mismatch")
        content = paths[version].read_text()
        if content.count("CAL_WEIGHT_REDUCE_PPA_COMPLETE") != 1:
            raise ValueError(f"{version}: missing or duplicated completion marker")
        case_rows = re.findall(
            r"REDUCE_MITER_CASE_PASS ns=(\d+) active=(\d+) units=(\d+) dither=(\d+) end=(\d+) checks=(\d+) allocations=(\d+)",
            content,
        )
        expected_rows = {
            tuple(
                row[name]
                for name in ("slices", "active", "units", "dither", "end", "checks", "allocations")
            )
            for row in metadata["per_geometry"]
        }
        if len(case_rows) != 7 or {tuple(map(int, row)) for row in case_rows} != expected_rows:
            raise ValueError(f"{version}: full geometry counts differ from the frozen manifest")
        if metadata["runs"][f"after_{version.replace('.', '')}"]["exit_code"] != 0:
            raise ValueError(f"{version}: successful exit was not recorded")
        if metadata["runs"][f"after_{version.replace('.', '')}"]["checked_cases"] != 1430848:
            raise ValueError(f"{version}: unexpected full-run count")
    if traces["5.020"] != traces["5.49"]:
        raise ValueError("The two versions do not report identical first-16 values")
    mutation_rows = read_trace(paths["mutation"])
    if [row["check"] for row in mutation_rows] != list(range(1, 10)):
        raise ValueError("Expected mutation failure at check 9")
    if metadata["runs"]["common_stale_mutation_5020"]["exit_code"] != 134:
        raise ValueError("Expected mutation exit 134 was not recorded")
    failure = mutation_rows[-1]
    if failure["actual"] != [0, 0, 0] or failure["oracle"] != [24, 24, 24]:
        raise ValueError("The shared-input mutation values differ from the recorded failure")
    if "reference scalar mismatch NS=32 NA=8 NM=2 check=9" not in paths["mutation"].read_text():
        raise ValueError("Missing expected negative-control failure")
    fixture = paths["mutation_fixture"].read_text()
    if "sampled_weights<='0;" not in fixture or fixture.count(".w_rom(sampled_weights)") != 2:
        raise ValueError("The negative control no longer forces both sampled weight inputs to zero")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    blue, green, ink, muted, red = "#216495", "#16816c", "#1a2e3d", "#546472", "#b64731"
    figure, axes = plt.subplots(2, 2, figsize=(14, 9))
    figure.subplots_adjust(
        left=0.075, right=0.97, top=0.775, bottom=0.185, hspace=0.44, wspace=0.25
    )
    figure.suptitle(
        "Reducer scheduling: direct numerical evidence",
        x=0.075,
        ha="left",
        y=0.972,
        fontsize=22,
        weight="bold",
        color=ink,
    )
    figure.text(
        0.075,
        0.917,
        "Full runs: 1,430,848 checked samples per version; 7 geometries; 43,758 production allocations.",
        color=ink,
        fontsize=12,
    )
    figure.text(
        0.075,
        0.881,
        "First 16 logged cases below: 32 slices / 8 active / 3 cells per slice. The two DUT traces and oracle coincide.",
        color=muted,
        fontsize=11,
    )
    checks = np.array([row["check"] for row in traces["5.020"]])
    titles = ("T: total weight sum", "G: sampled weight sum", "R: signed rail correction")
    for index_field, axis in enumerate(axes.flat[:3]):
        actual = [row["actual"][index_field] for row in traces["5.020"]]
        expected = [row["oracle"][index_field] for row in traces["5.020"]]
        axis.axvspan(8.5, 16.5, color="#edf5f8", zorder=0)
        axis.plot(
            checks,
            expected,
            "--o",
            color=ink,
            markerfacecolor="white",
            markersize=8,
            linewidth=1.3,
            label="Scalar oracle",
            zorder=2,
        )
        axis.plot(checks, actual, "o", color=blue, markersize=4, label="DUT: 5.020", zorder=3)
        axis.plot(
            checks,
            [row["actual"][index_field] for row in traces["5.49"]],
            "+",
            color=green,
            markersize=9,
            markeredgewidth=1.4,
            label="DUT: 5.49",
            zorder=4,
        )
        axis.set_title(titles[index_field], loc="left", weight="bold", color=ink, pad=11)
        axis.set_xlim(0.6, 16.5)
        axis.set_xticks((1, 4, 8, 9, 12, 16))
        axis.set_xlabel("Logged check index")
        axis.set_ylabel("Raw integer value")
        axis.grid(axis="y", color="#d9e1e7", linewidth=0.7)
        axis.set_axisbelow(True)
        axis.set_ylim((-38, 38) if index_field == 2 else (-4, 36))
        axis.set_yticks((-32, -16, 0, 16, 32) if index_field == 2 else (0, 8, 16, 24, 32))
        axis.text(0.035, 0.91, "weights = 0", transform=axis.transAxes, color=muted, fontsize=10)
        if index_field != 2:
            axis.text(0.60, 0.91, "weights = 1", transform=axis.transAxes, color=muted, fontsize=10)
        axis.text(
            0.035 if index_field == 2 else 0.985,
            0.06,
            "max |DUT - oracle| = 0",
            ha="left" if index_field == 2 else "right",
            transform=axis.transAxes,
            color=green,
            fontsize=10,
        )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="upper left",
        bbox_to_anchor=(0.066, 0.853),
        ncol=3,
        frameon=False,
        fontsize=10.5,
    )

    axis = axes[1, 1]
    positions = np.arange(3)
    axis.bar(
        positions + 0.16,
        failure["oracle"],
        width=0.30,
        color="#dae9e4",
        edgecolor=green,
        linewidth=1.3,
        label="Raw-input oracle",
    )
    axis.plot(
        positions - 0.16,
        failure["actual"],
        "x",
        color=red,
        markersize=9,
        markeredgewidth=2,
        label="Observed DUT",
    )
    for position in positions:
        axis.text(position - 0.16, 1.3, "0", ha="center", color=red, weight="bold")
        axis.text(position + 0.16, 25.0, "24", ha="center", color=green, weight="bold")
    axis.set_title(
        "Shared-input negative control: check 9", loc="left", weight="bold", color=ink, pad=11
    )
    axis.set_xticks(positions, FIELDS)
    axis.set_ylabel("Raw integer value")
    axis.set_ylim(-4, 36)
    axis.set_yticks((0, 8, 16, 24, 32))
    axis.grid(axis="y", color="#d9e1e7", linewidth=0.7)
    axis.set_axisbelow(True)
    axis.text(
        0.98,
        0.94,
        "Expected assertion failure; exit 134",
        ha="right",
        transform=axis.transAxes,
        color=red,
        fontsize=10,
    )
    axis.legend(loc="upper left", bbox_to_anchor=(-0.01, -0.20), frameon=False, ncol=2, fontsize=10)
    figure.text(
        0.075,
        0.071,
        "The mutation forces the shared weight snapshot to zero; the oracle still reads the original stimulus.",
        color=ink,
        fontsize=11,
    )
    figure.text(
        0.075,
        0.038,
        "Source: archived run logs, not an analog waveform or STA result. R is decoded as signed 66-bit; C++ padding is discarded.",
        color=muted,
        fontsize=10,
    )
    output = report / "figures/39_reducer_scheduler.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=190, facecolor="white")
    vector_output = output.with_suffix(".pdf")
    figure.savefig(
        vector_output, facecolor="white", metadata={"CreationDate": None, "ModDate": None}
    )
    plt.close(figure)
    manifest = {
        "purpose": "First-16 logged numerical comparisons and one detected shared-input mutation; no inferred timing waveform or PPA claim.",
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()
        },
        "script_sha256": sha256(Path(__file__)),
        "sv_widths": {"T": 64, "G": 64, "R": 66},
        "R_decode": "Mask to 66 valid bits, then interpret two's complement; ignore simulator storage padding.",
        "per_version_full_count": 1430848,
        "plotted_positive_rows": traces,
        "plotted_mutation_row": failure,
        "outputs": {path.name: sha256(path) for path in (output, vector_output)},
    }
    (report / "audit_figures/39_source_hashes.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
