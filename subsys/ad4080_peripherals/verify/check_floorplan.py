# =============================================================================
# 文件名     : check_floorplan.py
# 模块名     : 规划几何独立验收
# 版本       : v1.0
# 功能       : 读回 GDS、LEF/DEF 并核对面积归属、数字软区和 SRAM 尺寸上限。
# 设计说明   : 以整数 DBU 比较坐标，GDS 用 gdstk，LEF/DEF 用 KLayout。
#              所计净面积为平面几何上限，真实行扣减还需工艺 SITE、Tap 和宏约束。
#              接口导引和禁放矩形没有布线层、PG 网络、端子或电气连通语义。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
from argparse import ArgumentParser
from collections import Counter
from copy import deepcopy
import csv
from decimal import Decimal
from hashlib import sha256
from itertools import combinations
import json
from math import isclose
from pathlib import Path
import re
from struct import unpack
from tempfile import TemporaryDirectory

import gdstk
import klayout
from klayout import db, lay


# =============================================================================
# 1. 独立工程门槛与统一坐标
# =============================================================================
DBU = 1000                             # DBU/µm，所有几何断言均在 1 nm 栅格上做。
SQUARE = DBU * DBU                     # DBU²/µm²，先比整数面积避免浮点边界问题。
# 这些门槛由交付任务定义。坐标表和交换文件被同步篡改时仍应用独立外框和上限。
# 软区轮廓的转折点保存在规划表，面积单元格由这里重新求并集。
TOP = "AD4080_AREA_STUDY"
DIE = (0, 0, 1500 * DBU, 1500 * DBU)
RING = 120 * DBU
PRE_CTS_MAX = 120_000 * SQUARE          # 0.120 mm²，编译后的实体单元面积目标。
FINAL_MAX = 135_000 * SQUARE            # 0.135 mm²，计入 CTS/ECO 后的实体单元面积上限。
SRAM_CONFIGURATION = (4, 4096, 20)     # 四银行，每银行 4096×20，全阵列共 16384 字。
SRAM_SEPARATION = 8 * DBU               # 外圈净空及井间通道的规划值，单位 DBU。
PARTITIONS = set("IP FIFO SR2 OTP DIO CTRL CLK RDAC Q RA ADC2 REF LDO1 LDO2 CAP".split())
GROUP_LAYER = {"logic": 10, "memory": 20, "nv": 22, "io": 30, "clock": 31,
               "analog": 40, "reference": 41, "power": 42}
# 两个软区的 GDS 选择器是库内实际图层号，LYP 只控制显示。
# 归属合同从工程边界单独列出，分区几何共用整数坐标，归属不能只依赖两张表互相一致。
SOFT_LAYERS = {"IP": 10, "CTRL": 11}
CONVERTER_SCOPE = "转换功能 IP"
PERIPHERAL_KNOWN = "产品外围已知功能窗"
PERIPHERAL_UNBOUND = "产品外围待实物绑定窗"
PERIPHERAL_TOTAL = "产品外围五窗"
PARTITION_SCOPES = {CONVERTER_SCOPE: ("IP",), PERIPHERAL_KNOWN: ("FIFO", "DIO", "CTRL"),
                    PERIPHERAL_UNBOUND: ("SR2", "OTP"), "共享时钟": ("CLK",),
                    "模拟转换与参考": ("RDAC", "Q", "RA", "ADC2", "REF"),
                    "供电与电容": ("LDO1", "LDO2", "CAP")}
# 通道相邻约束要求正长公共边，IO_EAST 还需到达 Pad 内沿。
# Q 回传与 RDAC 控制分配独立端口和通道。计数用于合同核对，布线需实际端子。
PORT_SIDES = {"CLK_RST": "west", "SADC_RDAC": "south", "ADC2_RSP": "south",
              "RESULT": "east", "CFG_TEST": "north", "Q_RETURN": "west"}
PORT_GUIDES = {"SADC_RDAC": "ADC_DIG", "ADC2_RSP": "ADC_DIG", "CFG_TEST": "SRAM_BUS", "Q_RETURN": "Q_SPUR_N"}
SIGNAL_CONTRACT = {"SADC_RDAC": ("RDAC", 522), "Q_RETURN": ("Q", 31)}
CHANNEL_PEERS = {"ADC_DIG": ("IP",), "SRAM_BUS": ("IP", "FIFO"), "REF_FEED": ("REF",),
                 "RESIDUE": ("RDAC", "RA"), "RA_ADC2": ("RA", "ADC2"), "IO_EAST": ("DIO",),
                 "Q_SPUR_N": ("IP",), "Q_SPUR_S": ("Q",)}
FILES = ("plan.json", "chip.gds", "chip.lyp", "chip.def", "planning.lef", "area.csv", "block_binding.csv", "area_by_scope.csv")


class CheckError(ValueError):
    """输入背离规划合同，调用方可读取断言类别和具体对象。"""


def require(condition, check, detail):
    if not condition:
        raise CheckError(f"[{check}] {detail}")


def on_grid(values):
    """将以 µm 表示的规划坐标换成整 DBU，拒绝隐含四舍五入。"""
    # Decimal 从十进制声明构造。把二进制浮点尾差带入换算会误拒正常网格值。
    scaled = tuple(Decimal(str(value)) * DBU for value in values)
    require(all(value.is_finite() and value == int(value) for value in scaled),
            "grid", f"坐标不在 1 nm 栅格内 {values}")
    return tuple(int(value) for value in scaled)


def um(box):
    return [value / DBU for value in box]


def rect(box):
    require(len(box) == 4 and box[0] < box[2] and box[1] < box[3],
            "rectangle", f"无效矩形 {box}")
    return db.Box(*box)


def coords(box):
    return (box.left, box.bottom, box.right, box.top)


def region(boxes):
    return db.Region([rect(box) for box in boxes])


def edge_components(box_region):
    # 只在有正长度公共边或正面积交叠时连通。True 单参数会选错 KLayout 重载。
    return list(box_region.merged(True, 0).each())


def inside(piece, envelope):
    # 矩形边缘可与包络重合。用面积差集判断，避免对公共边人为添加间隙。
    return (region([piece]) - region([envelope])).is_empty()


def shared_edge(first, second):
    # 两矩形严格不重叠后再测公共边，角点接触在此为零。
    x_shared = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    y_shared = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    if first[2] == second[0] or second[2] == first[0]:
        return y_shared
    if first[3] == second[1] or second[3] == first[1]:
        return x_shared
    return 0


