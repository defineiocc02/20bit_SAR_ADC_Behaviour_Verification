#!/usr/bin/env python3
"""Draw source-derived FSM explanations, distinct from measured trace plots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

REPORT = Path(__file__).resolve().parents[1]
REPO = REPORT.parents[2]
FIG = REPORT / "figures"
BLUE = "#215a8e"
TEAL = "#177f79"
RED = "#ae4944"
GRAY = "#53606d"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "pdf.fonttype": 42})


def canvas(title, height=6.8):
    """Create an uncluttered source-derived diagram canvas."""
    fig, ax = plt.subplots(figsize=(11.6, height))
    ax.set(xlim=(0, 11.6), ylim=(0, height))
    ax.axis("off")
    ax.text(0.2, height - 0.32, title, fontsize=17, weight="bold", color=BLUE)
    ax.text(
        0.2,
        0.12,
        "SOURCE-DERIVED EXPLANATION | edge conditions use pre-edge values | no new state register implied",
        fontsize=9,
        color=GRAY,
    )
    return fig, ax


def box(ax, x, y, w, h, label, color=BLUE):
    """Draw an explanatory state or event box."""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.06,rounding_size=0.12",
            facecolor="#f2f6fa",
            edgecolor=color,
            linewidth=1.5,
        )
    )
    ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=11, color=color)


def arrow(ax, p1, p2, label="", offset=(0, 0.12), rad=0, color=BLUE):
    """Draw a transition with an optional edge-condition label."""
    ax.add_patch(
        FancyArrowPatch(
            p1,
            p2,
            arrowstyle="-|>",
            mutation_scale=14,
            color=color,
            linewidth=1.5,
            connectionstyle=f"arc3,rad={rad}",
        )
    )
    if label:
        ax.text(
            (p1[0] + p2[0]) / 2 + offset[0],
            (p1[1] + p2[1]) / 2 + offset[1],
            label,
            ha="center",
            va="center",
            fontsize=10,
            color=color,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
        )


def save(fig, name):
    """Export vector PDF and readable PNG for the report."""
    fig.subplots_adjust(left=0.02, right=0.99, bottom=0.02, top=0.99)
    fig.savefig(FIG / f"{name}.pdf")
    fig.savefig(FIG / f"{name}.png", dpi=165)
    plt.close(fig)


def phase():
    """Explain the registered structural phase schedule and priming."""
    fig, ax = canvas("Structural mode: 16-clock frame and registered macro controls", 7.4)
    labels = [
        "P0\ncontext / swap",
        "P1\nSAR starts",
        "P2..7\ncoarse bits",
        "P8\ncoarse done",
        "P9\nresidue save",
        "P10..13\nfine bits",
        "P14\nfine done",
        "P15\nrecon start",
    ]
    for i, text in enumerate(labels):
        x = 0.2 + i * 1.42
        box(ax, x, 5.83, 1.25, 0.76, text)
        if i < 7:
            arrow(ax, (x + 1.26, 6.2), (x + 1.40, 6.2))
    arrow(
        ax,
        (11.15, 5.82),
        (0.8, 5.82),
        "wrap only; NEXT pre-edge P0 transfers saved residue to fine",
        offset=(0, -0.42),
        rad=-0.08,
        color=TEAL,
    )
    ax.text(
        0.2,
        4.84,
        "Fastest valid comparator schedule: coarse P2..7 (6 bits), fine P2..13 (12 bits).",
        color=GRAY,
    )
    ax.text(
        0.2,
        4.5,
        "P8 / P14 capture old done before synchronous cancel clears it; RDAC uses prior accepted update.",
        color=GRAY,
    )
    tracks = [
        ("TP", range(1, 16)),
        ("RA AZ", range(1, 9)),
        ("Ref precharge", range(2, 9)),
        ("Amplify / accurate ref", [0, *range(10, 16)]),
        ("Fine acquire", [0, 14, 15]),
    ]
    start_x, step = 2.55, 0.52
    for p in range(16):
        ax.text(start_x + (p + 0.5) * step, 3.91, str(p), ha="center", fontsize=10, color=GRAY)
    for row, (label, active) in enumerate(tracks):
        y = 3.39 - 0.47 * row
        ax.text(0.2, y + 0.12, label, fontsize=11)
        for p in range(16):
            ax.add_patch(
                FancyBboxPatch(
                    (start_x + p * step, y),
                    step - 0.03,
                    0.25,
                    boxstyle="round,pad=0.01",
                    edgecolor="none",
                    facecolor=TEAL if p in active else "#e4e9ee",
                )
            )
    ax.text(
        0.2,
        0.73,
        "Steady-state intervals shown above. Reset/disabled phase0 has ALL registered enables low.",
        color=RED,
        fontsize=11,
    )
    ax.text(
        0.2,
        0.40,
        "Actual controls are registered from next_phase; first wrap is required before steady phase0 amplify/ref.",
        color=GRAY,
        fontsize=10,
    )
    save(fig, "44_structural_phase_fsm")


def sar():
    """Explain comparator acceptance, waiting and cancellation."""
    fig, ax = canvas("sar_trial_ctrl: implicit state and comparator acceptance")
    box(ax, 0.6, 3.2, 2.4, 1.1, "IDLE\nbusy = 0")
    box(ax, 7.7, 3.2, 2.8, 1.1, "BUSY\nbusy = 1")
    arrow(
        ax,
        (3.05, 3.85),
        (7.64, 3.85),
        "start: seed high bits + first trial\nresolved_valid = 1",
        offset=(0, 0.58),
    )
    arrow(
        ax,
        (7.68, 3.35),
        (3.04, 3.35),
        "cmp_valid && bit_index == 0\ndone = 1, resolved_valid = 1",
        offset=(0, -0.62),
    )
    arrow(
        ax,
        (9.5, 4.36),
        (8.62, 4.36),
        "!cmp_valid: hold;\ncmp_valid && index > 0:\naccept bit, index--, next trial",
        offset=(0, 1.08),
        rad=1.5,
        color=TEAL,
    )
    ax.text(
        0.7,
        1.78,
        "Priority on every posedge: reset/!enable > cancel > idle start > busy comparator.",
        color=BLUE,
    )
    ax.text(
        0.7,
        1.34,
        "cancel clears busy/done/valid but retains data/index. Reset/!enable clears all.",
        color=RED,
    )
    ax.text(
        0.7,
        0.90,
        "While busy, start is ignored; a valid comparison can still advance. compare_enable = enable && busy && !cancel.",
        fontsize=10,
        color=GRAY,
    )
    ax.text(
        0.7,
        0.48,
        "Coarse: BITS=9, SEED=3 => six decisions. Fine: BITS=12, SEED=0 => twelve decisions.",
        fontsize=10,
        color=GRAY,
    )
    save(fig, "45_sar_trial_fsm")


def recon():
    """Explain the fixed divider latency and simultaneous commit."""
    fig, ax = canvas("recon_core / div_floor: fixed latency and overlapping commit", 7.0)
    box(ax, 0.4, 4.22, 2.2, 1.0, "IDLE\nstage_b=0, div_busy=0")
    box(ax, 4.0, 4.22, 2.5, 1.0, "MAC_LAUNCH\nstage_b=1")
    box(ax, 8.0, 4.22, 2.9, 1.0, "DIV_RUN\ndiv_busy=1")
    arrow(ax, (2.67, 4.73), (3.94, 4.73), "accept start\nfreeze context", offset=(0, 0.52))
    arrow(ax, (6.56, 4.73), (7.94, 4.73), "next edge:\ndiv captures MAC", offset=(0, 0.52))
    box(
        ax,
        6.57,
        2.32,
        4.15,
        0.9,
        "COMMIT event window (not extra busy state)\ndiv_done=1, busy=0",
        color=TEAL,
    )
    arrow(ax, (9.44, 4.16), (9.44, 3.28), "final divider edge", offset=(-1.53, 0), color=TEAL)
    arrow(
        ax,
        (6.51, 2.76),
        (1.48, 4.15),
        "next edge: old ID/code/flags commit\nno new start => IDLE",
        offset=(-0.15, -0.62),
        color=TEAL,
    )
    arrow(
        ax,
        (7.1, 3.26),
        (5.25, 4.15),
        "same edge may accept\nnext sample",
        offset=(-0.42, 0.33),
        color=TEAL,
    )
    ax.text(
        0.4,
        1.8,
        "Accept at t; div launch at t+1; div done at t+N+1; output valid at t+N+2.",
        color=BLUE,
    )
    ax.text(
        0.4,
        1.34,
        "N=ceil(63/P_STAGES): P5 => 15 clocks; P6 => 13; P7 => 11. Top-level initiation interval = 16.",
        fontsize=10,
    )
    ax.text(
        0.4,
        0.91,
        "Divider RUN ignores start even on its final compute edge. Zero divisor keeps the same latency and reports error.",
        fontsize=10,
        color=GRAY,
    )
    ax.text(
        0.4,
        0.48,
        "cfg_ready=0 cancels pending output; reset clears state. clr_ovf clears sticky events, not the datapath transaction.",
        fontsize=10,
        color=RED,
    )
    save(fig, "46_reconstruction_fsm")


def config():
    """Explain the existing configuration epoch registers."""
    fig, ax = canvas("Configuration epoch: load, atomic control commit, validate, run", 7.2)
    box(ax, 0.3, 4.07, 2.1, 1.05, "LOAD\nready=0, seq=0")
    for x, seq in ((3.4, 3), (6.1, 2), (8.8, 1)):
        box(ax, x, 4.07, 2.05, 1.05, f"CTRL_WAIT{seq}\nready=0, seq={seq}")
    arrow(ax, (2.47, 4.58), (3.34, 4.58), "valid ctrl write", offset=(0, 0.62))
    arrow(ax, (5.51, 4.58), (6.04, 4.58), "!validate", offset=(0, 0.50))
    arrow(ax, (8.21, 4.58), (8.74, 4.58), "!validate", offset=(0, 0.50))
    arrow(
        ax,
        (9.83, 4.01),
        (1.38, 4.01),
        "!validate: commit four control bits atomically; return seq=0",
        offset=(0, -0.43),
        rad=-0.11,
        color=TEAL,
    )
    box(ax, 0.3, 1.88, 2.1, 0.9, "ACTIVE\nready=1, writes reject", color=TEAL)
    arrow(ax, (0.83, 4.01), (0.83, 2.84), "idle valid commit", offset=(1.25, 0), color=TEAL)
    ax.text(3.1, 2.68, "CTRL_WAIT + validate: stall sequence, reject validation.", color=RED)
    ax.text(3.1, 2.18, "LOAD validation requires: !cfg_wr, seq=0, !recon_busy;", fontsize=10)
    ax.text(3.1, 1.85, "all 18x71 weights + 3 scalar registers written; min < max;", fontsize=10)
    ax.text(3.1, 1.52, "sampling and quantizer dither are not both enabled.", fontsize=10)
    ax.text(
        0.3,
        0.97,
        "clear (any state): cancel epoch/transactions, clear written bitmap, retain coefficients/row sums/scalar values.",
        fontsize=10,
        color=RED,
    )
    ax.text(
        0.3,
        0.55,
        "reset: clear values as well. This is a logical abstraction of existing ready/sequence/bitmap registers.",
        fontsize=10,
        color=GRAY,
    )
    save(fig, "47_configuration_fsm")


def main():
    """Export four diagrams and their current source identity."""
    FIG.mkdir(exist_ok=True)
    phase()
    sar()
    recon()
    config()
    sources = [
        "rtl/core/analog_phase_ctrl.sv",
        "rtl/core/sar_structural_ctrl.sv",
        "rtl/core/sar_trial_ctrl.sv",
        "rtl/core/recon_core.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/top/sar20_digital_core.sv",
        "rtl/core/calib_regs.sv",
        "rtl/core/weight_store.sv",
    ]
    identity = {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in sources}
    identity["scope"] = "SOURCE_DERIVED_EXPLANATION_NOT_SIMULATION_OR_STA_PROOF"
    identity["generator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (REPORT / "audit_figures/engineering_fsm_sources.json").write_text(
        json.dumps(identity, indent=2) + "\n"
    )
    print("ENGINEERING_FSM_FIGURES_CREATED count=4")


if __name__ == "__main__":
    main()
