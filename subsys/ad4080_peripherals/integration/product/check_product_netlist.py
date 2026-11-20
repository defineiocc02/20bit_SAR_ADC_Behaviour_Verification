# =============================================================================
# 文件名     : check_product_netlist.py
# 模块名     : check_product_netlist
# 版本       : v1.0
# 功能       : 核对同一 DSP 控制链、唯一物理 SRAM 和原组帧叶子的通用 JSON。
# 设计说明   : 从指定顶层逐实例展开，因而四个宏型相同的银行分别计数。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import json
import sys
from pathlib import Path


def int_value(value):
    """读取 Yosys JSON 中的位参数。常量 WIDTH/SIZE 必须无未知位。"""
    return int(value, 2) if isinstance(value, str) else int(value)


def walk(modules, typename, path):
    """递归计出真实实例。仅进入 JSON 实际包含的用户层级。"""
    cfg = modules[typename]
    yield path, cfg.get("attributes", {}).get("hdlname", typename), None
    for name, cell in cfg.get("cells", {}).items():
        cellpath = f"{path}.{name}"
        celltype = cell["type"]
        if celltype in modules:
            yield from walk(modules, celltype, cellpath)
        elif celltype == "$mem_v2":
            params = cell["parameters"]
            yield cellpath, celltype, (int_value(params["SIZE"]), int_value(params["WIDTH"]))


def main():
    """检查由工具生成的 JSON，未展开或结构不符即返回失败。"""
    modules = json.loads(Path(sys.argv[1]).read_text())["modules"]
    top = "ad4080_peripheral_product"
    required = {
        "u_dsp": "ad4080_top",
        "u_dsp.u_spi": "spi_slave",
        "u_dsp.u_chain": "ad4080_dsp_chain",
        "u_dsp.u_chain.u_sinc1": "sinc1_decim",
        "u_dsp.u_event": "event_detect",
        "u_dsp.u_capture": "fifo_capture",
        "u_dsp.u_capture.u_fifo": "result_fifo",
        "u_services.u_result_store": "result_store",
        "u_record.u_framer": "ad4080_fifo_record_framer",
    }
    # [证据] 队列控制叶仍存在，但任一 DSP 子层出现 RAM 即是第二份存储。
    nodes = list(walk(modules, top, top))
    for path, wanted in required.items():
        path = f"{top}.{path}"
        matches = [name for actual, name, _ in nodes if actual == path]
        if matches != [wanted]:
            raise SystemExit(f"wrong: {path} = {matches}; expected {wanted}")
        if sum(name == wanted for _, name, _ in nodes) != 1:
            raise SystemExit(f"expected one instance of {wanted}")
    # [配置] 模块名存在还不能证明工程增益和降序配置已绑定。检查 u_dsp
    # 的实际派生模块参数，而非把 runner 的命令行值重复报告为证据。
    dsp_type = modules[top]["cells"]["u_dsp"]["type"]
    dsp_params = modules[dsp_type]["parameter_default_values"]
    shift = int(sys.argv[2]) if len(sys.argv) == 3 else 14
    if shift not in (14, 15):
        raise SystemExit(f"unsupported product fixture shift: {shift}")
    profile = {
        "FIFO_DEPTH": 16384, "IN_W": 20, "P_GAIN_MAP_VALID": 1, "K_GAIN": 1, "SHIFT": shift,
        "P_MULTIBYTE_ORDER": 0, "P_OFFSET_LSB_ADDR": 37, "P_WRITE_SDO_DRIVE": 1, "P_COMP_COEFF_VALID": 0,
    }
    for name, expected in profile.items():
        if int_value(dsp_params[name]) != expected:
            raise SystemExit(f"wrong elaborated binding {name}")
    banks = sorted((path, shape) for path, _, shape in nodes if shape)
    physical = [shape for path, shape in banks if ".u_services.u_result_store." in path]
    others = [shape for path, shape in banks if ".u_services.u_result_store." not in path]
    if physical != [(4096, 20)] * 4 or others != [(8, 22)]:
        raise SystemExit(f"unexpected physical/transport storage: {banks}")
    print("PRODUCT_STRUCTURE PASS: original DSP/control leaves, four 4096x20 result banks, one 8x22 transport queue")
    print(f"DSP engineering mapping K_GAIN=1 SHIFT={shift}")
    print("DSP config SPI P_WRITE_SDO_DRIVE=1")
    for path, shape in banks:
        print(f"{path}: {shape[0]}x{shape[1]}")


if __name__ == "__main__":
    main()
