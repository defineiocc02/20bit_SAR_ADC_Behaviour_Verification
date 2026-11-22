# =============================================================================
# 文件名     : area_gate.py
# 模块名     : area_gate
# 版本       : v1.0
# 功能       : 读取顶层规划，计算功能 IP 的行容量并核对分阶段面积报告。
#
# 设计说明   :
#   * 所有坐标使用裸片坐标系的 µm，面积统一换算为 µm²。十进制运算保留
#     报告在门槛附近的小数，等于上限视为通过。
#   * 标准单元由报告汇总。硬宏及 halo、额外行禁放用矩形并集折减分母，
#     只扣除规划禁放带之外新增损失的行面积。条带先核裸片边界，再裁入 IP。
#   * pre-CTS 维持初始 CTS/ECO 余量。额外禁放减少容量时，先收紧
#     pre-CTS 目标。final 使用剩余行面积乘以规划利用率。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import json
import sys
from decimal import Decimal
from itertools import combinations
from pathlib import Path


DEFAULT_PLAN = Path(__file__).resolve().parents[1] / "floorplan" / "plan.json"
AREA_UNIT_EXPONENT = {"um2": 0, "mm2": 6}  # 换算到 µm² 时十进制指数的增量


# =============================================================================
# 1. 坐标与面积口径
# =============================================================================
def decimal_value(value, label):
    """把标量转为有限 Decimal。调用方按字段检查单位、符号和取值范围。"""
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{label} 须为有限数值")
    try:
        number = Decimal(value)
    except ArithmeticError as exc:
        raise ValueError(f"{label} 须为有限数值") from exc
    if not number.is_finite():
        raise ValueError(f"{label} 须为有限数值")
    return number


def read_json(path):
    # JSON 重名字段默认保留末项。一条真禁放被同名空数组覆盖时会抬高预算。
    def unique_object(pairs):
        fields = {}
        for key, value in pairs:
            if key in fields:
                raise ValueError(f"{path} 含重复 JSON 字段 {key}")
            fields[key] = value
        return fields

    data = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal, object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 的顶层须为 JSON 对象")
    return data


def rect(raw, label):
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(f"{label} 须为 [x0,y0,x1,y1]")
    bounds = tuple(decimal_value(value, label) for value in raw)
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError(f"{label} 须有正宽和正高")
    return bounds


def rect_list(raw, label):
    if not isinstance(raw, list):
        raise ValueError(f"{label} 须为矩形数组")
    return [rect(box, label) for box in raw]


def intersect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x0 < x1 and y0 < y1:
        return x0, y0, x1, y1
    return None


def union_area(boxes):
    """求 µm 矩形并集的 µm² 面积。空数组返回 0，无文件或版图副作用。"""
    # 横坐标边界把区域切成单元片，每片内只合并纵向段。
    # 共边矩形可直接拼接，重叠禁放只为其新增纵向长度记账。
    x_edges = sorted({x for left, _, right, _ in boxes for x in (left, right)})
    total = Decimal(0)
    for x0, x1 in zip(x_edges, x_edges[1:]):
        spans = sorted((bottom, top) for left, bottom, right, top in boxes
                       if left < x1 and x0 < right)
        end = None
        for low, high in spans:
            if end is None or end < low:
                end = low
            if end < high:
                total += (x1 - x0) * (high - end)
                end = high
    return total


# =============================================================================
# 2. 规划及额外行禁放
# =============================================================================
def plan_inputs(plan):
    if plan["units"] != "um":
        raise ValueError("plan.units 须为 um")
    die = rect(plan["die"], "plan.die")
    core = rect(plan["core"], "plan.core")
    if intersect(core, die) != core:
        raise ValueError("plan.core 须完整位于裸片内")
    matches = [p for p in plan["partitions"] if p["id"] == "IP"]
    if len(matches) != 1:
        raise ValueError("plan 须包含恰好一个 ID 为 IP 的分区")
    ip = rect_list(matches[0]["rects"], "IP.rects")
    if not ip:
        raise ValueError("IP 分区须包含至少一个矩形")
    # 可用行由 IP 坐标直接导出。若 IP 伸入 Pad 环，错误新增面积会放宽每一阶段闸门。
    if any(intersect(box, core) != box for box in ip):
        raise ValueError("IP 分区须完整位于 plan.core 内")
    planned = rect_list(plan["ip_row_blockages"], "plan.ip_row_blockages")
    if union_area(ip + planned) != union_area(ip):
        raise ValueError("规划行禁放须位于 IP 分区内")
    utilization = decimal_value(plan["ip_utilization_max"], "ip_utilization_max")
    target = decimal_value(plan["ip_pre_cts_target_um2"], "ip_pre_cts_target_um2")
    if not 0 < utilization <= 1 or target < 0:
        raise ValueError("利用率须在 (0, 1] 且 pre-CTS 目标须为非负面积")
    return die, ip, planned, utilization, target