# =============================================================================
# 2. 预算、禁放绕行、通道与端口几何
# =============================================================================
def check_ip(plan, soft_boxes, bands):
    """以 KLayout 区域布尔运算核对软区拓扑、扣减和 CTS/ECO 上界。"""
    gross = region(soft_boxes)
    blocked = region(bands)
    require(len(edge_components(gross)) == 1, "placement", "IP 软区必须边连通")
    require((blocked - gross).is_empty() and blocked.area() == sum(rect(b).area() for b in bands),
            "placement", "禁放带越界或重叠")
    # 检查真实禁放面积，不能只从计划填入一个 90% 的折减系数。
    # 先做整数等式再换算百分比，差一平方 DBU 也能被发现。
    require(10 * blocked.area() == gross.area(), "reserve", "禁放带总面积必须占 IP 的 10%")

    # 带宽与桥接宽度是连续多边形值。零宽或角点接触会断开放置几何，
    # 单元行的真实连续性还受 SITE 节距、Tap/Cut、PG 与列对齐限制。
    net = gross - blocked
    islands = edge_components(net)
    island_areas = sorted(polygon.area() / SQUARE for polygon in islands)
    require(len(islands) == 1, "placement", f"禁放带截断净可摆区，分岛面积 {island_areas} µm²")
    bypasses = []
    for band in bands:
        # 这份规划在每个矩形内部留一条横向禁放带。先确认归属再测左右旁路，
        # 防止把相邻上臂的空隙算作该禁放带自身的几何绕行宽度。
        parents = [box for box in soft_boxes if inside(band, box)]
        require(len(parents) == 1, "placement", f"禁放带跨越规划矩形边界 {um(band)}")
        left = band[0] - parents[0][0]
        right = parents[0][2] - band[2]
        require(left > 0 and right > 0, "placement", f"禁放带两侧需要正宽旁路 {um(band)}")
        bypasses.append({"band_um": um(band), "area_um2": rect(band).area() / SQUARE,
                         "left_um": left / DBU, "right_um": right / DBU})

    # 这里核对验收输入本身，防止调整 JSON 百分比或目标后仍然通过互相一致的文件。
    pre_cts = Decimal(str(plan["ip_pre_cts_target_um2"])) * SQUARE
    util = Decimal(str(plan["ip_utilization_max"]))
    require(pre_cts == PRE_CTS_MAX and util == Decimal("0.60"),
            "cell-budget", "pre-CTS 目标需为 0.120 mm²，最大利用率需为 60%")
    final = net.area() * util
    require(final == FINAL_MAX and pre_cts <= final,
            "cell-budget", f"计入禁放带与 60% 利用率后的闸门须为 0.135 mm²，实得 {final / SQUARE} µm²")
    return {"gross_um2": gross.area() / SQUARE, "row_exclusion_um2": blocked.area() / SQUARE,
            "row_exclusion_pct": 100 * blocked.area() / gross.area(), "net_rows_um2": net.area() / SQUARE,
            "net_placement_island_areas_um2": island_areas, "bypasses": bypasses,
            "narrowest_row_band_bypass_um": min(min(b["left_um"], b["right_um"]) for b in bypasses),
            "pre_cts_cell_max_um2": int(pre_cts / SQUARE), "final_cell_max_um2": int(final / SQUARE),
            "cts_eco_headroom_um2": int((final - pre_cts) / SQUARE)}


def bank_rectangles(window, spec):
    """按 FIFO 窗口求朝向 N、2×2 均一银行的最大宏外形，尺寸包含外围电路。"""
    require(tuple(spec[key] for key in ("banks", "words_per_bank", "bits_per_word")) == SRAM_CONFIGURATION,
            "sram-capacity", "SRAM 必须容纳四个 4096×20 bank")
    require(on_grid([spec["outer_margin"], spec["bank_channel"]]) == (SRAM_SEPARATION, SRAM_SEPARATION),
            "sram-separation", "SRAM 外圈和银行间需各保留 8 µm")
    interval = SRAM_SEPARATION
    left, bottom, right, top = window
    # 每一轴有两侧外边距和一个内部通道，共扣三个间隔。
    # DBU 除以二有余数时需先定离散宏尺寸或改窗口，不能让同步坐标隐式取整。
    width, width_remainder = divmod(right - left - 3 * interval, 2)
    height, height_remainder = divmod(top - bottom - 3 * interval, 2)
    require(width > 0 and height > 0 and width_remainder == height_remainder == 0,
            "sram-size", "FIFO 窗口扣除边距和通道后无法在栅格内放置等大 SRAM 银行")
    banks = []
    for row in range(2):
        for column in range(2):
            x = left + interval + column * (width + interval)
            y = bottom + interval + row * (height + interval)
            banks.append((x, y, x + width, y + height))
    bank_bits = spec["words_per_bank"] * spec["bits_per_word"]
    evidence = {"banks": len(banks), "words_per_bank": spec["words_per_bank"],
                "bits_per_word": spec["bits_per_word"], "words": spec["words_per_bank"] * len(banks),
                "bits": bank_bits * len(banks), "container_um": um(window),
                "bank_boxes_um": [um(bank) for bank in banks], "bank_max_width_um": width / DBU,
                "bank_max_height_um": height / DBU, "bank_max_area_um2": width * height / SQUARE,
                "bank_max_effective_um2_per_bit": width * height / (SQUARE * bank_bits),
                "outer_margin_um": interval / DBU, "bank_channel_um": interval / DBU}
    return banks, evidence


def check_ports(port_entries, boxes, bands, channels, occupied):
    # 端口在软区外露边上才算落边。使用内/外各 1 DBU 的细条，可拒绝把
    # 单个矩形的内部拼缝误当作 IP 边缘，也会发现与相邻宏立即冲突的端口。
    require(Counter(port["group"] for port in port_entries) == Counter(PORT_SIDES.keys()),
            "port", "接口组不全或重名")
    ip = region(boxes)
    ports = {}
    outward_strips = {}
    for port in port_entries:
        name, side = port["group"], port["side"]
        # 端口分线仍用独立合同计数。图元导出为整组矩形，少一根时矩形和面积均可保持。
        # 31 对应 16 根预置/使能和 15 根判决回传，522 根另归 RDAC。
        signal_count = port.get("signal_count")
        if name in SIGNAL_CONTRACT:
            require((port["peer"], signal_count) == SIGNAL_CONTRACT[name],
                    "signal-count", f"{name} 要求 (对端, 根数) {SIGNAL_CONTRACT[name]}，实得 {(port['peer'], signal_count)}")
        require(side == PORT_SIDES[name], "port", f"{name} 侧边不符合框图规划")
        x1, y1, x2, y2 = on_grid(port["line"])
        if side in {"west", "east"}:
            require(x1 == x2 and y1 < y2, "port", f"{name} 需为竖线")
            low, high = (x1 - 1, y1, x1, y2), (x1, y1, x1 + 1, y2)
        else:
            require(y1 == y2 and x1 < x2, "port", f"{name} 需为横线")
            low, high = (x1, y1 - 1, x2, y1), (x1, y1, x2, y1 + 1)
        inner, outer = (low, high) if side in {"east", "north"} else (high, low)
        require((region([inner]) - ip).is_empty() and (region([outer]) & occupied).is_empty(),
                "port", f"{name} 需完整落在 IP 外露边，外侧须有规划净空")
        require((region([inner]) & region(bands)).is_empty(), "port", f"{name} 与禁放带重叠")
        # 外侧一格细条的正面积交叠等价于共边接口的正长重合。
        # 检查真实段而非 GDS 的 1 µm 展宽，避免把图面宽度当成物理针脚规则。
        overlapping = [key for key, strip in outward_strips.items() if rect(outer).overlaps(rect(strip))]
        require(not overlapping, "port", f"{name} 与 {overlapping} 重复分配同一外露边段")
        outward_strips[name] = outer
        # 外侧细条必须落入指定通道全长。对 Q 回传来说，仅贴在 IP 的西边
        # 还可能落到相邻的时钟窗口，因此要明确核对 Q_SPUR_N。
        route = PORT_GUIDES.get(name)
        if route:
            require(inside(outer, channels[route]), "port", f"{name} 需落到 {route} 的边缘")
        if name == "Q_RETURN":
            # 两端各有几何余量。留数值门槛以防端口缩水但信号计数仍为 31。
            require((x1, y1, x2, y2) == on_grid([145, 904, 145, 938]), "port", "Q_RETURN 端口位置偏离 ECO")
        ports[name] = {"side": side, "line_um": um((x1, y1, x2, y2)),
                       "span_um": (x2 - x1 + y2 - y1) / DBU, "peer": port["peer"], "guide": route}
        if name in SIGNAL_CONTRACT:
            ports[name]["signal_count"] = signal_count
    return ports


