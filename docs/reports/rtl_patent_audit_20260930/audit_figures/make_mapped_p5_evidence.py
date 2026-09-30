"""Render the actual P5 full-top XSim functional trace with strict source checks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def digest(path: Path) -> str:
    """Return the SHA-256 of the exact bytes on disk."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Verify the preserved vendor run and plot its observed public-port data."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    root = args.archive.resolve()
    hashes = json.loads((root / "sha256.json").read_text())
    for name, expected in hashes.items():
        assert digest(root / name) == expected, name
    summary = json.loads((root / "summary.json").read_text())
    assert summary["attempts"]["final_pass"]["status"] == "FULL_MAPPED_FUNCTIONAL_PASS"
    assert summary["final_audit"]["rows"] == 438
    final = root / "final_pass"
    run = json.loads((final / "remote_run_manifest.json").read_text())
    assert run["status"] == "FULL_MAPPED_FUNCTIONAL_PASS"
    trace = final / "out/trace.csv"
    log = final / "out/xsim.log"
    assert run["independent_local_audit"]["trace_sha256"] == digest(trace)
    assert run["independent_local_audit"]["log_sha256"] == digest(log)
    assert "STRUCTURAL_MAPPED_COMPLETE modes=3 outputs=438 checks=7680" in log.read_text()
    with trace.open(newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [
            "mode",
            "frame",
            "clock",
            "sample_id",
            "code",
            "expected_code",
            "flags",
            "expected_flags",
            "status",
        ]
        rows = [{key: int(value) for key, value in row.items()} for row in reader]
    assert len(rows) == 438
    expected_ids = [i for i in range(2, 159) if (i - 1) % 37 not in (11, 12) and i % 41 != 13]
    assert len(expected_ids) == 146
    per_mode = [[row for row in rows if row["mode"] == mode] for mode in range(3)]
    cadence = []
    for selected in per_mode:
        assert [row["sample_id"] for row in selected] == expected_ids
        assert all(row["code"] == row["expected_code"] for row in selected)
        assert all(row["flags"] == row["expected_flags"] == 0 for row in selected)
        for first, second in pairwise(selected):
            sample_delta = second["sample_id"] - first["sample_id"]
            clock_delta = second["clock"] - first["clock"]
            assert sample_delta > 0 and clock_delta == 16 * sample_delta
            cadence.append(clock_delta / sample_delta)
    assert len(cadence) == 435 and set(cadence) == {16.0}

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    colors = ["#215b93", "#218a7a", "#b68424"]
    fig, axes = plt.subplots(1, 3, figsize=(13.3, 4.6), gridspec_kw={"width_ratios": [1, 1.12, 1]})
    fig.patch.set_facecolor("white")

    ax = axes[0]
    ax.plot(
        [0, 1_048_576],
        [0, 1_048_576],
        color="#444444",
        lw=1.1,
        label="Expected = observed",
    )
    for mode, selected in enumerate(per_mode):
        ax.scatter(
            [row["expected_code"] for row in selected],
            [row["code"] for row in selected],
            s=13,
            alpha=0.75,
            color=colors[mode],
            label=f"Mode {mode}: 146",
        )
    ax.set_xlim(0, 1_048_576)
    ax.set_ylim(0, 1_048_576)
    ax.ticklabel_format(axis="both", style="sci", scilimits=(6, 6))
    ax.set_xlabel("Independent expected code")
    ax.set_ylabel("Mapped DUT code")
    ax.set_title("A  Code equality")
    ax.legend(loc="lower right", fontsize=7.5, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    x_all = set(range(2, 159))
    skipped = sorted(x_all - set(expected_ids))
    for mode, selected in enumerate(per_mode):
        ax.scatter(
            [row["sample_id"] for row in selected],
            [mode] * 146,
            marker="|",
            s=95,
            linewidths=1.2,
            color=colors[mode],
        )
        ax.scatter(
            skipped,
            [mode] * len(skipped),
            marker="x",
            s=20,
            linewidths=0.8,
            color="#c44937",
        )
        ax.scatter(
            [159],
            [mode],
            marker="o",
            s=42,
            facecolors="none",
            edgecolors="#c44937",
            linewidths=1.3,
        )
    ax.set_xlim(0, 162)
    ax.set_yticks([0, 1, 2], labels=["Mode 0", "Mode 1", "Mode 2"])
    ax.set_ylim(-0.6, 2.6)
    ax.set_xlabel("Sample ID (2–158); 159 cancelled")
    ax.set_title("B  Accepted, skipped, cancelled")
    ax.grid(axis="x", color="#dddddd", linewidth=0.6)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.text(
        0.02,
        0.91,
        "146 accepted + 11 skipped per mode",
        transform=ax.transAxes,
        fontsize=8.2,
    )

    ax = axes[2]
    values = [len(selected) - 1 for selected in per_mode]
    bars = ax.bar(["Mode 0", "Mode 1", "Mode 2"], values, color=colors, width=0.57)
    ax.set_ylim(0, 205)
    ax.set_ylabel("Consecutive accepted pairs")
    ax.set_title("C  Exact 16-cycle cadence")
    for bar, value in zip(bars, values, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 3,
            f"{value} / {value}",
            ha="center",
            fontsize=9,
        )
    ax.text(
        0.5,
        0.91,
        "All 435 pairs: Δclock / ΔID = 16",
        transform=ax.transAxes,
        ha="center",
        fontsize=9,
    )
    ax.spines[["top", "right"]].set_visible(False)

    fig.suptitle(
        "Complete P5 mapped netlist | Vivado/XSim 2018.3 | 18 physical slices",
        fontsize=12,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.005,
        "438 output rows, 3,900 configuration readbacks, 7,680 protocol checks, 3 cancels. Functional simulation only; no SDF or timing claim.",
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
        "source_archive": str(root),
        "source_archive_summary_sha256": digest(root / "summary.json"),
        "payload_hashes_verified": len(hashes),
        "trace_sha256": digest(trace),
        "log_sha256": digest(log),
        "rows": len(rows),
        "rows_per_mode": [len(x) for x in per_mode],
        "accepted_pairs_with_exact_16_cycle_spacing": len(cadence),
        "skipped_ids_per_mode": len(skipped),
        "cancelled_ids_per_mode": 1,
        "generator_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(args.out.with_suffix(".pdf")),
        "png_sha256": digest(args.out.with_suffix(".png")),
        "scope": "FULL_TOP_MAPPED_FUNCTIONAL_NO_SDF_NOT_TIMING",
    }
    (Path(__file__).resolve().parent / f"{args.out.stem}.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
