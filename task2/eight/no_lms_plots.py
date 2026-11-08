# =============================================================================
# 文件名     : no_lms_plots.py
# 模块名     : no_lms_plots
# 版本       : v1.0
# 功能       : 从八片归档重绘 DEM 开启、LMS 关闭的 dither 配对结果。
#
# 设计说明   :
#   * 承担 八片受控电荷源模型与对照实验 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""从八片归档重绘 DEM 开启、LMS 关闭的 dither 配对结果。"""

from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter, MaxNLocator

from adi_model.hashing import sha256_file
from task1.experiments.full20_linearity_plots import chinese_style
from task1.experiments.run_effects import ROOT
from task2.eight.experiment import N_CODES, SWITCHES, linearity
from task2.eight.plots import BRIDGE, MM, OFF, ON, SIGMAS, _annotate, _band

MODES = tuple(zip(SWITCHES, ("关闭", "开启"), (OFF, ON), strict=True))
SOURCE_FILES = (
    "task2/eight/no_lms_plots.py",
    "task2/eight/plots.py",
    "task2/eight/experiment.py",
    "task2/eight/model.py",
    "task1/experiments/full20_linearity_plots.py",
    "task1/experiments/inl_dnl.py",
)


# =========================================================================
# 1. 函数组：_shade_extent / _static / _spectra
# =========================================================================
# ------------------------------------------------------------------------
def _shade_extent(ax, first, stop):
    signed_min, signed_max = -(N_CODES // 2), N_CODES // 2 - 1
    ax.axvspan(signed_min, first, color="#edf0f2", zorder=-1)
    ax.axvspan(stop, signed_max, color="#edf0f2", zorder=-1)
    ax.set(xlim=(signed_min, signed_max), xticks=(signed_min, 0, signed_max))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: str(int(x))))
    first_tick, _, last_tick = ax.get_xticklabels()
    first_tick.set_horizontalalignment("left")
    last_tick.set_horizontalalignment("right")