def check_plan(plan):
    """建立与生成器无共享代码的 DBU 几何模型和验收摘要。"""
    require(plan["units"] == "um" and plan["dbu_per_micron"] == DBU, "grid", "规划须采用 µm 与 1000 DBU/µm")
    die, core = on_grid(plan["die"]), on_grid(plan["core"])
    x, y, u, v = die
    a, b, c, d = core
    require(die == DIE and on_grid([plan["pad_ring_width"]]) == (RING,) and
            core == (x + RING, y + RING, u - RING, v - RING), "outline", "1500×1500 µm 裸片及 120 µm Pad 环不符")
    # 上下两条包括四角，左右条从内圈底至内圈顶，保证角区只计一次。
    pad = [(x, y, u, b), (x, d, u, v), (x, b, a, d), (c, b, u, d)]
    parts = {part["id"]: [on_grid(box) for box in part["rects"]] for part in plan["partitions"]}
    require(Counter(part["id"] for part in plan["partitions"]) == Counter(PARTITIONS),
            "partitions", "功能分区缺漏或重名")
    soft_ids = {part["id"] for part in plan["partitions"] if part["kind"] == "soft"}
    require(soft_ids == {"IP", "CTRL"}, "partitions", "软区 ID 须为数字功能 IP 和数字外围 CTRL")
    channels = {channel["id"]: on_grid(channel["rect"]) for channel in plan["channels"]}
    require(Counter(channel["id"] for channel in plan["channels"]) == Counter(CHANNEL_PEERS.keys()),
            "channel", "规划通道 ID 缺漏或重名")

    # 片内实体窗和独占通道的正面积交叠会重复计算。IP 禁放带单独扣减，
    # 不可进此互斥集合。各窗保留各自 ID，出错时能直接指出冲突两端。
    all_boxes = [(name, box) for name, boxes in parts.items() for box in boxes]
    occupied = region([box for _, box in all_boxes])
    overlay_objects = all_boxes + list(channels.items())
    for name, box in overlay_objects:
        require(inside(box, core), "containment", f"{name} 越过 Pad 环前的片内边界 {um(box)}")
    for (first_name, first), (second_name, second) in combinations(overlay_objects, 2):
        require(not rect(first).overlaps(rect(second)), "overlap", f"{first_name} 与 {second_name} 重叠")
    require(len(edge_components(region(parts["CTRL"]))) == 1, "placement", "CTRL 软区必须边连通")
    bands = [on_grid(box) for box in plan["ip_row_blockages"]]
    ip = check_ip(plan, parts["IP"], bands)

    channel_metrics = {}
    for name, box in channels.items():
        peers = CHANNEL_PEERS[name]
        adjacency = {peer: sum(shared_edge(box, other) for other in parts[peer]) / DBU for peer in peers}
        require(all(adjacency.values()), "channel", f"{name} 未接到相邻分区 {adjacency}")
        channel_metrics[name] = {"box_um": um(box), "short_side_um": min(rect(box).width(), rect(box).height()) / DBU,
                                 "shared_edge_um": adjacency}
    # Q 回传从 IP 到 Q 宏经过两个矩形。上面的循环先验两端接窗；再核中间
    # 公共边的方向和长度，防止两个仍各自贴宏的矩形在中途有缝、只角点相接。
    q_north, q_south = channels["Q_SPUR_N"], channels["Q_SPUR_S"]
    q_join = shared_edge(q_north, q_south)
    require(q_north[1] == q_south[3] and q_join == 24 * DBU,
            "q-route", "Q_SPUR_N 与 Q_SPUR_S 必须以 24 µm 的公共边相接")
    # 按左右跨度核对纵行通道净宽，并冻结南北转折点，避免用相同面积掩盖改道。
    require(q_north == on_grid([121, 900, 145, 940]) and q_south == on_grid([121, 660, 146, 900]),
            "q-route", "Q 通道需保留 ECO 的绝对边界与北段 24 µm、南段 25 µm 净宽")
    channel_metrics["Q_SPUR_N"]["shared_edge_um"]["Q_SPUR_S"] = q_join / DBU

    # IO_EAST 必须从 DIO 东边直接出窗并到达 Pad 内沿，公共边已在循环核验。
    # 纵向重合但 x 有缝不能形成连续的预留几何。
    io_channel = channels["IO_EAST"]
    dio_window, = parts["DIO"]
    require(io_channel[2] == c and io_channel[0] == dio_window[2],
            "channel", "IO_EAST 须由 DIO 东侧出窗并到达内核东沿")
    ports = check_ports(plan["ports"], parts["IP"], bands, channels, occupied)
    fifo_window, = parts["FIFO"]
    banks, sram = bank_rectangles(fifo_window, plan["sram"])
    # 芯片总额里计整个 FIFO 容器。银行和井间通道已包含在其中，不再重复扣减。
    # 同理 IP 禁放带已包括在 IP 分区面积，只在 IP 单元预算局部再乘利用率。
    pad_area = region(pad).area()
    channel_area = region(channels.values()).area()
    remaining = rect(core).area() - occupied.area() - channel_area
    require(remaining >= 0, "budget", "片内未分配面积为负")

    summary = {"die_um": um(die), "core_um": um(core), "die_um2": rect(die).area() / SQUARE,
               "pad_ring_width_um": RING / DBU, "pad_ring_um2": pad_area / SQUARE,
               "inside_ring_um2": rect(core).area() / SQUARE, "allocated_um2": occupied.area() / SQUARE,
               "explicit_channels_um2": channel_area / SQUARE, "remaining_inside_ring_um2": remaining / SQUARE,
               "ip": ip, "sram": sram, "channels": channel_metrics, "interface_guides": ports,
               "narrowest_explicit_channel_um": min(ch["short_side_um"] for ch in channel_metrics.values())}
    model = {"parts": parts, "soft_ids": soft_ids, "banks": banks, "die": die, "pad": pad,
             "bands": bands, "channels": channels, "ports": ports, "summary": summary}
    return model


# =============================================================================
# 3. 用实际版图 reader 回读 DEF 和 GDS
# =============================================================================
def expect_boxes(actual, expected, category, name):
    # 多重集合会保留重复矩形。只做集合比对会把重复图层或多余物理对象吞掉。
    actual, expected = Counter(actual), Counter(expected)
    require(actual == expected, category,
            f"{name} 几何不符，缺失 {list((expected - actual).elements())}，多出 {list((actual - expected).elements())}")


