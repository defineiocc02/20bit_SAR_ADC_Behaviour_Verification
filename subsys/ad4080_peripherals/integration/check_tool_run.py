# =============================================================================
# 文件名     : check_tool_run.py
# 模块名     : check_tool_run
# 版本       : v1.0
# 功能       : 用完成标记和实际网表验收仿真及可选通用综合。
# 设计说明   : 每次工具独用新工作目录。成功退出但提前中止、遗漏网表或选错
#              顶层均不能通过。层级计数包含同一叶子在各级的每次实例化。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path


# =============================================================================
# 1. 按完成产物验收
# =============================================================================
def check_simulation(log_path: Path, marker: str) -> None:
    """期望结束行必须单独出现一次。仅读新日志，缺失时终止入口。"""
    if log_path.read_text(encoding="utf-8").splitlines().count(marker) != 1:
        raise ValueError(f"missing or repeated simulation end marker: {marker}")


def integer_bits(field: str | int) -> int:
    """Yosys JSON 固定参数通常为二进制串，兼容工具输出的十进制整数。"""
    return int(field, 2) if isinstance(field, str) else int(field)


def memory_shapes(modules: dict, module_name: str) -> list[tuple[int, int]]:
    """沿已展开层级读取每个抽象存储的字数和位宽，按实例累加。"""
    shapes = []
    for cell in modules[module_name]["cells"].values():
        cell_type = cell["type"]
        if cell_type == "$mem_v2":
            params = cell["parameters"]
            shapes.append((integer_bits(params["SIZE"]), integer_bits(params["WIDTH"])))
        elif cell_type in modules:
            shapes.extend(memory_shapes(modules, cell_type))
    return shapes


def check_synthesis(netlist_path: Path, log_path: Path, top: str, memory: str | None) -> None:
    """核对结构检查、指定顶层及所选默认存储合同，仅读取本轮 JSON/log。"""
    log = log_path.read_text(encoding="utf-8")
    if f"Checking module {top}..." not in log or "Found and reported 0 problems." not in log:
        raise ValueError(f"Yosys did not complete structural checking for {top}")
    modules = json.loads(netlist_path.read_text(encoding="utf-8"))["modules"]
    declared_tops = [name for name, data in modules.items()
                     if integer_bits(data.get("attributes", {}).get("top", "0")) == 1]
    if declared_tops != [top]:
        raise ValueError(f"wrong JSON top: {declared_tops}, expected [{top}]")
    messages = {
        "result_banks": ([(4096, 20)] * 4, "four 4096 x 20 result banks"),
        "record_link": ([(8, 22)], "one 8 x 22 transport queue"),
    }
    if memory:
        expected, shape_label = messages[memory]
        if sorted(memory_shapes(modules, top)) != expected:
            raise ValueError(f"expected {shape_label}")
        print(f"Verified Yosys JSON top {top}, {shape_label}")
    else:
        print(f"Verified Yosys JSON top {top}")


def main() -> None:
    """读取 runner 明确指定的本轮产物，不把工具退出码视为结构证据。"""
    parser = argparse.ArgumentParser()
    command = parser.add_subparsers(dest="kind", required=True)
    sim = command.add_parser("simulation")
    sim.add_argument("log", type=Path)
    sim.add_argument("marker")
    synth = command.add_parser("yosys")
    synth.add_argument("netlist", type=Path)
    synth.add_argument("log", type=Path)
    synth.add_argument("top")
    synth.add_argument("--memory", choices=("result_banks", "record_link"))
    args = parser.parse_args()
    try:
        if args.kind == "simulation":
            check_simulation(args.log, args.marker)
        else:
            check_synthesis(args.netlist, args.log, args.top, args.memory)
    except (OSError, KeyError, ValueError) as error:
        raise SystemExit(f"Verification artifact check failed: {error}") from error


if __name__ == "__main__":
    main()
