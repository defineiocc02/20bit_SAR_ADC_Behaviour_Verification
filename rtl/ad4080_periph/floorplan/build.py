# =============================================================================
# 文件名     : build.py
# 模块名     : 整芯片几何预算与交换文件
# 版本       : v1.0
# 功能       : 从同一组微米坐标导出 DEF、规划 LEF、GDSII 与面积表。
# 设计说明   : GDS 使用评审图层；软分区以矩形并集表示，孔位、电源线和工艺层待宏交接。
# 变更记录   : 首次交付矩形互斥、软区连通、接口落边与银行阵列容量检查。
# =============================================================================
"""几何单位为 µm，原点在左下。独立手绘像素框保留原图左上坐标。"""
from pathlib import Path
from itertools import combinations
import csv
import json
import math
import struct

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UNIT = 1000  # GDS 与 DEF 均取 1 nm 数据库栅格。
LOGIC_LAYER = {"IP": 10, "CTRL": 11}  # 转换组和产品外围独立开关可见性。
GROUP_LAYER = {"memory": 20, "nv": 22, "io": 30, "clock": 31,
               "analog": 40, "reference": 41, "power": 42}


# =============================================================================
# 1. 分区、银行和同层排它关系
# =============================================================================
def area(r):
    """左下与右上坐标定义半开矩形；共边块相加时不会重复记面积。"""
    return (r[2] - r[0]) * (r[3] - r[1])


def inside(r, box):
    return box[0] <= r[0] < r[2] <= box[2] and box[1] <= r[1] < r[3] <= box[3]


def overlap(a, b):
    return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])


def abut(a, b):
    """只把有非零长度的共边算作摆放通路；点接触容不下标准单元行。"""
    vertical = (a[0] == b[2] or a[2] == b[0]) and max(a[1], b[1]) < min(a[3], b[3])
    horizontal = (a[1] == b[3] or a[3] == b[1]) and max(a[0], b[0]) < min(a[2], b[2])
    return vertical or horizontal


def banks(plan):
    """先扣除外圈与井间通道，再求编译器宏含外围电路的最大允许尺寸。"""
    x, y, right, top = next(p["rects"][0] for p in plan["partitions"] if p["id"] == "FIFO")
    macro = plan["sram"]
    margin, gap = macro["outer_margin"], macro["bank_channel"]
    w = (right - x - 2 * margin - gap) / 2
    h = (top - y - 2 * margin - gap) / 2
    result = []
    for row in range(2):
        for col in range(2):
            left, bot = x + margin + col * (w + gap), y + margin + row * (h + gap)
            result.append([left, bot, left + w, bot + h])
    return result


def pad_ring(plan):
    x, y, u, v = plan["die"]
    a, b, c, d = plan["core"]
    # 角区归上下两边，把四角只计一次，侧边仅以 core 的上下边界为限。
    return [[x,y,u,b],[x,d,u,v],[x,b,a,d],[c,b,u,d]]