def check_gds(path, plan, model):
    library = gdstk.read_gds(str(path))
    # 读回整幅坐标前先认定 user unit / precision，避免同形的纳米或米单位数据通过。
    require(isclose(library.unit, 1e-6, rel_tol=1e-12) and isclose(library.precision, 1e-9, rel_tol=1e-12),
            "gds", "GDS 给定长度单位须为 1 µm，DBU 须为 1 nm")
    require([cell.name for cell in library.cells] == [TOP], "gds", "规划流应有唯一顶层单元")
    cell = library.cells[0]
    require(not cell.references and not cell.paths, "gds", "规划 GDS 应为单层级矩形图形")

    def gds_dbu(value):
        # GDS real8 单位经二进制浮点读回后可能有微小尾差，允许 <10⁻⁶ DBU。
        nearest = round(value * DBU)
        require(abs(value * DBU - nearest) < 1e-6, "gds", f"非网格坐标 {value} µm")
        return nearest

    actual = []
    for polygon in cell.polygons:
        points = [tuple(gds_dbu(point) for point in vertex) for vertex in polygon.points]
        require(len(points) == 4 and polygon.datatype == 0, "gds", "发现非规划矩形或意外 datatype")
        xs, ys = zip(*points)
        box = (min(xs), min(ys), max(xs), max(ys))
        # 仅比较包围盒会放过蝴蝶结、自交或三角形，需同时核四角和连续正交边。
        require(len(set(xs)) == len(set(ys)) == 2 and len(set(points)) == 4 and
                all(left[0] == right[0] or left[1] == right[1]
                    for left, right in zip(points, points[1:] + points[:1])),
                "gds", f"图层 {polygon.layer} 图形不是非退化轴对齐矩形")
        actual.append((polygon.layer, box))

    # 期待图层按规划语义分别检查。跨层叠加 SRAM 容器和银行图形是有意的，
    # 因此改用联合读回的图层编号与绝对坐标比对，避免把容器和银行打平求和。
    expected = [(1, model["die"])] + [(2, box) for box in model["pad"]]
    names = {}
    for part in plan["partitions"]:
        # 规划表里的两块仍属同一逻辑族，仅对交换显示图层使用分区级编号。
        name = part["id"]
        layer = SOFT_LAYERS[name] if name in SOFT_LAYERS else GROUP_LAYER[part["group"]]
        for index, box in enumerate(model["parts"][name]):
            expected.append((layer, box))
            names[f"{name}_{index}"] = box
    for index, box in enumerate(model["banks"]):
        expected.append((21, box))
        names[f"BANK_{index}"] = box
    expected.extend((50, box) for box in model["bands"])
    expected.extend((51, box) for box in model["channels"].values())
    for port in model["ports"].values():
        x1, y1, x2, y2 = on_grid(port["line_um"])
        # 1 µm 展宽只供平面评审可视化，中心线才是边界位置。
        expected.append((52, (x1 - 500, y1 - 500, x2 + 500, y2 + 500)))
    expect_boxes(actual, expected, "gds", "规划图层 1–52")

    require(Counter(label.text for label in cell.labels) == Counter(names.keys()), "gds", "分区和银行标签不全或重名")
    for label in cell.labels:
        x1, y1, x2, y2 = names[label.text]
        x, y = map(gds_dbu, label.origin)
        # 文字锚点需落进正确的窗口，字体尺寸和可见性由版图查看器控制。
        require(label.layer == 90 and label.texttype == 0 and x1 < x < x2 and y1 < y < y2,
                "gds", f"标签 {label.text} 不在所属分区或规划标注层")
    return {"reader": f"gdstk {gdstk.__version__}", "top": cell.name, "dbu_um": 1 / DBU,
            "polygons": len(actual), "labels": len(cell.labels),
            "polygon_count_by_layer": dict(sorted(Counter(layer for layer, _ in actual).items()))}


def klayout_boxes(top, layer_index, category, name):
    # 原生导入可能按 box 或四角 polygon 存储矩形，均核对真实图形和绝对坐标。
    require(layer_index is not None, category, f"缺少 {name} 层")
    result = []
    for shape in top.shapes(layer_index).each():
        require(shape.is_box() or (shape.is_polygon() and shape.polygon.is_box()),
                category, f"{name} 层存在非矩形图形")
        result.append(coords(shape.bbox()))
    return result


def check_klayout_layers(gds_path, lyp_path, model):
    # Headless LayoutView 走 KLayout 原生 GDS reader 和 LYP 选择器。
    # 先测库内 10/0 与 11/0，再加载 LYP，避免空图层的显示项掩盖实际合层。
    view = lay.LayoutView(False)
    view_index = view.load_layout(str(gds_path))
    layout = view.cellview(view_index).layout()
    top = layout.top_cell()
    require(top is not None and top.name == TOP and isclose(layout.dbu, 1 / DBU, rel_tol=1e-12),
            "native-gds", "KLayout 读回的 GDS 顶层或 DBU 不符")
    # GDS 两个面图层各自读几何，文字层 90 独立于两条轮廓选择器。
    # 四个矩形合并面积正确也可能错层，因此这里逐层检验矩形集合。
    scopes = {}
    for key, layer in SOFT_LAYERS.items():
        source = f"{layer}/0"
        boxes = klayout_boxes(top, layout.find_layer(layer, 0), "native-gds", source)
        expect_boxes(boxes, model["parts"][key], "native-gds", f"{key} 的原生图层 {source}")
        scopes[key] = {"selector": source, "rects_um": [um(box) for box in boxes],
                       "gross_um2": region(boxes).area() / SQUARE}

    # LYP 中各选择器仅出现一次，以真实列表状态检验两种单独隐藏操作。
    # 操作只影响本次独立查看器，不改 GDS、LYP 或用户的显示状态。
    view.load_layer_props(str(lyp_path), False)
    # @1 指唯一加载的 CellView。显示名可调整，实际控制绑定依赖 layer/datatype。
    expected_sources = {f"{layer}/0@1": name for name, layer in SOFT_LAYERS.items()}
    actual = [item for item in view.each_layer() if item.source in expected_sources]
    require(Counter(item.source for item in actual) == Counter(expected_sources.keys()),
            "layer-visibility", "LYP 须提供独立的 10/0 与 11/0 开关")
    controls = {expected_sources[item.source]: item for item in actual}
    states = {"loaded": {name: item.visible for name, item in controls.items()}}
    require(all(states["loaded"].values()), "layer-visibility", "两个软区初始均应可见")
    for name, item in controls.items():
        # 每次试验都从两个可见开始，单独关闭本节点后读取另一个原生节点的状态。
        item.visible = False
        states[f"hide_{name}"] = {key: control.visible for key, control in controls.items()}
        require(states[f"hide_{name}"] == {key: key != name for key in controls},
                "layer-visibility", f"隐藏 {name} 时另一软区须保持可见")
        scopes[name]["layer_name"] = item.name
        item.visible = True
    return {"reader": f"KLayout {klayout.__version__} LayoutView", "scopes": scopes, "visibility": states}


def def_section(text, name):
    # KLayout 导入为几何数据库，会丢失 FENCE 的名字和 FIXED 状态等 DEF 语义。
    # 局部读这些声明与计数，矩形坐标仍以原生 reader 读回为准。
    section = re.findall(rf"(?ms)^{name}\s+(\d+)\s*;(.*?)^END\s+{name}\s*$", text)
    require(len(section) == 1, "def-metadata", f"{name} 段缺少或重复")
    declared, body = section[0]
    statements = [statement.strip() for statement in body.split(";") if statement.strip()]
    require(len(statements) == int(declared), "def-metadata", f"{name} 段实际数与声明不符")
    return statements


