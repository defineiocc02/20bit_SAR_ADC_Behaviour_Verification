"""用固定制造样本测量较大失配下 DEM 的逐码平均、波动及频谱。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

from adi_model.metrics import spectrum_dbfs

from . import run_effects as effects

SIGMA_PCT = (2.0, 3.0)
SEEDS = tuple(range(100, 106))
AVERAGES = (1, 4, 16, 64, 128)
PLANS = ("fixed_ab", "dynamic")
MODES = ("off", "on")
COLORS = {"off": effects.ORANGE, "on": effects.BLUE}
LINESTYLES = {"fixed_ab": "--", "dynamic": "-"}
PLAN_LABELS = {"fixed_ab": "Fixed A/B", "dynamic": "Dynamic"}


def _label(plan: str, mode: str) -> str:
    """为相同设置在三张图中使用一致的标签。"""
    return f"{PLAN_LABELS[plan]}, DEM {mode.upper()}"


@dataclass(frozen=True)
class Stimulus:
    """逐笔输入、粗码、物理计划和对应的两种实际掩码。"""

    vin: np.ndarray
    code: np.ndarray
    plan: np.ndarray
    masks: dict[str, tuple[np.ndarray, np.ndarray]]


def _stimulus(vin: np.ndarray, code: np.ndarray, plan: np.ndarray, phases: tuple) -> Stimulus:
    """通过交付映射器为同一序列生成两种掩码。"""
    masks = {mode: effects._map_record(code, phases, dem_enable=mode == "on") for mode in MODES}
    return Stimulus(vin, code, plan, masks)


def _observe(
    pool: effects.Capacitors,
    data: Stimulus,
    codebook: np.ndarray,
    cfg: effects.ExperimentConfig,
    mode: str,
) -> np.ndarray:
    """用独立两节点电荷求解和理想余量读出折算输入电压。"""
    q = effects._feedback_charge(pool, data.vin, data.plan, data.masks[mode], cfg)
    return codebook[data.code] + effects._voltage_equivalent(q, cfg)


def _distribution(values: np.ndarray) -> dict[str, float]:
    """描述预定制造样本的最小值、中位数和最大值。"""
    return {
        "min": float(np.min(values)),
        "median": float(np.median(values)),
        "max": float(np.max(values)),
    }


def _measure(cfg: effects.ExperimentConfig) -> dict:
    """配对测试 2% 与 3% 失配池的静态误差及相干频谱。"""
    nominal, _ = effects._cap_pool(cfg)
    codebook = effects._codebook(nominal, cfg)
    levels = 0.5 * (codebook + np.r_[codebook[1:], cfg.full_scale_peak_v])
    dynamic_plan, _, phases = effects._schedule(cfg.samples, cfg)
    sample = np.arange(cfg.samples)
    fixed_ab_plan = (
        np.arange(effects.GROUP_SIZE)[None, :] + (sample % 2 * effects.GROUP_SIZE)[:, None]
    )
    plans = {"fixed_ab": fixed_ab_plan, "dynamic": dynamic_plan}
    time_s = sample / cfg.fs_hz
    tone_v = cfg.tone_peak_v * np.sin(2 * np.pi * cfg.fin_hz * time_s)
    tone_code = (np.searchsorted(codebook, tone_v, side="right") - 1).astype(np.uint16)
    tone = _stimulus(tone_v, tone_code, dynamic_plan, phases)

    repeats = max(AVERAGES)
    static_plan = np.tile(dynamic_plan[:repeats], (len(levels), 1))
    static_phases = tuple(np.tile(phase[:repeats], len(levels)) for phase in phases)
    static = _stimulus(
        np.repeat(levels, repeats),
        np.repeat(np.arange(len(levels), dtype=np.uint16), repeats),
        static_plan,
        static_phases,
    )
    tone_stimuli = {name: replace(tone, plan=plan) for name, plan in plans.items()}
    static_stimuli = {
        name: replace(static, plan=np.tile(plan[:repeats], (len(levels), 1)))
        for name, plan in plans.items()
    }
    closure = {
        f"{name}_{mode}": float(
            np.max(np.abs(_observe(nominal, data, codebook, cfg, mode) - data.vin))
        )
        for name, data in static_stimuli.items()
        for mode in MODES
    }
    if max(closure.values()) > 1e-12:
        raise RuntimeError("nominal static charge closure failed")

    sample_shape = (len(SIGMA_PCT), len(SEEDS), len(PLANS), len(MODES))
    curves_shape = (*sample_shape, len(levels))
    residual = np.empty(curves_shape)
    variation = np.empty_like(residual)
    mean_error = np.empty_like(residual)
    average = np.empty((*sample_shape, len(AVERAGES)))
    waveforms = np.empty((*sample_shape, len(tone_v)))
    main_cap = np.empty((*sample_shape[:2], *nominal.main.shape))
    sub_cap = np.empty((*sample_shape[:2], *nominal.sub.shape))
    bridge_cap = np.empty((*sample_shape[:2], *nominal.bridge.shape))
    records = []

    for sigma_index, sigma in enumerate(SIGMA_PCT):
        for seed_index, seed in enumerate(SEEDS):
            _, die = effects._cap_pool(
                replace(cfg, mismatch_sigma_unit=sigma / 100, manufacturing_seed=seed)
            )
            main_cap[sigma_index, seed_index] = die.main
            sub_cap[sigma_index, seed_index] = die.sub
            bridge_cap[sigma_index, seed_index] = die.bridge
            errors = {
                (name, mode): (_observe(die, data, codebook, cfg, mode) - data.vin).reshape(
                    -1, repeats
                )
                for name, data in static_stimuli.items()
                for mode in MODES
            }
            # 同一颗的四组均值等权拟合一次，给该颗所有平均次数使用同一参考线。
            coefficients = np.polyfit(
                levels, np.mean([error.mean(axis=1) for error in errors.values()], axis=0), 1
            )
            common_line = np.polyval(coefficients, levels)
            for p, name in enumerate(PLANS):
                for m, mode in enumerate(MODES):
                    index = (sigma_index, seed_index, p, m)
                    error = errors[name, mode]
                    mean_error[index] = error.mean(axis=1)
                    residual[index] = mean_error[index] - common_line
                    variation[index] = error.std(axis=1, ddof=1)
                    for n_index, n in enumerate(AVERAGES):
                        block_means = error.reshape(len(levels), repeats // n, n).mean(axis=2)
                        average[(*index, n_index)] = np.sqrt(
                            np.mean((block_means - common_line[:, None]) ** 2)
                        )
                    waveform = _observe(die, tone_stimuli[name], codebook, cfg, mode)
                    waveforms[index] = waveform
                    metrics = effects._spectral_stats(waveform, cfg)
                    records.append(
                        {
                            "sigma_pct": sigma,
                            "seed": seed,
                            "plan": name,
                            "dem": mode,
                            "sndr_db": metrics["SNDR_dB"],
                            "sfdr_db": metrics["SFDR_dB"],
                            "spur_hz": metrics["spur_Hz"],
                            "static_peak_residual_v": float(np.max(np.abs(residual[index]))),
                            "static_residual_rms_v": float(average[(*index, -1)]),
                            "static_within_code_sigma_median_v": float(np.median(variation[index])),
                            "common_line_offset_v": float(coefficients[1]),
                            "common_line_gain_v_per_v": float(coefficients[0]),
                        }
                    )
    return {
        "records": records,
        "closure_v": closure,
        "levels": levels,
        "codebook": codebook,
        "residual": residual,
        "mean_error": mean_error,
        "variation": variation,
        "average": average,
        "waveforms": waveforms,
        "tone": tone,
        "plans": plans,
        "time_s": time_s,
        "phases": phases,
        "main_cap": main_cap,
        "sub_cap": sub_cap,
        "bridge_cap": bridge_cap,
    }


def _plot_static(path: Path, result: dict) -> None:
    """显示每个物理样本的逐码残差和码内波动，中位数用实线。"""
    fig, axes = plt.subplots(
        2, 2, figsize=(11.4, 7.4), sharex=True, sharey="col", layout="constrained"
    )
    fig.suptitle("Fixed capacitor mismatch | code-by-code linearity and spread", fontweight="bold")
    code = np.arange(len(result["levels"]))
    for s, sigma in enumerate(SIGMA_PCT):
        for col, (field, ylabel) in enumerate(
            (("residual", "Mean residual (mV)"), ("variation", "Within-code std. dev. (mV)"))
        ):
            axis = axes[s, col]
            for p, plan in enumerate(PLANS):
                for m, mode in enumerate(MODES):
                    traces_mv = 1e3 * result[field][s, :, p, m, :]
                    axis.fill_between(
                        code,
                        traces_mv.min(axis=0),
                        traces_mv.max(axis=0),
                        color=COLORS[mode],
                        alpha=0.09,
                    )
                    axis.plot(
                        code,
                        np.median(traces_mv, axis=0),
                        color=COLORS[mode],
                        ls=LINESTYLES[plan],
                        lw=1.1,
                        label=_label(plan, mode),
                    )
            title = (
                "Mean of 128 trials, shared line removed"
                if col == 0
                else "Trial-to-trial variation"
            )
            axis.set(title=f"{sigma:g}% unit mismatch | {title}", ylabel=ylabel, xlim=(0, 511))
            axis.grid(alpha=0.2)
            if s == 0:
                axis.legend(fontsize=8)
            else:
                axis.set_xlabel("Nominal 9-bit code")
    fig.supxlabel(
        "Lines are codewise medians, shading spans all six seeds. Ideal residue readout.",
        fontsize=9,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def _plot_average(path: Path, result: dict) -> None:
    """比较相同平均次数下去共同直线后的误差。"""
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.4), sharey=True, layout="constrained")
    fig.suptitle("Residual vs. number of conversions averaged per code", fontweight="bold")
    for s, (axis, sigma) in enumerate(zip(axes, SIGMA_PCT, strict=True)):
        for p, plan in enumerate(PLANS):
            for m, mode in enumerate(MODES):
                curves_mv = 1e3 * result["average"][s, :, p, m, :]
                axis.fill_between(
                    AVERAGES,
                    curves_mv.min(axis=0),
                    curves_mv.max(axis=0),
                    color=COLORS[mode],
                    alpha=0.10,
                )
                axis.plot(
                    AVERAGES,
                    np.median(curves_mv, axis=0),
                    color=COLORS[mode],
                    marker="o",
                    lw=1.4,
                    ls=LINESTYLES[plan],
                    label=_label(plan, mode),
                )
        axis.set_xscale("log", base=2)
        axis.set_xticks(AVERAGES, AVERAGES)
        axis.set(title=f"{sigma:g}% unit mismatch", xlabel="Conversions averaged, N")
        axis.grid(alpha=0.22)
        axis.legend(fontsize=8)
    axes[0].set_ylabel("Residual RMS across codes and blocks (mV)")
    fig.supxlabel(
        "Shaded bands show six-seed min–max. Non-overlapping blocks use the same 128 trials.",
        fontsize=9,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def _plot_spectra(path: Path, result: dict, cfg: effects.ExperimentConfig) -> None:
    """显示预先指定种子的频谱及全部种子的指标分布。"""
    fig, axes = plt.subplots(2, 2, figsize=(10.8, 7.1), layout="constrained")
    fig.suptitle("Single-conversion spectrum | ideal residue readout", fontweight="bold")
    for s, sigma in enumerate(SIGMA_PCT):
        spectrum, dots = axes[:, s]
        for p, plan in enumerate(PLANS):
            for m, mode in enumerate(MODES):
                freq, dbfs = spectrum_dbfs(
                    result["waveforms"][s, 0, p, m],
                    cfg.fs_hz,
                    cfg.full_scale_peak_v,
                    window="boxcar",
                )
                spectrum.plot(
                    freq / 1e6,
                    dbfs,
                    color=COLORS[mode],
                    lw=0.75,
                    ls=LINESTYLES[plan],
                    label=_label(plan, mode),
                )
                rows = [
                    r
                    for r in result["records"]
                    if r["sigma_pct"] == sigma and r["plan"] == plan and r["dem"] == mode
                ]
                for x, field in enumerate(("sndr_db", "sfdr_db")):
                    values = [r[field] for r in rows]
                    pos = x + (2 * p + m - 1.5) * 0.13
                    dots.scatter(
                        np.full(len(values), pos),
                        values,
                        color=COLORS[mode],
                        marker="x" if p == 0 else "o",
                        alpha=0.50,
                        s=19,
                    )
                    dots.scatter(pos, np.median(values), color=COLORS[mode], marker="D", s=26)
        spectrum.set(
            title=f"{sigma:g}% | preselected seed {SEEDS[0]}",
            xlabel="Frequency (MHz)",
            ylabel="Amplitude (dBFS/bin)",
            xlim=(0, 5),
            ylim=(-113, 3),
        )
        spectrum.legend(fontsize=8, loc="upper right")
        dots.set(title="All seeds: fixed ×, dynamic ○, median ◇", ylabel="dB", xlim=(-0.36, 1.36))
        dots.set_xticks((0, 1), ("SNDR", "SFDR"))
        for axis in (spectrum, dots):
            axis.grid(alpha=0.22)
    fig.supxlabel(
        "10 MS/s, 4096 coherent points, 998.535 kHz. Static averaging is a separate test.",
        fontsize=9,
    )
    fig.savefig(path, dpi=170, facecolor="white")
    plt.close(fig)


def _write_tables(out: Path, result: dict) -> None:
    """归档每颗指标、每码统计和所有平均次数。"""
    with (out / "dem_high_sigma_samples.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(result["records"][0]))
        writer.writeheader()
        writer.writerows(result["records"])
    with (
        (out / "dem_high_sigma_static.csv").open("w", encoding="utf-8", newline="") as static,
        (out / "dem_high_sigma_average.csv").open("w", encoding="utf-8", newline="") as average,
    ):
        stat = csv.writer(static)
        avg = csv.writer(average)
        stat.writerow(
            (
                "sigma_pct",
                "seed",
                "plan",
                "dem",
                "code",
                "input_v",
                "mean_error_v",
                "common_line_residual_v",
                "within_code_sigma_v",
            )
        )
        avg.writerow(("sigma_pct", "seed", "plan", "dem", "averaged_conversions", "residual_rms_v"))
        for s, sigma in enumerate(SIGMA_PCT):
            for d, seed in enumerate(SEEDS):
                for p, plan in enumerate(PLANS):
                    for m, mode in enumerate(MODES):
                        index = (s, d, p, m)
                        for code, vin in enumerate(result["levels"]):
                            stat.writerow(
                                (
                                    sigma,
                                    seed,
                                    plan,
                                    mode,
                                    code,
                                    vin,
                                    result["mean_error"][(*index, code)],
                                    result["residual"][(*index, code)],
                                    result["variation"][(*index, code)],
                                )
                            )
                        for n, rms in zip(AVERAGES, result["average"][index], strict=True):
                            avg.writerow((sigma, seed, plan, mode, n, rms))


def _write_waveforms(out: Path, result: dict) -> None:
    """保存用于绘制和复算相干频谱的完整记录。"""
    tone = result["tone"]
    np.savez_compressed(
        out / "dem_high_sigma_tone.npz",
        sigma_pct=SIGMA_PCT,
        seeds=SEEDS,
        plan_kind=PLANS,
        dem=MODES,
        time_s=result["time_s"],
        input_v=tone.vin,
        output_v=result["waveforms"],
        coarse_code=tone.code,
        nominal_threshold_v=result["codebook"],
        conv_plan=np.stack([result["plans"][name] for name in PLANS]),
        phase_h=result["phases"][0],
        phase_v=result["phases"][1],
        main_mask_off=tone.masks["off"][0],
        main_mask_on=tone.masks["on"][0],
        sub_mask_off=tone.masks["off"][1],
        sub_mask_on=tone.masks["on"][1],
        physical_main_f=result["main_cap"],
        physical_sub_f=result["sub_cap"],
        physical_bridge_f=result["bridge_cap"],
    )


def _summary(result: dict, cfg: effects.ExperimentConfig) -> dict:
    """汇总各设置在六颗预设虚拟样本上的结果。"""
    series = []
    metrics = (
        "sndr_db",
        "sfdr_db",
        "static_peak_residual_v",
        "static_residual_rms_v",
        "static_within_code_sigma_median_v",
    )
    for s, sigma in enumerate(SIGMA_PCT):
        for p, plan in enumerate(PLANS):
            for m, mode in enumerate(MODES):
                records = [
                    r
                    for r in result["records"]
                    if r["sigma_pct"] == sigma and r["plan"] == plan and r["dem"] == mode
                ]
                series.append(
                    {
                        "sigma_pct": sigma,
                        "plan": plan,
                        "dem": mode,
                        **{
                            key: _distribution(np.array([r[key] for r in records]))
                            for key in metrics
                        },
                        "block_average_residual_rms_v": {
                            str(n): _distribution(result["average"][s, :, p, m, idx])
                            for idx, n in enumerate(AVERAGES)
                        },
                    }
                )
    sources = (
        Path(__file__).resolve().relative_to(effects.ROOT).as_posix(),
        "task1/experiments/run_effects.py",
        "task1/model/module1.py",
        "task1/model/scheduler.py",
        "task1/model/fig11_dem.py",
        "src/adi_model/charge_ref.py",
        "src/adi_model/metrics.py",
    )
    return {
        "schema": "task1_dem_high_sigma_v1",
        "model_scope": "Fig.11 固定制造失配、实际 DEM 位图、两节点电荷和理想余量读出。用于单独诊断 DEM 平均机制。真实 ADC2、热噪声和权重校准属于另行实验。",
        "configuration": {
            "sigma_pct": SIGMA_PCT,
            "seeds": SEEDS,
            "fs_hz": cfg.fs_hz,
            "samples": cfg.samples,
            "tone_bin": cfg.tone_bin,
            "tone_hz": cfg.fin_hz,
            "tone_peak_v": cfg.tone_peak_v,
            "full_scale_peak_v": cfg.full_scale_peak_v,
            "schedule_seed": cfg.schedule_seed,
            "dem_seed": cfg.dem_seed,
            "plans": {
                "fixed_ab": "片 0–7 与片 8–15 按样本奇偶固定交替。",
                "dynamic": "交付模块一模型的有序选片记录。",
            },
            "static_levels": len(result["levels"]),
            "static_repeats": max(AVERAGES),
            "block_sizes": AVERAGES,
            "fit_line": "每颗的四组共用一条最小二乘线，拟合目标是 N=128 时四组逐码均值误差的等权平均。所有 N 均沿用此线。",
            "unit_cap_f": cfg.unit_cap_f,
            "nominal_bridge_beta": 1 / 17,
            "plot_fft_window": "boxcar",
            "sfdr_residual_fft_window": "blackmanharris",
        },
        "nominal_closure_max_v": result["closure_v"],
        "series": series,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
            "platform": platform.system(),
        },
        "source_sha256": {
            source: hashlib.sha256((effects.ROOT / source).read_bytes()).hexdigest()
            for source in sources
        },
    }


def run(out: Path) -> dict:
    """生成 2% 和 3% 固定失配对照的全部图和数据。"""
    cfg = effects.ExperimentConfig()
    result = _measure(cfg)
    out.mkdir(parents=True, exist_ok=True)
    _write_tables(out, result)
    _write_waveforms(out, result)
    plt.switch_backend("Agg")
    _plot_static(out / "dem_high_sigma_static.png", result)
    _plot_average(out / "dem_high_sigma_average.png", result)
    _plot_spectra(out / "dem_high_sigma_spectra.png", result, cfg)
    summary = _summary(result, cfg)
    (out / "dem_high_sigma_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    """从命令行运行并打印每组中位数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="实验数据和图的输出目录")
    summary = run(parser.parse_args().out)
    for row in summary["series"]:
        print(
            f"{row['sigma_pct']:g}% {row['plan']} DEM {row['dem']}: "
            f"SNDR {row['sndr_db']['median']:.2f} dB, SFDR {row['sfdr_db']['median']:.2f} dB, "
            f"static rms {row['static_residual_rms_v']['median'] * 1e3:.3f} mV"
        )


if __name__ == "__main__":
    main()
