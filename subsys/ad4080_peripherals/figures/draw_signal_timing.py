#!/usr/bin/env python3
# =============================================================================
# 文件名     : draw_signal_timing.py
# 模块名     : AD4080 产品逐沿观测图
# 版本       : v1.0
# 功能       : 读取产品台架 PIN_EDGE 实测行，绘制图 99 和可选图 83／89 对照。
#
# 设计说明   :
#   * 横轴只使用实测记录的 ps 时戳，换算 ns 后以该面板首个记录为原点。
#     台架给的是采样点及接受事件，图中不构造未观测的低电平、跳变或脉宽。
#   * 各条 y 轨仅用 observed；expected 保留给检查器，手册对照独立写在图下注释。
#     无观测或显式未绑定的轨保留名称和“未测”。实际 FAIL 样点仍显示，用边框区分。
#   * SRAM 写标记来自已经受理的写事件，地址来自同一沿的实测列。
#     图 89 的逻辑位对与 FIFO 状态不代表 ODDR 的物理发射沿。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import csv
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties, findfont
from matplotlib.ticker import MaxNLocator


INK = "#183247"
MUTED = "#536877"
GRID = "#dce4eb"
BLUE = "#1a5d91"
TEAL = "#117362"
ORANGE = "#a55214"
RED = "#ae3239"
UNOBSERVED = {"", "-", "NA", "N/A", "UNBOUND", "UNKNOWN", "X", "Z"}
MAC_CJK_FONT = Path("/System/Library/Fonts/STHeiti Light.ttc")
MANUAL = "https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf"
FIG89_FRAME_SIGNALS = {"FIRST_WORD_LOAD", "LAST_WORD_LOAD", "BEAT_LAST",
                       "BEAT_VALID", "FIFO_FULL", "FIFO_READ_DONE"}


@dataclass(frozen=True)
class EdgeSample:
    case: str
    domain: str
    edge: int
    time_ps: int
    signal: str
    observed: str
    verdict: str


# =============================================================================
# 1. 从台架记录冻结数据边界
# =============================================================================
def load_edges(path):
    """返回按原始时戳排序的真实样点和输入摘要。重复冲突或坏字段会报错。"""
    raw = path.read_bytes()
    entries = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.lstrip().startswith("PIN_EDGE,"):
            continue
        values = next(csv.reader([line.lstrip()]))
        if len(values) != 9:
            raise ValueError(f"PIN_EDGE 应含 9 列：{line}")
        _, case, domain, index, time_ps, signal, observed, _, verdict = (
            value.strip() for value in values
        )
        if case.lower() == "case":
            continue
        edge = EdgeSample(
            case.upper(), domain.upper(), int(index), int(time_ps),
            signal.upper(), observed, verdict.upper(),
        )
        key = (edge.case, edge.domain, edge.edge, edge.time_ps, edge.signal)
        # 同一记录反复打印可以归并；同一采样点的冲突值则不能选其一画出。
        if key in entries and entries[key] != edge:
            raise ValueError(f"同一引脚记录有冲突：{key}")
        entries[key] = edge
    if not entries:
        raise ValueError("输入未找到实测 PIN_EDGE 行")
    return sorted(entries.values(), key=lambda r: (r.time_ps, r.edge)), sha256(raw).hexdigest()


def samples_for(rows, signal):
    """返回该信号的已观测行。NA 和未绑定记录只用于标记缺测。"""
    # 将缺测和逻辑 0 分开。前者没有坐标，后者必须作为一个可追溯样点保留。
    return [r for r in rows if r.signal == signal and r.observed.upper() not in UNOBSERVED]


def binary_value(sample):
    """从已观测样点获取逻辑 0／1；异常数值归为数据错误。"""
    value = int(sample.observed, 16 if sample.observed.lower().startswith("0x") else 10)
    if value not in (0, 1):
        raise ValueError(f"{sample.case}/{sample.signal} 不是逻辑 0/1：{sample.observed}")
    return value


def relative_ns(time_ps, origin_ps):
    """从台架同一全局时基的两个 ps 整数计算相对 ns，避免绝对浮点丢低位。"""
    return (time_ps - origin_ps) / 1000


