#!/usr/bin/env python3
"""Plot observed divider-profile cycles from final summaries and raw RTL logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


def sha256(path: Path) -> str:
    """Return the digest used to identify a profile evidence file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Cross-check the three profile logs and render measured clock intervals."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        default=report.parent
        / "sar_adc_review_20260919/calibration-closure/docs/evidence/20260930/compact_profiles",
    )
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    # Compact regression has its own manifest schema and did not repeat the
    # separate streaming bench. Dispatch to its reader; never fabricate the
    # legacy busy-release / streaming evidence files.
    if (evidence / "manifest.json").exists():
        candidate = json.loads((evidence / "manifest.json").read_text())
        if "frozen_rtl_sha256" in candidate:
            from make_compact_divider_profiles import render

            render(evidence, report, Path(__file__))
            return
    paths = {
        "summary": evidence / "structural_adc_ppa_profiles.final.summary.json",
        "sources": evidence / "structural_adc_ppa_profiles.final.sources.json",
        "stream": evidence / "recon_ppa_latency_tb.run.final.log",
    }
    summary = json.loads(paths["summary"].read_text())
    sources = json.loads(paths["sources"].read_text())
    stream_text = paths["stream"].read_text()
    stream_pattern = re.compile(
        r"RECON_PPA_PROFILE_PASS stages=(\d+) samples=(\d+) busy_release=(\d+) "
        r"latency=(\d+) initiation_interval=(\d+)"
    )
    stream = {
        int(match[0]): dict(
            zip(
                ("samples", "busy_release", "latency", "interval"),
                map(int, match[1:]),
                strict=True,
            )
        )
        for match in stream_pattern.findall(stream_text)
    }
    if set(stream) != {5, 6, 7} or "Verilog $finish" not in stream_text:
        raise ValueError("Incomplete three-profile streaming evidence")
    pattern = re.compile(
        r"STRUCTURAL_ADC_COMPLETE modes=(\d+) outputs=(\d+) checks=(\d+) "
        r"physical_slices=(\d+) recon_stages=(\d+) recon_latency=(\d+) latency_checks=(\d+)"
    )
    profiles = {}
    for item in summary:
        stage = item["stages"]
        if item["compile_returncode"] != 0 or item["run_returncode"] != 0:
            raise ValueError(f"Unsuccessful final profile: {stage}")
        path = evidence / f"structural_adc_tb.final.stages{stage}.run.log"
        paths[f"stage{stage}_run"] = path
        text = path.read_text()
        matches = pattern.findall(text)
        if len(matches) != 1 or "Verilog $finish" not in text:
            raise ValueError(f"Missing completion for profile {stage}")
        values = dict(
            zip(
                (
                    "modes",
                    "outputs",
                    "ticks",
                    "slices",
                    "stage",
                    "latency",
                    "latency_checks",
                ),
                map(int, matches[0]),
                strict=True,
            )
        )
        if item["completion_lines"] != [pattern.search(text).group(0)]:
            raise ValueError("Summary/raw-log mismatch")
        if values["stage"] != stage or values["latency"] != stream[stage]["latency"]:
            raise ValueError("Top-level/streaming latency mismatch")
        if values["outputs"] != values["latency_checks"]:
            raise ValueError("A top-level output lacks a latency check")
        values.update(stream[stage])
        if not (0 < values["busy_release"] < values["latency"] < values["interval"]):
            raise ValueError("Profile does not fit the 16-cycle budget")
        profiles[stage] = values
    if set(profiles) != {5, 6, 7}:
        raise ValueError("Final summary lacks a required profile")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    ink, muted, blue, amber, green = (
        "#152d45",
        "#526579",
        "#2563a6",
        "#c68612",
        "#087f6a",
    )
    fig = plt.figure(figsize=(15.4, 8.8), facecolor="#f6f8fb")
    fig.text(
        0.06,
        0.935,
        "DIVIDER PROFILES | OBSERVED CYCLE BUDGET",
        fontsize=23,
        weight="bold",
        color=ink,
    )
    fig.text(
        0.06,
        0.895,
        "Final RTL snapshot  |  Top-level result timestamps + independent streaming logs  |  2026-09-30",
        fontsize=11.5,
        color=muted,
    )
    ax = fig.add_axes([0.12, 0.36, 0.82, 0.43], facecolor="white")
    labels = []
    for y, stage in zip((2, 1, 0), (5, 6, 7), strict=True):
        p = profiles[stage]
        busy, latency, interval = p["busy_release"], p["latency"], p["interval"]
        ax.barh(y, busy, height=0.44, color=blue)
        ax.barh(y, latency - busy, left=busy, height=0.44, color=amber)
        ax.barh(
            y,
            interval - latency,
            left=latency,
            height=0.44,
            color="#d8eee8",
            edgecolor=green,
        )
        ax.text(
            busy / 2,
            y,
            f"busy releases at +{busy}",
            color="white",
            ha="center",
            va="center",
            weight="bold",
        )
        ax.plot(latency, y, "o", color=ink, markersize=7, zorder=5)
        ax.text(
            latency,
            y + 0.35,
            f"output at +{latency}",
            ha="center",
            color=ink,
            weight="bold",
            fontsize=11.5,
        )
        ax.text(
            (latency + interval) / 2,
            y,
            str(interval - latency),
            ha="center",
            va="center",
            weight="bold",
            color=green,
        )
        labels.append(f"P_STAGES = {stage}")
    ax.axvline(16, color=green, linestyle="--", linewidth=1.7)
    ax.text(
        16,
        2.67,
        "next request at +16",
        ha="right",
        color=green,
        fontsize=11.5,
        weight="bold",
    )
    ax.set_yticks([2, 1, 0], labels)
    ax.set_xticks(range(0, 17, 2))
    ax.set_xlim(0, 16.65)
    ax.set_ylim(-0.62, 2.9)
    ax.set_xlabel(
        "Complete clock intervals after the accepted request (acceptance = 0)",
        color=muted,
        labelpad=12,
    )
    ax.grid(axis="x", alpha=0.15)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(
        handles=[
            Patch(color=blue, label="busy asserted"),
            Patch(color=amber, label="output-commit interval"),
            Patch(
                facecolor="#d8eee8", edgecolor=green, label="margin before next request"
            ),
        ],
        loc="upper left",
        bbox_to_anchor=(0, 1.2),
        ncol=3,
        frameon=False,
        fontsize=11,
    )

    for x, stage in zip((0.12, 0.415, 0.71), (5, 6, 7), strict=True):
        p = profiles[stage]
        fig.text(
            x,
            0.245,
            f"P={stage}   {p['outputs']}/{p['latency_checks']} results + latency: PASS",
            color=green,
            weight="bold",
            fontsize=12,
        )
        fig.text(
            x,
            0.205,
            f"{p['modes']} modes  |  {p['slices']} slices  |  {p['ticks']:,} checked ticks",
            color=muted,
            fontsize=10.7,
        )
        fig.text(
            x,
            0.171,
            f"Streaming: {p['samples']}/{p['samples']} at interval {p['interval']}",
            color=muted,
            fontsize=10.7,
        )
    reducer = sources["sha256"]["rtl/core/cal_weight_reduce.sv"]
    fig.text(
        0.06,
        0.095,
        "Functional, zero-delay RTL evidence. This chart does not establish 640 MHz timing, area, or power gains.",
        fontsize=11,
        color=ink,
    )
    fig.text(
        0.06,
        0.057,
        f"Final reducer SHA-256: {reducer[:24]}...  |  Full input hashes: audit_figures/31_source_hashes.json",
        fontsize=9.5,
        color=muted,
    )
    output = report / "figures/31_divider_profiles.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, facecolor=fig.get_facecolor())
    plt.close(fig)
    manifest = {
        "figure": str(output),
        "purpose": "Observed latency/busy release and fixed initiation-interval budget; no PPA measurement.",
        "inputs": {
            key: {"path": str(path), "sha256": sha256(path)}
            for key, path in paths.items()
        },
        "script_sha256": sha256(Path(__file__)),
        "final_reducer_sha256": reducer,
        "observed_profiles": profiles,
        "figure_sha256": sha256(output),
    }
    (report / "audit_figures/31_source_hashes.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
