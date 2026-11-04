//=============================================================================
// @file    sar20_ad4080_top.sv
// @brief   SAR结果到DSP的码制、故障和复位边界
// @details 入口/并行出口翻转最高位以转换offset-binary与补码；串行payload保持补码。
//          任一输入flags或模拟sticky异常停止接收与输出，直到全DSP重启。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 无；本项目接口合同。
// 时钟域   : clk；所有SAR数据、标志和控制同步，不含CDC
// 复位     : 外部rst_n或sar_epoch_clear/dsp_restart异步断言DSP复位；必须同步释放
// 延迟     : sample_accepted/rejected为输入侧组合事件，ID只在对应事件有效
// 吞吐     : 输入无ready/反压；下游吞吐依赖滤波/FIFO/串行配置
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : ad4080_top
// 边界     : DSP重启同时清配置/滤波/FIFO；仅DSP重启不清SAR模拟sticky。
//          输出不携带过滤窗口/结果ID；不能把输入accepted ID当成滤波/FIFO输出标签。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sar20_ad4080_top #(
    parameter int FIFO_DEPTH = 16384
) (
    input  logic        clk,
    input  logic        rst_n,
    // Both pulses reset the DSP, including configuration and FIFO contents.
    input  logic        sar_epoch_clear,
    input  logic        dsp_restart,

    // sar20_digital_core retirement interface. flags = {clip_high, clip_low,
    // adc2_ovf, gain_err, acc_ovf}. All five flags are treated as a fault.
    input  logic [19:0] sar_dout,
    input  logic        sar_dout_valid,
    input  logic [31:0] sar_sample_id,
    input  logic [4:0]  sar_flags,
    // SAR analog_ovf is sticky and is not part of sar_flags.
    input  logic        sar_analog_ovf,

    // Input-side audit stream. IDs are not attached to filtered/FIFO outputs.
    output logic        sample_accepted,
    output logic [31:0] accepted_sample_id,
    output logic        sample_rejected,
    output logic [31:0] rejected_sample_id,
    output logic [4:0]  rejected_flags,
    output logic        rejected_after_fault,
    output logic        stream_fault,

    input  logic        cs_n,
    input  logic        sclk,
    input  logic        sdi,
    output logic        sdo,
    output logic        sdo_oe,
    input  logic        cnv_p,
    input  logic        cnv_n,
    output logic        cnv_pulse,
    input  logic        filter_sync,
    input  logic        ext_event,
    input  logic        arm,
    input  logic        rd_en,
    input  logic        bit_en,
    output logic [1:0]  ser_data,
    output logic [1:0]  ser_frame,
    output logic        dco,
    input  logic [3:0]  gpio_i,
    output logic [3:0]  gpio_o,
    output logic [3:0]  gpio_oe,

    // Project-facing parallel output is offset binary, like the SAR input.
    // The serial payload stays in the AD4080 DSP's two's-complement format.
    output logic [19:0] dout,
    output logic        dout_valid,
    output logic [7:0]  status,
    output logic        correction_clip_low,
    output logic        correction_clip_high
);
    wire dsp_rst_n = rst_n && !sar_epoch_clear && !dsp_restart;
    wire input_fault = sar_dout_valid && (|sar_flags);
    wire stream_active = dsp_rst_n && !stream_fault && !input_fault && !sar_analog_ovf;
    wire [19:0] signed_input = {~sar_dout[19], sar_dout[18:0]};
    wire [19:0] signed_output;
    wire inner_valid;
    wire [1:0] inner_ser_data, inner_ser_frame;
    wire inner_dco;
    wire inner_clip_low, inner_clip_high;

    // Any flagged retirement halts acceptance until a full DSP restart. This
    // prevents an error-held SAR word from silently contaminating a filter.
    always_ff @(posedge clk or negedge dsp_rst_n) begin
        if (!dsp_rst_n) stream_fault <= 1'b0;
        else if (input_fault || sar_analog_ovf) stream_fault <= 1'b1;
    end

    assign sample_accepted = stream_active && sar_dout_valid;
    assign accepted_sample_id = sar_sample_id;
    assign sample_rejected = dsp_rst_n && sar_dout_valid &&
                             (stream_fault || input_fault || sar_analog_ovf);
    assign rejected_sample_id = sar_sample_id;
    assign rejected_flags = sar_flags;
    assign rejected_after_fault = sample_rejected && stream_fault;

    ad4080_top #(.IN_W(20), .FIFO_DEPTH(FIFO_DEPTH)) u_dsp (
        .clk(clk), .rst_n(dsp_rst_n),
        .cs_n(cs_n), .sclk(sclk), .sdi(sdi), .sdo(sdo), .sdo_oe(sdo_oe),
        .cnv_p(cnv_p), .cnv_n(cnv_n), .cnv_pulse(cnv_pulse),
        .valid_in(sample_accepted), .x(signed_input),
        .filter_sync(filter_sync), .ext_event(ext_event), .arm(arm),
        .rd_en(rd_en && stream_active), .bit_en(bit_en),
        .ser_data(inner_ser_data), .ser_frame(inner_ser_frame), .dco(inner_dco),
        .gpio_i(gpio_i), .gpio_o(gpio_o), .gpio_oe(gpio_oe),
        .dout(signed_output), .dout_valid(inner_valid), .status(status),
        .correction_clip_low(inner_clip_low), .correction_clip_high(inner_clip_high)
    );

    assign dout = {~signed_output[19], signed_output[18:0]};
    // The fault also suppresses older in-flight results; restart clears all
    // filter/FIFO state rather than resuming across a missing sample.
    assign dout_valid = inner_valid && stream_active;
    assign ser_data = stream_active ? inner_ser_data : 2'b00;
    assign ser_frame = stream_active ? inner_ser_frame : 2'b00;
    assign dco = stream_active && inner_dco;
    assign correction_clip_low = inner_clip_low && stream_active;
    assign correction_clip_high = inner_clip_high && stream_active;
endmodule

`default_nettype wire
