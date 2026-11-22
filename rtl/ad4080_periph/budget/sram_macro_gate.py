# =============================================================================
# 文件名     : sram_macro_gate.py
# 模块名     : sram_macro_gate
# 版本       : v1.0
# 功能       : 对照项目合同接收供应方的选定 SRAM 实物视图。
# 设计说明   : 项目侧要求和供应方宏声明是独立输入。与实际 LEF、每个必需角
#              Liberty 交叉核对；缺货、缺角或无法解读一律停在 UNKNOWN。
#              读延迟按请求沿至下一消费沿计一拍，硅实现仍需功能模型和 STA。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
import argparse
from decimal import Decimal
import gzip
from hashlib import sha256
import json
from pathlib import Path
import re

from area_gate import DEFAULT_PLAN, decimal_value, intersect, read_json, rect
from sram_view_readers import (LibGroup, MissingEvidence, ViewConflict, agrees, evidence,
                               named_group, read_lef, read_liberty, table_values)


# =============================================================================
# 1. 项目规划和实际朝向
# =============================================================================
SRAM_WORDS = 4096
SRAM_BITS = 20
BANKS = 4
# 每个银行的 12 位地址来自全局地址低位，高两位选银行。
ADDRESS_PINS = 12
MAX_BANK_PERIOD_NS = Decimal(25)  # 40 MSPS 全速捕获或回读，每个字周期最多 25 ns。
SCOPE = "SRAM 实物资料的静态接收，目标库摆放、布线、宏功能仿真、STA 和签核待另行验收"
ORIENTATIONS = {"N", "S", "W", "E", "FN", "FS", "FW", "FE"}
TURNED = {"W", "E", "FW", "FE"}


def label(value, where):
    evidence(isinstance(value, str) and bool(value.strip()), f"{where} 缺字符串标识")
    return value


def names(values, where):
    evidence(isinstance(values, list) and values, f"{where} 缺名称列表")
    labels = [label(value, where) for value in values]
    # 维持原列表位序，同时禁止一根真实 pin 复用作两个逻辑位。
    agrees(len(set(labels)) == len(labels), f"{where} 重复绑定同一名称")
    return labels


def file_view(path):
    # 记录实际读取字节的哈希。压缩 Liberty 的哈希采用供应方原始字节。
    payload = path.read_bytes()
    data = gzip.decompress(payload) if payload.startswith(b"\x1f\x8b") else payload
    return data.decode("utf-8"), {"name": path.name, "sha256": sha256(payload).hexdigest()}


def planned_slots(plan):
    # 四银行窗口按南0/1、北2/3生成，与现有 14 bit 地址高二位一致。
    # 从统一几何重算，不把某个候选宏的尺寸拿来改变可用包络。
    as_given = plan["sram"]
    agrees(plan["units"] == "um" and (as_given["banks"], as_given["words_per_bank"], as_given["bits_per_word"]) ==
           (BANKS, SRAM_WORDS, SRAM_BITS), "统一规划须采用 µm 与 4×4096×20")
    result_windows = [p for p in plan["partitions"] if p["id"] == "FIFO"]
    evidence(len(result_windows) == 1 and len(result_windows[0]["rects"]) == 1, "需要唯一矩形 FIFO 窗")
    # 生产窗口保留三道间隔，两道为外边距，中间一道为银行间接线通道。
    # 逐个银行约束，面积总和相同的长宽交换不会自动被允许。
    left, bottom, right, top = rect(result_windows[0]["rects"][0], "FIFO 窗")
    margin = decimal_value(as_given["outer_margin"], "FIFO 外边距")
    gap = decimal_value(as_given["bank_channel"], "FIFO 银行间通道")
    width, height = (right - left - 2 * margin - gap) / 2, (top - bottom - 2 * margin - gap) / 2
    agrees(min(width, height, margin, gap) > 0, "FIFO 窗扣除边距与通道后须容纳四个正尺寸银行")
    slots = []
    for row in range(2):
        for column in range(2):
            x = left + margin + column * (width + gap)
            y = bottom + margin + row * (height + gap)
            slots.append((x, y, x + width, y + height))
    return slots


