"""Render source-bound local-row P5/P6/P7 structural RTL functional evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def sha256(path: Path) -> str:
    """Return the SHA-256 of exact file bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Validate the frozen logs, then draw observed digital latency by profile."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    archive = args.archive.resolve()
    repo = Path(__file__).resolve().parents[4]
    hashes = json.loads((archive / "sha256.json").read_text())
    for rel, expected in hashes.items():
        assert sha256(archive / rel) == expected, rel
    result = json.loads((archive / "result.json").read_text())
    assert result["bench_count"] == 22 and result["strict_lint_profiles"] == 3
    assert result["rtl_content_sha256"] == (
        "6b27c64df11838428e7f364ac165c5986b51be586fd8cca1722d8095e54e7a22"
    )
    measured = []
    for stage, rel in (
        (5, "profiles/p5.run.log"),
        (6, "profiles/p6.run.log"),
        (7, "logs/structural_adc_tb.run.log"),
    ):
        log = (archive / rel).read_text()
        matches = re.findall(
            r"STRUCTURAL_ADC_COMPLETE modes=(\d+) outputs=(\d+) checks=(\d+) "
            r"physical_slices=(\d+) recon_stages=(\d+) recon_latency=(\d+) latency_checks=(\d+)",
            log,
        )
        assert len(matches) == 1, rel
        modes, outputs, checks, slices, reported_stage, latency, latency_checks = map(
            int, matches[0]
        )
        assert (modes, outputs, checks, slices, reported_stage, latency_checks) == (
            3,
            438,
            7680,
            18,
            stage,
            438,
        )
        assert "Verilog $finish" in log
        assert latency == 25 - 2 * stage
        measured.append(latency)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, ax = plt.subplots(figsize=(8.0, 3.5))
    bars = ax.bar(["P5", "P6", "P7"], measured, color=["#285e85", "#397aa0", "#4a94b3"], width=0.55)
    ax.set_ylim(0, 18.5)
    ax.set_ylabel("Observed output latency (clocks)")
    ax.set_title("Local row update | complete 18-slice RTL top | 3 modes per profile")
    for bar, value in zip(bars, measured, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.3,
            str(value),
            ha="center",
            fontweight="bold",
        )
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(
        0.5,
        0.01,
        "Each profile: 438 outputs, 7,680 protocol cycles, 438 latency checks. Zero-delay RTL; no physical timing claim.",
        ha="center",
        fontsize=8.5,
        color="#6c3e20",
    )
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        args.out.with_suffix(".pdf"),
        metadata={"CreationDate": None, "ModDate": None, "Creator": "make_local_row_profiles.py"},
    )
    fig.savefig(args.out.with_suffix(".png"), dpi=190)
    plt.close(fig)
    record = {
        "scope": "LOCAL_ROW_ZERO_DELAY_RTL_FUNCTIONAL_NOT_PPA",
        "archive": archive.relative_to(repo).as_posix(),
        "archive_payloads_verified": len(hashes),
        "rtl_content_sha256": result["rtl_content_sha256"],
        "profiles": {
            f"P{stage}": latency for stage, latency in zip((5, 6, 7), measured, strict=True)
        },
        "generator_sha256": sha256(Path(__file__)),
        "pdf_sha256": sha256(args.out.with_suffix(".pdf")),
        "png_sha256": sha256(args.out.with_suffix(".png")),
    }
    (Path(__file__).resolve().parent / f"{args.out.stem}.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
