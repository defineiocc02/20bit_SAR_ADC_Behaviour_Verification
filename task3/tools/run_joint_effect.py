#!/usr/bin/env python3
"""重放 3% 失配的八片 dither、片上 Q28 重构与 CAAZ 四组开关对照。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.experiments.adctoolbox_calibration import CalibrationConfig  # noqa: E402
from task1.experiments.full20_linearity_plots import _chinese_style  # noqa: E402
from task1.experiments.residue_control_study import MapperTable  # noqa: E402
from task1.experiments.run_effects import Capacitors, ExperimentConfig  # noqa: E402
from task2.eight.experiment import _capture  # noqa: E402
from task2.eight.model import ChargePool  # noqa: E402
from task_integration.tools.run_lms_checks import pack, slots  # noqa: E402
from task_reconstruction.fixed import from_training, reconstruct  # noqa: E402
from tools.run_open_rtl import digest, new_output, write_json  # noqa: E402

EVIDENCE = ROOT / "task2/evidence/eight_native/sigma_3pct"
RTL_RECEIPT = ROOT / "task_integration/evidence/on_chip/result.json"
INPUTS = (
    EVIDENCE / "data.npz",
    EVIDENCE / "summary.json",
    RTL_RECEIPT,
    ROOT / "task2/eight/model.py",
    ROOT / "task2/eight/experiment.py",
    ROOT / "task_integration/tools/run_lms_checks.py",
    ROOT / "task_reconstruction/fixed.py",
    ROOT / "task1/experiments/full20_linearity_plots.py",
    Path(__file__).resolve(),
)
SWITCHES = (False, True)
COLORS = ("#555555", "#0072B2")


def _label(dither: bool, caaz: bool) -> str:
    return f"dither_{int(dither)}_caaz_{int(caaz)}"


def _reconstruct(coefficient, word, state, coarse, adc2):
    plan = state.switches.plan
    reconstructed = np.empty(len(plan), dtype=np.int32)
    vectors = hashlib.sha256()
    for i, (ids, main, sub, sample, fine) in enumerate(
        zip(
            plan,
            state.switches.main,
            state.switches.sub,
            state.dither.sample_sub_high,
            adc2,
            strict=True,
        )
    ):
        code, bad = reconstruct(
            coefficient,
            tuple(map(int, ids)),
            tuple(map(int, main)),
            tuple(map(int, sub)),
            tuple(map(int, sample)),
            int(fine),
        )
        if bad:
            raise ValueError(f"Q28 reconstruction rejected sample {i}")
        reconstructed[i] = code
        row = pack(
            (
                (word[i], 6),
                (slots(ids, 5), 40),
                (coarse[i], 9),
                (state.switches.codes[i], 9),
                (slots(main, 17), 136),
                (slots(sub, 5), 40),
                (slots(sample, 5), 40),
                (fine, 12),
                (code, 20),
            )
        )
        vectors.update(row.encode("ascii"))
    return reconstructed, vectors.hexdigest()


def _spectrum(output, reference, peak, tone_bin):
    amplitude = abs(np.fft.rfft(output)) * (2 / len(output)) / peak
    amplitude[[0, -1]] *= 0.5
    dbfs = 20 * np.log10(np.maximum(amplitude, 1e-15))
    power = amplitude**2
    power[-1] *= 2
    signal = power[tone_bin]
    power[[0, tone_bin]] = 0
    error = (output - reference) * 1e6
    return dbfs, {
        "sndr_db": float(10 * np.log10(signal / power.sum())),
        "sfdr_dbc": float(10 * np.log10(signal / power.max())),
        "mean_error_uv": float(error.mean()),
        "ac_error_rms_uv": float(error.std()),
    }


def _plot(out, frequency, arrays, report):
    font = _chinese_style()
    # 直接导出字形轮廓，规避 macOS Hiragino TTC 子集嵌入时的 Type0 渲染问题。
    plt.rcParams["pdf.fonttype"] = 3
    plt.rcParams["svg.hashsalt"] = "task3-joint-effect"
    fig = plt.figure(figsize=(146 / 25.4, 151 / 25.4))
    grid = fig.add_gridspec(3, 2, height_ratios=[1, 1, 0.8])
    fig.subplots_adjust(left=0.13, right=0.966, top=0.82, bottom=0.15, hspace=0.93, wspace=0.36)
    fig.text(0.5, 0.981, "八片原生 dither 与 CAAZ 联合开关对照", ha="center", va="top", weight=600)
    fig.text(0.5, 0.942, "3% 固定电容失配 · 同一物理池、Fig.21 DEM 和冻结 Q28 LMS", ha="center")
    fig.text(0.5, 0.91, "10 MS/s 原始码，两笔输出 5 MS/s；频谱基波约 0.999 MHz", ha="center")
    for col, dither in enumerate(SWITCHES):
        for row, caaz in enumerate(SWITCHES):
            key = _label(dither, caaz)
            m = report[key]
            ax = fig.add_subplot(grid[row, col])
            ax.set_title(
                f"dither {'开' if dither else '关'} · CAAZ {'开' if caaz else '关'}\n"
                f"SNDR {m['sndr_db']:.2f} dB · SFDR {m['sfdr_dbc']:.2f} dBc",
                fontsize=7.2,
                loc="left",
            )
            ax.plot(
                frequency[1:] / 1e6, arrays[f"{key}_spectrum_dbfs"][1:], color=COLORS[row], lw=0.7
            )
            ax.set(xlim=(0, 2.5), ylim=(-169, 3), yticks=[-160, -120, -80, -40, 0])
            if not col:
                ax.set_ylabel("幅度（dBFS）")
            if row:
                ax.set_xlabel("频率（MHz）")
            ax.grid(color="0.73", linestyle="--", alpha=0.63)
    offset = fig.add_subplot(grid[2, :])
    for caaz, shift, color in zip(SWITCHES, (-0.19, 0.19), COLORS, strict=True):
        values = [report[_label(d, caaz)]["mean_error_uv"] for d in SWITCHES]
        bars = offset.bar(
            np.arange(2) + shift,
            values,
            width=0.33,
            color=color,
            label=f"CAAZ {'开' if caaz else '关'}",
        )
        offset.bar_label(bars, labels=[f"{v:+.2f}" for v in values], padding=1.2, fontsize=7.2)
    offset.set_title("同号 80 微伏输入等效偏移在配对后的残留", loc="left", fontsize=7.8)
    offset.set(
        xticks=[0, 1],
        xticklabels=["dither 关", "dither 开"],
        ylim=(0, 108),
        yticks=[0, 40, 80],
        ylabel="误差均值（微伏）",
    )
    offset.legend(loc="lower right", bbox_to_anchor=(1, 1.02), fontsize=7.1, frameon=False, ncols=2)
    offset.grid(axis="y", color="0.73", linestyle="--", alpha=0.63)
    offset.set_axisbelow(True)
    fig.text(
        0.13,
        0.037,
        "CAAZ 关采用同速率的两笔平均，开采用偶减奇。两者均重新量化到 20 位。\n"
        "频谱不含直流，条形图单独显示误差均值。偏移为人工激励，未加入模拟噪声。",
        fontsize=7.1,
        va="bottom",
    )
    for ext, options in (
        ("png", {"dpi": 300}),
        ("pdf", {"metadata": {"CreationDate": None, "ModDate": None}}),
        ("svg", {"metadata": {"Date": None}}),
    ):
        fig.savefig(out / f"joint_dither_caaz.{ext}", **options)
    plt.close(fig)
    return font


def run(out: Path) -> dict:
    """对照共同输入与固定配置，校验对拍向量身份并归档。"""
    meta = json.loads((EVIDENCE / "summary.json").read_text())
    receipt = json.loads(RTL_RECEIPT.read_text())
    fixture = receipt["paired_3pct_fixture"]
    data_path = EVIDENCE / "data.npz"
    data_sha = digest(data_path)
    if fixture["input_data_sha256"] != data_sha or meta["artifact_sha256"]["data.npz"] != data_sha:
        raise ValueError("3% capacitance and training archive no longer matches the RTL fixture")
    experiment = ExperimentConfig(**meta["experiment"])
    cfg = CalibrationConfig(**meta["calibration"])
    coefficient = from_training(data_path)
    config_stream = "".join(f"{word:07x}\n" for word in coefficient.words()).encode("ascii")
    if hashlib.sha256(config_stream).hexdigest() != fixture["config_sha256"]:
        raise ValueError("frozen Q28 words do not match the compiled RTL replay configuration")
    offset = fixture["common_offset_v"]
    mapper = MapperTable.build(bridge=True)
    with np.load(data_path) as data:
        raw_reference = data["fig21_dem_tone_reference_v"]
        if len(raw_reference) != fixture["samples"] or len(raw_reference) != experiment.samples:
            raise ValueError(f"expected {experiment.samples} archived raw samples")
        plan = data["fig21_dem_tone_plan"]
        phase = (data["fig21_dem_tone_horizontal"], data["fig21_dem_tone_vertical"])
        active_word = data["fig21_dem_on_tone_word"]
        thresholds = data["nominal_threshold_v"]
        pool = ChargePool.from_caps(
            Capacitors(
                data["actual_main_cap_f"], data["actual_sub_cap_f"], data["actual_bridge_cap_f"]
            ),
            experiment,
        )

    pair_reference = raw_reference.reshape(-1, 2).mean(axis=1)
    fs_pair = experiment.fs_hz / 2
    frequency = np.fft.rfftfreq(len(pair_reference), 1 / fs_pair)
    arrays = {
        "raw_reference_v": raw_reference,
        "pair_reference_v": pair_reference,
        "plan": plan,
        "phase_horizontal": phase[0],
        "phase_vertical": phase[1],
        "dither_word_on": active_word,
        "frequency_hz": frequency,
    }
    groups = {}
    polarity = 1 - 2 * (np.arange(len(plan)) % 2)
    for dither in SWITCHES:
        word = active_word if dither else np.zeros_like(active_word)
        for caaz in SWITCHES:
            key = _label(dither, caaz)
            voltage = (polarity * raw_reference if caaz else raw_reference) + offset
            state, coarse, residue, adc2 = _capture(
                voltage,
                word,
                plan,
                phase,
                thresholds,
                meta["native_dither_step_v"],
                mapper,
                pool,
                cfg,
            )
            raw_code, vector_sha = _reconstruct(coefficient, word, state, coarse, adc2)
            if caaz and dither and vector_sha != fixture["vectors_sha256"]:
                raise ValueError(
                    "dither+CAAZ vector does not match the compiled RTL replay fixture"
                )
            even, odd = raw_code.reshape(-1, 2).T
            exact_2lsb = even - odd if caaz else even + odd + 1
            pair_code = exact_2lsb // 2
            if not np.all((pair_code >= -(1 << 19)) & (pair_code < 1 << 19)):
                raise ValueError("paired output exceeded the signed 20-bit range")
            output = (pair_code + 0.5) * meta["output_lsb20_v"]
            spectrum, metrics = _spectrum(
                output, pair_reference, experiment.full_scale_peak_v, experiment.tone_bin
            )
            groups[key] = {
                **metrics,
                "adc2_residue_min_v": float(residue.min()),
                "adc2_residue_max_v": float(residue.max()),
                "rejected_samples": 0,
                "vectors_sha256": vector_sha,
                "identical_to_compiled_rtl_fixture": dither and caaz,
            }
            arrays.update(
                {
                    f"{key}_raw_input_v": voltage,
                    f"{key}_raw_coarse": coarse,
                    f"{key}_command": state.switches.codes,
                    f"{key}_adc2_code": adc2,
                    f"{key}_adc2_residue_v": residue,
                    f"{key}_raw_signed_code": raw_code,
                    f"{key}_pair_exact_half_lsb": exact_2lsb,
                    f"{key}_pair_signed_code": pair_code,
                    f"{key}_spectrum_dbfs": spectrum,
                }
            )

    np.savez_compressed(out / "joint_dither_caaz_data.npz", **arrays)
    font = _plot(out, frequency, arrays, groups)
    result = {
        "scope": "same_archived_3pct_pool_ideal_charge_and_q28_digital_reconstruction",
        "manufacturing_seed": experiment.manufacturing_seed,
        "fixed_capacitor_sigma": experiment.mismatch_sigma_unit,
        "raw_samples": experiment.samples,
        "pair_outputs": len(pair_reference),
        "raw_rate_hz": experiment.fs_hz,
        "output_rate_hz": fs_pair,
        "output_bits": 20,
        "tone_hz": float(frequency[experiment.tone_bin]),
        "tone_peak_v": experiment.tone_peak_v,
        "analysis_band_hz": [0, fs_pair / 2],
        "fft_window": "rectangular coherent; DC excluded from SNDR and SFDR",
        "added_same_sign_offset_v": offset,
        "adc2_window_half_v": cfg.adc2_half_range_v,
        "groups": groups,
        "font_family": font,
        "pdf_fonttype": 3,
        "archived_fixture_identity": fixture,
        "source_sha256": {str(path.relative_to(ROOT)): digest(path) for path in INPUTS},
        "artifact_sha256": {path.name: digest(path) for path in sorted(out.iterdir())},
    }
    write_json(out / "summary.json", result)
    return result


def main() -> None:
    """选择尚未创建的仓库外输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(new_output(args.out))["groups"], ensure_ascii=False))


if __name__ == "__main__":
    main()
