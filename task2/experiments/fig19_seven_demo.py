"""重跑 Fig. 19 七片工程等效实验并生成静态与动态对照图。"""

from __future__ import annotations

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from platform import python_version

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from adctoolbox.spectrum import compute_spectrum
from matplotlib.ticker import MaxNLocator
from task1.experiments.full20_linearity_plots import _chinese_style
from task2.model.dither_word_gen import DEFAULT_DITHER_SEED, DitherWordGenerator
from task2.model.fig19_seven import (
    ADC2_CODES,
    COARSE_CODES,
    COARSE_LSB_V,
    DEFAULT_CONFIG,
    FULL_SCALE_V,
    INPUT_MAX_V,
    INPUT_MIN_V,
    OUTPUT_CODES,
    OUTPUT_LSB_V,
    ModelConfig,
    code_density,
    convert,
    decode_words,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "task2/evidence/fig19_seven"
STATIC_LIMIT_V = 188 * COARSE_LSB_V
SAMPLE_RATE_HZ = 10_000_000
TONE_SAMPLES = 4096
TONE_BIN = 409
TONE_PEAK_V = 10 / 3
SIGNED_ZERO = OUTPUT_CODES // 2
LOWER_WORD_MASK = 0b010111
ARMS = (("off", "关闭 dither", "#555555"), ("on", "开启 dither", "#0072B2"))
# 沿用历史归档的英寸画布尺寸，使 PNG 与 SVG 可逐字节复现。
LINEARITY_FIGURE_IN = (5.74, 6.25)
SPECTRUM_FIGURE_IN = (5.74, 4.76)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rms(value: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(value))))


def _span(value: np.ndarray) -> list[float]:
    return [float(np.min(value)), float(np.max(value))]


def _linearity(words: np.ndarray, code_span: np.ndarray) -> tuple[dict, dict]:
    density = code_density(-STATIC_LIMIT_V, STATIC_LIMIT_V, words, require_prefix_valid=True)
    width = density.width_v[code_span]
    dnl = width / OUTPUT_LSB_V - 1
    # 端点 INL 作用在码边界，横轴比 DNL 的逐码宽度多一个点。
    inl = np.r_[0.0, np.cumsum(dnl)]
    inl -= np.linspace(0.0, inl[-1], len(inl))
    rejection = [
        density.pre_adc2_rejected_v,
        density.adc2_rejected_v,
        density.output_rejected_v,
    ]
    if not np.isclose(density.width_v.sum(), 2 * STATIC_LIMIT_V, atol=1e-12, rtol=0):
        raise RuntimeError("static integration lost input width")
    if sum(rejection) > 1e-12:
        raise RuntimeError("the common static window contains an invalid conversion")
    metrics = {
        "dnl_range_lsb20": _span(dnl),
        "dnl_rms_lsb20": _rms(dnl),
        "inl_range_lsb20": _span(inl),
        "zero_width_codes": int(np.count_nonzero(width <= 1e-15)),
        "mass_v": float(density.width_v.sum()),
        "rejected_input_width_v_by_stage": rejection,
    }
    arrays = {"width_v": density.width_v, "dnl_lsb20": dnl, "inl_lsb20": inl}
    return metrics, arrays


