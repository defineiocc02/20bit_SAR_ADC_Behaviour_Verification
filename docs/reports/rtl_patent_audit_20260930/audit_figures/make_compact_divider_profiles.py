#!/usr/bin/env python3
"""Read the actual compact-run schema; do not invent legacy streaming evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


def sha256(path: Path) -> str:
    """Digest exact evidence bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(evidence: Path, report: Path, entrypoint: Path) -> Path:
    """Verify the compact schema and render only its observed latency checks."""
    manifest_path = evidence / "manifest.json"
    identity_path = evidence / "rtl_compact_identity.json"
    seal_path = evidence / "evidence_hashes.json"
    manifest = json.loads(manifest_path.read_text())
    identity = json.loads(identity_path.read_text())
    seal = json.loads(seal_path.read_text())
    if (
        manifest.get("status") != "PASS"
        or not manifest.get("frozen_source_unchanged")
        or not manifest.get("compiled_source_unchanged")
        or seal.get("status") != "PASS"
    ):
        raise ValueError("Compact regression is incomplete or sources changed")
    aggregate = hashlib.sha256(
        "".join(
            f"{name} {identity['files'][name]}\n" for name in sorted(identity["files"])
        ).encode()
    ).hexdigest()
    if (
        len(identity["files"]) != 26
        or aggregate != identity["rtl_sha256"]
        or aggregate != manifest["frozen_rtl_sha256"]
        or aggregate != seal["rtl_sha256"]
    ):
        raise ValueError("Inconsistent 26-file compact RTL identity")
    if manifest.get("rtl_changes_from_previous_frozen") != ["rtl/core/weight_store.sv"]:
        raise ValueError("Unexpected compact RTL delta")
    for name, digest in identity["files"].items():
        if manifest["source_sha256"].get(name) != digest:
            raise ValueError(f"Compile-source identity mismatch: {name}")
    inputs = {
        "manifest": manifest_path,
        "identity": identity_path,
        "original_seal": seal_path,
    }
    archive_path = evidence / "archive_manifest.json"
    if archive_path.exists():
        archive = json.loads(archive_path.read_text())
        if archive.get("status") != "PASS" or archive.get("rtl_sha256") != aggregate:
            raise ValueError("Archive status/identity mismatch")
        for name, item in archive["byte_identical_copies"].items():
            if sha256(evidence / name) != item["sha256"]:
                raise ValueError(f"Archive bytes differ: {name}")
        inputs["archive_manifest"] = archive_path
    pattern = re.compile(
        r"^STRUCTURAL_ADC_COMPLETE modes=(\d+) outputs=(\d+) checks=(\d+) physical_slices=(\d+) recon_stages=(\d+) recon_latency=(\d+) latency_checks=(\d+)$",
        re.M,
    )
    profiles = {}
    for item in manifest["profiles"]:
        stage = item["stages"]
        if (
            stage in profiles
            or stage not in (5, 6, 7)
            or item["status"] != "PASS"
            or item["compile_returncode"] != 0
            or item["run_returncode"] != 0
        ):
            raise ValueError("Unsuccessful/duplicate compact profile")
        path = evidence / f"p{stage}/run.log"
        text = path.read_text()
        digest = sha256(path)
        if (
            digest != item["run_log_sha256"]
            or digest != seal["files_sha256"][f"p{stage}/run.log"]
        ):
            raise ValueError("Raw run log hash mismatch")
        matches = list(pattern.finditer(text))
        if len(matches) != 1 or "Verilog $finish" not in text or "%Error" in text:
            raise ValueError("Raw run did not finish normally")
        observed = dict(
            zip(
                (
                    "modes",
                    "outputs",
                    "checks",
                    "physical_slices",
                    "stages",
                    "recon_latency",
                    "latency_checks",
                ),
                map(int, matches[0].groups()),
            )
        )
        expected = {
            "modes": 3,
            "outputs": 438,
            "checks": 7680,
            "physical_slices": 18,
            "stages": stage,
            "recon_latency": (63 + stage - 1) // stage + 2,
            "latency_checks": 438,
        }
        if (
            observed != expected
            or any(item.get(k) != v for k, v in observed.items())
            or item["completion_lines"] != [matches[0].group(0)]
        ):
            raise ValueError("Manifest/raw observed counts or latency disagree")
        profiles[stage] = observed
        inputs[f"stage{stage}_run"] = path
    if set(profiles) != {5, 6, 7}:
        raise ValueError("Missing compact profile")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12})
    ink, muted, blue, green = "#152d45", "#526579", "#2563a6", "#087f6a"
    fig = plt.figure(figsize=(13.2, 7.55), facecolor="#f6f8fb")
    fig.text(
        0.06,
        0.937,
        "COMPACT RTL | MEASURED OUTPUT LATENCY",
        fontsize=21,
        weight="bold",
        color=ink,
    )
    fig.text(
        0.06,
        0.895,
        f"Frozen {aggregate[:16]}...  |  18 x 71 production top  |  Verilator 5.49  |  2026-09-30",
        fontsize=11.4,
        color=muted,
    )
    ax = fig.add_axes([0.145, 0.365, 0.79, 0.405], facecolor="white")
    for y, stage in zip((2, 1, 0), (5, 6, 7)):
        latency = profiles[stage]["recon_latency"]
        ax.barh(y, latency, height=0.43, color=blue)
        ax.barh(
            y, 16 - latency, left=latency, height=0.43, color="#d8eee8", edgecolor=green
        )
        ax.plot(latency, y, "o", color=ink, markersize=6, zorder=5)
        ax.text(
            latency / 2,
            y,
            f"{latency} measured clock intervals",
            color="white",
            ha="center",
            va="center",
            weight="bold",
            fontsize=11.6,
        )
        ax.text(
            latency,
            y + 0.33,
            f"output +{latency}",
            ha="center",
            color=ink,
            weight="bold",
            fontsize=11.1,
        )
        ax.text(
            (latency + 16) / 2,
            y,
            str(16 - latency),
            ha="center",
            va="center",
            color=green,
            weight="bold",
            fontsize=12,
        )
    ax.axvline(16, color=green, linestyle="--", linewidth=1.6)
    ax.text(
        16,
        2.62,
        "16-cycle design budget",
        ha="right",
        color=green,
        fontsize=11.3,
        weight="bold",
    )
    ax.set_yticks([2, 1, 0], ["P5", "P6", "P7"])
    ax.set_xticks(range(0, 17, 2))
    ax.set_xlim(0, 16.45)
    ax.set_ylim(-0.52, 2.94)
    ax.set_xlabel(
        "Clock intervals after the accepted reconstruction start (start = 0)",
        color=muted,
        labelpad=10,
    )
    ax.grid(axis="x", alpha=0.15)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.legend(
        handles=[
            Patch(color=blue, label="Observed start-to-output latency"),
            Patch(
                facecolor="#d8eee8",
                edgecolor=green,
                label="Unused 16-cycle design budget",
            ),
        ],
        loc="upper left",
        bbox_to_anchor=(0, 1.205),
        ncol=2,
        frameon=False,
        fontsize=11.2,
    )
    for x, stage in zip((0.10, 0.405, 0.71), (5, 6, 7)):
        p = profiles[stage]
        fig.text(
            x,
            0.245,
            f"P{stage}  |  438 / 438 checks: PASS",
            color=green,
            weight="bold",
            fontsize=12,
        )
        fig.text(
            x,
            0.205,
            f"3 modes | 18 slices | {p['checks']:,} ticks",
            color=muted,
            fontsize=11,
        )
        fig.text(x, 0.171, "Compile rc=0 | Simulation rc=0", color=muted, fontsize=10.8)
    fig.text(
        0.06,
        0.107,
        "Local zero-delay RTL observations. No FPGA/ASIC frequency, area or power conclusion.",
        fontsize=11,
        color=ink,
    )
    fig.text(
        0.06,
        0.071,
        "Busy-release and independent streaming benches were not repeated in this compact run.",
        fontsize=10.6,
        color=muted,
    )
    fig.text(
        0.06,
        0.035,
        "Provenance: compact_profiles manifest + raw logs + 26-file identity | audit_figures/31_source_hashes.json",
        fontsize=9.6,
        color=muted,
    )
    output = report / "figures/31_divider_profiles.png"
    fig.savefig(output, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)
    record = {
        "figure": str(output),
        "purpose": "Actual compact 3-profile top-level output latency; 16-cycle design budget is an annotated scheduling assumption, not measured busy release or PPA.",
        "schema": "compact_profiles/manifest.json",
        "frozen_rtl_sha256": aggregate,
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in inputs.items()
        },
        "entrypoint_sha256": sha256(entrypoint),
        "adapter_sha256": sha256(Path(__file__)),
        "observed_profiles": profiles,
        "design_budget_cycles": 16,
        "busy_release_measured_in_this_run": False,
        "independent_streaming_bench_repeated_in_this_run": False,
        "figure_sha256": sha256(output),
    }
    (report / "audit_figures/31_source_hashes.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(output)
    return output