# ------------------------------------------------------------------------
def _static(inputs):
    fig, axes = plt.subplots(4, 2, figsize=(146 / MM, 172 / MM))
    fig.subplots_adjust(left=0.11, right=0.965, top=0.85, bottom=0.125, hspace=0.66, wspace=0.32)
    _annotate(
        fig,
        "关闭 LMS · 八切片 dither 的 20 位 INL／DNL",
        "Fig.21 DEM 始终开启，每档复用同一颗失配样本及 256 个配对状态",
        "均匀输入窗 ±3.671875 V，实际评价 754,319 个 20 位码。浅灰区域未评价。\n"
        "每 512 码保留极值。开启组 DNL 纵轴单独缩放，虚线为 -1 LSB20。",
    )
    for col, (sigma, (summary, data)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        stats = summary["groups"][BRIDGE]["linearity"]
        inl_limit = 1.09 * max(
            stats[mode]["nominal"]["endpoint_inl_peak_lsb20"] for mode in SWITCHES
        )
        for j, (mode, label, color) in enumerate(MODES):
            report = stats[mode]["nominal"]
            first = report["evaluated_first_unsigned_code"]
            stop = report["evaluated_stop_unsigned_code"]
            width = data[f"{BRIDGE}_{mode}_nominal_width_v"]
            dnl, inl = linearity(width, first, stop, summary["output_lsb20_v"])
            missing = np.count_nonzero(width[first:stop] <= 1e-15)
            assert len(dnl) == report["evaluated_code_count"]
            assert missing == report["zero_width_codes"]
            np.testing.assert_allclose(
                [np.sqrt(np.mean(dnl**2)), np.abs(inl).max()],
                [report["dnl_rms_lsb20"], report["endpoint_inl_peak_lsb20"]],
                atol=1e-12,
                rtol=0,
            )
            signed = first - N_CODES // 2
            signed_stop = stop - N_CODES // 2
            dnl_ax, inl_ax = axes[2 * j : 2 * j + 2, col]
            _band(dnl_ax, signed, dnl, color, label)
            _band(inl_ax, signed, inl, color, label)
            dnl_ax.axhline(-1.0, color="#67757d", linewidth=0.55, linestyle="--", alpha=0.65)
            lo, hi = float(dnl.min()), float(dnl.max())
            dnl_ax.set(
                title=f"{sigma}%｜{label} dither · 统计零宽码 {missing:,}",
                ylim=(-1.2, hi + 0.1 * (hi - lo)),
            )
            inl_ax.set(
                title=f"峰值 INL {np.abs(inl).max():.2f} LSB20", ylim=(-inl_limit, inl_limit)
            )
            index = int(np.argmax(np.abs(inl)))
            inl_ax.plot(signed + index, inl[index], "o", color=color, ms=3, mec="white", mew=0.4)
            for ax in (dnl_ax, inl_ax):
                _shade_extent(ax, signed, signed_stop)
                ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
                ax.grid(alpha=0.25)
    for row, ax in enumerate(axes[:, 0]):
        ax.set_ylabel(f"{'INL' if row % 2 else 'DNL'}（LSB20）")
    for ax in axes[-1]:
        ax.set_xlabel("有符号 20 位码")
    for ax in axes[:-1].flat:
        ax.tick_params(labelbottom=False)
    return fig


# ------------------------------------------------------------------------
def _spectra(inputs):
    fig, axes = plt.subplots(2, 2, figsize=(146 / MM, 111 / MM), sharex=True, sharey=True)
    fig.subplots_adjust(left=0.112, right=0.965, top=0.79, bottom=0.17, hspace=0.55, wspace=0.2)
    _annotate(
        fig,
        "关闭 LMS · 八切片 dither 的 20 位输出频谱",
        "Fig.21 DEM 始终开启，10 MS/s，998.535 kHz，4096 点相干采样",
        "同一 ±3.333 V 正弦与矩形窗。圆点为最大杂散，比较均排除直流。",
    )
    for col, (sigma, (summary, data)) in enumerate(zip(SIGMAS, inputs, strict=True)):
        config = summary["experiment"]
        freq = np.fft.rfftfreq(config["samples"], 1 / config["fs_hz"]) / 1e6
        for row, (mode, label, color) in enumerate(MODES):
            ax = axes[row, col]
            perf = summary["groups"][BRIDGE]["tone"][mode]["nominal"]
            spectrum = data[f"{BRIDGE}_{mode}_nominal_fft_dbfs"]
            assert spectrum.shape == freq.shape
            spur = round(perf["spur_hz"] * config["samples"] / config["fs_hz"])
            ax.plot(freq, spectrum, lw=0.58, color=color)
            ax.plot(freq[spur], spectrum[spur], "o", ms=3, color=color, mec="white", mew=0.4)
            ax.set(title=f"{sigma}% 失配｜{label} dither", ylim=(-150, 4), xlim=(0, 5))
            ax.text(
                0.97,
                0.9,
                f"SNDR {perf['sndr_db']:.2f} dB\nSFDR {perf['sfdr_dbc']:.2f} dBc",
                transform=ax.transAxes,
                ha="right",
                va="top",
                fontsize=7,
            )
            ax.grid(alpha=0.23)
        axes[1, col].set_xlabel("频率（MHz）")
    for ax in axes[:, 0]:
        ax.set_ylabel("幅度（dBFS）")
    return fig


# =========================================================================
# 2. 函数组：run / main
# =========================================================================
# -------------------------------------------------------------------------
# 核验原始收据，生成两张图及独立哈希清单。
# 按已有归档重绘关闭 LMS 的图。
# ------------------------------------------------------------------------
def run(data_dir: Path, output: Path) -> None:
    """核验原始收据，生成两张图及独立哈希清单。"""
    font = chinese_style()
    # 轮廓字形避免 macOS 的 Hiragino TTC 在 PDF 阅读器中漏字。
    plt.rcParams["pdf.fonttype"] = 3
    output.mkdir(parents=True, exist_ok=True)
    inputs, checks, produced = [], {}, {}
    with ExitStack() as stack:
        for sigma in SIGMAS:
            path = data_dir / f"sigma_{sigma}pct"
            metadata, raw = path / "summary.json", path / "data.npz"
            summary = json.loads(metadata.read_text(encoding="utf-8"))
            digest = sha256_file(raw)
            if digest != summary["artifact_sha256"][raw.name]:
                raise ValueError(f"SHA-256 mismatch: {raw}")
            checks.update(
                {
                    f"{path.name}/{raw.name}": digest,
                    f"{path.name}/{metadata.name}": sha256_file(metadata),
                }
            )
            inputs.append((summary, stack.enter_context(np.load(raw))))
        for label, draw in (("linearity", _static), ("spectra", _spectra)):
            fig = draw(inputs)
            try:
                for ext in ("png", "svg", "pdf"):
                    path = output / f"eight_native_no_lms_{label}.{ext}"
                    metadata = {
                        "pdf": {"CreationDate": None, "ModDate": None},
                        "svg": {"Date": None},
                    }
                    fig.savefig(path, dpi=300, metadata=metadata.get(ext))
                    produced[path.name] = sha256_file(path)
            finally:
                plt.close(fig)
    manifest = {
        "condition": {"dem": "Fig.21", "lms": "off", "dither": ["off", "on"]},
        "font_family": font,
        "pdf_fonttype": 3,
        "figure_width_mm": 146,
        "dpi": 300,
        "input_sha256": checks,
        "source_sha256": {name: sha256_file(ROOT / name) for name in SOURCE_FILES},
        "artifact_sha256": produced,
    }
    (output / "no_lms_figures.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


# ------------------------------------------------------------------------
def main():
    """按已有归档重绘关闭 LMS 的图。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data_dir, args.output)


if __name__ == "__main__":
    main()
