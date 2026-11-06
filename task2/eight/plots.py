"""将归档的八片逐码积分、正弦频谱和学习轨迹绘成中文图。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter
from task1.experiments.full20_linearity_plots import _chinese_style
from task1.experiments.inl_dnl import _sha
from task1.experiments.run_effects import ROOT
from task2.eight.experiment import N_CODES, SWITCHES, linearity

OFF, ON = "#626C75", "#0072B2"
LABELS = ("关闭扰动", "开启扰动")
BRIDGE = "fig21_dem"
ORDER = (("no_dem", "nominal"), (BRIDGE, "nominal"), ("no_dem", "lms"), (BRIDGE, "lms"))
SIGMAS = (2, 3)
MM = 25.4


def _annotate(fig, title, subtitle, note):
    fig.text(0.11, 0.98, title, fontsize=11, fontweight=600, va="top")
    fig.text(0.11, 0.932, subtitle, fontsize=8, va="top")
    fig.text(0.11, 0.015, note, fontsize=7, va="bottom")


def _band(ax, start, values, color, label):
    step = 512
    i = np.arange(0, len(values), step)
    low = np.minimum.reduceat(values, i)
    high = np.maximum.reduceat(values, i)
    edge = start + np.r_[i, len(values)]
    ax.fill_between(
        edge,
        np.r_[low, low[-1]],
        np.r_[high, high[-1]],
        step="post",
        color=color,
        alpha=0.37,
        label=label,
        linewidth=0,
    )
    for trace in (low, high):
        ax.stairs(trace, edge, linewidth=0.48, color=color, alpha=0.75)


def _linearity(inputs):
    fig, axes = plt.subplots(3, 2, figsize=(146 / MM, 155 / MM))
    fig.subplots_adjust(left=0.112, right=0.975, top=0.84, bottom=0.125, hspace=0.78, wspace=0.32)
    _annotate(
        fig,
        "八切片原生 dither 开关 · 20 位 INL／DNL",
        "相同 Fig.21 DEM、冻结 LMS 和 256 个配对状态，12 位 ADC2 ±100 mV",
        "均匀输入窗 ±3.671875 V，统计 754,319 个内码。上两行每 512 码保留极值。\n"
        "浅灰为评价范围外。第三行显示中码逐码结果。LSB20 = 9.5367 μV。",
    )
    dnl_low, dnl_high, inl_peak = 0.0, 0.0, 0.0
    for col, (sigma, (summary, data)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        stats = summary["groups"][BRIDGE]["linearity"]
        pair = [stats[arm]["lms"] for arm in SWITCHES]
        dnl_low = min(dnl_low, *(v["dnl_min_lsb20"] for v in pair))
        dnl_high = max(dnl_high, *(v["dnl_max_lsb20"] for v in pair))
        inl_peak = max(inl_peak, *(v["endpoint_inl_peak_lsb20"] for v in pair))
        first = pair[0]["evaluated_first_unsigned_code"]
        stop = pair[0]["evaluated_stop_unsigned_code"]
        signed = first - N_CODES // 2
        for mode, color, label in zip(SWITCHES, (OFF, ON), LABELS, strict=True):
            dnl, inl = linearity(
                data[f"{BRIDGE}_{mode}_lms_width_v"], first, stop, summary["output_lsb20_v"]
            )
            _band(axes[0, col], signed, dnl, color, label)
            _band(axes[1, col], signed, inl, color, label)
            zoom = np.arange(-24, 25)
            axes[2, col].step(
                zoom,
                dnl[zoom - signed],
                where="post",
                color=color,
                linewidth=0.75,
                linestyle="--" if mode == "off" else "-",
            )
        for row in (0, 1):
            ax = axes[row, col]
            ax.axvspan(-N_CODES // 2, signed, color="#eef1f3", zorder=-1)
            ax.axvspan(stop - N_CODES // 2, N_CODES // 2 - 1, color="#eef1f3", zorder=-1)
            ax.set(xlim=(-N_CODES // 2, N_CODES // 2 - 1), xticks=[-524288, 0, 524287])
            ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: str(int(x))))
            first_label, _, last_label = ax.get_xticklabels()
            first_label.set_horizontalalignment("left")
            last_label.set_horizontalalignment("right")
        d0, d1 = (v["dnl_rms_lsb20"] for v in pair)
        i0, i1 = (v["endpoint_inl_peak_lsb20"] for v in pair)
        axes[0, col].set(title=f"{sigma}% 失配｜DNL RMS {d0:.3f} → {d1:.3f}")
        axes[1, col].set(title=f"峰值 INL {i0:.3f} → {i1:.3f}")
        axes[2, col].set(
            xlim=(-24, 24),
            xticks=[-24, -12, 0, 12, 24],
            xlabel="有符号 20 位码",
        )
    for ax in axes[[0, 2]].flat:
        ax.set_ylim(dnl_low - 0.1, dnl_high + 0.1)
    for ax in axes[1]:
        ax.set_ylim(-inl_peak - 0.1, inl_peak + 0.1)
    axes[0, 0].set_ylabel("DNL（LSB20）")
    axes[1, 0].set_ylabel("INL（LSB20）")
    axes[2, 0].set_ylabel("DNL（LSB20）")
    fig.legend(
        *axes[0, 0].get_legend_handles_labels(),
        loc="center right",
        bbox_to_anchor=(0.98, 0.883),
        frameon=False,
        ncol=2,
        handlelength=1.0,
        columnspacing=0.9,
    )
    for ax in axes.flat:
        ax.grid(alpha=0.26)
    return fig


def _spectra(inputs):
    fig, axes = plt.subplots(2, 2, figsize=(146 / MM, 111 / MM), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.112, right=0.975, top=0.79, bottom=0.17, hspace=0.55, wspace=0.2)
    _annotate(
        fig,
        "八切片原生 dither 开关 · 20 位输出频谱",
        "Fig.21 DEM 与同一冻结 LMS，10 MS/s，998.535 kHz，4096 点相干采样",
        "同一 ±3.333 V 正弦和矩形窗。圆点标记各面板最大杂散。两组均无超窗。",
    )
    for col, (sigma, (summary, data)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        config = summary["experiment"]
        freq = np.fft.rfftfreq(config["samples"], 1 / config["fs_hz"]) / 1e6
        for row, (mode, label, color) in enumerate(zip(SWITCHES, LABELS, (OFF, ON), strict=True)):
            ax = axes[row, col]
            perf = summary["groups"][BRIDGE]["tone"][mode]["lms"]
            spectrum = data[f"{BRIDGE}_{mode}_lms_fft_dbfs"]
            spur = round(perf["spur_hz"] * config["samples"] / config["fs_hz"])
            ax.plot(freq, spectrum, lw=0.52, color=color)
            ax.plot(freq[spur], spectrum[spur], "o", ms=3, color=color, mec="white", mew=0.4)
            ax.set(title=f"{sigma}% 失配｜{label}", ylim=(-176, 4), xlim=(0, 5))
            ax.text(
                0.97,
                0.90,
                f"SNDR {perf['sndr_db']:.2f} dB\nSFDR {perf['sfdr_dbc']:.2f} dBc",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=7,
            )
            ax.grid(alpha=0.23)
        axes[1, col].set_xlabel("频率（MHz）")
    for row in range(2):
        axes[row, 0].set_ylabel("幅度（dBFS）")
    return fig


def _factorial(inputs):
    fig, axes = plt.subplots(4, 2, figsize=(146 / MM, 169 / MM), sharex=True)
    fig.subplots_adjust(left=0.115, right=0.975, top=0.83, bottom=0.102, hspace=0.58, wspace=0.29)
    _annotate(
        fig,
        "同一制造样本上的八组开关组合",
        "每档失配使用同一切片时序。左／右两列分别为 2%／3%。",
        "每组点连线表示同条件 dither 开关的变化。峰值 INL 使用对数纵轴。",
    )
    specs = (
        ("linearity", "dnl_rms_lsb20", "DNL RMS（LSB20）"),
        ("linearity", "endpoint_inl_peak_lsb20", "峰值 INL（LSB20）"),
        ("tone", "sndr_db", "SNDR（dB）"),
        ("tone", "sfdr_dbc", "SFDR（dBc）"),
    )
    for col, (sigma, (summary, _)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        groups = summary["groups"]
        for row, (section, metric, ylabel) in enumerate(specs):
            ax = axes[row, col]
            values = np.array(
                [
                    [groups[group][section][mode][cal][metric] for group, cal in ORDER]
                    for mode in SWITCHES
                ]
            )
            x = np.arange(len(ORDER))
            ax.plot([x - 0.12, x + 0.12], values, lw=0.72, color="#a4aeb4")
            ax.scatter(x - 0.12, values[0], s=12, color=OFF, label=LABELS[0], zorder=2)
            ax.scatter(x + 0.12, values[1], s=12, color=ON, marker="D", label=LABELS[1], zorder=2)
            ax.set_xlim(-0.34, 3.34)
            ax.grid(axis="y", alpha=0.24)
            if row == 1:
                ax.set_yscale("log")
            if row == 0:
                ax.set_title(f"{sigma}% 固定电容失配")
            if col == 0:
                ax.set_ylabel(ylabel)
        axes[-1, col].set_xticks(np.arange(len(ORDER)), ("均关闭", "仅 DEM", "仅 LMS", "DEM＋LMS"))
        axes[-1, col].tick_params(axis="x", labelsize=6.8)
    for row in range(len(specs)):
        limits = np.array([ax.get_ylim() for ax in axes[row]])
        for ax in axes[row]:
            ax.set_ylim(limits[:, 0].min(), limits[:, 1].max())
    fig.legend(
        *axes[0, 0].get_legend_handles_labels(),
        loc="center right",
        bbox_to_anchor=(0.98, 0.886),
        frameon=False,
        ncol=2,
        handlelength=1.0,
        columnspacing=0.9,
    )
    return fig


def _learning(inputs):
    fig, axes = plt.subplots(1, 2, figsize=(146 / MM, 90 / MM))
    fig.subplots_adjust(left=0.112, right=0.97, top=0.735, bottom=0.22, wspace=0.37)
    _annotate(
        fig,
        "LMS 需要观察扰动采样状态",
        "Fig.21 路径每档使用 8,192 笔参考输入，6 轮后冻结。",
        "关闭训练只识别 324 个等效权重。混合训练包含扰动采样，识别 360 个。",
    )
    for col, (sigma, (summary, _)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        group = summary["groups"][BRIDGE]
        learn = group["fit"]["shared"]["learning"]
        epoch = [v["epoch"] for v in learn]
        axes[0].plot(
            epoch,
            [v["training_residual_rms_v"] * 1e6 for v in learn],
            "o-" if col == 0 else "s--",
            ms=3,
            lw=0.75,
            color=ON if col == 0 else "#D55E00",
            label=f"{sigma}% 失配",
        )
        old = group["tone"]["on"]["old_off"]["sndr_db"]
        new = group["tone"]["on"]["lms"]["sndr_db"]
        axes[1].bar([col - 0.16, col + 0.16], [old, new], width=0.27, color=[OFF, ON])
        for x, val in zip([col - 0.16, col + 0.16], [old, new], strict=True):
            axes[1].text(x, val + 1, f"{val:.1f}", ha="center", fontsize=7)
    axes[0].set(
        yscale="log", xlabel="训练轮次", ylabel="训练误差 RMS（μV）", title="混合训练的收敛"
    )
    axes[0].legend(frameon=False, fontsize=7)
    axes[1].set(
        ylabel="SNDR（dB）",
        title="启用扰动后的独立留出",
        ylim=(0, 116),
        xticks=(0, 1),
        xticklabels=("2% 失配", "3% 失配"),
    )
    fig.legend(
        axes[1].containers[-1],
        ["关闭态训练", "开关混合训练"],
        frameon=False,
        loc="center right",
        bbox_to_anchor=(0.97, 0.824),
        ncol=2,
        fontsize=7,
    )
    for ax in axes:
        ax.grid(axis="y", alpha=0.23)
    return fig


def run(data_dir: Path, output: Path) -> None:
    """从两档归档数据导出四张图和逐文件收据。"""
    font = _chinese_style()
    # 轮廓字形避免 macOS 的 Hiragino TTC 在 PDF 阅读器中漏字。
    plt.rcParams["pdf.fonttype"] = 3
    output.mkdir(parents=True, exist_ok=True)
    inputs, checks = [], {}
    for sigma in SIGMAS:
        path = data_dir / f"sigma_{sigma}pct"
        metadata = path / "summary.json"
        raw = path / "data.npz"
        summary = json.loads(metadata.read_text(encoding="utf-8"))
        digest = _sha(raw)
        if digest != summary["artifact_sha256"][raw.name]:
            raise ValueError(f"SHA-256 of {raw.name} differs from the experiment receipt")
        checks.update(
            {f"{path.name}/{raw.name}": digest, f"{path.name}/{metadata.name}": _sha(metadata)}
        )
        inputs.append((summary, np.load(raw)))
    produced = {}
    try:
        for label, draw in (
            ("linearity", _linearity),
            ("spectra", _spectra),
            ("factorial", _factorial),
            ("lms", _learning),
        ):
            fig = draw(inputs)
            for ext in ("png", "svg", "pdf"):
                path = output / f"eight_native_{label}.{ext}"
                metadata = {"pdf": {"CreationDate": None, "ModDate": None}, "svg": {"Date": None}}
                fig.savefig(path, dpi=300, metadata=metadata.get(ext))
                produced[path.name] = _sha(path)
            plt.close(fig)
    finally:
        for _, data in inputs:
            data.close()
    manifest = {
        "font_family": font,
        "font_file": Path(font_manager.findfont(font)).name,
        "pdf_fonttype": 3,
        "figure_width_mm": 146,
        "dpi": 300,
        "input_sha256": checks,
        "script_sha256": {str(Path(__file__).relative_to(ROOT)): _sha(Path(__file__))},
        "artifact_sha256": produced,
    }
    (output / "figures.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    """读取 sigma_2pct 与 sigma_3pct，输出可打印图形。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data_dir, args.output)


if __name__ == "__main__":
    main()
