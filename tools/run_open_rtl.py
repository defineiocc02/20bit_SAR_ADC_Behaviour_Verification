#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_open_rtl.py
# 模块名     : run_open_rtl
# 版本       : v1.0
# 功能       : 只运行显式选择的自检组件，不隐式复用输出、参数或通过记录。
#
# 设计说明   :
#   * --out 必须是不存在且位于版本库外的目录。每个 --tops 项只编译已登记的依赖，
#   * 不扫描整个工程，也不提供旧配置、参考向量或“已知失败即通过”的默认集合。
#   * 需完整核输入的测试必须提供源导出器生成的 --inputs-dir；组件数学夹具不代表器件。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =========================================================================
# 附：自检流程与证据纪律（本文件专用）
# =========================================================================
# -------------------------------------------------------------------------
# [入口] main() 按显式 --tops 选择组件；不扫描整个工程、不复用旧输出、
#        不提供「已知失败即通过」的默认集合。缺依赖、编译、执行或完成计数
#        错误一律以非零状态退出，避免把空跑记成 PASS。
# [输出] new_output() 拒绝已存在目录，也拒绝任何 Git 工作树内的位置；
#        --out 必须是版本库外的新目录，防止覆盖既有收据或污染参考输出。
# [标识] digest() 用文件内容的 SHA-256 而非文件名标识输入；
#        validate_inputs() 核对显式输入包中复制的字节，不要求原 sv_path
#        仍可访问，因此输入包一旦冻结即可离线复演。
# [编译] run_bench() 为每个所选组件独立编译，临时对象目录不复用旧二进制；
#        verilator_compiler_args() 显式选择 clang++ 与 C++20 参数，
#        不依赖 make 的隐式编译器选择，避免不同机器上换编译器造成口径漂移。
# [运行] checked_run() 保留完整日志并核对真实返回码；超时、%Error/%Fatal、
#        UVM_ERROR、断言失败等诊断都判为失败，不因「有日志」就算通过。
#        ERROR_DIAGNOSTIC 正是把这些诊断从日志里单独抓出来的判据。
# [完成] completion() 要求唯一整行完成记录，且每个必需字段为正计数；
#        单独出现 finish 或零计数都不算完成，防止截断日志被误判为成功。
# [环境] command_for() 只解析调用方已配置的工具，不安装工具、不补写供应商
#        或许可证环境；alias_runtime() 仅为完整运行库建立 ASCII 路径别名，
#        保留原安装布局，避免路径含非 ASCII 字符时编译链静默失败。
# [范围] SCOPE_COMPONENT = COMPONENT_SYNTHETIC_DIGITAL 是证据范围标注：
#        组件数学夹具不代表器件，不能外推为模拟宏或硅上结论。
# [等级] 本脚本产出的是 Python 驱动 + 工具日志的**数字自检证据**；
#        状态沿用 PASS / FAIL / BLOCKED / NOT_RUN / WAIVED。工具缺失记
#        BLOCKED，不得从「无日志」推成 PASS；归档收据必须同时带
#        source_sha256 与 log_sha256，且只在同源码哈希下有效。
# [边界] 本文件不涉及 dither 极性，C9 的 k=c−U 由被测 RTL 负责；
#        它也不是综合/STA/DRC/LVS 签核，物理结论以对应物理 README 为准。
# -------------------------------------------------------------------------
# =========================================================================
# 附：证据字段、顶层常量与失败路径（按本文件实现逐项核对）
# =========================================================================
# -------------------------------------------------------------------------
# [常量] REPO 以 __file__ 的父目录定位仓库根，不依赖当前工作目录；
#        SCOPE_COMPONENT 标注证据范围为组件合成数字，不代表器件。
# [常量] ERROR_DIAGNOSTIC 从日志单独抓 %Error / %Fatal / Error: / Fatal: /
#        Error-[ / UVM_ERROR / Assertion failed；命中即失败，不只看返回码。
# [常量] BENCHES / CORE_SOURCES / SPECIAL_TOPS 是显式登记的顶层、公共源
#        与特殊顶层依赖；没有自动扫描，新增顶层必须显式登记才会被编译。
# [环境] VERILATOR_ROOT 由调用方给出；工具缺失记 BLOCKED，不隐式安装或替换。
# [收据] write_json 落盘字段按用途分三组，缺任一组都不能复演：
#   1) 运行身份：scope / top / module / parameters / argv / directory；
#   2) 内容指纹：source_sv / input_sha256 / sources_sha256 /
#      artifact_sha256 / payload_sha256 / log_sha256；用内容而非文件名标识；
#   3) 判定结果：status / raw_returncode / execution_error / counts /
#      marker / open_boundaries / input_bundle。
# [文件] compile.log、run.log 保留完整原始日志；result.json 是本次判定；
#        manifest.json、config.json、format.json、binding.json、
#        rtl_input_manifest.json、packet_schema.json、protocol_fixture.json、
#        math_format.json 是输入包与绑定的结构化清单，供离线复演核对。
# [失败路径] 四类行为不同，不能混为一类：
#   a) 工具或依赖缺失 -> BLOCKED，不产生 PASS 收据；
#   b) 编译或执行返回码非零 -> FAIL，保留 compile.log / run.log；
#   c) 日志命中 ERROR_DIAGNOSTIC 或超时 -> FAIL，即使返回码为 0；
#   d) 完成记录缺失、重复或字段零计数 -> FAIL，不把单独 finish 当完成。
# [完成] marker 必须是唯一整行；counts 的每个必需字段须为正计数。
# [复演] 用 sources_sha256 / input_sha256 锁定输入，在干净的仓库外目录
#        重跑同一 argv；哈希不一致时归档结论不适用，须重新取证。
# [等级] PASS 只表示本次自检组件在给定输入与工具下通过，不等于
#        20 位有效精度、模拟宏验收或工艺签核结论。
# -------------------------------------------------------------------------
# 逐函数合同（按定义顺序，行号对应本文件当前版本）
# -------------------------------------------------------------------------
# [F01] Bench()：声明一个自检顶层的源依赖、完成标记及计数。
#       @dataclass(frozen=True) 保证运行期配置不被改写；不据此推断整个工程的覆盖。
# -------------------------------------------------------------------------
# [F02] digest()：计算实际文件的 SHA256，用内容而不是文件名标识输入。
#       同名不同内容必须能被区分，否则归档收据会指向错误源码。
# -------------------------------------------------------------------------
# [F03] write_json()：向本次显式输出路径写完整 JSON 记录。
#       输出路径来自 --out，不得回落到默认目录或覆盖既有文件。
# -------------------------------------------------------------------------
# [F04] new_output()：拒绝已有目录以及当前或其他 Git 工作树内的构建位置。
#       这是「不污染参考输出」的结构性闸门，不能靠调用方自觉。
# -------------------------------------------------------------------------
# [F05] command_for()：解析调用方已配置的工具，不安装工具或补写供应商、许可证环境。
#       工具缺失应显式报 BLOCKED，而不是静默换一个可用工具。
# -------------------------------------------------------------------------
# [F06] checked_run()：保留完整日志并检查实际返回码；超时或错误诊断均导致失败。
#       退出码与日志诊断是两条独立判据，任一不满足即失败。
# -------------------------------------------------------------------------
# [F07] completion()：要求唯一整行完成记录及每个必需字段的正计数。
#       单独 finish 或零计数不算完成，防止空转测试被记成 PASS。
# -------------------------------------------------------------------------
# [F08] validate_inputs()：核对显式输入包中复制的字节，不要求已移动的原 sv_path 仍可访问。
#       输入包冻结后即可离线复演，避免依赖本机路径。
# -------------------------------------------------------------------------
# [F09] alias_runtime()：仅为完整运行库建立 ASCII 路径别名，保留原安装布局。
#       非 ASCII 路径会让部分编译链静默失败，故只在完整运行库上做别名。
# -------------------------------------------------------------------------
# [F10] verilator_compiler_args()：显式选择现有 clang++ 与 C++20 参数。
#       不依赖 make 的隐式编译器选择，保证跨机器口径一致。
# -------------------------------------------------------------------------
# [F11] run_bench()：为所选组件独立编译；临时对象目录不复用旧二进制。
#       每个 --tops 项只编译已登记的依赖，不扫描整个工程。
# -------------------------------------------------------------------------
# [F12] main()：按显式选择运行；缺依赖、编译、执行或完成计数错误均以非零状态退出。
#       组件数学夹具不代表器件，需完整核输入的测试必须提供 --inputs-dir。
# -------------------------------------------------------------------------
# 运行前置（摘自顶层 README 的运行节）：Python 3.12、Verilator；
# 输出目录须为尚不存在的仓库外目录；校准与静态分析使用固定提交的 ADCToolbox。
# 各 --tops 的源依赖、完成标记与必需字段由 Bench 显式登记，不自动推断。
# -------------------------------------------------------------------------
"""只运行显式选择的自检组件，不隐式复用输出、参数或通过记录。

--out 必须是不存在且位于版本库外的目录。每个 --tops 项只编译已登记的依赖，
不扫描整个工程，也不提供旧配置、参考向量或“已知失败即通过”的默认集合。
需完整核输入的测试必须提供源导出器生成的 --inputs-dir；组件数学夹具不代表器件。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

REPO = Path(__file__).resolve().parents[1]
SCOPE_COMPONENT = "COMPONENT_SYNTHETIC_DIGITAL"
ERROR_DIAGNOSTIC = re.compile(
    r"(?mi)^\s*(?:%Error|%Fatal|Error:|Fatal:|Error-\[|Fatal-\[|\*\*\s*(?:Error|Fatal)"
    r"|UVM_(?:ERROR|FATAL)[^\r\n]*@)"
    r"|\bAssertion\b[^\r\n]*\bfailed\b"
)


# =========================================================================
# 1. 函数组：Bench / digest / write_json
# =========================================================================
# -------------------------------------------------------------------------
# 声明一个自检顶层的源依赖、完成标记及计数，不据此推断整个工程的覆盖。
# 计算实际文件的 SHA256，用内容而不是文件名标识输入。
# 向本次显式输出路径写完整 JSON 记录。
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Bench:
    """声明一个自检顶层的源依赖、完成标记及计数，不据此推断整个工程的覆盖。"""

    sources: tuple[str, ...]
    marker: str
    positive_fields: tuple[str, ...]
    lint: tuple[tuple[str, tuple[str, ...]], ...] = ()
    requires_inputs: bool = False
    requires_math_format: bool = False
    requires_protocol_fixture: bool = False
    pair_vectors: bool = False
    module: str | None = None
    parameters: tuple[tuple[str, int], ...] = ()
    support_files: tuple[str, ...] = ()
    source_control_trace: bool = False
    system_trace: bool = False
    zero_fields: tuple[str, ...] = ()
    open_markers: tuple[str, ...] = ()


# 完整核测试只在接口和绑定固定后登记。
# 不登记已退役的 P1/P2/P3、D=2、兼容入口或旧反例集合。
BENCHES = {
    "divider_borrow_tb": Bench(
        ("rtl/core/div_floor.sv", "sim/tb/divider_borrow_tb.sv"),
        "DIVIDER_BORROW_COMPLETE",
        ("exhaustive_checks",),
        requires_math_format=True,
    ),
    "caaz_pair_vectors_tb": Bench(
        ("rtl/calibration/caaz_pair_processor.sv", "sim/tb/caaz_pair_vectors_tb.sv"),
        "CAAZ_PAIR_VECTORS_COMPLETE",
        ("cycles", "outputs", "checks", "cases"),
        (("caaz_pair_processor", ("-GP_CAAZ_ENABLE=1'b1",)),),
        pair_vectors=True,
    ),
}

# DSP 测试使用各自的明确合同；零计数要求与 OPEN 声明分别处理。
BENCHES["result_fifo_tb"] = Bench(
    ("ad4080_dsp/rtl/result_fifo.sv", "ad4080_dsp/sim/tb/result_fifo_tb.sv"),
    "RESULT_FIFO_CONTRACT_COMPLETE",
    ("depth", "queue_reads", "snapshot_reads", "collisions", "writes", "checks"),
    (),
)
BENCHES["cnv_ctrl_contract_tb"] = Bench(
    ("ad4080_dsp/rtl/cnv_ctrl.sv", "ad4080_dsp/sim/tb/cnv_ctrl_contract_tb.sv"),
    "CNV_CTRL_CONTRACT_COMPLETE",
    ("input_edges", "busy_errors", "pulses", "checks"),
    (),
)
BENCHES["sys_error_corr_contract_tb"] = Bench(
    ("ad4080_dsp/rtl/sys_error_corr.sv", "ad4080_dsp/sim/tb/sys_error_corr_contract_tb.sv"),
    "SYS_ERROR_CORR_CONTRACT_COMPLETE",
    ("comparisons", "unbound_rejects", "cancels", "checks"),
    (),
)
BENCHES["sinc5_decim_tb"] = Bench(
    (
        "ad4080_dsp/rtl/sinc1_decim.sv",
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/sim/tb/sinc5_decim_tb.sv",
    ),
    "SINC_KERNEL_CONTRACT_COMPLETE",
    ("cases", "sinc1_outputs", "sinc5_outputs", "steady_compares", "checks"),
    (),
)
BENCHES["spi_slave_tb"] = Bench(
    (
        "ad4080_dsp/rtl/spi_slave.sv",
        "ad4080_dsp/rtl/ad4080_regs.sv",
        "ad4080_dsp/sim/tb/spi_slave_tb.sv",
    ),
    "SPI_ENTITY_CONTRACT_COMPLETE",
    (
        "requests",
        "read_compares",
        "crc_compares",
        "status_compares",
        "partial_errors",
        "crc_errors",
        "notready_errors",
        "prepare_end_cases",
        "checks",
    ),
    (),
)
BENCHES["fifo_capture_contract_tb"] = Bench(
    (
        "ad4080_dsp/rtl/result_fifo.sv",
        "ad4080_dsp/rtl/fifo_capture.sv",
        "ad4080_dsp/sim/tb/fifo_capture_contract_tb.sv",
    ),
    "FIFO_CAPTURE_CONTRACT_COMPLETE",
    (
        "depth",
        "early_reads",
        "known_history_compares",
        "post_event_compares",
        "rearm_rejects",
        "ordered_compares",
        "checks",
    ),
    (),
)
BENCHES["ad4080_top_tb"] = Bench(
    (
        "ad4080_dsp/rtl/ad4080_top.sv",
        "ad4080_dsp/rtl/spi_slave.sv",
        "ad4080_dsp/rtl/ad4080_regs.sv",
        "ad4080_dsp/rtl/ad4080_dsp_chain.sv",
        "ad4080_dsp/rtl/sys_error_corr.sv",
        "ad4080_dsp/rtl/sinc1_decim.sv",
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/rtl/sinc5_comp.sv",
        "ad4080_dsp/rtl/event_detect.sv",
        "ad4080_dsp/rtl/fifo_capture.sv",
        "ad4080_dsp/rtl/result_fifo.sv",
        "ad4080_dsp/rtl/gpio_ctrl.sv",
        "ad4080_dsp/sim/tb/ad4080_top_tb.sv",
    ),
    "AD4080_TOP_CONTRACT_COMPLETE",
    (
        "retained_resets",
        "rejected_resets",
        "read_compares",
        "quiescent_assertions",
        "route_checks",
        "checks",
    ),
    (),
)
BENCHES["sinc5_comp_contract_tb"] = Bench(
    (
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/rtl/sinc5_comp.sv",
        "ad4080_dsp/sim/tb/sinc5_comp_contract_tb.sv",
    ),
    "SINC5_COMP_CONTRACT_COMPLETE",
    (
        "fir_compares",
        "positive",
        "negative",
        "changed_s5",
        "unbound_rejects",
        "sync_cancels",
        "checks",
    ),
    (),
)
BENCHES["sar20_dsp_integration_tb"] = Bench(
    (
        "ad4080_dsp/rtl/sar20_ad4080_top.sv",
        "ad4080_dsp/rtl/ad4080_top.sv",
        "ad4080_dsp/rtl/spi_slave.sv",
        "ad4080_dsp/rtl/ad4080_regs.sv",
        "ad4080_dsp/rtl/ad4080_dsp_chain.sv",
        "ad4080_dsp/rtl/sys_error_corr.sv",
        "ad4080_dsp/rtl/sinc1_decim.sv",
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/rtl/sinc5_comp.sv",
        "ad4080_dsp/rtl/event_detect.sv",
        "ad4080_dsp/rtl/fifo_capture.sv",
        "ad4080_dsp/rtl/result_fifo.sv",
        "ad4080_dsp/rtl/gpio_ctrl.sv",
        "ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv",
    ),
    "SAR20_DSP_INGRESS_CONTRACT_COMPLETE",
    (
        "accepted",
        "rejected",
        "comparisons",
        "canceled",
        "after_fault",
        "flag_cases",
        "sticky_cases",
        "restarts",
        "conversion_cases",
        "held_fault_restarts",
        "checks",
    ),
    (),
)
BENCHES["sar20_system_top_tb"] = Bench(
    (
        "rtl/core/adc2_dec.sv",
        "rtl/control/caaz_cycle_ctrl.sv",
        "rtl/calibration/caaz_pair_processor.sv",
        "rtl/core/analog_phase_ctrl.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/cal_sample_context.sv",
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/calib_regs.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/rdac_drv.sv",
        "rtl/core/recon_core.sv",
        "rtl/core/sar_structural_ctrl.sv",
        "rtl/core/sar_trial_ctrl.sv",
        "rtl/core/slice_pool_ctrl.sv",
        "rtl/core/status_regs.sv",
        "rtl/core/unit_therm.sv",
        "rtl/core/weight_store.sv",
        "rtl/top/sadc_enc.sv",
        "rtl/top/sar20_digital_core.sv",
        "ad4080_dsp/rtl/sar20_system_top.sv",
        "ad4080_dsp/rtl/sar20_ad4080_top.sv",
        "ad4080_dsp/rtl/ad4080_top.sv",
        "ad4080_dsp/rtl/spi_slave.sv",
        "ad4080_dsp/rtl/ad4080_regs.sv",
        "ad4080_dsp/rtl/ad4080_dsp_chain.sv",
        "ad4080_dsp/rtl/sys_error_corr.sv",
        "ad4080_dsp/rtl/sinc1_decim.sv",
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/rtl/sinc5_comp.sv",
        "ad4080_dsp/rtl/event_detect.sv",
        "ad4080_dsp/rtl/fifo_capture.sv",
        "ad4080_dsp/rtl/result_fifo.sv",
        "ad4080_dsp/rtl/gpio_ctrl.sv",
        "ad4080_dsp/sim/tb/sar20_system_top_tb.sv",
    ),
    "SAR20_SYSTEM_CONTRACT_COMPLETE",
    (
        "accepted",
        "comparisons",
        "source_handshakes",
        "busy_reset_rejects",
        "interface_rejects",
        "csr_compares",
        "checks",
    ),
    (("sar20_system_top", ()),),
    requires_inputs=True,
    system_trace=True,
    support_files=("rtl/params/rtl_error_codes.vh",),
    open_markers=("SAR20_SYSTEM_IDLE_RESET_PRESERVATION_OPEN",),
    zero_fields=("pending",),
)
BENCHES["ad4080_regs_rejection_tb"] = Bench(
    ("ad4080_dsp/rtl/ad4080_regs.sv", "ad4080_dsp/sim/tb/ad4080_regs_rejection_tb.sv"),
    "AD4080_REG_REJECTION_COMPLETE",
    ("rejected_writes", "metadata_rejects", "preserved_compares", "checks"),
    (),
)
BENCHES["spi_unbound_contract_tb"] = Bench(
    ("ad4080_dsp/rtl/spi_slave.sv", "ad4080_dsp/sim/tb/spi_unbound_contract_tb.sv"),
    "SPI_UNBOUND_CONTRACT_COMPLETE",
    ("frames", "rejects", "missing_bindings", "not_ready", "invalid_address", "readonly", "checks"),
    (),
    zero_fields=("write_requests",),
)

CORE_SOURCES = (
    "rtl/core/adc2_dec.sv",
    "rtl/control/caaz_cycle_ctrl.sv",
    "rtl/calibration/caaz_pair_processor.sv",
    "rtl/core/analog_phase_ctrl.sv",
    "rtl/core/cal_output_stage.sv",
    "rtl/core/cal_residue_mac.sv",
    "rtl/core/cal_sample_context.sv",
    "rtl/core/cal_weight_reduce.sv",
    "rtl/core/calib_regs.sv",
    "rtl/core/div_floor.sv",
    "rtl/core/rdac_drv.sv",
    "rtl/core/recon_core.sv",
    "rtl/core/sar_structural_ctrl.sv",
    "rtl/core/sar_trial_ctrl.sv",
    "rtl/core/slice_pool_ctrl.sv",
    "rtl/core/status_regs.sv",
    "rtl/core/unit_therm.sv",
    "rtl/core/weight_store.sv",
    "rtl/top/sadc_enc.sv",
    "rtl/top/sar20_digital_core.sv",
)
for name, caaz in (("source_control_contract_tb", 0), ("source_control_caaz_tb", 1)):
    BENCHES[name] = Bench(
        (*CORE_SOURCES, "sim/tb/source_control_contract_tb.sv"),
        "SOURCE_CONTROL_COMPONENT_COMPLETE",
        ("observations", "outputs", "checks", "epochs"),
        (("sar20_digital_core", ("-GP_CAL_OBSERVE=1'b1", f"-GP_CAAZ_ENABLE=1'b{caaz}")),),
        requires_inputs=True,
        module="source_control_contract_tb",
        parameters=(("TEST_CAAZ_ENABLE", caaz),),
        support_files=("rtl/params/rtl_error_codes.vh",),
        source_control_trace=True,
    )


# Generic mathematical/protocol cases consume an explicit numerical fixture.
BENCHES["review_recon_protocol_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "sim/tb/review_recon_protocol_tb.sv",
    ),
    "REVIEW_RECON_PROTOCOL_COMPLETE",
    ("outputs", "requests"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["weight_row_cache_tb"] = Bench(
    ("rtl/core/weight_store.sv", "sim/tb/weight_row_cache_tb.sv"),
    "WEIGHT_ROW_CACHE_COMPLETE",
    ("geometries", "steps"),
    requires_protocol_fixture=True,
    parameters=(("STRESS_MAXIMUM", 0),),
)
BENCHES["cal_weight_reduce_ppa_tb"] = Bench(
    ("rtl/core/cal_weight_reduce.sv", "sim/tb/cal_weight_reduce_ppa_tb.sv"),
    "CAL_WEIGHT_REDUCE_PPA_COMPLETE",
    ("geometries", "checks"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["recon_ppa_latency_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "sim/tb/recon_ppa_latency_tb.sv",
    ),
    "RECON_PPA_LATENCY_COMPLETE",
    ("profiles", "samples_per_profile", "outputs"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["tree_mapping_tb"] = Bench(
    ("rtl/core/cal_weight_reduce.sv", "sim/tb/tree_mapping_dut.sv", "sim/tb/tree_mapping_tb.sv"),
    "TREE_MAPPING_COMPLETE",
    ("checks", "valid_cases", "invalid_cases"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["calibration_physical_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "sim/tb/calibration_physical_tb.sv",
    ),
    "CALIBRATION_PHYSICAL_COMPLETE",
    ("samples", "coefficient_rows", "invalid_cases"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["calibration_recovery_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "sim/tb/calibration_recovery_tb.sv",
    ),
    "CALIBRATION_RECOVERY_COMPLETE",
    ("samples",),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["generic_identity_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "sim/tb/generic_identity_tb.sv",
    ),
    "GENERIC_IDENTITY_COMPLETE",
    ("cases", "checks", "full_12bit", "boundary_20bit"),
    requires_protocol_fixture=True,
    parameters=(),
)

SPECIAL_TOPS = ("arithmetic_audit_tb", "engineering_fsm_tb")
BENCHES["sinc_rate_oracle_tb"] = Bench(
    (
        "ad4080_dsp/rtl/sinc1_decim.sv",
        "ad4080_dsp/rtl/sinc5_decim.sv",
        "ad4080_dsp/sim/tb/sinc_rate_oracle_tb.sv",
    ),
    "SINC_RATE_ORACLE_COMPLETE",
    ("cases", "outputs1", "outputs5", "positive", "negative", "checks"),
)
BENCHES["fifo_capture_16k"] = Bench(
    BENCHES["fifo_capture_contract_tb"].sources,
    "FIFO_CAPTURE_CONTRACT_COMPLETE",
    BENCHES["fifo_capture_contract_tb"].positive_fields,
    BENCHES["fifo_capture_contract_tb"].lint,
    module="fifo_capture_contract_tb",
    parameters=(("DEPTH", 16384),),
)


# ------------------------------------------------------------------------
def digest(path: Path) -> str:
    """计算实际文件的 SHA256，用内容而不是文件名标识输入。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------------