def check(plan):
    """先检查互斥关系再计算总和；面积碰巧相等不能弥补越界或重复占地。"""
    # 同组的多个矩形可能是同一个软区，也可能是两个参考窗，逐对核对重叠。
    parts = {p["id"]: p for p in plan["partitions"]}
    rects = [(p["id"], r) for p in parts.values() for r in p["rects"]]
    assert len(parts) == len(plan["partitions"])
    for key, r in rects:
        assert inside(r, plan["core"]), (key, r)
    for (ak, ar), (bk, br) in combinations(rects, 2):
        assert not overlap(ar, br), (ak, bk)
    # 通道从空白区扣除，不能再算一次进占地。IP 内部的两条禁放带另行扣除。
    channels = [c["rect"] for c in plan["channels"]]
    for ch in channels:
        assert inside(ch, plan["core"]) and not any(overlap(ch, r) for _, r in rects), ch
    assert not any(overlap(a, b) for a, b in combinations(channels, 2))
    # 用共边图搜索整个 IP 的连通分量。独立回读器还会切片检查扣禁放后的净区域。
    soft = parts["IP"]["rects"]
    visited = {0}
    while True:
        more = {i for i, r in enumerate(soft) if any(abut(r, soft[j]) for j in visited)}
        if more <= visited:
            break
        visited |= more
    assert len(visited) == len(soft), "功能 IP 必须为一块连通分区"
    gaps = plan["ip_row_blockages"]
    assert all(any(inside(g, s) for s in soft) for g in gaps)
    assert not any(overlap(a, b) for a,b in combinations(gaps,2))
    # 端口组只是一段访问窗口，不能当作实际金属引脚。窗口须完整落在相应朝向的边上。
    for port in plan["ports"]:
        x, y, u, v = port["line"]
        side = port["side"]
        if side in ("east", "west"):
            edge = 2 if side == "east" else 0
            touches = any(x == u == r[edge] and r[1] <= y < v <= r[3] for r in soft)
        else:
            edge = 3 if side == "north" else 1
            touches = any(y == v == r[edge] and r[0] <= x < u <= r[2] for r in soft)
        assert touches, port
        for g in gaps:
            assert not (max(x,g[0]) <= min(u,g[2]) and max(y,g[1]) <= min(v,g[3])), port
    # 单宏的最大密度含译码、感放、写驱动等内部开销；窗口条件约束最终 LEF 外形。
    b = banks(plan)
    assert all(inside(r,parts["FIFO"]["rects"][0]) for r in b)
    assert not any(overlap(a,b) for a,b in combinations(b,2))
    reserve = sum(area(r) for r in gaps)
    ip = sum(area(r) for r in soft)
    bank_bits = plan["sram"]["words_per_bank"] * plan["sram"]["bits_per_word"]
    assert len(b) == plan["sram"]["banks"] and bank_bits*len(b) == 16384*20
    # 在净行面积上乘利用率；若把禁放条叠到利用率之后，会高估 CTS 可用面积。
    cells = (ip-reserve)*plan["ip_utilization_max"]
    assert plan["ip_pre_cts_target_um2"] <= cells
    used = sum(area(r) for _,r in rects)
    ring = sum(area(r) for r in pad_ring(plan))
    guide = sum(area(c) for c in channels)
    free = area(plan["core"])-used-guide
    assert free >= 0 and used+ring+guide+free == area(plan["die"])
    return {"die_um2":area(plan["die"]), "inside_ring_um2":area(plan["core"]), "pad_ring_um2":ring,
            "allocated_um2":used,"explicit_channels_um2":guide,"remaining_inside_ring_um2":free,
            "ip_gross_um2":ip,"ip_no_rows_um2":reserve,"ip_net_rows_um2":ip-reserve,
            "ip_pre_cts_target_um2":plan["ip_pre_cts_target_um2"],"ip_final_cells_max_um2":cells,
            "ip_cts_eco_reserve_um2":cells-plan["ip_pre_cts_target_um2"],
            "sram_bits":bank_bits*len(b),"bank_max_width_um":b[0][2]-b[0][0],
            "bank_max_height_um":b[0][3]-b[0][1],"bank_max_area_um2":area(b[0]),
            "bank_max_effective_um2_per_bit":area(b[0])/bank_bits}


# =============================================================================
# 2. DEF / LEF。软区用 FENCE，其余固定区域以尺寸抽象交给 floorplan 工具。
# =============================================================================
def def_box(r):
    x,y,u,v = (round(n*UNIT) for n in r)
    return f"( {x} {y} ) ( {u} {v} )"