def _tone(input_v: np.ndarray, words: np.ndarray) -> tuple[dict, dict]:
    result = convert(input_v, words)
    if not np.all(result.accepted_prefix_guard):
        raise RuntimeError("tone samples must pass the direct and prefix range checks")
    terms = decode_words(words)
    nominal_coarse_v = INPUT_MIN_V + result.coarse_code * COARSE_LSB_V
    oracle_residue = input_v - terms.lower_v - nominal_coarse_v
    dac_v = INPUT_MIN_V + result.command_code * COARSE_LSB_V
    closure_error = dac_v + result.residue_v + terms.total_v - input_v
    residue_error = result.residue_v - oracle_residue
    if np.max(np.abs(closure_error)) > 1e-14 or np.max(np.abs(residue_error)) > 1e-14:
        raise RuntimeError("seven-slice charge path disagrees with the independent scalar oracle")

    spectrum = compute_spectrum(
        result.output_v,
        fs=SAMPLE_RATE_HZ,
        max_scale_range=(-5.0, 5.0),
        win_type="rectangular",
        side_bin=0,
    )
    if spectrum["plot_data"]["fundamental_bin"] != TONE_BIN:
        raise RuntimeError("ADCToolbox did not identify the coherent input tone")
    # 独立从双边 FFT 的功率和、峰值核对工具箱使用的整谱积分口径。
    power = np.square(np.abs(np.fft.fft(result.output_v)))
    signal_power = power[TONE_BIN] + power[-TONE_BIN]
    power[[0, TONE_BIN, -TONE_BIN]] = 0.0
    sndr = 10 * np.log10(signal_power / power.sum())
    sfdr = 10 * np.log10((signal_power / 2) / power.max())
    toolbox = spectrum["metrics"]
    np.testing.assert_allclose(
        [sndr, sfdr], [toolbox["sndr_dbc"], toolbox["sfdr_dbc"]], atol=1e-7, rtol=0
    )
    spur_bin = spectrum["plot_data"]["spur_bin_idx"]
    error = result.output_v - input_v
    fs_peak_v = FULL_SCALE_V / 2
    normalized_fft = np.abs(np.fft.rfft(error)) / (TONE_SAMPLES * fs_peak_v)
    normalized_fft[1:-1] *= 2
    error_dbfs = 20 * np.log10(np.maximum(normalized_fft, np.finfo(float).tiny))
    # 图只显示非基波误差分量，SNDR/SFDR 仍从完整输出计算。
    error_dbfs[[0, TONE_BIN]] = np.nan
    metrics = {
        "accepted_samples": int(np.count_nonzero(result.accepted_direct)),
        "accepted_with_prefix_guard": int(np.count_nonzero(result.accepted_prefix_guard)),
        "sndr_dbc": float(spectrum["metrics"]["sndr_dbc"]),
        "sfdr_dbc": float(spectrum["metrics"]["sfdr_dbc"]),
        "independent_fft_sndr_dbc": float(sndr),
        "independent_fft_sfdr_dbc": float(sfdr),
        "largest_spur_bin": int(spur_bin),
        "largest_spur_hz": float(spectrum["plot_data"]["freq"][spur_bin]),
        "largest_spur_dbfs": float(error_dbfs[spur_bin]),
        "input_referred_residue_range_v": _span(result.residue_v),
        "adc2_quantization_error_rms_v": _rms(result.residue_quantized_v - result.residue_v),
        "output_error_rms_v": _rms(error),
        "unquantized_reconstruction_error_max_v": float(np.max(np.abs(closure_error))),
        "independent_residue_oracle_error_max_v": float(np.max(np.abs(residue_error))),
    }
    arrays = {
        "coarse_code": result.coarse_code,
        "rdac_command_code": result.command_code,
        "prefix_command_code": result.prefix_command_code,
        "sadc_sample_v": result.sadc_sample_v,
        "rdac_slice_sample_v": result.slice_sample_v,
        "residue_v": result.residue_v,
        "adc2_code": result.adc2_code,
        "adc2_residue_v": result.residue_quantized_v,
        "code": result.output_code,
        "output_v": result.output_v,
        "error_spectrum_dbfs": error_dbfs,
        "accepted": result.accepted_direct,
        "accepted_with_prefix_guard": result.accepted_prefix_guard,
    }
    return metrics, arrays


def _check_upper_only_range(arrays: dict, words: np.ndarray) -> dict:
    lower_words = arrays["tone_dither_word_on"] & LOWER_WORD_MASK
    lower = convert(arrays["tone_input_v"], lower_words)
    if not np.all(lower.accepted_prefix_guard):
        raise RuntimeError("lower-only ablation must pass the same window and prefix guards")
    np.testing.assert_array_equal(lower.adc2_code, arrays["tone_on_adc2_code"])
    np.testing.assert_array_equal(lower.output_code, arrays["tone_on_code"])
    density = code_density(
        -STATIC_LIMIT_V, STATIC_LIMIT_V, words & LOWER_WORD_MASK, require_prefix_valid=True
    )
    np.testing.assert_array_equal(density.width_v, arrays["linearity_on_width_v"])
    return {
        "rdac_span_lsb9_lower_only": _span(
            decode_words(words & LOWER_WORD_MASK).total_v / COARSE_LSB_V
        ),
        "rdac_span_lsb9_full_word": _span(decode_words(words).total_v / COARSE_LSB_V),
        "common_tone_samples": int(lower.output_code.size),
        "different_adc2_codes": 0,
        "different_20bit_codes": 0,
        "different_static_code_widths": 0,
        "residue_difference_max_v": float(
            np.max(np.abs(lower.residue_v - arrays["tone_on_residue_v"]))
        ),
    }


