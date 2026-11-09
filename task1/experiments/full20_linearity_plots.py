# =============================================================================
# 文件名     : full20_linearity_plots.py
# 模块名     : full20_linearity_plots
# 版本       : v1.0
# 功能       : Render signed 20-bit DNL and endpoint INL from the archived per-code mass.
#
# 设计说明   :
#   * 承担 行为级实验与图件生成 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Render signed 20-bit DNL and endpoint INL from the archived per-code mass."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

from adi_model.hashing import sha256_file
from task1.experiments.inl_dnl import OUTPUT_BITS, inl_linearity

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "task1/evidence/inl_dnl"
REFERENCE_SHA256 = "0a9aeecd7c274a00ab1a05d43ee50337bf63268b90a402d9d8b1e37b54ca705e"
CODE_COUNT = 1 << OUTPUT_BITS
SIGNED_MIN, SIGNED_MAX = -(CODE_COUNT // 2), CODE_COUNT // 2 - 1
BLOCK = 512
WIDTH_MM, HEIGHT_MM = 146, 185
DETAIL_RANGES = {"midscale": (-64, 64), "low_edge": (SIGNED_MIN, SIGNED_MIN + 1024)}
BODY_ZOOM_RANGES = {"DNL": (-0.60, 0.60), "INL": (-0.15, 1.40)}
ARMS = (
    ("fixed_ab_lms_only", "(a) 固定 A/B · 无 DEM＋独立 LMS", "#555555"),
    ("fig21_bridge_lms", "(b) 动态 Fig. 21 · DEM 开启＋独立 LMS", "#0072B2"),
)
UNCALIBRATED_ARM = ("fixed_ab_no_dem_no_lms", "(a) 固定 A/B · 无 DEM、无 LMS", "#333333")
THREE_ARMS = (
    UNCALIBRATED_ARM,
    (ARMS[0][0], ARMS[0][1].replace("(a)", "(b)"), ARMS[0][2]),
    (ARMS[1][0], ARMS[1][1].replace("(b)", "(c)"), ARMS[1][2]),
)


# =========================================================================
# 1. 函数组：_style / chinese_style
# =========================================================================
# -------------------------------------------------------------------------
# Set the reference geometry with a verified Simplified Chinese font.
# ------------------------------------------------------------------------
def _style() -> str:
    # Match the reference panel roles at their delivered physical dimensions.
    font = "Arial"
    try:
        font_manager.findfont(font_manager.FontProperties(family=[font]), fallback_to_default=False)
    except ValueError:
        font = "DejaVu Sans"
    mpl.rcParams.update(
        {
            "font.family": font,
            "font.size": 8,
            "axes.labelsize": 8,
            "axes.titlesize": 8,
            "axes.linewidth": 0.7,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.7,
            "ytick.major.width": 0.7,
            "grid.linewidth": 0.45,
            "lines.linewidth": 1,
            "path.simplify": False,
            "pdf.fonttype": 42,
            "svg.fonttype": "path",
            "svg.hashsalt": "task1-full20",
            "savefig.facecolor": "white",
        }
    )
    return font


# ------------------------------------------------------------------------
def chinese_style() -> str:
    """Set the reference geometry with a verified Simplified Chinese font."""
    _style()
    for family, regular_weight in (("Hiragino Sans GB", 300), ("Noto Sans CJK SC", 400)):
        try:
            path = font_manager.findfont(
                font_manager.FontProperties(family=[family], weight=regular_weight),
                fallback_to_default=False,
            )
        except ValueError:
            continue
        if ord("码") not in font_manager.get_font(path).get_charmap():
            continue
        mpl.rcParams.update(
            {
                "font.family": family,
                "font.weight": regular_weight,
                "axes.labelweight": regular_weight,
                "axes.titleweight": regular_weight,
                "axes.unicode_minus": False,
            }
        )
        return family
    raise RuntimeError("Install Hiragino Sans GB or Noto Sans CJK SC for Chinese plot labels")


# =========================================================================
# 2. 函数组：_blocks / _axes / _save
# =========================================================================
# -------------------------------------------------------------------------
# Keep both extrema of every contiguous block and their signed code locations.
# ------------------------------------------------------------------------
def _blocks(
    values: np.ndarray, *, missing: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[tuple]]:
    """Keep both extrema of every contiguous block and their signed code locations.

    Args:
        values: 按输出码排列的逐码数值，不能先排序或重采样。
        missing: 可选逐码零宽标记；与 values 按相同码序对齐。
    Returns:
        每个显示块的范围和极值，供全景图绘制。

    显示块只压缩图形，不参与 DNL／INL 的重新计算。
    零宽码与峰值必须在压缩前的完整数组上统计。
    """
    first_code = SIGNED_MIN + 1
    start = np.arange(0, len(values), BLOCK)
    stop = np.r_[start[1:], len(values)]
    low = np.minimum.reduceat(values, start)
    high = np.maximum.reduceat(values, start)
    edges = np.r_[start, len(values)] + first_code
    rows = []
    for lo, hi, minimum, maximum in zip(start, stop, low, high, strict=True):
        part = values[lo:hi]
        rows.append(
            (
                first_code + lo,
                first_code + hi - 1,
                hi - lo,
                minimum,
                maximum,
                first_code + lo + np.argmin(part),
                first_code + lo + np.argmax(part),
                "" if missing is None else np.count_nonzero(missing[lo:hi]),
            )
        )
    assert int((stop - start).sum()) == len(values)
    assert low.min() == values.min() and high.max() == values.max()
    if missing is not None:
        assert sum(row[-1] for row in rows) == np.count_nonzero(missing)
    return edges, low, high, rows


# ------------------------------------------------------------------------
def _axes(fig: plt.Figure, axes: np.ndarray, meta: dict, view: str) -> None:
    """调用合同。

    Args:
        fig: 当前图形对象。
        axes: 面板轴对象，按调用方规定的顺序组织。
        meta: 实验条件、位宽和单位说明。
        view: 选择全景或局部显示方式，不改变数据口径。

    图例中的处理方式对应冻结实验臂，不能把 DEM 状态与 dither 状态混写。
    横轴为整数输出码，纵轴为 LSB20；显示包络不是降低 ADC 输出位数。
    """
    scale = HEIGHT_MM / (fig.get_figheight() * 25.4)
    fig.subplots_adjust(
        left=0.135, right=0.954, top=1 - 0.13 * scale, bottom=0.136 * scale, hspace=0.82
    )
    suffix = {
        "midscale": " · 中码逐码放大",
        "low_edge": " · 负满幅端逐码放大",
        "body_zoom": " · DEM 纵轴放大",
    }
    title = "20 位输出 INL／DNL" + suffix.get(view, "")
    fig.text(0.5, 1 - 0.021 * scale, title, ha="center", va="top", weight=600)
    fig.text(
        0.5,
        1 - 0.049 * scale,
        f"{100 * meta['sigma']:g}% 固定失配  ｜  种子 {meta['manufacturing_seed']}  ｜  "
        f"{meta['adc2_bits']} 位理想 ADC2（输入等效 ±{meta['adc2_half_range_v'] * 1e3:g} mV）",
        ha="center",
        va="top",
    )
    fig.text(
        0.5,
        1 - 0.075 * scale,
        f"标称 LSB20 = {meta['output_ideal_lsb_v'] * 1e6:.4f} 微伏  ｜  dither 关闭  ｜  ADC2 未超窗",
        ha="center",
        va="top",
    )
    state_note = "等权码密度，非单次转换。固定 A/B 取 2 状态，动态取 4 组种子 × 128 快照。"
    note = (
        f"分析 1,048,574 个内码，色带保留每 {BLOCK} 码的极值。\n"
        "剔除首尾饱和码，按内码平均码宽做端点归一。\n" + state_note
    )
    if view in DETAIL_RANGES:
        note = "逐码画出，按内码平均码宽做端点归一。\n" + state_note
    if view == "body_zoom":
        note = (
            "仅放大 DEM 面板的纵轴，超出显示范围的逐码点数已标注。\n"
            "极值和缺码见全范围图与负满幅端放大图。\n" + state_note
        )
    fig.text(0.135, 0.017 * scale, note, ha="left", va="bottom")
    xlim = DETAIL_RANGES.get(view, (SIGNED_MIN, SIGNED_MAX))
    ticks = np.r_[np.arange(SIGNED_MIN, SIGNED_MAX, 1 << 17), SIGNED_MAX]
    if view in DETAIL_RANGES:
        ticks = np.linspace(*xlim, 9, dtype=int)
    for ax, (metric, abscissa) in zip(
        axes, (("DNL", "码"), ("INL", "转换边界")) * (len(axes) // 2), strict=True
    ):
        ax.set(xlim=xlim, xticks=ticks, ylabel=f"{metric}（LSB20）")
        ax.set_xlabel(f"有符号 20 位{abscissa}", labelpad=2)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}"))
        ax.grid(color="0.72", linestyle="--", alpha=0.65)
        ax.set_axisbelow(True)
        ax.tick_params(top=True, right=True, length=2.5)
        ax.axhline(0, color="0.45", lw=0.5, zorder=1)


# ------------------------------------------------------------------------
def _save(fig: plt.Figure, stem: Path, *, zoom: bool) -> None:
    """调用合同。

    Args:
        fig: 已完成数据及注释的图形。
        stem: 文件名前缀，由调用方组织目录。
        zoom: 是否生成带放大后缀的显示视图。

    多个格式共用同一个 figure，避免 PNG 与矢量图分别计算而出现数值差异。
    输出是可视化交付，不等于新增模拟、RTL 或工艺签核证据。
    """
    fig.savefig(stem.with_suffix(".png"), dpi=300)
    fig.savefig(stem.with_suffix(".svg"), metadata={"Date": None})
    if not zoom:
        fig.savefig(stem.with_suffix(".pdf"), metadata={"CreationDate": None, "ModDate": None})


# =========================================================================
# 3. 函数组：_draw_envelope / _case / main
# =========================================================================
# -------------------------------------------------------------------------
# Export two fixed mismatch levels for each archived ADC2 resolution.
# ------------------------------------------------------------------------
def _draw_envelope(
    ax: plt.Axes, edges: np.ndarray, low: np.ndarray, high: np.ndarray, color: str
) -> None:
    low_steps, high_steps = np.r_[low, low[-1]], np.r_[high, high[-1]]
    ax.fill_between(
        edges, low_steps, high_steps, step="post", color=color, alpha=0.53, linewidth=0, zorder=2
    )
    ax.step(edges, low_steps, where="post", color=color, lw=0.37, zorder=3)
    ax.step(edges, high_steps, where="post", color=color, lw=0.37, zorder=3)


# ------------------------------------------------------------------------
def _case(
    path: Path, out: Path, meta: dict, *, uncalibrated: tuple[Path, dict] | None = None
) -> dict:
    """调用合同。

    Args:
        path: 一组已有实验数据所在目录。
        out: 图片输出目录；不在这里训练系数或运行新的失配仿真。
        meta: 配套统计与单位，须与当前码宽数组同源。
        uncalibrated: 可选未校准归档路径和元数据，用于第三个对照臂。

    全景、主体放大和端点放大使用同一份数据。缩小显示纵轴时仍报告超界点数。
    DNL 的单位是名义 20 位 LSB，不能随画图分块数更改。
    INL 使用既定的端点扣直线口径，不能在重绘阶段改成最佳拟合来改善指标。
    灰色区域表示未评价或超出共同输入窗，不可误读为理想零误差。

    评价范围内每个输出码仍保留独立宽度。
    极值包络不丢弃原始极值，但会压缩码间的视觉细节。
    因此中码和端点局部图用于辨认离散结构。
    同一幅图的不同实验臂不应使用未说明的独立标度。
    放大后的坐标范围不改变报告的峰值与零宽数量。
    重绘时不允许只保留更平滑的状态子集。
    输入数据缺失或元数据不配套应终止，而不是补出近似曲线。
    旧图对应旧代码时，只能作为历史结果引用。
    图像渲染成功不能提升原数据的验证等级。
    图片和摘要的结果单位必须一致。
    """
    stem = f"adc2_{meta['adc2_bits']}bit_sigma_{100 * meta['sigma']:g}pct"
    if meta["output_quantizer_bits"] != OUTPUT_BITS:
        raise ValueError(f"{path} is not {OUTPUT_BITS}-bit output data")
    case = {
        "source": str(path.relative_to(ROOT)),
        "source_sha256": sha256_file(path),
        "adc2_bits": meta["adc2_bits"],
        "adc2_half_range_v_input_referred": meta["adc2_half_range_v"],
        "fixed_mismatch_sigma": meta["sigma"],
        "manufacturing_seed": meta["manufacturing_seed"],
        "nominal_lsb20_v": meta["output_ideal_lsb_v"],
        "comparison": "independent NLMS paths",
        "arms": {},
    }
    arms = ARMS
    paths = {name: path for name, *_ in arms}
    arm_data = dict(meta["arms"])
    height_mm = HEIGHT_MM
    if uncalibrated is not None:
        uncalibrated_path, uncalibrated_meta = uncalibrated
        for field in (
            "sigma",
            "adc2_bits",
            "adc2_half_range_v",
            "manufacturing_seed",
            "output_quantizer_bits",
            "input_full_scale_v",
            "sample_rate_hz",
        ):
            if meta[field] != uncalibrated_meta[field]:
                raise ValueError(f"{stem}: independent baseline differs in {field}")
        with (
            np.load(path, allow_pickle=False) as calibrated,
            np.load(uncalibrated_path, allow_pickle=False) as original,
        ):
            np.testing.assert_array_equal(
                original["nominal_coarse_threshold_v"], calibrated["nominal_coarse_threshold_v"]
            )
            np.testing.assert_array_equal(
                original[f"{UNCALIBRATED_ARM[0]}_state_plan"],
                calibrated[f"{ARMS[0][0]}_state_plan"],
            )
        paths[UNCALIBRATED_ARM[0]] = uncalibrated_path
        arm_data.update(uncalibrated_meta["arms"])
        arms = THREE_ARMS
        height_mm = 256
        stem += "_three_arms"
        case.update(
            comparison="independent uncalibrated baseline and both NLMS paths",
            uncalibrated_source=str(uncalibrated_path.relative_to(ROOT)),
            uncalibrated_source_sha256=sha256_file(uncalibrated_path),
        )
    case["dimensions_mm"] = [WIDTH_MM, height_mm]
    artifacts = [f"{stem}_envelope.csv"]
    axes_count = 2 * len(arms)
    figsize = (WIDTH_MM / 25.4, height_mm / 25.4)
    full, axes = plt.subplots(axes_count, figsize=figsize)
    details = {}
    if meta["adc2_bits"] == 12 and uncalibrated is None:
        for view in DETAIL_RANGES:
            details[view] = plt.subplots(4, figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    figures = {"full": (full, axes), **details}
    if meta["adc2_bits"] == 12:
        figures["body_zoom"] = plt.subplots(axes_count, figsize=figsize)
    bounds = {view: [[], []] for view in figures}
    for view, (fig, panels) in figures.items():
        _axes(fig, panels, meta, view)
    with (out / f"{stem}_envelope.csv").open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file, lineterminator="\n")
        writer.writerow(
            (
                "arm",
                "metric",
                "first_signed_code",
                "last_signed_code",
                "sample_count",
                "min_lsb20",
                "max_lsb20",
                "signed_code_at_min",
                "signed_code_at_max",
                "zero_width_codes",
            )
        )
        for arm_index, (name, heading, color) in enumerate(arms):
            arm = arm_data[name]
            if arm["status"] != "completed" or arm["adc2_out_of_range_input_width_v"] != 0.0:
                raise ValueError(f"{stem}: cannot claim complete linearity for {name}")
            with np.load(paths[name], allow_pickle=False) as archive:
                widths = archive[f"{name}_uniform_input_width_v_20b"]
            if widths.shape != (CODE_COUNT,) or not np.all(np.isfinite(widths) & (widths >= 0)):
                raise ValueError(f"{stem}: invalid archived per-code mass for {name}")
            np.testing.assert_allclose(widths.sum(), meta["input_full_scale_v"], rtol=0, atol=1e-10)
            dnl, inl, stats = inl_linearity(widths)
            expected = arm["linearity"][str(OUTPUT_BITS)]
            np.testing.assert_allclose(
                list(stats.values()), [expected[key] for key in stats], rtol=0, atol=1e-10
            )
            missing = widths[1:-1] <= 1e-15
            missing_codes = np.flatnonzero(missing) + SIGNED_MIN + 1
            case["arms"][name] = {
                "effective_states": arm["effective_states"],
                "adc2_out_of_range_input_width_v": arm["adc2_out_of_range_input_width_v"],
                "encoder_clip_input_width_v": arm["encoder_clip_input_width_v"],
                "dnl_signed_code_range": [SIGNED_MIN + 1, SIGNED_MAX - 1],
                "inl_signed_transition_range": [SIGNED_MIN + 1, SIGNED_MAX],
                "dnl_signed_code_at_max": SIGNED_MIN + 1 + int(dnl.argmax()),
                "inl_signed_transition_at_abs_peak": SIGNED_MIN + 1 + int(np.abs(inl).argmax()),
                "zero_width_signed_code_range": (
                    [int(missing_codes[0]), int(missing_codes[-1])] if missing_codes.size else None
                ),
                **stats,
            }
            for index, (metric, values) in enumerate((("DNL", dnl), ("INL", inl))):
                edges, low, high, rows = _blocks(values, missing=missing if index == 0 else None)
                writer.writerows((name, metric, *row) for row in rows)
                panel = 2 * arm_index + index
                ax = axes[panel]
                _draw_envelope(ax, edges, low, high, color)
                bounds["full"][index].extend((low.min(), high.max()))
                if "body_zoom" in figures:
                    zoom_ax = figures["body_zoom"][1][panel]
                    _draw_envelope(zoom_ax, edges, low, high, color)
                    bounds["body_zoom"][index].extend((low.min(), high.max()))
                    if index == 0:
                        zoom_ax.set_title(heading, loc="left", weight=600, pad=13)
                    caption = f"峰值 |INL| = {np.abs(inl).max():.3f} LSB20"
                    if index == 1 and name == UNCALIBRATED_ARM[0]:
                        caption += (
                            f"  ｜  输出端裁剪宽度 {arm['encoder_clip_input_width_v'] * 1e3:.3f} mV"
                        )
                    if index == 0:
                        caption = f"DNL [{dnl.min():+.3f}, {dnl.max():+.3f}]   |   "
                        caption += f"零宽码 {np.count_nonzero(missing):,}"
                    if name == ARMS[1][0]:
                        zoom_lo, zoom_hi = BODY_ZOOM_RANGES[metric]
                        off_axis = int(np.count_nonzero((values < zoom_lo) | (values > zoom_hi)))
                        caption = (
                            f"显示 [{zoom_lo:+.2f}, {zoom_hi:+.2f}]  ｜  "
                            f"全范围 [{values.min():+.3f}, {values.max():+.3f}]  ｜  超出 {off_axis:,} 点"
                        )
                        if index == 0:
                            caption += f"  ｜  零宽码 {np.count_nonzero(missing):,}"
                        case["arms"][name][f"{metric.lower()}_body_zoom_off_axis"] = off_axis
                    zoom_ax.text(
                        0, 1.025, caption, transform=zoom_ax.transAxes, ha="left", va="bottom"
                    )
                for view, (_, detail_axes) in details.items():
                    detail_ax = detail_axes[panel]
                    start, end = DETAIL_RANGES[view]
                    x = np.arange(
                        max(start, SIGNED_MIN + 1), min(end, SIGNED_MIN + len(values)) + 1
                    )
                    y = values[x - (SIGNED_MIN + 1)]
                    detail_ax.step(x, y, where="post", color=color, zorder=2)
                    bounds[view][index].extend((y.min(), y.max()))
                if index == 0:
                    ax.set_title(heading, loc="left", weight=600, pad=13)
                    ax.text(
                        0,
                        1.025,
                        f"DNL [{dnl.min():+.3f}, {dnl.max():+.3f}]   |   "
                        f"零宽内码 {np.count_nonzero(missing):,}",
                        transform=ax.transAxes,
                        ha="left",
                        va="bottom",
                    )
                    for _, detail_axes in details.values():
                        detail_axes[panel].set_title(heading, loc="left", weight=600, pad=3)
                else:
                    caption = f"峰值 |INL| = {np.abs(inl).max():.3f} LSB20"
                    if name == UNCALIBRATED_ARM[0]:
                        caption += (
                            f"  ｜  输出端裁剪宽度 {arm['encoder_clip_input_width_v'] * 1e3:.3f} mV"
                        )
                    ax.set_title(caption, loc="left", pad=3)
    for view, (fig, panels) in figures.items():
        for index, (metric, values) in enumerate(zip(("DNL", "INL"), bounds[view], strict=True)):
            for arm_index, ax in enumerate(panels[index::2]):
                if view == "body_zoom" and arm_index == len(arms) - 1:
                    ax.set_ylim(*BODY_ZOOM_RANGES[metric])
                    continue
                if uncalibrated is not None:
                    selected = values[:2] if arm_index == 0 else values[2:]
                else:
                    selected = values
                lo, hi = min(selected), max(selected)
                pad = 0.055 * (hi - lo)
                ax.set_ylim(lo - pad, hi + pad)
                if index == 0 and lo <= -1.0:
                    ticks = ax.get_yticks()
                    ticks = ticks[(ticks >= lo - pad) & (ticks <= hi + pad)]
                    ticks = ticks[np.abs(ticks + 1.0) > 0.13 * (hi - lo)]
                    ax.set_yticks(np.union1d(ticks, [-1.0]))
                    ax.set_ylim(lo - pad, hi + pad)
                    ax.axhline(-1.0, color="0.28", lw=0.6, linestyle=":", zorder=4)
        filename = stem if view == "full" else f"{stem}_{view}"
        _save(fig, out / filename, zoom=view != "full")
        if view != "full":
            fig.savefig(out / f"{filename}.pdf", metadata={"CreationDate": None, "ModDate": None})
        artifacts.extend(f"{filename}.{ext}" for ext in ("png", "svg", "pdf"))
        plt.close(fig)
    case["artifacts"] = artifacts
    return case


# ------------------------------------------------------------------------
def main() -> None:
    """Export two fixed mismatch levels for each archived ADC2 resolution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DATA / "full20")
    parser.add_argument(
        "--reference", type=Path, help="Optionally verify the frozen style reference"
    )
    parser.add_argument(
        "--include-uncalibrated",
        action="store_true",
        help="Also render the independent uncalibrated comparison",
    )
    args = parser.parse_args()
    if args.reference is not None and sha256_file(args.reference) != REFERENCE_SHA256:
        raise ValueError("unexpected visual reference SHA-256")
    args.out.mkdir(parents=True, exist_ok=True)
    plt.switch_backend("Agg")
    font = chinese_style()
    cases = []
    source_reports = {}
    for source_dir in (DATA, DATA / "adc2_16bit"):
        source_report = source_dir / "summary.json"
        source_reports[str(source_report.relative_to(ROOT))] = sha256_file(source_report)
        experiments = json.loads(source_report.read_text(encoding="utf-8"))["experiments"]
        for meta in experiments:
            source = source_dir / f"linearity_sigma_{100 * meta['sigma']:g}pct.npz"
            cases.append(_case(source, args.out, meta))
        if args.include_uncalibrated:
            uncalibrated_dir = DATA / "uncalibrated" / source_dir.relative_to(DATA)
            uncalibrated_report = uncalibrated_dir / "summary.json"
            source_reports[str(uncalibrated_report.relative_to(ROOT))] = sha256_file(
                uncalibrated_report
            )
            independent = json.loads(uncalibrated_report.read_text(encoding="utf-8"))["experiments"]
            for meta in experiments:
                uncalibrated_meta = next(m for m in independent if m["sigma"] == meta["sigma"])
                source = source_dir / f"linearity_sigma_{100 * meta['sigma']:g}pct.npz"
                uncalibrated = uncalibrated_dir / source.name, uncalibrated_meta
                cases.append(_case(source, args.out, meta, uncalibrated=uncalibrated))
    manifest = {
        "schema": "task1_full20_linearity_figures_v1",
        "plot_source_sha256": sha256_file(Path(__file__)),
        "linearity_definition_source_sha256": sha256_file(Path(__file__).with_name("inl_dnl.py")),
        "style_reference": {"source": "User-provided Fig. 17 image", "sha256": REFERENCE_SHA256},
        "source_reports_sha256": source_reports,
        "plot": {
            "signed_axis_range": [SIGNED_MIN, SIGNED_MAX],
            "samples_per_block_maximum": BLOCK,
            "detail_signed_axis_ranges": DETAIL_RANGES,
            "dem_body_zoom_lsb20_ranges": BODY_ZOOM_RANGES,
            "dimensions_mm": [WIDTH_MM, HEIGHT_MM],
            "font_family": font,
            "normalization": "Per-arm mean of interior 20-bit code widths; cumulative DNL for endpoint INL",
        },
        "cases": cases,
        "output_sha256": {
            filename: sha256_file(args.out / filename)
            for case in cases
            for filename in case["artifacts"]
        },
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
