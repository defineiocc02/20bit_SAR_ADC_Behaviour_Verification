#!/usr/bin/env python3
"""只读取冻结数据，重绘首页四图；不重新仿真、拟合或训练。

输入为 --data 目录中的名义随机化合同、结构 DUT 留出记录、三种 Python
调度的最终整数码功率谱，以及完整 dither 幅度网格。默认读取随库 data/。
命令会覆盖 --out 中同名原创 PNG/SVG，并记录数据和绘图源码摘要。

校准图检验均值/标准差/RMS的区别；交织图同时保留杂散和非基波残余指标；
dither图保留失败点及所有节点的计数，不根据最终clip删除输入点。
重绘退出成功只证明冻结数据、指标和图表达自洽，不证明真实芯片性能。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle

HERE = Path(__file__).resolve().parent
BLUE, ORANGE, TEAL = "#27658e", "#cf8535", "#218578"
INK, MUTED, PALE, GRAY = "#203044", "#5a6878", "#edf2f6", "#81909f"
FIGURES = ("architecture", "calibration", "interleave", "dither_headroom")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _setup() -> None:
    """选择已有中文字体，不安装或修改字体环境。"""
    for font in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(font, fallback_to_default=False)
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("重绘需要已有中文字体；本工具不会安装字体")
    plt.rcParams.update(
        {
            "font.sans-serif": [font, "DejaVu Sans"],
            "font.size": 10.5,
            "axes.labelsize": 10.5,
            "axes.titlesize": 12,
            "axes.titleweight": 600,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.unicode_minus": False,
            "text.color": INK,
            "axes.labelcolor": INK,
            "svg.fonttype": "path",
            "svg.hashsalt": "sar-adc-current-overview-v1",
        }
    )


def _save(fig, destination: Path) -> None:
    for suffix in ("png", "svg"):
        path = destination.with_suffix(f".{suffix}")
        fig.savefig(
            path,
            dpi=200,
            facecolor="white",
            metadata={"Creator": "SAR ADC project original figure", "Date": None}
            if suffix == "svg"
            else {"Software": "SAR ADC project original figure"},
        )
        if suffix == "svg":
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n"
            )
    plt.close(fig)


def _arrow(ax, points, color=INK, dashed=False, width=1.3) -> None:
    for start, end in pairwise(points[:-1]):
        ax.plot(*zip(start, end, strict=True), color=color, lw=width, ls="--" if dashed else "-")
    ax.add_patch(
        FancyArrowPatch(
            points[-2],
            points[-1],
            arrowstyle="-|>",
            mutation_scale=12,
            color=color,
            lw=width,
            linestyle="--" if dashed else "-",
        )
    )


def _box(ax, x, y, w, h, title, detail, color=BLUE, dashed=False) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle="round,pad=0.025,rounding_size=0.08",
            facecolor="white",
            edgecolor=color,
            linewidth=1.5,
            linestyle="--" if dashed else "-",
        )
    )
    ax.text(x + w / 2, y + h * 0.65, title, ha="center", va="center", fontsize=11.5, weight=600)
    ax.text(x + w / 2, y + h * 0.28, detail, ha="center", va="center", fontsize=10, color=MUTED)


def architecture(data: Path, out: Path) -> dict:
    """按冻结名义合同画结构；实线连接不代表晶体管网表已恢复。"""
    profile = _json(data / "randomization_contract.json")["profile"]
    ns, active = int(profile["n_slices"]), int(profile["n_active"])
    spare = ns - 2 * active
    if (ns, active, spare) != (18, 8, 2):
        raise ValueError("本结构图对应18/8/8/2合同；其他几何需要重新审查图示")
    fig, ax = plt.subplots(figsize=(13.0, 5.8))
    fig.subplots_adjust(left=0.02, right=0.99, bottom=0.06, top=0.86)
    ax.set(xlim=(0, 14.2), ylim=(0, 6.3))
    ax.axis("off")
    fig.suptitle(
        "数字校准在信号链中的作用点",
        x=0.04,
        ha="left",
        fontsize=17,
        weight=600,
    )
    fig.text(
        0.04,
        0.895,
        "工程示意｜蓝框：模拟行为／接口　绿框：数字逻辑　灰色虚线框：输出原型",
        color=MUTED,
        fontsize=11,
    )
    _box(ax, 0.55, 2.7, 2.25, 1.1, f"{ns} 切片电容池", "采样／保持，按样本号归属")
    ax.text(0.10, 3.25, "输入", ha="left", va="center", fontsize=10)
    _arrow(ax, [(0.1, 3.05), (0.5, 3.05)])
    x = 0.57
    for count, color, label in (
        (active, BLUE, "转换"),
        (active, ORANGE, "采集"),
        (spare, GRAY, "待选"),
    ):
        width = 2.2 * count / ns
        ax.add_patch(Rectangle((x, 2.38), width, 0.19, color=color, lw=0))
        ax.text(x + width / 2, 2.18, f"{count}{label}", ha="center", fontsize=9.5, color=color)
        x += width
    _box(ax, 3.35, 4.45, 1.8, 0.9, "前级量化器 SADC", "独立采样／粗码 c₁")
    _box(ax, 5.70, 4.45, 1.9, 0.9, "残差数模转换器", "实际电容选择 RDAC")
    # The quantizer and RDAC sample the same input through distinct branches;
    # the quantizer does not consume the charge stored on the RDAC slice pool.
    _arrow(ax, [(0.3, 3.05), (0.3, 5.55), (3.08, 5.55), (3.08, 4.9), (3.32, 4.9)], BLUE)
    ax.text(
        1.65,
        5.94,
        "前级独立采样支路\n采样组／相位／样本号／响应匹配",
        ha="center",
        va="center",
        fontsize=9.5,
        color=BLUE,
    )
    _arrow(ax, [(5.18, 4.9), (5.67, 4.9)], BLUE)
    ax.text(5.42, 5.04, "c₁", ha="center", fontsize=10)
    ax.add_patch(Circle((5.6, 3.25), 0.22, facecolor="white", edgecolor=BLUE, lw=1.5))
    _arrow(ax, [(2.83, 3.25), (5.36, 3.25)], BLUE)
    ax.text(3.95, 3.43, "保存电压／电荷", ha="center", fontsize=10, color=BLUE)
    ax.text(5.23, 3.06, "+", fontsize=11, color=BLUE)
    _arrow(ax, [(6.65, 4.42), (6.65, 4.0), (5.6, 4.0), (5.6, 3.5)], BLUE)
    ax.text(5.93, 4.10, r"$-V_D$", fontsize=10, color=BLUE, ha="center")
    _box(ax, 6.10, 2.7, 2.30, 1.1, "共享残差放大器 RA", "＋ 后级量化器 ADC2")
    _arrow(ax, [(5.84, 3.25), (6.07, 3.25)], BLUE)
    _box(ax, 8.90, 2.7, 2.15, 1.1, "数字权重重构", "按单次观测重构20位输出", TEAL)
    _arrow(ax, [(8.43, 3.25), (8.87, 3.25)], BLUE)
    ax.text(8.65, 3.47, "细码", ha="center", fontsize=9.5)
    _box(ax, 11.55, 2.7, 2.1, 1.1, "输出数字处理 DSP", "滤波／队列缓存／接口原型", GRAY, True)
    _arrow(ax, [(11.08, 3.25), (11.52, 3.25)], TEAL)
    _arrow(ax, [(7.63, 4.92), (9.98, 4.92), (9.98, 3.84)], GRAY, True)
    ax.text(9.25, 5.12, "粗码／选通／切片号／样本号", ha="center", fontsize=10, color=MUTED)
    # These arrows identify control/injection locations, not an electrical layout.
    ax.text(1.67, 4.54, "切片调度：只选空闲组", ha="center", fontsize=10.5, color=ORANGE)
    _arrow(ax, [(1.67, 4.30), (1.67, 3.84)], ORANGE)
    ax.text(4.25, 5.96, "量化器扰动 dQ", ha="center", fontsize=10.5, color=ORANGE)
    _arrow(ax, [(4.25, 5.73), (4.25, 5.39)], ORANGE)
    ax.text(6.65, 5.96, "动态元件匹配（DEM）", ha="center", fontsize=10.5, color=TEAL)
    _arrow(ax, [(6.65, 5.73), (6.65, 5.39)], TEAL)
    ax.text(3.87, 1.65, "采样扰动：采样轨／保存电荷", ha="center", fontsize=10.5, color=ORANGE)
    _arrow(ax, [(3.87, 1.88), (3.87, 3.18)], ORANGE)
    _box(ax, 8.90, 0.50, 2.15, 1.05, "片外训练／固定系数", "独立已知输入 ＋ 数字观察", ORANGE)
    _box(ax, 11.55, 0.50, 2.1, 1.05, "片上应用系数", "Q30权重／Q32标量", TEAL)
    _arrow(ax, [(8.65, 3.15), (8.65, 1.03), (8.87, 1.03)], GRAY, True)
    ax.text(8.46, 2.02, "原始观测\n选通／样本号", ha="right", fontsize=9.5, color=MUTED)
    _arrow(ax, [(11.08, 1.03), (11.52, 1.03)], ORANGE)
    _arrow(ax, [(12.6, 1.58), (12.6, 2.07), (9.98, 2.07), (9.98, 2.66)], TEAL)
    ax.text(11.15, 2.20, "装载固定系数", ha="center", fontsize=9.2, color=TEAL)
    fig.text(
        0.04,
        0.02,
        "18个切片（slice）是电容资源，非18个独立ADC；扰动按配置／模式启用；可选斩波自归零见专题。原创工程示意。",
        fontsize=10,
        color=MUTED,
    )
    _save(fig, out / "current_architecture")
    return {
        "evidence": "project_concept_structure",
        "slice_pool": ns,
        "converting": active,
        "acquiring": active,
        "spare": spare,
    }


def calibration(data: Path, out: Path) -> dict:
    """从真实DUT留出码计算误差；检查统计摘要，不拟合或删样本。"""
    summary = _json(data / "structural_calibration_summary.json")
    row = next(r for r in summary["results"] if r["chip_seed"] == 42)
    with np.load(data / "structural_calibration_holdout.npz") as a:
        x, before, after, ids = (
            a[f"chip42_{key}"].copy() for key in ("x_v", "before_code", "after_code", "sample_id")
        )
    if not (len(x) == len(before) == len(after) == len(ids) == row["holdout_samples"]):
        raise ValueError("DUT留出输入/码/ID长度不一致")
    if np.any(~np.isfinite(x)) or any(np.any((v < 0) | (v >= 2**20)) for v in (before, after)):
        raise ValueError("留出输入或20位offset-binary字非法")
    errors = [(-3 + (v.astype(np.int64) + 0.5) * 6 / 2**20 - x) * 1e6 for v in (before, after)]
    measured = [
        {
            "mean_uv": float(e.mean()),
            "std_uv": float(e.std()),
            "rms_uv": float(np.sqrt(np.mean(e**2))),
        }
        for e in errors
    ]
    if not np.allclose(
        [r["std_uv"] for r in measured],
        [row["before_error_std_uv"], row["after_error_std_uv"]],
        rtol=1e-10,
        atol=1e-10,
    ):
        raise ValueError("留出码统计与摘要不一致")
    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.8), gridspec_kw={"width_ratios": [1.8, 1]})
    fig.subplots_adjust(left=0.08, right=0.98, top=0.79, bottom=0.29, wspace=0.24)
    fig.suptitle(
        f"校准前后误差标准差：{measured[0]['std_uv']:.2f} → {measured[1]['std_uv']:.2f} µV",
        x=0.04,
        ha="left",
        fontsize=18,
        weight=600,
    )
    fig.text(
        0.04,
        0.875,
        f"同一留出记录，仅改变重构系数｜固定失配行为模型；seed 42；训练{row['training_samples']}／独立留出{row['holdout_samples']}；外加噪声{row['noise_rms_uv']:g} µV RMS",
        fontsize=10.5,
        color=MUTED,
    )
    limit = np.ceil(max(abs(e).max() for e in errors) * 1.12 / 25) * 25
    zoom = np.ceil(abs(errors[1]).max() * 1.15)
    for e, color, label in zip(errors, (ORANGE, BLUE), ("未校准", "已校准"), strict=True):
        axes[0].scatter(x, e, s=9, alpha=0.65, color=color, label=label, rasterized=False)
    axes[0].axhspan(-zoom, zoom, color=BLUE, alpha=0.08, lw=0)
    axes[0].set(ylim=(-limit, limit), title="同一纵轴：未校准／已校准")
    axes[0].legend(frameon=False, fontsize=11, loc="upper left")
    axes[1].scatter(x, errors[1], s=13, alpha=0.75, color=BLUE)
    axes[1].set(ylim=(-zoom, zoom), title="已校准局部放大（纵轴改变）")
    for axis in axes:
        axis.axhline(0, color=GRAY, lw=0.6)
        axis.set(xlim=(-3, 3), xlabel="独立已知留出输入（V）", ylabel="最终码中心 − 已知输入（µV）")
        axis.grid(alpha=0.2, lw=0.6)
        axis.set_axisbelow(True)
    for y, stats, color, label in zip(
        (0.16, 0.105), measured, (ORANGE, BLUE), ("未校准", "已校准"), strict=True
    ):
        fig.text(
            0.085,
            y,
            f"{label}：均值 {stats['mean_uv']:.2f} µV　标准差 {stats['std_uv']:.2f} µV　RMS {stats['rms_uv']:.2f} µV",
            color=color,
            fontsize=11,
        )
    fig.text(
        0.085,
        0.035,
        "结构数字逻辑＋失配行为模型；全部样本保留，按20位偏移二进制码中心计算。非全码INL/DNL或芯片实测。",
        fontsize=10,
        color=MUTED,
    )
    _save(fig, out / "current_calibration")
    return {
        "evidence": "structural_digital_DUT_static_plant_holdout",
        "chip_seed": 42,
        "samples": len(x),
        "statistics": measured,
        "voltage_contract": {"fullscale_peak_v": 3, "output_bits": 20},
    }


def interleave(data: Path, out: Path) -> dict:
    """画相同物理时偏的三种Python调度；不将规则模型称作SV频谱。"""
    summary = _json(data / "interleave_40m.json")["timing_comparison"]
    names = ("fixed", "generic_random", "rtl_allocator")
    rows = [next(r for r in summary["rows"] if r["condition"] == name) for name in names]
    if (
        len({r["skew_sha256"] for r in rows}) != 1
        or len({r["clean_reference_sha256"] for r in rows}) != 1
    ):
        raise ValueError("三种调度没有共享相同输入和固定时偏")
    common = ("fs_hz", "n", "k", "fin_hz", "amplitude_v_peak", "skew_sigma_parameter_s")
    if any(len({r[key] for r in rows}) != 1 for key in common):
        raise ValueError("三种调度的采样、输入或时偏条件不一致")
    for row in rows:
        if row["output_domain"] != "final_20_bit_offset_binary_bin_center_V":
            raise ValueError("首页频谱要求最终整数码域")
    colors = (ORANGE, BLUE, TEAL)
    labels = ("固定A/B", "通用随机", "RTL规则Python策略")
    fig = plt.figure(figsize=(12.1, 6.1))
    grid = fig.add_gridspec(2, 3, width_ratios=(1, 1, 1.15))
    fig.subplots_adjust(left=0.075, right=0.98, top=0.75, bottom=0.25, wspace=0.38, hspace=0.58)
    spectral = fig.add_subplot(grid[:, :2])
    sndr = fig.add_subplot(grid[0, 2])
    sfdr = fig.add_subplot(grid[1, 2])
    with np.load(data / "interleave_timing_40m.npz") as a:
        frequency = a["frequency_hz"]
        expected_frequency = np.fft.rfftfreq(rows[0]["n"], 1 / rows[0]["fs_hz"])
        if not np.array_equal(frequency, expected_frequency):
            raise ValueError("频谱轴与记录长度/采样率不一致")
        for row, color, label, name in zip(rows, colors, labels, names, strict=True):
            power = a[name + "_power"]
            if power.shape != frequency.shape or np.any(~np.isfinite(power)) or np.any(power < 0):
                raise ValueError("功率谱形状或取值非法")
            index = row["n"] // 2 - row["k"]
            measured = 10 * np.log10(max(float(power[index]), 1e-300))
            if not np.isclose(measured, row["spur_fs2_minus_fin_dbfs_bin"], atol=1e-10, rtol=0):
                raise ValueError("冻结谱的指定杂散与指标摘要不一致")
            excluded = np.ones(len(power), dtype=bool)
            excluded[[0, row["k"]]] = False
            sfdr_db = 10 * np.log10(power[row["k"]] / power[excluded].max())
            sndr_db = 20 * np.log10(row["fitted_fundamental_rms_v"] / row["fit_residual_rms_v"])
            if not np.allclose(
                [sndr_db, sfdr_db], [row["SNDR_dB"], row["SFDR_dBc"]], rtol=0, atol=1e-7
            ):
                raise ValueError("频谱/拟合残余与SNDR或SFDR摘要不一致")
            spectral.plot(
                frequency / 1e6,
                10 * np.log10(np.maximum(power, 1e-300)),
                color=color,
                label=label,
                lw=0.85,
                alpha=0.88,
            )
    spectral.set(
        xlim=(0, rows[0]["fs_hz"] / 2e6),
        ylim=(-165, 5),
        xlabel="频率（MHz）",
        ylabel="单边功率（dBFS/bin）",
    )
    spectral.legend(frameon=False, fontsize=10.5, loc="upper right")
    spectral.axvline((rows[0]["fs_hz"] / 2 - rows[0]["fin_hz"]) / 1e6, color=GRAY, ls=":", lw=0.7)
    spectral.text(
        0.98,
        0.52,
        "虚线：fs/2 − fin",
        transform=spectral.transAxes,
        ha="right",
        color=MUTED,
        fontsize=10,
    )
    for axis, key, title, ylim in (
        (sndr, "SNDR_dB", "SNDR（dB）", (80, 100)),
        (sfdr, "SFDR_dBc", "SFDR（dBc）", (90, 116)),
    ):
        values = [r[key] for r in rows]
        axis.scatter(np.arange(3), values, c=colors, s=45, zorder=3)
        for i, v in enumerate(values):
            axis.text(i, v + 0.9, f"{v:.2f}", ha="center", fontsize=11)
        axis.set(
            title=title,
            xlim=(-0.45, 2.45),
            ylim=ylim,
            xticks=np.arange(3),
            xticklabels=("固定", "通用随机", "RTL规则"),
        )
        axis.tick_params(axis="x", labelsize=10)
    for axis in (spectral, sndr, sfdr):
        axis.grid(axis="y", alpha=0.22, lw=0.6)
        axis.set_axisbelow(True)
    delta_sfdr = rows[2]["SFDR_dBc"] - rows[0]["SFDR_dBc"]
    delta_sndr = rows[2]["SNDR_dB"] - rows[0]["SNDR_dB"]
    fig.suptitle("按RTL规则的Python策略，相对固定A/B", x=0.04, ha="left", fontsize=17, weight=600)
    fig.text(
        0.04,
        0.875,
        f"SFDR {delta_sfdr:+.2f} dB　／　SNDR {delta_sndr:+.2f} dB".replace("-", "−"),
        fontsize=21,
        weight=600,
    )
    fig.text(
        0.04,
        0.81,
        f"均为Python结果，非SV DUT频谱｜{rows[0]['fs_hz']/1e6:g} MS/s／{rows[0]['fin_hz']/1e6:.6f} MHz／{rows[0]['amplitude_v_peak']:g} V峰值／N={rows[0]['n']}／相干矩形窗／同一时偏池，标称σ={rows[0]['skew_sigma_parameter_s']*1e12:g} ps",
        fontsize=10.5,
        color=MUTED,
    )
    ref = "／".join(f"{r['error_rms_v']*1e6:.2f}" for r in rows)
    residual = "／".join(f"{r['fit_residual_rms_v']*1e6:.2f}" for r in rows)
    fig.text(
        0.075,
        0.13,
        f"参考误差RMS：{ref} µV；拟合残差RMS：{residual} µV。",
        fontsize=10.8,
    )
    fig.text(
        0.075,
        0.06,
        "固定／通用随机／RTL规则顺序；拟合仅评估指标，未校正输出。全频点保留；杂散改善不等于总误差下降。",
        fontsize=10.5,
        color=MUTED,
    )
    _save(fig, out / "current_interleave")
    return {
        "evidence": "Python_final_integer_code_timing_experiment",
        "conditions": names,
        "sndr_db": [r["SNDR_dB"] for r in rows],
        "sfdr_dbc": [r["SFDR_dBc"] for r in rows],
        "reference_rms_uv": [r["error_rms_v"] * 1e6 for r in rows],
        "fit_residual_rms_uv": [r["fit_residual_rms_v"] * 1e6 for r in rows],
    }


def dither_headroom(data: Path, out: Path) -> dict:
    """保留完整幅度网格，分别画非互斥节点计数。"""
    summary = _json(data / "dither_headroom.json")
    amps = summary["amplitudes_v"]
    modes = ("off", "sampling", "quantizer")
    if len(summary["cells"]) != len(amps) * len(modes):
        raise ValueError("dither完整幅度网格缺失")
    expected = {(mode, amp) for mode in modes for amp in amps}
    if {(r["dither_mode"], r["input_peak_v"]) for r in summary["cells"]} != expected:
        raise ValueError("dither幅度网格包含重复、遗漏或未知条件")
    groups = {
        mode: [
            next(
                r for r in summary["cells"] if r["dither_mode"] == mode and r["input_peak_v"] == amp
            )
            for amp in amps
        ]
        for mode in modes
    }
    if any(r["retained_samples"] != summary["N"] for group in groups.values() for r in group):
        raise ValueError("幅度扫描删除了输入或输出样本")
    chosen = next(
        r for r in groups["quantizer"] if np.isclose(r["input_peak_v"], 2.99, rtol=0, atol=1e-12)
    )
    values = [
        chosen["sadc_low_outside_count"] + chosen["sadc_high_outside_count"],
        chosen["ra_sat_count"],
        chosen["adc2_over_count"],
        chosen["rdac_over_count"],
        chosen["digital_clipped_low_count"] + chosen["digital_clipped_high_count"],
    ]
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 5.0), gridspec_kw={"width_ratios": [1.2, 1]})
    fig.subplots_adjust(left=0.08, right=0.98, top=0.78, bottom=0.29, wspace=0.27)
    for mode, label, color, marker, size in (
        ("off", "关闭扰动", GRAY, "o", 8),
        ("sampling", "采样端注入", BLUE, "x", 6),
        ("quantizer", "量化器注入", ORANGE, "s", 5),
    ):
        axes[0].plot(
            amps,
            [r["adc2_over_count"] for r in groups[mode]],
            color=color,
            marker=marker,
            ms=size,
            lw=1.2,
            label=label,
            markerfacecolor="none" if mode == "off" else color,
        )
    axes[0].set(
        title="完整幅度扫描：后级量化器越界",
        xlabel="原始输入峰值（V）",
        ylabel=f"溢出次数／{summary['N']}笔",
        xlim=(min(amps) - 0.01, max(amps) + 0.01),
        ylim=(-1, None),
    )
    axes[0].legend(frameon=False, fontsize=10.5, loc="upper left")
    axes[0].axvline(2.99, color=GRAY, ls=":", lw=0.7)
    axes[0].annotate(
        f"2.99 V：{chosen['adc2_over_count']}次越界",
        xy=(2.99, chosen["adc2_over_count"]),
        xytext=(2.79, 21),
        fontsize=12,
        weight=600,
        color=ORANGE,
        arrowprops={"arrowstyle": "->", "color": ORANGE, "lw": 1.1},
    )
    axes[1].bar(np.arange(5), values, color=(ORANGE, ORANGE, ORANGE, GRAY, GRAY), width=0.62)
    for i, value in enumerate(values):
        axes[1].text(i, value + 0.65, str(value), ha="center", fontsize=11)
    axes[1].set(
        title="量化器注入：2.99 V逐节点定位",
        ylabel=f"次数／{summary['N']}笔",
        xticks=np.arange(5),
        xticklabels=(
            "前级量化器\n输入越界",
            "残差放大器\n饱和",
            "后级量化器\n越界",
            "残差数模器\n码越界",
            "最终码\n限幅",
        ),
        ylim=(0, max(values) * 1.25),
    )
    axes[1].tick_params(axis="x", labelsize=10)
    for axis in axes:
        axis.grid(axis="y", alpha=0.2, lw=0.6)
        axis.set_axisbelow(True)
    fig.suptitle(
        "dither 2.99 V失败点：问题已定位，后级越界仍存在",
        x=0.04,
        ha="left",
        fontsize=18,
        weight=600,
    )
    fig.text(
        0.04,
        0.88,
        f"理想器件／结构范围配置；{summary['common_config']['fs']/1e6:g} MS/s／{summary['fin_hz']/1e6:.6f} MHz／N={summary['N']}；无外加随机噪声。",
        fontsize=10.5,
        color=MUTED,
    )
    upper = next(r for r in groups["sampling"] if r["input_peak_v"] == max(amps))
    clips = upper["digital_clipped_low_count"] + upper["digital_clipped_high_count"]
    fig.text(
        0.08,
        0.15,
        f"{max(amps):.1f} V端点保留：采样扰动模式最终码裁剪{clips}次；关闭扰动的上端点计数不等同新增电气故障。",
        fontsize=10.5,
        color=MUTED,
    )
    fig.text(
        0.08,
        0.09,
        "节点次数可重叠，不能相加；最终码未限幅，不代表模拟节点未越界。",
        fontsize=11,
    )
    fig.text(
        0.08,
        0.035,
        "仅覆盖该相干幅度网格，非全范围保证、完整INL/DNL或芯片实测；所有工况与样本保留。",
        fontsize=10,
        color=MUTED,
    )
    _save(fig, out / "current_dither_headroom")
    return {
        "evidence": "Python_ideal_plant_headroom_sweep",
        "cells": len(summary["cells"]),
        "N": summary["N"],
        "selected_peak_v": 2.99,
        "selected_node_counts": dict(
            zip(
                ("SADC_input_outside", "RA_sat", "ADC2_over", "RDAC_over", "digital_clip"),
                values,
                strict=True,
            )
        ),
    }


def main() -> None:
    """重绘选定图片，记录数据摘要与输出；不运行模型或训练器。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="明确的输出目录；覆盖其中同名图")
    parser.add_argument("--data", type=Path, default=HERE / "data", help="冻结JSON/NPZ数据目录")
    parser.add_argument("--only", nargs="+", choices=FIGURES, default=list(FIGURES))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    _setup()
    renderers = {
        "architecture": architecture,
        "calibration": calibration,
        "interleave": interleave,
        "dither_headroom": dither_headroom,
    }
    requirements = {
        "architecture": ["randomization_contract.json"],
        "calibration": [
            "structural_calibration_summary.json",
            "structural_calibration_holdout.npz",
        ],
        "interleave": ["interleave_40m.json", "interleave_timing_40m.npz"],
        "dither_headroom": ["dither_headroom.json"],
    }
    inputs = sorted({p for name in args.only for p in requirements[name]})
    before = {name: _sha(args.data / name) for name in inputs}
    records = {name: renderers[name](args.data, args.out) for name in args.only}
    if any(_sha(args.data / name) != before[name] for name in inputs):
        raise RuntimeError("重绘期间输入发生变化；冻结数据后重新运行，不发布本次图")
    manifest = {
        "artifact": "original_README_overview_redraw",
        "operation": "frozen_data_redraw_only_no_simulation_or_training",
        "renderer_sha256": _sha(Path(__file__).resolve()),
        "render_runtime": {
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
            "font": plt.rcParams["font.sans-serif"][0],
        },
        "selected": args.only,
        "inputs": {
            name: {"sha256": before[name], "bytes": (args.data / name).stat().st_size}
            for name in inputs
        },
        "figures": records,
        "outputs": {
            f"current_{name}.{suffix}": _sha(args.out / f"current_{name}.{suffix}")
            for name in args.only
            for suffix in ("png", "svg")
        },
    }
    (args.out / "current_figures_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"redrawn": args.only, "out": str(args.out), "simulation_rerun": False},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
