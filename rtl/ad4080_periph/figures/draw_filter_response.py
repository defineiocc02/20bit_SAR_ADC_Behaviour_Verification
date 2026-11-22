#!/usr/bin/env python3
# =============================================================================
# 文件名     : draw_filter_response.py
# 模块名     : AD4080 数字滤波频响审查图
# 版本       : v1.0
# 功能       : 把实际 RTL 扫频和量测摘要与手册表、读图区分呈现。
#
# 设计说明   :
#   * 面板 A/B 都以原 ADC 采样率 Fs 归一，便于对照手册原单位。
#     面板 C 乘总倍率 R，换算到最终输出字率 Fs/R；补偿支路 R=2N。
#   * 实线连接逐频点真实幅值。没有曲线或摘要时显示未测或缺档，
#     不读取模拟参考列补齐 RTL、也不从曲线伪造量测摘要。
#   * 表 28 是印刷数值；图 105／106 以原向量图的有限采点及读图包络呈现。
#     三组证据分别取自独立输入的 CSV，完整摘要写入 SVG 元数据。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import csv
from hashlib import sha256
from io import StringIO
from math import isfinite
from pathlib import Path

from draw_signal_timing import INK, MUTED, GRID, MANUAL, setup_fonts
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, ScalarFormatter, StrMethodFormatter


MODES = {
    "sinc1": ("SINC1", "#215b8a"),
    "sinc5": ("SINC5", "#a45013"),
    "sinc5_comp": ("SINC5 + 补偿", "#126e5e"),
}
FIGURE_COLOR = "#714a87"                       # 厂商图的独立估读取色
REPRESENTATIVE_N = 2                           # p.70 图 105/106 对应 CIC 的 N=2
PASSBAND_LIMIT_DB = .1                         # p.64 通带平直度的参照刻度
OFFICIAL_CUTOFF_DB = -3.0                      # 保持表 28 的标称分贝定义
PASSBAND_VIEW_MAX = .093                       # 包含图106最后一个读点 .092


# =============================================================================
# 1. 只从证据快照读取数值
# =============================================================================
def read_csv(path):
    """返回 CSV 原始字段和同一字节快照的 SHA-256，编码为 UTF-8。"""
    raw = path.read_bytes()
    rows = list(csv.DictReader(StringIO(raw.decode("utf-8-sig"))))
    if not rows:
        raise ValueError(f"CSV 没有观测行：{path}")
    return rows, sha256(raw).hexdigest()


def mode_name(name):
    """处理测量报告的首字母大小写及补偿模式英文短名。"""
    key = "".join(ch for ch in name.casefold() if ch.isalnum())
    names = {"sinc1": "sinc1", "sinc5": "sinc5", "comp": "sinc5_comp",
             "sinc5comp": "sinc5_comp", "sinc5compensated": "sinc5_comp",
             "sinc5compensation": "sinc5_comp"}
    if key not in names:
        raise ValueError(f"未识别的滤波支路：{name}")
    return names[key]


def read_manual(rows):
    """以各档 (滤波模式, N, R) 存印刷截止值，另取图105/106读点。"""
    table, codes, points = {}, {}, []
    for row in rows:
        if row["source_kind"] == "printed_table" and row["source_id"] == "Table 28":
            key = (mode_name(row["filter"]), int(row["sinc_stage_decimation_n"]),
                   int(row["total_decimation_d"]))
            table[key] = float(row["value"])
            codes[key] = int(row["sinc_dec_rate_bits"], 2)
        elif row["source_kind"] == "vector_figure" and row["source_id"] in {"Fig. 105", "Fig. 106"}:
            points.append(row)
    if not table:
        raise ValueError("原件数字表缺少表 28 数据")
    return table, codes, points


def measurement_key(row, codes):
    """用原件码与倍率共同剔除拥有同一钳位后 N、R 的保留码。"""
    key = (mode_name(row["filter"]), int(row["sinc_decimation"]),
           int(row["total_decimation"]))
    return key if codes.get(key) == int(row["code"]) else None