def legal_orientations(symmetry):
    # DEF 中 FS 为 X 镜像、FN 为 Y 镜像。有 R90 时允许交换宽高；镜像加
    # 旋转得到两种交换方向。供应商仍须在功能规格中确认实际合法子集。
    legal = {"N"}
    if "X" in symmetry:
        legal.add("FS")
    if "Y" in symmetry:
        legal.add("FN")
    if {"X", "Y"} <= symmetry:
        legal.add("S")
    if "R90" in symmetry:
        legal.update(("N", "W", "S", "E"))
        # 任一镜像与完整旋转组合即可生成另外三种镜像方向。
        if symmetry & {"X", "Y"}:
            legal.update(("FN", "FS", "FW", "FE"))
    return legal


def place_banks(manifest, macro, plan):
    allowed = set(names(manifest["allowed_orientations"], "供应方合法朝向"))
    agrees(allowed <= ORIENTATIONS and allowed <= legal_orientations(macro.symmetry),
           f"供应方合法朝向与 LEF SYMMETRY {sorted(macro.symmetry)} 冲突")
    banks = manifest["banks"]
    evidence(isinstance(banks, list) and len(banks) == BANKS, "需要 B0 至 B3 四个放置记录")
    # 四个包络本身由规划留了净通道，只要各外廓完全包含在各自包络即可保持隔离。
    slot_boxes, result = planned_slots(plan), []
    for index, (placement, slot) in enumerate(zip(banks, slot_boxes)):
        orientation = label(placement["orient"], f"B{index} 朝向")
        agrees(orientation in allowed, f"B{index} 朝向 {orientation} 未获供应方和 LEF 共同支持")
        origin = placement["ll_um"]
        evidence(isinstance(origin, list) and len(origin) == 2, f"B{index} 需要旋转后物理框左下点 ll_um")
        x, y = (decimal_value(value, f"B{index} 左下点") for value in origin)
        width, height = (macro.height, macro.width) if orientation in TURNED else (macro.width, macro.height)
        box = (x, y, x + width, y + height)
        # 每轴独立约束。微小宏可以宽松放置，宏+halo 若超出包络则无法满足已留间距。
        # 接收表使用变换后外框左下点，不能把旋转后的 DEF 锚点直接抄进 ll_um。
        footprint = rect(placement["footprint_um"], f"B{index} 实体及实际 halo 外框")
        # 将尺寸和边界同查双栅格，防止用四舍五入掩盖靠边一小格的越界。
        grids = (macro.dbu, decimal_value(plan["dbu_per_micron"], "规划 DBU"))
        agrees(all(value * grid == int(value * grid) for grid in grids for value in (*box, *footprint)),
               f"B{index} 实际外框坐标超出 LEF 或规划的数据库栅格")
        agrees(intersect(box, footprint) == box and intersect(footprint, slot) == footprint,
               f"B{index} 尺寸或放置越界，朝向 {orientation} 的实际 {width}×{height} µm，包络 {slot}")
        result.append({"bank": index, "orient": orientation, "box_um": box, "footprint_um": footprint, "slot_um": slot})
    return result


