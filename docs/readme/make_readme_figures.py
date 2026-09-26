#!/usr/bin/env python3
"""README 配图生成器：架构总览、模块地图、专利论文对应关系、披露值锚点、来源分级、验证方法论。

设计约束
--------
* **全部为原创示意图**。本仓库从不分发第三方原文；本脚本亦**不复制**论文/专利的任何
  图像，只按公开披露的**文字**描述重绘拓扑与对应关系（引用条目见 `NOTICE`）。
* **计数现场推导，不硬编码**：参数分级来自运行时 `provenance.PARAM_GRADES`，
  机制/来源/实现程度来自 `mechanism_inventory.json`，模块分组与文件系统实际内容做
  集合相等断言（新模块漏登记会让脚本直接失败，而不是悄悄过期）。
* 风格与 `docs/release_v8.2.1`、`docs/release_v8.2.2` 的配图一致（浅色、打印友好、
  中文字体优先 PingFang SC、200 dpi）。

用法::

    python docs/readme/make_readme_figures.py            # 全部
    python docs/readme/make_readme_figures.py --only 1 3 # 只出指定图
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIG = OUT / "fig"
FIG.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------- 调色板（与 release 配图一致）
INK = "#1F2733"
MUTED = "#5A6472"
PANEL = "#F7F9FC"
EDGE = "#D5DBE3"
BLUE = "#3C6EA5"
ORANGE = "#D98A3D"
TEAL = "#5B9E8F"
PURPLE = "#9B6BA8"
RED = "#C0504D"
GREY = "#B0B7C0"
GOLD = "#C9A227"

# 来源标签 → 颜色（对应关系图的列）
SOURCE_STYLE = {
    "00": ("[00] 论文", BLUE),
    "00_1": ("[00_1] 讲稿", BLUE),
    "09": ("[09] 专利", TEAL),
    "10": ("[10] 专利", TEAL),
    "11": ("[11] 专利", TEAL),
    "12": ("[12] 专利", TEAL),
    "13": ("[13] 专利", TEAL),
    "14": ("[14] 专利", TEAL),
    None: ("原创扩展", GOLD),
}

# 实现程度 → 颜色
DEGREE_STYLE = {
    "END_TO_END": ("端到端", TEAL),
    "INTEGRATED_SCENARIO": ("已入主链路", BLUE),
    "MECHANISM_MODEL": ("机制级", ORANGE),
    "NOT_MODELED": ("未建模", RED),
}

# 参数来源分级 → 颜色
GRADE_STYLE = {
    "DISCLOSED": ("[披露] 文献直接给出", TEAL),
    "DERIVED": ("[派生] 由披露值+公式算出", BLUE),
    "FITTED": ("[拟合] 对齐公开指标反推", PURPLE),
    "ASSUMED": ("[假设] 工程选取，非披露", ORANGE),
    "RESEARCH_EXTENSION": ("[原创扩展] 无文献源头", GOLD),
}


def pick_cjk() -> str:
    """挑一个本机可用的中文字体名（找不到则退化为 DejaVu Sans）。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return "DejaVu Sans"


def use_style() -> None:
    """统一 rcParams：中文字体、负号、网格、边框。"""
    plt.rcParams.update(
        {
            "font.sans-serif": [pick_cjk(), "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.edgecolor": EDGE,
            "text.color": INK,
        }
    )


def panel(ax, x, y, w, h, *, fc=PANEL, ec=EDGE, lw=1.0, rad=0.02, z=2):
    """画一个圆角面板。"""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={rad * 100}",
            facecolor=fc,
            edgecolor=ec,
            linewidth=lw,
            zorder=z,
        )
    )


def label(ax, x, y, text, *, size=9.5, color=INK, weight="normal", ha="center", va="center", **kw):
    """写一段文字（默认居中）。"""
    return ax.text(x, y, text, fontsize=size, color=color, fontweight=weight, ha=ha, va=va, **kw)


def arrow(ax, x0, y0, x1, y1, *, color=MUTED, lw=1.6, style="-|>", rad=0.0, z=4, ls="-"):
    """画一条带箭头的连线。"""
    ax.add_patch(
        FancyArrowPatch(
            (x0, y0),
            (x1, y1),
            arrowstyle=style,
            mutation_scale=13,
            color=color,
            linewidth=lw,
            linestyle=ls,
            zorder=z,
            connectionstyle=f"arc3,rad={rad}",
            shrinkA=0,
            shrinkB=0,
        )
    )


def blank_canvas(w=100.0, h=56.0):
    """建一个无坐标轴的画布，返回 (fig, ax)。"""
    fig, ax = plt.subplots(figsize=(14.0, h / 100.0 * 14.0 * 1.06))
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.set_axis_off()
    return fig, ax