def check_def(def_path, lef_path, model):
    def_text = re.sub(r"(?m)#.*$", "", def_path.read_text(encoding="ascii"))
    lef_text = re.sub(r"(?m)#.*$", "", lef_path.read_text(encoding="ascii"))
    require(re.search(r"(?m)^UNITS DISTANCE MICRONS 1000\s*;", def_text) and
            re.search(r"(?m)^\s*DATABASE MICRONS 1000\s*;", lef_text),
            "def-metadata", "源文件中的 LEF/DEF 数据库单位须为 1000 DBU/µm")
    require(all(re.search(r"(?m)^VERSION 5\.8\s*;", data) for data in (def_text, lef_text)),
            "def-metadata", "规划文件须采用 LEF/DEF 5.8")
    require(re.search(r"(?m)^MANUFACTURINGGRID\s+0\.001\s*;", lef_text),
            "def-metadata", "规划 LEF 栅格需为 0.001 µm")

    # 只喂入交付的规划 LEF，避免挨着 DEF 的第三方库遮盖宏缺失。
    # KLayout 可以缩放输入 DBU，所以前面另查了 LEF/DEF 原始单位声明。
    options = db.LoadLayoutOptions()
    config = options.lefdef_config
    config.read_lef_with_def = False
    config.lef_files = [str(lef_path.resolve())]
    config.dbu = 1 / DBU
    config.produce_cell_outlines = True
    config.produce_regions = True
    config.produce_placement_blockages = True
    config.cell_outline_layer = "OUTLINE"
    config.region_layer = "REGIONS"
    config.placement_blockage_layer = "PLACEMENT_BLK"
    config.instance_property_name = "DEF_INSTANCE"
    options.lefdef_config = config
    layout = db.Layout()
    layout.read(str(def_path), options)
    top = layout.top_cell()
    require(top is not None and top.name == TOP and layout.dbu == 1 / DBU,
            "def", "KLayout 读回顶层名或 DBU 不符合规划")

    layer_names = {layout.get_info(index).name: index for index in layout.layer_indexes()}

    def layer_boxes(name):
        return klayout_boxes(top, layer_names.get(name), "def", name)

    # 宏边界是 DEF 的位移 / 朝向与 LEF 的原生宏盒联合得到的结果，
    # 即使文本声明完整，一个宏尺寸或放置方向变化也会反映在这里。
    expected_components = {}
    for name, boxes in model["parts"].items():
        if name in model["soft_ids"]:
            continue
        for index, box in enumerate(model["banks"] if name == "FIFO" else boxes):
            expected_components[f"inst_{name}_{index}"] = (f"WIN_{name}_{index}", box)
    components = def_section(def_text, "COMPONENTS")
    matches = [re.match(r"-\s+(\S+)\s+(\S+)\s+\+\s+FIXED\s+\(\s*-?\d+\s+-?\d+\s*\)\s+N$", entry)
               for entry in components]
    require(all(matches) and Counter(match[1] for match in matches) == Counter(expected_components.keys()),
            "def-metadata", "固定窗口 ID 或 FIXED N 声明不符")
    declared_macros = re.findall(r"(?m)^MACRO\s+(\S+)\s*$", lef_text)
    require(Counter(declared_macros) == Counter(macro for macro, _ in expected_components.values()),
            "def-metadata", "LEF 定义与固定窗口需要的宏不一致")
    for macro, box in expected_components.values():
        body = re.search(rf"(?ms)^MACRO\s+{macro}\s*\n(.*?)^END\s+{macro}\s*$", lef_text)
        require(body and re.search(r"\bCLASS\s+BLOCK\s*;", body[1]) and re.search(r"\bORIGIN\s+0\s+0\s*;", body[1]),
                "def-metadata", f"{macro} 要求 CLASS BLOCK 与零原点")
        # SIZE 有小数时 reader 可舍入到最近的整数 DBU。先检查原声明是否能精确存储。
        size = re.search(r"\bSIZE\s+([.\d]+)\s+BY\s+([.\d]+)\s*;", body[1])
        require(size and all(Decimal(n) * DBU == int(Decimal(n) * DBU) for n in size.groups()),
                "def-metadata", f"{macro} 的 LEF SIZE 未对齐 1 nm 栅格")
    for instance in top.each_inst():
        name = instance.property("DEF_INSTANCE")
        require(name in expected_components and not instance.is_regular_array(), "def", f"意外实例 {name}")
        tag = "sram-size" if name.startswith("inst_FIFO_") else "def"
        expected_macro, expected_box = expected_components.pop(name)
        require((instance.cell.name, coords(instance.bbox())) == (expected_macro, expected_box) and
                instance.trans.angle == 0 and not instance.trans.is_mirror(),
                tag, f"{name} LEF/DEF 联合读回 {coords(instance.bbox())}，规划 {expected_box}（DBU）")
    require(not expected_components, "def", f"缺少固定窗口 {list(expected_components)}")

    expect_boxes(layer_boxes("OUTLINE"), [model["die"]], "def", "DIEAREA")
    # KLayout 的 REGIONS 合集不足以判断两块软区对调的错误。逐项核源 FENCE 的 ID
    # 与归属，再与整个 REGIONS 原生读回比对。实例绑定需要未来的 GROUPS/REGION。
    region_specs = def_section(def_text, "REGIONS")
    declared_fences = [re.match(r"-\s+(\S+)\s+.+\+\s+TYPE\s+FENCE$", spec, flags=re.S) for spec in region_specs]
    require(all(declared_fences) and Counter(match[1] for match in declared_fences) == Counter(model["soft_ids"]),
            "def-metadata", "需要 IP 与 CTRL 两个 FENCE")
    fence_evidence = {}
    for declaration, match in zip(region_specs, declared_fences):
        numbers = [int(value) for value in re.findall(r"-?\d+", declaration[match.end(1):])]
        boxes = [tuple(numbers[offset:offset + 4]) for offset in range(0, len(numbers), 4)]
        expect_boxes(boxes, model["parts"][match[1]], "def", f"FENCE {match[1]}")
        fence_evidence[match[1]] = {"rects_um": [um(box) for box in boxes], "gross_um2": region(boxes).area() / SQUARE}
    soft_rects = [box for key in model["soft_ids"] for box in model["parts"][key]]
    expect_boxes(layer_boxes("REGIONS"), soft_rects, "def", "KLayout REGIONS")

    # FIFO 的剩余禁放图形可不同分片，但精确并集必须等于窗口减去四银行。
    # 其他禁放矩形应逐一对应，避免几何用途被另一段同面积矩形掩盖。
    declared_blockages = def_section(def_text, "BLOCKAGES")
    # KLayout 会将带 SOFT、PARTIAL 或 COMPONENT 修饰的禁放导为同一矩形。
    # 本包按全局硬禁放记账，因此校验完整语句，坐标之后也不允许出现修饰。
    point = r"\(\s*-?\d+\s+-?\d+\s*\)"
    require(all(re.fullmatch(rf"-\s+PLACEMENT\s+RECT\s*{point}\s*{point}", entry) for entry in declared_blockages),
            "def-metadata", "BLOCKAGES 须为无修饰的全局硬 PLACEMENT RECT")
    imported_blockages = layer_boxes("PLACEMENT_BLK")
    fixed_guides = Counter(model["pad"] + model["bands"] + list(model["channels"].values()))
    actual_guides = Counter(imported_blockages)
    require(actual_guides >= fixed_guides, "def-blockages", f"DEF 缺少 Pad/IP/通道禁放对象 {fixed_guides - actual_guides}")
    fifo_guides = list((actual_guides - fixed_guides).elements())
    fifo_placement_blocked = region(fifo_guides)
    fifo_reference = region(model["parts"]["FIFO"]) - region(model["banks"])
    require((fifo_placement_blocked ^ fifo_reference).is_empty() and
            fifo_placement_blocked.area() == sum(rect(guide).area() for guide in fifo_guides),
            "def-blockages", "FIFO 窗口扣除银行后的四边与井间通道禁放不完整或重叠")
    require(len(imported_blockages) == len(declared_blockages), "def-blockages", "读回禁放数与 DEF 声明不符")

    # 记录能否提供下一阶段所需实现对象。这里确认的是有无记录，内容审核留给实际 PDK 流程。
    return {"reader": f"KLayout {klayout.__version__} LEF/DEF", "top": top.name, "dbu_um": layout.dbu,
            "fixed_macros": len(components), "fences": len(region_specs), "fence_rectangles": len(soft_rects),
            "fence_by_name": fence_evidence,
            "placement_blockages": len(imported_blockages), "sram_placement_blockages": len(fifo_guides),
            "def_implementation_records_present": [key for key in ("ROW", "GROUPS", "PINS", "NETS", "SPECIALNETS")
                                                   if re.search(rf"(?m)^{key}\s", def_text)],
            "lef_implementation_records_present": [key for key in ("SITE", "LAYER", "PIN", "OBS")
                                                   if re.search(rf"(?m)^\s*{key}\b", lef_text)]}


