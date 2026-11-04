#!/usr/bin/env python3
"""Plot preserved local audit counts and actual mutant failures; never run EDA."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def sha(path: Path) -> str:
    """Hash an exact archived input or generated artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def only(text: str, pattern: str) -> tuple[str, ...]:
    """Require one unambiguous raw-log match."""
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one recorded match: {pattern}")
    return matches[0]


def main() -> None:
    """Validate the two-tool archive and plot observed positive and negative controls."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=report.parent
        / "sar_adc_review_20260919/calibration-closure/docs/evidence/20260930/arithmetic_runner_ci",
    )
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    index = json.loads((evidence / "sha256.json").read_text())
    for name, digest in index.items():
        path = evidence / name
        if not path.resolve().is_relative_to(evidence) or sha(path) != digest:
            raise ValueError(f"Archive hash differs: {name}")
    manifest = json.loads((evidence / "manifest.json").read_text())
    if manifest["production_rtl_changed"] or manifest["coverage_reduced"]:
        raise ValueError("The archive does not support unchanged production/coverage")
    versions, counts, source_maps, valid_mac = [], [], [], []
    for version in ("5020", "549"):
        result = manifest["accepted_simulations"][version]
        acceptance = json.loads((evidence / result["manifest"]).read_text())
        text = (evidence / result["run_log"]).read_text()
        row = tuple(
            map(
                int,
                only(
                    text,
                    r"ARITHMETIC_AUDIT_COMPLETE mac=(\d+) adc=(\d+) div=(\d+) flag_sequences=(\d+)",
                ),
            )
        )
        expected = tuple(result["counts"][key] for key in ("mac", "adc", "div", "flag_sequences"))
        if row != expected or result["returncode"] != 0 or acceptance["status"] != "PASS":
            raise ValueError("Acceptance count or completion status differs")
        if (
            acceptance["counts"] != result["counts"]
            or acceptance["vectors_sha256"] != manifest["vectors_unchanged_sha256"]
        ):
            raise ValueError("Acceptance manifest counts/vector hashes differ")
        for label, n in zip(("MAC", "ADC", "DIV"), row[:3], strict=True):
            if text.count(f"{label}_INDEPENDENT_PASS cases={n}") != 1:
                raise ValueError(f"Missing independent {label} marker")
        if "latency=9 busy_requests_ignored" not in text or "Verilog $finish" not in text:
            raise ValueError("Missing divider protocol or finish marker")
        valid_mac.append(acceptance["counts"]["mac_nonoverflow"])
        if version == "5020" and not acceptance["verilator_version"].startswith("Verilator 5.020"):
            raise ValueError("5.020 tool identity differs")
        if version == "549" and "Verilator 5.49" not in text:
            raise ValueError("5.49 runtime identity differs")
        counts.append(row)
        versions.append("Verilator 5.020" if version == "5020" else "Verilator 5.49 dev")
        source_maps.append(acceptance["sources_sha256"])
    if source_maps[0] != source_maps[1] or valid_mac[0] != valid_mac[1]:
        raise ValueError("The two local tool runs used different DUT/TB sources")
    mac = tuple(
        map(
            int,
            only(
                (evidence / "stale_mac_5020.run.log").read_text(),
                r"MAC mismatch line=(\d+) ov=(\d+) expected=(\d+)",
            ),
        )
    )
    adc_raw = only(
        (evidence / "stale_adc_5020.run.log").read_text(),
        r"ADC mismatch line=(\d+) ov=(\d+) fine=([0-9a-f]+) expected=([0-9a-f]+)",
    )

    def signed(value: str) -> int:
        integer = int(value, 16)
        return integer - (1 << 64) if integer & (1 << 63) else integer

    adc = (int(adc_raw[0]), int(adc_raw[1]), signed(adc_raw[2]), signed(adc_raw[3]))
    for key in ("stale_mac", "stale_adc"):
        result = manifest["negative_controls"][key]
        if result["runner_returncode"] != 1 or result["simulator_returncode"] != -6:
            raise ValueError("Mutant did not exit through the expected failure")
    if mac != (3, 1, 0) or adc != (2, 0, 1, -1):
        raise ValueError("Negative-control numerical witness differs")
    unit_text = (evidence / "runner_unit_tests.log").read_text()
    if not re.search(r"\b8 passed in [0-9.]+s", unit_text) or manifest["unit_tests"]["passed"] != 8:
        raise ValueError("Runtime-path unit-test evidence differs")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "pdf.fonttype": 42})
    ink, green, red, muted = "#16324a", "#087862", "#b7442e", "#526673"
    fig = plt.figure(figsize=(10.4, 10.3), facecolor="white")
    fig.text(
        0.06,
        0.956,
        "INDEPENDENT ARITHMETIC AUDIT",
        color=ink,
        fontsize=20,
        weight="bold",
    )
    fig.text(
        0.06,
        0.921,
        "Local two-tool acceptance + deliberately stale-input negative controls",
        color=muted,
        fontsize=11,
    )
    fig.text(
        0.06,
        0.865,
        "A  Accepted simulations: exact raw-log counts",
        color=ink,
        fontsize=13,
        weight="bold",
    )
    ax = fig.add_axes((0.06, 0.665, 0.88, 0.16))
    ax.axis("off")
    data = [[versions[i], *[f"{v:,}" for v in row]] for i, row in enumerate(counts)]
    table = ax.table(
        cellText=data,
        colLabels=[
            "Recorded tool",
            "MAC cases",
            "ADC2 cases",
            "Divider requests",
            "Flag sequences",
        ],
        cellLoc="center",
        colWidths=[0.28, 0.17, 0.17, 0.20, 0.18],
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2.4)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor("white")
        cell.set_facecolor("#e6edf2" if row == 0 else "#f1f7f4")
        cell.get_text().set_color(ink if row == 0 else green)
        if row == 0:
            cell.get_text().set_weight("bold")
    fig.text(
        0.07,
        0.65,
        f"MAC: {valid_mac[0]:,} non-overflow cases are a subset of {counts[0][0]:,}, not an extra count.\nDivider: 9-cycle completion and ignored busy requests are explicitly logged.\nBoth tools replay the same vector hashes; counts are not summed into coverage.",
        color=muted,
        fontsize=10,
        linespacing=1.6,
        va="top",
    )
    ax1 = fig.add_axes((0.10, 0.235, 0.34, 0.26))
    ax2 = fig.add_axes((0.59, 0.235, 0.34, 0.26))
    ax1.bar([0, 1], [mac[2], mac[1]], color=[green, red], width=0.56)
    ax1.set(
        xticks=[0, 1],
        xticklabels=["Independent\nexpected", "Stale-input\nmutant"],
        ylabel="MAC overflow flag",
        yticks=[0, 1],
        ylim=(-0.08, 1.4),
        title=f"B  MAC mutant: vector row {mac[0]}",
    )
    for i, v in enumerate([mac[2], mac[1]]):
        ax1.text(i, v + 0.055, str(v), ha="center", color=ink, weight="bold")
    ax2.bar([0, 1], [adc[3], adc[2]], color=[green, red], width=0.56)
    ax2.set(
        xticks=[0, 1],
        xticklabels=["Independent\nexpected", "Fixed-code\nmutant"],
        ylabel="ADC2 signed fine result",
        yticks=[-1, 0, 1],
        ylim=(-1.4, 1.4),
        title=f"C  ADC2 mutant: vector row {adc[0]}",
    )
    for i, v in enumerate([adc[3], adc[2]]):
        ax2.text(
            i,
            v + (0.07 if v > 0 else -0.07),
            str(v),
            ha="center",
            va="bottom" if v > 0 else "top",
            color=ink,
            weight="bold",
        )
    for ax in [ax1, ax2]:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.15)
        ax.set_axisbelow(True)
        ax.title.set_fontsize(11.3)
    fig.text(
        0.06,
        0.15,
        "Both intentional mutants fail: simulator return -6; audit runner return 1.\nThese are recorded assertion failures, not successful output samples.",
        color=red,
        fontsize=10.4,
        linespacing=1.55,
    )
    fig.text(
        0.06,
        0.097,
        "8 runtime-path/environment unit tests also pass (process fixtures, not RTL simulation).",
        color=ink,
        fontsize=10.2,
    )
    fig.text(
        0.06,
        0.062,
        "Source: docs/evidence/20260930/arithmetic_runner_ci/  |  No reconstructed analog waveform.",
        color=muted,
        fontsize=9,
    )
    fig.text(
        0.06,
        0.038,
        "Finite integer/protocol checks; no full-chip mapped, timing, SNDR or silicon claim.",
        color=muted,
        fontsize=9,
    )
    output = report / "figures"
    png, pdf = (
        output / "arithmetic_runner_dual_tool.png",
        output / "arithmetic_runner_dual_tool.pdf",
    )
    fig.savefig(png, dpi=220, facecolor="white")
    fig.savefig(pdf, facecolor="white", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    record = {
        "scope": "LOCAL_TWO_TOOL_ARITHMETIC_LOG_EVIDENCE_NOT_CI_OR_EDA",
        "script_sha256": sha(Path(__file__)),
        "inputs_sha256": {str(evidence / name): digest for name, digest in index.items()},
        "archive_hash_index_sha256": sha(evidence / "sha256.json"),
        "images_sha256": {str(q): sha(q) for q in [png, pdf]},
        "plotted_data": {
            "versions": versions,
            "positive_counts": counts,
            "mac_nonoverflow_subset": valid_mac[0],
            "mac_mutant_row_actual_expected": mac,
            "adc_mutant_row_overflow_actual_expected": adc,
            "unit_tests": 8,
        },
        "matched_two_tool_source_hashes": source_maps[0],
    }
    (report / "audit_figures/arithmetic_runner_dual_tool.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(png)


if __name__ == "__main__":
    main()
