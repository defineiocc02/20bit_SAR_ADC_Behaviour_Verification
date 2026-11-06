"""从归档数据重绘中文效果图。只计算绘图用的频谱与统计。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager, ft2font
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from adi_model.metrics import spectrum_dbfs

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "task1/evidence"
GRAY = "#626A73"
BLUE = "#106B9B"
ORANGE = "#A85227"
PALE = "#DCEBDD"


def _style(font: Path | None) -> Path:
    if font is None:
        candidates = [
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
            *(
                Path(entry.fname)
                for entry in font_manager.fontManager.ttflist
                if entry.name in {"Noto Sans CJK SC", "WenQuanYi Zen Hei", "Source Han Sans SC"}
            ),
        ]
        font = next((path for path in candidates if path.is_file()), None)
    if font is None or not font.is_file():
        raise ValueError("找不到中文字体。用 --font 指定本地中文字体文件。")
    face = ft2font.FT2Font(str(font))
    if any(not face.get_char_index(ord(char)) for char in "交织时偏电容失配冻结扰动转换码"):
        raise ValueError("指定字体缺少绘图所需的中文字形。")
    font_manager.fontManager.addfont(str(font))
    family = face.family_name
    weights = [
        entry.weight
        for entry in font_manager.fontManager.ttflist
        if entry.name == family and isinstance(entry.weight, int)
    ]
    regular = min(weights, key=lambda weight: abs(weight - 400), default=400)
    bold = min(weights, key=lambda weight: abs(weight - 700), default=700)
    matplotlib.rcParams.update(
        {
            "font.family": [family, "DejaVu Sans"],
            "font.size": 11,
            "font.weight": regular,
            "figure.titleweight": bold,
            "axes.titlesize": 13,
            "axes.titleweight": bold,
            "axes.labelsize": 11,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.grid": True,
            "grid.alpha": 0.24,
            "grid.linewidth": 0.65,
            "legend.frameon": False,
            "axes.unicode_minus": False,
            "svg.fonttype": "path",
            "svg.hashsalt": "task1-chinese-effects-v1",
        }
    )
    return font


def _save(fig: plt.Figure, out: Path, name: str) -> None:
    fig.savefig(out / f"{name}.png", dpi=200, facecolor="white")
    fig.savefig(out / f"{name}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def _spectrum(ax: plt.Axes, samples: np.ndarray, cfg: dict, **line: object) -> np.ndarray:
    freq, dbfs = spectrum_dbfs(
        samples, cfg["fs_hz"], cfg["full_scale_peak_v"], window=cfg["fft_window"]
    )
    ax.plot(freq / 1e6, dbfs, **line)
    ax.set(xlim=(0, cfg["fs_hz"] / 2e6), xlabel="频率（MHz）", ylabel="频谱幅度（dBFS／谱线）")
    return dbfs


def _mode(label: str, metrics: dict) -> str:
    return f"{label}   SFDR {metrics['SFDR_dB']:.2f} dB   SNDR {metrics['SNDR_dB']:.2f} dB"


def _interleave(out: Path, traces: np.lib.npyio.NpzFile, summary: dict) -> None:
    cfg = summary["configuration"]
    cases = summary["interleave"]
    fixed, dynamic = (cases[k]["mirror_dBFS"] for k in ("fixed_ab", "dynamic"))
    freq = cfg["mirror_hz"] / 1e6
    fig, (spectrum, roles) = plt.subplots(
        2, 1, figsize=(10.5, 6.9), height_ratios=(2.2, 1), layout="constrained"
    )
    fig.suptitle(
        f"动态选片降低交织时偏的镜像杂散\n{freq:.6f} MHz 镜像 · {fixed:.2f} → {dynamic:.2f} dBFS",
        fontsize=17,
    )
    _spectrum(spectrum, traces["input_v"], cfg, label="理想零时偏", color=GRAY, lw=0.75)
    for case, label, color in (
        ("fixed_ab", "固定 A/B 交替", ORANGE),
        ("dynamic", "动态选片", BLUE),
    ):
        dbfs = _spectrum(
            spectrum,
            traces[f"timing_{case}_v"],
            cfg,
            label=_mode(label, cases[case]),
            color=color,
            lw=0.85,
        )
        mirror_bin = round(cfg["mirror_hz"] / cfg["fs_hz"] * len(traces[f"timing_{case}_v"]))
        np.testing.assert_allclose(dbfs[mirror_bin], cases[case]["mirror_dBFS"], atol=1e-8)
    spectrum.axvline(freq, color=GRAY, lw=0.8, ls="--")
    spectrum.set(ylim=(-171, 3), title="同一组切片时偏 · 采样率 10 MS/s · 理想均值模型")
    spectrum.legend(loc="upper left", fontsize=9)

    slots = 32
    mask = np.zeros((len(traces["delay_s"]), slots), dtype=int)
    columns = np.arange(slots)[:, None]
    mask[traces["acquisition_plan"][:slots], columns] = 1
    mask[traces["conv_plan"][:slots], columns] = 2
    colors = ("#E5EAF0", "#69A7D3", "#244464")
    roles.imshow(
        mask,
        cmap=ListedColormap(colors),
        norm=BoundaryNorm((-0.5, 0.5, 1.5, 2.5), 3),
        aspect="auto",
        interpolation="nearest",
        origin="lower",
    )
    roles.grid(False)
    roles.set(
        xlabel="有效采样时隙",
        ylabel="物理切片编号",
        yticks=(0, 4, 8, 12, 17),
        title="动态选片的前 32 个时隙",
    )
    roles.legend(
        handles=[
            Patch(color=color, label=label)
            for color, label in zip(colors, ("备用", "采样", "转换"), strict=True)
        ],
        bbox_to_anchor=(1, 1.01),
        loc="lower right",
        ncols=3,
        fontsize=9,
    )
    _save(fig, out, "交织时偏")


def _usage(path: Path, cfg: dict) -> dict[str, np.ndarray]:
    rows = len(cfg["usage_denominator_by_slice"])
    cols = len(cfg["main_cap_weights"])
    rates = {mode: np.full((rows, cols), np.nan) for mode in ("off", "on")}
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["array"] == "main":
                conversions = int(row["conversions"])
                if conversions:
                    rates[row["dem"]][int(row["slice_id"]), int(row["unit"])] = (
                        int(row["selected"]) / conversions
                    )
    return rates


def _dem(
    out: Path,
    traces: np.lib.npyio.NpzFile,
    summary: dict,
    static: np.ndarray,
    rates: dict[str, np.ndarray],
) -> None:
    cfg, cases = summary["configuration"], summary["dem"]
    fig = plt.figure(figsize=(10.5, 9.5), layout="constrained")
    grid = fig.add_gridspec(3, 2, height_ratios=(1, 1, 1))
    spectrum = fig.add_subplot(grid[0, :])
    residual = fig.add_subplot(grid[1, 0])
    noise = fig.add_subplot(grid[1, 1], sharex=residual)
    heatmaps = [fig.add_subplot(grid[2, index]) for index in range(2)]
    fig.suptitle(
        "电容失配下的 DEM 开关对照\n未校准诊断 · 同一颗虚拟芯片 · 0.1% 单位电容失配",
        fontsize=17,
    )
    for mode, name, color, heatmap in zip(
        ("off", "on"), ("DEM 关", "DEM 开"), (ORANGE, BLUE), heatmaps, strict=True
    ):
        case = cases[mode]
        _spectrum(
            spectrum,
            traces[f"dem_{mode}_v"],
            cfg,
            label=_mode(name, case),
            color=color,
            lw=0.85,
        )
        peak = case["static_fit_residual_peak_v"] * 1e6
        median = case["static_median_sigma_v"] * 1e6
        fit_v, sigma_v = static[f"{mode}_fit_residual_v"], static[f"{mode}_sigma_v"]
        np.testing.assert_allclose(np.abs(fit_v).max() * 1e6, peak, atol=1e-9)
        np.testing.assert_allclose(np.median(sigma_v) * 1e6, median, atol=1e-9)
        residual.plot(
            static["input_v"], fit_v * 1e6, label=f"{name} · 峰值 {peak:.1f}", color=color, lw=0.9
        )
        noise.plot(
            static["input_v"],
            sigma_v * 1e6,
            label=f"{name} · 中位数 {median:.1f}",
            color=color,
            lw=0.9,
        )
        mesh = heatmap.imshow(
            rates[mode],
            vmin=0,
            vmax=1,
            cmap="viridis",
            aspect="auto",
            interpolation="nearest",
            origin="lower",
        )
        heatmap.grid(False)
        heatmap.axvline(14.5, color="white", lw=1)
        heatmap.set(
            title=f"{name}时的单元使用率",
            xlabel="主阵列单元编号",
            ylabel="物理切片编号",
            xticks=(0, 4, 8, 12, 15, 16),
            yticks=(0, 4, 8, 12, 17),
        )
    fig.colorbar(mesh, ax=heatmaps, label="选通次数／本切片转换次数", shrink=0.91)
    spectrum.set(ylim=(-155, 3), title="理想余量读出 · 仅切换单元 DEM")
    spectrum.legend(loc="upper right", fontsize=9)
    residual.set(title="静态误差去拟合直线后", xlabel="差分输入（V）", ylabel="残差（μV）")
    noise.set(title="同一电平内的波动", xlabel="差分输入（V）", ylabel="标准差（μV）")
    for ax in (residual, noise):
        ax.legend(fontsize=8.5)
    _save(fig, out, "固定失配下的DEM")


def _dither(out: Path, traces: tuple[np.lib.npyio.NpzFile, ...], summary: dict) -> None:
    case = summary["cases"][0]
    trace = traces[0]
    arm = "fig21_bridge_lms"
    report = case["arms"][arm]
    repeats = summary["repeat_counts"]
    fig = plt.figure(figsize=(10.5, 7.7), layout="constrained")
    grid = fig.add_gridspec(2, 2, height_ratios=(1.15, 1))
    local = fig.add_subplot(grid[0, :])
    missing, rms = (fig.add_subplot(grid[1, col]) for col in range(2))
    fig.suptitle(
        "理想减性扰动如何影响 20 位码密度\n12 位后级 ±100 mV · 跨段 DEM 开 · LMS 系数冻结",
        fontsize=17,
    )
    positions = np.arange(len(summary["cases"]))
    for key, label, color, offset in (("off", "扰动关", GRAY, -0.18), ("on", "扰动开", BLUE, 0.18)):
        width = trace[f"{arm}_density_{key}_central20_width_v"]
        codes = np.arange(len(width)) - len(width) // 2
        nominal_width = report["density"][key]["20bit"]["mean_interior_code_width_v"]
        local.step(codes, width / nominal_width - 1, color=color, where="mid", label=label)
        counts = []
        for saved, item in zip(traces, summary["cases"], strict=True):
            expected = item["arms"][arm]["density"][key]["20bit"]["zero_width_interior_codes"]
            occupied = np.unpackbits(saved[f"{arm}_density_{key}_occupied20_bitpack"])
            counts.append(int(np.count_nonzero(occupied[1:-1] == 0)))
            np.testing.assert_equal(counts[-1], expected)
        bars = missing.bar(positions + offset, counts, width=0.3, color=color, label=label)
        missing.bar_label(bars, labels=[f"{n:,}" for n in counts], padding=4)
        zero = np.asarray(counts) == 0
        missing.scatter(positions[zero] + offset, np.zeros(np.count_nonzero(zero)), color=color)
        reference = trace[f"{arm}_tone_reference_v"]
        errors = []
        for n in repeats:
            trial = f"{key}_repeat{n}"
            residual = trace[f"{arm}_tone_{trial}_post_export_v"] - reference
            errors.append(np.sqrt(np.mean(residual**2)) * 1e6)
            expected = report["tone"][trial]["after_export"]["input_error_rms_v"] * 1e6
            np.testing.assert_allclose(errors[-1], expected, atol=1e-9)
        rms.plot(repeats, errors, color=color, marker="o", label=label)
    local.set(
        title=f"中码附近逐码 DNL · 固定失配 {case['sigma_percent']:g}%",
        xlabel="有符号 20 位输出码",
        ylabel="DNL（LSB20）",
    )
    local.legend(loc="upper right")
    missing.set(
        title="全量程统计缺码",
        ylabel="零宽内码数",
        xlabel="单位电容失配",
        xticks=positions,
        xticklabels=[f"{item['sigma_percent']:g}%" for item in summary["cases"]],
        ylim=(0, 82000),
    )
    missing.legend(loc="upper right", fontsize=9)
    rms.set_xscale("log", base=2)
    rms.set(
        title=f"重复转换后平均 · 固定失配 {case['sigma_percent']:g}%",
        xlabel="同一保持样本的转换次数",
        ylabel="输出误差（μV RMS）",
        xticks=repeats,
        xticklabels=[str(n) for n in repeats],
    )
    rms.legend(fontsize=9)
    _save(fig, out, "理想减性扰动")


def _headroom(out: Path, summary: dict) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7), sharex=True, layout="constrained")
    first = summary["experiments"][0]
    half = first["adc2_half_window_for_one_export_lsb_v"] * 1e3
    fig.suptitle(
        f"冻结 LMS 权重辅助余量居中\n12 位后级窄窗 ±{half:.2f} mV · 图中为全幅理想两节点模型",
        fontsize=17,
    )
    for row, experiment in enumerate(summary["experiments"]):
        sigma = experiment["unit_sigma"] * 100
        with np.load(
            EVIDENCE / "residue_control" / f"capture_{row}.npz", allow_pickle=False
        ) as data:
            for col, (arm, label) in enumerate(
                (("fixed_ab_lms_only", "固定 A/B＋LMS"), ("fig21_bridge_lms", "跨段 DEM＋LMS"))
            ):
                ax = axes[row, col]
                ax.axhspan(-half, half, facecolor=PALE, label="允许窗口")
                counts = []
                for key, color, name in (
                    ("direct_coarse_command", GRAY, "直接用粗码"),
                    ("frozen_weight_centered_command", BLUE, "邻码居中"),
                ):
                    extrema = data[f"{arm}_static_{key}_residue_extrema_v"] * 1e3
                    ax.plot(extrema[:, 0], color=color, lw=0.8, label=name)
                    ax.plot(extrema[:, 1], color=color, lw=0.8)
                    tone = experiment["arms"][arm]["tone"][key]["target_narrow_adc2"]
                    counts.append(tone["below_range"] + tone["at_or_above_range"])
                ax.set_title(
                    f"{sigma:g}% · {label}\n正弦越界 {counts[0]:,} → {counts[1]:,} 笔／4096"
                )
                ax.set(ylabel="输入等效余量极值（mV）", ylim=(-54, 64))
                if row == 1:
                    ax.set_xlabel("前级粗码")
    axes[0, 0].legend(loc="lower left", fontsize=8, ncols=3)
    _save(fig, out, "后级余量")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """重绘并写入来源和图件的 SHA-256 清单。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=EVIDENCE / "fig_zh")
    parser.add_argument("--font", type=Path, help="本地中文字体文件，可选")
    args = parser.parse_args()
    font = _style(args.font)
    args.out.mkdir(parents=True, exist_ok=True)
    effect = EVIDENCE / "effects"
    dither = EVIDENCE / "adc2_dither"
    headroom = EVIDENCE / "residue_control"
    summary = json.loads((effect / "summary.json").read_text(encoding="utf-8"))
    with np.load(effect / "traces.npz", allow_pickle=False) as traces:
        _interleave(args.out, traces, summary)
        _dem(
            args.out,
            traces,
            summary,
            np.genfromtxt(effect / "static_sweep.csv", delimiter=",", names=True),
            _usage(effect / "usage.csv", summary["configuration"]),
        )
    with (
        np.load(dither / "traces_sigma_2pct.npz", allow_pickle=False) as sigma2,
        np.load(dither / "traces_sigma_3pct.npz", allow_pickle=False) as sigma3,
    ):
        _dither(
            args.out,
            (sigma2, sigma3),
            json.loads((dither / "summary.json").read_text(encoding="utf-8")),
        )
    _headroom(args.out, json.loads((headroom / "summary.json").read_text(encoding="utf-8")))
    sources = (
        Path(__file__).resolve(),
        ROOT / "src/adi_model/metrics.py",
        effect / "summary.json",
        effect / "traces.npz",
        effect / "static_sweep.csv",
        effect / "usage.csv",
        dither / "summary.json",
        dither / "traces_sigma_2pct.npz",
        dither / "traces_sigma_3pct.npz",
        headroom / "summary.json",
        headroom / "capture_0.npz",
        headroom / "capture_1.npz",
    )
    plots = ("交织时偏", "固定失配下的DEM", "理想减性扰动", "后级余量")
    manifest = {
        "command": "PYTHONPATH=.:src python -m task1.experiments.chinese_effect_plots",
        "python": platform.python_version(),
        "numpy": np.__version__,
        "matplotlib": matplotlib.__version__,
        "font": {"file": font.name, "sha256": _sha(font)},
        "input_sha256": {source.relative_to(ROOT).as_posix(): _sha(source) for source in sources},
        "output_sha256": {
            f"{plot}.{suffix}": _sha(args.out / f"{plot}.{suffix}")
            for plot in plots
            for suffix in ("png", "svg")
        },
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