def _full_scale_headroom(words: np.ndarray) -> dict:
    results = {}
    for key, require_prefix in (("direct_final", False), ("legacy_prefix_guard", True)):
        density = code_density(INPUT_MIN_V, INPUT_MAX_V, words, require_prefix_valid=require_prefix)
        rejected = density.pre_adc2_rejected_v + density.adc2_rejected_v + density.output_rejected_v
        np.testing.assert_allclose(
            density.width_v.sum() + rejected, FULL_SCALE_V, atol=1e-12, rtol=0
        )
        results[key] = {
            "rejected_pre_adc2_v": density.pre_adc2_rejected_v,
            "rejected_adc2_v": density.adc2_rejected_v,
            "rejected_output_v": density.output_rejected_v,
            "rejected_input_percent": 100 * rejected / FULL_SCALE_V,
        }
    return results


def _headroom() -> dict:
    top_word = convert(0.0, 63, ModelConfig(max_slice_setting_lsb=7))
    bottom = convert(INPUT_MIN_V + 0.25 * COARSE_LSB_V, 32)
    prefix = convert(INPUT_MIN_V + 8.5 * COARSE_LSB_V, 32)
    endpoint = convert(5.0, 0)
    near_top = convert(5.0 - 0.05 * OUTPUT_LSB_V, 0, ModelConfig(adc2_half_range_v=0.12))
    adc2_small = convert(0.75 * COARSE_LSB_V, 0, ModelConfig(adc2_half_range_v=0.005))
    if (
        top_word.accepted_direct
        or bottom.rdac_valid
        or not prefix.accepted_direct
        or prefix.accepted_prefix_guard
        or endpoint.input_valid
        or near_top.output_valid
        or adc2_small.adc2_valid
    ):
        raise RuntimeError("the headroom negative cases do not reach their intended guards")
    return {
        "word63_limit7": {
            "required_settings_lsb": decode_words(63).slice_setting_lsb.tolist(),
            "setting_valid": bool(top_word.setting_valid),
        },
        "word32_low_rail": {
            "input_v": INPUT_MIN_V + 0.25 * COARSE_LSB_V,
            "sadc_valid": bool(bottom.sadc_valid),
            "rdac_valid": bool(bottom.rdac_valid),
            "final_command": int(bottom.command_code),
        },
        "word32_prefix_vs_final": {
            "input_v": INPUT_MIN_V + 8.5 * COARSE_LSB_V,
            "final_command": int(prefix.command_code),
            "prefix_commands": prefix.prefix_command_code.tolist(),
            "accepted_direct": bool(prefix.accepted_direct),
            "accepted_prefix_guard": bool(prefix.accepted_prefix_guard),
        },
        "plus_5_v_endpoint": {"input_valid": bool(endpoint.input_valid)},
        "near_upper_output_code": {
            "input_v": 5.0 - 0.05 * OUTPUT_LSB_V,
            "adc2_half_range_v": 0.12,
            "reconstructed_v": float(near_top.reconstructed_v),
            "adc2_valid": bool(near_top.adc2_valid),
            "output_valid": bool(near_top.output_valid),
        },
        "adc2_5mv_half_window": {
            "residue_v": float(adc2_small.residue_v),
            "adc2_valid": bool(adc2_small.adc2_valid),
        },
    }


def _save(fig: plt.Figure, output: Path, name: str) -> None:
    fig.savefig(output / f"{name}.png", dpi=300)
    fig.savefig(output / f"{name}.svg", metadata={"Date": None})
    fig.savefig(output / f"{name}.pdf", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)


def _grid(ax: plt.Axes) -> None:
    ax.set_axisbelow(True)
    ax.grid(color="#D1D7DA", linestyle="--", alpha=0.9, linewidth=0.5)
    ax.tick_params(top=True, right=True, length=3)


