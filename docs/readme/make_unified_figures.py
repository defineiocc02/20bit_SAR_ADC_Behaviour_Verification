#!/usr/bin/env python3
"""保留旧版多图布局的原创绘图入口，不是当前首页的生产重绘工具。

本脚本读取当前随库数据和参数账本，不重新仿真或训练；默认会写入旧版
unified_*图片路径。历史图和旧基线需要先保留，不能将重绘当成历史复现。
当前首页已收敛为make_overview_figures.py的四图，优先使用该明确--out入口。
旧四案例交织布局使用内部浮点误差，不等同当前三策略最终整数码频谱。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from adi_model.config import Config  # noqa: E402
from adi_model.provenance import PARAM_GRADES  # noqa: E402

OUT = ROOT / "docs/readme/fig"
BLUE, TEAL, ORANGE = "#27628f", "#278979", "#d88c3d"
INK, MUTED, EDGE, PALE = "#1e2937", "#536274", "#d2dae4", "#f2f6fa"
PURPLE = "#8e66a4"


def setup():
    """Select a Chinese font and prepare the output directory."""
    for font in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(font, fallback_to_default=False)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("A Chinese font is required to render the README figures")
    plt.rcParams.update(
        {"font.sans-serif": [font, "DejaVu Sans"], "axes.unicode_minus": False, "text.color": INK}
    )
    OUT.mkdir(parents=True, exist_ok=True)


def canvas(title, subtitle, height=5.4):
    """Create a consistent illustration canvas."""
    fig, ax = plt.subplots(figsize=(14, height))
    fig.patch.set_facecolor("white")
    ax.set(xlim=(0, 14), ylim=(0, 8))
    ax.axis("off")
    ax.text(0.25, 7.6, title, fontsize=20, weight="bold", va="center")
    ax.text(0.25, 7.08, subtitle, fontsize=10.5, color=MUTED, va="center")
    return fig, ax


def box(ax, x, y, w, h, title, detail, color=BLUE):
    """Draw a labeled engineering block."""
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.03,rounding_size=0.12",
            facecolor=PALE,
            edgecolor=EDGE,
            linewidth=1,
        )
    )
    ax.add_patch(
        FancyBboxPatch(
            (x, y + h - 0.10),
            w,
            0.10,
            boxstyle="round,pad=0,rounding_size=0.04",
            facecolor=color,
            edgecolor="none",
        )
    )
    ax.text(x + w / 2, y + h * 0.64, title, ha="center", va="center", fontsize=12, weight="bold")
    ax.text(x + w / 2, y + h * 0.29, detail, ha="center", va="center", fontsize=9, color=MUTED)


def arrow(ax, x1, x2, y, color=BLUE):
    """Connect two blocks with a directional arrow."""
    ax.add_patch(
        FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>", mutation_scale=13, color=color, lw=1.5)
    )


def finish(fig, name):
    """Write a figure and release its resources."""
    fig.savefig(OUT / name, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(OUT / name)


def architecture():
    """Compare the implemented signal chain and configuration profiles."""
    cfg, literal = Config(), Config.paper_literal()
    fig, ax = canvas(
        "两级 SAR：论文线索与工程信号链",
        "ISSCC 2024 9.8 → behavioral model + structural RTL + project DSP",
    )
    labels = [
        ("采样切片池", f"{cfg.n_slices} total · {cfg.n_active} active"),
        ("粗量化 / RDAC", "独立量化 · 电荷残差"),
        ("共享 RA", f"标称增益 ×{cfg.g0:g}"),
        ("后级 ADC2", "细码 / fine code"),
        ("数字重构", "Q30 / Q32 · 20-bit"),
        ("DSP 接口", "码制转换 · 故障门控"),
    ]
    for i, (title, detail) in enumerate(labels):
        x = 0.25 + 2.25 * i
        box(ax, x, 4.20, 1.95, 1.40, title, detail, TEAL if i == 5 else BLUE)
        if i < len(labels) - 1:
            arrow(ax, x + 1.97, x + 2.22, 4.9)
    box(
        ax,
        0.25,
        2.10,
        6.35,
        1.30,
        "默认 Config()",
        f"{cfg.fs/1e6:g} MS/s · {cfg.b1} 位粗量化 · {cfg.dac_arch}",
        ORANGE,
    )
    box(
        ax,
        7.05,
        2.10,
        6.35,
        1.30,
        "Config.paper_literal() / RTL 候选",
        f"{literal.fs/1e6:g} MS/s · {literal.b1} 位 · 63+8 split（未披露的实现假设）",
        TEAL,
    )
    ax.text(
        0.35, 0.95, "20 位表示输出码宽；不等于 20-bit ENOB 或芯片时序签核。", fontsize=11, color=INK
    )
    finish(fig, "unified_1_architecture.png")


def matrix():
    """Draw source membership directly from the mechanism inventory."""
    inventory = json.loads((ROOT / "mechanism_inventory.json").read_text())
    mechanisms = {item["id"]: item for item in inventory["mechanisms"]}
    rows = json.loads((ROOT / "docs/readme/unified_figure_rows.json").read_text())["rows"]
    columns = [
        ("00", "论文"),
        ("00_1", "讲稿"),
        ("09", "[09]"),
        ("10", "[10]"),
        ("11", "[11]"),
        ("12", "[12]"),
        ("13", "[13]"),
        ("14", "[14]"),
    ]
    fig, ax = canvas(
        "论文与专利：机制来源对照",
        "圆点表示清单登记的文献来源；不代表晶体管实现或完整硬件验证",
        height=6.7,
    )
    x0, dx = 5.3, 0.60
    for j, (_, label) in enumerate(columns):
        ax.text(x0 + j * dx, 6.45, label, ha="center", fontsize=9, weight="bold", color=BLUE)
    ax.text(10.35, 6.45, "工程代码入口", fontsize=10, weight="bold", color=BLUE)
    for i, row in enumerate(rows):
        if row["id"] not in mechanisms or not (ROOT / row["code"]).is_file():
            raise RuntimeError(f"Unresolved mechanism/code: {row}")
        item = mechanisms[row["id"]]
        sources = {entry["id"] for entry in item["sources"]}
        y = 5.90 - i * 0.68
        if i % 2 == 0:
            ax.axhspan(y - 0.29, y + 0.29, xmin=0.015, xmax=0.98, color=PALE, zorder=0)
        ax.text(0.35, y, f'{row["id"]}  {row["label"]}', va="center", fontsize=10.5)
        for j, (source_id, _) in enumerate(columns):
            ax.scatter(x0 + j * dx, y, s=78, color=TEAL if source_id in sources else "#e1e7ed")
        ax.text(
            10.35,
            y,
            row["code"].replace("src/adi_model/", ""),
            va="center",
            fontsize=8.7,
            color=MUTED,
        )
    ax.text(
        0.35,
        0.55,
        f"数据：mechanism_inventory.json（共 {len(mechanisms)} 项，图为选摘）",
        fontsize=9,
        color=MUTED,
    )
    finish(fig, "unified_2_sources.png")


def calibration():
    """Illustrate the structural RTL fit and independent holdout evidence."""
    report = json.loads((ROOT / "docs/readme/data/structural_calibration_summary.json").read_text())
    cases = {row["chip_seed"]: row for row in report["results"]}
    first, second = cases[42], cases[91]
    if any(
        row["noise_rms_uv"] != 0
        or not row["rtl_matches_independent_integer"]
        or not row["python_rtl_sample_switch_contract"]
        for row in (first, second)
    ):
        raise ValueError("structural calibration figure requires the verified noiseless cases")
    if any(
        first[key] != second[key] for key in ("training_samples", "holdout_samples", "matrix_rank")
    ):
        raise ValueError("structural calibration cases use incompatible sample counts or ranks")
    fig, ax = canvas(
        "结构 RTL 电容权重校准：从观测到独立复验",
        f'{report["sample_rate_hz"] / 1e6:g} MS/s 理想节拍 · 数字 RTL + 静态行为失配夹具',
    )
    blocks = [
        ("RTL 观测 + 参考", "开关掩码、ADC2 和已知输入"),
        ("片外权重拟合", f'{first["training_samples"]} 笔 · 秩 {first["matrix_rank"]}'),
        ("量化并装载", "Q30 权重 / Q32 标量"),
        ("RTL 数字重构", "装入冻结系数"),
        ("独立留出复验", f'{first["holdout_samples"]} 笔 · 逐码对拍'),
    ]
    for i, (title, detail) in enumerate(blocks):
        x = 0.25 + 2.7 * i
        box(ax, x, 4.0, 2.35, 1.55, title, detail, TEAL if i == 4 else BLUE)
        if i < len(blocks) - 1:
            arrow(ax, x + 2.37, x + 2.67, 4.77)
    box(
        ax,
        0.25,
        1.55,
        13.15,
        1.38,
        "静态夹具：校准前 → 校准后的留出误差标准差",
        f'种子 42：{first["before_error_std_uv"]:.2f} → {first["after_error_std_uv"]:.2f} µV'
        f'       种子 91：{second["before_error_std_uv"]:.2f} → {second["after_error_std_uv"]:.2f} µV',
        TEAL,
    )
    ax.text(
        0.35,
        0.75,
        "真实参考与观测传输、动态建立及 ADC2 非线性尚未闭合；这些数值不是芯片实测。",
        fontsize=10,
        color=MUTED,
    )
    finish(fig, "unified_3_calibration.png")


def dsp_bridge():
    """Illustrate the SAR-to-DSP data and fault interface."""
    fig, ax = canvas(
        "SAR 输出接入 DSP：码制与异常",
        "同一时钟域；输入事件 ID 在接口侧可观察，滤波/FIFO 输出尚不携带该 ID",
    )
    blocks = [
        ("SAR 重构输出", "码 + ID + flags + 模拟溢出"),
        ("接入层", "翻转最高位 / 故障门控"),
        ("DSP", "补码校正 · 滤波 · FIFO"),
        ("并行输出", "转回 offset-binary"),
    ]
    for i, (title, detail) in enumerate(blocks):
        x = 0.25 + 3.35 * i
        box(ax, x, 4.15, 2.95, 1.45, title, detail, TEAL if i == 1 else BLUE)
        if i < len(blocks) - 1:
            arrow(ax, x + 2.97, x + 3.32, 4.88)
    box(
        ax,
        0.25,
        1.55,
        6.35,
        1.55,
        "数字标志或模拟溢出 → 锁故障",
        "重启清空 DSP；模拟粘滞位须由 SAR 清除。",
        ORANGE,
    )
    box(
        ax,
        7.05,
        1.55,
        6.35,
        1.55,
        "串行输出仍为补码",
        "单 lane 20 位帧 / 16 周期采样：不能无损发送。",
        PURPLE,
    )
    finish(fig, "unified_4_dsp.png")


def provenance():
    """Summarize runtime parameter source grades."""
    counts = Counter(value[0].value for value in PARAM_GRADES.values())
    grades = [
        ("disclosed", "披露", TEAL),
        ("derived", "派生", BLUE),
        ("fitted", "拟合", PURPLE),
        ("assumed", "假设", ORANGE),
        ("research_extension", "原创扩展", "#a8a3b9"),
    ]
    fig, ax = canvas(
        "参数来源与验证边界",
        f"Config 参数来源分级 · 实时读取 PARAM_GRADES，共 {sum(counts.values())} 项",
    )
    x, total = 0.45, sum(counts.values())
    for key, _label, color in grades:
        width = 12.8 * counts[key] / total
        ax.barh(5.0, width, left=x, height=0.55, color=color)
        x += width
    for i, (key, label, color) in enumerate(grades):
        x = 0.45 + (i % 3) * 4.3
        y = 3.8 - (i // 3) * 0.68
        ax.scatter(x, y, s=95, color=color)
        ax.text(x + 0.20, y, f"{label}  {counts[key]}", va="center", fontsize=11)
    ax.text(
        0.45,
        1.65,
        "行为模型 / RTL 仿真 / 归档 FPGA 结果 / PDK 及 STA 是不同证据层级。",
        fontsize=11,
    )
    ax.text(
        0.45,
        1.05,
        "拟合或假设得到的数值不能直接当作论文测量值或芯片性能。",
        fontsize=10.5,
        color=MUTED,
    )
    finish(fig, "unified_5_evidence.png")


def error_spectrum(error, vfs=3.0):
    """One-sided rectangular-bin error spectrum in dBFS/bin."""
    n = len(error)
    power = np.abs(np.fft.rfft(error - np.mean(error)) / n) ** 2
    power[1:-1] *= 2
    return 10 * np.log10(np.maximum(power / (vfs * vfs / 2), 1e-20))


def calibration_effect():
    """Plot frozen 40 MS/s calibration before and after measurements."""
    data = np.load(ROOT / "docs/readme/data/calibration_40m.npz")
    meta = json.loads((ROOT / "docs/readme/data/calibration_40m.json").read_text())
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 7.2), layout="constrained")
    fig.suptitle("电容失配：标称权重与离线拟合权重的留出样本对照", fontsize=19, weight="bold")
    for i, (case, label) in enumerate(
        (("ideal_noise", "随机模拟噪声关闭"), ("noise_94p2", "采样 + RA 噪声开启"))
    ):
        row = meta["cases"][case]
        x = data[case + "_x_clean_v"]
        for key, color, name in (("before", ORANGE, "标称权重"), ("after", BLUE, "冻结拟合权重")):
            err = data[f"{case}_{key}_v"] - x
            axes[i, 0].plot(np.arange(256), err[:256] * 1e6, lw=0.8, color=color, label=name)
            axes[i, 1].plot(
                np.fft.rfftfreq(len(err), 1 / 40e6) / 1e6,
                error_spectrum(err),
                lw=0.6,
                color=color,
                label=name,
            )
        before, after = row["before_integer"], row["after_integer"]
        axes[i, 0].set(
            title=f"{label} · 误差标准差 {before['error_std_v']*1e6:.1f} → {after['error_std_v']*1e6:.1f} µV",
            xlabel="样本序号（前 256 点）",
            ylabel="输出误差 / µV",
        )
        axes[i, 1].set(
            title=f"输出 SNDR {before['SNDR_dB']:.2f} → {after['SNDR_dB']:.2f} dB",
            xlabel="频率 / MHz",
            ylabel="误差频谱 / dBFS/bin",
            ylim=(-170, -75),
        )
        for axis in axes[i]:
            axis.grid(alpha=0.18)
            axis.spines[["top", "right"]].set_visible(False)
        axes[i, 0].text(
            0.012,
            0.96,
            "A" if i == 0 else "C",
            transform=axes[i, 0].transAxes,
            fontsize=11,
            weight="bold",
            color=BLUE,
            va="top",
        )
        axes[i, 1].text(
            0.012,
            0.96,
            "B" if i == 0 else "D",
            transform=axes[i, 1].transAxes,
            fontsize=11,
            weight="bold",
            color=BLUE,
            va="top",
        )
    axes[0, 0].legend(fontsize=9, loc="upper right")
    fig.text(
        0.03,
        -0.01,
        "行为模型：40 MS/s，N=16384，2.2 V 峰值，σ=1000 ppm，seed 42；使用独立留出输入与精确外部训练参考。",
        fontsize=9,
        color=MUTED,
    )
    fig.text(
        0.03,
        -0.04,
        "94.2 dB 噪声预算含拟合参数；不是芯片实测，也不是对论文 DR 的独立预测。",
        fontsize=9,
        color=MUTED,
    )
    finish(fig, "unified_6_calibration_effect.png")


def interleave_effect():
    """Plot 40 MS/s interleaving spurs and aggregate error."""
    data = np.load(ROOT / "docs/readme/data/interleave_40m.npz")
    meta = json.loads((ROOT / "docs/readme/data/interleave_40m.json").read_text())
    rows = {row["condition"]: row for row in meta["rows"]}
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 7.2), layout="constrained")
    fig.suptitle("交织失配：随机调度改变杂散分布，但不等于校准", fontsize=19, weight="bold")
    for i, (prefix, title, spur) in enumerate(
        (
            ("offset", "slice 失调：σ = 50 µV", "spur_fs2_dbfs_bin"),
            ("slice_gain", "slice 电容/增益失配：σ = 1000 ppm", "spur_fs2_minus_fin_dbfs_bin"),
        )
    ):
        names = [prefix + "_rotate", prefix + "_permute"]
        for name, color, label in zip(
            names, (ORANGE, BLUE), ("固定 A/B", "随机 8-of-18"), strict=True
        ):
            r = rows[name]
            if r["analog_overflow_count"]:
                raise RuntimeError(f"Cannot use overflowed case {name}")
            err = data[name]
            axes[0, i].plot(
                np.fft.rfftfreq(len(err), 1 / r["fs_hz"]) / 1e6,
                error_spectrum(err),
                lw=0.7,
                color=color,
                label=label,
            )
            spur_bin = r["n"] // 2 if i == 0 else r["n"] // 2 - r["k"]
            spur_x = spur_bin * r["fs_hz"] / r["n"] / 1e6
            axes[0, i].scatter(
                spur_x, r[spur], s=50, facecolor="white", edgecolor=color, linewidth=1.5, zorder=4
            )
        axes[0, i].axvline(spur_x, color=MUTED, lw=0.7, ls="--", alpha=0.65)
        axes[0, i].text(
            spur_x - 0.3,
            -94,
            "fs/2" if i == 0 else "fs/2 − fin",
            ha="right",
            va="top",
            fontsize=9,
            color=MUTED,
        )
        axes[0, i].set(
            title=title, xlabel="频率 / MHz", ylabel="误差频谱 / dBFS/bin", ylim=(-180, -90)
        )
        axes[0, i].legend(fontsize=9)
        axes[0, i].grid(alpha=0.18)
        a, b = (rows[name] for name in names)
        axes[1, i].bar(
            [0, 1],
            [a["error_rms_v"] * 1e6, b["error_rms_v"] * 1e6],
            color=[ORANGE, BLUE],
            width=0.48,
        )
        axes[1, i].set(
            xticks=[0, 1], xticklabels=["固定 A/B", "随机 8-of-18"], ylabel="总误差 RMS / µV"
        )
        text = f"指定杂散：{a[spur]:.1f} → {b[spur]:.1f} dBFS/bin"
        axes[1, i].set_title(text + ("（fs/2）" if i == 0 else "（fs/2 − fin）"))
        for j, value in enumerate((a["error_rms_v"] * 1e6, b["error_rms_v"] * 1e6)):
            axes[1, i].text(j, value, f" {value:.2f}", ha="center", va="bottom", fontsize=10)
        axes[1, i].set_ylim(0, max(a["error_rms_v"], b["error_rms_v"]) * 1e6 * 1.22)
        for axis in (axes[0, i], axes[1, i]):
            axis.spines[["top", "right"]].set_visible(False)
        axes[0, i].text(
            0.012,
            0.96,
            "A" if i == 0 else "B",
            transform=axes[0, i].transAxes,
            fontsize=11,
            weight="bold",
            color=BLUE,
            va="top",
        )
        axes[1, i].text(
            0.012,
            0.96,
            "C" if i == 0 else "D",
            transform=axes[1, i].transAxes,
            fontsize=11,
            weight="bold",
            color=BLUE,
            va="top",
        )
    fig.text(
        0.03,
        -0.01,
        "行为模型：40 MS/s，N=2048，fin≈2.949 MHz，2.2 V 峰值，seed 42，随机噪声关闭。",
        fontsize=9,
        color=MUTED,
    )
    fig.text(
        0.03,
        -0.04,
        "两种调度都未估计或消除失配；指定杂散下降时，本例的总误差 RMS 仍增大。",
        fontsize=9,
        color=MUTED,
    )
    finish(fig, "unified_7_interleave_effect.png")


if __name__ == "__main__":
    setup()
    architecture()
    matrix()
    calibration()
    dsp_bridge()
    provenance()
    calibration_effect()
    interleave_effect()