def write_json(path: Path, value) -> None:
    """向本次显式输出路径写完整 JSON 记录。"""
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


# =========================================================================
# 2. 函数组：new_output / command_for / checked_run
# =========================================================================
# -------------------------------------------------------------------------
# 拒绝已有目录以及当前或其他 Git 工作树内的构建位置。
# 解析调用方已配置的工具，不安装工具或补写供应商、许可证环境。
# 保留完整日志并检查实际返回码；超时或错误诊断均导致失败。
# ------------------------------------------------------------------------
def new_output(path: Path) -> Path:
    """拒绝已有目录以及当前或其他 Git 工作树内的构建位置。

    Args:
        path: 输出目录，允许展开用户目录但不得指向已有目录或符号链接。
    Returns:
        已创建的绝对目录路径。
    Raises:
        ValueError: 路径已有内容、属于工作树，或以符号链接别名规避隔离。

    检查沿父目录查找 .git，覆盖普通 checkout 及 worktree 的 .git 文件形式。
    只有位置隔离通过才允许后续编译，防止验证覆盖参考数据或已有收据。
    """
    raw = path.expanduser()
    if raw.is_symlink():
        raise ValueError("output must not be a symbolic-link alias")
    out = raw.resolve()
    if out.exists():
        raise ValueError(f"output already exists: {out}")
    if out == REPO or out.is_relative_to(REPO):
        raise ValueError("output must be outside the checkout")
    if any((parent / ".git").exists() for parent in (out, *out.parents)):
        raise ValueError("output must be outside version-controlled working trees")
    out.mkdir(parents=True, exist_ok=False)
    return out