# =============================================================================
# 2. 引脚位序与物理图形
# =============================================================================
def pin_bindings(pin_map):
    # 位列表按 RTL 低位到高位，避免按 A10 < A2 等字符串顺序把地址位接错。
    clock = label(pin_map["clock"], "clock 引脚")
    enables = (pin_map["chip_enable"], pin_map["write_enable"])
    # 极性不从 CEN/WEB 等名称猜测。有效电平是功能包装的供应方事实。
    for control in enables:
        evidence(control.get("active") in {"high", "low"}, "片选和写使能须给出明确有效极性")
    addresses = names(pin_map["address"], "地址位绑定")
    inputs = names(pin_map["data_in"], "写数据位绑定")
    outputs = names(pin_map["data_out"], "读数据位绑定")
    agrees((len(addresses), len(inputs), len(outputs)) == (ADDRESS_PINS, SRAM_BITS, SRAM_BITS),
           "位绑定必须给出 12 地址、20 写数据与 20 读数据")
    pg = pin_map["pg"]
    evidence(isinstance(pg, dict) and pg and all(isinstance(key, str) and key for key in pg), "需要 PG 实际端口映射")
    agrees(set(pg.values()) == {"power", "ground"}, "PG 映射须含电源与地且用法合法")
    # 额外控制输入需要显式绑定常量。漏列未知 TEST/SLEEP 不等于进入正常模式。
    ties = pin_map.get("tieoffs", {})
    evidence(isinstance(ties, dict) and all(type(value) is int and value in {0, 1} for value in ties.values()),
             "辅助输入 tieoffs 须为已批准的 0/1")
    entries = [clock, *(label(item["name"], "控制端口") for item in enables), *addresses, *inputs, *outputs, *pg, *ties]
    agrees(len(entries) == len(set(entries)), "宏引脚存在跨角色或跨位重复绑定")
    return clock, {name: "output" if name in outputs else "input" for name in entries if name not in pg}, pg, outputs


def check_lef(source, macro, routing_layers, clock, signals, pg):
    agrees(set(macro.pins) == set(signals) | set(pg),
           f"LEF 引脚与绑定不符，缺 {sorted((set(signals) | set(pg)) - set(macro.pins))}，有未绑定端 {sorted(set(macro.pins) - (set(signals) | set(pg)))}")
    for name, port in macro.pins.items():
        if name in pg:
            agrees(port.use == (pg[name].upper(),) and port.direction in {("INOUT",), ("INPUT",)},
                   f"LEF {name} 的 PG USE 或方向与绑定不符")
        else:
            # 宏 LEF 可把时钟标为普通 SIGNAL。对应 Liberty 仍须显式声明 clock。
            signal_uses = {("SIGNAL",), ("CLOCK",)} if name == clock else {("SIGNAL",)}
            agrees(port.direction == (signals[name].upper(),) and port.use in signal_uses,
                   f"LEF {name} 的 USE 或方向与绑定不符")
    shapes = [shape for p in macro.pins.values() for shape in p.shapes] + macro.obs
    layers = {layer for layer, _ in shapes}
    agrees(layers <= routing_layers, f"LEF 使用未获批准的物理层 {sorted(layers - routing_layers)}")
    # PORT/OBS 顶点应留在宏体且沿 LEF 数据库网格。检查真实原语坐标，
    # 不以 SIZE 和 pin 个数替代图形读取。
    for layer, points in shapes:
        for x, y in points:
            agrees(0 <= x <= macro.width and 0 <= y <= macro.height,
                   f"LEF {layer} 的 PORT/OBS 顶点 {(x, y)} 超出 SIZE")
            agrees(x * macro.dbu == int(x * macro.dbu) and y * macro.dbu == int(y * macro.dbu),
                   f"LEF {layer} 的 PORT/OBS 顶点未对齐 DBU")
    return {**source, "dbu_per_um": macro.dbu, "size_um": (macro.width, macro.height), "origin_um": macro.origin,
            "symmetry": sorted(macro.symmetry), "pin_count": len(macro.pins), "obs_shapes": len(macro.obs), "layers": sorted(layers)}


# =============================================================================
# 3. 各角实际 Liberty 视图
# =============================================================================
TIME_TO_NS = {"fs": Decimal("0.000001"), "ps": Decimal("0.001"), "ns": Decimal(1), "us": Decimal(1000)}


def time_scale(raw_unit):
    # 宏供应的多个角可以不同时间单位。统一换算后才与项目 ns 预算比较。
    unit = re.fullmatch(r"([0-9.]+)\s*(fs|ps|ns|us)", raw_unit)
    evidence(unit, f"不支持的 Liberty 时间单位 {raw_unit}")
    scale = decimal_value(unit[1], "Liberty 时间单位") * TIME_TO_NS[unit[2]]
    evidence(scale > 0, "Liberty 时间单位须大于零")
    return scale


