# =============================================================================
# 文件名     : full20_dither_plots.py
# 模块名     : full20_dither_plots
# 版本       : v1.0
# 功能       : Recompute and plot full 20-bit code widths for ideal ADC2 subtractive dither.
#
# 设计说明   :
#   * 承担 行为级实验与图件生成 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Recompute and plot full 20-bit code widths for ideal ADC2 subtractive dither."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import warnings
from dataclasses import replace
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import FuncFormatter

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.hashing import sha256_file
from task1.experiments.adc2_dither_study import VIEW_CODES, _dither_words
from task1.experiments.adctoolbox_calibration import CalibrationConfig
from task1.experiments.full20_linearity_plots import (
    BLOCK,
    CODE_COUNT,
    HEIGHT_MM,
    REFERENCE_SHA256,
    SIGNED_MAX,
    SIGNED_MIN,
    WIDTH_MM,
    _blocks,
    _save,
    _style,
    chinese_style,
)
from task1.experiments.inl_dnl import (
    CHUNK_COARSE,
    OUTPUT_BITS,
    RampStates,
    _build_intervals,
    _source_check,
    _states,
    code_mass,
    inl_linearity,
)
from task1.experiments.run_effects import ROOT
from task1.model.codebook import codebook

DATA = ROOT / "task1/evidence/adc2_dither"
TRAINING = ROOT / "task1/evidence/bridge_lms"
ARM = "fig21_bridge_lms"
PANELS = (
    ("off", "（a）DEM 开＋LMS 开，理想 dither 关", "#555555"),
    ("on", "（b）DEM 开＋LMS 开，理想 dither 开", "#0072B2"),
)


