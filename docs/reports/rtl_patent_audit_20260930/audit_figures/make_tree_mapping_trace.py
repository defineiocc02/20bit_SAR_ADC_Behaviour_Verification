#!/usr/bin/env python3
"""Render actual XSim records, negative controls and input-fanout evidence."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def sha(p: Path) -> str:
    """Return the SHA-256 digest of one archived input or generated figure."""
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rows(p: Path) -> list[dict[str, str]]:
    """Read recorded CSV fields without altering their source precision."""
    with p.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def signed_hex(s: str, width: int) -> int:
    """Decode a two's-complement CSV field at its declared bit width."""
    n = int(s, 16)
    return n - (1 << width) if n & (1 << (width - 1)) else n


def main() -> None:
    """Validate the mapped witnesses and render their existing measurements."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=report.parent / "sar_adc_vivado_20260930/tree_fix",
        help="Root containing mapped_trace_final/ and mapped_witness_retry/",
    )
    base = parser.parse_args().evidence_root.resolve()
    evidence = base / "mapped_trace_final"
    manifest = json.loads((evidence / "manifest.json").read_text())
    if manifest["status"] != "PASS_WITH_NEGATIVE_CONTROLS":
        raise ValueError("Mapped manifest lacks both successful and negative-control witnesses")
    sources = {"manifest": evidence / "manifest.json"}
    data = {}
    for name in ["after", "baseline_fixed"]:
        p = evidence / f"trace_{name}/trace.csv"
        sources[name] = p
        data[name] = rows(p)
        if len(data[name]) != 12904 or any(r["pass"] != "1" for r in data[name]):
            raise ValueError(f"Incomplete or failing repaired trace: {p}")
        if sha(p) != manifest["cases"][name]["trace.csv"]["sha256"]:
            raise ValueError(f"Mapped trace hash differs: {p}")
    for a, b in zip(data["after"], data["baseline_fixed"], strict=True):
        if a != b:
            raise ValueError("Mapped formulas diverge under the same independent inputs")
    bad = {}
    for name, file in [("flash", "trace.csv"), ("reducer", "reducer_negative.csv")]:
        p = evidence / "trace_before" / file
        sources["negative_" + name] = p
        bad[name] = rows(p)
        if sum(r["pass"] != "1" for r in bad[name]) != 1:
            raise ValueError(f"Negative trace must preserve one failing row: {p}")
    fan = {}
    for case in ["before", "after", "baseline_fixed"]:
        p = base / f"mapped_witness_retry/out_{case}/input_fanout.csv"
        sources[case + "_fanout"] = p
        rs = rows(p)
        if len(rs) != 969:
            raise ValueError(f"Fanout input inventory is incomplete: {p}")
        fan[case] = sum(int(r["endpoint_count"]) > 0 for r in rs)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    ink = "#15354a"
    green = "#087f6a"
    blue = "#2869b1"
    red = "#bc4545"
    muted = "#526777"
    fig = plt.figure(figsize=(16, 10), facecolor="#f7f9fc")
    fig.text(
        0.055,
        0.952,
        "GENERATED TREES | MAPPED FUNCTIONAL EVIDENCE",
        fontsize=22,
        weight="bold",
        color=ink,
    )
    fig.text(
        0.055,
        0.916,
        "Vivado / XSim 2018.3  |  Same independent scalar oracle  |  Actual outputs saved before every assertion",
        color=muted,
    )
    ax = fig.add_axes([0.065, 0.55, 0.40, 0.28], facecolor="white")
    r = data["after"][:128]
    x = [int(v["check"]) for v in r]
    ax.plot(x, [int(v["want3"]) for v in r], color=ink, lw=1.5, label="Independent popcount")
    ax.scatter(
        x,
        [int(v["code3"]) for v in r],
        s=12,
        facecolors="none",
        edgecolors=blue,
        label="Repaired mapped output",
        zorder=3,
    )
    ax.set(
        title="A   Seven-input encoder: all 128 input vectors",
        xlabel="Recorded check",
        ylabel="Three-bit code",
        ylim=(-0.3, 7.5),
    )
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.grid(alpha=0.12)
    ax2 = fig.add_axes([0.565, 0.55, 0.38, 0.28], facecolor="white")
    r = data["after"][640:768]
    x = [int(v["check"]) for v in r]
    ax2.plot(
        x,
        [signed_hex(v["want_rails_hex"], 66) for v in r],
        color=ink,
        lw=1.4,
        label="Independent scalar rails",
    )
    ax2.scatter(
        x,
        [signed_hex(v["rails_hex"], 66) for v in r],
        s=13,
        facecolors="none",
        edgecolors=green,
        label="Repaired mapped rails",
        zorder=3,
    )
    ax2.set(
        title="B   Switch / dither / sampling transitions",
        xlabel="Recorded check (first 128 reducer cases)",
        ylabel="Signed rails (integer code)",
    )
    ax2.legend(loc="upper right", frameon=False, fontsize=9)
    ax2.grid(alpha=0.12)
    ax3 = fig.add_axes([0.16, 0.235, 0.31, 0.205], facecolor="white")
    order = ["before", "after", "baseline_fixed"]
    values = [fan[k] for k in order]
    ax3.barh([2, 1, 0], values, height=0.5, color=[red, green, blue])
    ax3.barh([2, 1, 0], [969 - v for v in values], left=values, height=0.5, color="#e4e8ed")
    ax3.set_yticks(
        [2, 1, 0],
        [
            "Old references\ninvalid mapping",
            "Repaired\ncolumn formula",
            "Repaired\noriginal formula",
        ],
    )
    for y, v in zip([2, 1, 0], values, strict=True):
        ax3.text(990, y, f"{v} / 969", va="center", color=ink, weight="bold")
    ax3.set(
        xlim=(0, 1130), xticks=[0, 250, 500, 750, 969], xlabel="Inputs reaching a mapped endpoint"
    )
    ax3.set_title("C   Independently observed input connectivity", pad=13, loc="left")
    ax3.tick_params(axis="y", length=0)
    ax3.grid(axis="x", alpha=0.12)
    ax3.set_axisbelow(True)
    b1 = bad["flash"][-1]
    b2 = bad["reducer"][-1]
    fig.text(
        0.565,
        0.422,
        "D   Retained failing rows from the old netlist",
        fontsize=12,
        weight="bold",
        color=red,
    )
    fig.text(
        0.565,
        0.385,
        f"Flash check {b1['check']}: code3={b1['code3'].upper()} (expected {b1['want3']}); code9={b1['code9'].upper()} (expected {b1['want9']})",
        fontsize=10.8,
        color=ink,
    )
    fig.text(
        0.565,
        0.347,
        f"Reducer check {b2['check']}: total=0x{b2['total_hex']}\nExpected total={int(b2['want_total_hex'],16)}, gain={int(b2['want_gain_hex'],16)}, rails={signed_hex(b2['want_rails_hex'],66)}",
        fontsize=10.5,
        color=ink,
        linespacing=1.5,
        va="top",
    )
    fig.text(
        0.565,
        0.267,
        "Both repaired mapped formulas: 12,904 / 12,904 PASS",
        fontsize=11,
        weight="bold",
        color=green,
    )
    fig.text(
        0.565,
        0.245,
        "Each full CSV is identical; zero failures. Old RTL simulation also\npassed, so RTL-only checks could not detect the synthesis defect.",
        fontsize=10.2,
        color=muted,
        linespacing=1.5,
        va="top",
    )
    fig.text(
        0.055,
        0.137,
        "Coverage: 128 binary Flash patterns + 512 thermometer codes + 3,072 switch combinations + 8,192 address cases + 1,000 random cases.",
        fontsize=10.2,
        color=muted,
    )
    fig.text(
        0.055,
        0.092,
        "Small combinational witness (3 slices × 3 weights, 7/511 comparator inputs). No 640 MHz, full-chip equivalence, or ASIC PPA claim.",
        fontsize=10.5,
        color=ink,
    )
    fig.text(
        0.055,
        0.056,
        f"CSV TB SHA-256: {manifest['tb_sha256'][:24]}...   |   Full inputs and hashes: audit_figures/38_source_hashes.json",
        fontsize=9.5,
        color=muted,
    )
    for a in [ax, ax2, ax3]:
        for spine in a.spines.values():
            spine.set_visible(False)
    output = report / "figures/38_tree_mapping_trace.png"
    fig.savefig(output, dpi=175, facecolor=fig.get_facecolor())
    plt.close(fig)
    meta = {
        "figure": str(output),
        "figure_sha256": sha(output),
        "script_sha256": sha(Path(__file__)),
        "inputs": {k: {"path": str(p), "sha256": sha(p)} for k, p in sources.items()},
        "mapped_checks_per_formula": 12904,
        "mapped_input_reachability": fan,
        "plotted_subsets": {"flash": [1, 128], "rails": [641, 768]},
        "scope": manifest["scope"],
    }
    (report / "audit_figures/38_source_hashes.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