def _plot_linearity(output: Path, summary: dict, arrays: dict) -> None:
    fig, axes = plt.subplots(
        4,
        2,
        figsize=LINEARITY_FIGURE_IN,
        gridspec_kw={"width_ratios": [2.65, 1.0]},
        sharex="col",
    )
    fig.subplots_adjust(left=0.12, right=0.979, bottom=0.148, top=0.848, hspace=0.57, wspace=0.18)
    fig.text(0.12, 0.965, "加 dither 后，理想七片模型的静态线性度改善", fontsize=10, weight=600)
    off, on = (summary["linearity"][key] for key in ("off", "on"))
    fig.text(
        0.12,
        0.931,
        f"DNL 均方根 {off['dnl_rms_lsb20']:.2f} → {on['dnl_rms_lsb20']:.2f} LSB20"
        f"    零宽度码 {off['zero_width_codes']:,} → {on['zero_width_codes']:,}",
    )
    fig.text(0.12, 0.902, "输入窗 ±3.671875 V  ·  20 位输出  ·  无失配、无热噪声")
    fig.text(0.12, 0.053, "左侧每 512 码显示极值包络，浅灰区域未纳入。右侧显示中央逐码结果。")
    fig.text(0.12, 0.026, "各行纵轴按仿真范围缩放。开启组对 64 个字等权积分。")
    code = arrays["linearity_signed_code"]
    for i, (key, title, color) in enumerate(ARMS):
        for j, metric in enumerate(("DNL", "INL")):
            row = 2 * i + j
            full, zoom = axes[row]
            series = arrays[f"linearity_{key}_{metric.lower()}_lsb20"]
            x = code if metric == "DNL" else np.r_[code, code[-1] + 1]
            indices = np.arange(0, len(series), 512)
            edges = np.r_[x[indices], x[-1] + 1]
            low = np.minimum.reduceat(series, indices)
            high = np.maximum.reduceat(series, indices)
            full.axvspan(-SIGNED_ZERO, edges[0], color="#E9EDF0")
            full.axvspan(edges[-1], SIGNED_ZERO, color="#E9EDF0")
            full.fill_between(
                edges,
                np.r_[low, low[-1]],
                np.r_[high, high[-1]],
                step="post",
                color=color,
                alpha=0.45,
            )
            full.stairs(low, edges, baseline=None, color=color, lw=0.4)
            full.stairs(high, edges, baseline=None, color=color, lw=0.4)
            mid = (x >= -16) & (x <= 16)
            zoom.step(x[mid], series[mid], where="post", lw=0.8, color=color)
            lo, hi = _span(series)
            padding = 0.11 * (hi - lo)
            full.set(
                xlim=(-SIGNED_ZERO, SIGNED_ZERO),
                xticks=[-SIGNED_ZERO, -262144, 0, 262144, SIGNED_ZERO - 1],
                ylabel=f"{metric}（LSB20）",
            )
            zoom.set(xlim=(-16, 16), xticks=[-16, 0, 16])
            full.set_title(f"{title}  ·  {lo:+.2f} 至 {hi:+.2f}", loc="left", color=color)
            for ax in (full, zoom):
                ax.set_ylim(lo - padding, hi + padding)
                ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
                _grid(ax)
                ax.axhline(0, color="#4F5B64", lw=0.55, zorder=1)
            zoom.tick_params(labelleft=False)
    axes[-1, 0].set_xlabel("有符号 20 位码（INL 取码边界）")
    axes[-1, 1].set_xlabel("中央局部码")
    _save(fig, output, "fig19_seven_linearity")


