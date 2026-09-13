"""Generate the v7→v8 README comparison figure.

Reads the frozen v7.0.10 baseline from git (``git show v7.0.10:tools/results/results.json``)
and the current ``tools/results/results.json``, then renders two panels in the
same dark style as the other result figures:

1. Headline dB-metric pairs (v7 vs v8) for the physical-pipeline integration.
2. Metric accounting donut: how many shared leaves are identical / float-noise
   level / genuinely changed, plus removed/added key counts.

Usage::

    python tools/make_readme_compare.py [--old-ref v7.0.10]

Output: ``tools/results/fig/v7_v8_compare.png`` (150 dpi, dark background).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "results" / "fig" / "v7_v8_compare.png"

BG = "#1D1F27"
FG = "#E8E8EC"
GRID = "#3A3D47"
C_OLD = "#8A8F9C"
C_NEW = "#4ECDC4"
C_HL = "#FF6B6B"
SEG_SAME = "#4ECDC4"
SEG_FLOAT = "#F0C24B"
SEG_CHANGE = "#FF6B6B"


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": BG,
            "axes.facecolor": BG,
            "savefig.facecolor": BG,
            "axes.edgecolor": GRID,
            "axes.labelcolor": FG,
            "axes.titlecolor": FG,
            "xtick.color": FG,
            "ytick.color": FG,
            "text.color": FG,
            "grid.color": GRID,
            "font.size": 10,
            "font.family": "sans-serif",
            "font.sans-serif": ["PingFang SC", "Microsoft YaHei", "Helvetica Neue", "Arial"],
        }
    )


def _load_pair(old_ref: str) -> tuple[dict, dict]:
    old_raw = subprocess.run(
        ["git", "show", f"{old_ref}:tools/results/results.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    old = json.loads(old_raw)
    new = json.loads((ROOT / "tools" / "results" / "results.json").read_text(encoding="utf-8"))
    return old, new


def _leaves(d: object, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if not isinstance(d, dict):
        return out
    for key, value in d.items():
        path = prefix + key
        if isinstance(value, dict):
            out.update(_leaves(value, path + "."))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            out[path] = float(value)
    return out


def _dig(d: dict[str, float], path: str) -> float:
    """Look up a dotted-path key in the flat leaf dict produced by ``_leaves``."""
    return d[path]


HEADLINE_DB = [
    ("pipeline.interleave.floor_dB", "交织本底深度 |dB|（无输入）", -1.0),
    ("pipeline.interleave.spur_reduction_dB", "交织杂散抑制量", 1.0),
    ("mc_pdk_off.SFDR_min", "MC 最差 SFDR", 1.0),
    ("s7_dem_off.cases.缩电容 s=0.25 · 有 KTC.SFDR_dB", "DEM 关·缩电容 SFDR", 1.0),
    ("s7_dem_off.cases.原电容 · 无 KTC.SFDR_dB", "DEM 关·原电容 SFDR", 1.0),
    ("flicker.autozero_removal_dB", "AZ 对 1/f 清除深度 |dB|", -1.0),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-ref", default="v7.0.10", help="git ref of the baseline results.json")
    args = parser.parse_args()

    old, new = _load_pair(args.old_ref)
    lo, ln = _leaves(old), _leaves(new)

    common = [k for k in lo if k in ln]
    changed = [k for k in common if lo[k] != ln[k]]
    meaningful = [
        k for k in changed if abs(ln[k] - lo[k]) / max(abs(lo[k]), abs(ln[k]), 1e-12) > 1e-6
    ]
    n_same = len(common) - len(changed)
    n_float = len(changed) - len(meaningful)
    n_change = len(meaningful)
    n_removed = len(set(lo) - set(ln))
    n_added = len(set(ln) - set(lo))

    _style()

    fig = plt.figure(figsize=(13.0, 5.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.55, 1.0], wspace=0.32)

    # ---- Panel A: headline dB pairs -------------------------------------
    axA = fig.add_subplot(gs[0, 0])
    rows = [(label, _dig(lo, key) * sign, _dig(ln, key) * sign) for key, label, sign in HEADLINE_DB]

    y = list(range(len(rows)))[::-1]
    h = 0.34
    for off, color, tag in (
        (h / 2 + 0.02, C_OLD, "v7.0.10（聚合基线）"),
        (-h / 2 - 0.02, C_NEW, "v8.0.0（物理主链路）"),
    ):
        vals = [r[1 + (0 if color is C_OLD else 1)] for r in rows]
        axA.barh([yi + off for yi in y], vals, height=h, color=color, label=tag, zorder=3)
    for yi, (_, v7, v8) in zip(y, rows, strict=True):
        axA.text(v7 + 1.2, yi + h / 2 + 0.02, f"{v7:.1f}", va="center", fontsize=7.5, color=C_OLD)
        axA.text(v8 + 1.2, yi - h / 2 - 0.02, f"{v8:.1f}", va="center", fontsize=7.5, color=C_NEW)
        delta = v8 - v7
        if abs(delta) > 0.05:
            axA.text(
                max(v7, v8) + 9.5,
                yi,
                f"+{delta:.1f} dB",
                va="center",
                fontsize=8,
                color=C_NEW,
                fontweight="bold",
            )
    axA.set_yticks(y)
    axA.set_yticklabels([r[0] for r in rows], fontsize=9)
    axA.set_xlabel("dB（各行为“越大越好”）")
    axA.set_xlim(0, 152)
    axA.grid(axis="x", alpha=0.35, zorder=0)
    axA.set_title(
        "物理化主链路后的关键指标：v7.0.10 → v8.0.0", fontsize=11, fontweight="bold", pad=10
    )
    axA.legend(loc="lower right", fontsize=8, framealpha=0.15)

    # ---- Panel B: metric accounting donut --------------------------------
    axB = fig.add_subplot(gs[0, 1])
    sizes = [n_same, n_float, n_change]
    colors = [SEG_SAME, SEG_FLOAT, SEG_CHANGE]
    wedges, _ = axB.pie(
        sizes,
        colors=colors,
        startangle=90,
        counterclock=False,
        wedgeprops={"width": 0.42, "edgecolor": BG, "linewidth": 1.5},
    )
    axB.text(0, 0.10, f"{len(common)}", ha="center", fontsize=21, fontweight="bold", color=FG)
    axB.text(0, -0.16, "两代共有指标", ha="center", fontsize=9, color=C_OLD)

    legend_labels = [
        f"完全一致 {n_same}",
        f"浮点级噪声（<1e-6 相对） {n_float}",
        f"实质变化 {n_change}",
        f"v7 独有键移除 {n_removed} · v8 新增 {n_added}",
    ]
    handles = list(wedges) + [
        plt.Rectangle((0, 0), 1, 1, facecolor="none", edgecolor="none") for _ in range(1)
    ]
    axB.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.055),
        fontsize=8.5,
        framealpha=0.0,
        ncol=1,
    )
    axB.set_title("results.json 逐项 diff 对账", fontsize=11, fontweight="bold", pad=10)
    axB.text(
        0.0,
        -2.12,
        "实质变化集中在物理主链路新覆盖的子系统：交织 / 时序失配 / 低频 / AZ / 校准",
        ha="center",
        fontsize=8,
        color=FG,
        transform=axB.transData,
    )

    fig.subplots_adjust(left=0.20, bottom=0.17)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
