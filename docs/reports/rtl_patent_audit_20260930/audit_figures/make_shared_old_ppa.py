"""Plot source-bound shared-selector area and actual routed timing."""

from __future__ import annotations

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


def verified(path: Path) -> int:
    """Reject any mutated evidence before rendering."""
    hashes = json.loads((path / "sha256.json").read_text())
    for rel, expected in hashes.items():
        if digest(path / rel) != expected:
            raise ValueError(f"Evidence mismatch: {path / rel}")
    return len(hashes)


def main() -> None:
    """Compare the same P5/25 ns source packages and completed routes."""
    repo = Path(__file__).resolve().parents[4]
    base = repo / "docs/evidence/20260930"
    paths = {
        "baseline_route": base / "vivado_cached_p5_route_25ns_fail",
        "shared_synth": base / "shared_old_trial_synth",
        "shared_route": base / "shared_old_trial_route_25ns",
        "shared_mapped": base / "shared_old_trial_full_mapped",
    }
    counts = {key: verified(path) for key, path in paths.items()}
    comparison = json.loads((paths["shared_synth"] / "comparison_vs_cached.json").read_text())
    result = json.loads((paths["shared_route"] / "reviewed_result.json").read_text())
    mapped = json.loads((paths["shared_mapped"] / "summary.json").read_text())
    baseline = json.loads((paths["baseline_route"] / "reviewed_result.json").read_text())
    assert comparison["changed_source_only"] == ["rtl/core/weight_store.sv"]
    assert comparison["period_ns"] == result["requested_period_ns"] == 25.0
    assert comparison["dcp_sha256"]["shared_old"] == result["input_dcp_sha256"]
    assert mapped["dcp_sha256"] == result["input_dcp_sha256"]
    assert baseline["input_dcp_sha256"] == comparison["dcp_sha256"]["cached"]
    assert result["core_timing_met"] and not result["all_path_timing_met"]
    assert result["routed_complete"] and result["route_errors"] == result["drc_errors"] == 0
    assert mapped["trace_equals_baseline"] and mapped["final_audit"]["rows"] == 438
    delays = []
    for path in (paths["baseline_route"], paths["shared_route"]):
        match = re.search(
            r"Data Path Delay:\s+([\d.]+)ns\s+\(logic ([\d.]+)ns.*?route ([\d.]+)ns",
            (path / "out/internal_max_paths.rpt").read_text(),
        )
        assert match is not None
        delays.append(tuple(float(value) for value in match.groups()))
    assert delays == [(25.231, 1.603, 23.628), (23.914, 5.291, 18.623)]

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.2})
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.6))
    labels = ["Baseline", "Shared selector"]
    colors = ["#a25439", "#247e79"]
    lut = comparison["metrics_post_synth"]["lut"]
    bars = axes[0].bar(labels, [lut["cached"], lut["shared_old"]], color=colors, width=0.55)
    axes[0].set_ylim(0, 132000)
    axes[0].set_title("A  Post-synthesis LUT")
    for bar, value in zip(bars, [lut["cached"], lut["shared_old"]], strict=True):
        axes[0].text(bar.get_x() + bar.get_width() / 2, value + 1800, f"{value:,}", ha="center")
    axes[0].text(0.5, 0.96, "+2,637 LUT / +2.48%", transform=axes[0].transAxes, ha="center")
    wns = list(comparison["route_setup_wns_ns"].values())
    axes[1].bar(labels, wns, color=colors, width=0.55)
    axes[1].axhline(0, color="#555555", lw=0.8)
    axes[1].set_ylim(-0.65, 1.5)
    axes[1].set_title("B  Routed register setup WNS")
    axes[1].set_ylabel("Slack (ns)")
    for i, value in enumerate(wns):
        axes[1].text(i, value + (0.07 if value >= 0 else -0.17), f"{value:+.3f} ns", ha="center")
    axes[1].text(0.5, 0.96, "Shared hold = +0.052 ns", transform=axes[1].transAxes, ha="center")
    logic = [entry[1] for entry in delays]
    route = [entry[2] for entry in delays]
    axes[2].bar(labels, logic, color="#7394ae", label="Logic", width=0.55)
    axes[2].bar(labels, route, bottom=logic, color="#ccab58", label="Route", width=0.55)
    axes[2].set_ylim(0, 31)
    axes[2].set_ylabel("Worst data path (ns)")
    axes[2].set_title("C  The critical path moved")
    for i, entry in enumerate(delays):
        axes[2].text(i, entry[0] + 0.5, f"{entry[0]:.3f}", ha="center")
        axes[2].text(i, logic[i] + route[i] / 2, f"Route {100*route[i]/entry[0]:.1f}%", ha="center")
    axes[2].legend(loc="upper right", fontsize=8, frameon=False)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.text(
        0.5,
        0.008,
        "Vivado 2018.3 / Virtex-7 / P5 / 25 ns. Core timing passed; external OOC hold remains -2.278 ns. No board or ASIC signoff.",
        ha="center",
        fontsize=8,
    )
    fig.tight_layout(rect=(0, 0.065, 1, 1))
    out = Path(__file__).resolve().parent.parent / "figures/shared_old_ppa"
    fig.savefig(out.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None})
    fig.savefig(out.with_suffix(".png"), dpi=190)
    plt.close(fig)
    record = {
        "scope": "SOURCE_BOUND_P5_CORE_ROUTED_TIMING_NOT_BOARD_OR_ASIC",
        "archives": {key: value.relative_to(repo).as_posix() for key, value in paths.items()},
        "verified_payloads": counts,
        "comparison_sha256": digest(paths["shared_synth"] / "comparison_vs_cached.json"),
        "routed_result_sha256": digest(paths["shared_route"] / "reviewed_result.json"),
        "critical_data_logic_route_ns": delays,
        "generator_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(out.with_suffix(".pdf")),
        "png_sha256": digest(out.with_suffix(".png")),
    }
    (Path(__file__).parent / "shared_old_ppa.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
