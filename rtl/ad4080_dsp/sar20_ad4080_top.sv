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
    wire restart_sync=sar_epoch_clear || dsp_restart;
    logic [19:0] signed_input, signed_output;
    logic stream_active, inner_valid, stored_word_valid, clip_low, clip_high;
    sar20_result_guard u_retirement (
        .clk, .rst_n, .restart_sync, .sar_dout, .sar_dout_valid, .sar_sample_id,
        .sar_flags, .sar_analog_ovf, .sar_conversion_fault, .signed_input,
        .stream_active, .stream_fault, .sample_accepted, .accepted_sample_id,
        .sample_rejected, .rejected_sample_id, .rejected_flags, .rejected_after_fault
    );
    // 并行适配器只接数值与寄存器。下面的模式和 IO 观察输出保留在 DSP 叶层。
    logic unused_pwr_cmd_valid_o, unused_cnv_data_timing_error_o, unused_continuous_bypass_o, unused_continuous_enable_o, unused_continuous_flush_o, unused_continuous_word_valid_o;
    logic [1:0] unused_pwr_cmd_op_o, unused_pwr_cmd_value_o;
    logic [7:0] unused_data_intf_config_a_o, unused_data_intf_config_b_o, unused_data_intf_config_c_o, unused_filter_config_o;
    logic [19:0] unused_continuous_word_o;
    logic [31:0] unused_continuous_word_tag_o;
    ad4080_top #(.IN_W(20),.FIFO_DEPTH(FIFO_DEPTH),
        .P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),.K_GAIN(K_GAIN),.SHIFT(SHIFT),
        .P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),.P_MULTIBYTE_ORDER(P_MULTIBYTE_ORDER),
        .P_OFFSET_LSB_ADDR(P_OFFSET_LSB_ADDR),.P_PRODUCT_ID_L(P_PRODUCT_ID_L),
        .P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),.P_WRITE_SDO_DRIVE(P_WRITE_SDO_DRIVE)) u_dsp (
    // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
    .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(unused_pwr_cmd_valid_o),
    .pwr_cmd_op_o(unused_pwr_cmd_op_o), .pwr_cmd_value_o(unused_pwr_cmd_value_o), .pwr_rsp_valid_i(1'b0),
    .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
    .pwr_ldo_pd_i(2'b00), .rom_crc_error_i(1'b0),

    // 此适配器的既有契约是并行结果与标记；设备计时、串行发射及 AON 由产品装配接入。
    .cnv_tick_i(1'b0), .conversion_timing_error_i(1'b0), .por_ana_event_i(1'b0),
    .cnv_data_timing_error_o(unused_cnv_data_timing_error_o), .phy_fifo_full_i(1'b0), .phy_fifo_read_done_i(1'b0),
    .csr_fifo_full_i(1'b0), .csr_fifo_read_done_i(1'b0), .data_config_ready_i(1'b0),
    .sample_tag_i(sar_sample_id), .data_intf_config_a_o(unused_data_intf_config_a_o), .data_intf_config_b_o(unused_data_intf_config_b_o), .data_intf_config_c_o(unused_data_intf_config_c_o),
    .continuous_bypass_o(unused_continuous_bypass_o), .continuous_enable_o(unused_continuous_enable_o), .continuous_flush_o(unused_continuous_flush_o), .filter_config_o(unused_filter_config_o), .continuous_word_valid_o(unused_continuous_word_valid_o), .continuous_word_o(unused_continuous_word_o),
    .continuous_word_tag_o(unused_continuous_word_tag_o), // 连续 LVDS 未绑定。此并行出口不使用流标签。
        .clk(clk),.rst_n(rst_n),.restart_sync_i(restart_sync),
        .cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),
        .adc_idle(adc_idle),.interface_ready(interface_ready),
        .valid_in(sample_accepted),.x(signed_input),.filter_sync(filter_sync),
        .ext_event(ext_event),.arm(arm && stream_active),.rd_en(rd_en && rst_n && !restart_sync),
        .gpio_i(gpio_i),.gpio_o(gpio_o),.gpio_oe(gpio_oe),
        .dout(signed_output),.dout_valid(inner_valid),.stored_word_valid_o(stored_word_valid),.status(status),
        .correction_clip_low(clip_low),.correction_clip_high(clip_high),
        .cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason),.processing_reject(processing_reject)
    );
    assign dout={~signed_output[19],signed_output[18:0]};
    assign dout_valid=inner_valid && (stream_active || stored_word_valid);
    assign correction_clip_low=clip_low && stream_active;
    assign correction_clip_high=clip_high && stream_active;
endmodule
`default_nettype wire
