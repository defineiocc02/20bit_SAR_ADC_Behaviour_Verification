#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_open_rtl.py
# 模块名     : run_open_rtl
# 版本       : v1.0
# 功能       : 以显式依赖和输入快照启动选定数字组件并保存分层执行证据。
# 设计说明   : 只选择声明的数字组件和所属夹具，每次单独保存源及仿真日志。
#              编译成功只是前置条件，必须再验证实际仿真的完成行及计数。
#              未接通的接口作为 OPEN 边界保留，组件通过不等同器件签核。
#              源码、向量和运行日志均有独立身份；失败仍保留相应日志。
# 变更记录   : 移除独立 ADC 整链目标。保留 DSP、协议和算术组件。
# =============================================================================
"""只运行显式选择的自检组件，不隐式复用输出、参数或通过记录。

--out 必须是不存在且位于版本库外的目录。每个 --tops 项只编译已登记的依赖，
不扫描整个工程，也不提供旧配置、参考向量或“已知失败即通过”的默认集合。
需要数值夹具或配对向量的组件须明确提供输入。整链验收统一使用 v21 的验证入口。
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

# =============================================================================
# 1. 结果范围与错误诊断
# =============================================================================
# REPO 只用于从约定的版本库中取源，不用当前启动目录推定被测版本。
# 错误识别同时要求实际返回码和工具日志。部分工具会在报错后仍返回零。
# 正则锚定真实诊断及断言失败，防止把带有 error 字样的计数名称当成错误。
# 组件范围只包含所选的数字模块，不能从组件结果推断 ADC 整链或模拟宏表现。
REPO = Path(__file__).resolve().parents[1]
SCOPE_COMPONENT = "COMPONENT_SYNTHETIC_DIGITAL"
ERROR_DIAGNOSTIC = re.compile(
    r"(?mi)^\s*(?:%Error|%Fatal|Error:|Fatal:|Error-\[|Fatal-\[|\*\*\s*(?:Error|Fatal)"
    r"|UVM_(?:ERROR|FATAL)[^\r\n]*@)"
    r"|\bAssertion\b[^\r\n]*\bfailed\b"
)


# 候选对象是不可变的，避免同一进程运行多个顶层时携带上一项的计数规则。
# sources 指实际编译顺序，末尾是测试台；support_files 只按原路径保存供 include。
# lint 描述设计顶层及配置参数，不能和测试台 elaboration 参数混为一谈。
# requires_math_format、requires_protocol_fixture 各自约束组件专用几何，不读取 ADC 主线参数。
# marker、positive_fields 验证实际发生了事件，zero_fields 验证没有发生禁止事件。
# open_markers 保留测试不承诺的接口，并要求记录确实出现在日志中。
# module 指登记别名共享的实际模块；目录和报告仍以所选名字区分数值场景。
@dataclass(frozen=True)
class Bench:
    """声明一个自检顶层的源依赖、完成标记及计数，不据此推断整个工程的覆盖。"""

    sources: tuple[str, ...]
    marker: str
    positive_fields: tuple[str, ...]
    lint: tuple[tuple[str, tuple[str, ...]], ...] = ()
    requires_math_format: bool = False
    requires_protocol_fixture: bool = False
    pair_vectors: bool = False
    module: str | None = None
    parameters: tuple[tuple[str, int], ...] = ()
    support_files: tuple[str, ...] = ()
    zero_fields: tuple[str, ...] = ()
    open_markers: tuple[str, ...] = ()


# =============================================================================
# 2. 独立组件依赖登记
# =============================================================================
# 只把一个测试台列入一次运行的依赖。互相同名的模块在不同源码快照中隔离。
# 整数除法使用纯数学格式，不附加芯片的示例码表或前端增益。
# 配对向量测试独立验证扩展模块；其结果不写成 v21 已接入该扩展。
# 组件之间的计数是各自成果的必要条件，不能统一写成只检查一个 PASS 字样。
BENCHES = {
    "divider_borrow_tb": Bench(
        ("rtl/core/div_floor.sv", "flow/sim/tb/divider_borrow_tb.sv"),
        "DIVIDER_BORROW_COMPLETE",
        ("exhaustive_checks",),
        requires_math_format=True,
    ),
    "caaz_pair_vectors_tb": Bench(
        ("subsys/extensions/caaz/rtl/caaz_pair_processor.sv", "subsys/extensions/caaz/tb/caaz_pair_vectors_tb.sv"),
        "CAAZ_PAIR_VECTORS_COMPLETE",
        ("cycles", "outputs", "checks", "cases"),
        (("caaz_pair_processor", ("-GP_CAAZ_ENABLE=1'b1",)),),
        pair_vectors=True,
    ),
}

# DSP 测试使用各自的明确合同；零计数要求与 OPEN 声明分别处理。
BENCHES["result_fifo_tb"] = Bench(
    ("subsys/ad4080_dsp/rtl/result_fifo.sv", "subsys/ad4080_dsp/sim/tb/result_fifo_tb.sv"),
    "RESULT_FIFO_CONTRACT_COMPLETE",
    ("depth", "queue_reads", "snapshot_reads", "collisions", "writes", "checks"),
    (),
)
BENCHES["cnv_ctrl_contract_tb"] = Bench(
    ("subsys/ad4080_dsp/rtl/cnv_ctrl.sv", "subsys/ad4080_dsp/sim/tb/cnv_ctrl_contract_tb.sv"),
    "CNV_CTRL_CONTRACT_COMPLETE",
    ("input_edges", "busy_errors", "pulses", "checks"),
    (),
)
BENCHES["sys_error_corr_contract_tb"] = Bench(
    ("subsys/ad4080_dsp/rtl/sys_error_corr.sv", "subsys/ad4080_dsp/sim/tb/sys_error_corr_contract_tb.sv"),
    "SYS_ERROR_CORR_CONTRACT_COMPLETE",
    ("comparisons", "unbound_rejects", "cancels", "checks"),
    (),
)
BENCHES["sinc5_decim_tb"] = Bench(
    (
        "subsys/ad4080_dsp/rtl/sinc1_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_decim.sv",
        "subsys/ad4080_dsp/sim/tb/sinc5_decim_tb.sv",
    ),
    "SINC_KERNEL_CONTRACT_COMPLETE",
    ("cases", "sinc1_outputs", "sinc5_outputs", "steady_compares", "checks"),
    (),
)
# SPI 的交易须同时核对观察到的写请求与实际读取，防止读路径恒等于回显值。
# 串行位数、连续传输以及未绑定时的拒绝行为属于各自协议用例。
# 独立协议通过不代表板级引脚和外设时序可用，范围在完成行的边界说明中保留。
BENCHES["spi_slave_tb"] = Bench(
    (
        "subsys/ad4080_dsp/rtl/spi_slave.sv",
        "subsys/ad4080_dsp/rtl/ad4080_regs.sv",
        "subsys/ad4080_dsp/sim/tb/spi_slave_tb.sv",
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
        "subsys/ad4080_dsp/rtl/result_fifo.sv",
        "subsys/ad4080_dsp/rtl/fifo_capture.sv",
        "subsys/ad4080_dsp/sim/tb/fifo_capture_contract_tb.sv",
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
# DSP 顶层的结果面向协议和数值窗口。要求 OPEN 行使未覆盖边界出现在证据中，
# 避免 DSP 搜索和 FIFO 通过被误读成对前级采样或外部设备互操作的确认。
# 输入接入与数字处理的控制分属不同模块，顶层的窗口激励不能复用叶子激励。
BENCHES["ad4080_top_tb"] = Bench(
    (
        "subsys/ad4080_dsp/rtl/ad4080_top.sv",
        "subsys/ad4080_dsp/rtl/spi_slave.sv",
        "subsys/ad4080_dsp/rtl/ad4080_regs.sv",
        "subsys/ad4080_dsp/rtl/ad4080_dsp_chain.sv",
        "subsys/ad4080_dsp/rtl/sys_error_corr.sv",
        "subsys/ad4080_dsp/rtl/sinc1_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_comp.sv",
        "subsys/ad4080_dsp/rtl/event_detect.sv",
        "subsys/ad4080_dsp/rtl/fifo_capture.sv",
        "subsys/ad4080_dsp/rtl/result_fifo.sv",
        "subsys/ad4080_dsp/rtl/gpio_ctrl.sv",
        "subsys/ad4080_dsp/sim/tb/ad4080_top_tb.sv",
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
        "subsys/ad4080_dsp/rtl/sinc5_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_comp.sv",
        "subsys/ad4080_dsp/sim/tb/sinc5_comp_contract_tb.sv",
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
# DSP 接入用例以已经提供的校准码为边界，不重跑模拟采样或重构内部估计。
# 已失效样本应经过明确拒绝路径；同样需要测试取消发生在下一笔接入之前的情形。
# 错误隔离、有效输出和停流后重新受理分别计数，防止只覆盖稳态。
BENCHES["sar20_dsp_integration_tb"] = Bench(
    (
        "subsys/ad4080_dsp/rtl/sar20_ad4080_top.sv",
        "subsys/ad4080_dsp/rtl/ad4080_top.sv",
        "subsys/ad4080_dsp/rtl/spi_slave.sv",
        "subsys/ad4080_dsp/rtl/ad4080_regs.sv",
        "subsys/ad4080_dsp/rtl/ad4080_dsp_chain.sv",
        "subsys/ad4080_dsp/rtl/sys_error_corr.sv",
        "subsys/ad4080_dsp/rtl/sinc1_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_comp.sv",
        "subsys/ad4080_dsp/rtl/event_detect.sv",
        "subsys/ad4080_dsp/rtl/fifo_capture.sv",
        "subsys/ad4080_dsp/rtl/result_fifo.sv",
        "subsys/ad4080_dsp/rtl/gpio_ctrl.sv",
        "subsys/ad4080_dsp/sim/tb/sar20_dsp_integration_tb.sv",
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
BENCHES["ad4080_regs_rejection_tb"] = Bench(
    ("subsys/ad4080_dsp/rtl/ad4080_regs.sv", "subsys/ad4080_dsp/sim/tb/ad4080_regs_rejection_tb.sv"),
    "AD4080_REG_REJECTION_COMPLETE",
    ("rejected_writes", "metadata_rejects", "preserved_compares", "checks"),
    (),
)
BENCHES["spi_unbound_contract_tb"] = Bench(
    ("subsys/ad4080_dsp/rtl/spi_slave.sv", "subsys/ad4080_dsp/sim/tb/spi_unbound_contract_tb.sv"),
    "SPI_UNBOUND_CONTRACT_COMPLETE",
    ("frames", "rejects", "missing_bindings", "not_ready", "invalid_address", "readonly", "checks"),
    (),
    zero_fields=("write_requests",),
)

# Generic mathematical/protocol cases consume an explicit numerical fixture.
# 夹具专门约束数字协议和定点格式。显式周期值属于该夹具，不作为模块性能指标。
# 选中的重构场景需要独立完成行与事件计数，不能使用别的重构配置下的储存结果。
BENCHES["review_recon_protocol_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "flow/sim/tb/review_recon_protocol_tb.sv",
    ),
    "REVIEW_RECON_PROTOCOL_COMPLETE",
    ("outputs", "requests"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["weight_row_cache_tb"] = Bench(
    ("rtl/core/weight_store.sv", "flow/sim/tb/weight_row_cache_tb.sv"),
    "WEIGHT_ROW_CACHE_COMPLETE",
    ("geometries", "steps"),
    requires_protocol_fixture=True,
    parameters=(("STRESS_MAXIMUM", 0),),
)
# 权重归约用数值夹具检查选通和重构计算。用例名称里的 PPA 不承诺布局量测。
# 速度或面积比较本应来自单独工艺工具，不能从这份事件次数外推。
BENCHES["cal_weight_reduce_ppa_tb"] = Bench(
    ("rtl/core/cal_weight_reduce.sv", "flow/sim/tb/cal_weight_reduce_ppa_tb.sv"),
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
        "flow/sim/tb/recon_ppa_latency_tb.sv",
    ),
    "RECON_PPA_LATENCY_COMPLETE",
    ("profiles", "samples_per_profile", "outputs"),
    requires_protocol_fixture=True,
    parameters=(),
)
BENCHES["tree_mapping_tb"] = Bench(
    ("rtl/core/cal_weight_reduce.sv", "flow/sim/tb/tree_mapping_dut.sv", "flow/sim/tb/tree_mapping_tb.sv"),
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
        "flow/sim/tb/calibration_physical_tb.sv",
    ),
    "CALIBRATION_PHYSICAL_COMPLETE",
    ("samples", "coefficient_rows", "invalid_cases"),
    requires_protocol_fixture=True,
    parameters=(),
)
# 校准恢复用例消费身份明确的系数格式；覆盖装载和恢复策略，不从实验临时值推默认格式。
# 前级训练产生的估计质量另有评价。完整回放只验证已给定系数下的数字通过条件。
BENCHES["calibration_recovery_tb"] = Bench(
    (
        "rtl/core/cal_weight_reduce.sv",
        "rtl/core/adc2_dec.sv",
        "rtl/core/cal_residue_mac.sv",
        "rtl/core/div_floor.sv",
        "rtl/core/cal_output_stage.sv",
        "rtl/core/recon_core.sv",
        "flow/sim/tb/calibration_recovery_tb.sv",
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
        "flow/sim/tb/generic_identity_tb.sv",
    ),
    "GENERIC_IDENTITY_COMPLETE",
    ("cases", "checks", "full_12bit", "boundary_20bit"),
    requires_protocol_fixture=True,
    parameters=(),
)

SPECIAL_TOPS = ("arithmetic_audit_tb", "engineering_fsm_tb")
BENCHES["sinc_rate_oracle_tb"] = Bench(
    (
        "subsys/ad4080_dsp/rtl/sinc1_decim.sv",
        "subsys/ad4080_dsp/rtl/sinc5_decim.sv",
        "subsys/ad4080_dsp/sim/tb/sinc_rate_oracle_tb.sv",
    ),
    "SINC_RATE_ORACLE_COMPLETE",
    ("cases", "outputs1", "outputs5", "positive", "negative", "checks"),
)
# 同一测试台用于大 RAM 深度的数值场景。模块名一致不代表运行参数或预期深度相同。
# 该场景保留独立证据目录，并在汇总前将完成行的 depth 与登记值比对。
BENCHES["fifo_capture_16k"] = Bench(
    BENCHES["fifo_capture_contract_tb"].sources,
    "FIFO_CAPTURE_CONTRACT_COMPLETE",
    BENCHES["fifo_capture_contract_tb"].positive_fields,
    BENCHES["fifo_capture_contract_tb"].lint,
    module="fifo_capture_contract_tb",
    parameters=(("DEPTH", 16384),),
)


# =============================================================================
# 3. 输入身份、输出位置与日志
# =============================================================================
# 内容摘要用于消除路径、mtime 和别名的歧义。报告应同时保留快照和运行日志。
# 同一文件名可能对应不同字节；在执行边界校验实际读入内容，而非只看路径存在。
# JSON 拒绝 NaN 和无穷，避免下游把缺失数字解析成正常状态。
def digest(path: Path) -> str:
    """计算实际文件的 SHA256，用内容而不是文件名标识输入。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    """向本次显式输出路径写完整 JSON 记录。"""
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