# ----------------------------------------------------------------- 数据源（现场读取）


def load_mechanisms() -> list[dict]:
    """读机制清单（来源 / 实现程度 / 验证性质 / 代码落点）。"""
    payload = json.loads((REPO / "mechanism_inventory.json").read_text(encoding="utf-8"))
    return payload["mechanisms"]


def load_provenance_counts() -> dict[str, int]:
    """跑一遍运行时分级账本，统计 93 项参数各等级的数量。"""
    sys.path.insert(0, str(REPO / "src"))
    from adi_model.provenance import PARAM_GRADES

    counts: dict[str, int] = {}
    for grade, _note in PARAM_GRADES.values():
        counts[grade.name] = counts.get(grade.name, 0) + 1
    counts["_total"] = len(PARAM_GRADES)
    return counts


MODULE_GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "配置·溯源·入口",
        BLUE,
        [
            "config",
            "provenance",
            "inventory_gate",
            "serialization",
            "_arrays",
            "cli",
            "__init__",
            "__main__",
        ],
    ),
    ("采样与模拟前端", TEAL, ["sampler", "sampling_charge", "input_network", "aux_input", "sadc"]),
    (
        "DAC·DEM·切片池",
        ORANGE,
        ["rdac", "dac_arch", "dem", "mapper", "slice_pool", "scheduler", "pretracking"],
    ),
    (
        "参考·RA·动态·后端",
        PURPLE,
        [
            "ra",
            "adc2",
            "conversion",
            "dynamics",
            "reference_charge",
            "ref_track",
            "interleave_tracking",
            "timing",
        ],
    ),
    ("主链路求解器", BLUE, ["pipeline", "pipeline_engine", "sim", "sim_split", "chip"]),
    (
        "数字·校准·重构",
        TEAL,
        ["digital_core", "fixed_point", "calib", "weight_calibration", "reconstruction"],
    ),
    ("噪声模型", ORANGE, ["ktc", "noise_phase", "low_frequency_noise"]),
    (
        "验证·实验·报告",
        PURPLE,
        ["acceptance", "experiments", "closure_experiments", "benchmarks", "metrics", "reporting"],
    ),
    ("独立参照实现", GOLD, ["charge_ref"]),
]


def check_module_groups() -> int:
    """断言分组覆盖文件系统里的全部模块（漏登记即失败，不静默过期）。"""
    actual = {p.stem for p in (REPO / "src" / "adi_model").glob("*.py")}
    listed = {m for _t, _c, mods in MODULE_GROUPS for m in mods}
    missing, extra = actual - listed, listed - actual
    if missing or extra:
        raise SystemExit(f"模块分组与文件系统不一致：未登记={sorted(missing)} 多余={sorted(extra)}")
    return len(actual)


