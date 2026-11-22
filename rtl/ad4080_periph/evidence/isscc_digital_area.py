# =============================================================================
# 文件名     : isscc_digital_area.py
# 模块名     : isscc_digital_area
# 版本       : v1.0
# 功能       : 从 ISSCC 9.8 原件复算讲稿的 Digital 标注区面积。
# 设计说明   :
#   * 讲稿第 40 页在嵌入照片上叠加红色矢量路径。定位各边中心线，
#     用矢量箭尖在 x、y 两轴上分别换算。探针只选择目视核定的路径。
#   * 论文 Fig. 9.8.7 中的框是照片栅格的一部分。检测红色像素只用于
#     复核形状和归属，物理尺寸采用讲稿标尺。
#   * 输出数值及输入哈希，读原 PDF，不提取或复制原图到交付。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import hashlib
import json
from pathlib import Path

import fitz


RED = (1.0, 0.0, 0.0)
SLIDE_PAGE = 40
PAPER_FIGURE_PDF_PAGE = 3
UM2_PER_MM2 = 1_000_000


# =============================================================================
# 1. 讲稿矢量框与箭头
# =============================================================================
def one(items, description):
    if len(items) != 1:
        raise ValueError(f"{description} 提取到 {len(items)} 条候选，须重新定位原件")
    return items[0]


def line_coordinate(segment, axis, along):
    """返回沿轴正交探针扫描得到的中心线坐标，所有参数与结果使用 PDF pt。"""
    start, end = segment
    other = 1 - axis
    return start[axis] + (end[axis] - start[axis]) * (along - start[other]) / (
        end[other] - start[other]
    )


def stroke_at(paths, axis, probe, width):
    # 探针给的是图上某条边附近的位置。用路径原生端点，避免按照探针坐标计算面积。
    other = 1 - axis
    matches = []
    for path in paths:
        if path["color"] != RED or path["width"] != width or len(path["items"]) != 1:
            continue
        verb, *points = path["items"][0]
        if verb != "l":
            continue
        start, end = points
        if abs(start[axis] - end[axis]) > 1:
            continue
        if min(start[other], end[other]) < probe[other] < max(start[other], end[other]):
            coord = line_coordinate(points, axis, probe[other])
            if abs(coord - probe[axis]) < 2:
                matches.append([tuple(point) for point in points])
    return one(matches, f"红色笔划探针 {probe}")


def arrow_tips(paths, motion_axis, shaft_coord):
    # 箭杆中心线比尖端短。只选和箭杆对正的两个实填红三角形，取最外侧矢量顶点。
    across = 1 - motion_axis
    triangles = []
    for path in paths:
        if path["fill"] != RED or len(path["items"]) != 3:
            continue
        points = [point for verb, *edge in path["items"] if verb == "l" for point in edge]
        if len(points) == 6 and abs(path["rect"].tl[across] + path["rect"].br[across]
                                    - 2 * shaft_coord) < 2:
            triangles.append(points)
    if len(triangles) != 2:
        raise ValueError("标尺箭尖未找到一对红色三角形")
    tips = [point for triangle in triangles for point in triangle]
    tips.sort(key=lambda point: point[motion_axis])
    return [tuple(tips[0]), tuple(tips[-1])]


def tolerances(spans, arrows, labels, edge_pt, tip_pt):
    """假设框每边和箭尖每端分别偏移指定 pt，返回相反极值组合的 mm² 范围。"""
    # 同向缩短两条框边的间距会压低面积，放长对应箭头也会压低换算尺度。
    # 这里固定讲稿标注的物理长度，区间含义是最不利取点组合。
    ranges = []
    for direction in (-1, 1):
        width, height = (span + direction * 2 * edge_pt for span in spans)
        x_len, y_len = (length - direction * 2 * tip_pt for length in arrows)
        ranges.append(width * height * labels[0] * labels[1] / (x_len * y_len * UM2_PER_MM2))
    return ranges