def lefdef(plan):
    """导出无工艺依赖的规划抽象。真实层、行与电源网络由选定 PDK 交付。"""
    # 几何复用微米输入，DEF 整数坐标与 LEF DATABASE MICRONS 必须同一缩放。
    lef=["VERSION 5.8 ;","BUSBITCHARS \"[]\" ;","DIVIDERCHAR \"/\" ;",
         "UNITS","  DATABASE MICRONS 1000 ;","END UNITS","MANUFACTURINGGRID 0.001 ;"]
    comp=[]
    regs=[]
    for p in plan["partitions"]:
        key=p["id"]
        # FENCE 是软区所有者的边界，即使本包没有功能 IP 实例，也可先审查坐标。
        if p["kind"]=="soft":
            regs.append(f"- {key} "+ " ".join(def_box(r) for r in p["rects"])+" + TYPE FENCE ;")
            continue
        # 整个 FIFO 窗放四个真实尺寸上限的规划抽象，外围留空；避免窗口与 bank 双重占地。
        for i,(x,y,u,v) in enumerate(banks(plan) if key=="FIFO" else p["rects"]):
            name=f"WIN_{key}_{i}"
            lef.extend([f"MACRO {name}", "  CLASS BLOCK ;","  ORIGIN 0 0 ;",
                        f"  SIZE {u-x:g} BY {v-y:g} ;",f"END {name}"])
            comp.append(f"- inst_{key}_{i} {name} + FIXED ( {round(x*UNIT)} {round(y*UNIT)} ) N ;")
    lef.append("END LIBRARY")
    (HERE/"planning.lef").write_text("\n".join(lef)+"\n")
    blocked=pad_ring(plan)+plan["ip_row_blockages"]+[c["rect"] for c in plan["channels"]]
    # 将 SRAM 外围与井间通道明确禁放，避免读入 DEF 后标准单元侵入 bank 间隙。
    x,y,u,v=next(p["rects"][0] for p in plan["partitions"] if p["id"]=="FIFO")
    a,b,c,d=banks(plan)
    blocked.extend([[x,y,u,a[1]], [x,d[3],u,v], [x,a[1],a[0],d[3]], [b[2],a[1],u,d[3]],
                    [a[2],a[1],b[0],d[3]],[a[0],a[3],a[2],c[1]], [b[0],b[3],b[2],d[1]]])
    lines=["VERSION 5.8 ;",'DIVIDERCHAR "/" ;','BUSBITCHARS "[]" ;',"DESIGN AD4080_AREA_STUDY ;",
           "UNITS DISTANCE MICRONS 1000 ;","DIEAREA "+def_box(plan["die"])+" ;",
           f"COMPONENTS {len(comp)} ;",*comp,"END COMPONENTS",f"REGIONS {len(regs)} ;",
           *regs,"END REGIONS",f"BLOCKAGES {len(blocked)} ;",
           *["- PLACEMENT RECT "+def_box(r)+" ;" for r in blocked],"END BLOCKAGES","END DESIGN"]
    (HERE/"chip.def").write_text("\n".join(lines)+"\n")


# =============================================================================
# 3. GDSII 规划图层。独立图层保存设计边界和禁放区，不把彩色占位当工艺掩模。
# =============================================================================
def gds(plan):
    records=[]
    def rec(code, dtype=0, data=b""):
        if len(data)%2: data+=b"\0"
        records.append(struct.pack(">HBB",4+len(data),code,dtype)+data)
    def ints(values, fmt): return struct.pack(">"+fmt*len(values),*values)
    # GDSII 的实数是 16 进制指数格式；使用 IEEE double 直接打包会读成错误尺度。
    def real8(value):
        mant,exponent=value,64
        while mant < 1/16: mant*=16;exponent-=1
        while mant >= 1: mant/=16;exponent+=1
        return bytes([exponent])+int(mant*(1<<56)).to_bytes(7,"big")
    def coords(points): return ints([round(v*UNIT) for point in points for v in point],"i")
    # BOUNDARY 的最后一点回到第一点；不重复矩形边界上的物理 pin 或 pad 单元。
    def rect(layer,r):
        x,y,u,v=r
        rec(8);rec(13,2,ints([layer],"h"));rec(14,2,ints([0],"h"))
        rec(16,3,coords([(x,y),(u,y),(u,v),(x,v),(x,y)]));rec(17)
    def label(layer,r,name):
        rec(12);rec(13,2,ints([layer],"h"));rec(22,2,ints([0],"h"))
        rec(16,3,coords([(r[0]+2,r[1]+2)]));rec(25,6,name.encode("ascii"));rec(17)
    # 时间戳固定以保证同一坐标表可生成字节相同的交换文件。
    ts=ints([2026,11,20,0,0,0]*2,"h")
    rec(0,2,ints([600],"h"));rec(1,2,ts);rec(2,6,b"FLOORPLAN")
    rec(3,5,real8(1/UNIT)+real8(1e-6/UNIT));rec(5,2,ts);rec(6,6,b"AD4080_AREA_STUDY")
    rect(1,plan["die"])
    for r in pad_ring(plan):rect(2,r)
    for p in plan["partitions"]:
        # 两种逻辑都使用标准单元；分层用于审阅面积归属，DEF 的两个 FENCE 保持独立。
        layer = LOGIC_LAYER[p["id"]] if p["group"] == "logic" else GROUP_LAYER[p["group"]]
        for i, r in enumerate(p["rects"]):
            rect(layer, r)
            label(90, r, f'{p["id"]}_{i}')
    for i,r in enumerate(banks(plan)):rect(21,r);label(90,r,"BANK_"+str(i))
    for r in plan["ip_row_blockages"]:rect(50,r)
    for c in plan["channels"]:rect(51,c["rect"])
    # 无宽的线段用 1 µm 宽规划标记表示；其宽度只为显示，不能用作 pin pitch。
    for p in plan["ports"]:
        x,y,u,v=p["line"]
        rect(52,[x-0.5,y-0.5,u+0.5,v+0.5])
    rec(7);rec(4)
    (HERE/"chip.gds").write_bytes(b"".join(records))