# =========================================================================
# 1. 函数组：_widths / _check / _plot
# =========================================================================
# ------------------------------------------------------------------------
def _widths(
    data: np.lib.npyio.NpzFile,
    experiment: ExperimentConfig,
    cfg: CalibrationConfig,
    words: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
    """调用合同。

    对粗码区间分批积分，避免构造全部粗码、状态和 ADC2 码的笛卡尔积。
    扰动加在 ADC2 输入折算残差上，数字基值减去相同偏移，形成加减性对照。
    这条独立实验路径不等同于八片采样负电荷源。

    Args:
        data: 冻结增益和等效开关权重的归档，列顺序由归档约定。
        experiment: 固定制造池、参考电压与片选参数。
        cfg: ADC2 量程和位数。
        words: 单位 V 的扰动偏移集合；比较时所有偏移等权。
    Returns:
        每个实验臂的全码宽数组、拒绝宽度统计和状态数量。

    码宽单位为 V，不是命中次数。状态权重为状态数与扰动字数乘积的倒数。
    状态数增加不会自动提高 ADC 的物理位数，也不代表更多独立制造样本。
    """
    nominal, pool = cap_pool(experiment)
    peak, half = experiment.full_scale_peak_v, cfg.adc2_half_range_v
    threshold = np.r_[codebook(nominal, experiment), peak]
    np.testing.assert_array_equal(threshold[:-1], data["nominal_threshold_v"])
    groups = _states(experiment, 4, bridge=True)
    states = RampStates(
        np.concatenate([group.plan for group in groups]),
        np.concatenate([group.horizontal for group in groups]),
        np.concatenate([group.vertical for group in groups]),
    )
    gain = data[f"{ARM}_frozen_gain"]
    weight = data[f"{ARM}_frozen_equivalent_switch_weight_v"]
    state_count = len(states.plan)
    widths = {key: np.zeros(CODE_COUNT) for key, _, _ in PANELS}
    limits = {key: np.zeros(4) for key in widths}
    for start in range(0, len(threshold) - 1, CHUNK_COARSE):
        coarse = np.arange(start, min(start + CHUNK_COARSE, len(threshold) - 1))
        interval = _build_intervals(
            pool, experiment, threshold, states, gain, weight, coarse, bridge=True
        )
        for key, offsets in (("off", np.zeros(1)), ("on", words)):
            probability = 1 / (state_count * len(offsets))
            for offset in offsets:
                shifted = replace(
                    interval,
                    residue_low_v=interval.residue_low_v + offset,
                    residue_high_v=interval.residue_high_v + offset,
                    digital_base_v=interval.digital_base_v - offset,
                )
                mass = code_mass(shifted, cfg.adc2_bits, half, peak)
                if mass.code.size:
                    first, stop = int(mass.code.min()), int(mass.code.max()) + 1
                    widths[key][first:stop] += probability * np.bincount(
                        mass.code - first, weights=mass.width_v, minlength=stop - first
                    )
                limits[key] += probability * np.array(
                    [mass.adc2_below_v, mass.adc2_above_v, mass.output_below_v, mass.output_above_v]
                )
    return widths, limits, state_count


# ------------------------------------------------------------------------
def _check(
    widths: dict[str, np.ndarray],
    limits: dict[str, np.ndarray],
    case: dict,
    peak: float,
    state_count: int,
    word_count: int,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict]:
    """调用合同。

    Args:
        widths: 各臂全码输入宽度数组，单位 V。
        limits: ADC2 和输出量程以外的宽度统计。
        case: 选择的基线记录。
        peak: 差分输入峰值，单位 V。
        state_count: 等权调度／DEM 状态数量。
        word_count: 等权扰动偏移数量。
    Returns:
        校验及逐码线性度摘要。

    积分总量必须与输入窗及拒绝宽度一致，不能通过丢弃坏区间使图更平滑。

    20 位 LSB 来自输入总宽除以 2^20，不来自绘图块大小。
    码宽为零时 DNL=-1，这是统计窗口内的零宽，不是删码后的新量程。
    INL 由逐码 DNL 累积并扣既定直线，不能先平滑再求峰值。
    端点截断与内部超窗分别记录。
    窗口外区域不能补零后纳入性能改善统计。
    LMS 的训练条件由输入归档决定，不能在绘图中重新拟合。
    扰动状态采用等权时要在图注明分母。
    非等权真实随机序列需另做有限记录验证。
    此处守恒检查不能代替完整 ADC 的动态非理想验证。
    """
    curves = {}
    metrics = {}
    for key, values in widths.items():
        reference = case["arms"][ARM]["density"][key]
        if reference["state_count"] != state_count or reference["dither_word_count"] != (
            word_count if key == "on" else 1
        ):
            raise ValueError("static state or dither word count differs from the frozen summary")
        np.testing.assert_allclose(
            values.sum() + limits[key][:2].sum(), 2 * peak, rtol=0, atol=5e-11
        )
        if limits[key][:2].sum() > 1e-15:
            raise ValueError("ADC2 overload prevents full-range DNL")
        np.testing.assert_allclose(
            limits[key],
            [
                reference["adc2_below_input_width_v"],
                reference["adc2_above_input_width_v"],
                reference["export_below_input_width_v"],
                reference["export_above_input_width_v"],
            ],
            rtol=0,
            atol=1e-11,
        )
        dnl, inl, stats = inl_linearity(values)
        np.testing.assert_allclose(
            [stats[name] for name in reference["20bit"]],
            list(reference["20bit"].values()),
            rtol=0,
            atol=1e-9,
        )
        curves[key] = (dnl, inl)
        metrics[key] = {
            **stats,
            "adc2_overload_input_width_v": float(limits[key][:2].sum()),
            "encoder_clip_input_width_v": float(limits[key][2:].sum()),
        }
    return curves, metrics


# ------------------------------------------------------------------------
def _plot(
    stem: Path,
    curves: dict[str, tuple[np.ndarray, np.ndarray]],
    stats: dict,
    sigma: float,
    seed: int,
    lsb_v: float,
    *,
    zoom: bool,
) -> None:
    """调用合同。

    Args:
        stem: 图片输出路径前缀，各格式共用同一个数据来源。
        curves: 按 20 位码顺序排列的 DNL／INL。
        stats: 对应曲线的极值、统计范围和边界记录。
        sigma: 制造失配标准差，不是每笔瞬时噪声。
        seed: 固定制造样本的种子。
        lsb_v: 名义 20 位电压步长，单位 V。
        zoom: 是否突出主体范围，图中仍需报告超界极值。

    缩放纵轴只改变显示范围，超界极值和评价窗口必须继续在图中披露。
    """
    fig, axes = plt.subplots(4, figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    fig.subplots_adjust(left=0.14, right=0.95, top=0.865, bottom=0.18, hspace=0.88)
    fig.text(
        0.5,
        0.976,
        "20 位逐码线性度｜理想减性 dither" + ("（中码放大）" if zoom else ""),
        ha="center",
        weight=600,
    )
    fig.text(
        0.5,
        0.95,
        f"固定失配 {sigma:g}%　制造种子 {seed}　Fig.21 跨段 DEM＋冻结 NLMS",
        ha="center",
    )
    fig.text(
        0.5,
        0.924,
        f"理想 12 位 ADC2　输入等效 ±100 mV　标称 LSB20＝{lsb_v * 1e6:.4f} 微伏",
        ha="center",
    )
    footer = (
        "中码放大：-64 至 +64 逐码显示。"
        if zoom
        else f"全幅覆盖 1,048,574 个内码，每 {BLOCK} 码保留最小值和最大值。"
    )
    fig.text(
        0.14,
        0.022,
        f"{footer}\n"
        "码密度混合 16 个等权 DEM 状态，开时另用 16 个等权扰动字。\n"
        "理想 ADC2 输入加减模型，Fig.19 电容路径待验证。单次误差另测。\n"
        "排除首尾饱和码，DNL 按内码均宽归一。INL 用端点法。",
        ha="left",
        va="bottom",
    )
    ticks = (
        np.arange(-64, 65, 16)
        if zoom
        else np.r_[np.arange(SIGNED_MIN, SIGNED_MAX, 1 << 17), SIGNED_MAX]
    )
    for mode_index, (key, title, color) in enumerate(PANELS):
        for metric_index, (metric, values) in enumerate(
            zip(("微分非线性", "积分非线性"), curves[key], strict=True)
        ):
            ax = axes[2 * mode_index + metric_index]
            ax.set(
                xlim=(-64, 64) if zoom else (SIGNED_MIN, SIGNED_MAX),
                xticks=ticks,
                ylabel=f"{metric}（LSB20）",
                xlabel="有符号 20 位输出码" if metric_index == 0 else "有符号 20 位码跃迁",
            )
            ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}"))
            ax.yaxis.set_label_coords(-0.09, 0.5)
            ax.tick_params(top=True, right=True, length=2.5)
            ax.grid(color="0.72", linestyle="--", alpha=0.65)
            ax.set_axisbelow(True)
            ax.axhline(0, color="0.45", lw=0.5)
            if zoom:
                code = np.arange(-64, 65)
                local = values[code - (SIGNED_MIN + 1)]
                ax.step(code, local, where="post", color=color)
                low, high = local.min(), local.max()
            else:
                edges, minima, maxima, _ = _blocks(values)
                low, high = minima.min(), maxima.max()
                ax.fill_between(
                    edges,
                    np.r_[minima, minima[-1]],
                    np.r_[maxima, maxima[-1]],
                    step="post",
                    color=color,
                    alpha=0.53,
                    linewidth=0,
                )
                ax.step(edges, np.r_[minima, minima[-1]], where="post", color=color, lw=0.37)
                ax.step(edges, np.r_[maxima, maxima[-1]], where="post", color=color, lw=0.37)
            pad = 0.055 * (high - low)
            ax.set_ylim(low - pad, high + pad)
            if metric_index == 0:
                if low <= -1:
                    ax.axhline(-1, color="0.28", lw=0.6, linestyle=":")
                    yticks = np.union1d(ax.get_yticks(), [-1])
                    ax.set_yticks(yticks[(yticks >= low - pad) & (yticks <= high + pad)])
                    ax.set_ylim(low - pad, high + pad)
                ax.set_title(title, loc="left", color=color, weight=600, pad=13)
                ax.text(
                    0,
                    1.03,
                    f"全幅 DNL [{stats[key]['dnl_min_lsb']:+.3f}, "
                    f"{stats[key]['dnl_max_lsb']:+.3f}] LSB20　"
                    f"零宽内码 {stats[key]['zero_width_interior_codes']:,}",
                    transform=ax.transAxes,
                )
            else:
                ax.set_title(
                    f"全幅峰值 |INL|＝{stats[key]['endpoint_inl_peak_lsb']:.3f} LSB20",
                    loc="left",
                    pad=3,
                )
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "error", message=r"Glyph .* missing from font", category=UserWarning
        )
        _save(fig, stem, zoom=zoom)
    plt.close(fig)