def measure_slide(page):
    text = page.get_text()
    if not all(label in text for label in ("Die Micrograph", "Digital", "1050µm", "820µm")):
        raise ValueError("第 40 页与选定讲稿的图名或标尺文字不符")
    paths = page.get_drawings()
    probes = {
        "left": (0, (256.5, 178)),
        "right": (0, (293.9, 240)),
        "top": (1, (273, 138.5)),
        "bottom": (1, (273, 267.8)),
        "right_upper_slope": (0, (294.2, 166)),
    }
    # 左边为 Digital 的独立界线，底边与 Quantizer 共边。右边下段垂直，
    # 取其 x 为近似矩形的基准，并单独估计右上段微斜对面积的影响。
    strokes = {key: stroke_at(paths, axis, probe, 3.0) for key, (axis, probe) in probes.items()}
    centerlines = {key: line_coordinate(strokes[key], axis, probe[1 - axis])
                   for key, (axis, probe) in probes.items()}
    x0, y0, x1, y1 = (centerlines[side] for side in ("left", "top", "right", "bottom"))
    x_shaft = stroke_at(paths, 1, (380, 441.2), 2.0)
    y_shaft = stroke_at(paths, 0, (559, 325), 2.0)
    horizontal = arrow_tips(paths, 0, x_shaft[0][1])
    vertical = arrow_tips(paths, 1, y_shaft[0][0])
    arrow_lengths = (horizontal[1][0] - horizontal[0][0], vertical[1][1] - vertical[0][1])
    labels = (1050, 820)  # 讲稿标注的 x/y 轴 µm 尺度
    sx, sy = (size / length for size, length in zip(labels, arrow_lengths))
    spans = (x1 - x0, y1 - y0)
    # 两轴缩尺相差约 13%，共用一把标尺会把照片拉伸误计为占地。
    width, height = spans[0] * sx, spans[1] * sy
    area = width * height / UM2_PER_MM2

    slope = strokes["right_upper_slope"]
    slope_y_end = max(point[1] for point in slope)
    dx_top = line_coordinate(slope, 0, y0) - x1
    dx_end = line_coordinate(slope, 0, slope_y_end) - x1
    slant_extra_area = (dx_top + dx_end) * (slope_y_end - y0) / 2 * sx * sy / UM2_PER_MM2
    photo = one(page.get_image_info(), "讲稿照片")
    im_box = fitz.Rect(photo["bbox"])
    return {
        "page_pdf_1_based": SLIDE_PAGE,
        "coordinates": "PyMuPDF pt，页左上角为原点，x 向右，y 向下",
        "frame_centerline_box_pt": [x0, y0, x1, y1],
        "frame_strokes_pt": strokes,
        "frame_size_pt": spans,
        "frame_stroke_width_pt": 3.0,
        "x_arrow": {"tips_pt": horizontal, "span_pt": arrow_lengths[0], "label_um": labels[0]},
        "y_arrow": {"tips_pt": vertical, "span_pt": arrow_lengths[1], "label_um": labels[1]},
        "scale_um_per_pt": [sx, sy],
        "frame_size_um": [width, height],
        "centerline_area_mm2": area,
        "slanted_right_segment_extra_mm2": slant_extra_area,
        "photo_bbox_pt": tuple(im_box),
        "frame_fraction_of_photo_xy": [spans[0] / im_box.width, spans[1] / im_box.height],
        "sensitivity_mm2": {
            "box_edge_shift_pt_each": 1.5,
            "arrow_tip_shift_pt_each": 1.0,
            "box_shift_only": tolerances(spans, arrow_lengths, labels, 1.5, 0.0),
            "combined_box_and_arrow_shift": tolerances(spans, arrow_lengths, labels, 1.5, 1.0),
            "method": "框线半线宽与标尺尖端的取点灵敏度，物理标签按原图标注值",
        },
    }


# =============================================================================
# 2. 论文 Fig. 9.8.7 的栅格形状交叉核对
# =============================================================================
def red_run(rgb, stride, width, coords):
    """从论文内嵌照片取一维扫描的连续红色像素，返回扫描索引的首末像素。"""
    hits = []
    for index, (x, y) in enumerate(coords):
        offset = (y * width + x) * stride
        r, g, b = rgb[offset:offset + 3]
        if r >= 176 and max(g, b) <= 95 and r - max(g, b) >= 100:
            hits.append(index)
    if not hits or hits[-1] - hits[0] + 1 != len(hits):
        raise ValueError("论文数字区框线的选定扫描窗无连续红色笔划")
    return hits[0], hits[-1]