# 输出目录由调用方选择。先拒绝符号链接和已有目标，避免旧报告成为本轮证据。
# 解析后的路径不能位于任何 Git 工作树内，防止测试改写源码或向仓库灌入产物。
# exist_ok=False 让并发运行竞争同名目录时有一个明确失败，不能互相覆盖。
# 空目录建立前不运行仿真器；失败后由调用方处置该次保留的独立目录。
def new_output(path: Path) -> Path:
    """拒绝已有目录以及当前或其他 Git 工作树内的构建位置。"""
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


# 工具由调用方准备。只解析命令词并定位首个可执行文件，后续参数逐词传递。
# 用绝对 executable 路径使工作目录切换后仍调用同一程序，不从当前目录推定工具。
# 这里不设置或重写许可证变量，不为环境缺口伪造可用的仿真器。
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


# 所有外部执行都把 stdout 和 stderr 合并到指定日志，方便关联诊断与返回值。
# cwd 属于该次运行空间。仿真器的默认中间文件写入不会落到启动者的 HOME。
# 超时和启动失败没有正常进程返回码；仍写命令记录，区分缺工具和测试返回非零。
# 命令记录保留实际词序和日志 SHA；执行环境不写入该记录，避免扩散宿主配置。
# 返回码非零先于日志完成行判定，防止旧输出或工具错误被误判为通过。
# 错误检测为第二层证据，命令即使 rc=0，也不能携带真实 ERROR/FATAL 继续。
def checked_run(command: list[str], *, cwd: Path, env: dict, log: Path, timeout: int) -> str:
    """保留完整日志并检查实际返回码；超时或错误诊断均导致失败。"""
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
    # 先检查启动和超时，再检查工具返回状态；执行异常时不允许继续解析正常完成行。
    # full log 已持久化，报错只返回日志位置，避免调用方丢失原始失败现场。
    if execution_error is not None:
        raise RuntimeError(f"execution failed; see {log}")
    if raw_returncode:
        raise RuntimeError(f"command returned {raw_returncode}; see {log}")
    if ERROR_DIAGNOSTIC.search(output):
        raise RuntimeError(f"tool emitted an error despite rc=0; see {log}")
    return output