# ------------------------------------------------------------------------
def command_for(simulator: str) -> list[str]:
    """解析调用方已配置的工具，不安装工具或补写供应商、许可证环境。"""
    variable = {"verilator": "VERILATOR", "vcs": "VCS", "iverilog": "IVERILOG"}[simulator]
    command = shlex.split(os.environ.get(variable, simulator))
    if not command:
        raise ValueError(f"{variable} is empty")
    executable = shutil.which(command[0])
    if executable is None:
        raise ValueError(f"simulator not found; configure {variable}")
    command[0] = str(Path(executable).absolute())
    return command


# ------------------------------------------------------------------------
def checked_run(command: list[str], *, cwd: Path, env: dict, log: Path, timeout: int) -> str:
    """保留完整日志并检查实际返回码；超时或错误诊断均导致失败。

    Args:
        command: 独立参数数组，不经 shell 展开。
        cwd: 明确的执行目录。
        env: 调用方已配置的进程环境。
        log: 本次日志文件；标准输出与错误合并保存。
        timeout: 等待上限，单位 s。
    Returns:
        完整工具文本输出。
    Raises:
        RuntimeError: 无法执行、超时、返回非零或日志含失败诊断。

    返回码为零不覆盖日志内断言失败。命令收据保存真实返回码和日志摘要，
    不能因工具产生了一个文件就宣布该阶段通过。
    """
    raw_returncode = None
    execution_error = None
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            raw_returncode = result.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            stream.write(f"\nRUNNER_EXECUTION_ERROR: {error}\n")
            execution_error = str(error)
    output = log.read_text(encoding="utf-8", errors="backslashreplace")
    write_json(
        log.with_suffix(log.suffix + ".command.json"),
        {
            "argv": command,
            "raw_returncode": raw_returncode,
            "execution_error": execution_error,
            "log_sha256": digest(log),
        },
    )
    if execution_error is not None:
        raise RuntimeError(f"execution failed; see {log}")
    if raw_returncode:
        raise RuntimeError(f"command returned {raw_returncode}; see {log}")
    if ERROR_DIAGNOSTIC.search(output):
        raise RuntimeError(f"tool emitted an error despite rc=0; see {log}")
    return output