def _plot_spectrum_and_residue(output: Path, summary: dict, arrays: dict) -> None:
    fig, (spectrum, residue) = plt.subplots(
        2, 1, figsize=SPECTRUM_FIGURE_IN, gridspec_kw={"height_ratios": [1.65, 1]}
    )
    fig.subplots_adjust(left=0.117, right=0.967, bottom=0.18, top=0.774, hspace=0.63)
    fig.text(0.117, 0.951, "dither 压低最大杂散，SNDR 略降", fontsize=10, weight=600)
    off, on = (summary["tone"][key] for key in ("off", "on"))
    fig.text(
        0.117,
        0.911,
        f"SFDR {off['sfdr_dbc']:.2f} → {on['sfdr_dbc']:.2f} dBc"
        f"    SNDR {off['sndr_dbc']:.2f} → {on['sndr_dbc']:.2f} dBc",
    )
    fig.text(0.117, 0.868, "10 MS/s  ·  约 1 MHz、±3.33 V 差分正弦  ·  4096 点")
    fig.text(0.117, 0.074, "扣除输入正弦，显示 -179 dBFS 以上的非基波误差谱。指标按完整输出计算。")
    fig.text(0.117, 0.04, "两组逐级守卫均通过。残差量程与噪声结论限于此理想模型。")

    frequency_mhz = arrays["tone_frequency_hz"] / 1e6
    residue_edges = np.linspace(0, COARSE_LSB_V * 1e3, 33)
    for key, title, color in ARMS:
        spectrum.plot(
            frequency_mhz,
            arrays[f"tone_{key}_error_spectrum_dbfs"],
            color=color,
            label=title,
            lw=0.43,
            alpha=0.76,
        )
        metrics = summary["tone"][key]
        x_spur = metrics["largest_spur_hz"] / 1e6
        y_spur = metrics["largest_spur_dbfs"]
        spectrum.scatter(
            x_spur, y_spur, edgecolor=color, facecolor="white", s=19, linewidth=0.75, zorder=4
        )
        spectrum.annotate(
            f"{y_spur:.1f}",
            (x_spur, y_spur),
            xytext=(4, 3),
            textcoords="offset points",
            color=color,
            fontsize=7,
        )
        count, _ = np.histogram(arrays[f"tone_{key}_residue_v"] * 1e3, bins=residue_edges)
        residue.stairs(
            count / TONE_SAMPLES * 100,
            residue_edges,
            color=color,
            lw=0.9,
            label=title,
        )

    spectrum.legend(loc="lower left", ncols=2, framealpha=0.96, fontsize=7)
    spectrum.set(xlim=(0, 5), ylim=(-179, -119), xlabel="频率（MHz）", ylabel="误差谱（dBFS）")
    spectrum.set_title("A  输出误差谱与最大杂散", loc="left")
    _grid(spectrum)
    residue.set(
        xlim=(0, 20), xlabel="ADC2 输入端残差（折算至 ADC 输入，mV）", ylabel="样本占比（%）"
    )
    residue.set_title("B  两组 ADC2 残差均落在 0 至 19.53 mV", loc="left")
    _grid(residue)
    _save(fig, output, "fig19_seven_spectrum_residue")


