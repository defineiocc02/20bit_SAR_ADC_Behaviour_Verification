#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_caaz_effect.py
# 模块名     : run_caaz_effect
# 版本       : v1.0
# 功能       : 复现同输出速率下的 CAAZ 共用 AZ 噪声示例及数字时序图。
#
# 设计说明   :
#   * 承担 联合效应/噪声实验与图件 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""复现同输出速率下的 CAAZ 共用 AZ 噪声示例及数字时序图。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from tools.run_open_rtl import digest, new_output, write_json  # noqa: E402

from task1.experiments.full20_linearity_plots import chinese_style  # noqa: E402

RAW_RATE_HZ = 10_000_000
PAIR_RATE_HZ = RAW_RATE_HZ // 2
FULL_SCALE_PEAK_V = 5.0
LSB_V = FULL_SCALE_PEAK_V / (1 << 19)
PAIR_COUNT = 1 << 16
FFT_LEN = 2048
TONE_BIN = 37
TONE_PEAK_V = 0.5
SEED = 0xCAAA20
FRESH_SIGMA_V = 10e-6
AZ_SIGMA_V = 20e-6
OFFSET_V = 40e-6
HOLD_RATIO = 0.75
HOLD_SHIFT_V = 5e-6
CASES = (
    ("conventional", "每笔自零＋两笔平均", "#555555", "-"),
    ("caaz_ideal", "CAAZ · 理想保持", "#0072B2", "-"),
    ("caaz_hold_error", "CAAZ · 保持衰减与偏移", "#D55E00", "--"),
)


# =========================================================================
# 1. 函数组：quantize / _codes / _simulate
# =========================================================================
# -------------------------------------------------------------------------
def quantize(values: np.ndarray) -> np.ndarray:
    """按 LSB_V 向下取整到 20 位有符号码；越界直接报错，不静默饱和。"""
    codes = np.floor(values / LSB_V).astype(np.int64)
    if np.any((codes < -(1 << 19)) | (codes >= 1 << 19)):
        raise RuntimeError("CAAZ example input exceeded the 20-bit code range")
    return codes


def _codes(first: np.ndarray, second: np.ndarray, *, chopped: bool) -> np.ndarray:
    # 码中心按 (signed_code + 0.5) LSB 定义，偶减奇时 0.5 消去。
    a, b = quantize(first), quantize(second)
    return (a - b) // 2 if chopped else (a + b + 1) // 2


def _simulate() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(SEED)
    time = np.arange(2 * PAIR_COUNT, dtype=float) / RAW_RATE_HZ
    tone_hz = TONE_BIN * PAIR_RATE_HZ / PAIR_COUNT
    x_e, x_o = (TONE_PEAK_V * np.sin(2 * np.pi * tone_hz * time)).reshape(-1, 2).T
    fresh_e, fresh_o = rng.normal(0, FRESH_SIGMA_V, (2, PAIR_COUNT))
    az_e, az_o = rng.normal(0, AZ_SIGMA_V, (2, PAIR_COUNT))
    positive = x_e + OFFSET_V + az_e + fresh_e
    output = {
        "conventional": _codes(positive, x_o + OFFSET_V + az_o + fresh_o, chopped=False),
        "caaz_ideal": _codes(positive, -x_o + OFFSET_V + az_e + fresh_o, chopped=True),
        "caaz_hold_error": _codes(
            positive, -x_o + OFFSET_V + HOLD_RATIO * az_e + HOLD_SHIFT_V + fresh_o, chopped=True
        ),
    }
    reference = (x_e + x_o) * 0.5
    return {
        "pair_reference_v": reference,
        "raw_signal_even_v": x_e,
        "raw_signal_odd_v": x_o,
        "fresh_even_v": fresh_e,
        "fresh_odd_v": fresh_o,
        "az_even_v": az_e,
        "az_independent_odd_v": az_o,
        **{f"{key}_code": value for key, value in output.items()},
        **{f"{key}_error_v": (value + 0.5) * LSB_V - reference for key, value in output.items()},
    }


# =========================================================================
# 2. 函数组：_density / _plot / run
# =========================================================================
# -------------------------------------------------------------------------
# 保存完整样本、频谱和可核验指标。
# -------------------------------------------------------------------------
def _density(error: np.ndarray) -> np.ndarray:
    blocks = error.reshape(-1, FFT_LEN)
    transformed = np.fft.rfft(blocks - blocks.mean(axis=1, keepdims=True), axis=1)
    psd = np.mean(abs(transformed) ** 2, axis=0) / (FFT_LEN * PAIR_RATE_HZ)
    psd[1:-1] *= 2
    return psd