def setup_fonts(font_path):
    """使用现有 CJK 字体，字形轮廓随 SVG 保存，便于打印或无字体预览。"""
    # 支持使用方显式提供字体。避免默认字体没有中文时静默生成方框。
    if font_path is None and MAC_CJK_FONT.is_file():
        font_path = MAC_CJK_FONT
    if font_path is None:
        for family in ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "SimHei"):
            try:
                font_path = Path(findfont(family, fallback_to_default=False))
                break
            except ValueError:
                continue
    if font_path is None:
        raise ValueError("未找到 CJK 字体，用 --font 指定本机字体文件")
    prop = FontProperties(fname=str(font_path.resolve(strict=True)))
    # 省略可变日期，固定 SVG 标识符用盐；同一机读输入可重生成相同图。
    matplotlib.rcParams.update({
        "svg.fonttype": "path",
        "svg.hashsalt": "ad4080-pin-edge-timing-v1",
        "axes.unicode_minus": False,
        "font.size": 9.2,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    })
    return prop


# =============================================================================
# 2. 用采样点和接受事件排信号轨
# =============================================================================
def prepare_panel(ax, labels, times, font):
    """布置多个 0/1 信号轨。纵向间隔是排版间距，不代表电压。"""
    span = max(times) - min(times)
    pad = max(span * .035, .08)
    ax.set_xlim(min(times) - pad, max(times) + pad)
    ax.set_ylim(-.38, len(labels) - .32)
    bases = list(range(len(labels) - 1, -1, -1))
    for y in bases:
        ax.axhline(y, color=GRID, lw=.7, zorder=0)
        ax.axhline(y + .35, color=GRID, lw=.6, linestyle=":", zorder=0)
    ax.set_yticks([y + .16 for y in bases], labels, fontproperties=font, color=INK)
    ax.tick_params(axis="y", length=0, pad=9)
    ax.tick_params(axis="x", color=GRID, labelcolor=MUTED)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    return bases


def no_measurement(ax, y, font, words="未测"):
    """将文字置于空轨中，避免缺测被视觉误认成逻辑低。"""
    ax.text(.50, y + .15, words, transform=ax.get_yaxis_transform(),
            fontproperties=font, color=MUTED, ha="center", va="center")


def plot_samples(ax, rows, signal, origin, y, font, color=BLUE):
    """记录点只在相应观测时刻显示；返回实测高的事件，供后续关联。"""
    observed = samples_for(rows, signal)
    if not observed:
        no_measurement(ax, y, font, "未测／未绑定")
        return []
    highs, horizontal, vertical, faces, edges = [], [], [], [], []
    # GPIO 及单口值来自离散事件快照，不能从两个样点补出中间波形。
    # 对不一致的样点仍使用实际值，仅替换标记样式。
    for sample in observed:
        bit = binary_value(sample)
        if bit:
            highs.append(sample)
        mismatch = "FAIL" in sample.verdict or "MISMATCH" in sample.verdict
        horizontal.append(relative_ns(sample.time_ps, origin))
        vertical.append(y + .35 * bit)
        faces.append("white" if mismatch else color)
        edges.append(RED if mismatch else color)
    ax.scatter(horizontal, vertical, s=21, zorder=4,
               facecolors=faces, edgecolors=edges, linewidths=1.0)
    return highs


def fig99(ax, rows, font):
    """图 99 主面板。按实测 CNV 编号布局，写标签只跟随 SRAM 受理时刻。"""
    cnv = [row for row in rows if row.domain == "CNV"]
    if not cnv:
        raise ValueError("FIG99 缺少 CNV 每沿实测行")

    ticks_ps = {}
    # 同沿的第一条记录用于画参考竖线。各轨若来自稍后的采样，仍使用它
    # 自己的实测时戳，不能把域内信号移到手册预期的沿位。
    for row in cnv:
        ticks_ps.setdefault(row.edge, row.time_ps)
    origin = min(ticks_ps.values())
    ticks = [(index, relative_ns(time, origin)) for index, time in ticks_ps.items()]
    bases = prepare_panel(
        ax,
        ["CNV↑ 记录", "FILTER_SYNC_n", "FILTER_RESULT_RDY_n", "SRAM 写受理", "FIFO_FULL"],
        [relative_ns(r.time_ps, origin) for r in cnv], font,
    )
    for index, x in ticks:
        ax.axvline(x, color=GRID, lw=.65, linestyle=":", zorder=0)
        ax.scatter(x, bases[0] + .09, marker="^", color=INK, s=30, zorder=5)
        ax.text(x, bases[0] + .33, f"E{index}", ha="center", va="bottom", color=INK, fontsize=8.6)

    plot_samples(ax, cnv, "FILTER_SYNC_N", origin, bases[1], font, ORANGE)
    plot_samples(ax, cnv, "FILTER_RESULT_RDY_N", origin, bases[2], font, TEAL)
    writes = plot_samples(ax, cnv, "FIFO_WRITE", origin, bases[3], font, BLUE)
    # 写入地址与码值不能从 W0／W1 或激励公式反推。只有同一沿同一时刻
    # 的观测才可并列，部分缺测时也保留已接受写入的样点。
    addresses = {(r.edge, r.time_ps): r.observed for r in samples_for(cnv, "FIFO_WRITE_ADDR")}
    data = {(r.edge, r.time_ps): r.observed for r in samples_for(cnv, "FIFO_WRITE_DATA")}
    for number, row in enumerate(writes):
        key = (row.edge, row.time_ps)
        address = addresses.get(key, "地址未测")
        label = f"W{number} [{address}]"
        if key in data:
            label += f" · {data[key]}"
        ax.annotate(label, (relative_ns(row.time_ps, origin), bases[3] + .35),
                    xytext=(0, 7), textcoords="offset points", ha="center",
                    size=8.2, color=INK, fontproperties=font)
    plot_samples(ax, cnv, "FIFO_FULL", origin, bases[4], font, TEAL)
    ax.set_xticks([x for _, x in ticks], [f"{x:g}" for _, x in ticks])
    ax.set_xlabel("相对首个 CNV 记录的时间 / ns", fontproperties=font, color=MUTED)
    title = ax.set_title("图 99 对照｜CNV 沿后取样及单口写受理", loc="left",
                         fontproperties=font, color=INK, pad=12, fontsize=11.8)
    title.set_url(MANUAL + "#page=68")