def read_rtl(curve_rows, summary_rows, codes):
    """连接合法档的幅值及截止摘要，频率均是相对于输入 Fs 的无量纲数。"""
    curves, cutoffs = {}, {}
    # 钳位和无效编码由测试另外收录。仅以印刷表共同的模式和倍率作为图的档位，
    # 不把独立无效用例并到某个合法档曲线或重复计数。
    for row in curve_rows:
        key = measurement_key(row, codes)
        if key is None:
            continue
        # 台架保留若干零幅频点，dB 字段为空。NaN 会中断连线且不画一个猜测
        # 的底噪或功率值，邻近真实频点仍正常显示。
        raw = row["magnitude_db"]
        value = float(raw) if raw else float("nan")
        frequency = float(row["f_over_fs_in"])
        if isfinite(frequency) and frequency >= 0:
            curves.setdefault(key, []).append((frequency, value))
    for row in summary_rows:
        key = measurement_key(row, codes)
        raw = row["measured_bw_3db_over_input_fs"]
        if key is not None and raw:
            value = float(raw)
            if isfinite(value):
                cutoffs[key] = value
    # 浮点参考列、候选 FIR 与预期响应不在这两条读取路径内。
    if not any(isfinite(y) for xy in curves.values() for _, y in xy) or not cutoffs:
        raise ValueError("缺少真实 RTL 逐频幅值或截止摘要")
    return curves, cutoffs


# =============================================================================
# 2. 排三幅共用证据图例的坐标面板
# =============================================================================
def style_axis(ax, title, xlabel, ylabel, font, source_page):
    """标注归一化基准、幅值单位和原件页链接，淡网格便于读点。"""
    ax.set_title(title, loc="left", fontproperties=font, color=INK,
                 fontsize=11.6, pad=9).set_url(f"{MANUAL}#page={source_page}")
    ax.set_xlabel(xlabel, fontproperties=font, color=MUTED)
    ax.set_ylabel(ylabel, fontproperties=font, color=MUTED)
    ax.tick_params(colors=MUTED, direction="out", length=3)
    ax.grid(color=GRID, linewidth=.65)
    ax.set_axisbelow(True)
    for name in ("top", "right"):
        ax.spines[name].set_visible(False)
    for name in ("bottom", "left"):
        ax.spines[name].set_color(GRID)


def draw_curve(ax, xy, colour):
    """实线只连接已取样坐标，点标和曲线共享同一真实输入集合。"""
    if xy:
        x, y = zip(*sorted(xy))
        ax.plot(x, y, lw=1.7, marker=".", markersize=2.7, color=colour, zorder=3)


def figure_point(ax, row):
    """图读点用空心菱形；频率带宽的误差在横轴，读增益在纵轴。"""
    value = float(row["value"])
    envelope = float(row["read_uncertainty_abs"] or 0)
    # 单位可以是输入归一频率或增益，同一字段不能同时映到横纵两轴。
    if row["unit"] == "f_over_fs":
        gain = OFFICIAL_CUTOFF_DB if row["source_id"] == "Fig. 105" else -PASSBAND_LIMIT_DB
        x, y, xerr, yerr = value, gain, envelope, None
    else:
        x, y, xerr, yerr = float(row["x_over_fs"]), value, None, envelope
    ax.errorbar(x, y, xerr=xerr, yerr=yerr, fmt="D", ms=4.1, alpha=.92,
                markerfacecolor="white", markeredgecolor=FIGURE_COLOR,
                color=FIGURE_COLOR, linewidth=.9, capsize=2, zorder=5)