# =============================================================================
# 4. 面积 CSV、像素尺和负例证据
# =============================================================================
def check_csv(path, plan, model):
    columns = ["分区", "角色", "矩形数", "占地_um2", "占裸片_%"]
    with path.open(newline="", encoding="utf-8") as file:
        table = csv.DictReader(file)
        require(table.fieldnames == columns, "csv", "面积表字段与单位不符")
        rows = list(table)
    summary = model["summary"]
    roles = {"pad_ring_um2": "Pad 与角区", "explicit_channels_um2": "指定通道", "remaining_inside_ring_um2": "未分配面积"}
    expected = {key: (description, "", Decimal(str(summary[key]))) for key, description in roles.items()}
    for part in plan["partitions"]:
        name = part["id"]
        boxes = model["parts"][name]
        expected[name] = (part["name"], str(len(boxes)), Decimal(region(boxes).area()) / SQUARE)
    require(Counter(row["分区"] for row in rows) == Counter(expected.keys()), "csv", "面积表分区或汇总行不全")
    total = Decimal(str(summary["die_um2"]))
    # 各百分比最末位独立取三位小数，和可能偏离 100%。按原面积验守恒。
    for row in rows:
        role, quantity, area = expected[row["分区"]]
        percentage = (100 * area / total).quantize(Decimal("0.001"))
        require((row["角色"], row["矩形数"], Decimal(row["占地_um2"]), Decimal(row["占裸片_%"])) ==
                (role, quantity, area, percentage), "csv", f'{row["分区"]} 的面积、百分比或分区标识不符')
    require(sum(values[2] for values in expected.values()) == total, "csv", "面积表与裸片总面积不守恒")
    return {"data_rows": len(rows), "covers_die_um2": float(total), "percent_decimals": 3}


def check_area_scopes(binding_path, area_path, model):
    # 归属表先与固定合同核对。仅验证生成表与输入表相符，会漏掉两表一致的错归属。
    with binding_path.open(newline="", encoding="utf-8") as file:
        bindings = list(csv.DictReader(file))
    require(Counter(row["分区"] for row in bindings) == Counter(PARTITIONS),
            "area-scope", "归属表须完整且每个物理分区仅出现一次")
    expected_scope = {part: scope for scope, parts in PARTITION_SCOPES.items() for part in parts}
    actual_scope = {row["分区"]: row["面积归属"] for row in bindings}
    require(actual_scope == expected_scope, "area-scope",
            f"分区归属错误 {[(part, scope, expected_scope[part]) for part, scope in actual_scope.items() if scope != expected_scope[part]]}")

    # 每个模型窗口先按绝对坐标与实际 GDS 读回校验。从这些矩形重算面积和
    # 不相交性，SRAM 的四个实宏包络已占 FIFO 容器，避免在外围面积里叠加。
    def partition_region(parts):
        return region(box for part in parts for box in model["parts"][part])

    perimeter_ids = PARTITION_SCOPES[PERIPHERAL_KNOWN] + PARTITION_SCOPES[PERIPHERAL_UNBOUND]
    ip_region = partition_region(PARTITION_SCOPES[CONVERTER_SCOPE])
    peripheral = partition_region(perimeter_ids)
    # 这里的交集判据只计正面积。相切公边不会重复计账，实体间距由下阶段约束。
    overlap = (ip_region & peripheral).area()
    require(overlap == 0, "area-scope", "五个外围窗与转换功能 IP 面积必须互斥")
    # 分区面积保持十进制精度到与 CSV 核对结束，微米栅格边界的部分面积可精确计入。
    exclusive = {name: (parts, Decimal(partition_region(parts).area()) / SQUARE) for name, parts in PARTITION_SCOPES.items()}
    summary = model["summary"]
    for scope, key in (("Pad 环与角区", "pad_ring_um2"), ("指定通道", "explicit_channels_um2"),
                       ("环内未分配", "remaining_inside_ring_um2")):
        exclusive[scope] = ((key,), Decimal(str(summary[key])))
    # 小计用于盘点外围窗口，裸片面积只加互斥明细。外围合同值独立与统计结果核对。
    # 已知功能表示物理窗口用途明确，几何验收不核工艺宏或标准单元映射。
    peripheral_area = Decimal(peripheral.area()) / SQUARE
    scope_areas = {name: exclusive[name][1] for name in (CONVERTER_SCOPE, PERIPHERAL_KNOWN, PERIPHERAL_UNBOUND)}
    require((*scope_areas.values(), peripheral_area) == (250000, 183226, 103140, 286366),
            "area-scope", f"转换 IP 和产品外围毛面积偏离合同 {scope_areas}、外围合计 {peripheral_area}")
    require(sum(area for _, area in exclusive.values()) == Decimal(str(summary["die_um2"])),
            "area-scope", "互斥明细之和须闭合到裸片面积")

    with area_path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        require(reader.fieldnames == ["行型", "归属", "包含分区", "毛面积_um2", "毛面积_mm2"],
                "area-scope", "归属面积表字段或单位不符")
        rows = list(reader)
    expected_rows = {("互斥明细", scope): values for scope, values in exclusive.items()}
    expected_rows["小计", PERIPHERAL_TOTAL] = (perimeter_ids, peripheral_area)
    require(Counter((row["行型"], row["归属"]) for row in rows) == Counter(expected_rows.keys()),
            "area-scope", "归属面积表明细或小计缺失、重复")
    for row in rows:
        parts, area = expected_rows[row["行型"], row["归属"]]
        # 多重集合既接受成员顺序变化，也会发现重复计一块窗来凑合计。
        declared_items = Counter(item.strip() for item in row["包含分区"].split("+"))
        require(declared_items == Counter(parts) and Decimal(row["毛面积_um2"]) == area and
                Decimal(row["毛面积_mm2"]) == Decimal(area) / SQUARE,
                "area-scope", f'{row["归属"]} 的成员或毛面积与回读几何不符')

    def scope_entry(parts, area):
        return {"members": parts, "gross_um2": float(area), "gross_mm2": float(area / SQUARE)}

    return {"exclusive_detail_rows": len(exclusive), "exclusive_total_um2": summary["die_um2"],
            "converter": scope_entry(*exclusive[CONVERTER_SCOPE]),
            "peripheral_known": scope_entry(*exclusive[PERIPHERAL_KNOWN]),
            "peripheral_unbound": scope_entry(*exclusive[PERIPHERAL_UNBOUND]),
            "peripheral_total": scope_entry(perimeter_ids, peripheral_area),
            "peripheral_windows_um": {part: [um(box) for box in model["parts"][part]] for part in perimeter_ids},
            "converter_peripheral_overlap_um2": overlap / SQUARE}