# =============================================================================
# 4. 功能组、产品外围与公共资源分别汇总
# =============================================================================
def write_scope_areas(plan, stats):
    """从无重叠几何和分区归属表产生各组毛面积，单位 µm²。"""
    sizes = {part["id"]: sum(area(r) for r in part["rects"]) for part in plan["partitions"]}
    # 归属改动由 CSV 管理；功能表和 GDS 须包含完全相同的物理分区。
    with (HERE / "block_binding.csv").open(encoding="utf-8", newline="") as f:
        bindings = list(csv.DictReader(f))
    if len(bindings) != len(sizes) or {row["分区"] for row in bindings} != set(sizes):
        raise ValueError("分区归属与规划几何不一致")
    grouped = {}
    for row in bindings:
        group = row["面积归属"]
        if not group:
            raise ValueError(f'缺少 {row["分区"]} 的面积归属')
        grouped.setdefault(group, []).append(row["分区"])
    rows = []
    for group, ids in grouped.items():
        rows.append(("互斥明细", group, " + ".join(ids), sum(sizes[i] for i in ids)))
    # 环、走廊和分散空地不归 IP 团队或某一外围单元所有，单独补足裸片面积。
    for key, label in (("pad_ring_um2", "Pad 环与角区"),
                       ("explicit_channels_um2", "指定通道"),
                       ("remaining_inside_ring_um2", "环内未分配")):
        rows.append(("互斥明细", label, key, stats[key]))
    if sum(row[3] for row in rows) != stats["die_um2"]:
        raise ValueError("互斥面积未闭合")
    # 外围小计用于读取本方五个窗口，不能再与明细相加。
    peripheral = grouped["产品外围已知功能窗"] + grouped["产品外围待实物绑定窗"]
    rows.append(("小计", "产品外围五窗", " + ".join(peripheral), sum(sizes[i] for i in peripheral)))
    with (HERE / "area_by_scope.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("行型", "归属", "包含分区", "毛面积_um2", "毛面积_mm2"))
        for kind, name, items, value in rows:
            writer.writerow((kind, name, items, value, f"{value / 1_000_000:.6f}"))


def main():
    if not __debug__:
        raise RuntimeError("几何断言需要普通 Python 模式")
    plan=json.loads((HERE/"plan.json").read_text())
    stats=check(plan)
    write_scope_areas(plan, stats)
    lefdef(plan)
    gds(plan)
    with (HERE/"area.csv").open("w",newline="") as f:
        w=csv.writer(f); w.writerow(["分区","角色","矩形数","占地_um2","占裸片_%"])
        for part in plan["partitions"]:
            amt=sum(area(r) for r in part["rects"])
            w.writerow([part["id"],part["name"],len(part["rects"]),amt,round(100*amt/stats["die_um2"],3)])
        for key,name in [("pad_ring_um2","Pad 与角区"),("explicit_channels_um2","指定通道"),("remaining_inside_ring_um2","未分配面积")]:
            amt=stats[key]; w.writerow([key,name,"",amt,round(100*amt/stats["die_um2"],3)])
    (ROOT/"evidence"/"geometry.json").write_text(json.dumps(stats,indent=2)+"\n")
    print(json.dumps(stats,ensure_ascii=False))

if __name__=="__main__": main()