def response_panels(cutoff_ax, pass_ax, curves, table, points, font):
    """以同一 N 比三种支路，补偿通带单独放大；缺测曲线不画。"""
    style_axis(cutoff_ax, "a  三种滤波器的截止与邻近阻带", r"$f/F_s$",
               "增益 / dB", font, 64)
    style_axis(pass_ax, "b  SINC5 + 补偿通带放大", r"$f/F_s$",
               "增益 / dB", font, 70)
    cutoff_ax.axhline(OFFICIAL_CUTOFF_DB, color=MUTED, linestyle="--", lw=.7)
    cutoff_ax.text(.985, OFFICIAL_CUTOFF_DB + .6, "−3.000 dB",
                   transform=cutoff_ax.get_yaxis_transform(), ha="right", va="bottom",
                   color=MUTED, fontsize=7.4)
    cutoff_ax.set(xlim=(-.003, .30), ylim=(-55, 2.7))
    pass_ax.axhspan(-PASSBAND_LIMIT_DB, PASSBAND_LIMIT_DB, color="#e4f1ec", zorder=0)
    pass_ax.set_xlim(-.001, PASSBAND_VIEW_MAX)
    relevant_gain = []
    absent = []
    for key, cutoff in table.items():
        mode, n, _ = key
        if n != REPRESENTATIVE_N:
            continue
        xy = curves.get(key, [])
        color = MODES[mode][1]
        draw_curve(cutoff_ax, xy, color)
        cutoff_ax.plot(cutoff, OFFICIAL_CUTOFF_DB, marker="s", ms=5,
                       markerfacecolor="none", markeredgecolor=color, zorder=4)
        if not any(isfinite(y) for _, y in xy):
            absent.append(MODES[mode][0])
        if mode == "sinc5_comp":
            draw_curve(pass_ax, xy, color)
            relevant_gain.extend(y for x, y in xy
                                 if 0 <= x <= PASSBAND_VIEW_MAX and isfinite(y))
    if absent:
        cutoff_ax.text(.03, .08, "未测 " + "／".join(absent), transform=cutoff_ax.transAxes,
                       fontproperties=font, fontsize=8.5, color=MUTED)
    if "SINC5 + 补偿" in absent:
        pass_ax.text(.05, .09, "补偿支路 RTL 未测", transform=pass_ax.transAxes,
                     fontproperties=font, fontsize=8.5, color=MUTED)
    for row in points:
        figure_point(cutoff_ax if row["source_id"] == "Fig. 105" else pass_ax, row)
    # 保证有偏差的补偿样点也在可见范围内，不能用固定窄窗掩住差异。
    pass_ax.set_ylim(min([-.58, *(v - .042 for v in relevant_gain)]),
                     max([.16, *(v + .031 for v in relevant_gain)]))


def all_cutoffs(ax, measured, table, font):
    """全合法档的截止用 R 做横轴，纵轴乘 R 后可比较大小倍率。"""
    style_axis(ax, "c  合法档的 −3 dB 带宽", "总抽取倍率 R",
               r"$f_{-3\,\mathrm{dB}}/F_{\mathrm{out}}$", font, 65)
    ax.set_xscale("log", base=2)
    available = sorted({r for _, _, r in table})
    ax.xaxis.set_major_locator(FixedLocator(available))
    ax.xaxis.set_major_formatter(ScalarFormatter())
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.2f}"))
    for mode, (name, colour) in MODES.items():
        keys = sorted((key for key in table if key[0] == mode), key=lambda key: key[2])
        r = [key[2] for key in keys]
        observed = [key[2] * measured.get(key, float("nan")) for key in keys]
        targets = [key[2] * table[key] for key in keys]
        # 未测档的 NaN 断开实线；方形空标单独保留表 28 的已知印刷值。
        ax.plot(r, observed, "o-", lw=1.6, ms=3.4, color=colour, label=name)
        ax.plot(r, targets, "s", ms=6, markerfacecolor="none",
                markeredgecolor=colour, zorder=4)
    ax.set_xlim(1.72, max(available)*1.14)
    font9 = font.copy()
    font9.set_size(8.7)
    ax.legend(loc="center right", ncol=3, prop=font9, frameon=False)