def measure_paper(doc):
    page = doc[PAPER_FIGURE_PDF_PAGE - 1]
    if not page.search_for("Figure 9.8.7"):
        raise ValueError("论文第 3 个 PDF 页缺 Fig. 9.8.7")
    photo = one(page.get_image_info(xrefs=True), "论文 Fig. 9.8.7 照片")
    pix = fitz.Pixmap(doc, photo["xref"])
    # 栅格中标签文字会盖住红线。图中 x=151、y=150 的短窗避开文字和其他分区。
    scans = {
        "left": (0, 150, 108, 141),
        "right": (0, 150, 176, 207),
        "top": (1, 151, 75, 109),
        "bottom": (1, 151, 312, 336),
    }
    samples = pix.samples
    runs = {}
    for edge, (vary, constant, lo, hi) in scans.items():
        coords = [(i, constant) if vary == 0 else (constant, i) for i in range(lo, hi)]
        runs[edge] = [lo + index for index in red_run(samples, pix.n, pix.width, coords)]
    left, right, top, bottom = [sum(runs[edge]) / 2 for edge in scans]
    return {
        "figure": "9.8.7",
        "page_pdf_1_based": PAPER_FIGURE_PDF_PAGE,
        "photo_size_px": [pix.width, pix.height],
        "red_runs_px": runs,
        "frame_centerline_px": [left, top, right, bottom],
        "frame_fraction_of_photo_xy": [(right - left) / pix.width, (bottom - top) / pix.height],
        "fig_absolute_scale_um": None,
        "use": "红框位置、边界及狭长形状交叉核对",
    }


# =============================================================================
# 3. 可复算结果与功能页证据
# =============================================================================
def source(path):
    return {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description="复算 ISSCC 9.8 讲稿原型 Digital 区域面积")
    parser.add_argument("slides", type=Path, help="ISSCC2024_SLIDES.pdf 原件")
    parser.add_argument("paper", type=Path, help="ISSCC2024_PAPER.pdf 原件")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    with fitz.open(args.slides) as slide_doc, fitz.open(args.paper) as paper_doc:
        picture = measure_slide(slide_doc[SLIDE_PAGE - 1])
        crosscheck = measure_paper(paper_doc)
    # 机制结论来自图文共同核对。图上版图占地、功能数值和作者披露面积分别保留字段。
    result = {
        "scope": "ISSCC 2024 9.8 原型的 Digital 标注区图上面积",
        "slides_source": source(args.slides),
        "paper_source": source(args.paper),
        "slide_40": picture,
        "paper_fig_9_8_7": crosscheck,
        "reported_areas_mm2": {"paper_die": 2.25, "paper_adc_core": 0.9,
                               "slide_core": 0.77, "slide_core_plus_refbuf": 0.9},
        "reviewed_function_evidence": [
            {"source": "讲稿第 17–18 页", "finding": "RDAC 分段、DEM、上下段扰动路径。第 18 页定性提及较小 DAC 电平转换器的面积收益"},
            {"source": "讲稿第 19–22 页", "finding": "片上失调与增益校正、带宽及时钟匹配、10 ps 失配仿真与交错随机化"},
            {"source": "讲稿第 29–31 页", "finding": "8/18 片选择，横向 3 bit 轮转，纵向 3 bit 分段，第 31 页图示 50% 概率桥接"},
            {"source": "讲稿第 32 页", "finding": "INL/DNL 测量及输入建立、数字串扰、参考建立等限制"},
            {"source": "论文第 182–183 页和 Fig. 9.8.3", "finding": "3 bit 轮转、6 bit 二维 DEM、dither 从量化器转移至 RDAC 时范围增加 2 bit"},
        ],
        "separately_disclosed_area_mm2": {"DEM": None, "dither": None},
    }
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