def liberty_signal_pins(library, cell):
    leaves = cell.groups("pin")
    for bus in cell.groups("bus"):
        # 显式 bit 覆盖总线属性。纯总线模板按实际 type 和命名规则展开，
        # 然后再比较 LEF/RTL 位名。禁止根据数组顺序或宏名猜模板宽度。
        members = bus.groups("pin")
        if not members:
            evidence(len(bus.args) == 1, "Liberty bus 需唯一名称")
            array = named_group(library.groups("type"), bus.value("bus_type"), "Liberty bus type")
            evidence(array.value("base_type") == "array" and array.value("data_type") == "bit", "仅可展开 Liberty bit array")
            i, j = (decimal_value(array.value(key), f"Liberty {key}") for key in ("bit_from", "bit_to"))
            width = decimal_value(array.value("bit_width"), "Liberty bit_width")
            evidence(i == int(i) and j == int(j) and min(i, j) >= 0 and abs(i - j) + 1 == width <= SRAM_BITS,
                     f"Liberty 总线 {bus.args} 的位号或宽度不可展开")
            style = library.value("bus_naming_style")
            evidence(style in {"%s[%d]", "%s<%d>"}, f"未支持的 bus_naming_style {style}")
            members = [LibGroup("pin", (style % (bus.args[0], bit),)) for bit in range(int(min(i, j)), int(max(i, j)) + 1)]
        for member in members:
            evidence(len(member.args) == 1 and not re.search(r"[\[<]\d+:\d+[\]>]$", member.args[0]),
                     f"Liberty bit pin {member.args} 使用未展开的范围声明")
            leaves.append(LibGroup("pin", member.args, {**bus.attrs, **member.attrs},
                                   member.children or bus.children, member.calls))
    evidence(all(len(pin.args) == 1 for pin in leaves), "Liberty PIN 名未明确展开")
    result = {pin.args[0]: pin for pin in leaves}
    agrees(len(result) == len(leaves), "Liberty bit pin 重名")
    return result


def period_values(clock_pin, clock):
    # Liberty 可在时钟 pin 直接给最小周期，或分 rise/fall 特性表给出。
    # 同时提供时取最严格值，缺少任何可读约束时接收保持待验。
    periods = [decimal_value(clock_pin.attrs[key], "Liberty 最小周期")
               for key in ("min_period", "minimum_period") if key in clock_pin.attrs]
    for timing in clock_pin.groups("timing"):
        if timing.attrs.get("timing_type") in {"min_period", "minimum_period"}:
            evidence(timing.attrs.get("related_pin", clock) == clock, "最小周期时序弧未绑定到 SRAM 时钟")
            for table in timing.groups("rise_constraint") + timing.groups("fall_constraint"):
                periods.extend(table_values(table))
    evidence(periods and min(periods) > 0, "Liberty 时钟缺可读且为正的最小周期")
    return periods


def clock_to_q_values(signal, clock):
    # 每个读位需要从同一接受沿出发的实际时序弧。最大值按已提供表点取包络。
    arcs = [arc for arc in signal.groups("timing")
            if arc.attrs.get("related_pin") == clock and arc.attrs.get("timing_type") == "rising_edge"]
    evidence(arcs, f"Liberty {signal.args[0]} 缺 {clock} 上升沿至 Q 的时序弧")
    values = []
    for arc in arcs:
        for kind in ("cell_rise", "cell_fall"):
            tables = arc.groups(kind)
            evidence(tables, f"Liberty {signal.args[0]} 缺 {kind} 表")
            for table in tables:
                values.extend(table_values(table))
    return values


