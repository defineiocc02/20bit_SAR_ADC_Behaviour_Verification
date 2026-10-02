#!/usr/bin/env python3
"""Recheck raw FSM CSV and plot actual edge observations, not invented waves."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPORT = Path(__file__).resolve().parents[1]
REPO = REPORT.parents[2]
RAW = REPO / "docs/evidence/20261001/engineering_fsm"
FIG = REPORT / "figures"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "pdf.fonttype": 42})
BLUE, TEAL, RED = "#215a8e", "#177f79", "#ae4944"


def read(name):
    """Read all finite edge rows and enforce their continuity."""
    with (RAW / name).open() as handle:
        rows = list(csv.DictReader(handle))
    for i, row in enumerate(rows, 1):
        if None in row or int(row["edge"]) != i:
            raise ValueError(f"Malformed CSV edge in {name}: {i}")
    return rows


def val(row, key):
    """Decode a decimal state observation from the raw CSV."""
    return int(row[key])


def check():
    """Independently match accepted inputs, output IDs/words and cancellation."""
    sar, recon, phase = read("sar_trace.csv"), read("recon_trace.csv"), read("phase_trace.csv")
    if not (len(sar) == len(recon) == len(phase) == 175):
        raise ValueError("Expected the complete 175-edge directed run")
    for name, expected in json.loads((RAW / "sha256.json").read_text()).items():
        if hashlib.sha256((RAW / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Raw payload hash mismatch: {name}")
    accepted = [
        r
        for r in recon
        if val(r, "rst_n") and val(r, "cfg_ready") and val(r, "start") and not val(r, "pre_busy")
    ]
    output = [r for r in recon if val(r, "post_valid")]
    ids = {val(r, "id"): r for r in accepted}
    expected = {
        101: (73, 0),
        201: (111, 0),
        202: (203, 0),
        302: (150, 0),
        401: (150, 2),
        501: (304, 0),
        701: (409, 0),
    }
    if len(accepted) != 9 or len(output) != 7:
        raise ValueError("Accept/result counts changed")
    for r in output:
        rid = val(r, "post_id")
        if (val(r, "post_word"), val(r, "post_flags")) != expected[rid]:
            raise ValueError(f"Wrong result/flags for ID{rid}")
        if val(r, "edge") - val(ids[rid], "edge") != 11:
            raise ValueError(f"Wrong independent latency for ID{rid}")
    if {val(r, "post_id") for r in output} != set(expected):
        raise ValueError("Duplicate/missing output ID")
    if set(ids) - set(expected) != {301, 601}:
        raise ValueError("Cancelled input IDs changed")
    overlap = recon[61]
    if not (
        val(overlap, "post_valid")
        and val(overlap, "start")
        and not val(overlap, "pre_busy")
        and val(overlap, "post_id") == 201
        and val(overlap, "id") == 202
        and val(overlap, "post_stage_b")
    ):
        raise ValueError("Edge62 commit/accept overlap missing")
    if [(val(r, "edge"), val(r, "post_resolved")) for r in sar if val(r, "post_done")] != [
        (14, 345),
        (33, 201),
    ]:
        raise ValueError("SAR expected completed targets changed")
    zero = [r for r in phase if val(r, "rst_n") and val(r, "enable") and val(r, "post_phase") == 0]
    if len(zero) != 10 or any(
        not val(r, "post_amplify") or not val(r, "post_accurate") for r in zero
    ):
        raise ValueError("Steady-state wrap/analog enable mismatch")
    return sar, recon, phase, accepted, output


def style(ax, title):
    """Apply readable axes without hiding the edge/time scope."""
    ax.set_title(title, loc="left", fontweight="bold", color=BLUE, fontsize=12)
    ax.grid(alpha=0.18)
    ax.spines[["top", "right"]].set_visible(False)


def save(fig, name):
    """Export both vector report figures and directly inspectable PNGs."""
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png", dpi=165)
    plt.close(fig)


def overview(sar, recon, phase, accepted, output):
    """Plot target convergence, startup/wrap and all recon results."""
    fig, axes = plt.subplots(3, 1, figsize=(11.6, 9.5), constrained_layout=True)
    fig.suptitle(
        "Actual zero-delay RTL observations: independent FSM witness",
        fontsize=17,
        weight="bold",
        color=BLUE,
    )
    selected = sar[:36]
    axes[0].step(
        [val(r, "edge") for r in selected],
        [val(r, "post_trial") for r in selected],
        where="post",
        label="trial after NBA",
        color=BLUE,
    )
    axes[0].step(
        [val(r, "edge") for r in selected],
        [val(r, "post_resolved") for r in selected],
        where="post",
        label="resolved after NBA",
        color=TEAL,
    )
    done = [r for r in selected if val(r, "post_done")]
    axes[0].scatter(
        [val(r, "edge") for r in done],
        [val(r, "post_resolved") for r in done],
        color=TEAL,
        zorder=4,
    )
    for r in done:
        axes[0].annotate(
            f"done {r['post_resolved']}",
            (val(r, "edge"), val(r, "post_resolved")),
            xytext=(8, 16),
            textcoords="offset points",
            fontsize=10,
        )
    cancelled = next(r for r in selected if val(r, "cancel"))
    axes[0].axvline(val(cancelled, "edge"), color=RED, ls="--", label="cancel input sampled")
    axes[0].set(xlabel="Rising-edge number", ylabel="9-bit code", xlim=(1, 36), ylim=(0, 430))
    axes[0].legend(loc="lower left", ncol=3, fontsize=9)
    style(axes[0], "A. SAR accepts only valid comparisons; waits and cancellation are exercised")
    selected = phase[:23]
    axes[1].step(
        [val(r, "edge") for r in selected],
        [val(r, "post_phase") for r in selected],
        where="post",
        label="registered phase",
        color=BLUE,
    )
    axes[1].step(
        [val(r, "edge") for r in selected],
        [16 + 2 * val(r, "post_amplify") for r in selected],
        where="post",
        color=TEAL,
        label="amplify state (16=0,18=1)",
    )
    axes[1].set(
        xlabel="Rising-edge number",
        ylabel="Phase / offset logic level",
        xlim=(1, 23),
        yticks=[0, 5, 10, 15, 16, 18],
    )
    axes[1].legend(loc="upper left", ncol=2, fontsize=9)
    style(
        axes[1], "B. Reset phase0 is disabled; first steady wrap phase0 enables amplify/reference"
    )
    for r in accepted:
        edge, sid = val(r, "edge"), val(r, "id")
        color = RED if sid in (301, 601) else BLUE
        axes[2].scatter(edge, sid, color=color, marker="x", s=44)
        axes[2].annotate(
            f"{sid}",
            (edge, sid),
            xytext=(-3, 9),
            textcoords="offset points",
            fontsize=9,
            color=color,
        )
    for r in output:
        edge, sid = val(r, "edge"), val(r, "post_id")
        axes[2].scatter(edge, sid, color=TEAL, marker="o", s=35)
        axes[2].annotate(
            f"word {r['post_word']} / f{r['post_flags']}",
            (edge, sid),
            xytext=(-6 if sid == 701 else 3, -39 if sid == 202 else -19),
            ha="right" if sid == 701 else "left",
            textcoords="offset points",
            fontsize=9,
            color=TEAL,
        )
    axes[2].scatter([], [], color=BLUE, marker="x", label="accepted input ID")
    axes[2].scatter([], [], color=TEAL, label="output ID, +11 edges")
    axes[2].scatter([], [], color=RED, marker="x", label="cancelled ID, no output")
    axes[2].set(
        xlabel="Rising-edge number (10 ns stimulus clock; no physical timing inference)",
        ylabel="Sample ID",
        xlim=(35, 186),
        ylim=(0, 790),
    )
    axes[2].legend(loc="upper left", ncol=3, fontsize=9)
    style(
        axes[2],
        "C. Nine accepted requests; seven outputs; error ID401 holds prior word150 with flags2",
    )
    save(fig, "48_engineering_fsm_trace")


def zoom(recon):
    """Show actual before/after edge levels at the important recon boundaries."""
    fig, axes = plt.subplots(4, 1, figsize=(11.6, 11.8), constrained_layout=True)
    fig.suptitle(
        "Reconstruction boundary evidence from the actual CSV",
        fontsize=17,
        weight="bold",
        color=BLUE,
    )
    panels = [
        (38, 74, "A. Edge62: old ID201 commits while new ID202 is accepted"),
        (73, 95, "B. ID301 cancelled during division; no delayed output"),
        (107, 122, "C. Invalid physical slice: ID401 commits word150 (held) and flags2"),
        (134, 151, "D. ID601 cancelled on its scheduled error commit edge; no flags/event leak"),
    ]
    keys = [
        ("cfg_ready", "ready pre", BLUE),
        ("start", "start pre", BLUE),
        ("post_stage_b", "stage_b post", TEAL),
        ("post_div_busy", "div busy post", TEAL),
        ("post_div_done", "div done post", TEAL),
        ("post_valid", "out valid post", RED),
    ]
    for ax, (first, last, title) in zip(axes, panels, strict=False):
        selected = [r for r in recon if first <= val(r, "edge") <= last]
        for i, (key, _label, color) in enumerate(keys):
            ax.step(
                [val(r, "edge") for r in selected],
                [i + 0.65 * val(r, key) for r in selected],
                where="post",
                color=color,
                linewidth=1.5,
            )
        ax.set(
            xlim=(first - 0.5, last + 0.5),
            ylim=(-0.2, 5.9),
            yticks=[i + 0.25 for i in range(len(keys))],
            yticklabels=[k[1] for k in keys],
            xlabel="Rising-edge number; inputs sampled before edge, states observed after NBA",
        )
        ax.tick_params(axis="y", length=0)
        style(ax, title)
        for r in selected:
            if val(r, "post_valid"):
                ax.annotate(
                    f"ID{r['post_id']} word{r['post_word']} f{r['post_flags']}",
                    (val(r, "edge"), 5.65),
                    xytext=(0, 0),
                    textcoords="offset points",
                    fontsize=9,
                    color=RED,
                    ha="center",
                )
    save(fig, "49_reconstruction_boundary_trace")


def main():
    """Validate the full source-bound archive before making plots."""
    sar, recon, phase, accepted, output = check()
    overview(sar, recon, phase, accepted, output)
    zoom(recon)
    result = {
        "status": "PASS",
        "scope": "ACTUAL_SMALL_GEOMETRY_ZERO_DELAY_RTL_TRACE_NOT_STA",
        "rows_each": 175,
        "accepted": 9,
        "outputs": 7,
        "cancelled_ids": [301, 601],
        "latency_ticks": 11,
        "overlap_edge": 62,
        "trace_hash_index_sha256": hashlib.sha256((RAW / "sha256.json").read_bytes()).hexdigest(),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (REPORT / "audit_figures/engineering_trace_review.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print("ENGINEERING_TRACE_RECHECK_PASS csv_rows_each=175 accepted=9 outputs=7 latency=11")


if __name__ == "__main__":
    main()