# =========================================================================
# 3. 函数组：completion / validate_inputs / alias_runtime
# =========================================================================
# -------------------------------------------------------------------------
# 要求唯一整行完成记录及每个必需字段的正计数，单独 finish 或零计数不算完成。
# 核对显式输入包中复制的字节，不要求已移动的原 sv_path 仍可访问。
# 仅为完整运行库建立 ASCII 路径别名，保留原安装布局。
# ------------------------------------------------------------------------
def completion(output: str, marker: str, fields: tuple[str, ...]) -> dict[str, int]:
    """要求唯一整行完成记录及每个必需字段的正计数，单独 finish 或零计数不算完成。

    完成计数只证明列明的覆盖事件发生，不等于所有未列场景都被验证。
    缺字段、重复标记或零计数不能借用上次运行结果填补。
    """
    rows = re.findall(rf"(?m)^{re.escape(marker)}(?:\s+[^\r\n]*)?$", output)
    if len(rows) != 1:
        raise ValueError(f"missing or duplicate completion line: {marker}")
    counts = {}
    for name in fields:
        values = re.findall(rf"(?:^|\s){re.escape(name)}=([0-9]+)(?=\s|$)", rows[0])
        if len(values) != 1 or int(values[0]) <= 0:
            raise ValueError(f"{marker}: required nonzero count absent: {name}")
        counts[name] = int(values[0])
    return counts


