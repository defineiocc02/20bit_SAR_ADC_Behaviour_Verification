#!/usr/bin/env python3
# =============================================================================
# 文件名     : draw_cooperation.py
# 模块名     : draw_cooperation
# 版本       : v1.0
# 功能       : Draw the sample binding between the eight-slice ADC and CAAZ.
#
# 设计说明   :
#   * 承担 联合效应/噪声实验与图件 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Draw the sample binding between the eight-slice ADC and CAAZ."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, Rectangle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from tools.run_open_rtl import digest, new_output, write_json  # noqa: E402

from task1.experiments.full20_linearity_plots import chinese_style  # noqa: E402
from task3.model.caaz20 import CaazOracle, Input  # noqa: E402

WIDTH_MM, HEIGHT_MM = 146, 129
EVEN, ODD = "#0072B2", "#D55E00"
JOINT_FIXTURE = "task3/evidence/negative/integrated/joint_dither_caaz_data.npz"
SOURCES = (
    "references/PKU_VLSI2025_C32_1_PAPER.pdf",
    "references/PKU_VLSI2025_C32_1_SLIDES.pdf",
    "references/ISSCC2024_PAPER.pdf",
    "references/ISSCC2024_SLIDES.pdf",
    "task1/SPEC.md",
    "task1/BRIDGE_SPEC.md",
    "task1/rtl/module1_bridge_top.sv",
    "task1/rtl/slice_scheduler.sv",
    "task2/eight_rtl/SPEC.md",
    "task2/eight_rtl/rtl/dither8_top.sv",
    "task3/SPEC.md",
    JOINT_FIXTURE,
    "task3/model/caaz20.py",
    "task3/rtl/caaz20_pair.sv",
    "task_integration/rtl/sar20_digital_lms_top.sv",
    "task_integration/LMS_PIPELINE.md",
    "task_reconstruction/fixed.py",
    "task1/experiments/full20_linearity_plots.py",
    "task3/tools/draw_cooperation.py",
)


# =========================================================================
# 1. 函数组：_transactions / _text / _box
# =========================================================================
# ------------------------------------------------------------------------
def _transactions():
    model = CaazOracle()
    controls = [model.step(Input(acq_valid=True, acq_id=n)) for n in range(7)]
    returns = [
        model.step(
            Input(
                raw_valid=True,
                raw_id=control.acq_id,
                raw_chop_neg=control.chop_neg,
                raw_az_id=control.az_id,
            )
        )
        for control in controls
    ]
    pairs = [
        (o.out_first_id, o.out_id)
        for o in returns
        if o.out_valid and o.out_pair and o.out_first_id >= 2
    ]
    if pairs != [(2, 3), (4, 5)] or not all(c.acq_valid for c in controls):
        raise ValueError("CAAZ model changed the sample pairing shown in the diagram")
    return controls, pairs


# ------------------------------------------------------------------------
def _text(ax, x, y, label, *, center=False, bold=False, color="black", size=8):
    math_only = label.startswith("$") and label.endswith("$")
    font = "DejaVu Sans" if math_only else plt.rcParams["font.family"]
    return ax.text(
        x,
        y,
        label,
        ha="center" if center else "left",
        va="center",
        fontfamily=font,
        fontweight=600 if bold else 400 if math_only else plt.rcParams["font.weight"],
        color=color,
        fontsize=size,
        linespacing=1.35,
    )


# ------------------------------------------------------------------------
def _box(ax, x, y, w, h, label):
    ax.add_patch(Rectangle((x, y), w, h, facecolor="white", edgecolor="black", lw=0.75))
    _text(ax, x + w / 2, y + h / 2, label, center=True)


# =========================================================================
# 2. 函数组：_arrow / _signal_flow / _sample_order
# =========================================================================
# ------------------------------------------------------------------------
def _arrow(ax, start, end, *, control=False):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=7,
            lw=0.6 if control else 0.75,
            ls=(0, (2, 1.5)) if control else "solid",
            color="0.30" if control else "black",
            shrinkA=0,
            shrinkB=0,
        )
    )


