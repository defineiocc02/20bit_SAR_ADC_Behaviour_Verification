#!/usr/bin/env python3
# =============================================================================
# 文件名     : render_floorplan.py
# 模块名     : AD4080 整芯片规划图版
# 版本       : v1.0
# 功能       : 从规划坐标、面积账和两种源图生成三页 PDF 和主图 PNG。
# 设计说明   : 左下起算的微米规划与左上起算的图像像素各用自身的标尺。
#              复用规划构建器的几何检查和银行编号，避免图版与交换文件漂移。
#              ISSCC 讲稿的两段局部独立标注页号，按原图横、纵箭头读取比例。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""在 figures 下生成 floorplan.pdf 和 300 dpi 的 floorplan.png。

使用 Python 3.10 或更新版本、ReportLab、macOS 字体和 Poppler pdftoppm。源图以只读方式打开。
资料搬迁后可通过 --photo 指定 IPBrain 原图，通过 --slides 指定 ISSCC 原始讲稿。
"""

import argparse
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import runpy
import subprocess

from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# 商品裸片照片随本地审阅报告交付。原讲稿从冻结基线或显式路径读取，不搜索旧版工作树。
PHOTO = ROOT / "report" / "ipbrain_reference.png"
# 三页使用相同页面尺寸，可整页插入报告，也可按比例裁出单幅论文插图。
WIDTH, HEIGHT = 940, 704  # PDF 点。所有版式坐标从页面左上起算。
INK = HexColor("#192F43")
MUTED = HexColor("#486075")
RULE = HexColor("#D7E1EA")
GRID = HexColor("#DFE8EF")
RING = HexColor("#EFF2F4")
RING_EDGE = HexColor("#9AAAB5")
CORE = HexColor("#FBFDFF")
CHANNEL = HexColor("#AAD8C8")
FLOW = HexColor("#145F56")
PORT = HexColor("#9D452E")
Q_SPUR = {"Q_SPUR_N", "Q_SPUR_S"}
PERIPHERAL = ("#FAE2BB", "#905B1F")  # CTRL 用暖色对应 GDS 上的外围图层。
# 端口、通道与禁放带具有独立线型，保留黑白打印时的可辨性。
HATCH_FILL = HexColor("#F3E8EB")
HATCH_INK = HexColor("#90606C")

# 颜色按功能族复用，原图叠加边框沿用同组颜色以便跨页定位。
# 规划窗尺寸来自 JSON。此处的样式、锚点和标签只影响读图。
STYLE = {
    "logic": ("#DCEAF9", "#265B8D"),
    "memory": ("#F1E8F6", "#775492"),
    "nv": ("#F8E6D5", "#995C37"),
    "io": ("#D8EDF1", "#276A76"),
    "clock": ("#F7ECCA", "#816A32"),
    "analog": ("#E1F0E9", "#487C6C"),
    "reference": ("#DCECF3", "#4B778D"),
    "power": ("#E9EEDC", "#688047"),
}
# 标签位置保持在对应矩形内。CAP 窄窗使用旋转文字，Q 的两块各自按宽度选字号。
# LDO1 和 LDO2 用规划标识对应原图两处 LDO，以便正文独立说明供电方案的证据边界。
LABELS = {
    "SR2": [("小 SRAM", 8.8, 0)],
    "OTP": [("OTP 预留", 13.3, 0)],
    "DIO": [("数据接口", 10.4, 0)],
    "CTRL": [("结果处理\n系统控制", 10.6, 0)],
    "CLK": [("时钟", 10.8, 0)],
    "RDAC": [("RDAC 阵列", 17, 0)],
    "Q": [("粗量化与采样", 12.6, 0), ("Q 规划窗", 11, 0)],
    "RA": [("余差放大", 12.5, 0)],
    "ADC2": [("第二级量化", 12.2, 0)],
    "REF": [("参考与缓冲", 12.5, 0), ("参考与缓冲", 11.5, 0)],
    "LDO1": [("LDO1", 8.8, 0)],
    "LDO2": [("LDO2", 8.8, 0)],
    "CAP": [("电容预留", 9, -90)],
}


# =============================================================================
# 1. 页面图元、字体及两套坐标变换
# =============================================================================
def register_fonts():
    fonts = Path("/System/Library/Fonts")
    # TTC 的第 1 个索引为简体中文。ReportLab 对每页所用字形自动做子集嵌入。
    # 坐标表的等宽字体维持数列对齐，中文字体承担混排字体中箭头及面积单位的字形。
    for name, filename, index in (
        ("Han", "STHeiti Light.ttc", 1),
        ("HanBold", "STHeiti Medium.ttc", 1),
        ("Latin", "Avenir Next.ttc", 7),
        ("LatinBold", "Avenir Next.ttc", 0),
        ("Mono", "Menlo.ttc", 0),
    ):
        pdfmetrics.registerFont(TTFont(name, str(fonts / filename), subfontIndex=index))


class Sheet:
    """把版式点坐标转换为 PDF 的左下原点，统一图元对齐和线宽。"""

    def __init__(self, pdf):
        self.pdf = pdf

    def text(self, value, x, y, size=10, font="Han", color=INK, align="left", angle=0):
        # 先按字符串在本地坐标下对齐，再旋转，可使竖排标签仍围绕窗口中心布局。
        p = self.pdf
        shift = {"left": 0, "center": 0.5, "right": 1}[align]
        p.saveState()
        p.setFont(font, size)
        p.setFillColor(color)
        p.translate(x, HEIGHT - y)
        p.rotate(angle)
        p.drawString(-shift * pdfmetrics.stringWidth(value, font, size), 0, value)
        p.restoreState()

    def line(self, xy, uv, color=RULE, width=0.7, dash=()):
        # 分开保存线状态，核心边界的虚线不会套用到后续银行及端口。
        p = self.pdf
        p.saveState()
        p.setLineWidth(width)
        p.setStrokeColor(color)
        p.setDash(dash)
        p.line(xy[0], HEIGHT - xy[1], uv[0], HEIGHT - uv[1])
        p.restoreState()

    def box(self, x, y, w, h, fill=None, stroke=None, width=0.8, dash=()):
        # ReportLab 的矩形锚点为左下，左上坐标需在反转方向后再扣除矩形高度。
        p = self.pdf
        p.saveState()
        p.setDash(dash)
        p.setLineWidth(width)
        if fill is not None:
            p.setFillColor(fill)
        if stroke is not None:
            p.setStrokeColor(stroke)
        p.rect(x, HEIGHT - y - h, w, h, fill=fill is not None, stroke=stroke is not None)
        p.restoreState()

    def arrow(self, xy, uv, color=MUTED, width=0.85, head=3.5):
        self.line(xy, uv, color, width)
        # 箭尖按页面方向计算，使像素 y 向下和规划 y 向上的共用绘图方法同样有效。
        angle = math.atan2(uv[1] - xy[1], uv[0] - xy[0])
        ends = [(uv[0] - head * math.cos(angle + d), uv[1] - head * math.sin(angle + d))
                for d in (-0.55, 0.55)]
        for end in ends:
            self.line(uv, end, color, width)

    def hatched(self, x, y, w, h):
        self.box(x, y, w, h, HATCH_FILL)
        # 在截断矩形后绘制实线斜纹，以便灰度打印时仍能识别禁放带。
        p = self.pdf
        p.saveState()
        clip = p.beginPath()
        clip.rect(x, HEIGHT - y - h, w, h)
        p.clipPath(clip, fill=0, stroke=0)
        for step in range(0, math.ceil(w + h), 4):
            self.line((x + step - h, y + h), (x + step, y), HATCH_INK, 0.65)
        p.restoreState()
        # 边线最后覆盖裁切点，紧贴 IP 边界的面积细节在输出屏幕图中也清楚。
        self.box(x, y, w, h, stroke=HATCH_INK, width=0.7)

    def header(self, title, subtitle):
        # 左侧产品代号共享同一基线，分别给规划图和量测图标题留出固定起点。
        self.text("AD4080", 48, 51, 30, "LatinBold")
        self.line((176, 30), (176, 53), RULE, 1)
        self.text(title, 193, 50, 25, "HanBold")
        self.text(subtitle, 49, 74, 10.8, color=MUTED)
        self.line((48, 86), (894, 86))

    def footer(self, note, page):
        # 报告拆取单页后仍保留坐标解释和源文件名。
        self.line((48, 674), (894, 674))
        self.text(note, 48, 691, 9.5, color=MUTED)
        self.text(f"{page} / 3", 894, 691, 9.5, "Latin", MUTED, "right")


class PlanFrame:
    """规划区以 µm 输入，矩形保持等比例。像素原图另按左上坐标描框。"""

    def __init__(self, rect, x, y, size):
        # 横纵应用同一比例尺，银行纵横比与通道宽度均能从图面量取。
        self.die = rect
        self.x, self.y = x, y
        self.scale = size / (rect[2] - rect[0])

    def point(self, x, y):
        # 裸片上边缘落在页面上边缘，保持北侧数据接口与原图方位一致。
        return self.x + (x - self.die[0]) * self.scale, self.y + (self.die[3] - y) * self.scale

    def box(self, rect):
        x, y = self.point(rect[0], rect[3])
        return x, y, (rect[2] - rect[0]) * self.scale, (rect[3] - rect[1]) * self.scale


# =============================================================================
# 2. 规划图，从数据库复用窗口、银行、禁放带和端口长度
# =============================================================================
def outline(rectangles):
    """输出矩形并集外缘。相邻小网格的共用边相消，因此 IP 不出现内部伪分区线。"""
    xs = sorted({r[k] for r in rectangles for k in (0, 2)})
    ys = sorted({r[k] for r in rectangles for k in (1, 3)})
    edges = set()
    # 用全部顶点把长边拆成共端点小段。这样 IP 的翼部只贴主区一段边时也能正确相消。
    for x, u in zip(xs, xs[1:]):
        for y, v in zip(ys, ys[1:]):
            if any(a <= x and u <= c and b <= y and v <= d for a, b, c, d in rectangles):
                edges.symmetric_difference_update({((x, y), (u, y)), ((x, v), (u, v)),
                                                   ((x, y), (x, v)), ((u, y), (u, v))})
    return sorted(edges)


def label_at(frame, sheet, value, x, y, size, font="HanBold", color=INK, angle=0):
    # 中文和拉丁字体都把基线放在矩形的视觉中线以下约三分之一字号。
    # 旋转九十度后把相同基线补偿旋转到 x 方向，C4 通道窄编号和电容标注居中。
    tx, ty = frame.point(x, y)
    if angle == -90:
        tx -= 0.32 * size
    else:
        ty += 0.32 * size
    sheet.text(value, tx, ty, size, font, color, "center", angle)


def map_grid(sheet, frame, plan):
    # 焊盘环按规划宽度连续绘制。规划文件尚无单焊盘节距或编号。
    sheet.box(*frame.box(plan["die"]), fill=RING, stroke=INK, width=1.35)
    sheet.box(*frame.box(plan["core"]), fill=CORE)
    x, y, u, v = plan["die"]
    a, b, c, d = plan["core"]
    # 从裸片全宽和全高中等分取得网格，再限裁到焊盘环内侧，便于估计未分配区大小。
    ticks = [(x + i * (u - x) / 5, y + i * (v - y) / 5) for i in range(6)]
    for tick_x, tick_y in ticks[1:-1]:
        sheet.line(frame.point(tick_x, b), frame.point(tick_x, d), GRID, 0.45, (2, 2))
        sheet.line(frame.point(a, tick_y), frame.point(c, tick_y), GRID, 0.45, (2, 2))
    sheet.box(*frame.box(plan["core"]), stroke=RING_EDGE, width=0.85, dash=(3, 2))
    label_at(frame, sheet, f"焊盘环预留  {plan['pad_ring_width']} µm", (x + u) / 2,
             (v + d) / 2, 10.8, "HanBold", MUTED)
    label_at(frame, sheet, "北", a, (v + d) / 2, 10.2, "HanBold", MUTED)
    label_at(frame, sheet, "南", a, (y + b) / 2, 10.2, "HanBold", MUTED)
    label_at(frame, sheet, "西", (x + a) / 2, (y + v) / 2, 10.2, "HanBold", MUTED)
    label_at(frame, sheet, "东", (u + c) / 2, (y + v) / 2, 10.2, "HanBold", MUTED)
    label_at(frame, sheet, f"{u - x:g} × {v - y:g} µm", (x + u) / 2, (y + b) / 2,
             11.5, "LatinBold", MUTED)

    x0, y0 = frame.point(x, y)
    x1, y1 = frame.point(u, v)
    # 坐标刻度移到外轮廓外，留完整绘图区给 120 µm 焊盘环和紧贴北界的数据接口。
    ax, ay = x0 - 11, y0 + 10
    sheet.arrow((x0, ay), (x1 + 12, ay))
    sheet.arrow((ax, y0), (ax, y1 - 9))
    sheet.text("x", x1 + 14, ay + 3.5, 9.5, "LatinBold", MUTED)
    sheet.text("y", ax - 6, y1 - 8, 9.5, "LatinBold", MUTED, "right")
    for tick_x, tick_y in ticks:
        tx, ty = frame.point(tick_x, tick_y)
        sheet.line((tx, ay - 2), (tx, ay + 4), MUTED, 0.52)
        sheet.text(f"{tick_x:g}", tx, ay + 17, 9.4, "Latin", MUTED, "center")
        sheet.line((ax - 4, ty), (ax + 2, ty), MUTED, 0.52)
        sheet.text(f"{tick_y:g}", ax - 7, ty + 3.1, 9.4, "Latin", MUTED, "right")


def part_windows(sheet, frame, plan, banks, stats):
    for part in plan["partitions"]:
        key = part["id"]
        fill, stroke = map(HexColor, PERIPHERAL if key == "CTRL" else STYLE[part["group"]])
        if key == "IP":
            # 先画软区本体，再叠加扣除行，外边界最后闭合以便读者直接核对连通性。
            for r in part["rects"]:
                sheet.box(*frame.box(r), fill=fill)
            # 禁放带只扣除标准单元行，仍位于完整的 IP FENCE 内。
            for r in plan["ip_row_blockages"]:
                sheet.hatched(*frame.box(r))
            for xy, uv in outline(part["rects"]):
                sheet.line(frame.point(*xy), frame.point(*uv), stroke, 1.65)
            # 主标题和面积各占禁放带的一侧，避开北侧配置端口和南侧两组接口。
            label_at(frame, sheet, "转换 IP（功能组·已有）", 720, 1045, 16.1, "HanBold", stroke)
            gross = stats['ip_gross_um2'] / 1_000_000
            label_at(frame, sheet, f"规划窗口  {gross:.3f} mm²", 720, 957, 10.9, "Han", stroke)
            label_at(frame, sheet, "IP 北侧翼", 349, 1257, 9, "HanBold", stroke)
            label_at(frame, sheet, "IP 南侧翼", 399, 870, 9, "HanBold", stroke)
            continue

        if key == "FIFO":
            # FIFO 外框是编译器窗口，内层四矩形才是各银行的尺寸上限。
            # `banks` 返回编号与 DEF/GDS 一致，自南向北分两排，按每排从西向东递增。
            sheet.box(*frame.box(part["rects"][0]), fill=fill, stroke=stroke, width=0.9)
            for i, r in enumerate(banks):
                sheet.box(*frame.box(r), fill=HexColor("#E4D3EF"), stroke=stroke, width=0.72)
                x, y, u, v = r
                label_at(frame, sheet, f"BANK {i}", (x + u) / 2, (y + v) / 2,
                         11.4, "LatinBold", stroke)
            continue

        # 尺寸与窗口个数以规划数据为准。个数有变化时 strict 使标签复核成为显式步骤。
        for r, (label, size, angle) in zip(part["rects"], LABELS[key], strict=True):
            sheet.box(*frame.box(r), fill=fill, stroke=stroke, width=0.85)
            x, y, u, v = r
            lines = label.split("\n")
            # 结果处理、系统控制共享单一 CTRL 窗。换行只调整字的位置与行距。
            for i, line in enumerate(lines):
                shift = (len(lines) - 1 - 2 * i) * 0.66 * size / frame.scale
                label_at(frame, sheet, line, (x + u) / 2, (y + v) / 2 + shift, size, "HanBold", stroke, angle)


def markers(sheet, frame, plan):
    # C 号跟随 plan 中的通道顺序。横向通道的短编号放在内部；C4 旋转排字。
    # C1 放在 P2 和 P3 两段线的间距内，避免白色端口衬线压到通道编号。
    lane_labels = {
        "ADC_DIG": (868, 889, 0), "SRAM_BUS": (800, 1107.5, 0),
        "REF_FEED": (782, 226, 0), "RESIDUE": (989, 602, -90),
        "RA_ADC2": (1150, 659, 0), "IO_EAST": (1172, 1368, 0),
        "Q_SPUR_N": (93, 921, 0), "Q_SPUR_S": (93, 799, 0),
    }
    # 五根箭头表示预留走向。总线同时服务读写，所以只显示精确通道边界。
    # 模拟余差箭头沿 RDAC 到 RA 再到 ADC2，和朝上汇入阵列的参考通路使用相同绿色。
    flow = {
        "ADC_DIG": ((672, 898), (672, 881)), "REF_FEED": ((718, 220), (718, 233)),
        "RESIDUE": ((981, 545), (997, 545)), "RA_ADC2": ((1203, 651), (1203, 667)),
        "IO_EAST": ((1235, 1368), (1366, 1368)),
    }
    for i, chan in enumerate(plan["channels"], 1):
        key = chan["id"]
        sheet.box(*frame.box(chan["rect"]), fill=CHANNEL, stroke=FLOW, width=0.48)
        x, y, angle = lane_labels[key]
        label_at(frame, sheet, f"C{i}", x, y, 6.65, "Mono", FLOW, angle)
        # 西侧两段是连续的窄通道。编号引到环区，通道内保留空间给走向箭头。
        if key in Q_SPUR:
            sheet.line(frame.point(x + 16, y), frame.point(chan["rect"][0], y), FLOW, 0.5)
        if key in flow:
            xy, uv = flow[key]
            sheet.arrow(frame.point(*xy), frame.point(*uv), FLOW, 0.75, 2.8)

    # 端口线按 JSON 的真实端点绘制，文字移到 IP 内以免挡住模拟通道。
    # 白色窄衬线把红色端口端点与蓝色 IP 外框分开，方便在放大图里精确定位。
    anchors = {"CLK_RST": (174, 963), "SADC_RDAC": (675, 919), "ADC2_RSP": (975, 919),
               "RESULT": (1060, 952), "CFG_TEST": (548, 1082), "Q_RETURN": (174, 921)}
    for i, port in enumerate(plan["ports"], 1):
        x, y, u, v = port["line"]
        sheet.line(frame.point(x, y), frame.point(u, v), white, 3.6)
        sheet.line(frame.point(x, y), frame.point(u, v), PORT, 2.1)
        label_at(frame, sheet, f"P{i}", *anchors[port["group"]], 8.1, "Mono", PORT)
    q_return(sheet, frame, plan)


def q_return(sheet, frame, plan):
    # 预置与写使能去往 Q，判决回到 P6，两个箭头指向各自所接的宏边。
    # 共用竖线落在南北两段的公共 x 范围，中点取两个接口各自共享边的中点。
    lanes = {ch["id"]: ch["rect"] for ch in plan["channels"] if ch["id"] in Q_SPUR}
    north, south = lanes["Q_SPUR_N"], lanes["Q_SPUR_S"]
    spine_x = (max(north[0], south[0]) + min(north[2], south[2])) / 2
    q = next(part["rects"][0] for part in plan["partitions"] if part["id"] == "Q")
    port = next(p["line"] for p in plan["ports"] if p["group"] == "Q_RETURN")
    q_y = (max(south[1], q[1]) + min(south[3], q[3])) / 2
    port_y = (port[1] + port[3]) / 2
    south_bend, north_bend = frame.point(spine_x, q_y), frame.point(spine_x, port_y)
    sheet.line(south_bend, north_bend, FLOW, 0.82)
    sheet.arrow(south_bend, frame.point(q[0] - 1.2, q_y), FLOW, 0.82, 2.5)
    # 端口线有自身笔宽。箭尖在页面左侧让出半个端口线宽，保留清楚的 P6 边缘。
    port_x, port_edge_y = frame.point(port[0], port_y)
    sheet.arrow(north_bend, (port_x - 1.05, port_edge_y), FLOW, 0.82, 2.3)


def plan_sidebar(sheet, plan, stats):
    # 正文通道用 C 号索引，避免把长名称硬塞进实际只有十几微米高的通道。
    x, right = 659, 894
    sheet.line((638, 104), (638, 630))
    sheet.text("图例", x, 118, 14, "HanBold")
    sheet.box(x, 131, 26, 10, fill=HexColor(STYLE["logic"][0]), stroke=HexColor(STYLE["logic"][1]), width=1.1)
    sheet.box(x, 148, 26, 10, fill=HexColor(PERIPHERAL[0]), stroke=HexColor(PERIPHERAL[1]), width=0.85)
    sheet.hatched(x, 165, 26, 10)
    sheet.box(x, 182, 26, 10, fill=CHANNEL, stroke=FLOW, width=0.55)
    sheet.arrow((x + 6, 187), (x + 20, 187), FLOW, 0.75, 3)
    sheet.line((x, 204), (x + 26, 204), PORT, 2.1)
    for value, y in (("转换 IP（功能组·已有）", 140), ("系统外围（CTRL）", 157),
                     ("标准单元禁放带", 174), ("预留通道与规划走向", 191), ("IP 边界端口组", 208)):
        sheet.text(value, x + 37, y, 10.7)

    sheet.line((x, 220), (right, 220))
    sheet.text("预留通道", x, 241, 13, "HanBold")
    channel_text = {"RESIDUE": "RDAC → 余差放大", "RA_ADC2": "余差放大 → 第二级量化",
                    "IO_EAST": "数据接口 → 东侧焊盘"}
    channels = [(i, c) for i, c in enumerate(plan["channels"], 1)]
    lane_rows = [(f"C{i}", channel_text.get(c["id"], c["name"]))
                 for i, c in channels if c["id"] not in Q_SPUR]
    # Q 走廊有两块几何，在图例用同一行说明两段的共同用途。
    q_code = "/".join(str(i) for i, c in channels if c["id"] in Q_SPUR)
    lane_rows.append((f"C{q_code}", "粗量化区 ↔ IP 西侧 P6"))
    for i, (code, label) in enumerate(lane_rows, 1):
        y = 241 + 16 * i
        sheet.text(code, x, y, 10.2, "Mono", FLOW)
        sheet.text(label, x + 37, y, 10.5)

    sheet.line((x, 367), (right, 367))
    # P 号列表与端口端点按同一顺序编号，名称直接取接口合同中的规划字段。
    sheet.text("IP 边界端口", x, 387, 13, "HanBold")
    for i, item in enumerate(plan["ports"], 1):
        y = 387 + 16 * i
        sheet.text(f"P{i}", x, y, 10.2, "Mono", PORT)
        count = f"（{item['signal_count']} 根）" if "signal_count" in item else ""
        sheet.text(item["name"] + count, x + 37, y, 10.5)

    sheet.line((x, 493), (right, 493))
    # 容量和最大宏尺寸分别引用 sram 合同及几何账，强调窗口的容量与物理两道约束。
    sheet.text("结果 SRAM 窗口", x, 515, 13, "HanBold")
    memory = plan["sram"]
    sheet.text(f"{memory['banks']} 银行 × {memory['words_per_bank']} 字 × {memory['bits_per_word']} bit",
               x, 538, 12.6, "HanBold")
    bw, bh = stats["bank_max_width_um"], stats["bank_max_height_um"]
    sheet.text(f"单银行尺寸上限  {bw:g} × {bh:g} µm", x, 560, 10.7)
    margin, gap = memory['outer_margin'], memory['bank_channel']
    sheet.text(f"窗口外边距 {margin} µm  ·  井间通道 {gap} µm", x, 580, 10.7)
    sheet.text("宏尺寸与电源边界待工艺宏确认。", x, 608, 10.1, color=MUTED)


def plan_page(sheet, plan, banks, stats):
    sheet.header("整芯片 Floorplan", "按规划坐标绘制转换 IP（功能组·已有）、片上系统外围和模拟分区")
    frame = PlanFrame(plan["die"], 91, 104, 526)
    # 绘制顺序使网格落在未分配区域，覆盖于最上层的端口把边缘位置带到最终图。
    map_grid(sheet, frame, plan)
    part_windows(sheet, frame, plan, banks, stats)
    markers(sheet, frame, plan)
    plan_sidebar(sheet, plan, stats)
    sheet.footer("规划坐标以裸片左下角为原点，单位 µm。几何依据 floorplan/plan.json。", 1)


# =============================================================================
# 3. 像素量测图与独立的微米面积账
# =============================================================================
def photo_panel(sheet, plan, path):
    photo = plan["photo"]
    img = ImageReader(str(path))
    pw, ph = photo["width"], photo["height"]
    # 手读像素框绑定原始画幅。尺寸、内容任一偏离即中止，避免在缩放副本上错位标注。
    if img.getSize() != (pw, ph) or hashlib.sha256(path.read_bytes()).hexdigest() != photo["sha256"]:
        raise ValueError("裸片图的尺寸或 SHA-256 与规划引用不一致。")

    # 加框在 PDF 矢量层完成。位图整幅保持原比例，水印及原注保留。
    x, y, w = 83, 132, 508
    k = w / pw
    h = k * ph
    sheet.pdf.drawImage(img, x, HEIGHT - y - h, width=w, height=h)
    sheet.box(x, y, w, h, stroke=INK, width=1.1)
    parts = {p["id"]: p["group"] for p in plan["partitions"]}
    for key, (a, b, c, d) in photo["boxes"].items():
        stroke = HexColor(STYLE[parts[key]][1])
        bounds = (x + a * k, y + b * k, (c - a) * k, (d - b) * k)
        # 亮色衬边保证暗色照片上的对比度，空心框保留框内原像素和原注。
        sheet.box(*bounds, stroke=white, width=2.25)
        sheet.box(*bounds, stroke=stroke, width=0.8)

    # 图像 y 轴向下；刻度包含画幅边界，位置由读得的像素宽高决定。
    ax, ay = x - 10, y - 10
    sheet.arrow((x, ay), (x + w + 6, ay))
    sheet.arrow((ax, y), (ax, y + h + 11))
    sheet.text("x", x + w + 10, ay + 3, 9.3, "LatinBold", MUTED)
    sheet.text("y", ax - 7, y + h + 14, 9.3, "LatinBold", MUTED, "right")
    for tick in (0, 375, 750, pw):
        # 末端刻度是画幅外边界。像素框按半开区间计算宽高。
        tx = x + tick * k
        sheet.line((tx, ay - 3), (tx, ay + 2), MUTED, 0.55)
        sheet.text(str(tick), tx, ay - 7, 9.1, "Latin", MUTED, "center")
    for tick in (0, 375, 750, ph):
        ty = y + tick * k
        sheet.line((ax - 3, ty), (ax + 2, ty), MUTED, 0.55)
        sheet.text(str(tick), ax - 6, ty + 3.1, 9.1, "Latin", MUTED, "right")
    sheet.text(f"图源 IPBrain（用户提供）  ·  画幅 {pw} × {ph} px", x, 658, 10.2, color=MUTED)


def photo_measurements(sheet, plan):
    x, right = 628, 894
    photo = plan["photo"]
    labels = {"DIO": "数据接口", "FIFO": "结果 SRAM", "OTP": "OTP", "SR2": "小 SRAM"}
    parts = {p["id"]: p["group"] for p in plan["partitions"]}
    sheet.text("图像标注框", x, 120, 14.5, "HanBold")
    sheet.text("读数从画幅左上起算，使用 px。", x, 141, 10.6, color=MUTED)
    sheet.text("分区", x, 163, 9.7, "HanBold", MUTED)
    sheet.text("框的左上角  →  右下角", x + 82, 163, 9.7, "HanBold", MUTED)
    sheet.text("画幅占比", right, 163, 9.7, "HanBold", MUTED, "right")
    # 分母取原始图尺寸乘积，和 1500 × 1500 µm 规划区的面积账各自独立。
    area = photo["width"] * photo["height"]
    for i, (key, (a, b, c, d)) in enumerate(photo["boxes"].items()):
        y = 188 + 25 * i
        fill, stroke = map(HexColor, STYLE[parts[key]])
        sheet.line((x, y - 15), (right, y - 15))
        sheet.box(x, y - 7.8, 8.4, 8.4, fill=fill, stroke=stroke, width=0.85)
        sheet.text(labels[key], x + 15, y, 10.6)
        sheet.text(f"({a},{b}) → ({c},{d})", x + 82, y, 8.9, "Mono")
        # 百分位用于比较分区尺度，读数人工误差由表下注记表达。
        sheet.text(f"{100 * (c - a) * (d - b) / area:.2f}%", right, y,
                   11.1, "LatinBold", INK, "right")
    sheet.line((x, 277), (right, 277))
    sheet.text(f"彩色标注框边界手读约 ±{photo['uncertainty_px']} px。", x, 297, 10.3, color=MUTED)
    sheet.text("芯片实体边界与宏的物理尺寸待量测。", x, 314, 10.3, color=MUTED)


def ip_budget(sheet, plan, stats, windows):
    x, right = 628, 894
    # 规模层级为毛面积、净区、占用上限、综合目标。数值统一在制图阶段从 µm² 换至 mm²。
    area = {key: val / 1_000_000 for key, val in stats.items() if key.startswith("ip_")}
    utilization = 100 * plan["ip_utilization_max"]
    sheet.text("转换 IP 面积预算", x, 352, 14.5, "HanBold")
    sheet.text("mm²", right, 352, 11.0, "LatinBold", MUTED, "right")
    rows = [
        ("分区面积", "ip_gross_um2", False, INK),
        ("扣除标准单元禁放带", "ip_no_rows_um2", True, HATCH_INK),
        ("净可布面积", "ip_net_rows_um2", False, INK),
        (f"终局单元面积上限（净区 {utilization:g}%）", "ip_final_cells_max_um2", False, INK),
        ("综合目标（CTS 前）", "ip_pre_cts_target_um2", False, HexColor("#265B8D")),
        ("CTS / ECO 面积余量", "ip_cts_eco_reserve_um2", False, HexColor("#265B8D")),
    ]
    for i, (label, key, deduct, ink) in enumerate(rows):
        # 禁放带作为减项显示；CTS/ECO 仍为正的面积预算差额，便于逐行复算。
        y = 378 + 25 * i
        sheet.line((x, y - 15), (right, y - 15))
        sheet.text(label, x, y, 10.7, "HanBold" if i in {2, 3} else "Han", ink)
        amount = f"{'−' if deduct else ''}{area[key]:.3f}"
        sheet.text(amount, right, y, 12, "LatinBold", ink, "right")

    # 堆叠条以毛面积为分母：净区余白和两条禁放带不能计入 CTS/ECO 余量。
    # 沿用主图禁放带斜纹；白蓝区展示行内余白，可观察两个概念在预算上的位置。
    net = area["ip_net_rows_um2"]
    final = area["ip_final_cells_max_um2"]
    segments = [
        ("综合目标", area["ip_pre_cts_target_um2"], HexColor("#2E6295")),
        ("CTS / ECO 余量", area["ip_cts_eco_reserve_um2"], HexColor("#81ADD7")),
        ("净区余白", net - final, HexColor("#DFEBF8")),
        ("标准单元禁放", area["ip_no_rows_um2"], HATCH_FILL),
    ]
    offset = x
    for i, (_, val, fill) in enumerate(segments):
        length = (right - x) * val / area["ip_gross_um2"]
        if i == 3:
            sheet.hatched(offset, 527, length, 14)
        else:
            sheet.box(offset, 527, length, 14, fill=fill, stroke=white, width=0.27)
        offset += length
    sheet.box(x, 527, right - x, 14, stroke=RING_EDGE, width=0.65)
    # 预留段只有毛面积的 6%。各段文字置于条形下方，使比例尺保持真实。
    for i, (label, val, fill) in enumerate(segments):
        tx, ty = x + 137 * (i % 2), 563 + 21 * (i // 2)
        if i == 3:
            sheet.hatched(tx, ty - 7.5, 8.5, 8.5)
        else:
            sheet.box(tx, ty - 7.5, 8.5, 8.5, fill=fill, stroke=RING_EDGE, width=0.25)
        sheet.text(f"{label} {val:.3f}", tx + 14, ty, 9.3)
    # 宏窗口来自整片规划几何。三处外围和两处预留分项列出，均在转换 IP 围栏之外。
    peripheral = sum(windows[key] for key in ("CTRL", "DIO", "FIFO"))
    reserves = sum(windows[key] for key in ("SR2", "OTP"))
    sheet.text("系统外围与独立预留窗口", x, 612, 11.8, "HanBold")
    sheet.text("mm²", right, 612, 9.7, "LatinBold", MUTED, "right")
    parts = "   ".join(f"{key} {windows[key]:.6f}" for key in ("CTRL", "DIO", "FIFO"))
    sheet.text(parts, x, 633, 9.5, "Latin", MUTED)
    sheet.text(f"三窗 {peripheral:.6f} + SR2/OTP {reserves:.6f} = {peripheral + reserves:.6f}",
               x, 652, 9.7, color=MUTED)


def evidence_page(sheet, plan, stats, windows, path):
    sheet.header("商品裸片量测与规划面积预算", "IPBrain 彩色标注框按画幅像素量测。转换 IP 及外围窗口按本方案的微米坐标计算。")
    # 量测和预算并列，轴与单位各随对应数据，方便独立截图或重用一侧。
    sheet.line((611, 104), (611, 642))
    photo_panel(sheet, plan, path)
    photo_measurements(sheet, plan)
    ip_budget(sheet, plan, stats, windows)
    sheet.footer("标注框读数取自原始画幅。预算依据 evidence/geometry.json 和 floorplan/plan.json。", 2)


# =============================================================================
# 4. ISSCC 讲稿功能图与带标尺的裸片图
# =============================================================================
def slide_clip(sheet, slides, number, box, x, y, width):
    """从讲稿指定物理页截取 bbox。box 为左上原点的 PDF 点坐标。"""
    # 取图时同时保留各自所需的边框、图内标签与箭头。未使用的讲稿页面留在原资料。
    dpi = 240
    a, b, c, d = (round(coord * dpi / 72) for coord in box)
    command = ["pdftoppm", "-f", str(number), "-l", str(number), "-r", str(dpi),
               "-x", str(a), "-y", str(b), "-W", str(c - a), "-H", str(d - b),
               "-png", "-singlefile", str(slides)]
    # 通过标准输出读取 PNG，图像生成失败会保护现有 PDF，目录里只保留正式图版。
    png = subprocess.run(command, check=True, stdout=subprocess.PIPE).stdout
    img = ImageReader(BytesIO(png))
    w, h = img.getSize()
    height = width * h / w
    sheet.pdf.drawImage(img, x, HEIGHT - y - height, width=width, height=height)


def lecture_area(sheet, measure):
    # 讲稿第 40 页原生矢量读数，单位 pt。Digital 取主红框线中心；标尺取箭尖间距。
    frame_pt = measure["frame_size_pt"]
    ruler = [measure["x_arrow"], measure["y_arrow"]]
    arrows_pt = [item["span_pt"] for item in ruler]
    scale_um = [item["label_um"] for item in ruler]
    w_um, h_um = (extent * um / span for extent, um, span in zip(frame_pt, scale_um, arrows_pt, strict=True))
    estimate = w_um * h_um / 1_000_000
    # 敏感性和两项扰动幅度同取读数审计文件，保持图例与数值证据同步。
    sensitivity = measure["sensitivity_mm2"]
    lo, hi = sensitivity["combined_box_and_arrow_shift"]
    box_shift, tip_shift = sensitivity["box_edge_shift_pt_each"], sensitivity["arrow_tip_shift_pt_each"]
    # 水平和纵向比例各自计算，保留 PDF 画幅中两个箭头的独立伸缩关系。
    x, right = 518, 893
    sheet.line((x, 444), (right, 444))
    sheet.text("Digital 红框的比例尺估算", x, 466, 13.2, "HanBold")
    sheet.text("图面读数单位  pt", right, 466, 9.7, color=MUTED, align="right")
    sheet.text(f"宽  {frame_pt[0]:.2f} × ({scale_um[0]} / {arrows_pt[0]:.2f}) = {w_um:.2f} µm", x, 488, 11.1)
    sheet.text(f"高  {frame_pt[1]:.2f} × ({scale_um[1]} / {arrows_pt[1]:.2f}) = {h_um:.2f} µm", x, 508, 11.1)
    sheet.text("红框主矩形面积", x, 539, 12.0, "HanBold")
    sheet.text(f"≈ {estimate:.3f} mm²", right, 540, 21.6, "HanBold", PORT, "right")
    sheet.text(f"分别扰动每条框边 ±{box_shift:g} pt 和每个箭尖 ±{tip_shift:g} pt，", x, 561, 10.5, color=MUTED)
    sheet.text(f"面积范围为 {lo:.3f} 至 {hi:.3f} mm²。", x, 578, 10.5, color=MUTED)
    sheet.line((x, 594), (right, 594))
    # p.17 支持功能归属，p.40 只标总区域。面积图留给实际子区证据单独填写。
    sheet.text("红框仅标 Digital 总区。DEM 和 dither 的单独面积", x, 617, 10.8)
    sheet.text("及其在框内的位置，公开讲稿未给出。", x, 635, 10.8)


def lecture_page(sheet, slides, measurement):
    # 哈希绑定讲稿版本，两处裁切和读数均以该文件的原始页坐标为准。
    if hashlib.sha256(slides.read_bytes()).hexdigest() != measurement["slides_source"]["sha256"]:
        raise ValueError("ISSCC 讲稿 SHA-256 与本图读数依据不一致。")

    die_page = measurement["slide_40"]["page_pdf_1_based"]
    sheet.header("ISSCC 原型的转换功能与面积证据", "ISSCC 2024 论文 9.8 的 40 nm ULP 原型讲稿与图面估算")
    sheet.line((498, 103), (498, 648))
    sheet.text(f"裸片照片与比例尺  ·  讲稿第 {die_page} 页", 48, 113, 12.5, "HanBold")
    slide_clip(sheet, slides, die_page, (172, 89, 595, 500), 48, 124, 432)
    # 两个面积由作者直接列出。原图的 1050 µm 与 820 µm 箭头保留在照片周边。
    sheet.text("讲稿作者标注", 48, 568, 12.3, "HanBold")
    reported = measurement["reported_areas_mm2"]
    for key, label, x in (("slide_core", "核心面积", 48), ("slide_core_plus_refbuf", "核心含参考缓冲", 260)):
        sheet.text(f"{reported[key]:.2f} mm²", x, 604, 23.2, "LatinBold")
        sheet.text(label, x, 624, 11.1, color=MUTED)
    sheet.text("对应论文图 9.8.7。这里使用讲稿保留标尺的版本。", 48, 648, 9.9, color=MUTED)

    sheet.text("DEM 与 dither 路径  ·  讲稿第 17 页", 518, 113, 12.5, "HanBold")
    slide_clip(sheet, slides, 17, (547, 145, 924, 405), 518, 124, 376)
    # 保留控制路径与模拟元件的功能边界，防止据控制图给参考/扰动开关分配数字面积。
    sheet.text("DEM 选择 RDAC 元件，dither 路径与 SADC / RDAC 相接。", 518, 407, 11.0)
    sheet.text("采样、参考切换和扰动注入的实体开关归相应模拟宏。", 518, 426, 11.0)
    lecture_area(sheet, measurement["slide_40"])
    sheet.footer("Rares Bodnar 等  ·  ISSCC 2024 论文 9.8  ·  DOI 10.1109/ISSCC49657.2024.10454329  ·  © 2024 IEEE", 3)


# =============================================================================
# 5. 导出与一致性检查
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--photo", type=Path, default=PHOTO)
    checkout = os.environ.get("ADC_FUNCTION_REPO")
    source = Path(checkout) / "references" / "ISSCC2024_SLIDES.pdf" if checkout else None
    parser.add_argument("--slides", type=Path, default=source, help="原作者 ISSCC2024_SLIDES.pdf")
    args = parser.parse_args()
    if args.slides is None:
        parser.error("请用 --slides 指定讲稿，或用 ADC_FUNCTION_REPO 指定含该原件的检出。")
    # 以脚本目录定位项目，调用者可以从其它工作路径复生成图版。
    plan = json.loads((ROOT / "floorplan" / "plan.json").read_text(encoding="utf-8"))
    stats = json.loads((ROOT / "evidence" / "geometry.json").read_text(encoding="utf-8"))
    measurement = json.loads((ROOT / "evidence" / "isscc_digital_area.json").read_text(encoding="utf-8"))
    # run_path 使用不同模块名载入函数，避免触发构建器 main 或修改交换文件。
    build = runpy.run_path(str(ROOT / "floorplan" / "build.py"))
    # 对照当前计划重新算一遍面积账。旧几何和新坐标混用会立即报错并保留现有图版。
    if build["check"](plan) != stats:
        raise ValueError("规划坐标和面积证据已分歧，请先同步 floorplan 构建结果。")
    banks = build["banks"](plan)
    # 外围附注复用构建器的矩形面积函数。转换 IP 预算仍直接引用 geometry.json 的净行结果。
    windows = {part["id"]: sum(build["area"](r) for r in part["rects"]) / 1_000_000
               for part in plan["partitions"]}

    register_fonts()
    pdf_path = HERE / "floorplan.pdf"
    # 固定文档元数据时基。同一输入和字体可生成可逐字节核对的 PDF。
    pdf = canvas.Canvas(str(pdf_path), pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    pdf.setTitle("AD4080 整芯片 Floorplan 与转换 IP 面积证据")
    pdf.setSubject("整芯片规划、转换 IP、IPBrain 商品裸片与 ISSCC 原型对照")
    pdf.setCreator("render_floorplan.py")
    sheet = Sheet(pdf)
    # 书签将首图与证据页对齐，引用独立页时保留对应的页面顺序。
    pdf.bookmarkPage("floorplan")
    pdf.addOutlineEntry("整芯片 Floorplan", "floorplan")
    plan_page(sheet, plan, banks, stats)
    pdf.showPage()
    pdf.bookmarkPage("evidence")
    pdf.addOutlineEntry("商品裸片量测与规划面积预算", "evidence")
    evidence_page(sheet, plan, stats, windows, args.photo)
    pdf.showPage()
    pdf.bookmarkPage("isscc")
    pdf.addOutlineEntry("ISSCC 原型的转换功能与面积证据", "isscc")
    lecture_page(sheet, args.slides, measurement)
    # 三页都构建成功后才写文件。源图校验失败时仍能保留之前的 PDF 和 PNG。
    pdf.save()

    # 从同一页矢量 PDF 光栅化，主图 PNG 与 PDF 无需分别排版或复核尺寸。
    subprocess.run(["pdftoppm", "-png", "-r", "300", "-f", "1", "-l", "1", "-singlefile",
                    str(pdf_path), str(HERE / "floorplan")], check=True)


if __name__ == "__main__":
    main()