def extra_blockages(occupancy, die, ip, gross):
    """读取硬宏实体及 halo 和额外行禁放，坐标单位为 µm，返回 IP 内矩形。"""
    if occupancy.get("units") != "um":
        raise ValueError("occupancy.units 须为 um")
    if occupancy.keys() - {"units", "macros", "row_blockages"}:
        raise ValueError("occupancy 仅支持 units、macros、row_blockages")
    macros = occupancy.get("macros", [])
    if not isinstance(macros, list):
        raise ValueError("macros 须为数组")
    footprints, bodies, names = [], [], set()
    for item in macros:
        if not isinstance(item, dict) or item.keys() - {"name", "body", "halo"}:
            raise ValueError("macro 仅支持 name、body、halo")
        name = item["name"]
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("宏名称须非空且唯一")
        names.add(name)
        body = rect(item["body"], f"{name}.body")
        footprint = rect(item["halo"], f"{name}.halo") if "halo" in item else body
        if intersect(body, footprint) != body:
            raise ValueError(f"{name}.halo 须包含完整的宏实体")
        if union_area(ip + [footprint]) != gross:
            raise ValueError(f"{name} 的宏含 halo 足迹超出 IP 分区")
        footprints.append(footprint)
        bodies.append(body)
    # 宏实体重叠属于几何冲突。仅用于禁放行的 halo 可以重叠，随后取并集。
    if any(intersect(a, b) for a, b in combinations(bodies, 2)):
        raise ValueError("硬宏实体发生重叠")

    # 整片 PDN 通道可跨多个分区，只计该条带对 IP 摆放行的几何投影。
    # 出裸片属于非法输入。先验坐标，避免将整条界外的误输入当成没有新增行损。
    for blockage in rect_list(occupancy.get("row_blockages", []), "row_blockages"):
        if intersect(blockage, die) != blockage:
            raise ValueError("额外行禁放 row_blockages 须完整位于裸片内")
        for region in ip:
            overlap = intersect(blockage, region)
            if overlap is not None:
                footprints.append(overlap)
    return footprints


def area_limits(plan, occupancy=None):
    """基于 plan.json 返回规划门槛，面积为 µm²。非法几何或预算引发 ValueError。"""
    die, ip, planned, utilization, target = plan_inputs(plan)
    gross, reserved = union_area(ip), union_area(planned)
    base_rows = gross - reserved
    reserve = base_rows * utilization - target
    if reserve < 0:
        raise ValueError("规划 pre-CTS 目标高于基准 final 容量")

    extra = [] if occupancy is None else extra_blockages(occupancy, die, ip, gross)
    total_blocked = union_area(planned + extra)
    rows = gross - total_blocked
    final = rows * utilization
    # 面积折减为 U×新增禁放面积。保留原定 CTS/ECO 预算，避免在前段透支。
    # 当 final 容量低于该预留时，负的 pre-CTS 门槛明示当前方案无可用预算。
    return {
        "ip_gross_um2": gross,
        "ip_planned_row_blockage_um2": reserved,
        "ip_extra_footprint_union_um2": union_area(extra),
        "ip_extra_row_loss_um2": total_blocked - reserved,
        "ip_effective_rows_um2": rows,
        "ip_utilization_max": utilization,
        "ip_cts_eco_reserve_um2": reserve,
        "ip_pre_cts_max_um2": min(target, final - reserve),
        "ip_final_cells_max_um2": final,
    }


# =============================================================================
# 3. 命令行与可复查结果
# =============================================================================
def main(argv=None):
    parser = argparse.ArgumentParser(description="检查 IP 分区的阶段面积容量")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN, help="统一 floorplan/plan.json")
    parser.add_argument("--occupancy", type=Path, help="宏与额外行禁放的 JSON 文件")
    parser.add_argument("--stage", choices=("pre-cts", "final"))
    parser.add_argument("--cell-area", help="报告中标准单元与常驻行单元的合计面积")
    parser.add_argument("--area-unit", choices=AREA_UNIT_EXPONENT, help="合计面积的已核实单位")
    args = parser.parse_args(argv)

    # 三个报告字段须同时给出。省略全部字段时输出规划容量，避免误判为面积通过。
    supplied = [args.stage is not None, args.cell_area is not None, args.area_unit is not None]
    if any(supplied) and not all(supplied):
        parser.error("--stage、--cell-area、--area-unit 须同时提供")
    try:
        occupancy = read_json(args.occupancy) if args.occupancy is not None else None
        result = area_limits(read_json(args.plan), occupancy)
        result["status"] = "LIMITS_ONLY"
        if args.stage is not None:
            # 单位换算只移动小数点。直接比较面积，避免先计算余量再将微小超额舍入到零。
            digits = decimal_value(args.cell_area, "cell-area").as_tuple()
            exponent = digits.exponent + AREA_UNIT_EXPONENT[args.area_unit]
            area = Decimal((digits.sign, digits.digits, exponent))
            if area < 0:
                raise ValueError("cell-area 须为非负面积")
            key = "ip_pre_cts_max_um2" if args.stage == "pre-cts" else "ip_final_cells_max_um2"
            limit = result[key]
            margin = limit - area
            result.update(stage=args.stage, reported_cells_um2=area, stage_margin_um2=margin,
                          status="AREA_PASS" if area <= limit else "AREA_FAIL")
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        print(f"面积门禁输入无效：{exc}", file=sys.stderr)
        return 2

    # Decimal 使用 JSON 字符串输出，使归档与脚本记录的比较值保持同一精度。
    print(json.dumps(result, default=str, indent=2, ensure_ascii=False))
    return 1 if result["status"] == "AREA_FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
