# =============================================================================
# 文件名     : behavioral_closure.py
# 模块名     : task2.eight.behavioral_closure
# 版本       : v1.0
# 功能       : 检查前端余量保护、多种子 DEM／NLMS 和 CAAZ 低频噪声。
# 设计说明   :
#   * 所有结论是行为实验；端点预测使用理想外部余量标志，不代表现有宏。
#   * 原始制造样本、输入与估计系数保持配对；图与统计不改变旧数据。
#   * CAAZ 是独立样本域实验；未建 ADC2、DEM 或模拟保持电路。
#   * 输出目录必须尚不存在，避免覆盖之前的结果与产生混合证据。
# -----------------------------------------------------------------------------
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""生成端点保护、多种子线性度和 CAAZ 低频噪声的行为对照。"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import welch

from adi_model.capacitors import ExperimentConfig, cap_pool
from task1.experiments.adctoolbox_calibration import decode_switches, quantize
from task1.experiments.full20_linearity_plots import chinese_style
from task1.experiments.residue_control_study import MapperTable
from task1.model.codebook import codebook
from task2.eight.model import (
    ChargePool,
    coarse_decision,
    conversion_state,
    decode,
    native_step_v,
    weighted_sum,
)
from task2.eight.tests.audit_archives import _check_metrics

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "task2/evidence/behavioral_closure"
ARCHIVE = ROOT / "task2/evidence/eight_negative"
SEEDS = (100, 101, 102, 103, 104)


# =============================================================================
# 1. 固定制造池：复核已有逐码宽和频谱，再汇总单颗芯片的配对差异
# =============================================================================
def _summary_path(percent: int, seed: int) -> Path:
    """返回相应制造样本的目录，seed 100 使用原始基线。"""
    if seed == 100:
        return ARCHIVE / f"sigma_{percent}pct"
    return EVIDENCE / f"sigma_{percent}pct_seed{seed}"