def fig89(ax, rows, font):
    """图 89 辅面板。绘制记录内的逻辑位对、装载和 FIFO 状态。"""
    origin = min(row.time_ps for row in rows)
    # 本案例的时基来自 IO；把它与图 99 的 E1 横轴关联会混淆两次激励。
    bases = prepare_panel(
        ax,
        ["装载与记录末槽", "BEAT_VALID", "FIFO_FULL", "FIFO_READ_DONE"],
        [relative_ns(r.time_ps, origin) for r in rows], font,
    )
    found_event = 0
    for signal, label, color in (
        ("FIRST_WORD_LOAD", "首字装载", ORANGE),
        ("LAST_WORD_LOAD", "末字装载", BLUE),
        ("BEAT_LAST", "尾槽交付", INK),
    ):
        for row in samples_for(rows, signal):
            if not binary_value(row):
                continue
            found_event += 1
            x = relative_ns(row.time_ps, origin)
            ax.vlines(x, bases[0], bases[0] + .30, color=color, lw=1.5)
            ax.text(x, bases[0] + .34, label, ha="center", va="bottom",
                    fontproperties=font, color=color, fontsize=8.3)
    if not found_event:
        no_measurement(ax, bases[0], font, "未观测到装载或尾槽事件")
    valid = plot_samples(ax, rows, "BEAT_VALID", origin, bases[1], font)
    # 计数是 BEAT_VALID 实测为高的拍数。头尾与 DCO 的物理关系另行证实。
    plot_samples(ax, rows, "FIFO_FULL", origin, bases[2], font, TEAL)
    plot_samples(ax, rows, "FIFO_READ_DONE", origin, bases[3], font, TEAL)
    ax.xaxis.set_major_locator(MaxNLocator(8))
    ax.set_xlabel("相对首个 IO 记录的时间 / ns", fontproperties=font, color=MUTED)
    title = ax.set_title(f"图 89 对照｜IO 逻辑事件 · 有效位对样点 {len(valid)}",
                         loc="left", fontproperties=font, color=INK, pad=12, fontsize=11.8)
    title.set_url(MANUAL + "#page=60")


def fig83(ax, rows, font):
    """图 83 备选面板。GPIO 采样只看 CNV，SPI 读回保持独立观察口径。"""
    origin = min(row.time_ps for row in rows)
    # HI_STATUS 是本台架观测的保持位，与手册图所示的阈值判决层级不同。
    # 输入条件也有独立采样，能同时读出越限和恢复之间的数字延迟。
    signals = ("INPUT_ABOVE", "ALERT_N", "HI_STATUS")
    bases = prepare_panel(ax, signals, [relative_ns(r.time_ps, origin) for r in rows], font)
    for y, name in zip(bases, signals):
        plot_samples(ax, rows, name, origin, y, font, TEAL)
    ax.xaxis.set_major_locator(MaxNLocator(8))
    ax.set_xlabel("相对首个 CNV 记录的时间 / ns", fontproperties=font, color=MUTED)
    title = ax.set_title("图 83 对照｜CNV 沿后 GPIO 与保持状态", loc="left",
                         fontproperties=font, color=INK, pad=10, fontsize=11.8)
    title.set_url(MANUAL + "#page=55")