# ------------------------------------------------------------------------
def _signal_flow(ax):
    _text(ax, 4, 119, "(a) 模拟换相、逐笔转换与数字配对", bold=True)
    _text(ax, 14, 111, "外部模拟宏", bold=True)
    _text(ax, 90, 111, "片上数字", bold=True)
    ax.vlines((86, 86), (88, 106), (97, 115), color="0.55", lw=0.6, ls=(0, (2, 1.5)))
    _text(ax, 3, 99, r"$x_n$")
    _box(ax, 14, 92, 21, 14, "差分换相\n正相／反相")
    _box(ax, 42, 92, 38, 14, "SADC／八片 RDAC\n共用 RA → ADC2")
    _box(ax, 92, 92, 24, 14, "Q28 位权重构\n逐笔去 dither")
    _box(ax, 123, 92, 20, 14, "CAAZ\n偶码减奇码")
    for left, right in ((8, 14), (35, 42), (80, 92), (116, 123)):
        _arrow(ax, (left, 99), (right, 99))
    _text(ax, 86, 103, "原码", center=True, size=7)

    _box(ax, 4, 69, 33, 14, "按 ID 发 CAAZ 意图\n换相与 AZ 更新／保持")
    _box(ax, 42, 69, 40, 14, "本笔片号与扰动字\n换相后注入 SADC／RDAC")
    _box(ax, 89, 69, 54, 14, "ADC2、片号、采／转换位图同 ID\n宏确认实际极性和 AZ 代")
    for x in (24.5, 61, 104, 133):
        _arrow(ax, (x, 83), (x, 92), control=True)
    ax.plot((37, 39, 39, 48), (76, 76, 87, 87), color="0.30", lw=0.6, ls=(0, (2, 1.5)))
    _arrow(ax, (48, 87), (48, 92), control=True)
    _text(ax, 43.5, 89.1, "AZ", center=True, size=7)
    _text(ax, 4, 63, "数字把 SADC 粗码 c 加高路整数 U，Fig.21 DEM 据此生成转换位图。")
    _text(ax, 4, 58, "Q28 使用主转换位图与子位图差（转换减采样），按片去扰后交 CAAZ。")


# ------------------------------------------------------------------------
def _sample_order(ax, controls, pairs):
    _text(ax, 4, 50, "(b) 连续采样槽与共用 RA 的样本归属", bold=True)
    rows = (44, 39, 34, 29, 24, 19)
    labels = (
        "相对采样时刻",
        "采样 ID／转换 ID",
        "采样组／转换组",
        "换相后采样输入",
        "随采样发 AZ 意图",
        "转换 ID 进 RA 后",
    )
    for y, label in zip(rows, labels, strict=True):
        _text(ax, 4, y, label)
    groups = "PABCDE"
    for col, acquisition in enumerate(controls[2:]):
        n = acquisition.acq_id
        conversion = controls[n - 1]
        x = 45 + 21 * col
        acq_color = ODD if acquisition.chop_neg else EVEN
        conv_color = ODD if conversion.chop_neg else EVEN
        sign = "-" if acquisition.chop_neg else "+"
        values = (
            (f"{100 * col} ns", "black"),
            (f"{n}／{n - 1}", "black"),
            (f"{groups[col + 1]}／{groups[col]}", "black"),
            (rf"${sign}x_{n}$", acq_color),
            (f"{'更新' if acquisition.az_capture else '保持'} g{acquisition.az_id}", acq_color),
            (f"{'更新' if conversion.az_capture else '保持'} g{conversion.az_id}", conv_color),
        )
        for y, (value, color) in zip(rows, values, strict=True):
            _text(ax, x, y, value, center=True, color=color)
    ax.hlines((46.8, 41.4, 16.2), xmin=3.9, xmax=143, color="0.63", lw=0.45)
    ax.vlines((33, 55.5, 97.5), ymin=16.2, ymax=46.8, color="0.70", lw=0.45)
    _text(ax, 4, 11.9, "两码返回后提交")
    for even, odd in pairs:
        left, right = 36 + 21 * (even - 1), 54 + 21 * (odd - 1)
        ax.plot((left, left, right, right), (15.1, 14.2, 14.2, 15.1), color="black", lw=0.6)
        _text(
            ax,
            (left + right) / 2,
            11.9,
            rf"$y_{even // 2} = \lfloor(r_{even}-r_{odd})/2\rfloor$",
            center=True,
        )
    _text(ax, 4, 6.6, "每槽 8 片采样、上组 8 片转换、余 2 片。配对输出 5 MS/s。")
    _text(ax, 4, 2.7, "P、A～E 是组标签。AZ 意图随 ID 保存，宏在该笔转换时窗执行。", size=7.4)