# 完成行必须唯一且匹配整行。用前缀或统计摘要子串无法区分断言前的进展。
# 正计数证明该类测试事件至少执行一次；对禁止动作的零计数在调用层单独校验。
# 先检查唯一字段再转换整数，从而拒绝用重复字段隐藏零执行。
def completion(output: str, marker: str, fields: tuple[str, ...]) -> dict[str, int]:
    """要求唯一整行完成记录及每个必需字段的正计数，单独 finish 或零计数不算完成。"""
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


# =============================================================================
# 4. 运行库与编译器
# =============================================================================
# 只在完整 Verilator 运行树能解析并确实含可执行文件时建立 ASCII 别名。
# 常见系统包的可执行文件和头文件分离。不能仅根据头目录改 VERILATOR_ROOT。
# 已有别名指向别的工具即终止，避免两个运行共享同名路径而复现实验结果。
# 别名随临时运行目录释放，不改宿主安装或持久化环境。
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


# 显式 LLVM 编译器及 C++20 参数与含 timing 的生成 C++ 配套。
# 探测失败时不切换为宿主默认编译选项，避免不同运行得到不具可比性的结果。
# make 再次解析变量时应接到同一可执行文件，编译脚本的 Python 入口也显式指定。
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


# =============================================================================
# 5. 单组件运行的隔离与快照
# =============================================================================
# folder 保存持久证据，work 保存可回收的中间对象。二者均为本次运行独占。
# 先确定登记名和实际模块名。前者用于证据目录，后者供工具选顶层和二进制名。
# 带参数覆盖的测试要求支持其编译选项的工具，其他工具不自动换成默认参数。
# 把声明依赖按仓库目录结构复制，并逐个求摘要，避免 include 路径被展平成同名文件。
# 编译器只访问临时 repo 别名下的快照，不直接沿原检出依赖调用方当前目录。
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
        if spec.requires_math_format:
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
            path = work / "repo" / name
            if not path.is_file():
                raise ValueError(f"selected dependency is absent: {name}")
            sources.append(str(path))
        plusargs = []
        parameters = dict(spec.parameters)
        # =============================================================================
        # 5.1 运行载荷与参数
        # =============================================================================
        # 当前组件的数值边界来自独立夹具或配对向量。
        # 整链入口独立装载 ADC 系数和片组参数。
        # 扩展配对向量必须给出实际周期数、不同用例数和有效输出数。
        # 测试台读取固定向量文件名，因此在临时目录建立软链，归档仍保留完整副本。
        # 预期数量由调用方带入，不能从被测日志反向填预期而让漏事件得到 PASS。
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
        # =============================================================================
        # 5.2 工具执行与输出二进制
        # =============================================================================
        # 每个顶层的对象目录重新创建，禁止使用其他顶层或上次失败遗留的编译产物。
        # 生产 lint 排除当前 TB，独立检查设计顶层和必要配置；宽度及漏端口仍为错误。
        # TB 编译对其余诊断保持原策略，关键端口和范围错误仍不能被警告降级。
        # 实际产生的二进制必须有效，再执行成功的路径不能由编译返回码预判。
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
        # 不同工具保留各自的 include 参数和顶层选项，不能把一套编译词传给另一工具。
        # VCS 运行时用退出状态传播失败；其他运行同样需要独立的日志和完成记录。
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
        # 非空二进制是先验条件，仍需检查仿真返回值及断言。
        # 运行 cwd 切到本组件证据目录，包含显式 CSV 和运行日志的输出位置可以定位。
        # 标记不足或重复时不输出结果 PASS，避免半途中断但已产生部分 CSV 被误读。
        if not binary.is_file() or binary.stat().st_size == 0:
            raise RuntimeError("compiler returned zero without producing a new simulation binary")
        output = checked_run(
            run_command, cwd=folder, env=env, log=folder / "run.log", timeout=args.timeout
        )
        counts = completion(output, spec.marker, spec.positive_fields)
        # =============================================================================
        # 5.3 完成计数、证据范围与退出门禁
        # =============================================================================
        # 禁止事件计数须显式给零。字面记录缺失与确实没有事件是两种情况。
        # OPEN 声明须出现且唯一，避免工具、测试台和报告对未覆盖范围各自采用默认解释。
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
        # 除了完成数量再检查 CSV 行数和每行列数。截断证据不能靠最后一行标记绕过。
        # 向量改变时快照仍用于审计，但本运行不能证明调用方当前文件是同一输入。
        if spec.pair_vectors:
            with (folder / "actual.csv").open(newline="") as stream:
                rows = list(csv.reader(stream))
            if len(rows) != args.expected_cycles + 1 or any(len(row) != 25 for row in rows):
                raise ValueError(
                    "pair component actual CSV is incomplete or does not have 25 columns"
                )
            if digest(vectors) != digest(saved_vectors):
                raise RuntimeError("pair vectors changed during execution; snapshot retained")
        # 运行前后同时核对源码和载荷，发现并发编辑时不把旧快照通过称为新内容已通过。
        # 各失败均沿本次目录保留证据，调用者必须选择新目录后再试。
        if any(digest(REPO / name) != expected for name, expected in source_hashes.items()):
            raise RuntimeError(
                "selected source changed during execution; snapshot retained for review"
            )
        # 每项 result 只绑定所选顶层、快照和运行 SHA，不能代表整个工程默认验收。
        # 运行的输出和参数、输入摘要同时记录，便于第三方从同一夹具复算。
        # 结果范围为独立数字组件，报告同时列出本次声明的未接通边界。
        record = {
            "status": "PASS",
            "scope": SCOPE_COMPONENT,
            "top": top,
            "module": module,
            "parameters": parameters,
            # 保留结果结构中的空载荷字段，便于已有报告读取器识别组件结果。
            "payload_sha256": {},
            "counts": counts,
            "sources_sha256": source_hashes,
            "marker": spec.marker,
            "open_boundaries": list(spec.open_markers),
            "input_bundle": None,
            "run_log_sha256": digest(folder / "run.log"),
        }
        write_json(folder / "result.json", record)
        return record