# ------------------------------------------------------------------------
def validate_inputs(directory: Path) -> dict:
    """核对显式输入包中复制的字节，不要求已移动的原 sv_path 仍可访问。"""
    root = directory.expanduser().resolve()
    wanted = {
        "rtl_params.vh",
        "source_control_binding.sv",
        "source_acquisition_binding.sv",
        "packet_schema.json",
        "config.json",
        "format.json",
        "binding.json",
        "rtl_input_manifest.json",
    }
    if (
        not root.is_dir()
        or {p.name for p in root.iterdir()} != wanted
        or not all(p.is_file() for p in root.iterdir())
    ):
        raise ValueError("inputs-dir requires exactly the eight exported bundle files")
    manifest = json.loads((root / "rtl_input_manifest.json").read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("status") != "EXPLICIT_RTL_INPUTS_GENERATED"
    ):
        raise ValueError("inputs-dir is not a recognized explicit-source bundle")
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict) or set(hashes) != wanted - {"rtl_input_manifest.json"}:
        raise ValueError("source bundle artifact hash inventory is incomplete")
    for name, expected in hashes.items():
        if not isinstance(expected, str) or digest(root / name) != expected:
            raise ValueError(f"source bundle bytes changed: {name}")
    inputs = manifest.get("input_sha256", {})
    expected_inputs = {
        "config_json": digest(root / "config.json"),
        "format_json": digest(root / "format.json"),
        "binding_json": digest(root / "binding.json"),
        "source_sv": digest(root / "source_control_binding.sv"),
        "acquisition_sv": digest(root / "source_acquisition_binding.sv"),
    }
    if inputs != expected_inputs:
        raise ValueError("source bundle input hashes do not bind the copied bytes")
    binding = json.loads((root / "binding.json").read_text())
    if not isinstance(binding, dict):
        raise ValueError("source binding must be a JSON object")
    control = binding.get("source_control", {})
    if (
        not isinstance(control, dict)
        or control.get("kind") != "sv_module"
        or control.get("sv_module_name") != "source_control_binding"
        or control.get("sha256") != expected_inputs["source_sv"]
    ):
        raise ValueError("source-control binding does not identify the copied SV module")
    acquisition = binding.get("acquisition_control", {})
    if (
        not isinstance(acquisition, dict)
        or acquisition.get("kind") != "sv_module"
        or acquisition.get("sv_module_name") != "source_acquisition_binding"
        or acquisition.get("sha256") != expected_inputs["acquisition_sv"]
    ):
        raise ValueError("acquisition binding does not identify the copied SV module")
    packet = json.loads((root / "packet_schema.json").read_text())
    if (
        not isinstance(packet, dict)
        or packet.get("schema_version") != 2
        or packet.get("source_binding_sha256") != inputs["binding_json"]
    ):
        raise ValueError("packet schema does not match the exported binding")
    return {"directory": str(root), "input_sha256": inputs, "artifact_sha256": hashes}