def _sha(path: Path) -> str:
    """计算文件 SHA-256，确保图与输入数据身份可追踪。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _multiseed(output: Path) -> dict:
    """复核 10 个制造样本，输出四组 INL、SNDR、SFDR 的配对图。

    Args:
        output: 已存在的独立输出目录，图文件写入该目录。

    Returns:
        每个失配档、种子和开关组合的指标及原始数据摘要。

    每颗制造样本内共用物理电容、输入和选片计划；两种映射各自训练权重。
    原有前台增益参考共享，不能把两条链路描述成完全独立训练。
    逐码宽与频谱由独立复算器检查，图只使用校验后的摘要。
    只比较关闭扰动的一组数据；开扰动时需要另行定义对应对照。
    理想后级与关闭热噪声是输入条件，不是芯片动态范围的实测值。
    五个种子用于显示样本敏感度，数量不足以估算工艺良率。
    """
    rows = []
    groups = (
        ("no_dem", "nominal"),
        ("fig21_dem", "nominal"),
        ("no_dem", "lms"),
        ("fig21_dem", "lms"),
    )
    for percent in (2, 3):
        for seed in SEEDS:
            source = _summary_path(percent, seed)
            summary = json.loads((source / "summary.json").read_text())
            assert summary["experiment"]["manufacturing_seed"] == seed
            assert _sha(source / "data.npz") == summary["artifact_sha256"]["data.npz"]
            with np.load(source / "data.npz") as data:
                verified = _check_metrics(data, summary)
            for group, mode in groups:
                rec = summary["groups"][group]
                linearity = rec["linearity"]["off"][mode]
                tone = rec["tone"]["off"][mode]
                rows.append(
                    {
                        "sigma_percent": percent,
                        "seed": seed,
                        "group": group,
                        "calibration": mode,
                        "inl_peak_lsb20": linearity["endpoint_inl_peak_lsb20"],
                        "dnl_rms_lsb20": linearity["dnl_rms_lsb20"],
                        "sndr_db": tone["sndr_db"],
                        "sfdr_db": tone["sfdr_dbc"],
                        "data_sha256": _sha(source / "data.npz"),
                        "verified": verified,
                    }
                )
    fig, axes = plt.subplots(2, 3, figsize=(15, 8), constrained_layout=True)
    labels = ("无DEM\n无LMS", "DEM\n无LMS", "无DEM\nLMS", "DEM\nLMS")
    for row, percent in enumerate((2, 3)):
        for col, (metric, title) in enumerate(
            (
                ("inl_peak_lsb20", "峰值 INL（LSB20）"),
                ("sndr_db", "SNDR（dB）"),
                ("sfdr_db", "SFDR（dBc）"),
            )
        ):
            ax = axes[row, col]
            for seed in SEEDS:
                values = [
                    next(
                        x[metric]
                        for x in rows
                        if x["sigma_percent"] == percent
                        and x["seed"] == seed
                        and x["group"] == group
                        and x["calibration"] == mode
                    )
                    for group, mode in groups
                ]
                ax.plot(range(4), values, marker="o", lw=1, ms=3, label=f"种子 {seed}")
            if col == 0:
                ax.set_yscale("log")
            ax.set_title(f"{percent}% 失配 · {title}")
            ax.set_xticks(range(4), labels)
            ax.grid(alpha=0.25)
    axes[0, 1].legend(ncol=3, fontsize=8)
    fig.suptitle("同池配对：DEM 与 LMS 的五个制造种子（dither 关闭）")
    fig.savefig(output / "dem_lms_multiseed.png", dpi=200)
    plt.close(fig)
    return {
        "rows": rows,
        "seed_count_per_sigma": len(SEEDS),
        "scope": "2% and 3% use same seed indices; 10 V span, common window, 12-bit ideal ADC2; no thermal noise",
    }


# =============================================================================
# 2. 满幅端点：仅演示预采样的外部保护标志，不把理想输入当数字已知量
# =============================================================================
def apply_external_guard(word: np.ndarray, guard: np.ndarray) -> np.ndarray:
    """用外部预采样保护标志将六位字置零。

    Args:
        word: 与样本逐笔对应的整数数组，元素范围为 0 至 63。
        guard: 同形布尔数组；True 表示本笔在采样前屏蔽扰动。

    Returns:
        uint8 数组；保护位置为零，其余位置保持原字。

    Raises:
        ValueError: 数组形状、标志类型或扰动字范围不符合合同。

    保护标志必须在模拟采样前取得且与采样 ID 同笔绑定。
    本函数不检查标志的模拟来源，也不实现保护电路或 PRNG 更新时序。
    不能在采样完成后用新字覆盖已发生的扰动，否则数字补偿会错绑。
    """
    word = np.asarray(word)
    guard = np.asarray(guard)
    if word.shape != guard.shape or guard.dtype.kind != "b":
        raise ValueError("guard must be a boolean array matching word shape")
    if word.dtype.kind not in "iu" or np.any((word < 0) | (word >= 64)):
        raise ValueError("dither word must be an integer from 0 through 63")
    return np.where(guard, 0, word).astype(np.uint8)


def _endpoints(output: Path) -> dict:
    """枚举 64 字及端点网格，区分前级拒绝和后级超窗。

    Args:
        output: 已存在的目录，保存图与逐输入点拒绝计数。

    Returns:
        两种保护状态的拒绝次数、后级越界次数及覆盖条件。

    输入采用左闭右开区间，+5 V 精确端点需要单独确定输出编码合同。
    保护阈值取所有字中最苛刻的名义下限，并非真实比较器的保证阈值。
    此实验使用理想输入生成保护标志，仅是外部标志合同的可行性上界。
    ADC2 与浮点重构复用 seed 100 的冻结系数及固定切片、固定相位。
    网格为有限采样，不能据零计数推断所有连续输入和所有物理状态安全。
    若前级未越界但后级或最终码越界，必须分别报告，不得互相抵消。
    """
    cfg = ExperimentConfig()
    nominal, _ = cap_pool(cfg)
    threshold = np.r_[codebook(nominal, cfg), cfg.full_scale_peak_v]
    step = native_step_v(cfg)
    terms = decode(np.arange(64, dtype=np.uint8))
    # 每个字合法下限为阈值 U 加 lower；最苛刻下限供保守外部保护使用。
    limits = threshold[terms.upper] + terms.lower_eighth * step / 8
    boundary = float(np.max(limits))
    x = np.linspace(-5.0, 5.0, 20001, endpoint=False)
    before = np.zeros(len(x), dtype=int)
    after = np.zeros(len(x), dtype=int)
    saturated = {"2": 0, "3": 0}
    output_out_of_range = {"2": 0, "3": 0}
    plan = np.broadcast_to(np.arange(8, dtype=np.int8), (len(x), 8))
    phases = (np.zeros(len(x), dtype=np.uint8), np.zeros(len(x), dtype=np.uint8))
    table = MapperTable.build(bridge=True)
    pools = {}
    coefficients = {}
    for percent in (2, 3):
        _, caps = cap_pool(replace(cfg, mismatch_sigma_unit=percent / 100))
        pools[str(percent)] = ChargePool.from_caps(caps, cfg)
        # 沿用混合扰动训练系数；端点扫描不使用真实电容反推校准权重。
        with np.load(ARCHIVE / f"sigma_{percent}pct/data.npz") as archive:
            coefficients[str(percent)] = (
                archive["frozen_gain"].copy(),
                archive["fig21_dem_shared_frozen_weight_v"].copy(),
            )
    for value in range(64):
        word = np.full(len(x), value, dtype=np.uint8)
        initial = decode(word)
        _, _, valid = coarse_decision(x, initial, threshold, step)
        before += ~valid
        # 该比较只生成理想保护标志；实际电路需要在采样前独立获得该标志。
        guarded_word = apply_external_guard(word, x < boundary)
        guarded = decode(guarded_word)
        _, command, guarded_valid = coarse_decision(x, guarded, threshold, step)
        after += ~guarded_valid
        assert np.all(guarded_valid)
        state = conversion_state(command, plan, phases, guarded, threshold, step, table)
        for percent, pool in pools.items():
            gain, base = pool.affine(plan, state.features)
            residue = gain * (x + 5) - base
            valid_adc = (residue >= -0.1) & (residue < 0.1)
            saturated[percent] += int(np.count_nonzero(~valid_adc))
            # 对合法 ADC2 码进行未限幅重构，区分前级成功与最终码越界。
            if np.any(valid_adc):
                fine = quantize(residue[valid_adc], 12, 0.1)
                fitted_gain, fitted_weight = coefficients[percent]
                ids = plan[valid_adc]
                estimate = (
                    state.nominal_base_v[valid_adc]
                    + decode_switches(fine, 12, 0.1)
                    + weighted_sum(ids, state.features[valid_adc], fitted_weight)
                ) / fitted_gain[ids].sum(axis=1) - 5
                output_out_of_range[percent] += int(
                    np.count_nonzero((estimate < -5) | (estimate >= 5))
                )
    assert not np.any(after)
    np.savez_compressed(
        output / "endpoint_grid.npz",
        input_v=x,
        rejected_before=before,
        rejected_after=after,
        guard_active=x < boundary,
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    for ax, limits in zip(axes, ((-5, 5), (-5, -4.8)), strict=True):
        ax.plot(x, before / 64 * 100, label="保护关闭")
        ax.plot(x, after / 64 * 100, label="理想预采样保护")
        ax.axvline(boundary, color="black", ls="--", label=f"阈值 {boundary:.6f} V")
        ax.set(xlim=limits, xlabel="差分输入（V；+5 V 不含）", ylabel="64 字前级拒绝率（%）")
        ax.grid(alpha=0.3)
    axes[1].legend()
    fig.suptitle("端点候选：负端关闭扰动，保护标志须在采样前由外部提供")
    fig.savefig(output / "dither_endpoint_guard.png", dpi=200)
    plt.close(fig)
    return {
        "candidate": "pre-sampling external guard; ideal oracle used only to generate flag",
        "boundary_v": boundary,
        "input_domain_v": "[-5, 5)",
        "positive_endpoint": "+5 V excluded; output saturation convention required",
        "grid_points": len(x),
        "dither_words": 64,
        "samples_per_case": len(x) * 64,
        "rejected_before": int(before.sum()),
        "rejected_after": int(after.sum()),
        "guarded_input_points": int(np.count_nonzero(x < boundary)),
        "adc2_overrange_count": saturated,
        "calibrated_output_out_of_range_count": output_out_of_range,
        "adc2_input_half_range_v": 0.1,
        "pool_seed": 100,
        "slot_ids": list(range(8)),
        "phase": [0, 0],
        "scope": "fixed physical slots and phase; archived float NLMS coefficients; physical guard latency not evaluated",
    }


# =============================================================================
# 3. 样本域低频噪声：同时分离普通平均、斩波及共享 AZ 的贡献
# =============================================================================
def _caaz_lowfreq(output: Path) -> dict:
    """对同一 1/f 漂移、独立白噪声和 AZ 噪声比较三种配对。

    Args:
        output: 已存在的目录，保存功率谱和配对误差数组。

    Returns:
        输入噪声、采样条件、Welch 参数以及各组积分和全带宽 RMS。

    三组使用相同的低频失调、独立白噪声和原始 AZ 随机向量。
    先比较普通平均与斩波以隔离慢变误差，再比较独立 AZ 与共享 AZ。
    共享组仅复制偶笔 AZ 样本，不能把每笔独立噪声也设成共享。
    输入为零，因而这是输入等效误差谱，不是完整 ADC 的 SNDR。
    此理想浮点样本实验没有 ADC2 量化、DEM、dither 或物理保持误差。
    Welch 结果使用 5 MS/s 配对频率，所有曲线保持同一测量带宽。
    """
    fs = 10_000_000.0
    count = 1 << 20
    rng = np.random.default_rng(20261109)
    frequency = np.fft.rfftfreq(count, 1 / fs)
    spectral = rng.normal(size=len(frequency)) + 1j * rng.normal(size=len(frequency))
    spectral[0] = 0
    spectral[-1] = spectral[-1].real
    spectral[1:] /= np.sqrt(frequency[1:])
    flicker = np.fft.irfft(spectral, n=count)
    flicker *= 80e-6 / np.std(flicker)
    # 低频失调被视为两笔之间缓慢变化，不错误地强制它们完全相等。
    drift_hz = 100 * fs / count
    drift = 100e-6 * np.sin(2 * np.pi * drift_hz * np.arange(count) / fs)
    independent = rng.normal(0, 10e-6, count)
    az = rng.normal(0, 20e-6, count)
    shared = np.repeat(az[::2], 2)
    slow = flicker + drift
    ordinary = (
        slow[::2] + slow[1::2] + independent[::2] + independent[1::2] + az[::2] + az[1::2]
    ) / 2
    chop_independent = (
        slow[::2] - slow[1::2] + independent[::2] - independent[1::2] + az[::2] - az[1::2]
    ) / 2
    chop_shared = (
        slow[::2] - slow[1::2] + independent[::2] - independent[1::2] + shared[::2] - shared[1::2]
    ) / 2
    cases = {
        "ordinary_independent": ordinary,
        "chop_independent": chop_independent,
        "caaz_shared": chop_shared,
    }
    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    # 保存原始噪声分量，便于不调用本实验函数独立重构三组配对。
    arrays = {"slow_offset_v": slow, "independent_noise_v": independent, "az_independent_v": az}
    stats = {}
    labels = {
        "ordinary_independent": "普通平均、独立 AZ",
        "chop_independent": "斩波、独立 AZ",
        "caaz_shared": "斩波、共享 AZ",
    }
    for name, y in cases.items():
        f, psd = welch(y, fs=fs / 2, window="hann", nperseg=65536, noverlap=32768)
        arrays[name] = y
        arrays[name + "_psd_v2_per_hz"] = psd
        stats[name] = {
            "rms_uv": float(np.std(y) * 1e6),
            "integrated_153hz_10khz_uv": float(
                np.sqrt(np.trapezoid(psd[(f >= 153) & (f <= 10000)], f[(f >= 153) & (f <= 10000)]))
                * 1e6
            ),
        }
        ax.semilogx(f[1:], 10 * np.log10(np.maximum(psd[1:], 1e-40)), label=labels[name])
    ax.set(
        xlabel="频率（Hz）",
        ylabel="输入等效误差 PSD（dBV^2/Hz）",
        title="CAAZ 样本域：普通平均、斩波、共享 AZ 的低频误差",
    )
    ax.grid(alpha=0.3, which="both")
    ax.legend()
    fig.savefig(output / "caaz_low_frequency.png", dpi=200)
    plt.close(fig)
    np.savez_compressed(output / "caaz_low_frequency.npz", frequency_hz=f, **arrays)
    return {
        "samples": count,
        "sample_rate_hz": fs,
        "pair_rate_hz": fs / 2,
        "noise_seed": 20261109,
        "flicker_rms_uv": 80,
        "drift_hz": drift_hz,
        "drift_peak_uv": 100,
        "az_sigma_uv": 20,
        "independent_sigma_uv": 10,
        "welch_nperseg": 65536,
        "welch_bin_hz": fs / 2 / 65536,
        "integral_selected_bins_hz": [float(f[f >= 153][0]), float(f[f <= 10000][-1])],
        "results": stats,
        "scope": "noise-only ideal arithmetic, no ADC2 quantization, DEM or transistor model",
    }


def main() -> None:
    """校验输出路径，运行三类实验并保存可追溯数值。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    chinese_style()
    report = {
        "source_sha256": {"task2/eight/behavioral_closure.py": _sha(Path(__file__))},
        "numpy_version": np.__version__,
        "multiseed": _multiseed(args.out),
        "endpoint": _endpoints(args.out),
        "caaz_lowfreq": _caaz_lowfreq(args.out),
    }
    (args.out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"endpoint": report["endpoint"], "caaz": report["caaz_lowfreq"]["results"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
