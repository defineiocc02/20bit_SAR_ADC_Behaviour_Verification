# Academic Figure Skill Asset Confirmation (verified against assets/figures/)
# (a) paired integer errors → PairedBoxScatter → param inherit (exact multiplicities)
# (b) switch occupancy → heatmap → param inherit (fixed physical rows and columns)
# (c) timing and full-code envelopes → LineTrend → param inherit (observed edges and extrema)
# =============================================================================
# 文件名     : adc_figure_plates.py
# 模块名     : 数字 IP 论文插图
# 功能       : 将归档的引脚输出、叶子掩码和静态码宽分别绘成可溯源的矢量图。
# 设计说明   : 重复点按精确次数汇总；时钟沿和被拒事件保留原值。图例的颜色
#              同时配以形状、线型或直接标签，灰度打印也可读。SVG 保留文字。
# =============================================================================
"""由 verify_v21_algorithms.py 与 run_static_linearity.py 的制图入口调用。"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from hashlib import sha256

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np

INK, BLUE, TEAL, RUST = "#142F40", "#2166AC", "#006E68", "#A74529"
MUTED, RULE, PALE, WARM = "#485F6B", "#BAC8CD", "#E6F3F1", "#F9E8DB"
# 正文以双栏插图的实物宽度排，不再对成图整体裁边缩放。
# 字号以 point 写入矢量文件，几何尺寸与 300 dpi 预览的像素数互相独立。
PAPER_WIDTH = 183 / 25.4
# 此 Git 提交标识生成 CSV 的顶层与宏驱动。出图脚本可以在它之后修改。
# 源清单和驱动的逐文件指纹见同目录的原始 result.json 副本；CSV 保留 GitHub 的换行字节。
# 需要追一次实测时，在该 run 的 digital-component-evidence 工件里查 v21-top。
CI_RUN = 44508573316
# GitHub Actions 的 head SHA 对应被测 RTL。正式发布标签和 Python 包号都不能定位这批开关数据。
# 部署或再版时只需读取已检入的 CSV 与凭据；不联网是为了让论文原图也能在隔离机复算。
CI_COMMIT = "3057d2fe8d869ef3ce7d10981d4ddaf9ecc4e362"
TRACE_HASH = "e4fc3a307db26979c2cb5c955cd5d703ebd85b7dced76a2827675ef8d0ee7189"
FACES = {item.name for item in font_manager.fontManager.ttflist}
# 优先使用可内嵌常用中文轮廓的字体。无中文字体的检出使用同一数据的英文图注。
CJK = next(
    (
        name
        for name in (
            "Heiti SC",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "WenQuanYi Zen Hei",
            "Arial Unicode MS",
        )
        if name in FACES
    ),
    None,
)
# 取英文数字字体和中文字体组成回退链，避免单位、数字、汉字的负号渲染不一致。
# 生成 SVG 时保留可搜索文字；PDF 直接嵌入字形，所以论文排版机无需重装本机字体。
# 散点、箭头、每个掩码格均用矢量绘制。图中局部色块的面积不表示出现次数。
# 相同文件名的出版图禁用自动 tight 裁边，否则比较图粘入论文时横轴不再等宽。
STYLE = {
    "font.family": (["Arial" if "Arial" in FACES else "DejaVu Sans"] + ([CJK] if CJK else [])),
    "font.size": 8,
    "axes.labelsize": 8,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.linewidth": 0.6,
    "axes.edgecolor": RULE,
    "axes.labelcolor": INK,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.size": 3,
    "ytick.major.size": 3,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
    "svg.hashsalt": "adc-evidence",
}


# 两种语言仅替换标注。分类、采样范围和聚合方法相同，因而缺字机器上的对照仍有相同物理意义。
def t(zh, en):
    return zh if CJK else en


def sheet(title, subtitle, height=3.75):
    """固定印刷宽度并预留标题与来源行。数据轴不因标题长度或极值而缩放。"""
    # 标题描述可检验的结论，副标题交代来源与受控工况。印成黑白仍能独立读图。
    # 上部留白在所有主图上固定，正文组版时可以压在同一条标题和数据基线上。
    fig = plt.figure(figsize=(PAPER_WIDTH, height), facecolor="white")
    fig.text(0.065, 0.984, title, va="top", fontsize=15, fontweight="bold")
    fig.text(0.066, 0.827, subtitle, va="bottom", fontsize=8.3, color=MUTED)
    fig.add_artist(
        Line2D([0.065, 0.965], [0.807, 0.807], transform=fig.transFigure, lw=0.7, color=RULE)
    )
    return fig


# 此行解释图里一个容易误读的条件，如状态覆盖、单位或饱和。它与原文定位和短作用句分别排印。
def note(fig, text):
    fig.text(0.066, 0.041, text, fontsize=7.3, color=MUTED, va="bottom")


# 原文条目随独立 SVG、PDF、PNG 一起印出；把图片离开图册发给读者仍能追到原图。
# 每条指出公开机制与此图所用工程条件。专利的七片实施例不能自动成为八槽模型的参数表。
# 原文定位按印刷图号或作者 PDF 实际页号，避免网页锚点变化后失去出处。
# 作用句仅解释技术和因果关系，图号、样本数、注入条件另列。所有可见汉字、
# 拉丁字母、标点和空格都算一字；同一规则用于 PDF 中的算法图及 HTML 图注。
PURPOSE = {
    'dither-transfer': ('分路注入扰动，再按同笔权重扣回。', 'Split the dither and cancel it with the sampled weights.'),
    'integer-arithmetic': ('按实际电容权重修正失配，改善线性度。', 'Correct mismatch using the physical cell weights.'),
    'physical-masks': ('轮换等权电容，减轻失配造成的非线性。', 'Rotate equal-weight cells to spread mismatch.'),
    'sar-events': ('只写已决位，防止试探扰动余差。', 'Write resolved decisions and suppress rejected trials.'),
    'capture-handover': ('交错采样并按标签配对，保持连续出码。', 'Interleave samples and match returning codes by tag.'),
    'slice-allocation': ('轮换采集与转换片组，分摊器件使用。', 'Rotate slice roles to distribute usage.'),
    'linearity': ('按物理权重补偿失配，改善INL与DNL。', 'Correct cell mismatch to improve static linearity.'),
    'effects': ('分开切换扰动与校正，识别各自的线性度收益。', 'Isolate switching and weight effects within each condition.'),
}

PRINT_SOURCE = {
    "dither-transfer": ("讲稿 p.17 · US10505561B2 图 19：七片 f/7、c−U。本图采用八槽 f/8、c+U。", "ISSCC slides p.17; US10505561B2 Fig.19: seven slices, c−U. Here: eight slots, c+U."),
    "integer-arithmetic": ("ISSCC 2024 p.182：片外求系数、片上修正 DAC 权重。±32 码来自工程注入。", "ISSCC 2024 p.182: externally derived coefficients, on-chip DAC correction. ±32 codes: injected test."),
    "physical-masks": ("ISSCC 图 9.8.3 下部、讲稿 p.29 · US10511316B2 图 19–20。遍历相位和命令 283 为测试输入。", "ISSCC Fig.9.8.3 (bottom), slides p.29; US10511316B2 Figs.19–20. Command and phases are test inputs."),
    "sar-events": ("作者讲稿 p.9：RDAC 跟随 SADC。512 试探、拒绝沿和 bit5 回退是工程规则。", "ISSCC slides p.9: RDAC follows SADC. Trial 512, write suppression and retry at bit 5 are RTL choices."),
    "capture-handover": ("ISSCC 图 9.8.1–2 · US10516408B2 图 16。图中 tag、640 MHz 时钟与返回沿取自工程仿真。", "ISSCC Figs.9.8.1–2; US10516408B2 Fig.16. Tags, 640 MHz clock and return edges are RTL observations."),
    "slice-allocation": ("ISSCC 图 9.8.1、讲稿 p.10 · US10516408B2 图 16。随机递推和冻结向量由工程确定。", "ISSCC Fig.9.8.1, slides p.10; US10516408B2 Fig.16. Sequence and vectors are engineering choices."),
    "linearity": ("论文图 9.8.4 与讲稿 p.32 是样片曲线。此图用工程模型的端点 LSB20。", "ISSCC Fig.9.8.4 and slides p.32 are silicon data. These curves use endpoint LSB20 from the model."),
    "effects": ("ISSCC p.182 与图 9.8.4 披露片上校正和样片线性度。四组消融及其峰值来自工程模型。", "ISSCC p.182 and Fig.9.8.4 describe on-chip correction and silicon linearity. Ablations are model results.")
}


def export(fig, out, name):
    """相同数据与字体环境下可逐字节复画。PDF 内嵌字体，PNG 为 300 dpi。"""
    # 先保存 SVG 和 PNG，再写 PDF。版式在三个后端共享，坐标不依赖打印机 DPI。
    # 时间戳留空才能发现实际数据、代码或字体引起的变化，复算才有逐字比较的意义。
    # 线性度文件名带实验失配量，其他图名稳定。多个失配强度复用同一句机制说明，不另外定义算法。
    # 原文定位属于来源证据，不可与 CI 的 tag、开关值、样本数合并成同一类出处。
    # 这使得单张 PNG 离开总图册后仍带有论文图号，而 SVG 文件同时提供可编辑文字。
    kind = name.rsplit("-", 1)[0] if name.startswith(("effects-", "linearity-")) else name
    if len(PURPOSE[kind][0]) > 30:
        raise ValueError(f"figure purpose exceeds 30 characters: {kind}")
    fig.text(.066, .89, t("作用　", "Purpose  ") + t(*PURPOSE[kind]), fontsize=8.8, color=TEAL, va="center")
    fig.text(.066, .009, t(*PRINT_SOURCE[kind]), fontsize=7.05, color=TEAL, va="bottom")
    fig.savefig(out / (name + ".svg"), metadata={"Date": None})
    fig.savefig(out / (name + ".png"), dpi=300, facecolor="white")
    fig.savefig(
        out / (name + ".pdf"),
        metadata={"Creator": "SAR ADC digital IP", "CreationDate": None, "ModDate": None},
    )
    plt.close(fig)


# =============================================================================
# 1. 读取真实退休记录。最小对照单位是同一输入、物理片号与开关状态下的一笔 tag。
# =============================================================================
def rtl_evidence(path):
    """使用完整 CI CSV，校验配对键并保留每个扰动字和全部三个事件分支。"""
    # 取材时保留 samples.csv 的全部列和全部退休行，避免重新序列化改变哈希。
    # 没有退休的测试场景不会在这个表里产生数据。关于错过捕获的结论另查 CI summary.csv。
    # 此处的输入证据属于理想宏驱动的 Verilator 顶层。模拟电压和真实建立时间需另测。
    if sha256(path.read_bytes()).hexdigest() != TRACE_HASH:
        raise ValueError("sample_cadence.csv differs from the archived GitHub RTL output")
    # result.json 随 GitHub 原工件保存。只把记录归给当时的 filelist、顶层与 C++ 驱动。
    # 后续逻辑即使仍产生相同统计，也要有同源码的观测才能更新此结果的身份。
    receipt = json.loads(path.with_name("sample_cadence_evidence.json").read_text())
    if receipt["payload_sha256"]["samples.csv"] != TRACE_HASH or receipt["status"] != "PASS":
        raise ValueError("trace does not match the original passing CI receipt")
    root = path.parents[2]
    for name, expected in receipt["sources_sha256"].items():
        if sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"DUT or driver differs from the archived run: {name}")
    # tag 在每个测试场景独立编号，不同场景间相同整数只在明示的配对试验中代表同个输入。
    # 保持整数解码；把 40/160 位打包字段通过浮点中转会悄悄丢掉低位物理片号或开关。
    # CSV 的 code 来自 DUT 的 out_code 引脚，input_code 是针对固定输入给出的参考网格点。
    groups = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as stream:
        for record in csv.DictReader(stream):
            name = record.pop("case")
            row = {key: int(value) for key, value in record.items()}
            if row["tag"] in groups[name]:
                raise ValueError(f"duplicate tag in {name}")
            groups[name][row["tag"]] = row

    # 相同输出码恰好重叠时保存频数和每字极值。扫描与断线对照必须有同一套输入与字序列。
    # 片内序列打开生产字源，由受理沿推进；与强制字实验的数据分母各自计算。
    # 固定输入下检查 64 个字的重构透明性，反映的是扣回对不对，而不是输出谱或噪声。
    internal = list(groups["dither_internal"].values())
    sweep, broken = groups["dither_sweep"], groups["dither_sadc_disconnected"]
    nominal, known = groups["dither_nominal_table"], groups["dither_corrected_table"]
    if sweep.keys() != broken.keys() or nominal.keys() != known.keys():
        raise ValueError("control tags do not match")
    # 负对照保留相同相位、轮换方向及字序列。断线后的细码和错误标志预期会变，不能当作配对键。
    # 每个场景中的 tag 已验过唯一性。即使 CSV 写入顺序改变，按 tag 对应也不会错误合并样本。
    for tag, a in sweep.items():
        if any(
            a[k] != broken[tag][k]
            for k in ("word", "input_code", "input_quarters", "h", "v", "half", "coin")
        ):
            raise ValueError(f"input changed in disconnected control, tag {tag}")
    # 装表对照要先匹配 ADC2 原码、器件片号和实际掩码，然后比较两次退休的 20 位码。
    # 仅按 word 或仅按 k 配对可能遇到同一码的不同片组，会把另一颗电容的失配记在本笔。
    # 输入四分之一码的余量也参与比较，以免两次激励处在不同的舍入边界。
    # raw 来自理想宏对开关的响应。禁止由名义目标倒推 raw 再把结果算作 DUT 实测。
    pins = [
        "word",
        "raw",
        "k",
        "input_code",
        "input_quarters",
        "ids_packed",
        "pre_packed",
        *(f"post_slot{i}" for i in range(8)),
    ]
    errors = Counter()
    cell_cases = Counter()
    # 名义表和已知权重表属于两次运行。两侧必须都退休才能比较，遗漏的标签不能按零误差补入。
    # post_slot 包含桥接后的全部主、子阵列位，可发现名义命令相同而实际切换元件已经不同的情况。
    for tag, a in nominal.items():
        b = known[tag]
        if any(a[k] != b[k] for k in pins):
            raise ValueError(f"raw data or switches changed in the weight control, tag {tag}")
        errors[a["code"] - a["input_code"], b["code"] - b["input_code"]] += 1
        # 无器件偏差的强制字扫与两张表使用相同片号、开关和目标输入，可直接比较细码。
        ideal = sweep[tag]
        if any(a[k] != ideal[k] for k in pins if k != "raw"):
            raise ValueError(f"unperturbed control is not paired, tag {tag}")
        pre = a["mismatch_pre"]
        cell_cases[pre, pre + a["post_minus_pre"], a["raw"] - ideal["raw"]] += 1

    # 按六位字聚合后仍保留每个字的样本数与误差极值。同一个字可在不同片组出现多次。
    # 极值相同才画成一个点；这不是对输出码取平均后强行画成直线。
    # 码差单位为 20 位输出的一 LSB，只有使用相同输入量程时才能换算成电压。
    by_word = defaultdict(list)
    for row in internal:
        by_word[row["word"]].append(row["code"] - row["input_code"])
    words = list(range(64))
    if set(by_word) != set(words) or len({a["input_code"] for a in internal}) != 1:
        raise ValueError("fixed-input internal sequence is incomplete")
    # 断开 SADC 低段的负对照仅改变一处宏连接。越窗取自硬件实验记录的 adc2_range。
    # 该标志描述细量化器物理窗口，与最终 20 位饱和标志及协议 out_err 分别处理。
    # 被标记的样本仍然占用分母；删去它们会把错误接线伪装成表现更好的样本群。
    # 越窗后宏返回 0 原码，0 本身也能是合法原码。区分两者要保留 adc2_range 原始标志。
    # 这里不对断线波形做有效样本筛选，负对照的含义正来自这些被保留的异常。
    ranges = []
    for batch in (sweep, broken):
        selected = Counter(r["word"] for r in batch.values())
        if any(selected[word] != 3 for word in words):
            raise ValueError("expected three complete forced-word sweeps")
        flags = Counter(r["word"] for r in batch.values() if r["adc2_range"])
        ranges.append([flags[word] for word in words])
    # 从真实活动样本取得单笔标签与预置。粗码由最终命令和该字的工程上段反推。
    example = next(a for a in sweep.values() if a["word"] == 53)
    # 六位字的 D6 和 D4 分别表示四与一，对应专利图 19 的上段输入。
    # 字的十进制数不等于扣到二十位码上的数值，输出的换算还要经过本笔物理权重与增益。
    upper = 4 * ((example["word"] >> 5) & 1) + ((example["word"] >> 3) & 1)
    cadence = sorted(groups["sample25ns_16ticks"].values(), key=lambda a: a["tag"])
    # clock_fs 的单位为飞秒，除以 10⁶ 得纳秒。out、capture 和 raw_edge 存的是沿序号。
    # 只比较同一个场景里按 tag 排序的相邻退休事件。首笔流水填充不计作持续吞吐。
    # 若中间发生停顿，最小和最大间隔就会分叉，此时不能再用单个 25 ns 结论概括。
    periods = {a["clock_fs"] for a in cadence}
    gaps = {b["out"] - a["out"] for a, b in zip(cadence, cadence[1:])}
    if len(periods) != 1 or len(gaps) != 1:
        raise ValueError("cadence changed across the observed trace")
    # Flash 前缀提交也算事件。512 保护路径可能消费一次比较而不更新底板。
    # 各路径选择首次出现的 tag 展示细节；路径计数和迭代时间信息使用整组退休样本。
    # 图示行不能把两个 tag 的 Flash 前缀和 SAR 后缀拼成一条虚构的比较序列。
    # path 是测试台对刺激分支的标识，事件列则取自实际写门。一个合格的试探可能被拒绝。
    # 从结束后的占位值推断拒绝会多算事件，故必须先按本笔记录的长度截出有效事件窗口。
    example_paths, counts = {}, Counter()
    for a in cadence:
        # 行尾的 −1 是填充。仅在 event_count 内才表示比较已经发生而写门关闭。
        events = [a[f"event_k{i}"] for i in range(a["event_count"])]
        counts[a["path"]] += 1
        example_paths.setdefault(a["path"], dict(tag=a["tag"], events=events))
    if set(example_paths) != {0, 1, 2}:
        raise ValueError("missing ordinary, reject or accept path")
    # 旧 raw 返回遇到下一笔捕获时，返回槽沿前属于旧 tag，沿后可以登记新 tag。
    # 这里直接对比相邻记录中的 raw_edge 与 capture，另查所有输出间隔是否一致。
    # 两笔同沿不表示捕获和退休是一件事；out 继续携带前级锁存的标签。
    boundaries = sum(a["raw_edge"] == b["capture"] for a, b in zip(cadence, cadence[1:]))
    return dict(
        commit=CI_COMMIT,
        run=CI_RUN,
        trace_sha256=TRACE_HASH,
        reference_code=internal[0]["input_code"],
        internal_count=len(internal),
        per_word_count=[len(by_word[w]) for w in words],
        error_min=[min(by_word[w]) for w in words],
        error_max=[max(by_word[w]) for w in words],
        range_flags=ranges,
        forced_count=len(sweep),
        example={
            k: example[k]
            for k in (
                "tag",
                "word",
                "sadc_mask",
                "sadc_lower_eighths",
                "rdac_pre_eighths",
                "k",
                "raw",
                "code",
            )
        },
        coarse=example["k"] - upper,
        upper=upper,
        disconnected_example={k: broken[example["tag"]][k] for k in ("code", "raw", "adc2_range")},
        cell_cases=[[pre, post, delta, n] for (pre, post, delta), n in sorted(cell_cases.items())],
        pairs=[[x, y, n] for (x, y), n in sorted(errors.items())],
        pair_count=len(nominal),
        path_examples=[dict(path=k, count=counts[k], **example_paths[k]) for k in (0, 1, 2)],
        cadence=[
            {k: a[k] for k in ("tag", "sample", "start", "post", "capture", "raw_edge", "out")}
            for a in cadence[:3]
        ],
        clock_fs=periods.pop(),
        output_gap=gaps.pop(),
        output_pairs=len(cadence) - 1,
        coincident=boundaries,
    )


# =============================================================================
# 2. 独立对照。标题给出主结论，轴保留物理单位和比较分母，颜色以外保留形状或文字。
# =============================================================================
def draw_dither(e, out):
    # 两组面板共用 0…63 的字序号，左端和右端的单元边界也相互对齐。
    # 面板 a 的 1,024 笔来自片内字源；面板 b 的三遍扫字来自测试引脚。
    # 相同字的出现次数可能不同，归零的误差表示该字的每一笔都匹配目标。
    word = np.arange(64)
    fig = sheet(
        t(
            "扰动字在变，固定输入的输出码保持不变",
            "Dither changes while the output code stays fixed",
        ),
        t(
            "生产顶层 RTL  ·  理想模拟宏  ·  差分输入 −0.6522216796875 V",
            "Production RTL  ·  ideal analog macros  ·  fixed input −0.6522216796875 V",
        ),
        3.85,
    )
    ax = fig.add_axes([0.17, 0.515, 0.765, 0.195])
    lo, hi = np.array(e["error_min"]), np.array(e["error_max"])
    # 画出所有字的最小与最大误差，完全重合时仍保留一个标记。
    # 保留正负一个 LSB 的刻度使零线可读，不能通过极小纵轴放大浮点噪声。
    # 字 53 的引线落在实际横坐标上，以便与第一页的两路开关和细码追踪对应。
    ax.axhline(0, color=RULE, lw=0.65)
    ax.vlines(word, lo, hi, color=BLUE, lw=1)
    ax.scatter(word, lo, s=8, color=BLUE)
    ax.set(xlim=(-0.7, 63.7), ylim=(-1.05, 1.05), yticks=[-1, 0, 1], xticks=[])
    ax.set_ylabel(t("输出误差 / 码", "Output error / code"))
    ax.spines["bottom"].set_visible(False)
    ax.set_title(
        t("a   按字统计全部 1,024 笔的误差范围", "a   Min–max error by word, all 1,024 outputs"),
        loc="left",
        fontsize=9,
        fontweight="bold",
    )
    ax.annotate(
        t("64 个字都回到 410304", "All 64 words return 410304"),
        xy=(53, 0),
        xytext=(38, 0.77),
        color=TEAL,
        fontsize=8,
        arrowprops=dict(arrowstyle="-", color=TEAL, lw=0.85),
        ha="center",
    )
    # 色块编码每字三次的越窗次数，而非模拟幅值；两个实验逐 tag 共享输入。
    bx = fig.add_axes([0.17, 0.29, 0.765, 0.13])
    flags = np.array(e["range_flags"])
    cmap = ListedColormap(["#E8F1F1", "#F2C8A9", "#D98755", RUST])
    # 每一格对应一个完整的字和一组接线。色阶编码同一字三次重复中的越窗数。
    # 矩形边界落在半整数位置，16 与 15、48 与 47 的交界不会因插值而变成渐变。
    # 两个深色区只对这组固定输入和宏约定成立，不能外推为全输入量程的故障概率。
    bx.pcolormesh(
        np.arange(65) - 0.5,
        np.arange(3) - 0.5,
        flags,
        cmap=cmap,
        vmin=0,
        vmax=3,
        shading="flat",
        edgecolors="none",
    )
    # 上行始终是连接完整的对照，纵坐标反向后不会把正常与断线标签交换。
    # 相邻字之间没有数据点，块边界放在半码位置，便于检查最先触发越窗的字。
    bx.set_ylim(1.5, -0.5)
    bx.set(
        yticks=[0, 1],
        yticklabels=[t("正常接线", "Connected"), t("断开低段", "Disconnected")],
        xticks=[0, 15, 31, 47, 63],
        xlabel=t("六位扰动字（十进制）", "Six-bit dither word (decimal)"),
    )
    bx.tick_params(axis="y", length=0)
    bx.set_title(
        t(
            "b   断开 SADC 低段后，32 个字使 ADC2 越窗",
            "b   Disconnecting the SADC low segment causes half the sweep to overflow",
        ),
        loc="left",
        fontsize=9,
        fontweight="bold",
        pad=5,
    )
    for x in (15.5, 31.5, 47.5):
        bx.axvline(x, color="white", lw=0.7)
    # 这两个标记位于 16…31 和 48…63 的中心。分母是该字的三遍扫字，并非生产随机序列的总数。
    # 顶图使用真实片内序列，不能从底下均匀强制的字频推断片内伪随机发生器的概率。
    for x in (23.5, 55.5):
        bx.text(x, 1, "3/3", ha="center", va="center", color="white", fontsize=8, weight="bold")
    bx.text(
        31.5,
        0,
        t("全程 0 / 192", "0 / 192 overrange"),
        ha="center",
        va="center",
        color=TEAL,
        fontsize=7.4,
    )
    for s in bx.spines.values():
        s.set_visible(False)
    # 从强制字正常接线与断线两个实际记录取原码与出码。断线的 raw=0 伴有越窗标志。
    # 该数值与合法的最小细码共用编码，所以图中必须把越窗条件一起写明。
    ex = e["example"]
    off = e["disconnected_example"]
    fig.text(
        0.066,
        0.122,
        t(
            f'字 {ex["word"]}  正常：细码 {ex["raw"]} → 输出 {ex["code"]}     断线：越窗，细码 {off["raw"]} → 输出 {off["code"]}',
            f'Word {ex["word"]}: connected raw {ex["raw"]} → {ex["code"]}; disconnected raw {off["raw"]} → {off["code"]} (overrange).',
        ),
        fontsize=8.2,
        weight="bold",
    )
    note(
        fig,
        t(
            "下图每个字强制输入三次。深色区每次都越窗，合计 96 / 192 笔。",
            "Each word was forced three times. Dark blocks overflowed on all three trials (96 / 192).",
        ),
    )
    export(fig, out, "dither-transfer")


def draw_weights(e, out):
    # 本图关心物理开关方向如何改变误差符号，因此同时给出配对线与四种 pre/post 情形。
    # 公开的单元差值由测试台直接注入，不从这 192 个输出反求最佳系数。
    # 同一标签在两表中的 raw、量化字和路由一致，图上的斜率只表示加载表引起的变化。
    # 权重估计误差的统计另由独立训练/验证的静态实验承担。
    fig = sheet(
        t(
            "写入已知权重后，24 笔错码全部回到参考值",
            "Known weights correct all 24 displaced outputs",
        ),
        t(
            "同一物理子单元施加已知偏差  ·  192 个 tag 逐笔配对  ·  两次输入、原码和开关完全相同",
            "One injected cell error  ·  192 matched tags  ·  identical inputs, raw codes and switches",
        ),
        3.65,
    )
    ax = fig.add_axes([0.112, 0.255, 0.425, 0.43])
    ax.set(
        xlim=(-0.13, 1.2),
        ylim=(-43, 43),
        yticks=[-32, 0, 32],
        xticks=[0, 1],
        xticklabels=[t("名义表", "Nominal"), t("已知权重表", "Known weights")],
    )
    ax.set_ylabel(t("输出误差 / LSB20", "Output error / LSB20"))
    # 纵轴零点是输入参考代码，不是某组均值。负 32 表示输出比目标低 32 个输出码。
    # 使用对称的 ± 区间，避免正向与负向误差看起来幅度不同。
    # 连接线表示配对关系，横轴只有两种加载条件，不作连续插值或趋势拟合。
    ax.set_title(
        t("a   逐 tag 配对", "a   Paired by tag"), loc="left", fontsize=9, weight="bold", pad=7
    )
    ax.axhline(0, color=RULE, lw=0.6, ls=":")
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="x", length=0, pad=3)
    # 合并完全重合的点，以文字报告每条线真实的重复数，不给确定性误差添加随机抖动。
    for before, after, count in e["pairs"]:
        color = RUST if before else MUTED
        ax.plot(
            [0, 1],
            [before, after],
            color=color,
            lw=1.45 if before else 1,
            ls="-" if before else "--",
            zorder=2,
        )
        ax.plot(0, before, marker="o", ms=6, mec=color, mfc="white", mew=1.25, zorder=3)
        ax.annotate(
            t(f"{count} 笔", f"n={count}"),
            (0, before),
            xytext=(6, 3),
            textcoords="offset points",
            fontsize=7.7,
            color=color,
        )
    ax.scatter([1], [0], s=48, marker="D", color=TEAL, zorder=4)
    ax.annotate(
        t("192 笔归零", "192 at zero"),
        (1, 0),
        xytext=(-4, 8),
        textcoords="offset points",
        ha="right",
        color=TEAL,
        weight="bold",
        fontsize=8.1,
    )
    # 右栏回到物理开关：pre 与 post 相等时，扰动对差值的贡献抵消；不等时符号随方向翻转。
    # 表中的 raw 偏移与第三次、未注入器件偏差的同字扫取得对照。
    # 仅转换时接入的子单元与仅采样时接入的子单元在重构符号上相反。
    # 两个端口同时接入和都未接入各自独立统计，否则无法得知零误差来自何种开关。
    table = fig.add_axes([0.607, 0.255, 0.345, 0.43])
    table.axis("off")
    table.set(xlim=(0, 1), ylim=(0, 1))
    table.set_title(
        t("b   片 0 的子单元 18", "b   Slice 0, subcell 18"),
        loc="left",
        fontsize=9,
        weight="bold",
        pad=7,
    )
    # pre 与 post 都是被注入的同一物理子单元的选择位；slot 编号不会代替这两个位。
    # 细码偏移使用 ADC2 原码的码步，左侧最终误差使用 LSB20。两栏的比例只适用于该刺激。
    # 本表先列采样再列转换，所以两位选择从 0 至 1 对应正的 post−pre。
    # 原码偏移的符号恰与该方向相反，来自理想余差宏的观测而非从最终误差回算。
    cols = [0.09, 0.31, 0.60, 0.91]
    for x, head in zip(cols, ["pre", "post", t("细码偏移", "Δraw"), t("笔数", "Tags")]):
        table.text(x, 0.90, head, ha="center", color=MUTED, fontsize=7.4)
    table.plot([0, 1], [0.80, 0.80], color=RULE, lw=0.7)
    for j, (pre, post, delta, n) in enumerate(e["cell_cases"]):
        y = 0.66 - j * 0.175
        if delta:
            table.add_patch(Rectangle((0, y - 0.078), 1, 0.154, fc=WARM, ec="none", zorder=0))
        for x, val in zip(cols, [pre, post, f"{delta:+d}" if delta else "0", n]):
            table.text(
                x, y, val, ha="center", va="center", fontsize=8.4, color=RUST if delta else INK
            )
    fig.text(
        0.066,
        0.135,
        t(
            "仅采样时选中和仅转换时选中，会产生方向相反的误差。",
            "Selecting the cell only during sampling or only during conversion reverses the error sign.",
        ),
        color=TEAL,
        weight="bold",
        fontsize=8.3,
    )
    note(
        fig,
        t(
            "理想宏给该单元注入 1/1024 的权重差。细码偏移取自无偏差对照，目标输出 410304 码。",
            "Ideal macro: injected cell-weight error = 1/1024. Raw shifts are relative to the unperturbed sweep. Target: 410304.",
        ),
    )
    export(fig, out, "integer-arithmetic")


def draw_dem(data, out):
    # 矩阵行是八个活动槽，列是每槽的等权宏。一次选中两个半单元表示使用一个完整宏。
    # 不能用片组内的位置统计替代 18 个物理片在长期调度中的访问概率。
    # 命令 283 固定，低段和桥接条件在左右图相同，只有相位发生变化。
    fig = sheet(
        t(
            "命令保持 283，轮换相位把选通分到全部 64 个位置",
            "Phase rotation spreads command 283 across all 64 positions",
        ),
        t(
            "归档叶子 RTL 开关  ·  八个槽 × 八个等权宏列  ·  桥交换关闭",
            "Archived RTL leaf outputs  ·  eight slots × eight equal-weight columns  ·  bridge off",
        ),
        3.9,
    )
    colors = ["#F1F4F3", "#64ADA6", "#183D51"]
    # 三种离散颜色分别表示这次归档实际出现的 0、35、64 次。它们不是消耗能量的刻度。
    # 均匀的轮换热图应呈现同一颜色，不能用每幅图自己定范围的色标夸大局部差异。
    cmap = ListedColormap(colors)
    norm = BoundaryNorm([-0.5, 0.5, 63.5, 64.5], 3)
    # 两图共用绝对次数色标。框出同一个未使用位置，避免热图自动配色把均匀矩阵画成极大值。
    # 高段选满四列，纵向在保留列再选三槽，因此两侧每次都使用 35 个等权位置。
    # 固定组复用同一条记录 64 次；另一组对横纵相位的笛卡尔积各取一条。
    # 记录头含工具与原始流摘要，未给出该次 VCS 编译的 Git 提交。
    fixed = np.asarray(data["fixed_counts"])
    rotated = np.asarray(data["rotated_counts"])
    row, col = np.argwhere(fixed == 0)[0]
    # 方框的坐标由固定相位中的第一个空位决定，两个矩阵画同一个坐标。
    # 计数是整数事件数。画格而不插值，避免出现并未测得的中间颜色。
    # 文字在高低两种颜色上都保持高对比，黑白打印时仍可读出 0 和 35。
    for left, a, title in (
        (0.115, fixed, t("a   固定同一相位", "a   Repeat one phase")),
        (0.565, rotated, t("b   遍历 64 对相位", "b   Cycle all 64 phase pairs")),
    ):
        ax = fig.add_axes([left, 0.302, 0.33, 0.418])
        ax.pcolormesh(
            np.arange(9) - 0.5,
            np.arange(9) - 0.5,
            a,
            cmap=cmap,
            norm=norm,
            shading="flat",
            edgecolors="white",
            lw=0.45,
        )
        ax.set_aspect("equal")
        ax.set_ylim(7.5, -0.5)
        ax.set(xticks=[0, 3, 7], yticks=[0, 3, 7], xlabel=t("等权宏列", "Macro column"))
        if left < 0.5:
            ax.set_ylabel(t("槽位", "Slot"))
        ax.set_xticks(np.arange(-0.5, 8, 1), minor=True)
        ax.set_yticks(np.arange(-0.5, 8, 1), minor=True)
        ax.grid(which="minor", color="white", lw=0.55)
        ax.tick_params(which="both", length=0)
        ax.set_title(title, loc="left", weight="bold", fontsize=9)
        ax.add_patch(Rectangle((col - 0.47, row - 0.47), 0.94, 0.94, fc="none", ec=RUST, lw=1.5))
        ax.text(
            col,
            row,
            str(a[row, col]),
            ha="center",
            va="center",
            fontsize=7.7,
            weight="bold",
            color=INK,
        )
        for s in ax.spines.values():
            s.set_visible(False)
    fig.text(
        0.28,
        0.150,
        t("35 个位置被选，29 个一直空闲", "35 positions used; 29 idle"),
        ha="center",
        weight="bold",
        fontsize=8.6,
    )
    fig.text(
        0.73,
        0.150,
        t("每个位置都被选中 35 次", "All positions used 35 times"),
        ha="center",
        weight="bold",
        color=TEAL,
        fontsize=8.6,
    )
    note(
        fig,
        t(
            "每次选中 8×4＋3＝35 个位置，两组各选通 2,240 次。深色、青色、浅色依次为 64、35、0 次。",
            "64 selections per group. Dark = 64, teal = 35, pale = 0. Outlines trace the same cell.",
        ),
    )
    export(fig, out, "physical-masks")


def draw_events(e, out):
    """每格按实际比较事件排序。空位只表示该分支已经结束，叉才表示真实关写门。"""
    # 第一格是 Flash 已决前缀，此后格子是依序接受的逐位事件。横轴单位是事件序号。
    # 三种路径起码和上段可以不同，切勿把行间命令差归因于保护分支本身。
    # 图只演示每条路径首个 tag 的命令，各类样本数由整组计算。
    # 对实际经过了多长时间的问题，使用同一 CSV 中的时钟沿列。
    fig = sheet(
        t(
            "拒绝 512 试探时，多一次比较，写门仍只开七次",
            "Rejecting the 512 trial adds a comparison without adding a write",
        ),
        t(
            "生产顶层 RTL  ·  三种路径各 64 笔  ·  每行展示该路径首个 tag 的实际 RDAC 命令",
            "Production RTL  ·  64 tags per path  ·  first observed command sequence in each path",
        ),
        3.5,
    )
    ax = fig.add_axes([0.04, 0.225, 0.94, 0.52])
    ax.set(xlim=(-2.42, 9.2), ylim=(-0.56, 2.93))
    ax.axis("off")
    labels = [
        t("普通转换", "Ordinary"),
        t("512 试探拒绝", "512 rejected"),
        t("512 试探接受", "512 accepted"),
    ]
    # 事件序号从 1 起，与硬件测试台的接口约定相同。Flash 的已决前缀可以在第一格直接提交。
    # 图按事件推进显示，空拍和宏响应时钟信息留在真实时序图，避免把两个横轴混用。
    for i in range(8):
        ax.text(i, 2.7, str(i + 1), ha="center", fontsize=8, color=MUTED)
    ax.text(-2.14, 2.7, t("比较事件", "Compare event"), fontsize=8, color=MUTED)
    ax.text(7.85, 2.7, t("事件 / 写入", "Events / writes"), fontsize=7.5, color=MUTED)
    # 若事件拒绝 512，试探本身确实发生，不能简单删除 −1 后对时间重新编号。
    # 同一正命令重复出现时仍是有效写入；不能用相邻数值变化次数冒充写入次数。
    # 横向留白代表这个分支已经结束，与有意抑制的一次有效比较区别开。
    for j, rec in enumerate(e["path_examples"]):
        y = 2 - j
        events = rec["events"]
        ax.text(
            -2.14,
            y + 0.04,
            labels[j],
            va="center",
            fontsize=8.7,
            weight="bold",
            color=RUST if j == 1 else INK,
        )
        ax.text(-2.14, y - 0.29, f'tag {rec["tag"]}', fontsize=7, color=MUTED)
        for i, k in enumerate(events):
            closed = k < 0
            ax.add_patch(
                Rectangle(
                    (i - 0.42, y - 0.245),
                    0.84,
                    0.50,
                    facecolor=WARM if closed else PALE,
                    edgecolor=RUST if closed else RULE,
                    lw=1.15 if closed else 0.5,
                )
            )
            ax.text(
                i,
                y,
                t("关写门", "OFF") if closed else str(k),
                ha="center",
                va="center",
                color=RUST if closed else INK,
                weight="bold" if closed else "normal",
                fontsize=7.9,
            )
        ax.text(
            8.35,
            y,
            f"{len(events)} / {sum(k>=0 for k in events)}",
            ha="center",
            va="center",
            fontsize=9,
            color=RUST if j == 1 else INK,
            weight="bold",
        )
    note(
        fig,
        t(
            "格内数字为实际写入码。拒绝路径的第 2 个事件抑制写入，第 3 个事件从 bit5 继续。",
            "Cells contain actual write commands. After the blocked second event, the next trial resumes at bit 5.",
        ),
    )
    export(fig, out, "sar-events")


def draw_handover(e, out):
    # 画前三个连续标签是为了看到一次三种角色同时活动的时钟沿。
    # 首笔 raw 返回、第二笔余差捕获与第三笔输入采样在第 32 沿重合。
    # 下面 191 个间隔的结论使用全部 192 笔，不由这三个展示标签推导。
    # 图只刻画数字时钟沿，不估计比较器建立或真实宏的非重叠时间。
    fig = sheet(
        t(
            "同一个时钟沿可以同时采样、捕获和返回三笔数据",
            "One edge can sample, capture and return three different tags",
        ),
        t(
            "生产顶层 RTL  ·  首三笔实际时钟沿  ·  640 MHz 理想时钟，宏按约定时刻应答",
            "Production RTL  ·  first three observed tags  ·  ideal 640 MHz clock and scheduled macro responses",
        ),
        3.95,
    )
    ax = fig.add_axes([0.135, 0.315, 0.59, 0.375])
    rows = e["cadence"]
    common = rows[2]["sample"]
    # 空心圆与实心符号区分输入采样和后级观测，图形编码可独立于颜色解读。
    # 同一行的水平位置是实际沿序号；该行始终跟随同一个 tag，无跨样本拼接。
    # 转换开始与末写另外画为粗蓝段，它的长度不含后续安静窗口。
    stages = [
        ("sample", "o", MUTED, t("输入采样", "Sample")),
        ("capture", "D", BLUE, t("余差捕获", "Capture")),
        ("raw_edge", "^", TEAL, t("细码返回", "Raw return")),
        ("out", "s", INK, t("输出", "Output")),
    ]
    ax.axvspan(common - 1.3, common + 1.3, color=WARM, zorder=0)
    ax.axvline(common, color=RUST, lw=0.7, ls="--")
    for i, row in enumerate(rows):
        y = 2 - i
        ax.plot([row["sample"], row["out"]], [y, y], lw=0.9, color=RULE, zorder=1)
        ax.plot(
            [row["start"], row["post"]],
            [y, y],
            lw=3,
            color=BLUE,
            alpha=0.55,
            solid_capstyle="butt",
            zorder=2,
        )
        for key, marker, color, _ in stages:
            ax.scatter(
                row[key],
                y,
                marker=marker,
                s=28,
                facecolor="white" if key == "sample" else color,
                edgecolor=color,
                lw=1.05,
                zorder=3,
            )
    ax.set(
        ylim=(-0.58, 2.48),
        xlim=(-2, 73),
        yticks=[2, 1, 0],
        yticklabels=[f'tag {a["tag"]}' for a in rows],
        xticks=[0, 16, 32, 48, 64],
        xlabel=t("数字时钟上升沿", "Digital clock edge"),
    )
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(
        handles=[
            Line2D(
                [], [], marker=m, lw=0, ms=4.5, mec=c, mfc="white" if k == "sample" else c, label=l
            )
            for k, m, c, l in stages
        ],
        loc="lower left",
        bbox_to_anchor=(-0.02, 1.05),
        ncol=4,
        fontsize=7.1,
        handletextpad=0.1,
        columnspacing=0.8,
        borderaxespad=0,
    )
    fig.text(
        0.755,
        0.68,
        t(f"看第 {common} 沿", f"At edge {common}"),
        color=RUST,
        fontsize=10,
        weight="bold",
    )
    # 并行的三件事按同一沿的上下文列出。tag 1 的细码返回后仍须经过重构才产生最终输出。
    # 直接串联两个相邻 tag 的 valid 很容易把旧笔的返回读成新记入的 owner，因此单列文字标记。
    for y, (row, action) in zip(
        (0.588, 0.505, 0.422),
        zip(
            rows,
            [
                t("返回细码", "raw returns"),
                t("捕获余差", "residue captured"),
                t("采集输入", "input sampled"),
            ],
        ),
    ):
        fig.text(0.755, y, f'tag {row["tag"]}   {action}', fontsize=8.2)
    # clock_fs 只属于此工况。相邻输出拍数乘以该工况的时钟周期才是输出间隔。
    # 周期乘积说明给定宏假设下调度可持续；管线深度影响首笔延迟，是另外一个量。
    counter = e["output_pairs"]
    gap = e["output_gap"]
    period = e["clock_fs"] / 1e6
    fig.text(
        0.115,
        0.165,
        t(
            f"全部 {counter} 个相邻输出间隔均为 {gap} 拍 × {period:g} ns = {gap*period:g} ns。",
            f"All {counter} consecutive output gaps were {gap} clocks × {period:g} ns = {gap*period:g} ns.",
        ),
        color=TEAL,
        weight="bold",
        fontsize=8.8,
    )
    note(
        fig,
        t(
            "粗蓝段从转换启动延伸至末次写入。符号为实测事件沿，宏建立与整核物理时序另行验收。",
            "Thick segments span conversion start to final write. Symbols are observed edges. Physical timing is assessed separately.",
        ),
    )
    export(fig, out, "capture-handover")


def draw_schedule(data, out):
    """左图按实际片号画角色；右图从均匀计数的零偏差开始，两个图各用自己的分母。"""
    # 片组调度镜像每次有效推进才更新，行列统计均按推进次数计数。
    # 前 12 次分配用来展示互斥与交权顺序；整段 20,000 次用来检查长期计数。
    # 逐位向量只覆盖其中前 4,096 行；剩下的参考运算不能登记为 HDL 实测。
    fig = sheet(
        t(
            "八片采集、八片转换，两片备用随调度轮换",
            "Slice roles rotate with eight acquiring, eight converting and two idle",
        ),
        t(
            "调度整数镜像  ·  前 4,096 行逐行匹配冻结向量  ·  统计 20,000 次有效推进",
            "Scheduler reference  ·  first 4,096 rows matched frozen vectors  ·  20,000 accepted advances",
        ),
        3.85,
    )
    roles = np.array(data["roles"])
    ax = fig.add_axes([0.13, 0.27, 0.36, 0.43])
    ax.pcolormesh(
        np.arange(roles.shape[1] + 1) - 0.5,
        np.arange(roles.shape[0] + 1) - 0.5,
        roles,
        cmap=ListedColormap(["#EBEEED", TEAL, INK]),
        vmin=-0.5,
        vmax=2.5,
        shading="flat",
        edgecolors="none",
    )
    ax.invert_yaxis()
    ax.set(
        xticks=[0, 3, 7, 11],
        xticklabels=[1, 4, 8, 12],
        yticks=[0, 5, 11, 17],
        xlabel=t("有效推进序号", "Advance index"),
        ylabel=t("物理片号", "Physical slice ID"),
    )
    ax.tick_params(length=0)
    ax.set_title(
        t("a   前 12 次分配", "a   First 12 allocations"), loc="left", fontsize=9, weight="bold"
    )
    for s in ax.spines.values():
        s.set_visible(False)
    # 每推进一次恰有 8 个采集片，因此均匀基线是推进数乘 8/18。
    # 相邻两次的片组受流水互斥条件限制，散点不能按独立二项试验画置信区间。
    # 正负偏差沿同一纵轴对称显示，便于看清频数漂移的实际比例。
    bx = fig.add_axes([0.64, 0.27, 0.285, 0.43])
    counts = np.asarray(data["counts"])
    base = data["cycles"] * 8 / 18
    bx.axhline(0, color=RULE, lw=0.7)
    bx.scatter(np.arange(18), 100 * (counts / base - 1), color=BLUE, s=12)
    bx.set(
        xticks=[0, 5, 11, 17],
        ylim=(-0.7, 0.7),
        yticks=[-0.5, 0, 0.5],
        xlabel=t("物理片号", "Physical slice ID"),
        ylabel=t("相对均匀值 / %", "Deviation / %"),
    )
    bx.set_title(
        t("b   各片被选作采集的偏差", "b   Acquisition count by slice"),
        loc="left",
        fontsize=8.8,
        weight="bold",
    )
    fig.text(
        0.13,
        0.143,
        t("青色为采集，深色为转换，浅灰为备用。", "Teal: acquire. Navy: convert. Grey: idle."),
        fontsize=7.5,
        color=MUTED,
    )
    note(
        fig,
        t(
            f'20,000 次推进共覆盖 {data["idle_pairs"]} 种备用片对。横轴按有效推进计数，与纳秒时长分开。',
            f'All {data["idle_pairs"]} idle pairs occurred over 20,000 advances. The horizontal axis counts accepted advances.',
        ),
    )
    export(fig, out, "slice-allocation")


# =============================================================================
# 3. 逐码模型图。每段保存全体输出码的最小、最大值，局部曲线使用未压缩的单码宽度。
# =============================================================================
def peak_pair(case, mode, decoder, metric):
    """返回同一设置的绝对峰值；码段包络只用于画线，不用于取峰值或筛选工况。"""
    stats = case["modes"][mode]["decoders"][decoder]["exact"]
    k = metric.lower()
    return max(abs(stats[k + "_min"]), abs(stats[k + "_max"]))


def draw_linearity(data, case, out):
    sigma = case["relative_mismatch_sigma"]
    state_count = int(data["states"])
    seed = case["seed"]
    # 两组曲线来自同一颗模型器件和独立评估状态，DEM 与扰动同时开启。
    # 差别为名义表或训练所得拟合表。此处并不把几种改动的总收益归给单项校准。
    # 每个显示区间保留全部内部码的最小值及最大值；极值附近不会因抽点消失。
    # 图题沿用此归档的器件种子和失配口径，不随代码最新提交自动换代。
    fig = sheet(
        t(
            "相同的 DEM 和扰动条件下，拟合权重降低线性误差",
            "Fitted weights lower linearity error with switching controls held fixed",
        ),
        t(
            f"归档静态电荷模型  ·  相对失配 {sigma:.2%}  ·  {state_count} 个状态  ·  DEM 与扰动均开启",
            f"Archived static charge model  ·  mismatch {sigma:.2%}  ·  {state_count} states  ·  DEM and dither on",
        ),
        4.38,
    )
    # INL 指过渡点相对端点直线的非线性，DNL 指内部码宽相对基准码宽的误差。
    # 同一指标的两种加载条件共享坐标。两种指标的量级不同，各自有单位与刻度。
    # 饱和端码的截断规则与归档一致；曲线不能证明每个单独开关状态都单调。
    # 论文图 9.8.4 的横轴是输出码，故这里保持相同顺序。论文的样片曲线没有被数字化混进模型。
    # 归档区间的纵向跨度保留真实窄码尖峰；上下界之间的填色不是重复测量的置信区间。
    for j, metric in enumerate(("INL", "DNL")):
        bottom = (0.496, 0.193)[j]
        ax = fig.add_axes([0.126, bottom, 0.60, 0.23])
        for dec, color, ls in [("nominal", RUST, "--"), ("fitted", BLUE, "-")]:
            x, lower, upper = data[f"{dec}_{metric}"]
            ax.fill_between(x, lower, upper, color=color, alpha=0.12, lw=0)
            ax.plot(x, lower, color=color, ls=ls, lw=0.65)
            ax.plot(x, upper, color=color, ls=ls, lw=0.65)
        ax.axhline(0, color=MUTED, lw=0.45, zorder=0)
        ax.set_xlim(0, 2**20 - 1)
        ax.set_ylabel(metric + " / LSB20")
        ax.set_xticks([0, 2**18, 2**19, 3 * 2**18, 2**20 - 1])
        if j == 0:
            ax.set_xticklabels([])
        else:
            ax.set_xticklabels(["0", "262144", "524288", "786432", "1048575"])
            ax.set_xlabel(t("20 位输出码", "20-bit output code"))
        # 圈出的码号或过渡点由完整数组的最坏索引决定，不取压缩码段中心冒充实际码位。
        # 纵坐标带符号，所以位于负区的最大绝对误差仍应画在零线下方。
        # 右侧数字报告绝对峰值；曲线本身保留正负方向，二者不能互换。
        raw = case["modes"]["both"]["decoders"]["nominal"]["exact"]
        key = metric.lower()
        signed = max([raw[key + "_min"], raw[key + "_max"]], key=abs)
        location = raw["worst_inl_transition" if metric == "INL" else "worst_dnl_code"]
        ax.scatter(
            location, signed, s=17, facecolor="white", edgecolor=RUST, linewidth=0.85, zorder=4
        )
        a = peak_pair(case, "both", "nominal", metric)
        b = peak_pair(case, "both", "fitted", metric)
        fig.text(
            0.77,
            bottom + 0.18,
            t(f"峰值 |{metric}|", f"Peak |{metric}|"),
            fontsize=8.2,
            color=MUTED,
        )
        fig.text(0.77, bottom + 0.096, f"{a:.3f}", color=RUST, fontsize=12, weight="bold")
        fig.text(0.862, bottom + 0.098, t("名义", "Nominal"), fontsize=7.5, color=RUST)
        fig.text(0.77, bottom + 0.017, f"{b:.3f}", color=BLUE, fontsize=12, weight="bold")
        fig.text(0.862, bottom + 0.019, t("拟合", "Fitted"), fontsize=7.5, color=BLUE)
    fig.text(
        0.13,
        0.755,
        t(
            "虚线  名义表       实线  拟合表       空心圆  名义表峰值位置",
            "Dashed  nominal       Solid  fitted       Circle  nominal worst code",
        ),
        color=MUTED,
        fontsize=7.7,
    )
    note(
        fig,
        t(
            f"全部 1,048,576 个码按区间保留极值。指标排除两端饱和码，按端点拟合。器件种子 {seed}。",
            f"Extrema preserved across all 1,048,576 codes. Endpoint metrics exclude rail codes. Device seed {seed}.",
        ),
    )
    export(fig, out, f"linearity-{sigma:g}")


def draw_control_effects(data, case, out):
    sigma = case["relative_mismatch_sigma"]
    modes = ("fixed", "dem", "dither", "both")
    # 每个模式内部只交换名义与拟合两张表。不同控制行之间的开关状态本来就不同。
    # 比较 DNL 时必须保留某些工况反而变坏的对照。不能只挑峰值下降的三行。
    # 成对标记不是统计误差棒，其连线也不是各工况之间的插值。
    fig = sheet(
        t(
            "四种开关条件下，权重校正的效果不同",
            "The effect of weight correction depends on the switching mode",
        ),
        t(
            f"归档静态电荷模型  ·  相对失配 {sigma:.2%}  ·  每行比较同一器件和相同开关条件",
            f"Archived charge model  ·  mismatch {sigma:.2%}  ·  paired within each switching mode",
        ),
        4.53,
    )
    names = [
        t("固定相位", "Fixed"),
        t("仅 DEM", "DEM"),
        t("仅扰动", "Dither"),
        t("DEM + 扰动", "Both"),
    ]
    for j, metric in enumerate(("INL", "DNL")):
        ax = fig.add_axes([(0.16, 0.605)[j], 0.525, 0.32, 0.19])
        ax.set(ylim=(3.48, -0.48), yticks=range(4), yticklabels=names if j == 0 else [""] * 4)
        ax.set_xlabel(t(f"峰值 |{metric}| / LSB20", f"Peak |{metric}| / LSB20"))
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        for i, mode in enumerate(modes):
            a, b = [peak_pair(case, mode, x, metric) for x in ("nominal", "fitted")]
            # 一行只换加载表。即使误差上升也保留同样的轴和标识。
            ax.plot([a, b], [i, i], color=RULE, lw=1.35)
            ax.plot(a, i, "o", mfc="white", mec=RUST, ms=4.8, mew=1)
            ax.plot(b, i, "D", color=BLUE, ms=4)
        ax.set_xlim(left=0)
        ax.set_title("ab"[j] + "   " + metric, loc="left", weight="bold", fontsize=8.8, pad=3)
    fig.text(
        0.94,
        0.778,
        t("空心圆  名义表     菱形  拟合表", "Circle  nominal     Diamond  fitted"),
        ha="right",
        fontsize=7.0,
        color=MUTED,
    )
    ax = fig.add_axes([0.14, 0.20, 0.78, 0.17])
    # 只用未校准、固定相位基准的最坏 DNL 选局部窗口。选择完成后所有解码器都用同一窗。
    # 中心码可能靠近端点，此时数据准备阶段只保留存在的码，不补造端点样本。
    # 横轴以中心码为零，纵轴是一整个实际码的输入宽度，用阶梯线表达离散性。
    center = int(data["center"])
    # 宽度为 1 对应理想单码。归档数组以输入等效的 LSB20 为单位，局部窗口直接读该数组。
    # 比较同一码是否变宽或变窄时，不把几条曲线各自对齐到自己的最坏码。
    offset = data["codes"] - center
    # 局部叠图保留固定相位名义表、固定相位拟合表以及全开拟合表的次序。
    # 前两条只改系数，后两条再改开关状态；避免把两步改善都归给同一种校准。
    for name, c, ls in [
        ("fixed_nominal", RUST, "--"),
        ("fixed_fitted", MUTED, ":"),
        ("both_fitted", BLUE, "-"),
    ]:
        ax.step(offset, data[name], where="mid", color=c, ls=ls, lw=1.05)
    ax.axhline(1, color=RULE, lw=0.5, zorder=0)
    ax.set(
        xlabel=t(
            f"距基准最宽码 {center:,} 的码数", f"Code relative to baseline worst DNL at {center:,}"
        ),
        ylabel=t("码宽 / LSB20", "Width / LSB20"),
    )
    ax.set_title(
        t(
            "c   同一局部码段，框选点为基准最坏 DNL 码",
            "c   Identical code interval around the worst baseline DNL",
        ),
        loc="left",
        weight="bold",
        fontsize=8.3,
        pad=4,
    )
    # 工作窗中的圆点追踪同一个实际输出码。整片峰值取自摘要，局部显示不重新定义峰值。
    k = int(np.where(offset == 0)[0][0])
    ax.scatter(0, data["fixed_nominal"][k], s=18, facecolor="white", edgecolor=RUST, zorder=4)
    ax.text(
        0.98,
        0.92,
        t(
            "虚线 固定·名义   点线 固定·拟合   实线 全开·拟合",
            "Dashed fixed/nominal   Dotted fixed/fitted   Solid both/fitted",
        ),
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.7,
        color=MUTED,
        bbox=dict(facecolor="white", edgecolor="none", pad=0.5),
    )
    a, b = [peak_pair(case, "dither", x, "DNL") for x in ("nominal", "fitted")]
    note(
        fig,
        t(
            f"仅扰动一行的峰值 |DNL| 从 {a:.3f} 变为 {b:.3f} LSB20。各工况分别计量。",
            f"Dither-only peak |DNL| changes from {a:.3f} to {b:.3f} LSB20. Controls are evaluated separately.",
        ),
    )
    export(fig, out, f"effects-{sigma:g}")