# =============================================================================
# 3. 复现单图及证据摘要
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="使用真实 RTL 及手册 CSV 绘制数字滤波频响")
    parser.add_argument("response", type=Path, help="真实 RTL 逐频 filter_response.csv")
    parser.add_argument("summary", type=Path, help="真实 RTL 截止 filter_summary.csv")
    parser.add_argument("manual", type=Path, help="带原件表格与图读标志的 manual_filter_targets.csv")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出的 SVG")
    parser.add_argument("--font", type=Path, help="用于中文的 TTF/TTC/OTF 路径")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".svg":
        parser.error("输出格式须为 SVG")

    response_rows, response_sha = read_csv(args.response)
    summary_rows, summary_sha = read_csv(args.summary)
    manual_rows, manual_sha = read_csv(args.manual)
    table, codes, figures = read_manual(manual_rows)
    curves, measured = read_rtl(response_rows, summary_rows, codes)
    font = setup_fonts(args.font)
    plt.rcParams["svg.hashsalt"] = "ad4080-filter-response-v1"

    fig = plt.figure(figsize=(12.8, 9.3))
    grid = fig.add_gridspec(2, 2, left=.10, right=.965, top=.80, bottom=.175,
                           hspace=.66, wspace=.27, height_ratios=(1, .78))
    response_panels(fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1]),
                    curves, table, figures, font)
    all_cutoffs(fig.add_subplot(grid[1, :]), measured, table, font)
    fig.text(.053, .954, "AD4080 数字滤波频响", fontproperties=font, fontsize=18.5,
             color=INK, va="top")
    fig.text(.053, .905, "Verilator 正弦扫频与 Rev. B 对照   ·   a/b 的 SINC 级 N=2，R=2 / 2 / 4",
             fontproperties=font, fontsize=9.7, color=MUTED)
    # 图例只说明证据类型；颜色用于区分三个滤波支路，独立的图读颜色固定。
    source_items = [
        Line2D([], [], color=INK, marker=".", label="RTL 取样与连线"),
        Line2D([], [], color=INK, ls="", marker="s", mfc="none", label="手册表 28"),
        Line2D([], [], color=FIGURE_COLOR, ls="", marker="D", mfc="white",
               label="手册图 105／106 读数及包络"),
    ]
    key_font = font.copy()
    key_font.set_size(9)
    fig.legend(handles=source_items, loc="upper left", bbox_to_anchor=(.046, .884),
               ncol=3, prop=key_font, frameon=False, handlelength=2.4)
    fig.text(.053, .105, "Fs 为 ADC 采样率，Fout = Fs/R。绿色带标出 ±0.1 dB 刻度。"
             "读图包络反映图线宽度，表格为原件印刷值。",
             fontproperties=font, fontsize=8.8, color=MUTED)
    fig.text(.053, .072, f"RTL 截止 {len(measured)}/{len(table)} 个合法档。"
             "无有限 dB 的采点留空。硅片与 PVT 未测。",
             fontproperties=font, fontsize=8.8, color=MUTED)
    fig.text(.053, .043, f"SHA-256  频点 {response_sha[:14]}  ·  摘要 {summary_sha[:14]}"
             f"  ·  原件取值 {manual_sha[:14]}",
             fontproperties=font, fontsize=7.8, color=MUTED)
    ref = (f"{args.response.name} SHA-256 {response_sha}; "
           f"{args.summary.name} SHA-256 {summary_sha}; "
           f"{args.manual.name} SHA-256 {manual_sha}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, format="svg",
                metadata={"Date": None, "Title": "AD4080 数字滤波频响", "Description": ref})
    plt.close(fig)
    finite_samples = sum(isfinite(y) for xy in curves.values() for _, y in xy)
    print(f"{args.output}: 真实截止 {len(measured)}/{len(table)}，有限增益样点 {finite_samples}")


if __name__ == "__main__":
    main()