def check_corner(raw, required, name, clock, signals, pg, outputs):
    library = read_liberty(raw)
    # 文件名可以被重命名。角身份由真实默认条件及 P、V、T 三个数共同确认。
    # 以批准的工作角核对，避免从同一个库里随手选择更快的条件。
    oc_name = label(required["operating_conditions"], "必需角 operating_conditions")
    agrees(library.value("default_operating_conditions") == oc_name, f"Liberty 默认工艺角与批准角 {oc_name} 不一致")
    oc = named_group(library.groups("operating_conditions"), oc_name, "Liberty 工艺角")
    scale = time_scale(library.value("time_unit"))
    evidence(library.value("voltage_unit") == "1V", "需以 1V 表示 Liberty 电压单位")
    measured = {}
    for field, key in (("process", "process"), ("voltage", "voltage_v"), ("temperature", "temperature_c")):
        actual = decimal_value(oc.value(field), f"Liberty {field}")
        wanted = decimal_value(required[key], f"项目 {key}")
        agrees(actual == wanted, f"工艺角 {oc_name} 的 {field} 为 {actual}，项目要求 {wanted}")
        measured[key] = actual

    cell = named_group(library.groups("cell"), name, "Liberty cell")
    # 供应方的协议声明必须与原始 memory 组兼容。缺组时源于同版接口规格，
    # 仍要求用独立读回的地址、数据、时钟与供电端完全匹配。
    for group in cell.groups("memory"):
        agrees(group.value("type") == "ram" and decimal_value(group.value("address_width"), "地址位数") == ADDRESS_PINS and
               decimal_value(group.value("word_width"), "数据位数") == SRAM_BITS, "Liberty memory 组织与 4096×20 矛盾")
    lib_pins = liberty_signal_pins(library, cell)
    agrees(set(lib_pins) == set(signals), "Liberty 信号 bit pin 集合与 LEF/RTL 绑定不相符")
    for label_name, direction in signals.items():
        agrees(lib_pins[label_name].value("direction") == direction, f"Liberty {label_name} 的方向不符")
    lib_pg = cell.groups("pg_pin")
    agrees(len(lib_pg) == len(pg) and {pin.args for pin in lib_pg} == {(key,) for key in pg}, "Liberty pg_pin 集合与绑定不相符")
    for pin in lib_pg:
        pg_type = pin.value("pg_type")
        agrees(pg_type in {f"primary_{pg[pin.args[0]]}", f"backup_{pg[pin.args[0]]}", f"internal_{pg[pin.args[0]]}"},
               f"Liberty {pin.args[0]} 的 pg_type 与 PG 绑定不一致")
    agrees(lib_pins[clock].value("clock") == "true", f"Liberty {clock} 未声明为时钟")
    required_period = decimal_value(required["clock_period_ns"], "项目宏时钟周期")
    tcq_budget = decimal_value(required["max_clock_to_q_ns"], "项目最大时钟至读出预算")
    agrees(0 < tcq_budget < required_period <= MAX_BANK_PERIOD_NS,
           "时钟至读出预算须低于消费沿周期，且 40 MSPS 方案的银行周期须不超过 25 ns")
    minimum_period = max(period_values(lib_pins[clock], clock)) * scale
    max_cq = max(value for output in outputs for value in clock_to_q_values(lib_pins[output], clock)) * scale
    agrees(minimum_period <= required_period and 0 <= max_cq <= tcq_budget,
           f"{oc_name} 最小周期 {minimum_period} ns / 最大 tCQ {max_cq} ns 超出批准上限 {required_period}/{tcq_budget} ns")
    return {"library": library.args[0], "operating_conditions": oc_name, **measured, "liberty_time_unit_ns": scale,
            "minimum_period_ns": minimum_period, "max_clock_to_q_ns": max_cq,
            "budget_clock_period_ns": required_period, "budget_max_clock_to_q_ns": tcq_budget}


