# =============================================================================
# 文件名     : check_product_netlist.py
# 模块名     : check_product_netlist
# 版本       : v1.0
# 功能       : 核对同一 DSP 控制链、唯一物理 SRAM 与两种结果路径的通用 JSON。
# 设计说明   : 从指定顶层逐实例展开，因而四个宏型相同的银行分别计数。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import json
import sys
from pathlib import Path


# =============================================================================
# 1. 层级、连线和真实部件
# =============================================================================
def int_value(value):
    """读取 Yosys JSON 中的位参数。常量 WIDTH/SIZE 必须无未知位。"""
    return int(value, 2) if isinstance(value, str) else int(value)


def walk(modules, typename, path):
    """递归计出真实实例。仅进入 JSON 实际包含的用户层级。"""
    cfg = modules[typename]
    # 参数化实例的工具名含派生哈希。用 HDL 类型查数量，用走到该处的真实
    # 实例路径查归属，因此四个同型号银行不会被折算成一个叶子。
    yield path, cfg.get("attributes", {}).get("hdlname", typename), None
    for name, cell in cfg.get("cells", {}).items():
        cellpath = f"{path}.{name}"
        celltype = cell["type"]
        if celltype in modules:
            yield from walk(modules, celltype, cellpath)
        elif celltype == "$mem_v2":
            params = cell["parameters"]
            yield cellpath, celltype, (int_value(params["SIZE"]), int_value(params["WIDTH"]))


def check_continuous_cdc(modules, top):
    """根据综合网表的实际数据连线和寄存器确认连续输出的两槽接力。"""
    children = modules[top]["cells"]
    dsp = children["u_dsp"]["connections"]
    bridge = children["u_stream_cdc"]["connections"]
    stream = children["u_stream"]["connections"]
    # [来源] 原 DSP 的结果和同笔标签都必须经过桥再到连续发送调度器。
    # 对照端口位网号，以免同名层级仍存在而其中一段被本地常数替代。
    for dsp_port, source_port, dest_port, stream_port, width in (
        ("continuous_word_o", "word_core_i", "word_io_o", "result_i", 20),
        ("continuous_word_tag_o", "tag_core_i", "tag_io_o", "result_tag_i", 32),
    ):
        if len(dsp[dsp_port]) != width or dsp[dsp_port] != bridge[source_port]:
            raise SystemExit(f"continuous CDC input is not bound to DSP port {dsp_port}")
        if len(stream[stream_port]) != width or stream[stream_port] != bridge[dest_port]:
            raise SystemExit(f"continuous CDC output is not bound to stream port {stream_port}")

    cfg = modules[children["u_stream_cdc"]["type"]]
    core_clock = cfg["ports"]["clk_core_i"]["bits"]
    # [实例] 两个 52 位槽在此实现为独立触发器组，不是泛型 mem_v2。
    # 先核实际 Q 驱动，再核时钟和宽度，不能只用 netname 判断存储存在。
    for slot in ("word0_q", "word1_q"):
        bits = cfg["netnames"][slot]["bits"]
        drivers = [
            cell for cell in cfg["cells"].values()
            if cell["connections"].get("Q") == bits
        ]
        if len(drivers) != 1:
            raise SystemExit(f"continuous CDC slot {slot} must have one register driver")
        driver = drivers[0]
        if (driver["type"] != "$dffe" or driver["connections"]["CLK"] != core_clock
                or int_value(driver["parameters"]["WIDTH"]) != 52):
            raise SystemExit(f"wrong continuous CDC slot {slot}")


def main():
    """检查由工具生成的 JSON，未展开或结构不符即返回失败。"""
    modules = json.loads(Path(sys.argv[1]).read_text())["modules"]
    top = "ad4080_peripheral_product"
    required = {
        "u_dsp": "ad4080_top",
        "u_dsp.u_spi": "spi_slave",
        "u_dsp.u_chain": "ad4080_dsp_chain",
        "u_dsp.u_chain.u_sinc1": "sinc1_decim",
        "u_dsp.u_chain.u_comp": "sinc5_comp",
        "u_dsp.u_chain.u_comp.u_sinc5": "sinc5_decim",
        "u_dsp.u_event": "event_detect",
        "u_dsp.u_capture": "fifo_capture",
        "u_dsp.u_capture.u_fifo": "result_fifo",
        "g_cnv_interval.u_cnv": "cnv_ctrl",
        "u_services.u_result_store": "result_store",
        "u_services.u_power_cdc": "ad4080_cfg_power_cdc",
        "u_record.u_framer": "ad4080_fifo_record_framer",
        "u_record.g_spi_result.u_pins": "ad4080_result_spi_pins",
        "u_stream_config": "ad4080_lvds_config_cdc",
        "u_stream_cdc": "ad4080_continuous_result_cdc",
        "u_stream": "ad4080_continuous_lvds",
    }
    # 一个共享 CIC 同时供 SINC5 和 PF1；SPIDCLK 只挂在同一个组帧器后。
    # 仅检查模块在文件清单中存在会漏掉意外重复或死逻辑。
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
        "P_MULTIBYTE_ORDER": 0, "P_OFFSET_LSB_ADDR": 37, "P_WRITE_SDO_DRIVE": 1,
        "P_SHORT_ADDR_BITS": 7, "P_PRODUCT_ID_L": 80,
        "P_CNV_TIMING_BOUND": 1, "P_CNV_ERROR_BOUND": 1, "P_PHY_FIFO_STATUS_BOUND": 1,
        "P_POR_ANA_EVENT_ENABLE": 1, "P_CONTINUOUS_LVDS_BOUND": 1, "P_AON_CFG_BOUND": 1,
    }
    for name, expected in profile.items():
        if int_value(dsp_params[name]) != expected:
            raise SystemExit(f"wrong elaborated binding {name}")
    top_params = modules[top]["parameter_default_values"]
    if int_value(top_params["CORE_CLOCK_HZ"]) != 100_000_000 or int_value(top_params["P_CNV_TIMING_BOUND"]) != 1:
        raise SystemExit("wrong elaborated CNV timing/profile")
    banks = sorted((path, shape) for path, _, shape in nodes if shape)
    # SRAM/运输队列保留抽象宏样式便于算阵列规模。连续两个寄存器槽另验，
    # 既防把连续槽误报为结果宏，也防原 DSP 队列隐藏第二套大容量。
    physical = [shape for path, shape in banks if ".u_services.u_result_store." in path]
    others = [(path, shape) for path, shape in banks if ".u_services.u_result_store." not in path]
    record_queue = f"{top}.u_record.u_readback.u_transport.fifo_memory_q"
    if physical != [(4096, 20)] * 4 or others != [(record_queue, (8, 22))]:
        raise SystemExit(f"unexpected physical/transport storage: {banks}")
    check_continuous_cdc(modules, top)
    print("PRODUCT_STRUCTURE PASS: original DSP/control leaves, four 4096x20 result banks, one 8x22 transport queue")
    print("PRODUCT_CONTINUOUS_STRUCTURE PASS: original DSP code/tag routed through two 52-bit CDC registers")
    print(f"DSP engineering mapping K_GAIN=1 SHIFT={shift}")
    print("DSP config SPI P_WRITE_SDO_DRIVE=1")
    print("PRODUCT_TIMING_BOUND CORE_CLOCK_HZ=100000000 P_CNV_TIMING_BOUND=1")
    for path, shape in banks:
        print(f"{path}: {shape[0]}x{shape[1]}")


if __name__ == "__main__":
    main()
