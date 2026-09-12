#!/usr/bin/env python3
"""Generate disclosure-vs-model comparison figures for the reproduction report.

Left panel of each figure: a schematic of the disclosed mechanism
(self-drawn, NOT copied from any third-party document — citation-compliance).
Right panel(s): model simulation data from tools/results/results.json.

Output: docs/report/figs/fig1..fig8 PNG (200 dpi, white-background academic style).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "report" / "figs"
OUT.mkdir(parents=True, exist_ok=True)
D = json.loads((ROOT / "tools" / "results" / "results.json").read_text())

# ---- unified academic style -------------------------------------------------
C_BLUE = "#2C6E9B"
C_ORANGE = "#E58234"
C_GREEN = "#4C9F70"
C_RED = "#C0504D"
C_GRAY = "#8A8A8A"
C_PURPLE = "#7B68AE"
plt.rcParams.update(
    {
        "font.family": ["PingFang SC", "Heiti SC", "sans-serif"],
        "font.size": 8.5,
        "axes.unicode_minus": False,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linewidth": 0.5,
        "axes.titlesize": 9,
        "axes.labelsize": 8.5,
        "legend.fontsize": 7.5,
        "figure.dpi": 200,
    }
)


def left_right(w=6.6, h=2.75, ratios=(1, 1.25)):
    """左"披露机制"卡 + 右"模型数据"卡的双栏画布（无坐标框）。"""
    fig, (axL, axR) = plt.subplots(1, 2, figsize=(w, h), gridspec_kw={"width_ratios": ratios})
    for ax in (axL, axR):
        ax.grid(False)
    axL.set_xticks([])
    axL.set_yticks([])
    for s in ("left", "right", "top", "bottom"):
        axL.spines[s].set_visible(False)
    return fig, axL, axR


def tag(ax, text, xy=(0.02, 0.97), color="#333", weight="bold", size=8.5):
    """面板左上角的机制标签（披露出处/口径注记）。"""
    ax.text(
        xy[0],
        xy[1],
        text,
        transform=ax.transAxes,
        va="top",
        fontsize=size,
        fontweight=weight,
        color=color,
    )


def save(fig, name):
    """统一收紧边距并写出 PNG（白底）。"""
    fig.tight_layout(pad=0.6)
    fig.savefig(OUT / name, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("saved", name)


# =============================================================================
# F1 — interleaving spurs ([00]): position f_S/2±f_IN; shuffle suppresses
# =============================================================================
fig, axL, axR = left_right()

# left: schematic spectrum
f = np.linspace(0, 1.0, 2000)
fin = 0.18


def peak(x, x0, w, a):
    """示意频谱用的单峰高斯（自绘示意，非原文实测曲线）。"""
    return a * np.exp(-(((x - x0) / w) ** 2))


spec = peak(f, fin, 0.006, 0.95) + peak(f, 0.5 - fin, 0.006, 0.30) + peak(f, 0.5 + fin, 0.006, 0.26)
axL.plot(f, spec, color=C_BLUE, lw=1.2)
axL.fill_between(f, spec, color=C_BLUE, alpha=0.12)
for x0, lab, col in [
    (fin, "$f_{IN}$", C_BLUE),
    (0.5 - fin, "$f_S/2{-}f_{IN}$", C_RED),
    (0.5 + fin, "$f_S/2{+}f_{IN}$", C_RED),
]:
    axL.annotate(
        lab,
        xy=(x0, peak(np.array([x0]), x0, 0.006, 1.0)[0]),
        xytext=(x0, 1.12),
        ha="center",
        fontsize=7.5,
        color=col,
        fontweight="bold",
    )
axL.axvline(0.5, color=C_GRAY, lw=0.7, ls="--")
axL.text(0.5, -0.16, "$f_S/2$", ha="center", fontsize=7.5, color=C_GRAY)
axL.set_ylim(-0.22, 1.25)
axL.set_xlim(0, 1.0)
tag(axL, "披露机制 [00]\n交织杂散：位置 $f_S/2\\pm f_{IN}$（结构性）\nspare/洗牌打散杂散")
axL.annotate(
    "",
    xy=(0.5 - fin, 0.34),
    xytext=(0.5 - fin, 0.02),
    arrowprops={"arrowstyle": "->", "color": C_RED, "lw": 1.0},
)
axL.text(0.5 - fin + 0.02, 0.10, "带宽失配\n→ 杂散", fontsize=6.8, color=C_RED)

# right: model data
il = D["pipeline"]["interleave"]["rows"]
labels = [
    "spread=0\n固定组",
    "spread=0.3\n固定组",
    "spread=0.3\n洗牌",
    "spread=0.6\n固定组",
    "spread=0.6\n洗牌",
]
spurs = [r["spur_fs2_fin_dB"] for r in il]
colors = [C_GRAY, C_RED, C_GREEN, C_RED, C_GREEN]
bars = axR.bar(range(5), spurs, color=colors, width=0.62, edgecolor="white")
for b, v in zip(bars, spurs, strict=False):
    axR.text(
        b.get_x() + b.get_width() / 2,
        v - 1.8,
        f"{v:.1f}",
        ha="center",
        va="top",
        fontsize=7,
        color="#333",
        fontweight="bold",
    )
axR.set_xticks(range(5))
axR.set_xticklabels(labels, fontsize=6.8)
axR.set_ylabel("$f_S/2{-}f_{IN}$ 杂散 (dBc)")
axR.set_ylim(-80, 4)
axR.grid(axis="y", alpha=0.25)
gain_db = spurs[1] - spurs[2]  # spread=0.3: 固定组 - 洗牌
axR.annotate(
    "",
    xy=(2, spurs[2] - 1.5),
    xytext=(1, spurs[1] - 1.0),
    arrowprops={"arrowstyle": "->", "color": C_ORANGE, "lw": 1.4},
)
axR.text(
    1.44,
    (spurs[1] + spurs[2]) / 2 - 4,
    f"洗牌抑制 {gain_db:.1f} dB",
    fontsize=7.5,
    color=C_ORANGE,
    fontweight="bold",
    rotation=90,
)
axR.text(
    0.02,
    0.03,
    "模型: pipeline.interleave｜洗牌=非因果实现（限定）",
    transform=axR.transAxes,
    fontsize=6.2,
    color=C_GRAY,
)
save(fig, "fig1_interleave.png")

# =============================================================================
# F2 — INL (M3/M4): paper 2.2 LSB; dynamic-error triad; rho sweep
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.9, ratios=(1, 1.6))

# left: schematic INL shape (parabolic bow + code-correlated sawtooth)
x = np.linspace(0, 1, 400)
bow = 2.0 * (x - 0.5) ** 2 * 4  # parabola
saw = 0.35 * (np.tile(np.linspace(-0.5, 0.5, 40), 10))
inl = bow + saw
axL.plot(x, inl, color=C_BLUE, lw=1.3)
axL.axhline(2.2, color=C_RED, lw=1.0, ls="--")
axL.text(0.03, 2.45, "论文实测 INL ≈ 2.2 LSB", fontsize=7.5, color=C_RED, fontweight="bold")
axL.set_ylim(-0.6, 3.4)
axL.set_xlim(0, 1)
axL.set_xlabel("归一化输入码")
axL.set_ylabel("INL (LSB)")
axL.set_xticks([0, 0.5, 1.0])
axL.set_yticks([0, 1, 2, 3])
axL.grid(alpha=0.2)
tag(
    axL,
    "本模型误差形状示意（自绘）\n非原文实测曲线；论文披露值：\nINL ≈ 2.2 LSB（量级）",
    xy=(0.30, 0.97),
)

# right top: rho sweep
gs = axR.get_subplotspec()
axR.remove()
fig.subplots_adjust(wspace=0.30)
axR1 = fig.add_subplot(2, 2, 2)
axR2 = fig.add_subplot(2, 2, 4)
sw = D["s13"]["ron_sweep"]
rho = [r["rho"] for r in sw]
inlmax = [r["INL_max_LSB20"] for r in sw]
axR1.plot(rho, inlmax, "o-", color=C_BLUE, lw=1.3, ms=3.5, label="模型 INL$_{max}$")
axR1.axhline(2.2, color=C_RED, ls="--", lw=1.0, label="论文 2.2 LSB")
_rho_t = D["s13"]["rho_max_for_2p2LSB"]
_inl_t = D["s13"]["rho_max_INL_at_rho_max"]
axR1.plot([_rho_t], [_inl_t], "v", color=C_ORANGE, ms=7, zorder=5)
axR1.annotate(
    f"ρ={_rho_t} → {_inl_t:.2f} LSB（条件性）",
    xy=(_rho_t, _inl_t),
    xytext=(0.09, 4.5),
    fontsize=7,
    color=C_ORANGE,
    fontweight="bold",
    arrowprops={"arrowstyle": "->", "color": C_ORANGE, "lw": 0.9},
)
axR1.set_xlabel("Ron 码调制系数 ρ")
axR1.set_ylabel("INL$_{max}$ (LSB)")
axR1.set_xlim(-0.012, 0.32)
axR1.set_ylim(-2, 48)
axR1.legend(loc="upper left", frameon=False)
axR1.grid(alpha=0.25)
tag(axR1, "M3｜ρ 扫描：条件性吻合", xy=(0.42, 0.97), size=7)

rows = D["s13"]["rows"]
labs = [
    r["设置"]
    .replace("输入建立+Ron码调制", "建立+Ron调制")
    .replace("数字串扰(共模+单位)", "数字串扰")
    for r in rows
]
vals = [r["INL_max_LSB20"] for r in rows]
cols = [C_GRAY, C_ORANGE, C_RED, C_PURPLE, C_BLUE, C_GREEN]
axR2.barh(range(len(rows)), vals, color=cols, height=0.62)
axR2.set_yticks(range(len(rows)))
axR2.set_yticklabels(labs, fontsize=6.2)
axR2.set_xlabel("INL$_{max}$ (LSB)")
axR2.grid(axis="x", alpha=0.25)
for i, v in enumerate(vals):
    axR2.text(v + 1.5, i, f"{v:.2f}", va="center", fontsize=6.2, color="#333")
tag(axR2, "M4｜三件套分解（s13）", xy=(0.55, 0.97), size=7)
save(fig, "fig2_inl.png")

# =============================================================================
# F3 — SADC self-healing window ([09] Q7/M9)
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.75, ratios=(1, 1.55))

# left: mechanism schematic — coarse error pushes residue out of nominal bin,
# amplified residue still inside ADC2 window → absorbed
axL.add_patch(Rectangle((0.05, 0.42), 0.42, 0.30, fc=C_GREEN, alpha=0.18, ec=C_GREEN, lw=1.0))
axL.text(0.26, 0.57, "ADC2 窗口\n(余量/G₀)", ha="center", va="center", fontsize=7, color=C_GREEN)
for x0, col in [(0.16, C_GRAY), (0.36, C_BLUE)]:
    axL.annotate(
        "",
        xy=(x0, 0.42),
        xytext=(x0, 0.14),
        arrowprops={"arrowstyle": "-", "color": col, "lw": 1.2},
    )
    axL.plot([x0 - 0.012, x0 + 0.012], [0.14, 0.14], color=col, lw=1.6)
axL.annotate(
    "",
    xy=(0.36, 0.30),
    xytext=(0.16, 0.30),
    arrowprops={"arrowstyle": "->", "color": C_ORANGE, "lw": 1.2},
)
axL.text(0.26, 0.335, "粗码误差 → 残差移位 ×G₀ 放大", ha="center", fontsize=6.6, color=C_ORANGE)
axL.text(
    0.26,
    0.05,
    "窗口内：输出不受影响（自愈）\n越界：误差直接泄漏到输出",
    ha="center",
    fontsize=7.2,
    color="#333",
)
axL.set_xlim(0, 0.55)
axL.set_ylim(0, 1.0)
tag(axL, "披露机制 [09] §1.3\n量化器分离 + 误差自愈窗口")

# right top: window scan
axR.remove()
axR1 = fig.add_subplot(2, 2, 2)
axR2 = fig.add_subplot(2, 2, 4)
w_ = D["pipeline"]["sadc_window"]
off = [r["offset_mV"] for r in w_["rows"]]
err = [r["err_rms_uV"] for r in w_["rows"]]
axR1.semilogy(off, err, "o-", color=C_BLUE, lw=1.3, ms=4)
axR1.axvline(w_["predicted_window_mV"], color=C_GREEN, ls="--", lw=1.1)
axR1.axvline(w_["measured_window_mV"], color=C_ORANGE, ls=":", lw=1.1)
axR1.text(
    w_["predicted_window_mV"] - 0.4,
    2.5,
    "预测 4.6875 mV",
    rotation=90,
    fontsize=6.5,
    color=C_GREEN,
    ha="right",
    fontweight="bold",
)
axR1.text(
    w_["measured_window_mV"] + 0.4,
    2.5,
    "实测 4.4531 mV",
    rotation=90,
    fontsize=6.5,
    color=C_ORANGE,
    fontweight="bold",
)
axR1.set_xlabel("注入粗码误差 (mV)")
axR1.set_ylabel("输出误差 RMS (µV)")
axR1.set_ylim(0.5, 6000)
axR1.grid(alpha=0.25, which="both")
tag(axR1, "Q7｜窗口扫描：越界即泄漏（对数轴）", xy=(0.30, 0.97), size=7)

cc = D["s9"]["coarse_correction"]
dt = [r["dtau_ps"] for r in cc]
sn = [r["SNDR_dB"] for r in cc]
axR2.plot(dt, sn, "s-", color=C_PURPLE, lw=1.3, ms=4)
axR2.axhline(93.71, color=C_GRAY, ls=":", lw=0.9)
axR2.text(150, 94.6, "基线 93.71 dB", fontsize=6.5, color=C_GRAY)
for x_, y_ in zip(dt, sn, strict=False):
    axR2.text(x_ + 6, y_ + 1.2, f"{y_:.1f}", fontsize=6.5, color=C_PURPLE)
axR2.set_xlabel("Δτ (ps)")
axR2.set_ylabel("SNDR (dB)")
axR2.set_ylim(55, 100)
axR2.grid(alpha=0.25)
tag(axR2, "M9｜边界两侧：20ps 不变，100ps 崩至 75.7 dB", xy=(0.02, 0.97), size=6.5)
save(fig, "fig3_sadc_window.png")

# =============================================================================
# F4 — dither ([10] Q3/M8/Q9): three paired elements; two implementations
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.75, ratios=(1, 1.35))

# left: three paired elements
items = [
    ("① 注入", "采样电荷叠加 ±D", C_BLUE),
    ("② 改码", "DAC 逻辑码偏移 ±D", C_ORANGE),
    ("③ 数字扣除", "输出减去 D·w₁", C_GREEN),
]
for i, (t, sub, col) in enumerate(items):
    y = 0.72 - i * 0.27
    axL.add_patch(Rectangle((0.06, y - 0.09), 0.40, 0.20, fc=col, alpha=0.15, ec=col, lw=1.0))
    axL.text(0.26, y + 0.02, t, ha="center", fontsize=8, color=col, fontweight="bold")
    axL.text(0.26, y - 0.055, sub, ha="center", fontsize=6.4, color="#333")
    if i < 2:
        axL.annotate(
            "",
            xy=(0.26, y - 0.115),
            xytext=(0.26, y - 0.18),
            arrowprops={"arrowstyle": "->", "color": C_GRAY, "lw": 0.9},
        )
axL.text(
    0.26,
    0.005,
    "三要素严格配对：α=(N−2D)/N 恒定衰减；增强 2 bit",
    ha="center",
    fontsize=6.6,
    color="#333",
)
axL.set_xlim(0, 0.55)
axL.set_ylim(0, 1.0)
tag(axL, "披露机制 [10]\ndither 施加方法（Fig.6/19 两种实现）")

# right: two implementations + closure
axR.remove()
axR1 = fig.add_subplot(1, 2, 2)
di = D["s9"]["dither_impl"]
modes = ["off", "analog", "sampling"]
mlabels = ["关", "输入注入\n(analog)", "采样态\n(sampling)"]
ovf = [next(r for r in di if r["mode"] == m)["ovf_fullscale"] * 100 for m in modes]
sndr = [next(r for r in di if r["mode"] == m)["SNDR_dB"] for m in modes]
xb = np.arange(3)
bars = axR1.bar(xb - 0.18, sndr, width=0.34, color=C_BLUE, label="SNDR (dB)")
axR1.set_ylim(93.2, 93.9)
axR1.set_ylabel("SNDR (dB)")
axR1.set_xticks(xb)
axR1.set_xticklabels(mlabels, fontsize=6.8)
axR1.grid(axis="y", alpha=0.25)
axr2 = axR1.twinx()
axr2.grid(False)
axr2.bar(xb + 0.18, ovf, width=0.34, color=C_ORANGE, label="溢出 (%)")
axr2.set_ylabel("满量程溢出 (%)", color=C_ORANGE)
axr2.tick_params(axis="y", colors=C_ORANGE)
axr2.set_ylim(0, 5.5)
for x_, v in zip(xb + 0.18, ovf, strict=False):
    axr2.text(x_, v + 0.12, f"{v:.1f}%", ha="center", fontsize=6.4, color=C_ORANGE)
axr2.spines["right"].set_visible(True)
axr2.spines["right"].set_color(C_ORANGE)
tag(axR1, "M8｜等代价对比：采样态不占输入量程", xy=(0.02, 0.97), size=7)
axR1.text(
    0.03,
    0.78,
    "α(配置)−α(掩码)=1.1e-16（Q9 闭合）\n代价=−20·log₁₀(α)=0.067 dB",
    transform=axR1.transAxes,
    fontsize=6.4,
    color="#333",
)
save(fig, "fig4_dither.png")

# =============================================================================
# F5 — DEM ([11] M5/M6/M7): nominal conservation; C_C sawtooth; rank
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.75, ratios=(1, 1.35))

# left: equal-weight permutation schematic
rng = np.random.default_rng(7)
n = 24
sel = rng.permutation(n)[:10]
axL.add_patch(Rectangle((0.03, 0.52), 0.94, 0.20, fc="none", ec=C_GRAY, lw=0.8))
for i in range(n):
    col = C_BLUE if i in sel else "#D8D8D8"
    axL.add_patch(
        Rectangle((0.03 + i * (0.94 / n), 0.55), 0.94 / n * 0.82, 0.14, fc=col, ec="white", lw=0.4)
    )
axL.text(
    0.5,
    0.78,
    "任意置换 state：选中单位数 = 逻辑码（k=10）",
    ha="center",
    fontsize=7.2,
    color=C_BLUE,
    fontweight="bold",
)
axL.text(
    0.5,
    0.40,
    "✓ 名义总电荷守恒（结构性质）\n✗ 桥接 C_C 比例误差锯齿：置换无效\n✗ 增益/共模/边界误差：不可修复",
    ha="center",
    fontsize=7.0,
    color="#333",
    linespacing=1.6,
)
axL.text(
    0.5,
    0.10,
    "排列空间：64 个联合排列（R16：512 状态标签坍缩）",
    ha="center",
    fontsize=7.0,
    color=C_ORANGE,
    fontweight="bold",
)
axL.set_xlim(0, 1)
axL.set_ylim(0, 1.0)
tag(axL, "披露机制 [11]\nDEM 等权置换：有效域与边界", xy=(0.02, 0.99))

# right: rank comparison + C_C immunity
axR.remove()
axR1 = fig.add_subplot(1, 2, 2)
# rank 与校准改善倍数全部从 s14.maps / s14.calibration 读取
maps = {m["映射"]: m for m in D["s14"]["maps"]}
cal = {c["映射"]: c for c in D["s14"]["calibration"]}
order = ["fixed", "dem_rotate", "permute"]
mlabels5 = ["固定交织映射\n(fixed)", "DEM 轮转\n(dem_rotate)", "随机置换\n(permute)"]
ranks = [maps[m]["rank"] for m in order]
gains = [cal[m]["改善倍数_A"] for m in order]
bars = axR1.bar(range(3), ranks, color=[C_RED, C_ORANGE, C_GREEN], width=0.55)
axR1.axhline(512, color=C_GRAY, ls="--", lw=0.9)
axR1.text(2.42, 518, "满秩 512", fontsize=6.8, color=C_GRAY, ha="right")
for _i, (b, v, g) in enumerate(zip(bars, ranks, gains, strict=False)):
    axR1.text(
        b.get_x() + b.get_width() / 2,
        v + 10,
        f"rank={v}",
        ha="center",
        fontsize=7.2,
        fontweight="bold",
        color="#333",
    )
    axR1.text(
        b.get_x() + b.get_width() / 2,
        max(v + 52, 90),
        f"校准改善\n{g:.1f}×",
        ha="center",
        fontsize=6.5,
        color=C_GREEN if g >= 3 else C_RED,
        fontweight="bold",
    )
axR1.set_xticks(range(3))
axR1.set_xticklabels(mlabels5, fontsize=6.5)
axR1.set_ylabel("rank(U)（512 维校准空间）")
axR1.set_ylim(0, 640)
axR1.grid(axis="y", alpha=0.25)
tag(axR1, "M7｜秩限制参数辨识（s14.maps）\n注意：满秩 ≠ 校准效果最好", xy=(0.02, 0.97), size=6.6)

save(fig, "fig5_dem.png")

# =============================================================================
# F6 — KTC cancellation (X1/X2, original research extension)
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.75, ratios=(1, 1.35))

# left: phase-noise cancellation schematic
th = np.linspace(0, 2 * np.pi, 100)
a_vec = np.array([1.0, 0.0])
b_vec = np.array([0.85, 0.45])
k = 0.98
res = a_vec - k * b_vec
for vec, col, lab in [
    (a_vec, C_BLUE, "采样噪声 a"),
    (k * b_vec, C_ORANGE, "κ·相关路径 b"),
    (res, C_GREEN, "残余 a−κb"),
]:
    axL.annotate(
        "",
        xy=(vec[0] * 0.8 + 0.5, vec[1] * 0.8 + 0.5),
        xytext=(0.5, 0.5),
        arrowprops={"arrowstyle": "->", "color": col, "lw": 1.8},
    )
    axL.text(
        vec[0] * 0.88 + 0.5, vec[1] * 0.88 + 0.52, lab, fontsize=7.2, color=col, fontweight="bold"
    )
axL.add_patch(Rectangle((0.03, 0.03), 0.94, 0.94, fill=False, ec="none"))
axL.text(
    0.5,
    0.02,
    "κ_opt = aᵀΣb / (bᵀΣb + σ²_eN)（联合最优，R18）",
    ha="center",
    fontsize=7.2,
    color="#333",
    fontweight="bold",
)
axL.set_xlim(0.25, 1.80)
axL.set_ylim(-0.1, 1.1)
tag(axL, "原理（原创研究扩展）\n逐相位噪声相消，不归属 ADI", xy=(0.02, 0.99))

# right: MC vs analytic + sweep
axR.remove()
axR1 = fig.add_subplot(1, 2, 2)
nt = D["noise_transfer"]["rows"]
gam = [r["gamma"] for r in nt]
sa = [r["sigma_analytic_uV"] for r in nt]
sm = [r["sigma_mc_uV"] for r in nt]
su = [r["sigma_uncancellable_uV"] for r in nt]
xb = np.arange(3)
axR1.bar(xb - 0.20, sa, width=0.38, color=C_BLUE, alpha=0.85, label="解析")
axR1.bar(xb + 0.20, sm, width=0.38, color=C_ORANGE, alpha=0.85, label="MC")
axR1.plot(xb, su, "_", color=C_RED, ms=10, mew=1.8, label="结构性残余")
for i, (a_, m_) in enumerate(zip(sa, sm, strict=False)):
    axR1.text(i - 0.20, a_ + 0.03, f"{a_:.3f}", ha="center", fontsize=6.2, color=C_BLUE)
    axR1.text(i + 0.20, m_ + 0.03, f"{m_:.3f}", ha="center", fontsize=6.2, color=C_ORANGE)
axR1.set_xticks(xb)
axR1.set_xticklabels([f"γ={g}" for g in gam])
axR1.set_ylabel("σ_res (µV)")
axR1.set_ylim(0, 1.75)
axR1.legend(frameon=False, loc="upper left")
axR1.grid(axis="y", alpha=0.25)
_dev = max(abs(m - a) / a for a, m in zip(sa, sm, strict=False)) * 100
axR1.text(
    0.98,
    0.97,
    f"X1：最大偏差 {_dev:.2f}%",
    transform=axR1.transAxes,
    ha="right",
    va="top",
    fontsize=7,
    fontweight="bold",
    color="#333",
)
axR1.text(
    0.03,
    0.80,
    "X2：开 KTC → 95.00 dB\n（理想读出上界，s6）",
    transform=axR1.transAxes,
    fontsize=6.4,
    color=C_GREEN,
    fontweight="bold",
)
save(fig, "fig6_ktc.png")

# =============================================================================
# F7 — auto-zero budget (Q8)
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.6, ratios=(1, 1.25))

# left: AZ phase schematic — noise folding budget
axL.plot([0.05, 0.45], [0.55, 0.55], color=C_BLUE, lw=6, solid_capstyle="butt", alpha=0.5)
axL.plot([0.45, 0.62], [0.55, 0.42], color=C_ORANGE, lw=2, ls="--")
axL.plot([0.62, 0.95], [0.42, 0.42], color=C_GREEN, lw=6, solid_capstyle="butt", alpha=0.5)
axL.text(0.25, 0.62, "信号采样", ha="center", fontsize=7.2, color=C_BLUE)
axL.text(0.78, 0.49, "AZ 复位", ha="center", fontsize=7.2, color=C_GREEN)
axL.text(0.535, 0.30, "噪声折叠", fontsize=6.4, color=C_ORANGE, ha="center")
axL.text(
    0.5,
    0.14,
    "白噪声 ×10^(1.6/20)（折叠）\n1/f 整体移除（结构）",
    ha="center",
    fontsize=7.0,
    color="#333",
)
axL.set_xlim(0, 1)
axL.set_ylim(0, 1.0)
tag(
    axL,
    "披露 [00_1] p.34–35\nAZ 代价 −1.6 dB；ADC2 动态带宽 +1.3 dB\n（本模型=效果预算，非相位级）",
)

# right: predicted vs measured
az = D["autozero"]["rows"]
groups = [("autozero", "AZ 代价"), ("adc2_dyn_bw", "ADC2 动态带宽"), ("combined", "叠加")]
pred = [az[g[0]]["predicted_dB"] for g in groups]
meas = [az[g[0]]["measured_dB"] for g in groups]
disc = [-1.6, 1.3, None]
xb = np.arange(3)
axR.bar(xb - 0.19, pred, width=0.36, color=C_BLUE, label="预算预测")
axR.bar(xb + 0.19, meas, width=0.36, color=C_ORANGE, label="模型实测")
for i, dd in enumerate(disc):
    if dd is not None:
        axR.axhline(dd, xmin=(i + 0.08) / 3, xmax=(i + 0.42) / 3, color=C_RED, ls=":", lw=1.6)
        axR.text(i, dd + 0.05, f"披露 {dd:+.1f}", ha="center", fontsize=6.4, color=C_RED)
for i, (p_, m_) in enumerate(zip(pred, meas, strict=False)):
    axR.text(
        i - 0.19,
        p_ - 0.09 if p_ < 0 else p_ + 0.04,
        f"{p_:+.3f}",
        ha="center",
        fontsize=6.3,
        color=C_BLUE,
    )
    axR.text(
        i + 0.19,
        m_ - 0.09 if m_ < 0 else m_ + 0.04,
        f"{m_:+.3f}",
        ha="center",
        fontsize=6.3,
        color=C_ORANGE,
    )
axR.set_xticks(xb)
axR.set_xticklabels([g[1] for g in groups])
axR.set_ylabel("SNDR 变化 (dB)")
axR.axhline(0, color="#333", lw=0.7)
axR.set_ylim(-1.9, 1.75)
axR.grid(axis="y", alpha=0.25)
axR.legend(frameon=False, loc="upper left")
tag(axR, "Q8｜预测↔实测最大偏差 0.006 dB", xy=(0.55, 0.97), size=7)
axR.text(
    0.99,
    0.03,
    "与披露差额 = 参照系差异（不硬凑）",
    transform=axR.transAxes,
    ha="right",
    fontsize=6.2,
    color=C_GRAY,
)
save(fig, "fig7_autozero.png")

# =============================================================================
# F8 — mismatch budget & calibration necessity (M10/Q4)
# =============================================================================
fig, axL, axR = left_right(w=6.6, h=2.75, ratios=(1, 1.45))

# left: budget gap schematic
# 注：300 ppm 是 [00_1] 披露的匹配目标（文献常量）；模型均值口径达标上限
# 由 budget.rows 读出计算，与披露目标是两个口径，不得混写。
sig = np.linspace(0, 1300, 300)
need = 300
brows0 = D["budget"]["rows"]
ok_sig = [r["sigma_ppm"] for r in brows0 if r["SNDR_mean"] >= 93.0]
mean_lim = max(ok_sig) if ok_sig else 0
axL.axvspan(0, need, color=C_GREEN, alpha=0.10)
axL.axvspan(need, 1300, color=C_RED, alpha=0.06)
axL.axvline(need, color=C_GREEN, lw=1.4, ls="--")
axL.axvline(1117, color=C_RED, lw=1.4)
axL.text(
    need / 2,
    0.66,
    "披露匹配目标\nσ ≤ 300 ppm\n[00_1]",
    ha="center",
    fontsize=7.2,
    color=C_GREEN,
    fontweight="bold",
)
axL.text(
    720,
    0.66,
    "PDK 估算 1117 ppm\n（3.7× 于披露目标）",
    ha="center",
    fontsize=7.2,
    color=C_RED,
    fontweight="bold",
)
axL.annotate(
    "",
    xy=(need, 0.42),
    xytext=(1117, 0.42),
    arrowprops={"arrowstyle": "<->", "color": C_ORANGE, "lw": 1.3},
)
axL.text(
    (need + 1117) / 2, 0.48, "3.7×", ha="center", fontsize=8.5, color=C_ORANGE, fontweight="bold"
)
axL.text(
    650,
    0.24,
    "等面积电容补齐需 3.7² ≈ 14× 面积；\n且面积改变会连带采样噪声/负载/建立\n→ 单纯扩面积非等效替代",
    ha="center",
    fontsize=6.6,
    color="#333",
)
axL.text(
    650,
    0.05,
    f"模型均值口径 SNDR≥93 dB 达标上限 ≈{mean_lim:.0f} ppm（budget.rows）",
    ha="center",
    fontsize=6.6,
    color=C_PURPLE,
    fontweight="bold",
)
axL.set_xlim(0, 1300)
axL.set_ylim(0, 1.0)
axL.set_xlabel("电容失配 σ (ppm)")
tag(
    axL,
    "披露论证 [00_1]：>12b AC matching → 校准动机\n（模型等效匹配 13.29 bit ≥ 12，Q4；映射链见正文）",
    xy=(0.50, 0.99),
    size=6.6,
)

# right top: sigma sweep (budget.rows)
axR.remove()
axR1 = fig.add_subplot(2, 2, 2)
brows = D["budget"]["rows"]
s_ppm = [r["sigma_ppm"] for r in brows]
s_mean = [r["SNDR_mean"] for r in brows]
s_min = [r["SNDR_min"] for r in brows]
axR1.plot(s_ppm, s_mean, "o-", color=C_BLUE, lw=1.3, ms=3.5, label="SNDR 均值")
axR1.plot(s_ppm, s_min, "v--", color=C_ORANGE, lw=1.1, ms=3.5, label="SNDR 最小")
axR1.axhline(93.0, color=C_RED, ls=":", lw=1.2)
axR1.axvline(300, color=C_GREEN, ls="--", lw=1.0)
axR1.text(310, 90.5, "披露目标 300 ppm", fontsize=6.3, color=C_GREEN, va="bottom", ha="center")
axR1.axvline(mean_lim, color=C_PURPLE, ls="-.", lw=1.0)
axR1.set_xlabel("失配 σ (ppm)")
axR1.set_ylabel("SNDR (dB)")
axR1.set_xlim(80, 1200)
axR1.set_ylim(90.4, 94.0)
axR1.legend(frameon=False, loc="upper right", fontsize=6.5)
axR1.grid(alpha=0.25)
tag(axR1, "M10｜σ 扫描（budget.rows）", xy=(0.02, 0.97), size=6.8)

# right bottom: MC strip — 16 chips at their own configuration σ=100 ppm
# x = 该组 MC 的失配配置（mc 记录对应 σ=100 ppm 组），y = 逐片实测 SNDR
axR2 = fig.add_subplot(2, 2, 4)
chips = D["mc"]["sndr_per_chip"]
mc_sigma = 100.0
rngj = np.random.default_rng(3)
xj = mc_sigma + rngj.uniform(-9, 9, len(chips))
axR2.axvspan(mc_sigma - 12, mc_sigma + 12, color=C_PURPLE, alpha=0.10)
axR2.axvline(mc_sigma, color=C_PURPLE, ls=":", lw=1.0)
axR2.plot(xj, chips, "x", ms=4.5, color=C_PURPLE, linestyle="none")
axR2.axhline(float(np.mean(chips)), color=C_BLUE, lw=0.9, ls="--")
axR2.text(
    62,
    float(np.mean(chips)) + 0.03,
    f"均值 {np.mean(chips):.2f} dB",
    fontsize=6.5,
    color=C_BLUE,
    va="bottom",
    ha="left",
)
axR2.set_xlabel("失配 σ (ppm)")
axR2.set_ylabel("MC 单片 SNDR (dB)")
axR2.set_xlim(60, 140)
axR2.set_ylim(93.2, 93.8)
axR2.grid(alpha=0.25)
tag(axR2, f"MC 16 片（σ={mc_sigma:.0f} ppm, FITTED）：93.30–93.72 dB", xy=(0.02, 0.97), size=6.5)
save(fig, "fig8_budget.png")

print("all figures done ->", OUT)
