"""Render each of the 22 final shared-selector RTL benches from actual CI logs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import matplotlib
from make_final_regression_evidence import groups_for_count, metric_lines

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def digest(path: Path) -> str:
    """Bind raw bytes and figures to the exact archived inputs."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """Verify final CI source identity and redraw three groups of bench evidence."""
    report = Path(__file__).resolve().parent.parent
    repo = report.parents[2]
    archive = repo / "docs/evidence/20260930/ci_shared_71e7d5a"
    hashes = json.loads((archive / "sha256.json").read_text())
    for name, expected in hashes.items():
        assert digest(archive / name) == expected, name
    manifest = json.loads((archive / "manifest.json").read_text())
    inputs = json.loads((archive / "tested_inputs_sha256.json").read_text())
    assert manifest["workflow_conclusion"] == "success" and manifest["jobs_success"] == 9
    assert manifest["whole_tree_equal"] and inputs["whole_tree_equal"]
    head = manifest["head_sha"]
    for name, expected in inputs["files"].items():
        data = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=repo)
        assert hashlib.sha256(data).hexdigest() == expected, name
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    all_benches = {}
    for suffix, title, names in groups_for_count(22):
        fig, ax = plt.subplots(figsize=(10.5, 7.2))
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        ax.text(0.5, 0.98, title, ha="center", fontsize=14, fontweight="bold")
        ax.text(
            0.5,
            0.93,
            "Final RTL 89b9f8153fea | head 71e7d5a | CI 36727200500",
            ha="center",
            fontsize=10,
        )
        plotted = {}
        for i, name in enumerate(names):
            text = (archive / "artifact/open_rtl" / f"{name}.run.log").read_text()
            assert manifest["completion_markers"][name] in text and "Verilog $finish" in text
            tb = f"sim/tb/{name}.sv"
            assert tb in inputs["files"]
            lines, data = metric_lines(name, text)
            x = 0.01 + (i % 2) * 0.5
            y = 0.705 - (i // 2) * 0.195
            ax.add_patch(
                FancyBboxPatch(
                    (x, y),
                    0.47,
                    0.16,
                    boxstyle="round,pad=0.007",
                    facecolor="#edf5f3",
                    edgecolor="#b2ceca",
                    linewidth=0.8,
                )
            )
            ax.text(x + 0.015, y + 0.125, name, fontsize=10.5, fontfamily="monospace")
            ax.text(x + 0.455, y + 0.125, "PASS", ha="right", color="#176951", fontweight="bold")
            for line_index, line in enumerate(lines):
                ax.text(x + 0.015, y + 0.088 - line_index * 0.026, line, fontsize=10)
            plotted[name] = {
                "metrics": data,
                "display_lines": lines,
                "log_sha256": digest(archive / "artifact/open_rtl" / f"{name}.run.log"),
                "tb_sha256": inputs["files"][tb],
            }
        ax.text(
            0.5,
            0.038,
            "Counters retain their own units. Zero-delay RTL only; no SDF, analog performance or timing claim.",
            ha="center",
            fontsize=8.7,
            color="#77482e",
        )
        fig.tight_layout(pad=0.3)
        stem = report / "figures" / f"shared_ci_{suffix}"
        fig.savefig(stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None})
        fig.savefig(stem.with_suffix(".png"), dpi=160)
        plt.close(fig)
        all_benches.update(plotted)
    assert set(all_benches) == set(manifest["completion_markers"])
    record = {
        "scope": "FINAL_SOURCE_BOUND_ZERO_DELAY_22_BENCH_CI_NOT_EDA_OR_ANALOG",
        "archive_payloads_verified": len(hashes),
        "git_inputs_verified": len(inputs["files"]),
        "ci_manifest_sha256": digest(archive / "manifest.json"),
        "generator_sha256": digest(Path(__file__)),
        "metric_parser_sha256": digest(
            Path(__file__).with_name("make_final_regression_evidence.py")
        ),
        "benches": all_benches,
        "images_sha256": {
            path.name: digest(path) for path in sorted((report / "figures").glob("shared_ci_*"))
        },
    }
    (Path(__file__).parent / "shared_ci.sha256.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"benches": len(all_benches), "payloads": len(hashes), "inputs": len(inputs["files"])},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