# =============================================================================
# 6. CLI 与汇总结果的生命周期
# =============================================================================
# 显式顶层或登记的专用驱动是唯一入口。不会扫描其他可用仿真文件寻找替代项目。
# 输入路径统一在工具启动前解析，使后续 cwd 切换不改变相对路径对应的文件。
# 同名顶层禁止重复，避免结果目录覆盖。无效的超时参数不用来启多个僵死等待。
def main(argv=None) -> int:
    """按显式选择运行；缺依赖、编译、执行或完成计数错误均以非零状态退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tops", nargs="+", choices=(*BENCHES, *SPECIAL_TOPS), required=True)
    parser.add_argument(
        "--simulator", choices=("verilator", "vcs", "iverilog"), default="verilator"
    )
    parser.add_argument("--math-format", type=Path)
    parser.add_argument("--protocol-fixture", type=Path)
    parser.add_argument("--vectors", type=Path)
    parser.add_argument("--expected-cycles", type=int)
    parser.add_argument("--expected-cases", type=int)
    parser.add_argument("--expected-valid-outputs", type=int)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    for field in (
        "math_format",
        "protocol_fixture",
        "vectors",
    ):
        path = getattr(args, field)
        if path is not None:
            setattr(args, field, path.expanduser().resolve())
    if len(args.tops) != len(set(args.tops)) or args.timeout <= 0:
        parser.error("tops must be unique and timeout must be positive")
    # 即使工具版本读取失败，也保留本轮唯一输出位置。只有该运行关闭后汇总最终状态。
    # 版本命令用短超时，不复用上次保存的版本，也不借默认工具选择填缺口。
    # RUNNING 状态不是验证结果；调用者只应从实际结束后的 manifest 解释通过。
    out = None
    manifest = {"schema_version": 1, "status": "RUNNING", "selected_tops": args.tops, "results": []}
    try:
        out = new_output(args.out)
        command = command_for(args.simulator)
        # 工具各自定义版本参数，因此使用登记的原命令，保持语言环境与工具运行一致。
        # 记录打印的实际版本和路径身份，下一次独立运行可识别版本变动而无需比较猜测。
        version_args = (
            ["-V"]
            if args.simulator == "verilator"
            else ["-ID"]
            if args.simulator == "vcs"
            else ["-V"]
        )
        # 版本字符串不仅用于汇报，也用于为自解压安装识别运行时位置。
        # 若路径含空格而运行库不完整，后续选择保留现有环境，避免造出无效别名。
        # 读取版本失败时不尝试另一个工具，以便准确区分宿主缺依赖与设计编译失败。
        # 启动时给不同后端保留各自环境，主机 PATH 与许可证始终由调用者准备。
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
        # 算术和工程协议使用独立驱动；只有子驱动自己的完备记录满足时才并入汇总。
        # 两个驱动都有明确输入要求，缺失不能降级到其他夹具。非零返回保留其驱动日志。
        # 普通组件按登记运行，汇总中的多项 PASS 是逐项证据，不改变各项原有范围。
        for top in args.tops:
            # 算术子驱动本身有不同的证据结构。父运行仅在子清单和各算术计数齐备后纳入汇总。
            # 父层限制子进程总等待时间，内层仍对各命令和完成记录设独立门禁。
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
            # 工程协议夹具验证显式状态顺序。父层按固定记录形状识别完整子结果。
            # 部分阶段生成 CSV 不构成完整执行，不因为有某一张表而越过子结果状态。
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