# ------------------------------------------------------------------------
def alias_runtime(work: Path, env: dict[str, str], version: str) -> None:
    """仅为完整运行库建立 ASCII 路径别名，保留原安装布局。

    只有路径含非 ASCII 字符或空白，且能定位配套可执行文件时才建立别名。
    调用后原地更新传入环境的 VERILATOR_ROOT，不修改系统分离的 bin/share 布局。
    """
    runtime = env.get("VERILATOR_ROOT")
    if not runtime:
        candidates = re.findall(
            r"^[ \t]*VERILATOR_ROOT[ \t]*=[ \t]*([^\r\n]*)", version, re.MULTILINE
        )
        runtime = next((s.strip() for s in reversed(candidates) if s.strip()), None)
    if not runtime or (runtime.isascii() and not any(c.isspace() for c in runtime)):
        return
    root = Path(runtime)
    binary = env.get("VERILATOR_BIN", "verilator_bin")
    if not any(os.access(root / rel / binary, os.X_OK) for rel in ("bin", "")):
        return
    alias = work / "runtime"
    if alias.exists() or alias.is_symlink():
        if alias.resolve() != root.resolve():
            raise ValueError("runtime alias already identifies another tool")
    else:
        alias.symlink_to(root.resolve(), target_is_directory=True)
    env["VERILATOR_ROOT"] = str(alias)


# =========================================================================
# 4. 函数组：verilator_compiler_args / run_bench / main
# =========================================================================
# -------------------------------------------------------------------------
# 显式选择现有 clang++ 与 C++20 参数，不依赖 make 的隐式编译器选择。
# 为所选组件独立编译；临时对象目录不复用旧二进制文件。
# 按显式选择运行；缺依赖、编译、执行或完成计数错误均以非零状态退出。
# ------------------------------------------------------------------------
def verilator_compiler_args() -> list[str]:
    """显式选择现有 clang++ 与 C++20 参数，不依赖 make 的隐式编译器选择。"""
    compiler = shutil.which(os.environ.get("CXX", "clang++"))
    if compiler is None or "clang" not in Path(compiler).name:
        raise ValueError("this portable timing runner requires an existing clang++ compiler")
    return [
        "--compiler",
        "clang",
        "-CFLAGS",
        "-std=c++20",
        "-MAKEFLAGS",
        f"PYTHON3=/usr/bin/python3 CFG_CXXFLAGS_PCH_I=-include CXX={compiler}",
    ]