def run(output: Path) -> dict:
    """写入逐码宽度、正弦观测、指标、复现信息和两张中文对照图。"""
    output.mkdir(parents=True, exist_ok=True)
    discard = int(np.ceil(DEFAULT_CONFIG.adc2_lsb_v / 2 / OUTPUT_LSB_V)) + 1
    start = round((-STATIC_LIMIT_V - INPUT_MIN_V) / OUTPUT_LSB_V) + discard
    stop = round((STATIC_LIMIT_V - INPUT_MIN_V) / OUTPUT_LSB_V) - discard
    code_span = np.arange(start, stop)
    words = np.arange(64)
    generator = DitherWordGenerator()
    tone_words = np.array(
        [generator.step(sample_accept=True, sample_id=n).word for n in range(TONE_SAMPLES)]
    )
    input_v = TONE_PEAK_V * np.sin(2 * np.pi * TONE_BIN * np.arange(TONE_SAMPLES) / TONE_SAMPLES)
    sources = [
        "task2/model/fig19_seven.py",
        "task2/model/dither_word_gen.py",
        "task2/experiments/fig19_seven_demo.py",
        "task1/experiments/full20_linearity_plots.py",
    ]
    summary = {
        "schema_version": 1,
        "scope": "Fig. 19 equal-capacitance voltage-equivalent engineering model",
        "patent": "US10505561B2 PDF pp. 22 (Fig. 19), 33 (columns 19–20)",
        "assumptions": {
            "differential_input_window_v": [-5.0, 5.0],
            "coarse_bits": COARSE_CODES.bit_length() - 1,
            "coarse_lsb_v": COARSE_LSB_V,
            "adc2_bits": ADC2_CODES.bit_length() - 1,
            "adc2_input_referred_window_v": [
                -DEFAULT_CONFIG.adc2_half_range_v,
                DEFAULT_CONFIG.adc2_half_range_v,
            ],
            "adc2_input_referred_lsb_v": DEFAULT_CONFIG.adc2_lsb_v,
            "output_bits": OUTPUT_CODES.bit_length() - 1,
            "lsb20_v": OUTPUT_LSB_V,
            "fractional_count_7_all_slices_advance": True,
            "ideal_max_slice_setting_lsb": DEFAULT_CONFIG.max_slice_setting_lsb,
            "capacitance_mismatch": False,
            "thermal_noise": False,
            "lms": False,
        },
        "linearity": {
            "uniform_input_window_v": [-STATIC_LIMIT_V, STATIC_LIMIT_V],
            "assessed_signed_codes_inclusive": [start - SIGNED_ZERO, stop - 1 - SIGNED_ZERO],
            "discarded_output_codes_per_window_edge": discard,
            "zero_width_tolerance_v": 1e-15,
            "on_equal_weight_words": 64,
            "on_unique_total_dither_levels": int(
                np.unique(decode_words(words).total_numerator).size
            ),
            "dnl": "width / LSB20 - 1",
            "inl": "cumulative DNL at boundaries with endpoint line removed",
        },
        "tone": {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "samples": TONE_SAMPLES,
            "tone_bin": TONE_BIN,
            "tone_frequency_hz": SAMPLE_RATE_HZ * TONE_BIN / TONE_SAMPLES,
            "amplitude_peak_v": TONE_PEAK_V,
            "prbs31_seed_hex": f"0x{DEFAULT_DITHER_SEED:08X}",
            "word_histogram": np.bincount(tone_words, minlength=64).tolist(),
            "spectrum_window": "rectangular",
        },
        "headroom_examples": _headroom(),
        "full_scale_uniform_headroom": {
            "off": _full_scale_headroom(np.zeros(1, dtype=int)),
            "on_64_equal_weight_words": _full_scale_headroom(words),
        },
        "python": python_version(),
        "python_packages": {
            package: version(package) for package in ("numpy", "matplotlib", "adctoolbox")
        },
        "source_sha256": {name: _sha(ROOT / name) for name in sources},
    }
    arrays = {
        "linearity_signed_code": code_span - SIGNED_ZERO,
        "tone_input_v": input_v,
        "tone_dither_word_on": tone_words,
        "tone_frequency_hz": np.fft.rfftfreq(TONE_SAMPLES, d=1 / SAMPLE_RATE_HZ),
    }
    for key, _, _ in ARMS:
        static_words = words if key == "on" else np.zeros(1, dtype=int)
        selected_tone_words = tone_words if key == "on" else np.zeros_like(tone_words)
        for name, (metrics, traces) in (
            ("linearity", _linearity(static_words, code_span)),
            ("tone", _tone(input_v, selected_tone_words)),
        ):
            summary[name][key] = metrics
            arrays.update({f"{name}_{key}_{field}": trace for field, trace in traces.items()})

    summary["upper_ablation"] = _check_upper_only_range(arrays, words)
    # 码索引、DNL 和 INL 可由摘要及逐码宽度无损重建，无需在归档中重复存放。
    archived = {
        key: array
        for key, array in arrays.items()
        if key.startswith("tone_") or key.endswith("_width_v")
    }
    np.savez_compressed(output / "fig19_seven_data.npz", **archived)
    summary["font"] = _chinese_style()
    # 轮廓字形避免 macOS 的 Hiragino TTC 在 PDF 阅读器中漏字。
    mpl.rcParams.update({"pdf.fonttype": 3, "svg.hashsalt": "task2-fig19-seven"})
    summary["pdf_fonttype"] = 3
    summary["figure_canvas_in"] = {
        "linearity": LINEARITY_FIGURE_IN,
        "spectrum_residue": SPECTRUM_FIGURE_IN,
    }
    _plot_linearity(output, summary, arrays)
    _plot_spectrum_and_residue(output, summary, arrays)
    artifact_names = [
        f"fig19_seven_{name}.{ext}"
        for name in ("linearity", "spectrum_residue")
        for ext in ("png", "svg", "pdf")
    ]
    artifact_names.append("fig19_seven_data.npz")
    summary["artifact_sha256"] = {name: _sha(output / name) for name in artifact_names}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    """运行实验并输出 JSON 摘要。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = run(args.output)
    print(json.dumps({key: summary[key] for key in ("linearity", "tone")}, indent=2))


if __name__ == "__main__":
    main()