# ----------------------------------------------------------------- 图 1：架构总览
def fig1_architecture() -> Path:
    """两级残差 SAR 的信号链与逐样本相位流水（原创示意图）。"""
    fig, ax = blank_canvas(100, 58)

    label(ax, 1.5, 56.2, "信号链：两级残差 SAR 的物理通路", size=13.0, weight="bold", ha="left")
    label(
        ax,
        1.5,
        53.4,
        "第一级 9b 粗判 + 残差放大 + 第二级细判，两级在数字域加权合成 20-bit；"
        "全链共用同一组物理电容（电荷一致闭包）",
        size=9.4,
        color=MUTED,
        ha="left",
    )

    # ---- 模拟域（上）与数字域（下）的分界
    blocks = [
        (
            "采样网络",
            BLUE,
            ["双通路 SADC / RDAC", "20.5 pF / 512 单位", "采样态 dither 注入"],
            "sampler.py",
            "sampling_charge.py",
        ),
        (
            "第一级 SAR",
            TEAL,
            ["sDAC 与 RDAC 分离", "粗码 9b 判决", "误差自愈窗口"],
            "sadc.py",
            "mapper.py",
        ),
        (
            "RDAC + 顶板保持",
            ORANGE,
            ["残差电荷 v_top", "等权单位置换 DEM", "桥接 C_C 边界残差"],
            "rdac.py",
            "dac_arch.py",
        ),
        (
            "共享残差放大器",
            PURPLE,
            ["G = C_active / C_F", "auto-zero 消失调", "有限带宽 / 压摆"],
            "ra.py",
            "conversion.py",
        ),
        (
            "ADC2 细量化",
            BLUE,
            ["两级窗口 + 余量", "观察器校正后置", "溢出统计"],
            "adc2.py",
            "reference_charge.py",
        ),
    ]
    x0, w, gap = 3.6, 15.4, 3.4
    top, hh = 34.0, 15.0
    for i, (name, color, lines, f1, f2) in enumerate(blocks):
        bx = x0 + i * (w + gap)
        panel(ax, bx, top, w, hh, fc="white", ec=color, lw=1.6)
        ax.add_patch(Rectangle((bx, top + hh - 2.6), w, 2.6, facecolor=color, alpha=0.9, zorder=3))
        label(ax, bx + w / 2, top + hh - 1.3, name, size=10.2, color="white", weight="bold")
        for j, ln in enumerate(lines):
            label(ax, bx + w / 2, top + hh - 5.0 - j * 2.5, ln, size=8.5, color=INK)
        label(ax, bx + w / 2, top + 2.5, f1, size=7.0, color=MUTED)
        label(ax, bx + w / 2, top + 1.0, f2, size=7.0, color=MUTED)
        if i:
            arrow(ax, bx - gap + 0.3, top + hh / 2, bx - 0.4, top + hh / 2, color=color, lw=1.9)
    # 输入与输出
    label(ax, 1.3, top + hh / 2, "x", size=12, weight="bold", color=INK)
    arrow(ax, 2.1, top + hh / 2, x0 - 0.4, top + hh / 2, color=MUTED, lw=1.9)
    arrow(ax, x0 + 5 * (w + gap) - gap + 0.4, top + hh / 2, 98.4, top + hh / 2, color=MUTED, lw=1.9)

    # ---- 数字域
    dtop, dh = 19.0, 9.0
    panel(ax, 3.0, dtop, 93.6, dh, fc="#F2F7F5", ec=TEAL, lw=1.6)
    ax.add_patch(Rectangle((3.0, dtop + dh - 2.6), 93.6, 2.6, facecolor=TEAL, alpha=0.9, zorder=3))
    label(
        ax,
        4.2,
        dtop + dh - 1.3,
        "数字域（20-bit 整数输出）",
        size=10.2,
        color="white",
        weight="bold",
        ha="left",
    )
    label(
        ax,
        46.0,
        dtop + 4.4,
        "名义 DAC 口径重构：x̂ = ( v_D0 + fine / Ĝ − d_corr ) / α",
        size=10.6,
        weight="bold",
    )
    label(
        ax,
        46.0,
        dtop + 1.6,
        "定点核 Q30/Q32 + 96-bit 累加；α 与 dither 掩码同源推导；数字算法读不到物理真值 "
        "chip.C_true（数据流边界由验收强制）",
        size=8.4,
        color=MUTED,
    )
    label(ax, 89.5, dtop + dh / 2 - 0.4, "digital_core.py", size=7.2, color=MUTED)
    label(ax, 89.5, dtop + dh / 2 - 2.0, "fixed_point.py", size=7.2, color=MUTED)
    label(ax, 89.5, dtop + dh / 2 - 3.6, "reconstruction.py", size=7.2, color=MUTED)
    arrow(ax, 46.0, top - 0.3, 46.0, dtop + dh + 0.3, color=MUTED, lw=1.7, ls=(0, (4, 3)))

    # ---- 相位流水（下）
    label(
        ax,
        1.5,
        16.2,
        "逐样本相位流水（一个转换周期内的相位机）",
        size=13.0,
        weight="bold",
        ha="left",
    )
    phases = [
        ("① 采集", "共享源阻抗", "物理切片状态", TEAL),
        ("② 预跟踪", "可用判决", "预充电", BLUE),
        ("③ 粗转换", "9b 逐次逼近", "sDAC", BLUE),
        ("④ 残差建立", "顶板保持", "参考恢复", ORANGE),
        ("⑤ 放大", "有限带宽", "压摆 / 摆幅", PURPLE),
        ("⑥ 细转换", "ADC2 宽窄带", "跟踪", BLUE),
        ("⑦ 释放", "回写已释放", "切片状态", TEAL),
    ]
    py, ph = 5.2, 9.8
    pw = 12.4
    px0 = 3.0
    for i, (name, note1, note2, color) in enumerate(phases):
        bx = px0 + i * (pw + 1.0)
        panel(ax, bx, py, pw, ph, fc="white", ec=color, lw=1.4)
        label(ax, bx + pw / 2, py + ph - 1.9, name, size=10.0, weight="bold", color=color)
        label(ax, bx + pw / 2, py + 4.9, note1, size=7.2, color=INK)
        label(ax, bx + pw / 2, py + 2.9, note2, size=7.2, color=INK)
        if i:
            arrow(ax, bx - 1.0 + 0.15, py + ph / 2, bx - 0.15, py + ph / 2, color=GREY, lw=1.4)
    # 回环
    arrow(
        ax,
        px0 + 6 * (pw + 1.0) + pw / 2,
        py - 0.4,
        px0 + pw / 2,
        py - 0.4,
        color=GREY,
        lw=1.2,
        rad=0.12,
        ls=(0, (4, 3)),
    )
    label(
        ax,
        px0 + 3.5 * (pw + 1.0),
        py - 3.2,
        "下一采样（18 片池：8 转换 / 8 采集 / 2 备用，A/B 乒乓）",
        size=8.6,
        color=MUTED,
    )

    fig.savefig(FIG / "fig1_architecture.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig1_architecture.png"


# ----------------------------------------------------------------- 图 2：模块地图
def fig2_modules() -> Path:
    """48 个模块的分组地图（分组与文件系统做集合相等断言）。"""
    total = check_module_groups()
    fig, ax = blank_canvas(100, 76)

    label(
        ax,
        1.5,
        74.2,
        f"代码模块地图：{total} 个模块按职责分 9 组",
        size=13.0,
        weight="bold",
        ha="left",
    )
    label(
        ax,
        1.5,
        71.6,
        "同一份物理状态被两套独立求解器读取（逐相位状态机 pipeline.py 与向量化 sim_split.py）；"
        "charge_ref.py 不复用闭式解，充当独立电荷真值",
        size=9.2,
        color=MUTED,
        ha="left",
    )

    gap = 1.15
    y = 67.0
    badge_w = 23.0
    ncol_grid = 6
    grid_x0, grid_w = 26.8, 70.2
    for title, color, mods in MODULE_GROUPS:
        names = sorted(mods)
        rows = -(-len(names) // ncol_grid)
        strip_h = 2.6 + rows * 2.5
        y -= strip_h + gap
        panel(ax, 2.0, y, 96.0, strip_h, fc="white", ec=color, lw=1.4)
        ax.add_patch(Rectangle((2.0, y), badge_w, strip_h, facecolor=color, alpha=0.9, zorder=3))
        label(
            ax,
            2.0 + badge_w / 2,
            y + strip_h / 2,
            f"{title} （{len(names)}）",
            size=9.4,
            color="white",
            weight="bold",
            zorder=4,
        )
        for j, m in enumerate(names):
            row, col = divmod(j, ncol_grid)
            cx = grid_x0 + (col + 0.5) * (grid_w / ncol_grid)
            cy = y + strip_h - (row + 0.5) * (strip_h / rows)
            label(ax, cx, cy, f"{m}.py", size=7.9, color=INK)

    label(
        ax,
        50.0,
        2.2,
        "组外资产：rtl/ 定点 RTL（可综合校准核 + Verilator 仿真与变异门禁）、synth/ DC 综合流、"
        "sim/ 向量与测试台、tools/ 验收驱动与导出、tests/ 回归套件",
        size=8.6,
        color=MUTED,
    )
    fig.savefig(FIG / "fig2_modules.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig2_modules.png"


# ----------------------------------------------------------------- 图 5：来源分级
def fig5_provenance() -> Path:
    """93 项模型参数的来源分级（运行时账本，非注释）。"""
    counts = load_provenance_counts()
    total = counts["_total"]
    order = ["DISCLOSED", "DERIVED", "FITTED", "ASSUMED", "RESEARCH_EXTENSION"]
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 4.6), gridspec_kw={"width_ratios": [1.25, 1]})

    ax = axes[0]
    left = 0.0
    for name in order:
        n = counts.get(name, 0)
        if not n:
            continue
        ax.barh([0], [n], left=left, color=GRADE_STYLE[name][1], height=0.5, zorder=3)
        if n / total > 0.04:
            ax.text(
                left + n / 2,
                0,
                str(n),
                ha="center",
                va="center",
                color="white",
                fontsize=11,
                fontweight="bold",
                zorder=4,
            )
        left += n
    ax.set_xlim(0, total)
    ax.set_ylim(-0.65, 0.75)
    ax.set_yticks([])
    ax.set_xlabel(f"参数条目数（共 {total} 项）")
    ax.set_title("模型参数的来源分级构成", fontsize=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    for spine in ("top", "right", "left", "bottom"):
        ax.spines[spine].set_visible(False)
    disclosed = counts.get("DISCLOSED", 0)
    ax.annotate(
        f"直接披露 {disclosed} / {total}",
        xy=(disclosed / 2, 0.28),
        xytext=(disclosed / 2 + 6, 0.62),
        fontsize=10,
        fontweight="bold",
        color=TEAL,
        arrowprops={"arrowstyle": "-|>", "color": TEAL, "linewidth": 1.4},
    )

    ax = axes[1]
    ax.axis("off")
    y = 0.94
    ax.text(
        0.0, y, "分级是运行时值，不是注释", fontsize=12.5, fontweight="bold", transform=ax.transAxes
    )
    y -= 0.13
    for name in order:
        n = counts.get(name, 0)
        if not n:
            continue
        ax.add_patch(
            Rectangle(
                (0.0, y - 0.028),
                0.035,
                0.048,
                facecolor=GRADE_STYLE[name][1],
                transform=ax.transAxes,
            )
        )
        ax.text(0.06, y, GRADE_STYLE[name][0], fontsize=9.6, transform=ax.transAxes, color=INK)
        ax.text(
            0.97, y, f"{n}", fontsize=10.5, fontweight="bold", ha="right", transform=ax.transAxes
        )
        y -= 0.115
    y -= 0.01
    ax.text(
        0.0,
        y,
        f"共 {total} 条；`ASSUMED` 经公式计算后不得洗成纯披露结果，\n"
        "新增 Config 字段未登记等级会让测试直接失败（provenance.audit_provenance）。",
        fontsize=8.8,
        transform=ax.transAxes,
        color=MUTED,
        va="top",
        bbox={"boxstyle": "round,pad=0.5", "facecolor": PANEL, "edgecolor": EDGE},
    )
    fig.suptitle(
        f"来源可追责：{total} 项模型参数全部带等级标签（[披露] / [派生] / [拟合] / [假设] / [原创扩展]）",
        fontsize=13.0,
        y=0.99,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(FIG / "fig5_provenance_grades.png", dpi=200)
    plt.close(fig)
    return FIG / "fig5_provenance_grades.png"


# ----------------------------------------------------------------- 图 3：对应关系矩阵
def _mech_short(title: str) -> str:
    """把机制标题压成图上可读的短标签。"""
    head = title.split("（")[0].split("(")[0].strip()
    return head if len(head) <= 26 else head[:25] + "…"


def fig3_alignment() -> Path:
    """机制 × 文献来源 对应矩阵 + 实现程度（数据来自 mechanism_inventory.json）。"""
    mechs = load_mechanisms()
    columns = ["00", "00_1", "09", "10", "11", "12", "13", "14", None]
    nrow, ncol = len(mechs), len(columns)
    fig, ax = plt.subplots(figsize=(14.2, 1.9 + 0.42 * nrow))
    ax.set_xlim(0, ncol + 3.4)
    ax.set_ylim(-1.7, nrow + 1.6)
    ax.set_axis_off()

    # 列头
    for j, key in enumerate(columns):
        name, color = SOURCE_STYLE[key]
        ax.text(
            j + 0.5,
            nrow + 0.45,
            name.replace(" ", "\n"),
            ha="center",
            va="bottom",
            fontsize=8.6,
            color=color,
            fontweight="bold",
        )
    ax.text(
        ncol + 1.7,
        nrow + 0.45,
        "实现程度",
        ha="center",
        va="bottom",
        fontsize=9.4,
        color=INK,
        fontweight="bold",
    )

    for i, m in enumerate(mechs):
        y = nrow - i - 1
        if i % 2 == 0:
            ax.add_patch(
                Rectangle((0, y), ncol + 3.4, 1.0, facecolor=PANEL, edgecolor="none", zorder=1)
            )
        ax.text(
            -0.35,
            y + 0.5,
            f"{m['id']}  {_mech_short(m['title'])}",
            ha="right",
            va="center",
            fontsize=8.7,
            color=INK,
        )
        src_ids = {s.get("id") for s in m["sources"]}
        for j, key in enumerate(columns):
            _name, color = SOURCE_STYLE[key]
            if key in src_ids:
                ax.add_patch(
                    Rectangle(
                        (j + 0.22, y + 0.22),
                        0.56,
                        0.56,
                        facecolor=color,
                        edgecolor="none",
                        zorder=3,
                    )
                )
            else:
                ax.plot([j + 0.5], [y + 0.5], marker="o", markersize=2.2, color="#DCE2EA", zorder=2)
        deg = m["implementation_degree"]
        deg_name, deg_color = DEGREE_STYLE.get(deg, (deg, GREY))
        ax.add_patch(
            FancyBboxPatch(
                (ncol + 0.35, y + 0.18),
                2.7,
                0.64,
                boxstyle="round,pad=0,rounding_size=0.18",
                facecolor=deg_color,
                edgecolor="none",
                alpha=0.9,
                zorder=3,
            )
        )
        ax.text(
            ncol + 1.7,
            y + 0.5,
            deg_name,
            ha="center",
            va="center",
            fontsize=8.2,
            color="white",
            fontweight="bold",
            zorder=4,
        )

    counts = {}
    for m in mechs:
        counts[m["implementation_degree"]] = counts.get(m["implementation_degree"], 0) + 1
    legend = "   ".join(
        f"{DEGREE_STYLE.get(k, (k, GREY))[0]} {v}"
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    ax.text(
        0,
        -1.0,
        f"共 {nrow} 项机制   ·   {legend}   ·   实心色块 = 该机制取自对应文献；空心点 = 未取自该来源",
        fontsize=8.8,
        color=MUTED,
    )
    ax.set_title(
        "工程实现与主架构论文、机制专利的对应关系（逐机制 × 逐来源）",
        fontsize=13.0,
        pad=16,
    )
    fig.tight_layout()
    fig.savefig(FIG / "fig3_source_alignment.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig3_source_alignment.png"


# ----------------------------------------------------------------- 图 4：披露值 → 代码锚点
def fig4_anchors() -> Path:
    """论文/讲稿披露值 → 代码锚点 的双栏对照（含来源等级）。"""
    rows = [
        (
            "NSD 8.8 nV/√Hz、DR 94.6 dB（正文 94.2）",
            "config.py 量程校核：Vfs,rms = 2.111 Vrms → ±3.0 V 差分满幅",
            "DISCLOSED",
        ),
        ("G0 = 32（架构图标注）", "config.g0；残差裕量 = 0.15 V / G0 = 4.6875 mV", "DISCLOSED"),
        (
            "20.5 pF 采样电容 / 512 个单位",
            "rdac 步长 = 2·v_fs/512 → 11.719 mV；单位 40.04 fF",
            "DISCLOSED",
        ),
        (
            "18-slice 池（8 转换 + 8 采集 + 2 备用）",
            "scheduler.reserve_dual + slice_pool 因果不变量断言",
            "DISCLOSED",
        ),
        ("共享 RA 占 ADC 功耗约 40%", "ra.py：电荷一致增益 G = C_active / C_F", "DISCLOSED"),
        (
            "auto-zero：噪声代价 −1.6 dB、动态采样带宽 +1.3 dB",
            "ra.py 折叠因子；stage22 只做符号与量级结构校验",
            "DISCLOSED",
        ),
        (
            "第一级 9b 量化 + dither 范围“enhanced by 2b”",
            "b1 = 7 读法（ADR 0003），paper_literal / legacy_v61 备选保留",
            "ASSUMED",
        ),
        (
            "论文实测 INL 2.2 LSB（≈2.307 LSB20）",
            "dynamics.py 三项动态误差的缺口收敛对象",
            "比对目标",
        ),
    ]
    fig, ax = plt.subplots(figsize=(14.2, 6.0))
    ax.set_xlim(0, 100)
    ax.set_ylim(-1.8, len(rows) + 2.4)
    ax.set_axis_off()

    ax.text(
        22.5,
        len(rows) + 1.3,
        "论文 / 讲稿披露值",
        ha="center",
        fontsize=11.5,
        fontweight="bold",
        color=BLUE,
    )
    ax.text(
        69.0,
        len(rows) + 1.3,
        "本工程的代码锚点",
        ha="center",
        fontsize=11.5,
        fontweight="bold",
        color=TEAL,
    )
    ax.text(95.0, len(rows) + 1.3, "等级", ha="center", fontsize=11.5, fontweight="bold", color=INK)
    arrow(ax, 47.0, len(rows) - 0.2, 49.0, len(rows) - 0.2, color=GREY, lw=1.4)

    for i, (src, code, grade) in enumerate(rows):
        y = len(rows) - i - 1
        if i % 2 == 0:
            ax.add_patch(
                Rectangle((0, y - 0.06), 100, 1.06, facecolor=PANEL, edgecolor="none", zorder=1)
            )
        ax.text(0.6, y + 0.5, src, ha="left", va="center", fontsize=9.0, color=INK, zorder=3)
        arrow(ax, 44.6, y + 0.5, 45.9, y + 0.5, color=GREY, lw=1.1, z=3)
        ax.text(
            46.6, y + 0.5, code, ha="left", va="center", fontsize=9.0, color="#31485F", zorder=3
        )
        color = {"DISCLOSED": TEAL, "ASSUMED": ORANGE, "比对目标": PURPLE}.get(grade, GREY)
        ax.add_patch(
            Rectangle((92.4, y + 0.26), 0.55, 0.48, facecolor=color, edgecolor="none", zorder=3)
        )
        ax.text(
            93.4,
            y + 0.5,
            grade,
            ha="left",
            va="center",
            fontsize=8.6,
            color=color,
            fontweight="bold",
            zorder=3,
        )

    ax.text(
        0.6,
        -0.95,
        "等级含义：[披露] 文献直接给出 · [假设] 工程选取（非披露） · [比对目标] 论文实测值，用作缺口收敛对象。\n"
        "所有“论文说 X”级别的主张都对应一个带等级的运行时参数值；左侧仅为条目级转述，"
        "原文（PDF / 讲稿 / 专利）不随仓库分发，见 NOTICE。",
        fontsize=8.4,
        color=MUTED,
        va="top",
    )
    ax.set_title("披露值 → 代码锚点：每个头条数字都能追到文献或假设", fontsize=13.0, pad=18)
    fig.tight_layout()
    fig.savefig(FIG / "fig4_disclosed_anchors.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig4_disclosed_anchors.png"


# ----------------------------------------------------------------- 图 6：验证方法论
def fig6_methodology() -> Path:
    """四道防线：双实现等价、独立真值、运行时分级、验收门禁。"""
    fig, ax = blank_canvas(100, 34)
    label(
        ax,
        1.5,
        32.2,
        "验证方法论：为什么这些结论可以被质疑着读",
        size=13.0,
        weight="bold",
        ha="left",
    )

    cards = [
        (
            "① 双实现逐位等价",
            TEAL,
            ["pipeline.py 逐相位状态机", "sim_split.py 逐样本向量化", "非理想全关时逐位一致"],
            "两套独立实现读同一接口\n读出同样语义——最强交叉校验",
        ),
        (
            "② 独立电荷真值",
            BLUE,
            ["charge_ref.py 不复用闭式解", "逐相位节点方程另推一遍", "不一致先怀疑主循环口径"],
            "禁止反向修改凑数；\n中间节点口径错误无法被端到端测试掩盖",
        ),
        (
            "③ 参数来源运行时分级",
            ORANGE,
            ["DISCLOSED / DERIVED", "FITTED / ASSUMED", "新增字段未登记即测试红"],
            "分级是运行时值不是注释；\nASSUMED 不得洗成披露结果",
        ),
        (
            "④ 验收门禁 + 钉子",
            PURPLE,
            [
                "52 条硬性判据全通过",
                "REQUIRED_RECORDS 不可静默消失",
                "xfail(strict=True) 跟踪未闭合项",
            ],
            "修好变 XPASS 即失败，\n不留过期的“已完成”印象",
        ),
    ]
    w, gap = 23.0, 2.0
    for i, (title, color, lines, note) in enumerate(cards):
        bx = 1.5 + i * (w + gap)
        panel(ax, bx, 6.0, w, 23.5, fc="white", ec=color, lw=1.5)
        ax.add_patch(Rectangle((bx, 26.9), w, 2.6, facecolor=color, alpha=0.9, zorder=3))
        label(ax, bx + w / 2, 28.2, title, size=10.0, color="white", weight="bold")
        for j, ln in enumerate(lines):
            label(ax, bx + w / 2, 24.0 - j * 2.4, ln, size=8.6, color=INK)
        ax.add_patch(
            FancyBboxPatch(
                (bx + 1.0, 8.2),
                w - 2.0,
                6.6,
                boxstyle="round,pad=0.3,rounding_size=2.5",
                facecolor=PANEL,
                edgecolor=EDGE,
                linewidth=1.0,
                zorder=3,
            )
        )
        label(ax, bx + w / 2, 11.5, note, size=8.4, color=MUTED)
    label(
        ax,
        50.0,
        2.4,
        "另有：变异检验（回退源码看对应测试是否真的失败）、钉死的隔离副本上由未参与修复者做对抗复核、"
        "以及参考产物字节账（results.json 逐叶子对账）",
        size=9.0,
        color=MUTED,
    )
    fig.savefig(FIG / "fig6_verification.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig6_verification.png"


# ----------------------------------------------------------------- 图 7：机制 → 代码 契合点映射
def fig7_mapping() -> Path:
    """双栏映射：文献机制（左）→ 代码落点（右），连线标注机制编号与对齐程度。"""
    mechs = load_mechanisms()
    n = len(mechs)
    fig, ax = blank_canvas(100, 72)

    label(
        ax,
        1.5,
        70.2,
        "契合点映射：文献披露的机制（左）→ 本工程的代码落点（右）",
        size=13.0,
        weight="bold",
        ha="left",
    )
    label(
        ax,
        1.5,
        68.2,
        "每条连线 = 一项机制取自该文献；线的样式表示对齐程度。数据来自 mechanism_inventory.json（现场读取）",
        size=9.0,
        color=MUTED,
        ha="left",
    )

    row_h, row_gap = 2.9, 0.5
    top = 64.5
    row_center = [top - i * (row_h + row_gap) - row_h / 2 for i in range(n)]
    bottom = top - (n - 1) * (row_h + row_gap) - row_h
    left_x, left_w = 1.5, 20.0
    right_x, right_w = 62.0, 36.5

    label(ax, left_x + left_w / 2, top + 1.4, "文献来源", size=10.2, weight="bold", color=BLUE)
    label(
        ax,
        right_x + right_w / 2,
        top + 1.1,
        "机制 → 代码落点",
        size=10.2,
        weight="bold",
        color=TEAL,
    )

    # 每个机制的主模块（取前两个文件名，短）
    def modules_of(m: dict) -> str:
        names = [Path(p).name for p in m["code"]]
        head = ", ".join(names[:2]) + (" …" if len(names) > 2 else "")
        return head

    for i, m in enumerate(mechs):
        cy = row_center[i]
        deg = m["implementation_degree"]
        deg_name, deg_color = DEGREE_STYLE.get(deg, (deg, GREY))
        panel(ax, right_x, cy - row_h / 2, right_w, row_h, fc="white", ec=deg_color, lw=1.2)
        title = _mech_short(m["title"])
        title = title if len(title) <= 20 else title[:19] + "…"
        label(ax, right_x + 1.2, cy + 0.35, f"{m['id']}  {title}", size=8.2, color=INK, ha="left")
        label(ax, right_x + 1.2, cy - 0.75, modules_of(m), size=7.0, color=MUTED, ha="left")
        if deg == "MECHANISM_MODEL":
            label(
                ax,
                right_x + right_w - 1.0,
                cy,
                deg_name,
                size=7.0,
                color=ORANGE,
                ha="right",
                weight="bold",
            )

    # 左栏来源框：按引用它的机制的重心纵向定位，再消解重叠
    keys = list(SOURCE_STYLE)
    used = [k for k in keys if any(k in {s.get("id") for s in m["sources"]} for m in mechs)]
    centers: list[tuple[str, float, int]] = []
    for key in used:
        idx = [i for i, m in enumerate(mechs) if key in {s.get("id") for s in m["sources"]}]
        centers.append((key, sum(row_center[i] for i in idx) / len(idx), len(idx)))
    centers.sort(key=lambda t: -t[1])
    min_gap = 5.6
    floor = bottom + 1.8
    ceiling = top + 1.6
    ys = [want for _k, want, _c in centers]
    for i in range(1, len(ys)):  # 自上而下推开
        ys[i] = min(ys[i], ys[i - 1] - min_gap)
    if ys[-1] < floor:  # 底部越界则整体上移，再自下而上重推
        shift = floor - ys[-1]
        ys = [y + shift for y in ys]
        ys[0] = min(ys[0], ceiling)
        for i in range(len(ys) - 2, -1, -1):
            ys[i] = max(ys[i], ys[i + 1] + min_gap)
    if ys[0] > ceiling or ys[-1] < floor:  # 仍放不下：在可用区间内等距分布
        step = (ceiling - floor) / max(len(centers) - 1, 1)
        ys = [ceiling - i * step for i in range(len(centers))]
    src_y = {key: y for (key, _w, _c), y in zip(centers, ys, strict=True)}

    for key, y, cnt in centers:
        name, color = SOURCE_STYLE[key]
        panel(ax, left_x, y - 1.9, left_w, 3.8, fc="white", ec=color, lw=1.5)
        label(ax, left_x + left_w / 2, y + 0.6, name, size=9.6, weight="bold", color=color)
        label(ax, left_x + left_w / 2, y - 0.95, f"{cnt} 项机制", size=8.0, color=MUTED)

    for i, m in enumerate(mechs):
        cy = row_center[i]
        for s in m["sources"]:
            key = s.get("id")
            if key not in src_y:
                continue
            sy = src_y[key]
            deg = m["implementation_degree"]
            _dn, dcol = DEGREE_STYLE.get(deg, (deg, GREY))
            edge_col = GOLD if key is None else dcol
            edge_ls = "-" if deg == "INTEGRATED_SCENARIO" else (0, (4, 2))
            arrow(
                ax,
                left_x + left_w + 0.4,
                sy,
                right_x - 0.5,
                cy,
                color=edge_col,
                lw=0.95,
                rad=0.12,
                ls=edge_ls,
                z=4,
            )

    label(
        ax,
        1.5,
        bottom - 3.2,
        "实线 = 已入主链路（端到端生效）；虚线 + 机制级标签 = 仅机制级实现或机制对齐但排列空间/口径不同"
        "（见 §4 各表“已登记的缺口”列）",
        size=8.4,
        color=MUTED,
        ha="left",
    )
    fig.savefig(FIG / "fig7_mapping.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    return FIG / "fig7_mapping.png"


RENDERERS = {
    1: fig1_architecture,
    2: fig2_modules,
    3: fig3_alignment,
    4: fig4_anchors,
    5: fig5_provenance,
    6: fig6_methodology,
    7: fig7_mapping,
}


def main() -> int:
    """渲染选定的图（默认全部）。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", type=int, default=sorted(RENDERERS))
    args = parser.parse_args()
    use_style()
    for key in args.only:
        if key not in RENDERERS:
            raise SystemExit(f"未知图号：{key}（可选 {sorted(RENDERERS)}）")
        path = RENDERERS[key]()
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