# ------------------------------------------------------------------------
def run_bench(args, top: str, spec: Bench, out: Path, command: list[str], version: str) -> dict:
    """为所选组件独立编译；临时对象目录不复用旧二进制文件。"""
    folder = out / top
    folder.mkdir()
    module = spec.module or top
    if spec.parameters and args.simulator != "verilator":
        raise ValueError(
            "this explicitly parameterized numerical case currently requires Verilator"
        )
    with tempfile.TemporaryDirectory(prefix="sar_component_") as temporary:
        work = Path(temporary)
        snapshot = folder / "source_snapshot"
        snapshot.mkdir()
        source_hashes = {}
        for name in (*spec.sources, *spec.support_files):
            source = REPO / name
            if not source.is_file():
                raise ValueError(f"selected dependency is absent: {name}")
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
            source_hashes[name] = digest(target)
        (work / "repo").symlink_to(snapshot, target_is_directory=True)
        env = os.environ.copy()
        if args.simulator == "verilator":
            alias_runtime(work, env, version)
        include_dirs = []
        source_names = list(spec.sources)
        if spec.requires_inputs:
            if args.inputs_dir is None:
                raise ValueError(f"{top} requires --inputs-dir")
            validated = validate_inputs(args.inputs_dir)
            binding_snapshot = folder / "inputs_snapshot"
            shutil.copytree(validated["directory"], binding_snapshot)
            validate_inputs(binding_snapshot)
            (work / "inputs").symlink_to(binding_snapshot, target_is_directory=True)
            include_dirs.append(work / "inputs")
            include_dirs.append(work / "repo/rtl/params")
            source_names += ["source_control_binding.sv", "source_acquisition_binding.sv"]
        elif spec.requires_math_format:
            if args.math_format is None:
                raise ValueError(f"{top} requires --math-format")
            from audit_rtl_arithmetic import load_format, write_math_header

            fixture = work / "math_only"
            fixture.mkdir()
            write_math_header(fixture / "rtl_params.vh", load_format(args.math_format))
            shutil.copyfile(args.math_format, folder / "math_format.json")
            shutil.copyfile(fixture / "rtl_params.vh", folder / "math_params.vh")
            include_dirs.append(fixture)
        elif spec.requires_protocol_fixture:
            if args.protocol_fixture is None:
                raise ValueError(f"{top} requires --protocol-fixture")
            from run_engineering_fsm import load_fixture, write_fixture_header

            fixture = work / "protocol_numeric"
            fixture.mkdir()
            write_fixture_header(fixture / "rtl_params.vh", load_fixture(args.protocol_fixture))
            shutil.copyfile(args.protocol_fixture, folder / "protocol_fixture.json")
            shutil.copyfile(fixture / "rtl_params.vh", folder / "protocol_params.vh")
            include_dirs.append(fixture)
        sources = []
        for name in source_names:
            path = (
                work / "inputs" / name
                if name in ("source_control_binding.sv", "source_acquisition_binding.sv")
                else work / "repo" / name
            )
            if not path.is_file():
                raise ValueError(f"selected dependency is absent: {name}")
            sources.append(str(path))
        plusargs = []
        payload_hashes = {}
        parameters = dict(spec.parameters)
        if spec.system_trace:
            if (
                args.simulator != "verilator"
                or args.weights is None
                or args.scalars is None
                or args.controls is None
                or args.coarse_target is None
                or args.fine_target is None
            ):
                raise ValueError(
                    "system component requires explicit weights/scalars/controls and coarse/fine targets"
                )
            for field in ("weights", "scalars", "controls"):
                path = getattr(args, field)
                if not path.is_file():
                    raise ValueError(f"missing explicit system payload: {field}")
                target = folder / f"{field}.hex"
                shutil.copyfile(path, target)
                payload_hashes[field] = digest(target)
                plusargs.append(f"+{field.upper()}={target}")
            plusargs += [f"+COARSE_TARGET={args.coarse_target}", f"+FINE_TARGET={args.fine_target}"]
        if spec.source_control_trace:
            if (
                args.simulator != "verilator"
                or args.weights is None
                or args.scalars is None
                or not 24 <= args.frames <= 1000
            ):
                raise ValueError(
                    "source-control component requires Verilator, explicit weights/scalars and 24..1000 frames"
                )
            binding = json.loads((binding_snapshot / "binding.json").read_text())
            parameters["P_TICK_NS"] = binding["timing"]["tick_period_ns"]
            for field in ("weights", "scalars"):
                path = getattr(args, field)
                if not path.is_file():
                    raise ValueError(f"missing explicit payload: {field}")
                target = folder / f"{field}.hex"
                shutil.copyfile(path, target)
                payload_hashes[field] = digest(target)
                plusargs.append(f"+{field}={target}")
            plusargs += [f"+trace_dir={folder}", f"+frames={args.frames}"]
        if spec.pair_vectors:
            if args.vectors is None or any(
                getattr(args, field) is None or getattr(args, field) <= 0
                for field in ("expected_cycles", "expected_cases", "expected_valid_outputs")
            ):
                raise ValueError(
                    "pair vectors and explicit nonzero cycle/case/output counts are required"
                )
            vectors = args.vectors.expanduser().resolve()
            if not vectors.is_file():
                raise ValueError("pair vector file is absent")
            saved_vectors = folder / "vectors.txt"
            shutil.copyfile(vectors, saved_vectors)
            (work / "vectors.txt").symlink_to(saved_vectors)
            plusargs = [
                f"+vectors={work / 'vectors.txt'}",
                f"+trace_csv={folder / 'actual.csv'}",
                f"+expected_cycles={args.expected_cycles}",
                f"+expected_cases={args.expected_cases}",
                f"+expected_valid_outputs={args.expected_valid_outputs}",
            ]
        build = work / "obj"
        if args.simulator == "verilator":
            strict = [
                "-Werror-WIDTH",
                "-Werror-LATCH",
                "-Werror-MULTIDRIVEN",
                "-Werror-UNOPTFLAT",
                "-Werror-CASEINCOMPLETE",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
            ]
            production_sources = [
                p
                for p, name in zip(sources, source_names, strict=False)
                if name != spec.sources[-1]
            ]
            for lint_top, lint_parameters in spec.lint:
                checked_run(
                    [
                        *command,
                        "--lint-only",
                        "--top-module",
                        lint_top,
                        *lint_parameters,
                        *strict,
                        *[f"-I{p}" for p in include_dirs],
                        *production_sources,
                    ],
                    cwd=work,
                    env=env,
                    log=folder / f"{lint_top}.lint.log",
                    timeout=args.timeout,
                )
            compile_command = [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "--unroll-count",
                "8192",
                "-j",
                "1",
                "-Wno-fatal",
                "-Werror-PINMISSING",
                "-Werror-SELRANGE",
                *verilator_compiler_args(),
                "--top-module",
                module,
                *[f"-G{name}={value}" for name, value in parameters.items()],
                "--Mdir",
                str(build),
                *[f"-I{p}" for p in include_dirs],
                *sources,
            ]
            binary = build / ("V" + module)
            checked_run(
                compile_command, cwd=work, env=env, log=folder / "compile.log", timeout=args.timeout
            )
            run_command = [str(binary), *plusargs]
        elif args.simulator == "vcs":
            binary = work / "simv"
            checked_run(
                [
                    *command,
                    "-sverilog",
                    "-full64",
                    "-timescale=1ns/1ps",
                    "-top",
                    module,
                    "-o",
                    str(binary),
                    *[f"+incdir+{p}" for p in include_dirs],
                    *sources,
                ],
                cwd=work,
                env=env,
                log=folder / "compile.log",
                timeout=args.timeout,
            )
            run_command = [str(binary), "-exitstatus", *plusargs]
        else:
            binary = work / "simulation.vvp"
            checked_run(
                [
                    *command,
                    "-g2012",
                    "-s",
                    module,
                    "-o",
                    str(binary),
                    *[f"-I{p}" for p in include_dirs],
                    *sources,
                ],
                cwd=work,
                env=env,
                log=folder / "compile.log",
                timeout=args.timeout,
            )
            vvp = shutil.which(os.environ.get("VVP", "vvp"))
            if vvp is None:
                raise ValueError("vvp is required for an Icarus execution")
            run_command = [vvp, str(binary), *plusargs]
        if not binary.is_file() or binary.stat().st_size == 0:
            raise RuntimeError("compiler returned zero without producing a new simulation binary")
        output = checked_run(
            run_command, cwd=folder, env=env, log=folder / "run.log", timeout=args.timeout
        )
        counts = completion(output, spec.marker, spec.positive_fields)
        if spec.system_trace:
            counts.update(
                completion(
                    output,
                    "SAR20_SYSTEM_ACQUISITION_COMPLETE",
                    ("qualified_shift_cycles", "nonzero_rdac_cycles"),
                )
            )
            counts.update(
                completion(
                    output,
                    "SAR20_SYSTEM_FAULT_COMPLETE",
                    ("fault_cases", "good_raw_rejected", "dsp_restart_blocks", "epoch_recoveries"),
                )
            )
        # 安全字段的零计数单独解析，因为 completion() 只接受
        # 严格为正的工作计数；缺失字段不能视为零。
        completion_rows = re.findall(rf"(?m)^{re.escape(spec.marker)}(?:\s+[^\r\n]*)?$", output)
        for field in spec.zero_fields:
            values = re.findall(rf"(?:^|\s){re.escape(field)}=([0-9]+)(?=\s|$)", completion_rows[0])
            if len(values) != 1 or int(values[0]) != 0:
                raise ValueError("component zero-valued safety expectation failed")
            counts[field] = 0
        if any(
            len(re.findall(r"(?m)^" + re.escape(marker) + r"(?:\s|$)", output)) != 1
            for marker in spec.open_markers
        ):
            raise ValueError("component required OPEN-boundary statement missing or duplicated")
        if (
            "DEPTH" in dict(spec.parameters)
            and counts.get("depth") != dict(spec.parameters)["DEPTH"]
        ):
            raise ValueError("actual RAM-depth completion differs from the selected numerical case")
        if spec.pair_vectors and (
            counts["cycles"] != args.expected_cycles
            or counts["cases"] != args.expected_cases
            or counts["outputs"] != args.expected_valid_outputs
        ):
            raise ValueError("pair component completion counts differ from explicit expectations")
        if spec.pair_vectors:
            with (folder / "actual.csv").open(newline="") as stream:
                rows = list(csv.reader(stream))
            if len(rows) != args.expected_cycles + 1 or any(len(row) != 25 for row in rows):
                raise ValueError(
                    "pair component actual CSV is incomplete or does not have 25 columns"
                )
            if digest(vectors) != digest(saved_vectors):
                raise RuntimeError("pair vectors changed during execution; snapshot retained")
        if any(digest(REPO / name) != expected for name, expected in source_hashes.items()):
            raise RuntimeError(
                "selected source changed during execution; snapshot retained for review"
            )
        if any(
            digest(getattr(args, field)) != expected for field, expected in payload_hashes.items()
        ):
            raise RuntimeError("explicit coefficient payload changed during execution")
        if spec.requires_inputs:
            validate_inputs(args.inputs_dir)
        record = {
            "status": "PASS",
            "scope": SCOPE_COMPONENT
            if not spec.requires_inputs
            else "EXPLICIT_SOURCE_BOUND_DIGITAL",
            "top": top,
            "module": module,
            "parameters": parameters,
            "payload_sha256": payload_hashes,
            "counts": counts,
            "sources_sha256": source_hashes,
            "marker": spec.marker,
            "open_boundaries": list(spec.open_markers),
            "input_bundle": validated if spec.requires_inputs else None,
            "run_log_sha256": digest(folder / "run.log"),
        }
        write_json(folder / "result.json", record)
        return record


