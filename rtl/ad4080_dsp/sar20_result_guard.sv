// =============================================================================
// 文件名     : sar20_result_guard.sv
// 模块名     : sar20_result_guard
// 版本       : 随 Git 源版本
// 功能       : 将 SAR 偏移二进制转换为补码，并按同一结果标签隔离故障。
// 设计说明   : 复用并行 DSP 与单芯片的退役规则。无有效码的故障同样锁存。
//              已存记录位于模块外，可按独立复位和读口继续退役。
// =============================================================================
`timescale 1ns/1ps
`default_nettype none
module sar20_result_guard (
    input logic clk, rst_n, restart_sync,
    input logic [19:0] sar_dout,
    input logic sar_dout_valid,
    input logic [31:0] sar_sample_id,
    input logic [4:0] sar_flags,
    input logic sar_analog_ovf, sar_conversion_fault,
    output logic [19:0] signed_input,
    output logic stream_active, stream_fault,
    output logic sample_accepted,
    output logic [31:0] accepted_sample_id,
    output logic sample_rejected,
    output logic [31:0] rejected_sample_id,
    output logic [4:0] rejected_flags,
    output logic rejected_after_fault
);
    // -------------------------------------------------------------------------
    // 1. 当前转换的收码资格和跨样本粘滞隔离
    // -------------------------------------------------------------------------
    // 当前沿的故障先于同沿有效数据，下一沿继续拒绝，明确重启才告知下游换代。
    wire input_fault=sar_dout_valid && (|sar_flags);
    // 只有明确的 0 能解除上游故障许可。两态变量保留四态台架的拒绝行为。
    bit conversion_fault_clear;
    always_comb conversion_fault_clear = sar_conversion_fault == 1'b0;
    wire conversion_fault_active=!conversion_fault_clear;
    assign stream_active=rst_n && !restart_sync && !stream_fault && !input_fault &&
                       !sar_analog_ovf && !conversion_fault_active;
    assign signed_input={~sar_dout[19],sar_dout[18:0]};
    // 已冻结记录的读口保持独立，转换故障只隔离新数据。换代才取消旧记录。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) stream_fault<=1'b0;
        else if (restart_sync) stream_fault<=1'b0;
        else if (input_fault || sar_analog_ovf || conversion_fault_active) stream_fault<=1'b1;
    end
    assign sample_accepted=stream_active && sar_dout_valid;
    assign accepted_sample_id=sar_sample_id;
    assign sample_rejected=rst_n && !restart_sync && sar_dout_valid &&
                           (stream_fault || input_fault || sar_analog_ovf || conversion_fault_active);
    assign rejected_sample_id=sar_sample_id;
    assign rejected_flags=sar_flags;
    assign rejected_after_fault=sample_rejected && stream_fault;
endmodule
`default_nettype wire