def _plot(out: Path, arrays: dict[str, np.ndarray], metrics: dict) -> str:
    font = chinese_style()
    # 直接导出字形轮廓，规避 macOS Hiragino TTC 子集嵌入时的 Type0 渲染问题。
    plt.rcParams["pdf.fonttype"] = 3
    fig, axes = plt.subplots(2, 1, figsize=(146 / 25.4, 132 / 25.4), height_ratios=[1, 1.5])
    fig.subplots_adjust(left=0.193, right=0.968, top=0.885, bottom=0.155, hspace=0.57)
    fig.text(0.5, 0.977, "CAAZ 控制节拍与配对噪声对照", ha="center", va="top", weight=600)
    fig.text(0.5, 0.938, "10 MS/s 原始转换 → 5 MS/s 非重叠配对 · 样本域模型", ha="center")

    timing, spectrum = axes
    timing.set_title("同一 AZ 代跨一对原始采样，奇数结果提交一对", loc="left")
    phases = (
        ("输入反相", [0, 1, 0, 1, 0, 1], "#0072B2"),
        ("AZ 更新请求", [1, 0, 1, 0, 1, 0], "#009E73"),
        ("AZ 保持请求", [0, 1, 0, 1, 0, 1], "#D55E00"),
        ("配对提交", [0, 1, 0, 1, 0, 1], "#333333"),
    )
    for i, (_, values, color) in enumerate(phases):
        level = (len(phases) - i - 1) + 0.6 * np.r_[values, values[-1]]
        timing.step(np.arange(7), level, where="post", color=color, linewidth=1.35)
    for start in (0, 2, 4):
        timing.axvspan(start, start + 2, color="#0072B2", alpha=0.04 if start % 4 == 0 else 0.0)
    timing.set(
        xlim=(0, 6),
        ylim=(-0.25, 3.85),
        xticks=np.arange(6) + 0.5,
        xticklabels=list(map(str, range(6))),
        yticks=[3.3, 2.3, 1.3, 0.3],
        yticklabels=[phase[0] for phase in phases],
        xlabel="样本 ID（提交位置按归属编号，实际返回可延后）",
    )
    timing.tick_params(axis="y", length=0)

    frequency = np.fft.rfftfreq(FFT_LEN, d=1 / PAIR_RATE_HZ)
    full_scale_sine_power = FULL_SCALE_PEAK_V**2 / 2
    for key, label, color, style in CASES:
        psd = arrays[f"{key}_psd_v2_per_hz"]
        sigma_uv = metrics[key]["ac_rms_uv"]
        spectrum.plot(
            frequency[1:] / 1e6,
            10 * np.log10(psd[1:] / full_scale_sine_power),
            color=color,
            ls=style,
            lw=0.9,
            label=f"{label}  {sigma_uv:.2f} 微伏 RMS",
        )
    spectrum.set_title("同一配对输出速率、同一分析带宽的误差谱", loc="left")
    spectrum.set(xlabel="频率（MHz）", ylabel="误差 PSD（dBFS/Hz）", xlim=(0, 2.5))
    spectrum.grid(color="0.73", linestyle="--", alpha=0.65)
    spectrum.legend(loc="lower left", fontsize=7, framealpha=0.94)
    fig.text(
        0.193,
        0.033,
        "独立噪声 10 微伏、AZ 噪声 20 微伏 RMS。保持误差例保留 75% AZ，新增 5 微伏偏移。\n"
        "三组均经 20 位量化和两笔处理，误差谱去除直流。模型参数用于机制演示。",
        va="bottom",
        fontsize=7.3,
    )
    for ext, options in (
        ("png", {"dpi": 300}),
        ("pdf", {"metadata": {"CreationDate": None, "ModDate": None}}),
        ("svg", {"metadata": {"Date": None}}),
    ):
        fig.savefig(out / f"caaz_effect.{ext}", **options)
    plt.close(fig)
    return font


def run(out: Path) -> dict:
    """保存完整样本、频谱和可核验指标。"""
    arrays = _simulate()
    metrics = {}
    for key, *_ in CASES:
        error = arrays[f"{key}_error_v"]
        psd = _density(error)
        arrays[f"{key}_psd_v2_per_hz"] = psd
        metrics[key] = {
            "offset_uv": float(error.mean() * 1e6),
            "ac_rms_uv": float(error.std() * 1e6),
        }
    ratio = metrics["conventional"]["ac_rms_uv"] / metrics["caaz_ideal"]["ac_rms_uv"]
    np.savez_compressed(out / "caaz_effect_data.npz", **arrays)
    font = _plot(out, arrays, metrics)
    summary = {
        "scope": "ILLUSTRATIVE_SAMPLE_DOMAIN_SHARED_AZ_NOISE",
        "raw_rate_hz": RAW_RATE_HZ,
        "paired_rate_hz": PAIR_RATE_HZ,
        "pairs_per_case": PAIR_COUNT,
        "tone_hz": TONE_BIN * PAIR_RATE_HZ / PAIR_COUNT,
        "tone_peak_v": TONE_PEAK_V,
        "fft_len": FFT_LEN,
        "equal_bandwidth_hz": [0, PAIR_RATE_HZ / 2],
        "nominal_lsb20_v": LSB_V,
        "seed": SEED,
        "fresh_sigma_v": FRESH_SIGMA_V,
        "az_sigma_v": AZ_SIGMA_V,
        "offset_v": OFFSET_V,
        "hold_ratio": HOLD_RATIO,
        "hold_shift_v": HOLD_SHIFT_V,
        "metrics": metrics,
        "ideal_caaz_vs_conventional_ac_noise_reduction_db": float(20 * np.log10(ratio)),
        "font_family": font,
        "pdf_fonttype": 3,
        "source_sha256": {
            name: digest(ROOT / name)
            for name in (
                "task3/tools/run_caaz_effect.py",
                "task1/experiments/full20_linearity_plots.py",
            )
        },
        "artifact_sha256": {path.name: digest(path) for path in sorted(out.iterdir())},
    }
    write_json(out / "caaz_effect_summary.json", summary)
    return summary


# =========================================================================
# 3. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# 指定一个仓库外的新目录来复现。
# -------------------------------------------------------------------------
def main() -> None:
    """指定一个仓库外的新目录来复现。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    summary = run(new_output(args.out))
    print(summary["metrics"])


if __name__ == "__main__":
    main()
