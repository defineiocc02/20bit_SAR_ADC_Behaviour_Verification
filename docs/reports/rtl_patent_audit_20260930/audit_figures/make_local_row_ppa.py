"""Plot the measured A/B cost and physical rejection of local-row feedback."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def digest(path: Path) -> str:
    """Hash exact evidence bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified_archive(path: Path) -> int:
    """Verify all raw payloads before using an archive."""
    hashes = json.loads((path / "sha256.json").read_text())
    for rel, expected in hashes.items():
        if digest(path / rel) != expected:
            raise ValueError(f"Evidence hash mismatch: {path / rel}")
    return len(hashes)


def main() -> None:
    """Render only values and outcomes present in the frozen Vivado records."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-route", type=Path, required=True)
    parser.add_argument("--synth", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    baseline_route = args.baseline_route.resolve()
    synth = args.synth.resolve()
    route = args.route.resolve()
    repo = Path(__file__).resolve().parents[4]
    counts = {
        "baseline_route": verified_archive(baseline_route),
        "synth": verified_archive(synth),
        "route": verified_archive(route),
    }

    baseline = json.loads((baseline_route / "reviewed_result.json").read_text())
    comparison = json.loads((synth / "comparison_vs_cached.json").read_text())
    result = json.loads((route / "reviewed_result.json").read_text())
    lut = comparison["metrics"]["lut"]
    assert comparison["period_ns"] == 25.0
    assert comparison["part"] == "xc7vx690tffg1761-2"
    assert baseline["input_dcp_sha256"] == comparison["dcp_sha256"]["cached"]
    assert baseline["requested_period_ns"] == comparison["period_ns"]
    assert baseline["status"] == "ROUTED_COMPLETE_TIMING_NOT_MET"
    assert baseline["all_path_setup_wns_ns"] == -0.28
    assert (lut["cached"], lut["local_row"], lut["delta"]) == (106205, 121772, 15567)
    assert result["input_dcp_sha256"] == comparison["dcp_sha256"]["local_row"]
    assert result["status"] == "ROUTE_FAILED_CONGESTION_LEVEL_7"
    assert result["routed_complete"] is False
    assert result["valid_post_route_wns"] is None
    assert result["valid_post_route_whs"] is None
    assert (route / "out/status.txt").read_text().strip().startswith("STATUS=FAILED")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5})
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 3.4), gridspec_kw={"width_ratios": [1.05, 1]})
    bars = axes[0].bar(
        ["Cached baseline", "Local-row trial"],
        [lut["cached"], lut["local_row"]],
        color=["#287c79", "#ba6045"],
        width=0.55,
    )
    axes[0].set_ylim(0, 143000)
    axes[0].set_ylabel("Post-synthesis Slice LUT")
    axes[0].set_title("A  Same P5 / 25 ns synthesis")
    for bar, value in zip(bars, (lut["cached"], lut["local_row"]), strict=True):
        axes[0].text(
            bar.get_x() + bar.get_width() / 2,
            value + 2500,
            f"{value:,}",
            ha="center",
            fontweight="bold",
        )
    axes[0].text(
        0.5,
        0.95,
        "+15,567 LUT  |  +14.66%",
        transform=axes[0].transAxes,
        ha="center",
        color="#8b3824",
        fontweight="bold",
    )
    axes[0].spines[["top", "right"]].set_visible(False)

    axes[1].axis("off")
    axes[1].set_title("B  Physical implementation gate")
    axes[1].text(
        0.04,
        0.75,
        "Baseline: route completed",
        transform=axes[1].transAxes,
        color="#287c79",
        fontsize=12,
        fontweight="bold",
    )
    axes[1].text(
        0.04,
        0.62,
        f"25 ns setup WNS = {baseline['all_path_setup_wns_ns']:+.3f} ns",
        transform=axes[1].transAxes,
    )
    axes[1].text(
        0.04,
        0.42,
        "Local-row: route FAILED",
        transform=axes[1].transAxes,
        color="#a53927",
        fontsize=12,
        fontweight="bold",
    )
    axes[1].text(0.04, 0.29, "Route 35-3  |  congestion level 7", transform=axes[1].transAxes)
    axes[1].text(
        0.04, 0.16, "No final WNS or hold result", transform=axes[1].transAxes, color="#a53927"
    )

    fig.text(
        0.5,
        0.01,
        "Same Vivado 2018.3 / Virtex-7 source package except weight_store.sv. "
        "The trial is rejected; intermediate WNS is excluded.",
        ha="center",
        fontsize=8.2,
        color="#5e6570",
    )
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        args.out.with_suffix(".pdf"),
        metadata={"CreationDate": None, "ModDate": None, "Creator": "make_local_row_ppa.py"},
    )
    fig.savefig(args.out.with_suffix(".png"), dpi=190)
    plt.close(fig)

    record = {
        "scope": "P5_SAME_CONSTRAINT_SYNTH_AND_FAILED_ROUTE_NOT_TIMING_SIGNOFF",
        "archives": {
            "baseline_route": baseline_route.relative_to(repo).as_posix(),
            "synth": synth.relative_to(repo).as_posix(),
            "route": route.relative_to(repo).as_posix(),
        },
        "verified_payloads": counts,
        "source_file_sha256": {
            "baseline_route_result": digest(baseline_route / "reviewed_result.json"),
            "comparison": digest(synth / "comparison_vs_cached.json"),
            "route_result": digest(route / "reviewed_result.json"),
        },
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
