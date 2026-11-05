#!/usr/bin/env python3
"""从保留的全 ADC 实验记录绘制原创 CAAZ 说明图。

数值记录由 tools/reproduce_caaz.py 的实验流程生成；第二幅图使用独立于 ADC
噪声拟合的两样本解析响应。时序图另读保留的 RTL 上下文 CSV。
本脚本只重绘记录，不复制第三方图片的像素或几何形状。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
BLUE, RED, GREEN = "#0072B2", "#D55E00", "#009E73"


def _save(figure, destination: Path) -> None:
    for extension in ("png", "svg"):
        figure.savefig(
            destination.with_suffix(f".{extension}"),
            dpi=220,
            facecolor="white",
            metadata={"Date": None} if extension == "svg" else None,
        )
        if extension == "svg":
            path = destination.with_suffix(".svg")
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text().splitlines()) + "\n"
            )
    plt.close(figure)


def _draw_rtl_context(trace_file: Path, destination: Path) -> None:
    trace = np.genfromtxt(trace_file, delimiter=",", names=True)
    # 场景 2 是无故障记录；场景 0、1 含有意注入的故障。
    rows = trace[(trace["scenario"] == 2) & (trace["current_id"] >= 2) & (trace["current_id"] <= 8)]
    if not len(rows) or np.any(rows["pair_fault"]):
        raise ValueError("RTL timing illustration requires the clean, observed scenario")
    time_ns = rows["time_ns"] - rows["time_ns"][0]
    figure, axes = plt.subplots(4, 1, figsize=(10.2, 7.5), sharex=True, layout="constrained")
    for offset, column, color, label in (
        (0, "acquiring_chop", RED, "Acquiring input"),
        (2, "current_chop", BLUE, "Current RA"),
        (4, "fine_chop", GREEN, "Previous residue / fine SAR"),
    ):
        axes[0].step(time_ns, rows[column] + offset, where="post", color=color, label=label)
    axes[0].set(yticks=[], ylabel="Chop tags\n0: positive; 1: negative")
    axes[0].legend(loc="upper left", ncol=3, fontsize=8, framealpha=0.95)
    axes[1].step(
        time_ns,
        rows["legacy_ra_az"],
        where="post",
        color=BLUE,
        label="Legacy RA preparation: every cycle",
    )
    axes[1].step(
        time_ns,
        rows["az_capture"] + 2,
        where="post",
        color=RED,
        label="Separate CBIAS capture: even only",
    )
    axes[1].set(yticks=[], ylabel="Independent\nmacro controls", ylim=(-0.2, 3.7))
    axes[1].legend(loc="upper left", ncol=2, fontsize=8, framealpha=0.95)
    for column, color, label in (
        ("current_id", BLUE, "Current residue ID"),
        ("fine_id", RED, "Previous fine ID"),
    ):
        axes[2].step(time_ns, rows[column], where="post", color=color, label=label)
    axes[2].set(ylabel="Sample ID")
    axes[2].legend(loc="upper left", ncol=2, fontsize=8, framealpha=0.95)
    for valid, identifier, color, marker, label in (
        ("raw_valid", "raw_id", BLUE, "o", "Raw result: 40 MS/s budget"),
        ("pair_valid", "pair_last_id", RED, "s", "Pair result: 20 MS/s budget, last ID"),
    ):
        selected = rows[valid] == 1
        axes[3].vlines(time_ns[selected], 0, rows[identifier][selected], color=color, lw=0.9)
        axes[3].scatter(
            time_ns[selected],
            rows[identifier][selected],
            color=color,
            marker=marker,
            s=24,
            label=label,
        )
    axes[3].set(ylabel="Result ID", xlabel="Time from observed excerpt start (ns)", ylim=(0, 7))
    axes[3].legend(loc="upper left", ncol=2, fontsize=8, framealpha=0.95)
    for axis in axes:
        axis.grid(alpha=0.2, lw=0.6)
        axis.set_axisbelow(True)
    figure.suptitle(
        "Actual structural RTL: acquisition, residue and fine-result ownership\n"
        "Ideal 640 MHz simulation clock; engineering macro-control contract, not analog settling or STA",
        fontsize=11,
    )
    _save(figure, destination / "caaz_rtl_context")


def draw(data_file: Path, destination: Path) -> None:
    """将保留的误差记录、保持敏感性和带宽代价绘制到指定目录。

    同名 PNG/SVG 会被覆盖；绘图过程不重新运行 ADC。

    Args:
        data_file: 含测量结果、解析预测及假设的摘要 JSON 路径。
        destination: 图片输出目录；不存在时创建。
    """
    data = json.loads(data_file.read_text(encoding="utf-8"))
    if data["evidence_level"] != "SAMPLE_DOMAIN_BEHAVIORAL_SIMULATION":
        raise ValueError("CAAZ figures require the declared experiment evidence")
    destination.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "Arial",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "svg.fonttype": "none",
            "svg.hashsalt": "caaz_original_figures_v1",
            "axes.titleweight": "bold",
            "axes.labelcolor": "#273342",
            "text.color": "#273342",
        }
    )
    figure, axes = plt.subplots(2, 2, figsize=(11.4, 7.2), layout="constrained")
    ordinary = data["cases"]["conventional_az"]
    caaz = data["cases"]["chopping_assisted"]
    axis = axes[0, 0]
    for row, color, label in (
        (ordinary, RED, "Conventional: refresh every raw sample"),
        (caaz, BLUE, "CAAZ: refresh even, hold odd"),
    ):
        values = row["display_az_error_mv"]
        axis.step(
            np.arange(len(values) + 1),
            values + values[-1:],
            where="post",
            color=color,
            label=label,
            linewidth=1.5,
        )
    for start in range(0, 16, 4):
        axis.axvspan(start, start + 2, color=BLUE, alpha=0.06, lw=0)
    axis.set(
        title="A  One AZ realization is shared by each pair",
        xlabel="Raw sample index (positive even / negative odd in CAAZ)",
        ylabel="Injected AZ error at RA output (mV)",
        xlim=(0, 16),
        xticks=np.arange(0, 17, 2),
    )
    axis.legend(frameon=False, fontsize=8, loc="upper left")
    axis = axes[0, 1]
    for row, color, label in ((ordinary, RED, "Conventional paired"), (caaz, BLUE, "CAAZ paired")):
        axis.plot(row["display_pair_error_uv"], color=color, lw=0.9, alpha=0.85, label=label)
    axis.set(
        title="B  Error after actual ADC2 and 20-bit reconstruction",
        xlabel="Pair index (20 MS/s)",
        ylabel="Output error vs matched pair reference (µV)",
    )
    axis.legend(frameon=False, fontsize=8, loc="upper right")
    axis = axes[1, 0]
    rows = [(ordinary, "raw"), (ordinary, "pair"), (caaz, "pair")]
    measured = [row[domain]["std_v"] * 1e6 for row, domain in rows]
    predicted = [row[f"continuous_linear_prediction_{domain}_std_v"] * 1e6 for row, domain in rows]
    axis.bar(np.arange(3), measured, color=(RED, "#E69F00", BLUE), width=0.58)
    axis.scatter(
        np.arange(3),
        predicted,
        marker="_",
        color="#202020",
        s=250,
        lw=1.5,
        label="Continuous linear prediction",
    )
    for index, value in enumerate(measured):
        axis.text(index, value + 1.4, f"{value:.2f}", ha="center", fontsize=10)
    axis.set(
        title="C  Separate ordinary averaging from AZ cancellation",
        ylabel="Input-referred error standard deviation (µV)",
        xticks=np.arange(3),
        xticklabels=[
            "Conventional\nraw 40 MS/s",
            "Conventional\npair 20 MS/s",
            "CAAZ\npair 20 MS/s",
        ],
        ylim=(0, max(measured) * 1.28),
    )
    axis.legend(frameon=False, fontsize=8)
    axis = axes[1, 1]
    rows = data["hold_sensitivity"]
    retention = [row["retention"] for row in rows]
    axis.plot(
        retention,
        [row["std_v"] * 1e6 for row in rows],
        "o-",
        color=BLUE,
        label="Full ADC, AZ only + quantization",
    )
    axis.plot(
        retention,
        [row["linear_az_only_std_v"] * 1e6 for row in rows],
        "--",
        color=GREEN,
        label=r"Linear AZ term: $\sigma_{AZ}(1-h)/(2G)$",
    )
    axis.set(
        title="D  Imperfect hold leaks the shared noise",
        xlabel="Odd-cycle AZ hold retention h (assumed)",
        ylabel="Pair error standard deviation (µV)",
        xlim=(-0.03, 1.03),
        ylim=(0, None),
    )
    axis.legend(frameon=False, fontsize=8)
    for axis in axes.flat:
        axis.grid(axis="y", alpha=0.22, lw=0.6)
        axis.set_axisbelow(True)
    assumptions = data["assumptions"]
    figure.suptitle(
        "Chopping-assisted AZ: held noise, real ADC words, and a failure case\n"
        f"40 MS/s raw → 20 MS/s pair; {assumptions['raw_samples']} raw samples; "
        "AZ and ordinary errors = 1 mV RMS at RA output (assumed); G = 32",
        fontsize=12,
    )
    _save(figure, destination / "caaz_mechanism_results")

    figure, axis = plt.subplots(figsize=(7.3, 3.6), layout="constrained")
    raw_fs = assumptions["raw_sample_rate_hz"]
    frequency = np.linspace(0, raw_fs / 2, 1001)
    magnitude = np.abs(np.cos(np.pi * frequency / raw_fs))
    axis.plot(frequency / 1e6, 20 * np.log10(np.maximum(magnitude, 1e-4)), color=BLUE, lw=2)
    axis.axvspan(raw_fs / 4e6, raw_fs / 2e6, color=RED, alpha=0.09)
    axis.axvline(raw_fs / 4e6, color=RED, ls="--", lw=1)
    axis.annotate(
        "Pair-output Nyquist = 10 MHz\n−3.01 dB at this input frequency",
        xy=(raw_fs / 4e6, -3.0103),
        xytext=(2, -16),
        arrowprops={"arrowstyle": "->", "color": "#404040"},
        fontsize=9,
    )
    axis.set(
        title="Pairing changes signal bandwidth as well as noise",
        xlabel="Analog input frequency (MHz)",
        ylabel="Two-sample magnitude (dB)",
        xlim=(0, raw_fs / 2e6),
        ylim=(-45, 1),
    )
    axis.text(
        0.04,
        0.1,
        r"$|H(f)|=|\cos(\pi f/f_{raw})|$; group delay = $T_{raw}/2$",
        transform=axis.transAxes,
        fontsize=10,
    )
    axis.grid(alpha=0.22, lw=0.6)
    axis.text(
        0.71,
        0.45,
        "Above 10 MHz folds into\nthe output band without\nfurther antialias filtering",
        transform=axis.transAxes,
        fontsize=9,
        color=RED,
        ha="center",
    )
    _save(figure, destination / "caaz_bandwidth")
    _draw_rtl_context(HERE / "data/rtl_context_trace.csv", destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=HERE / "data/summary.json")
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    draw(arguments.data, arguments.out)