def check_photo(photo, model, png_path):
    # 像素标尺只估计相对位置。整体规划比例和手工框用于记录工程挪位，
    # 不把 ±px 手选误差解释成拆片绝对测量精度。
    w, h = photo["width"], photo["height"]
    image = {"width_px": w, "height_px": h, "sha256": photo["sha256"], "local_png_verified": False}
    if png_path is not None:
        contents = png_path.read_bytes()
        require(contents[:16] == b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" and
                unpack(">II", contents[16:24]) == (w, h) and sha256(contents).hexdigest() == photo["sha256"],
                "photo", "参考 PNG 的格式、像素尺寸或哈希不符")
        image["local_png_verified"] = True
    scale_x, scale_y = DIE[2] / (DBU * w), DIE[3] / (DBU * h)
    comparison = {}
    for name, (left, top, right, bottom) in photo["boxes"].items():
        require(0 <= left < right <= w and 0 <= top < bottom <= h, "photo", f"{name} 的像素框超界")
        # 原照片原点在上，规划原点在下。翻转后底边来自原像素框的 bottom。
        reference = [left * scale_x, (h - bottom) * scale_y, right * scale_x, (h - top) * scale_y]
        planned, = model["parts"][name]
        comparison[name] = {"photo_reference_bbox_um": [round(value, 3) for value in reference],
                            "plan_minus_reference_edges_um": [round(p - r, 3) for p, r in zip(um(planned), reference)]}
    return {"image": image, "uncertainty_px": photo["uncertainty_px"], "boxes": comparison}


def run_negative_checks(directory, plan, model):
    # 篡改只发生在内存副本和临时目录。每例必须在指定闸门失败，
    # 防止依赖加载失败、文件缺失或无关格式错误被误报为保护命中。
    rejected = {}

    def rejects(name, category, run):
        try:
            run()
        except CheckError as error:
            require(str(error).startswith(f"[{category}]"), "self-test", f"{name} 落入错误闸门 {error}")
            rejected[name] = str(error)
        else:
            raise CheckError(f"[self-test] {name} 本应拒绝")

    broken = deepcopy(plan)
    # 原两条全宽带的面积正好 25,000 µm²。这个负例证明连通检查独立于算术预算。
    broken["ip_row_blockages"] = [[145, 990, 1095, 1010], [230, 1190, 470, 1215]]
    rejects("same_area_bands_cut_ip_into_3_islands", "placement", lambda: check_plan(broken))
    broken = deepcopy(plan)
    broken["ip_row_blockages"][0][0] += 1
    rejects("row_exclusion_under_10_pct", "reserve", lambda: check_plan(broken))
    broken = deepcopy(plan)
    broken["ip_pre_cts_target_um2"] = 120_001
    rejects("pre_cts_budget_above_0_120_mm2", "cell-budget", lambda: check_plan(broken))
    broken = deepcopy(plan)
    broken["ip_utilization_max"] = 0.60001
    rejects("ip_utilization_over_60_pct", "cell-budget", lambda: check_plan(broken))
    broken = deepcopy(plan)
    # 保持净区连通、扣减比例和 60% 不变，只放大软区强制触发最终面积闸门。
    # 软区顶端加 10 µm 得 2,400 µm²，禁放第二带多 1.2 µm 刚好扣去 240 µm²。
    spur = max(next(part["rects"] for part in broken["partitions"] if part["id"] == "IP"), key=lambda box: box[3])
    spur[3] += 10
    broken["ip_row_blockages"][1][3] += 1.2
    rejects("larger_ip_with_same_10_pct_reserve_exceeds_0_135_mm2", "cell-budget", lambda: check_plan(broken))
    broken = deepcopy(plan)
    broken["sram"]["words_per_bank"] = 4097
    rejects("sram_capacity_changed", "sram-capacity", lambda: check_plan(broken))

    # 丢线负例保留 GDS、DEF 和所有几何，验证分线合同独立于规划图形。
    for name in SIGNAL_CONTRACT:
        broken = deepcopy(plan)
        next(port for port in broken["ports"] if port["group"] == name)["signal_count"] -= 1
        rejects(f"{name.lower()}_lost_one_signal", "signal-count", lambda: check_plan(broken))
    # 两段仍接各自功能区，只在交接处留一格。角点或越缝无法由通道面积识别。
    broken = deepcopy(plan)
    next(channel for channel in broken["channels"] if channel["id"] == "Q_SPUR_S")["rect"][3] -= 0.001
    rejects("q_route_1_dbu_gap_at_segment_join", "q-route", lambda: check_plan(broken))
    # 移到同一西边的空段，长度与朝向保持，专门测试指定通道的归属。
    broken = deepcopy(plan)
    next(port for port in broken["ports"] if port["group"] == "Q_RETURN")["line"] = [145, 1020, 145, 1054]
    rejects("q_port_shifted_to_unreserved_west_edge", "port", lambda: check_plan(broken))
    # 两组仍在正确通道外露边上，却重复占用了 x=800 至 845 的引脚分配长度。
    broken = deepcopy(plan)
    next(port for port in broken["ports"] if port["group"] == "ADC2_RSP")["line"] = [800, 900, 970, 900]
    rejects("rdac_and_adc2_port_spans_overlap", "port", lambda: check_plan(broken))
    broken = deepcopy(plan)
    next(channel for channel in broken["channels"] if channel["id"] == "IO_EAST")["rect"][0] += 2
    rejects("io_east_2_um_gap_to_dio", "channel", lambda: check_plan(broken))

    with TemporaryDirectory(prefix="ad4080-floorplan-check-") as temporary:
        scratch = Path(temporary)
        # 改写二进制 GDS 之后重新加载，不调用生成器。
        library = gdstk.read_gds(str(directory / "chip.gds"))
        next(polygon for polygon in library.cells[0].polygons if polygon.layer == 10).translate(1, 0)
        shifted = scratch / "shifted.gds"
        library.write_gds(str(shifted))
        rejects("gds_soft_region_shifted_1_um", "gds", lambda: check_gds(shifted, plan, model))

        # 真实并层只改 BOUNDARY 编号，原 LYP 仍保有两个正常名字，能暴露纯改图例。
        library = gdstk.read_gds(str(directory / "chip.gds"))
        next(polygon for polygon in library.cells[0].polygons if polygon.layer == SOFT_LAYERS["CTRL"]).layer = SOFT_LAYERS["IP"]
        merged = scratch / "merged-soft-layers.gds"
        library.write_gds(str(merged))
        rejects("gds_ctrl_merged_back_into_ip_layer", "gds", lambda: check_gds(merged, plan, model))
        rejects("klayout_gds_ctrl_merged_back_into_ip_layer", "native-gds",
                lambda: check_klayout_layers(merged, directory / "chip.lyp", model))

        original = (directory / "chip.lyp").read_text(encoding="utf-8")
        wrong_style = scratch / "merged-selectors.lyp"
        # 保留正确二进制，仅把两个显示项指向同一编号，反向覆盖查看器配置故障。
        wrong_style.write_text(original.replace("11/0@1", "10/0@1"), encoding="utf-8")
        rejects("klayout_lyp_ctrl_selector_aliases_ip", "layer-visibility",
                lambda: check_klayout_layers(directory / "chip.gds", wrong_style, model))

        # 只互换两条 FENCE 的名字，KLayout 的 REGIONS 几何全集仍一样，归属应报错。
        original = (directory / "chip.def").read_text(encoding="ascii")
        swapped = scratch / "swapped-fences.def"
        swapped.write_text(re.sub(r"(?m)^- (IP|CTRL) (?=\()", lambda match: "- CTRL " if match[1] == "IP" else "- IP ",
                                  original), encoding="ascii")
        rejects("def_ip_and_ctrl_fence_names_swapped", "def", lambda: check_def(swapped, directory / "planning.lef", model))

        # IP 与 CTRL 被同期错分时，总面积、各窗几何与 CSV 的算术仍能保持自洽。
        # 先改两张表再要求独立归属合同拦截，把错计为外围的核心挡在面积统计之前。
        original = (directory / "block_binding.csv").read_text(encoding="utf-8")
        wrong_binding = scratch / "swapped-scope-binding.csv"
        wrong_binding.write_text(original.replace(f"IP,{CONVERTER_SCOPE},", f"IP,{PERIPHERAL_KNOWN},", 1)
                                 .replace(f"CTRL,{PERIPHERAL_KNOWN},", f"CTRL,{CONVERTER_SCOPE},", 1), encoding="utf-8")
        original = (directory / "area_by_scope.csv").read_text(encoding="utf-8")
        wrong_area = scratch / "swapped-scope-area.csv"
        wrong_area.write_text(original.replace("IP,250000,0.250000", "CTRL,26400,0.026400", 1)
                              .replace("FIFO + DIO + CTRL,183226,0.183226", "FIFO + DIO + IP,406826,0.406826", 1)
                              .replace("FIFO + DIO + CTRL + SR2 + OTP,286366,0.286366",
                                       "FIFO + DIO + IP + SR2 + OTP,509966,0.509966", 1), encoding="utf-8")
        rejects("converter_ip_misclassified_as_peripheral", "area-scope", lambda: check_area_scopes(wrong_binding, wrong_area, model))
        rejects("area_scope_wrong_members_with_original_binding", "area-scope",
                lambda: check_area_scopes(directory / "block_binding.csv", wrong_area, model))

        original = (directory / "planning.lef").read_text(encoding="ascii")
        target = scratch / "oversized.lef"
        # 改变宏视图的宽度而保持 DEF 实例坐标，要求联合导入给出真实的超宽边界。
        target.write_text(re.sub(r"SIZE ([0-9.]+) BY", lambda match: f"SIZE {Decimal(match[1]) + Decimal('0.001')} BY",
                                 original, count=1), encoding="ascii")
        rejects("lef_bank_width_plus_1_dbu", "sram-size", lambda: check_def(directory / "chip.def", target, model))

        original = (directory / "chip.def").read_text(encoding="ascii")
        target = scratch / "wrong-blockage.def"
        # 一条禁放边只短一格时，多数绘图分辨率下看不出缝隙，整数几何仍会拒绝。
        target.write_text(re.sub(r"(PLACEMENT RECT \( \d+ \d+ \) \( )(\d+)",
                                 lambda match: f"{match[1]}{int(match[2]) - 1}", original, count=1), encoding="ascii")
        rejects("def_placement_blockage_edge_minus_1_dbu", "def-blockages",
                lambda: check_def(target, directory / "planning.lef", model))

        # 坐标之后的 SOFT 在读回中保留相同几何，须从语句直接挡住降级。
        target = scratch / "soft-blockage.def"
        target.write_text(original.replace("( 960000 1015000 ) ;", "( 960000 1015000 ) + SOFT ;", 1), encoding="ascii")
        rejects("def_row_blockage_has_trailing_soft_qualifier", "def-metadata",
                lambda: check_def(target, directory / "planning.lef", model))
    return rejected