# =============================================================================
# 3. 图面组合与可复现导出
# =============================================================================
def main():
    cli = argparse.ArgumentParser(description="从真实产品台架 PIN_EDGE 记录绘制 SRAM 与 GPIO 逐沿观测图")
    cli.add_argument("log", type=Path, help="含 PIN_EDGE 行的真实日志或同格式 CSV")
    cli.add_argument("-o", "--output", type=Path, required=True, help="输出 SVG 文件")
    cli.add_argument("--font", type=Path, help="显示中文使用的 TTF、TTC 或 OTF")
    cli.add_argument("--secondary", choices=("auto", "fig83", "fig89", "none"), default="auto")
    cli.add_argument("--conditions", default="产品 RTL 台架，数字信号逐沿采样",
                     help="记录已确认的实测条件，显示在图标题下")
    args = cli.parse_args()
    observed, input_sha = load_edges(args.log)
    font = setup_fonts(args.font)
    required = [row for row in observed if row.case == "FIG99"]
    if not required:
        raise ValueError("没有 FIG99 的实测行，无法形成主面板")

    # FIG89 的停钟后恢复断言也可能使用 IO 域。只有组帧监视器给出的
    # 信号属于本面板，物理或恢复事项不参与横轴范围。
    second_rows = {
        "FIG89": [row for row in observed if row.case == "FIG89" and row.domain == "IO"
                  and row.signal in FIG89_FRAME_SIGNALS],
        "FIG83": [row for row in observed if row.case == "FIG83" and row.domain == "CNV"],
    }
    selection = args.secondary.upper()
    # 自动面板只选择已记录的用例。显式请求一个缺测用例时在图下注明，
    # 保持主图可读，避免生成一个看似逻辑低的虚构副面板。
    if selection == "AUTO":
        selection = next((case for case, rows in second_rows.items() if rows), "NONE")
    second = second_rows.get(selection, [])
    h = 8.65 if second else 5.8
    figure = plt.figure(figsize=(12.6, h))
    layout = figure.add_gridspec(
        2 if second else 1, 1, left=.23, right=.975,
        bottom=.16 if second else .20, top=.85 if second else .79, hspace=.52,
        height_ratios=(5.0, 3.6) if second else None,
    )
    fig99(figure.add_subplot(layout[0, 0]), required, font)
    if second:
        chart = fig89 if selection == "FIG89" else fig83
        chart(figure.add_subplot(layout[1, 0]), second, font)

    # 实测与手册标注分别陈列。横轴只从 PIN_EDGE 计算，手册的 E 数量
    # 只出现在此条说明，避免把图中预期当作实测点。
    figure.text(.04, .956, "AD4080 数字外围｜RTL 台架逐沿观测", fontproperties=font,
                fontsize=18, color=INK, va="top")
    figure.text(.04, .910, args.conditions, fontproperties=font, fontsize=9.5, color=MUTED)
    note = "各点为记录值，每轨下／上对应 0／1。Wn [地址] · 有符号码。空心红圈为不一致样点。"
    figure.text(.04, .099 if second else .111, note,
                fontproperties=font, fontsize=8.0, color=MUTED)
    reference = "手册 p.68 图 99 的 RDY_n 在 E5/7/9 为低，写入 E6/8/10，FULL 在 E10 置位。"
    figure.text(.04, .071 if second else .074, reference,
                fontproperties=font, fontsize=8.0, color=MUTED).set_url(MANUAL + "#page=68")
    boundary = "实物接收、ODDR、DCO 与 CNV 电气相位未测。"
    if selection != "NONE" and not second:
        boundary += f" {selection} 未测。"
    figure.text(.04, .042 if second else .037,
                f"{boundary} 记录 {args.log.name} · SHA-256 {input_sha[:16]}",
                fontproperties=font, fontsize=7.6, color=MUTED)
    if args.output.suffix.lower() != ".svg":
        cli.error("输出文件名须以 .svg 结尾")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 全量摘要保存在 SVG 元数据，版面仅用短摘要确保印刷尺寸的可读性。
    metadata = {
        "Date": None,
        "Title": "AD4080 数字外围 RTL 台架逐沿观测",
        "Description": f"PIN_EDGE {args.log.name} SHA-256 {input_sha}",
    }
    figure.savefig(args.output, format="svg", metadata=metadata)
    plt.close(figure)
    print(f"{args.output}: FIG99 记录 {len(required)}，副面板 {selection} 记录 {len(second)}")


if __name__ == "__main__":
    main()