# =============================================================================
# 4. 文件接收、证据与退出码
# =============================================================================
def qualify(plan_path, requirements_path, candidate_path, fixture=False):
    req, supplier = read_json(requirements_path), read_json(candidate_path)
    source = "fixture" if fixture else "supplier"
    agrees(supplier["source_kind"] == source, "公开夹具须显式用 --fixture，实物接收须使用供应方资料")
    process, release = label(req["process_id"], "批准工艺"), label(req["release"], "批准工艺版本")
    agrees(supplier["process_id"] == process and supplier["release"] == release, "供应方工艺或发布版本与项目批准值不一致")
    agrees(req["technology_node_nm"] == supplier["technology_node_nm"] == 40,
           "本项目要求双方清单均指向已批准的 40 nm 目标工艺")
    compiler = label(supplier["compiler_id"], "供应方编译器标识")
    revision = label(supplier["macro_revision"], "供应方视图版本")
    name = label(supplier["name"], "供应方宏名")
    # 1RW、读周期和全字写来自供应方规格。只靠 PIN 数或 Liberty tCQ 推不出协议。
    contract = (supplier["words"], supplier["bits_per_word"], supplier["port_model"], supplier["read_cycles"],
                supplier["write_width_bits"], supplier["clock_edge"])
    agrees(contract == (SRAM_WORDS, SRAM_BITS, "1RW", 1, SRAM_BITS, "rising"),
           f"宏功能需满足 4096×20 同步单端口 1RW、读一拍与全字写，实收 {contract}")
    routing_layers = set(names(req["allowed_physical_layers"], "批准的针脚与 OBS 层"))
    required_corners = req["required_pvt"]
    evidence(isinstance(required_corners, dict) and required_corners, "项目须冻结非空的 required_pvt 清单")
    evidence(isinstance(supplier["liberty_by_pvt"], dict), "缺少供应方按角 Liberty 索引")
    missing = sorted(required_corners.keys() - supplier["liberty_by_pvt"].keys())
    evidence(not missing, f"缺少必需 PVT 的 Liberty {missing}")

    clock, signals, pg, outputs = pin_bindings(supplier["pins"])
    raw, lef_artifact = file_view(candidate_path.parent / label(supplier["lef"], "供应方 LEF"))
    macro = read_lef(raw, name)
    lef = check_lef(lef_artifact, macro, routing_layers, clock, signals, pg)
    placed = place_banks(supplier, macro, read_json(plan_path))
    corners = {}
    # 每一角从独立的供应视图读回。前面某一角已通过也不能弥补后面缺角。
    for corner, required in required_corners.items():
        try:
            raw, artifact = file_view(candidate_path.parent / label(supplier["liberty_by_pvt"][corner], "Liberty"))
            corners[corner] = {**artifact, **check_corner(raw, required, name, clock, signals, pg, outputs)}
        except (MissingEvidence, ViewConflict) as exc:
            raise type(exc)(f"PVT {corner}：{exc}") from exc
    digests = {tag: file_view(path)[1] for tag, path in
               (("plan", plan_path), ("requirements", requirements_path), ("candidate_manifest", candidate_path))}
    return {"source_kind": source, "part": name, "process_id": process, "release": release, "compiler_id": compiler,
            "macro_revision": revision, "bank_count": BANKS, "words_per_bank": SRAM_WORDS, "bits_per_word": SRAM_BITS,
            "port_model": "1RW", "read_cycles": 1, "pin_binding": supplier["pins"], "banks": placed,
            "lef": lef, "pvt": corners, "input_digests": digests}


def main(argv=None):
    parser = argparse.ArgumentParser(description="实际 SRAM 编译宏的 LEF/Liberty 与功能规格静态接收")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--requirements", type=Path, help="项目批准的工艺版本、真实层及必需 PVT 和时间预算")
    parser.add_argument("--candidate", type=Path, help="供应方宏规格和视图路径索引")
    parser.add_argument("--fixture", action="store_true", help="仅运行公开夹具，返回 FIXTURE_PASS")
    args = parser.parse_args(argv)
    try:
        # 没有实物清单时开放项保留待验，入口不尝试寻找其他工艺的占位文件。
        evidence(args.requirements and args.candidate, "缺少项目批准的工艺/PVT 要求和实际供应方宏清单，接收待验")
        details = qualify(args.plan, args.requirements, args.candidate, args.fixture)
        status, code = ("FIXTURE_PASS" if args.fixture else "PASS"), 0
    except ViewConflict as error:
        status, code, details = "FAIL", 1, {"reason": str(error)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ArithmeticError, RecursionError) as error:
        status, code, details = "UNKNOWN", 2, {"reason": str(error)}
    # 缺证不使用 0 退出，夹具回执带专用标志供汇总脚本过滤。
    print(json.dumps({"status": status, "scope": SCOPE, **details}, ensure_ascii=False, default=str, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