# =============================================================================
# 5. 可复现入口和验收范围
# =============================================================================
def main():
    parser = ArgumentParser(description="独立读回 AD4080 各规划交换文件，输出面积与坐标验收证据")
    parser.add_argument("--floorplan-dir", type=Path, default=Path(__file__).resolve().parents[1] / "floorplan")
    parser.add_argument("--photo", type=Path, help="可选的原始参考 PNG，供核对像素尺寸与哈希")
    parser.add_argument("--output", type=Path, help="通过验收后另存 JSON，默认仅写标准输出")
    options = parser.parse_args()

    directory = options.floorplan_dir
    # 证据这里只引用归档原文件的哈希。负例失败或主条件不符时直接退出，无 PASS 输出。
    # 读取阶段先固定绝对规划要求，再由原生 reader 核对坐标和跨文件语义。
    try:
        plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        model = check_plan(plan)
        result = {"status": "PASS", "checked_file_sha256": {name: sha256((directory / name).read_bytes()).hexdigest()
                                                            for name in FILES},
                  "geometry": model["summary"], "gds": check_gds(directory / "chip.gds", plan, model),
                  "klayout_layer_controls": check_klayout_layers(directory / "chip.gds", directory / "chip.lyp", model),
                  "lef_def": check_def(directory / "chip.def", directory / "planning.lef", model),
                  "area_csv": check_csv(directory / "area.csv", plan, model),
                  "area_by_scope": check_area_scopes(directory / "block_binding.csv", directory / "area_by_scope.csv", model),
                  "photo_reference": check_photo(plan["photo"], model, options.photo),
                  "negative_checks": run_negative_checks(directory, plan, model),
                  "scope": ["规划交换几何与面积阈值核对", "IP 禁放带留空和接口外露边检查",
                            "转换 IP 与外围 CTRL 的原生分层、FENCE 归属和规划毛面积互斥",
                            "Q/RDAC 分线声明、通道公共边和端口几何，布线轨道及物理引脚待交接",
                            "FENCE 仅含区域几何，标准单元实例绑定与 SITE/ROW 待交接",
                            "PG 专网、布线层、物理引脚与连通性待交接",
                            "真实 SRAM compiler 尺寸、引脚、BIST、存取时序及 PPA 待核对",
                            "CTS/布线和 DRC/LVS、STA、EM/IR 依赖实际工艺抽象及实现输入"]}
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(1, f"验收失败 {error}\n")

    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if options.output:
        options.output.write_text(serialized, encoding="utf-8")
        print(f"规划几何验收通过，记录 {options.output}")
    else:
        print(serialized, end="")


if __name__ == "__main__":
    main()