# =========================================================================
# 3. 函数组：draw / main
# =========================================================================
# -------------------------------------------------------------------------
# Save the figure and source identities for independent review.
# Render into a new external run directory.
# ------------------------------------------------------------------------
def draw(out: Path) -> dict:
    """Save the figure and source identities for independent review."""
    font = chinese_style()
    # Outline OpenType/CFF glyphs so PDF readers need no compatible CJK font decoder.
    plt.rcParams["pdf.fonttype"] = 3
    plt.rcParams["svg.hashsalt"] = "task3-caaz-cooperation"
    controls, pairs = _transactions()
    fig = plt.figure(figsize=(WIDTH_MM / 25.4, HEIGHT_MM / 25.4))
    ax = fig.add_axes((0, 0, 1, 1), xlim=(0, WIDTH_MM), ylim=(0, HEIGHT_MM))
    ax.set_axis_off()
    _text(ax, 4, 125.4, "八片 DEM／dither 与 CAAZ 的协作", bold=True, size=9)
    _signal_flow(ax)
    _sample_order(ax, controls, pairs)
    artifacts = {}
    for ext, options in (
        ("png", {"dpi": 300}),
        ("pdf", {"metadata": {"CreationDate": None, "ModDate": None}}),
        ("svg", {"metadata": {"Date": None}}),
    ):
        path = out / f"caaz_cooperation.{ext}"
        # All labels carry explicit weights. MathText fallback fonts use regular 400.
        with plt.rc_context({"font.weight": 400}):
            fig.savefig(path, **options)
        artifacts[path.name] = digest(path)
    plt.close(fig)
    with np.load(ROOT / JOINT_FIXTURE) as replay:
        plan, word = replay["plan"], replay["dither_word_on"]
        overlap = (plan[::2, :, None] == plan[1::2, None, :]).any(axis=(1, 2))
        fixture_counts = {
            "pairs": len(overlap),
            "pairs_with_common_slice_ids": int(overlap.sum()),
            "pairs_with_distinct_dither_words": int(np.count_nonzero(word[::2] != word[1::2])),
        }
    return {
        "description": "Logical cooperation and steady-state sample ownership",
        "figure_mm": [WIDTH_MM, HEIGHT_MM],
        "font": font,
        "pdf_labels": "Vector glyph outlines (Type 3) for portable rendering of OpenType/CFF",
        "sample_times_ns": [0, 100, 200, 300, 400],
        "acquisition_ids": [c.acq_id for c in controls[2:]],
        "conversion_ids": [c.acq_id for c in controls[1:-1]],
        "sample_pairs": pairs,
        "az_intent_generations": [c.az_id for c in controls[2:]],
        "az_applied_generations": [c.az_id for c in controls[1:-1]],
        "joint_fixture_3pct": fixture_counts,
        "references": {
            "ISSCC2024_PAPER.pdf": "Fig. 9.8.1 and 9.8.3",
            "ISSCC2024_SLIDES.pdf": "PDF pages 17–18",
            "PKU_VLSI2025_C32_1_PAPER.pdf": "Fig. 5 and 6",
            "PKU_VLSI2025_C32_1_SLIDES.pdf": "slides 32–34, PDF pages 33–35",
        },
        "input_sha256": {path: digest(ROOT / path) for path in SOURCES},
        "artifact_sha256": artifacts,
    }


# ------------------------------------------------------------------------
def main() -> None:
    """Render into a new external run directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    out = new_output(args.out)
    write_json(out / "summary.json", draw(out))


if __name__ == "__main__":
    main()
