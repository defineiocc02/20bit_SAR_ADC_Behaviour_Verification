#!/usr/bin/env python3
"""从保留数据重绘原创桥接机制与 RTL 校准留出图。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from adi_model import Config
from adi_model.dem import split_switch_command

HERE = Path(__file__).resolve().parent
BLUE, ORANGE = "#0072B2", "#D55E00"


def _save(fig, path: Path) -> None:
    for extension in ("png", "svg"):
        destination = path.with_suffix(f".{extension}")
        fig.savefig(destination, dpi=300, facecolor="white")
        if extension == "svg":
            destination.write_text(
                "\n".join(line.rstrip() for line in destination.read_text().splitlines()) + "\n"
            )
    plt.close(fig)


def _style(ax) -> None:
    ax.grid(axis="y", color="0.88", lw=0.4)
    ax.tick_params(direction="out", length=2.5, width=0.6)


def redraw(out: Path) -> None:
    """重绘工程候选及同一批 512 条 DUT 留出记录。"""
    out.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "Arial",
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "lines.linewidth": 1,
            "svg.fonttype": "none",
        }
    )
    cfg = Config.rtl_structural(dem_enable=True, dither_mode="off")
    fig = plt.figure(figsize=(6.1, 5.1), layout="constrained")
    grid = fig.add_gridspec(2, 2, height_ratios=(1, 1))
    boundary = fig.add_subplot(grid[0, :])
    codes = np.arange(5, 11)
    actual = (
        8.16 * (codes // 8)
        + 3.96 * ((codes % 8) // 4)
        + 2.01 * ((codes % 4) // 2)
        + 0.99 * (codes % 2)
    )
    boundary.plot(codes, codes, "o--", color=BLUE, label="Ideal")
    boundary.plot(codes, actual, "s-", color=ORANGE, label="Example mismatch")
    boundary.axvline(7.5, color="0.45", lw=0.7, ls=":")
    boundary.set(xlabel="Segmented code", ylabel="Nominal charge units", xticks=codes)
    boundary.set_title("Illustrative boundary: 4 + 2 + 1 → 8")
    boundary.text(
        0.98,
        0.06,
        "Step 7 → 8: 1.00 ideal, 1.20 example",
        transform=boundary.transAxes,
        ha="right",
        fontsize=7,
    )
    boundary.legend(loc="upper left", frameon=False)
    _style(boundary)
    axes = (fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1]))
    for ax, sid, color in zip(axes, (0, 256), (BLUE, ORANGE), strict=True):
        command = split_switch_command(cfg, np.array([176.0]), np.array([sid]))
        values = (command.main_counts * 8 + command.sub_counts)[0]
        if values.sum() != 8 * 176:
            raise ValueError("bridge candidate does not preserve the nominal aggregate code")
        x = np.arange(8)
        delta = values - 176
        ax.bar(x, delta, color=color, width=0.65, alpha=0.85)
        ax.scatter(x, delta, color=color, s=15, zorder=3)
        ax.axhline(0, color="0.25", ls="--", lw=0.8)
        for index, value in enumerate(values):
            ax.text(
                index,
                delta[index] + (0.10 if delta[index] >= 0 else -0.18),
                str(int(value)),
                ha="center",
                va="bottom",
                fontsize=7,
            )
        ax.set(xlabel="Active slice slot", ylim=(-1.5, 1.8), xticks=x, yticks=[-1, 0, 1])
        ax.set_title(rf"$sid[8]={sid // 256}$")
        label = "8 × 176 = 1408" if sid == 0 else "4 × 177 + 4 × 175 = 1408"
        ax.text(0.5, 0.97, label, transform=ax.transAxes, ha="center", fontsize=7)
        _style(ax)
    axes[0].set_ylabel("Code change from 176 (unit steps)")
    _save(fig, out / "bridge_state_example")

    summary = json.loads((HERE / "data/structural_calibration_summary.json").read_text())
    with np.load(HERE / "data/structural_calibration_holdout.npz") as data:
        x = data["chip42_x_v"]
        before = data["chip42_before_code"]
        after = data["chip42_after_code"]
        ids = data["chip42_sample_id"]
    if not (len(x) == len(before) == len(after) == len(ids) == 512):
        raise ValueError("held-out input, code and sample-ID lengths disagree")
    lsb = 6 / 2**20
    errors = [(-3 + (code + 0.5) * lsb - x) / lsb for code in (before, after)]
    expected = summary["results"][0]
    if not np.allclose(
        [np.std(e) for e in errors],
        [expected["before_error_std_lsb"], expected["after_error_std_lsb"]],
        rtol=1e-10,
        atol=1e-10,
    ):
        raise ValueError("plotted holdout errors disagree with the recorded results")
    fig, axes = plt.subplots(1, 2, figsize=(5.75, 2.65), layout="constrained", sharex=True)
    for ax, error, title, color, limit in zip(
        axes,
        errors,
        ("Nominal weights", "Frozen fitted weights"),
        (ORANGE, BLUE),
        ((-55, 55), (-1.5, 1.5)),
        strict=True,
    ):
        ax.scatter(x, error, s=5, alpha=0.5, color=color, rasterized=True)
        ax.axhline(0, color="0.3", lw=0.5)
        ax.set(xlim=(-3, 3), ylim=limit, xlabel="Known held-out input (V)")
        ax.set_title(title)
        ax.text(
            0.98,
            0.95,
            f"std {np.std(error):.2f} LSB",
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=7,
        )
        _style(ax)
    axes[0].set_ylabel("Output error (20-bit LSB)")
    _save(fig, out / "structural_calibration_holdout")


def main() -> None:
    """将两张图写入指定输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    args = parser.parse_args()
    redraw(args.out)


if __name__ == "__main__":
    main()
