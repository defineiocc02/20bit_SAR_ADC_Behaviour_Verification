#!/usr/bin/env python3
"""在同一 3% 电容池中重放八片 dither、Fig.21 DEM 和 CAAZ 的 AZ 噪声。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.experiments.adctoolbox_calibration import CalibrationConfig, _quantize  # noqa: E402
from task1.experiments.full20_linearity_plots import _chinese_style  # noqa: E402
from task1.experiments.residue_control_study import MapperTable  # noqa: E402
from task1.experiments.run_effects import Capacitors, ExperimentConfig  # noqa: E402
from task2.eight.model import ChargePool, coarse_decision, conversion_state, decode  # noqa: E402
from task3.tools.run_joint_effect import (  # noqa: E402
    EVIDENCE,
    RTL_RECEIPT,
    _reconstruct,
    _spectrum,
)
from task_reconstruction.fixed import Q, from_training  # noqa: E402
from tools.run_open_rtl import digest, new_output, write_json  # noqa: E402

AZ_SEED = 0x0A2E2025
FRESH_SEED = 0x0FCE2025
AZ_SIGMA_V = 20e-6
FRESH_SIGMA_V = 10e-6
RA_OFFSET_V = 80e-6
HOLD_RATIO = 0.75
HOLD_SHIFT_V = 5e-6
PSD_SEGMENT = 256
CASES = (
    ("az_independent", False, "不斩波\n独立 AZ", "#444444", "-"),
    ("chop_independent", True, "斩波\n独立 AZ", "#888888", "--"),
    ("shared_ideal", True, "CAAZ\n理想保持", "#0072B2", "-"),
    ("shared_hold_error", True, "CAAZ\n保持误差", "#D55E00", "--"),
)


def _psd(error: np.ndarray, rate_hz: float) -> np.ndarray:
    blocks = error.reshape(-1, PSD_SEGMENT)
    blocks = blocks - blocks.mean(axis=1, keepdims=True)
    spectrum = np.fft.rfft(blocks, axis=1)
    psd = np.mean(abs(spectrum) ** 2, axis=0) / (PSD_SEGMENT * rate_hz)
    psd[1:-1] *= 2
    return psd


def _noise(seed: int, sigma_v: float, samples: int) -> np.ndarray:
    return np.random.Generator(np.random.PCG64(seed)).normal(0.0, sigma_v, size=samples)


def _analog_front_end(reference, phase, plan, dither, threshold, step, mapper, pool):
    """冻结同笔前级及 RDAC 控制，再计算干净残差。"""
    coarse, command, valid = coarse_decision(reference, dither, threshold, step)
    peak = threshold[-1]
    counts = {
        "front_input_out_of_range": int(
            np.count_nonzero((reference < -peak) | (reference >= peak))
        ),
        "sadc_code_out_of_range": int(np.count_nonzero((coarse < 0) | (coarse >= 512))),
        "rdac_command_out_of_range": int(np.count_nonzero((command < 0) | (command >= 512))),
        "rdac_sample_setting_out_of_range": int(np.count_nonzero(dither.setting > 8)),
    }
    if not valid.all() or any(counts.values()):
        raise ValueError(f"SADC/RDAC rejection {counts}")
    state = conversion_state(command, plan, phase, dither, threshold, step, mapper)
    gain, intercept = pool.affine(plan, state.features)
    return state, coarse, gain * (reference + peak) - intercept, counts


def _one_case(az, fresh, caaz, reference, front_end, cfg, coefficient, word, meta):
    state, coarse, clean_residue, counts = front_end
    noisy_residue = clean_residue + (RA_OFFSET_V + az + fresh)
    lo, hi = -cfg.adc2_half_range_v, cfg.adc2_half_range_v
    adc2_rejected = int(np.count_nonzero((noisy_residue < lo) | (noisy_residue >= hi)))
    if adc2_rejected:
        raise ValueError(f"ADC2 input out of range for {adc2_rejected} samples")
    adc2 = _quantize(noisy_residue, cfg.adc2_bits, cfg.adc2_half_range_v)
    raw, vector_sha = _reconstruct(coefficient, word, state, coarse, adc2)
    even, odd = raw.reshape(-1, 2).T
    exact_half_lsb = even - odd if caaz else even + odd + 1
    pair = exact_half_lsb // 2
    pair_rejected = int(np.count_nonzero((pair < -(1 << 19)) | (pair >= 1 << 19)))
    if pair_rejected:
        raise ValueError(f"20-bit pair out of range for {pair_rejected} samples")

    experiment = meta["experiment"]
    gain = np.asarray(coefficient.gains)[state.switches.plan].sum(axis=1) / Q
    rate_hz = experiment["fs_hz"] / 2
    output = (pair + 0.5) * meta["output_lsb20_v"]
    spectrum, metrics = _spectrum(
        output, reference, experiment["full_scale_peak_v"], experiment["tone_bin"]
    )
    error = output - reference
    sign = -1 if caaz else 1
    az_even, az_odd = az.reshape(-1, 2).T
    az_pair = (az_even + sign * az_odd) / 2
    e, o = (az / gain).reshape(-1, 2).T
    residual_after_gain = (e + sign * o) / 2
    report = {
        **metrics,
        **counts,
        "adc2_rejected": adc2_rejected,
        "adc2_residue_min_v": float(noisy_residue.min()),
        "adc2_residue_max_v": float(noisy_residue.max()),
        "q28_raw_rejected": 0,
        "paired_code_rejected": pair_rejected,
        "az_pair_pearson": float(np.corrcoef(az_even, az_odd)[0, 1]),
        "az_injected_pair_residual_ac_uv": float(az_pair.std() * 1e6),
        "az_linear_pair_residual_after_lms_gain_ac_uv": float(residual_after_gain.std() * 1e6),
        "vectors_sha256": vector_sha,
    }
    trace = {
        "coarse": coarse,
        "command": state.switches.codes,
        "sample_sub_high": state.dither.sample_sub_high,
        "conv_main": state.switches.main,
        "conv_sub": state.switches.sub,
        "adc2_clean_residue_v": clean_residue,
        "adc2_noisy_residue_v": noisy_residue,
        "adc2_code": adc2,
        "az_injected_v": az,
        "az_linear_after_gain_v": residual_after_gain,
        "raw_q28_lms_signed_code": raw,
        "pair_exact_half_lsb": exact_half_lsb,
        "pair_signed_code": pair,
        "pair_error_v": error,
        "spectrum_dbfs": spectrum,
        "error_psd_v2_per_hz": _psd(error, rate_hz),
    }
    return report, trace


def _plot(out: Path, traces, report, fs_pair: float, peak_v: float) -> str:
    font = _chinese_style()
    # macOS 的 Hiragino TTC 子集在部分 PDF 阅读器中无法按 Type0 装载。
    plt.rcParams["pdf.fonttype"] = 3
    plt.rcParams["svg.hashsalt"] = "task3-joint-noise"
    fig = plt.figure(figsize=(146 / 25.4, 138 / 25.4))
    grid = fig.add_gridspec(2, 2, height_ratios=[1, 1.22], width_ratios=[0.94, 1.06])
    fig.subplots_adjust(left=0.143, right=0.974, top=0.835, bottom=0.162, hspace=0.79, wspace=0.47)
    fig.text(
        0.5, 0.978, "八片 dither、DEM 与 CAAZ 的 AZ 噪声对照", ha="center", va="top", weight=600
    )
    fig.text(0.5, 0.937, "3% 固定失配 · 同一物理池、相位、扰动字和冻结 Q28 LMS", ha="center")
    fig.text(0.5, 0.91, "10 MS/s 原始码 → 5 MS/s 非重叠配对 · 样本域模型", ha="center")

    az_axes = fig.add_subplot(grid[0, 0])
    az = traces["az_independent_az_injected_v"] * 1e6
    az_axes.scatter(az[:256:2], az[1:256:2], s=6, color="#777777", alpha=0.48, label="独立 AZ")
    horizontal = np.array([-59, 59])
    az_axes.plot(horizontal, horizontal, color="#0072B2", lw=1.1, label="理想共享")
    az_axes.plot(
        horizontal,
        HOLD_RATIO * horizontal + HOLD_SHIFT_V * 1e6,
        color="#D55E00",
        ls="--",
        lw=1.1,
        label="保持误差",
    )
    az_axes.set(
        title="偶奇 AZ 的注入关系",
        xlim=(-62, 62),
        ylim=(-62, 62),
        xlabel="偶数笔（微伏）",
        ylabel="奇数笔（微伏）",
        xticks=[-60, 0, 60],
        yticks=[-60, 0, 60],
    )
    az_axes.grid(color="0.76", ls="--", alpha=0.6)
    az_axes.legend(loc="upper left", fontsize=6.25, framealpha=0.90)

    rms_axes = fig.add_subplot(grid[0, 1])
    bars = rms_axes.bar(
        np.arange(len(CASES)),
        [report[key]["ac_error_rms_uv"] for key, *_ in CASES],
        color=[color for _, _, _, color, _ in CASES],
        width=0.69,
    )
    rms_axes.bar_label(bars, fmt="%.2f", padding=2, fontsize=7)
    rms_axes.set(
        title="配对后去直流总误差",
        ylim=(0, 24),
        yticks=[0, 10, 20],
        xticks=np.arange(len(CASES)),
        xticklabels=[title for _, _, title, _, _ in CASES],
        ylabel="RMS（微伏）",
    )
    rms_axes.tick_params(axis="x", labelsize=6.5)
    rms_axes.set_axisbelow(True)
    rms_axes.grid(axis="y", color="0.76", ls="--", alpha=0.6)

    spectrum = fig.add_subplot(grid[1, :])
    frequency = np.fft.rfftfreq(PSD_SEGMENT, d=1 / fs_pair)
    full_scale_sine_power = peak_v**2 / 2
    for key, _, title, color, line in CASES:
        sn = report[key]["sndr_db"]
        psd = traces[f"{key}_error_psd_v2_per_hz"][1:]
        spectrum.plot(
            frequency[1:] / 1e6,
            10 * np.log10(psd / full_scale_sine_power),
            color=color,
            lw=1.0,
            ls=line,
            label=f"{title.replace(chr(10), ' · ')}  {sn:.2f} dB",
        )
    spectrum.set_title("同一带宽的误差谱（图例数字为输出 SNDR）", loc="left")
    spectrum.set(xlabel="频率（MHz）", ylabel="PSD（dBFS/Hz）", xlim=(0, 2.5))
    spectrum.grid(color="0.76", ls="--", alpha=0.6)
    handles, labels = spectrum.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="center",
        bbox_to_anchor=(0.56, 0.51),
        fontsize=6.7,
        frameon=False,
        ncols=2,
    )
    fig.text(
        0.143,
        0.039,
        "人工残差激励：AZ 20 微伏、独立噪声 10 微伏 RMS，均在 ADC2 前注入。\n"
        "保持误差组奇数笔保留 75% AZ 幅度并新增 5 微伏偏移。四组使用同一 20 位码制。",
        va="bottom",
        fontsize=7,
    )
    for ext, options in (
        ("png", {"dpi": 300}),
        ("pdf", {"metadata": {"CreationDate": None, "ModDate": None}}),
        ("svg", {"metadata": {"Date": None}}),
    ):
        fig.savefig(out / f"caaz_joint_noise.{ext}", **options)
    plt.close(fig)
    return font


def run(out: Path) -> dict:
    """用已归档的物理池和标定数据执行四组匹配重放。"""
    meta_path, data_path = EVIDENCE / "summary.json", EVIDENCE / "data.npz"
    meta = json.loads(meta_path.read_text())
    fixture = json.loads(RTL_RECEIPT.read_text())["paired_3pct_fixture"]
    actual_sha = digest(data_path)
    if (
        fixture["input_data_sha256"] != actual_sha
        or meta["artifact_sha256"]["data.npz"] != actual_sha
    ):
        raise ValueError("3% archive identity differs from the matched RTL fixture")
    experiment = ExperimentConfig(**meta["experiment"])
    cfg = CalibrationConfig(**meta["calibration"])
    coefficient = from_training(data_path)
    mapper = MapperTable.build(bridge=True)
    with np.load(data_path) as data:
        raw_reference = data["fig21_dem_tone_reference_v"]
        plan = data["fig21_dem_tone_plan"]
        phase = (data["fig21_dem_tone_horizontal"], data["fig21_dem_tone_vertical"])
        word = data["fig21_dem_on_tone_word"]
        threshold = data["nominal_threshold_v"]
        caps = Capacitors(
            data["actual_main_cap_f"], data["actual_sub_cap_f"], data["actual_bridge_cap_f"]
        )

    count = len(raw_reference)
    if count != fixture["samples"] or count != experiment.samples or count % (2 * PSD_SEGMENT):
        raise ValueError("the archived cohort must cover complete nonoverlapping PSD blocks")
    sign = 1 - 2 * (np.arange(count) % 2)
    pool = ChargePool.from_caps(caps, experiment)
    dither = decode(word)
    front_end = {
        chop: _analog_front_end(
            sign * raw_reference if chop else raw_reference,
            phase,
            plan,
            dither,
            threshold,
            meta["native_dither_step_v"],
            mapper,
            pool,
        )
        for chop in (False, True)
    }
    az_independent = _noise(AZ_SEED, AZ_SIGMA_V, count)
    fresh = _noise(FRESH_SEED, FRESH_SIGMA_V, count)
    az_shared = np.repeat(az_independent[::2], 2)
    az_impaired = az_shared.copy()
    az_impaired[1::2] = HOLD_RATIO * az_impaired[1::2] + HOLD_SHIFT_V
    az = (az_independent, az_independent, az_shared, az_impaired)

    reference = raw_reference.reshape(-1, 2).mean(axis=1)
    fs_pair = experiment.fs_hz / 2
    traces = {
        "raw_reference_v": raw_reference,
        "raw_input_chopped_v": sign * raw_reference,
        "pair_reference_v": reference,
        "fresh_injected_v": fresh,
        "sample_plan": plan,
        "horizontal_phase": phase[0],
        "vertical_phase": phase[1],
        "dither_word": word,
        "fft_frequency_hz": np.fft.rfftfreq(len(reference), 1 / fs_pair),
        "psd_frequency_hz": np.fft.rfftfreq(PSD_SEGMENT, 1 / fs_pair),
    }
    groups = {}
    for (key, chop, *_), injection in zip(CASES, az, strict=True):
        groups[key], trace = _one_case(
            injection, fresh, chop, reference, front_end[chop], cfg, coefficient, word, meta
        )
        traces.update({f"{key}_{field}": value for field, value in trace.items()})

    np.savez_compressed(out / "caaz_joint_noise_data.npz", **traces)
    font = _plot(out, traces, groups, fs_pair, experiment.full_scale_peak_v)
    files = (
        meta_path,
        data_path,
        RTL_RECEIPT,
        Path(__file__).resolve(),
        ROOT / "task3/tools/run_joint_effect.py",
        ROOT / "task2/eight/model.py",
        ROOT / "task_reconstruction/fixed.py",
        ROOT / "task1/experiments/adctoolbox_calibration.py",
        ROOT / "task1/experiments/residue_control_study.py",
        ROOT / "task1/experiments/full20_linearity_plots.py",
        ROOT / "references/PKU_VLSI2025_C32_1_PAPER.pdf",
    )
    report = {
        "scope": "sample_domain_ra_noise_with_native_dither_dem_and_q28_lms",
        "manufacturing_seed": experiment.manufacturing_seed,
        "fixed_capacitor_sigma": experiment.mismatch_sigma_unit,
        "schedule_seed": experiment.schedule_seed,
        "dem_seed": experiment.dem_seed,
        "dither_word_seed": meta["tone_dither_word_seed"],
        "dither_words_differing_within_pair": int(np.count_nonzero(word[::2] != word[1::2])),
        "pairs_with_disjoint_slice_sets": int(
            np.count_nonzero(~(plan[::2, :, None] == plan[1::2, None, :]).any(axis=(1, 2)))
        ),
        "raw_samples": count,
        "pair_outputs": count // 2,
        "raw_rate_hz": experiment.fs_hz,
        "pair_rate_hz": fs_pair,
        "analysis_band_hz": [0, fs_pair / 2],
        "tone_hz": experiment.fs_hz * experiment.tone_bin / count,
        "tone_peak_v": experiment.tone_peak_v,
        "adc2_bits": cfg.adc2_bits,
        "adc2_window_half_v": cfg.adc2_half_range_v,
        "output_lsb20_v": meta["output_lsb20_v"],
        "psd_segment": PSD_SEGMENT,
        "az_seed": AZ_SEED,
        "fresh_seed": FRESH_SEED,
        "az_sigma_v": AZ_SIGMA_V,
        "fresh_sigma_v": FRESH_SIGMA_V,
        "ra_common_offset_v": RA_OFFSET_V,
        "hold_ratio": HOLD_RATIO,
        "hold_shift_v": HOLD_SHIFT_V,
        "groups": groups,
        "font_family": font,
        "numpy_version": np.__version__,
        "archived_fixture_identity": fixture,
        "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in files},
        "artifact_sha256": {path.name: digest(path) for path in sorted(out.iterdir())},
    }
    write_json(out / "summary.json", report)
    return report


def main() -> None:
    """选择尚未创建的仓库外目录保存结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(new_output(args.out))["groups"], ensure_ascii=False))


if __name__ == "__main__":
    main()