# ------------------------------------------------------------------------
def main(argv=None) -> int:
    """按显式选择运行；缺依赖、编译、执行或完成计数错误均以非零状态退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tops", nargs="+", choices=(*BENCHES, *SPECIAL_TOPS), required=True)
    parser.add_argument(
        "--simulator", choices=("verilator", "vcs", "iverilog"), default="verilator"
    )
    parser.add_argument("--inputs-dir", type=Path)
    parser.add_argument("--math-format", type=Path)
    parser.add_argument("--protocol-fixture", type=Path)
    parser.add_argument("--vectors", type=Path)
    parser.add_argument("--weights", type=Path)
    parser.add_argument("--scalars", type=Path)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--coarse-target", type=int)
    parser.add_argument("--fine-target", type=int)
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--expected-cycles", type=int)
    parser.add_argument("--expected-cases", type=int)
    parser.add_argument("--expected-valid-outputs", type=int)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    for field in (
        "inputs_dir",
        "math_format",
        "protocol_fixture",
        "vectors",
        "weights",
        "scalars",
        "controls",
    ):
        path = getattr(args, field)
        if path is not None:
            setattr(args, field, path.expanduser().resolve())
    if len(args.tops) != len(set(args.tops)) or args.timeout <= 0:
        parser.error("tops must be unique and timeout must be positive")
    if args.inputs_dir is not None and not any(
        top in BENCHES and BENCHES[top].requires_inputs for top in args.tops
    ):
        parser.error("selected components do not consume a whole-core source bundle")
    out = None
    manifest = {"schema_version": 1, "status": "RUNNING", "selected_tops": args.tops, "results": []}
    try:
        out = new_output(args.out)
        command = command_for(args.simulator)
        version_args = (
            ["-V"]
            if args.simulator == "verilator"
            else ["-ID"]
            if args.simulator == "vcs"
            else ["-V"]
        )
        version = checked_run(
            command + version_args,
            cwd=out,
            env=os.environ.copy(),
            log=out / "tool-version.log",
            timeout=min(args.timeout, 30),
        )
        manifest["tool_command"] = command
        manifest["tool_version"] = version
        write_json(out / "manifest.json", manifest)
        for top in args.tops:
            if top == "arithmetic_audit_tb":
                if args.math_format is None or args.simulator != "verilator":
                    raise ValueError("independent arithmetic requires --math-format and Verilator")
                child = out / top
                checked_run(
                    [
                        sys.executable,
                        str(REPO / "tools/audit_rtl_arithmetic.py"),
                        "--out",
                        str(child),
                        "--format-json",
                        str(args.math_format),
                        "--timeout",
                        str(args.timeout),
                    ],
                    cwd=out,
                    env=os.environ.copy(),
                    log=out / f"{top}.driver.log",
                    timeout=args.timeout * 3,
                )
                result = json.loads((child / "manifest.json").read_text())
                if result.get("status") != "PASS" or not all(
                    isinstance(result.get("counts", {}).get(k), int) and result["counts"][k] > 0
                    for k in ("mac", "adc", "div", "flag_sequences")
                ):
                    raise ValueError("arithmetic child did not establish nonzero completed checks")
                result["top"] = top
                manifest["results"].append(result)
            elif top == "engineering_fsm_tb":
                if args.protocol_fixture is None or args.simulator != "verilator":
                    raise ValueError(
                        "engineering protocol requires --protocol-fixture and Verilator"
                    )
                child = out / top
                checked_run(
                    [
                        sys.executable,
                        str(REPO / "tools/run_engineering_fsm.py"),
                        "--out",
                        str(child),
                        "--fixture-json",
                        str(args.protocol_fixture),
                        "--timeout",
                        str(args.timeout),
                    ],
                    cwd=out,
                    env=os.environ.copy(),
                    log=out / f"{top}.driver.log",
                    timeout=args.timeout * 2,
                )
                result = json.loads((child / "result.json").read_text())
                if (
                    result.get("status") != "PASS"
                    or result.get("csv_rows_each") != 175
                    or len(result.get("recon_results", [])) != 7
                ):
                    raise ValueError(
                        "engineering child did not establish its independent CSV checks"
                    )
                result["top"] = top
                manifest["results"].append(result)
            else:
                manifest["results"].append(
                    run_bench(args, top, BENCHES[top], out, command, version)
                )
        manifest["status"] = "PASS"
        write_json(out / "manifest.json", manifest)
        print(
            f"RTL_COMPONENTS_COMPLETE tops={len(manifest['results'])} scope=SELECTED_DIGITAL_COMPONENTS"
        )
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        if out is not None:
            manifest["status"] = "FAIL"
            manifest["error"] = str(error)
            write_json(out / "manifest.json", manifest)
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
