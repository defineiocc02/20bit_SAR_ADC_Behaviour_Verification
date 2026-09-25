#!/usr/bin/env python3
"""robustness_review_2026 报告配图：数据图表（浅色打印友好主题）。

输入：tools/results/results.json（v8.1.0 冻结产物）
输出：docs/robustness_review_2026/fig/chart_{mc,metrics,tests}.png
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[2]
FIG = ROOT / "docs" / "robustness_review_2026" / "fig"
RESULTS = ROOT / "tools" / "results" / "results.json"


def pick_cjk() -> str:
    """选一个可用的中文字体，兜底 DejaVu Sans。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return "DejaVu Sans"


plt.rcParams.update(
    {
        "font.family": pick_cjk(),
        "axes.unicode_minus": False,
        "figure.facecolor": "white",
        "axes.facecolor": "#FAFAFA",
        "axes.edgecolor": "#444444",
        "axes.labelcolor": "#222222",
        "axes.grid": True,
        "grid.alpha": 0.35,
        "grid.color": "#BBBBBB",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "font.size": 10,
        "savefig.dpi": 200,
        "savefig.facecolor": "white",
    }
)

C1, C2, C3 = "#2C6FBB", "#D1495B", "#3A9D6E"


def chart_mc(data: dict) -> None:
    """图：主 MC 16 颗芯片的 SNDR/SFDR 逐颗分布。"""
    mc = data["mc"]
    sndr = mc["sndr_per_chip"]
    sfdr = mc["sfdr_per_chip"]
    x = range(1, len(sndr) + 1)
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 3.6))
    for ax, vals, name, mean_key, color in (
        (axes[0], sndr, "SNDR", "SNDR_mean", C1),
        (axes[1], sfdr, "SFDR", None, C2),
    ):
        ax.bar(x, vals, color=color, alpha=0.85, width=0.72, zorder=3)
        if mean_key:
            m = mc[mean_key]
            ax.axhline(m, color="#222222", lw=1.2, ls="--", zorder=4)
            ax.text(len(vals) + 0.2, m, f"均值 {m:.2f}", va="center", fontsize=8.5)
        lo, hi = min(vals), max(vals)
        ax.set_ylim(lo - (hi - lo) * 0.35 - 0.05, hi + (hi - lo) * 0.35 + 0.05)
        ax.set_title(f"逐芯片 {name}（n={len(vals)}，DEM 开，单位失配主配置）")
        ax.set_xlabel("芯片编号")
        ax.set_ylabel("dB")
    fig.suptitle(
        "蒙特卡洛主配置：16 颗失配芯片的 SNDR / SFDR 分布（v8.1.0 results.json）",
        y=1.02,
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(FIG / "chart_mc.png", bbox_inches="tight")
    plt.close(fig)


def chart_metrics(data: dict) -> None:
    """图：关键指标三口径对比（理想链路 / 失配主配置 / PDK 关失配）+ 论文披露参照线。"""
    s1, mc, pdk = data["s1"], data["mc"], data["mc_pdk_off"]
    groups = [
        ("理想链路\n(s1 上界)", s1["SNDR_dB"], s1["SFDR_dB"]),
        ("失配主配置\n(MC 16 颗均值)", mc["SNDR_mean"], None),
        ("PDK 关失配\n(60 颗最差)", pdk["SNDR_min"], pdk["SFDR_min"]),
    ]
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    xs = range(len(groups))
    sndr = [g[1] for g in groups]
    sfdr = [g[2] if g[2] is not None else float("nan") for g in groups]
    w = 0.34
    ax.bar([x - w / 2 for x in xs], sndr, width=w, color=C1, label="SNDR (dB)", zorder=3)
    sfdr_x = [x + w / 2 for x, v in zip(xs, sfdr, strict=True) if v == v]
    sfdr_v = [v for v in sfdr if v == v]
    ax.bar(sfdr_x, sfdr_v, width=w, color=C2, label="SFDR (dB)", zorder=3)
    for x, v in zip(xs, sndr, strict=True):
        ax.text(x - w / 2, v + 1.5, f"{v:.1f}", ha="center", fontsize=9)
    for x, v in zip(sfdr_x, sfdr_v, strict=True):
        ax.text(x, v + 1.5, f"{v:.1f}", ha="center", fontsize=9)
    ax.axhline(94.2, color=C3, lw=1.4, ls="--", zorder=4)
    ax.text(2.42, 94.2, "论文 [00] DR = 94.2 dB", color=C3, fontsize=8.5, va="bottom", ha="right")
    ax.axhline(93.7, color="#8B6F3C", lw=1.2, ls=":", zorder=4)
    ax.text(
        2.42,
        93.7,
        "论文 [00] SNR<100kHz = 93.7 dB",
        color="#8B6F3C",
        fontsize=8.5,
        va="top",
        ha="right",
    )
    ax.set_xticks(list(xs))
    ax.set_xticklabels([g[0] for g in groups], fontsize=9)
    ax.set_ylabel("dB")
    ax.set_ylim(80, 165)
    ax.legend(loc="upper right", fontsize=9)
    ax.set_title("关键指标三口径对比与论文披露参照（v8.1.0）")
    fig.tight_layout()
    fig.savefig(FIG / "chart_metrics.png", bbox_inches="tight")
    plt.close(fig)


def chart_tests() -> None:
    """图：测试规模演进 v7.0.10 → v8.0.0 → v8.1.0。"""
    vers = ["v7.0.10\n(2026-09-13)", "v8.0.0\n(2026-09-13)", "v8.1.0\n(2026-09-24)"]
    passed = [231, 398, 572]
    xfail = [4, 0, 0]
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.bar(vers, passed, color=C1, width=0.52, label="passed", zorder=3)
    ax.bar(
        vers,
        xfail,
        bottom=passed,
        color="#999999",
        width=0.52,
        label="xfail（未闭合缺陷跟踪）",
        zorder=3,
    )
    for i, (p, x) in enumerate(zip(passed, xfail, strict=True)):
        ax.text(i, p + x + 8, f"{p}+{x}xf" if x else f"{p}", ha="center", fontsize=10)
    ax.set_ylabel("测试条数（not-slow 套件）")
    ax.set_ylim(0, 660)
    ax.legend(fontsize=9)
    ax.set_title("测试规模演进：物理化主链路与 RTL 子系统的门禁增厚")
    fig.tight_layout()
    fig.savefig(FIG / "chart_tests.png", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """读取 results.json 并生成三张报告数据图。"""
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    chart_mc(data)
    chart_metrics(data)
    chart_tests()
    print("OK: chart_mc.png chart_metrics.png chart_tests.png")


if __name__ == "__main__":
    main()
