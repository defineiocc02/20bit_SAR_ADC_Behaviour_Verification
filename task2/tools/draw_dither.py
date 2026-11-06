"""绘制 dither 数字发生、原文注入路径和归档线性度对照。"""

from __future__ import annotations

import argparse
import hashlib
import json
import warnings
from itertools import pairwise
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[2]
WIDTH = 146
BLUE = "#0072B2"
ORANGE = "#D55E00"
GRAY = "#4D4D4D"
RULE = "#B7B7B7"
PATENT_URL = "https://patents.google.com/patent/US10505561B2/en#description"
PAPER_DOI = "10.1109/ISSCC49657.2024.10454329"
PATENT_SHA = "91ae95528592ba16a6a34c4d5336cd3abd18a35f811d22d73e2620dc83363c14"
DATA_DIR = ROOT / "task1/evidence/adc2_dither/full20"
PRBS_RECEIPT = ROOT / "task2/evidence/dither_rng/result.json"
PRBS_RTL = "task2/rtl/dither_word_gen.sv"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font() -> dict:
    for family, regular, bold in (("Hiragino Sans GB", 300, 600), ("Noto Sans CJK SC", 400, 700)):
        try:
            font = Path(
                font_manager.findfont(
                    font_manager.FontProperties(family=family, weight=regular),
                    fallback_to_default=False,
                )
            )
        except ValueError:
            continue
        if ord("扰") in font_manager.get_font(font).get_charmap():
            mpl.rcParams.update(
                {
                    "font.family": family,
                    "font.size": 8,
                    "font.weight": regular,
                    "svg.fonttype": "path",
                    "svg.hashsalt": "task2-dither-figures-v1",
                    "mathtext.fontset": "dejavusans",
                }
            )
            return {"family": family, "bold_weight": bold, "file_sha256": _sha(font)}
    raise RuntimeError("请安装 Hiragino Sans GB 或 Noto Sans CJK SC 中文字体。")


def _figure(height: float) -> tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(figsize=(WIDTH / 25.4, height / 25.4), facecolor="white")
    ax = fig.add_axes((0, 0, 1, 1), xlim=(0, WIDTH), ylim=(height, 0))
    ax.axis("off")
    return fig, ax


def _label(
    ax: plt.Axes,
    x: float,
    y: float,
    value: str,
    *,
    center: bool = False,
    size: float = 8,
    color: str = "black",
    weight: int | None = None,
) -> None:
    ax.text(
        x,
        y,
        value,
        ha="center" if center else "left",
        va="center",
        fontsize=size,
        color=color,
        weight=400 if "$" in value and weight is None else weight,
    )


def _box(ax: plt.Axes, x: float, y: float, w: float, value: str, color: str = "black") -> None:
    ax.add_patch(Rectangle((x, y), w, 9, facecolor="white", edgecolor=color, linewidth=0.75))
    _label(ax, x + w / 2, y + 4.5, value, center=True, color=color)


