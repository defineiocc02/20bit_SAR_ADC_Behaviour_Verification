#!/usr/bin/env python3
# =============================================================================
# 文件名     : check_peripheral_counterexamples.py
# 模块名     : 外围四态反例门禁
# 版本       : 随 Git 源版本
# 功能       : 用 Icarus 检查独立 FIFO 复位后的 POR 事件和非单位增益数据路径。
# 设计说明   : POR 刺激复用已真实读写 OTP、SRAM 的台架。只有确认过的旧事件
#              才用于复位注入，避免未建立初始状态造成假绿。
# 变更记录   : 增加可用于 CI 的独立执行路径。
# =============================================================================
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERIPH = ROOT / "rtl/ad4080_periph"
DSP = ROOT / "rtl/ad4080_dsp"
POR_ANCHOR = '        $display("PASS REFIN loss freezes new writes and retains prior readable result");'
POR_PROBE = '''
        // 确保本次检测前的事件令牌为 1，而且核心已经确认该令牌。
        if (dut.por_req_aon_q == 1'b0) begin
            @(negedge clk_aon_i); ref_ok_async_i = 1'b0;
            repeat (4) @(negedge clk_aon_i); ref_ok_async_i = 1'b1;
            repeat (6) @(negedge clk_aon_i);
        end
        if (dut.por_req_aon_q !== 1'b1 || dut.por_ack_core_q !== 1'b1)
            $fatal(1, "independent POR probe did not establish the old acknowledged token");
        por_events_before_reference = por_core_consumed;
        @(negedge clk_core_i);
        force dut.fifo_reset_no = 1'b0;
        repeat (4) @(negedge clk_core_i);
        release dut.fifo_reset_no;
        repeat (16) @(negedge clk_core_i);
        if (fifo_reset_no !== 1'b1 || por_core_consumed != por_events_before_reference)
            $fatal(1, "POR_INDEPENDENT_RESET FAIL replayed=%0d",
                   por_core_consumed - por_events_before_reference);
        $display("POR_INDEPENDENT_RESET PASS replayed=0");
        $finish;
'''


def run(command, out, name):
    """Icarus 编译返回成功后，再要求运行日志包含预期业务完成标志。"""
    with (out / name).open("w") as log:
        subprocess.run(command, cwd=out, stdout=log, stderr=subprocess.STDOUT, check=True)
    return (out / name).read_text()


def main():
    parser = argparse.ArgumentParser(description="外围独立四态反例")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if out == ROOT or ROOT in out.parents:
        parser.error("output must be outside checkout")
    out.mkdir(parents=True)
    run(["iverilog", "-g2012", "-Wall", "-s", "sys_error_corr_contract_tb", "-o", "gain.vvp",
         DSP / "sys_error_corr.sv", DSP / "sim/tb/sys_error_corr_contract_tb.sv"], out, "gain-compile.log")
    gain = run(["vvp", "-N", "gain.vvp"], out, "gain.log")
    outcome = next(line for line in gain.splitlines() if line.startswith("SYS_ERROR_CORR_CONTRACT_COMPLETE"))
    print(outcome)

    sources = [PERIPH / s for s in (PERIPH / "integration/files.f").read_text().splitlines()
               if s.strip() and not s.lstrip().startswith("//")]
    bench = (PERIPH / "integration/peripheral_services_tb.sv").read_text()
    if bench.count(POR_ANCHOR) != 1:
        raise ValueError("POR probe requires unique live reference-fault anchor")
    instrumented = out / "peripheral_services_tb.sv"
    instrumented.write_text(bench.replace(POR_ANCHOR, POR_ANCHOR + POR_PROBE))
    # 转换器保留时间字面值。统一补生产和台架共同的时间单位，防止 Icarus 默认时间精度将钟周期归零。
    with (out / "por.v").open("w") as netlist, (out / "por-translate.log").open("w") as diagnostic:
        netlist.write("`timescale 1ns/1ps\n")
        netlist.flush()
        subprocess.run(["sv2v", *sources, instrumented], cwd=out, stdout=netlist, stderr=diagnostic, check=True)
    run(["iverilog", "-g2012", "-Wall", "-s", "peripheral_services_tb", "-o", "por.vvp", "por.v"],
        out, "por-compile.log")
    result = run(["vvp", "-N", "por.vvp"], out, "por.log")
    if result.splitlines().count("POR_INDEPENDENT_RESET PASS replayed=0") != 1:
        raise RuntimeError("independent POR proof lacks completion marker")
    print("POR_INDEPENDENT_RESET PASS replayed=0")


if __name__ == "__main__":
    main()
