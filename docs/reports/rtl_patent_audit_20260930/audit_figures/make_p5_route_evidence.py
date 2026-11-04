"""Plot the actual cached-P5 routed failure and corrected register-only STA."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def digest(path: Path) -> str:
    """Hash exact archived bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root: Path, index: str = "sha256.json") -> int:
    """Reject any changed raw payload in an evidence directory."""
    entries = json.loads((root / index).read_text())
    for name, expected in entries.items():
        assert digest(root / name) == expected, name
    return len(entries)


def main() -> None:
    """Render source-bound setup, path-delay, and hold-scope evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--synth", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    synth = args.synth.resolve()
    route = args.route.resolve()
    synth_count = verify(synth)
    route_count = verify(route)
    follow = route / "regreg_followup"
    follow_count = verify(follow)
    synth_data = json.loads((synth / "readings.json").read_text())
    route_data = json.loads((route / "reviewed_result.json").read_text())
    route_status = dict(
        line.split("=", 1) for line in (route / "out/status.txt").read_text().splitlines()
    )
    regreg = dict(
        line.split("=", 1) for line in (follow / "regreg_summary.txt").read_text().splitlines()
    )
    assert synth_data["external_dcp"]["sha256"] == route_data["input_dcp_sha256"]
    assert route_status["INPUT_DCP_SHA256"] == route_data["input_dcp_sha256"]
    assert route_status["ROUTED_FULLY"] == "1"
    assert route_status["ROUTE_ERRORS"] == route_status["DRC_ERRORS"] == "0"
    assert route_status["CHECK_TIMING_CATEGORIES"] == "16"
    assert route_status["CHECK_TIMING_ISSUES"] == "0"
    assert route_status["TIMING_MET"] == "0"
    synth_wns = float(synth_data["timing"]["wns_ns"])
    route_wns = float(route_status["WNS_NS"])
    all_whs = float(route_status["WHS_NS"])
    regreg_whs = float(regreg["REGREG_HOLD_WORST_NS"])
    assert (synth_wns, route_wns, all_whs, regreg_whs) == (
        10.640,
        -0.280,
        -2.361,
        0.062,
    )
    max_path = (route / "out/timing_max_paths.rpt").read_text()
    match = re.search(
        r"Data Path Delay:\s+([\d.]+)ns\s+\(logic\s+([\d.]+)ns.*?route\s+([\d.]+)ns",
        max_path,
    )
    assert match
    total, logic, wires = (float(value) for value in match.groups())
    assert (total, logic, wires) == (25.231, 1.603, 23.628)
    min_path = (route / "out/timing_min_paths.rpt").read_text()
    assert "Source:                 cfg_wdata[17]" in min_path
    assert "Input Delay:            0.000ns" in min_path
    assert float(regreg["REGREG_SETUP_WORST_NS"]) == route_wns

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(1, 3, figsize=(12.7, 4.4))
    fig.patch.set_facecolor("white")
    blue, red, green = "#246190", "#bb503b", "#248576"

    ax = axes[0]
    ax.bar(["Post-synth", "Post-route"], [synth_wns, route_wns], color=[blue, red], width=0.55)
    ax.axhline(0, color="#3b3b3b", linewidth=0.9)
    ax.set_ylim(-2.2, 12.2)
    ax.set_ylabel("Setup WNS (ns)")
    ax.set_title("A  Same DCP, routed reality")
    for x, value in enumerate((synth_wns, route_wns)):
        ax.text(x, value + (0.3 if value >= 0 else -0.5), f"{value:+.3f}", ha="center")

    ax = axes[1]
    ax.barh([0], [logic], color=blue, label="Cell logic")
    ax.barh([0], [wires], left=[logic], color="#ba8626", label="Routed nets")
    ax.set_xlim(0, 29)
    ax.set_yticks([0], labels=["Worst setup path"])
    ax.set_xlabel("Data path delay (ns)")
    ax.set_title("B  Routing dominates")
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.text(
        0.5,
        0.79,
        f"{total:.3f} ns = {logic:.3f} + {wires:.3f}",
        transform=ax.transAxes,
        ha="center",
    )
    ax.text(
        0.5,
        0.66,
        f"{wires / total:.1%} routed interconnect",
        transform=ax.transAxes,
        ha="center",
        color="#1f1f1f",
    )

    ax = axes[2]
    ax.bar(
        ["All OOC paths", "Register → register"],
        [all_whs, regreg_whs],
        color=[red, green],
        width=0.58,
    )
    ax.axhline(0, color="#3b3b3b", linewidth=0.9)
    ax.set_ylim(-2.9, 0.9)
    ax.set_ylabel("Hold WHS (ns)")
    ax.set_title("C  Boundary versus core")
    for x, value in enumerate((all_whs, regreg_whs)):
        ax.text(x, value + (0.12 if value >= 0 else -0.2), f"{value:+.3f}", ha="center")
    ax.text(
        0.5,
        0.93,
        "Worst all-path hold: cfg_wdata, 0 ns input delay",
        transform=ax.transAxes,
        ha="center",
        fontsize=7.5,
    )

    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(
        "Cached P5 | Vivado 2018.3 | Virtex-7 routed OOC timing", fontsize=12, fontweight="bold"
    )
    fig.text(
        0.5,
        0.005,
        "Route/DRC errors = 0; 16 check_timing categories = 0. Setup fails at 25 ns; OOC ports use zero I/O delay. No board or ASIC signoff.",
        ha="center",
        fontsize=8.5,
        color="#6c3e20",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.91))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        args.out.with_suffix(".pdf"),
        metadata={"CreationDate": None, "ModDate": None, "Creator": "make_p5_route_evidence.py"},
    )
    fig.savefig(args.out.with_suffix(".png"), dpi=190)
    plt.close(fig)
    repo_root = Path(__file__).resolve().parents[4]
    record = {
        "scope": "FPGA_POST_ROUTE_OOC_TIMING_FAILURE_WITH_REGISTER_PIN_FOLLOWUP",
        "synth_archive": synth.relative_to(repo_root).as_posix(),
        "route_archive": route.relative_to(repo_root).as_posix(),
        "synth_payloads_verified": synth_count,
        "route_payloads_verified": route_count,
        "regreg_payloads_verified": follow_count,
        "route_reviewed_result_sha256": digest(route / "reviewed_result.json"),
        "input_dcp_sha256": route_data["input_dcp_sha256"],
        "post_route_dcp_sha256": route_data["post_route_dcp_sha256"],
        "generator_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(args.out.with_suffix(".pdf")),
        "png_sha256": digest(args.out.with_suffix(".png")),
    }
    (Path(__file__).resolve().parent / f"{args.out.stem}.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
