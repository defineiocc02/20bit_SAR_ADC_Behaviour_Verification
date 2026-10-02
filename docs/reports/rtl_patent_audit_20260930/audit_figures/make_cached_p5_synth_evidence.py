"""Plot sealed, post-synthesis P5 readings without claiming routed timing."""

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
    """Return the exact file-byte SHA-256 digest."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Verify a sealed archive and render the constrained synthesis scope."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    archive = args.archive.resolve()
    hashes = json.loads((archive / "sha256.json").read_text())
    for name, expected in hashes.items():
        assert digest(archive / name) == expected, name
    source = archive / "readings.json"
    data = json.loads(source.read_text())
    assert data["status"] == "SYNTH_COMPLETE_TIMING_MET"
    assert data["scope"] == "VALID_POST_SYNTH_OOC_REPORTS_NOT_ROUTE_NOT_FULL_MAPPED_NOT_ASIC"
    assert data["stages"] == 5 and data["timing"]["requested_period_ns"] == 25
    assert data["resources"]["latch"] == 0
    report = (archive / "out/utilization.rpt").read_text()

    def capacity(label: str) -> int:
        match = re.search(
            r"^\|\s*" + re.escape(label) + r"\s*\|\s*[0-9]+\s*\|\s*[0-9]+\s*\|\s*([0-9]+)\s*\|",
            report,
            re.M,
        )
        assert match, label
        return int(match.group(1))

    used = [data["resources"][key] for key in ("lut", "ff", "dsp", "bram_tiles")]
    available = [
        capacity(x) for x in ("Slice LUTs*", "Register as Flip Flop", "DSPs", "Block RAM Tile")
    ]
    pct = [100 * a / b for a, b in zip(used, available, strict=True)]
    timing = data["timing"]
    slack = [timing[x] for x in ("wns_ns", "whs_ns", "wpws_ns")]
    path = data["worst_setup_path"]
    assert (
        abs(path["logic_delay_ns"] + path["estimated_route_delay_ns"] - path["data_delay_ns"])
        < 0.002
    )

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(
        1, 3, figsize=(13.3, 4.6), gridspec_kw={"width_ratios": [1.12, 1.03, 1.22]}
    )
    fig.patch.set_facecolor("white")
    palette = ["#215b93", "#218a7a", "#b68424", "#777777"]

    ax = axes[0]
    names = ["Slice LUT", "FF", "DSP48E1", "BRAM"]
    bars = ax.barh(names[::-1], pct[::-1], color=palette[::-1], height=0.58)
    ax.set_xlim(0, 29)
    ax.set_xlabel("Device capacity used (%)")
    ax.set_title("A  Resource occupancy")
    ax.spines[["top", "right"]].set_visible(False)
    for bar, value, count in zip(bars, pct[::-1], used[::-1], strict=True):
        ax.text(
            max(bar.get_width(), 0.1) + 0.4,
            bar.get_y() + bar.get_height() / 2,
            f"{value:.2f}% ({count:,})",
            va="center",
            fontsize=9,
        )

    ax = axes[1]
    colors = ["#218a7a" if x >= 0 else "#c44937" for x in slack]
    bars = ax.bar(["Setup", "Hold", "Pulse"], slack, color=colors, width=0.55)
    ax.axhline(0, color="#333333", lw=0.9)
    ax.set_ylim(-2.7, 14.6)
    ax.set_ylabel("Slack (ns)")
    ax.set_title("B  Post-synthesis timing")
    ax.spines[["top", "right"]].set_visible(False)
    for bar, value in zip(bars, slack, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + (0.34 if value >= 0 else -0.6),
            f"{value:+.3f}",
            ha="center",
            va="bottom" if value >= 0 else "top",
            fontsize=9,
        )
    ax.text(
        0.02,
        0.04,
        "Hold: 60,455 failing endpoints",
        transform=ax.transAxes,
        color="#a53c2c",
        fontsize=8.5,
    )

    ax = axes[2]
    logic = path["logic_delay_ns"]
    estimated_route = path["estimated_route_delay_ns"]
    ax.barh(["Divider worst\nsetup path"], [logic], color="#215b93", height=0.38, label="Logic")
    ax.barh(
        ["Divider worst\nsetup path"],
        [estimated_route],
        left=[logic],
        color="#b68424",
        height=0.38,
        label="Estimated interconnect",
    )
    ax.text(logic / 2, 0, f"{logic:.3f} ns", color="white", ha="center", va="center")
    ax.text(
        logic + estimated_route / 2,
        0,
        f"{estimated_route:.3f} ns",
        color="white",
        ha="center",
        va="center",
    )
    ax.set_xlim(0, 18)
    ax.set_ylim(-0.6, 0.8)
    ax.set_xlabel("Post-synthesis data delay (ns)")
    ax.set_title("C  Critical arithmetic path")
    ax.legend(loc="upper right", ncol=2, frameon=False, fontsize=8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.text(0.02, 0.63, "110 logic levels; 93 CARRY4", transform=ax.transAxes, fontsize=8.5)

    fig.suptitle(
        "P5 row-cache candidate | Vivado 2018.3 | xc7vx690tffg1761-2 | 25 ns request",
        fontsize=12,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.005,
        "Complete OOC synthesis only. Positive setup slack is not routed Fmax; hold fails before implementation. Power is not measured.",
        ha="center",
        fontsize=9,
        color="#6c3e20",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 0.93))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out.with_suffix(".pdf"))
    fig.savefig(args.out.with_suffix(".png"), dpi=190)
    plt.close(fig)
    record = {
        "source": str(source),
        "source_sha256": digest(source),
        "archive_hashes_verified": len(hashes),
        "metrics": {
            "resource_used": used,
            "resource_available": available,
            "resource_percent": pct,
            "slack_ns": slack,
            "data_delay_ns": path["data_delay_ns"],
            "logic_delay_ns": logic,
            "estimated_interconnect_ns": estimated_route,
            "logic_levels": path["logic_levels"],
        },
        "generator_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(args.out.with_suffix(".pdf")),
        "png_sha256": digest(args.out.with_suffix(".png")),
        "scope": "POST_SYNTHESIS_OOC_NOT_ROUTED_TIMING_NOT_POWER_MEASUREMENT",
    }
    (Path(__file__).resolve().parent / f"{args.out.stem}.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"pdf": str(args.out.with_suffix(".pdf")), "metrics": record["metrics"]}, indent=2
        )
    )


if __name__ == "__main__":
    main()
