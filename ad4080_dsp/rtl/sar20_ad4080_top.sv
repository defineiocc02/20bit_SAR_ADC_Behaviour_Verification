//=============================================================================
// @file    sar20_ad4080_top.sv
// @brief   SAR 结果退役适配与持续故障隔离。
// @details SAR 和本工程输出采用偏移二进制码，DSP 数值通路采用 20 bit 补码。
//          任一 SAR 结果标志或模拟粘滞故障均停止接收，直至明确执行完整重启。
//          上游 conversion_fault 独立传递无结果的转换失败，不按 valid 间隙或 ID 跳号推断。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: [设计约定] SAR 结果退役、配置代次与 DSP_G1_ACTIVE_2；不推断 AD4080 内部结构。
// 时钟域: clk；所有结果退役和控制输入均同步。
// 复位: rst_n 低有效；sar_epoch_clear 或 dsp_restart 也清 DSP 状态，不跨越缺失样本自动续流。
// 延迟: 由所选 DSP 通路决定；入口接受/拒绝 ID 是组合诊断事件。
// 吞吐: 每个 clk 最多一个退役结果。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: ad4080_top。
// 边界: adc_idle/interface_ready 由外部判定；入口 ID 不是滤波或 FIFO 输出 ID。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
module sar20_ad4080_top #(
    parameter int FIFO_DEPTH=16384,
    parameter bit P_GAIN_MAP_VALID=1'b0,
    parameter int K_GAIN=-1,
    parameter int SHIFT=-1,
    parameter bit P_GAIN_CODE0_SUPPORTED=1'b0,
    parameter bit P_COMP_COEFF_VALID=1'b0,
    parameter int C0=32'h40000000,
    parameter int C1=32'h40000000,
    parameter int C2=32'h40000000,
    parameter int P_MULTIBYTE_ORDER=-1,
    parameter int P_OFFSET_LSB_ADDR=-1,
    parameter int P_PRODUCT_ID_L=-1,
    parameter int P_SHORT_ADDR_BITS=-1,
    parameter int P_WRITE_SDO_DRIVE=-1
) (
    input logic clk, rst_n, sar_epoch_clear, dsp_restart,
    input logic [19:0] sar_dout,
    input logic sar_dout_valid,
    input logic [31:0] sar_sample_id,
    input logic [4:0] sar_flags,
    input logic sar_analog_ovf, sar_conversion_fault,
    output logic sample_accepted,
    output logic [31:0] accepted_sample_id,
    output logic sample_rejected,
    output logic [31:0] rejected_sample_id,
    output logic [4:0] rejected_flags,
    output logic rejected_after_fault, stream_fault,
    input logic cs_n, sclk, sdi,
    output logic sdo, sdo_oe,
    input logic adc_idle, interface_ready,
    input logic filter_sync, ext_event, arm, rd_en,
    input logic [3:0] gpio_i,
    output logic [3:0] gpio_o, gpio_oe,
    output logic [19:0] dout,
    output logic dout_valid,
    output logic [7:0] status,
    output logic correction_clip_low, correction_clip_high,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason,
    output logic processing_reject
);
    wire dsp_rst_n=rst_n && !sar_epoch_clear && !dsp_restart;
    wire input_fault=sar_dout_valid && (|sar_flags);
    wire conversion_fault_active=(sar_conversion_fault !== 1'b0);
    wire stream_active=dsp_rst_n && !stream_fault && !input_fault &&
                       !sar_analog_ovf && !conversion_fault_active;
    wire [19:0] signed_input={~sar_dout[19],sar_dout[18:0]};
    logic [19:0] signed_output;
    logic inner_valid,clip_low,clip_high;
    always_ff @(posedge clk or negedge dsp_rst_n) begin
        if (!dsp_rst_n) stream_fault<=1'b0;
        else if (input_fault || sar_analog_ovf || conversion_fault_active) stream_fault<=1'b1;
    end
    assign sample_accepted=stream_active && sar_dout_valid;
    assign accepted_sample_id=sar_sample_id;
    assign sample_rejected=dsp_rst_n && sar_dout_valid &&
                           (stream_fault || input_fault || sar_analog_ovf || conversion_fault_active);
    assign rejected_sample_id=sar_sample_id;
    assign rejected_flags=sar_flags;
    assign rejected_after_fault=sample_rejected && stream_fault;
    ad4080_top #(.IN_W(20),.FIFO_DEPTH(FIFO_DEPTH),
        .P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),.K_GAIN(K_GAIN),.SHIFT(SHIFT),
        .P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),.P_COMP_COEFF_VALID(P_COMP_COEFF_VALID),
        .C0(C0),.C1(C1),.C2(C2),.P_MULTIBYTE_ORDER(P_MULTIBYTE_ORDER),
        .P_OFFSET_LSB_ADDR(P_OFFSET_LSB_ADDR),.P_PRODUCT_ID_L(P_PRODUCT_ID_L),
        .P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),.P_WRITE_SDO_DRIVE(P_WRITE_SDO_DRIVE)) u_dsp (
        .clk(clk),.rst_n(dsp_rst_n),.cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),
        .adc_idle(adc_idle),.interface_ready(interface_ready),
        .valid_in(sample_accepted),.x(signed_input),.filter_sync(filter_sync),
        .ext_event(ext_event),.arm(arm),.rd_en(rd_en && stream_active),
        .gpio_i(gpio_i),.gpio_o(gpio_o),.gpio_oe(gpio_oe),
        .dout(signed_output),.dout_valid(inner_valid),.status(status),
        .correction_clip_low(clip_low),.correction_clip_high(clip_high),
        .cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason),.processing_reject(processing_reject)
    );
    assign dout={~signed_output[19],signed_output[18:0]};
    assign dout_valid=inner_valid && stream_active;
    assign correction_clip_low=clip_low && stream_active;
    assign correction_clip_high=clip_high && stream_active;
endmodule
`default_nettype wire