# =========================================================================
# 2. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# Recompute and compare against the archived dither study before writing figures.
# ------------------------------------------------------------------------
def main() -> None:
    """Recompute and compare against the archived dither study before writing figures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DATA / "full20")
    args = parser.parse_args()
    archived_report = DATA / "summary.json"
    report = json.loads(archived_report.read_text(encoding="utf-8"))
    for name, expected_sha in report["source_sha256"].items():
        if sha256_file(ROOT / name) != expected_sha:
            raise ValueError(f"source differs from frozen dither study: {name}")
    plt.switch_backend("Agg")
    font = chinese_style()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = []
    for case in report["cases"]:
        sigma = case["sigma_percent"]
        training = TRAINING / f"sigma_{sigma:g}pct_adc2_100mv"
        for name, expected_sha in case["training_artifact_sha256"].items():
            if sha256_file(training / name) != expected_sha:
                raise ValueError(f"training artifact differs from frozen dither study: {name}")
        meta = json.loads((training / "summary.json").read_text(encoding="utf-8"))
        _source_check(training, meta)
        experiment = ExperimentConfig(**meta["experiment"])
        cfg = CalibrationConfig(**meta["calibration"])
        if (
            cfg.adc2_bits != 12
            or cfg.adc2_half_range_v != 0.1
            or case["manufacturing_seed"] != experiment.manufacturing_seed
        ):
            raise ValueError("expected matching frozen 12-bit ±100 mV calibration")
        words = _dither_words(2 * cfg.adc2_half_range_v / (1 << cfg.adc2_bits))
        np.testing.assert_array_equal(words, case["dither_words_v"])
        with np.load(training / "observations.npz", allow_pickle=False) as data:
            widths, limits, state_count = _widths(data, experiment, cfg, words)
        curves, stats = _check(
            widths, limits, case, experiment.full_scale_peak_v, state_count, len(words)
        )
        archived_widths = DATA / f"traces_sigma_{sigma:g}pct.npz"
        with np.load(archived_widths, allow_pickle=False) as archive:
            for key, values in widths.items():
                prefix = f"{ARM}_density_{key}"
                np.testing.assert_array_equal(
                    np.packbits(values > 1e-15), archive[f"{prefix}_occupied20_bitpack"]
                )
                np.testing.assert_allclose(
                    values[VIEW_CODES], archive[f"{prefix}_central20_width_v"], rtol=0, atol=1e-15
                )
        stem = f"adc2_12bit_sigma_{sigma:g}pct_dither"
        lsb_v = 2 * experiment.full_scale_peak_v / CODE_COUNT
        np.savez_compressed(
            args.out / f"{stem}_code_widths.npz",
            **{f"{key}_uniform_input_width_v_20b": values for key, values in widths.items()},
        )
        _plot(
            args.out / stem, curves, stats, sigma, experiment.manufacturing_seed, lsb_v, zoom=False
        )
        _plot(
            args.out / f"{stem}_midscale",
            curves,
            stats,
            sigma,
            experiment.manufacturing_seed,
            lsb_v,
            zoom=True,
        )
        cases.append(
            {
                "sigma_percent": sigma,
                "manufacturing_seed": experiment.manufacturing_seed,
                "arm": ARM,
                "adc2_bits": cfg.adc2_bits,
                "adc2_half_range_v_input_referred": cfg.adc2_half_range_v,
                "nominal_lsb20_v": lsb_v,
                "state_count": state_count,
                "dither_on_word_count": len(words),
                "dither_words_v": words.tolist(),
                "frozen_training_sha256": case["training_artifact_sha256"],
                "archived_occupancy_artifact_sha256": sha256_file(archived_widths),
                "arms": stats,
            }
        )
    files = (
        "adc2_dither_study.py",
        "full20_dither_plots.py",
        "inl_dnl.py",
    )
    manifest = {
        "schema": "task1_full20_ideal_adc2_dither_v1",
        "scope": "Ideal fine ADC2 subtractive dither with frozen Fig.21 + NLMS",
        "archived_dither_summary_sha256": sha256_file(archived_report),
        "source_sha256": {
            f"task1/experiments/{name}": sha256_file(Path(__file__).with_name(name))
            for name in files
        },
        "figure_helper_source": "task1/experiments/full20_linearity_plots.py",
        "figure_helper_functions_sha256": {
            fn.__name__: hashlib.sha256(inspect.getsource(fn).encode("utf-8")).hexdigest()
            for fn in (_blocks, chinese_style, _save, _style)
        },
        "visual_reference_sha256": REFERENCE_SHA256,
        "signed_code_range": [SIGNED_MIN, SIGNED_MAX],
        "interior_dnl_signed_code_range": [SIGNED_MIN + 1, SIGNED_MAX - 1],
        "interior_inl_signed_transition_range": [SIGNED_MIN + 1, SIGNED_MAX],
        "output_bits": OUTPUT_BITS,
        "display_block_codes": BLOCK,
        "figure_dimensions_mm": [WIDTH_MM, HEIGHT_MM],
        "figure_language": "zh-CN",
        "font_family": font,
        "cases": cases,
        "output_sha256": {path.name: sha256_file(path) for path in sorted(args.out.glob("adc2_*"))},
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