def _arrow(
    ax: plt.Axes, x: float, y: float, x_end: float, y_end: float, color: str = "black"
) -> None:
    ax.annotate(
        "",
        xy=(x_end, y_end),
        xytext=(x, y),
        arrowprops={
            "arrowstyle": "-|>",
            "linewidth": 0.75,
            "color": color,
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )


def _rule(ax: plt.Axes, y: float) -> None:
    ax.hlines(y, 7, 139, colors=RULE, linewidth=0.45)


def _prbs_capture(path: Path) -> tuple[np.ndarray, dict]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if (
        receipt["status"] != "PASS"
        or receipt["mismatches"] != 0
        or receipt["scope"] != "DITHER_WORD_GENERATOR_DIGITAL_ONLY"
        or receipt["delay_polynomial"] != "1+x^28+x^31"
        or _sha(ROOT / PRBS_RTL) != receipt["source_sha256"][PRBS_RTL]
    ):
        raise ValueError("字发生器对拍收据不对应当前 RTL")
    stats = receipt["statistics"]
    counts = np.asarray(stats["word_counts_0_to_63"], dtype=np.int64)
    if (
        counts.shape != (64,)
        or np.any(counts <= 0)
        or counts.sum() != stats["continuous_accepted_words"]
        or counts.min() != stats["minimum_word_count"]
        or counts.max() != stats["maximum_word_count"]
    ):
        raise ValueError("归档的连续字计数不完整")
    capture = {
        "receipt": path.relative_to(ROOT).as_posix(),
        "receipt_sha256": _sha(path),
        "rtl_sha256": receipt["source_sha256"][PRBS_RTL],
        "vector_sha256": receipt["vector_sha256"],
        "seed": receipt["dither_seed"],
        "continuous_accepted_words": stats["continuous_accepted_words"],
        "min_count": int(counts.min()),
        "max_count": int(counts.max()),
    }
    return counts, capture


def _prbs_figure(bold: int, counts: np.ndarray, seed: int) -> plt.Figure:
    height = 138
    fig, ax = _figure(height)
    _label(ax, 7, 8, "每笔采样登记一个六位扰动字", size=12, weight=bold)
    _label(ax, 7, 16, r"本项目 PRBS31 字发生器｜延迟多项式 $1+x^{28}+x^{31}$", color=GRAY)

    blocks = (
        (7, 27, "31 位状态"),
        (41, 27, "推进 6 步"),
        (75, 34, "登记字与样本号"),
        (121, 18, "模拟采样"),
    )
    for x, width, value in blocks:
        _box(ax, x, 27, width, value)
    for (x, width, _), (next_x, _, _) in pairwise(blocks):
        _arrow(ax, x + width, 31.5, next_x, 31.5)
    _label(ax, 115, 23, "建立", center=True, color=GRAY, size=7)
    _label(ax, 7, 43, r"受理且开启时一次推进 6 步，反馈 $s[30]\oplus s[27]$，先生成 $D_6$。")
    _label(ax, 7, 50, "受理且关闭时发零字、保持状态。载入零种子或与受理同沿时报错且不发字。")
    _label(ax, 7, 57, "模拟采样前留出建立时间。下游按样本号保留扰动字至同笔去除。")
    _rule(ax, 63)

    total = int(counts.sum())
    expected = total / counts.size
    _label(ax, 7, 70, f"连续 {total:,} 个有效字，64 种码均出现", weight=bold)
    _label(
        ax,
        7,
        77,
        f"各码 {counts.min():,}～{counts.max():,} 次｜等频参考 {expected:,.0f} 次",
        color=GRAY,
    )
    hist = fig.add_axes((22 / WIDTH, 22 / height, 115 / WIDTH, 30 / height))
    hist.spines[["right", "top"]].set_visible(False)
    hist.spines[["left", "bottom"]].set_linewidth(0.7)
    hist.axhline(expected, color=GRAY, linewidth=0.6, linestyle="--", zorder=1)
    hist.plot(np.arange(counts.size), counts, "o", color=BLUE, markersize=2.8, zorder=2)
    hist.set_xlim(-1, 64)
    lower_tick = int(counts.min()) // 100 * 100
    upper_tick = (int(counts.max()) + 99) // 100 * 100
    hist.set_ylim(lower_tick - 10, upper_tick + 10)
    hist.set_xticks([0, 8, 16, 24, 32, 40, 48, 56, 63])
    hist.set_yticks([lower_tick, expected, upper_tick])
    hist.tick_params(direction="out", length=2.5, width=0.7, pad=2)
    hist.set_xlabel("六位字（十进制）", labelpad=2)
    hist.set_ylabel("出现次数", labelpad=2)
    _label(
        ax, 7, 132, f"固定种子 0x{seed:08X} 的有限字频。序列周期和独立性需另行验证。", color=GRAY
    )
    return fig


def _ppt_figure(bold: int) -> plt.Figure:
    fig, ax = _figure(101)
    _label(ax, 7, 8, "讲稿的两路扰动注入", size=12, weight=bold)
    _label(ax, 7, 16, "ISSCC 2024 讲稿第 17 页｜功能重绘", color=GRAY)
    _label(ax, 7, 23, "低路注入 SADC、RDAC｜高路只注入 RDAC｜两路均参与数字合成")

    _label(ax, 7, 50, r"$V_{\mathrm{IN}}$")
    ax.plot((7, 20, 20), (55, 55, 33.5), color="black", lw=0.75)
    ax.plot((20, 20), (55, 75.5), color="black", lw=0.75)
    _arrow(ax, 20, 33.5, 71, 33.5)
    _arrow(ax, 20, 75.5, 102, 75.5)
    ax.plot(20, 55, marker="o", markersize=2.7, color="black")

    _box(ax, 71, 29, 25, "SADC 采样")
    _box(ax, 108, 29, 26, "SAR 结果")
    _arrow(ax, 96, 33.5, 108, 33.5)
    _arrow(ax, 119, 38, 119, 45)
    _box(ax, 102, 45, 32, "数字合成（＋）")
    _box(ax, 108, 58.5, 20, "DEM")
    _arrow(ax, 118, 54, 118, 58.5)
    _arrow(ax, 118, 67.5, 118, 71)
    _box(ax, 102, 71, 28, "RDAC 采样")
    _arrow(ax, 130, 75.5, 136, 75.5)
    _label(ax, 137.5, 75.5, "残差", size=7)

    _box(ax, 25, 45, 23, "扰动发生器")
    for y, name, color, label_y in (
        (47.5, "lower 低路", BLUE, 42.5),
        (51.5, "upper 高路", ORANGE, 56.5),
    ):
        _arrow(ax, 48, y, 102, y, color)
        _label(ax, 74, label_y, name, color=color)
    _arrow(ax, 57, 47.5, 57, 33.5, BLUE)
    # 低路下行与高路横线交叉处留白，沿用讲稿的两条独立支路。
    ax.plot((61, 61), (47.5, 50.1), color=BLUE, lw=0.75)
    _arrow(ax, 61, 52.9, 61, 75.5, BLUE)
    _arrow(ax, 66, 51.5, 66, 75.5, ORANGE)
    for x, y, color in ((57, 47.5, BLUE), (61, 47.5, BLUE), (66, 51.5, ORANGE)):
        ax.plot(x, y, marker="o", markersize=2.7, color=color)

    _label(ax, 7, 88, "量化码转至 RDAC 时，扰动范围扩展 2 bit，辅助 DEM 线性化。", weight=bold)
    _label(ax, 7, 95, "“＋”沿用讲稿符号。物理极性和后级去除待电路验证。", color=GRAY)
    return fig


def _patent_figure(bold: int) -> plt.Figure:
    fig, ax = _figure(124)
    _label(ax, 7, 8, "专利七片示例的位分配与数字去除", size=12, weight=bold)
    _label(ax, 7, 16, "US10505561B2 Fig.19 及对应正文｜以该级 LSB 为单位", color=GRAY)

    _label(ax, 7, 26.5, "同一扰动字", weight=bold)
    for index, bit in enumerate(range(6, 0, -1)):
        _box(ax, 53 + 13 * index, 22, 10, f"$D_{bit}$", ORANGE if bit in (6, 4) else BLUE)
    _rule(ax, 35)
    _label(ax, 7, 40.5, "信号路径", weight=bold)
    _label(ax, 49, 40.5, "采用位", weight=bold)
    _label(ax, 96, 40.5, "标称扰动", weight=bold)
    _rule(ax, 45)
    rows = (
        (50, "前级 ADC 采样", "$D_5, D_3, D_2, D_1$", "$L=2D_5+m/7$", BLUE),
        (59, "采样 RDAC", "$D_6, D_5, D_4, D_3, D_2, D_1$", "$U+L$", "black"),
        (68, "前级数字修正", "$D_6, D_4$", "$U=4D_6+D_4$", ORANGE),
    )
    for y, path, bits, weight, color in rows:
        _label(ax, 7, y, path)
        _label(ax, 49, y, bits, color=color)
        _label(ax, 96, y, weight, color=color)
    _rule(ax, 73)
    _label(ax, 7, 78, r"$m=4D_3+2D_2+D_1$。$2+5/7$ 由 2 片取 2、5 片取 3 得到。")

    blocks = (
        (7, 25, "前级码 $c$"),
        (39, 25, "$k=c-U$"),
        (71, 29, "RDAC 形成残差"),
        (107, 32, "后级量化"),
    )
    for x, width, value in blocks:
        _box(ax, x, 84, width, value)
    for (x, width, _), (next_x, _, _) in pairwise(blocks):
        _arrow(ax, x + width, 88.5, next_x, 88.5)
    _box(ax, 7, 100, 53, "保留同笔低路字 $L$", BLUE)
    _box(ax, 93, 100, 46, "后级数字扣除对应量")
    _arrow(ax, 60, 104.5, 93, 104.5)
    _arrow(ax, 123, 93, 123, 100)

    _label(
        ax, 7, 116, "工程时序按同一采样号对齐。目标八片的注入权重、极性和余量待验证。", color=GRAY
    )
    return fig


def _density_cases() -> tuple[list[dict], dict[str, str]]:
    manifest = DATA_DIR / "manifest.json"
    report = json.loads(manifest.read_text(encoding="utf-8"))
    if report["output_bits"] != 20 or [case["sigma_percent"] for case in report["cases"]] != [2, 3]:
        raise ValueError("归档范围与图注的 20 位、2% 和 3% 固定失配不符")
    sources = {manifest.relative_to(ROOT).as_posix(): _sha(manifest)}
    cases = []
    for case in report["cases"]:
        condition = (
            case["arm"],
            case["adc2_bits"],
            case["adc2_half_range_v_input_referred"],
            case["state_count"],
            case["dither_on_word_count"],
        )
        if condition != ("fig21_bridge_lms", 12, 0.1, 16, 16):
            raise ValueError("归档的 DEM、ADC2 或等权统计条件与图注不符")
        sigma = case["sigma_percent"]
        source = DATA_DIR / f"adc2_12bit_sigma_{sigma:g}pct_dither_code_widths.npz"
        digest = _sha(source)
        if digest != report["output_sha256"][source.name]:
            raise ValueError(f"归档码宽散列不符 {source.name}")
        sources[source.relative_to(ROOT).as_posix()] = digest
        measured = {}
        with np.load(source, allow_pickle=False) as capture:
            for mode in ("off", "on"):
                widths = capture[f"{mode}_uniform_input_width_v_20b"]
                if widths.shape != (1 << 20,) or np.any(~np.isfinite(widths) | (widths < 0)):
                    raise ValueError(f"归档码宽不满足 20 位范围 {source.name}")
                np.testing.assert_allclose(widths.sum(), 10, rtol=0, atol=5e-11)
                interior = widths[1:-1]
                dnl = interior / interior.mean() - 1
                inl = np.r_[0.0, np.cumsum(dnl)]
                values = {
                    "dnl_min_lsb": float(dnl.min()),
                    "dnl_max_lsb": float(dnl.max()),
                    "endpoint_inl_peak_lsb": float(np.abs(inl).max()),
                    "zero_width_interior_codes": int(np.count_nonzero(interior <= 1e-15)),
                }
                reference = case["arms"][mode]
                np.testing.assert_allclose(
                    list(values.values()), [reference[key] for key in values], rtol=0, atol=1e-9
                )
                if reference["adc2_overload_input_width_v"] != 0:
                    raise ValueError("存在 ADC2 超窗，无法作全幅 DNL 对照")
                measured[mode] = values
        cases.append({"sigma_percent": sigma, "arms": measured})
    return cases, sources


def _result_figure(bold: int, cases: list[dict]) -> plt.Figure:
    fig, ax = _figure(99)
    _label(ax, 7, 8, "理想细级 dither 的线性度对照", size=12, weight=bold)
    _label(ax, 7, 16, "同一虚拟芯片｜Fig.21 DEM 开｜冻结 NLMS｜理想 ADC2 12 位", color=GRAY)
    _label(
        ax, 7, 23, "ADC2 输入等效 ±100 mV｜16 个状态等权｜开启时另用 16 个等权扰动字", color=GRAY
    )

    for x, value in ((7, "失配／扰动"), (57, "DNL 范围"), (102, "峰值 |INL|"), (130, "零宽内码")):
        _label(ax, x, 33, value, center=x != 7, weight=bold)
    _label(ax, 57, 39, "LSB20", center=True, size=6.5, color=GRAY)
    _label(ax, 102, 39, "LSB20", center=True, size=6.5, color=GRAY)
    _rule(ax, 43)
    for group, case in enumerate(cases):
        for row, (mode, state, color) in enumerate((("off", "关", GRAY), ("on", "开", BLUE))):
            y = 49 + 22 * group + 9 * row
            result = case["arms"][mode]
            low, high = result["dnl_min_lsb"], result["dnl_max_lsb"]
            _label(ax, 7, y, f"{case['sigma_percent']:g}%　{state}", color=color, weight=bold)
            _label(ax, 57, y, f"{low:+.3f} 至 {high:+.3f}", center=True, color=color)
            _label(ax, 102, y, f"{result['endpoint_inl_peak_lsb']:.3f}", center=True, color=color)
            _label(ax, 130, y, f"{result['zero_width_interior_codes']:,}", center=True, color=color)
        _rule(ax, 64 + 22 * group)
    _label(ax, 7, 93, "等权码密度统计。Fig.19 实际电容注入及八片实现待验证。", color=GRAY)
    return fig


def main() -> None:
    """生成中文图并记录输入、脚本和图件的散列。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "task2/figures")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    font = _font()
    bold = font["bold_weight"]
    cases, sources = _density_cases()
    word_counts, prbs_capture = _prbs_capture(PRBS_RECEIPT)
    figures = {
        "dither_prbs31": _prbs_figure(bold, word_counts, prbs_capture["seed"]),
        "dither_isscc_ppt": _ppt_figure(bold),
        "dither_fig19": _patent_figure(bold),
        "dither_ideal_adc2_effect": _result_figure(bold, cases),
    }
    outputs = {}
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Glyph .* missing from font")
        for stem, fig in figures.items():
            for suffix in ("png", "svg"):
                output = args.out / f"{stem}.{suffix}"
                if suffix == "png":
                    fig.savefig(output, dpi=300)
                else:
                    fig.savefig(output, metadata={"Date": None})
                outputs[output.name] = _sha(output)
            plt.close(fig)
    references = {
        "references/ISSCC2024_SLIDES.pdf": {"page": 17},
        "references/ISSCC2024_PAPER.pdf": {"printed_page": 182, "doi": PAPER_DOI},
    }
    for filename, reference in references.items():
        reference["sha256"] = _sha(ROOT / filename)
    manifest = {
        "schema": "task2_dither_figures_v1",
        "source_references": references,
        "patent_reference": {
            "url": PATENT_URL,
            "figure": "Fig.19, sheet 20, PDF page 22; description PDF page 33",
            "verified_local_original_sha256": PATENT_SHA,
        },
        "plot_source_sha256": _sha(Path(__file__)),
        "data_source_sha256": sources,
        "nominal_width_mm": WIDTH,
        "font": font,
        "numpy": np.__version__,
        "matplotlib": mpl.__version__,
        "prbs31_capture": prbs_capture,
        "adc2_effects_recomputed": cases,
        "output_sha256": outputs,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
