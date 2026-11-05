//=============================================================================
// @file    sar20_ad4080_top.sv
// @brief   SAR 结果到 DSP 的码制、故障与复位边界。
// @details 入口和并行出口翻转最高位，在偏移二进制码与补码间转换；串行载荷保持补码。
//          任一输入 flags 或模拟粘滞异常停止接收和输出，直至整个 DSP 重启。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 无；本项目接口规则。
// 时钟域: clk；SAR 数据、标志与控制均同步，不含 CDC。
// 复位: 外部 rst_n 或 sar_epoch_clear/dsp_restart 异步断言 DSP 复位，须同步释放。
// 延迟: sample_accepted/rejected 是入口组合事件，ID 仅在对应事件有效时解释。
// 吞吐: 输入无 ready/反压，下游速率依赖滤波、FIFO 与串行配置。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: ad4080_top。
// 边界: DSP 重启清配置、滤波和 FIFO 控制状态，不清 SAR 模拟粘滞故障或初始化 RAM。
//     输出不携带滤波窗口或结果 ID，不能将入口 accepted ID 当作滤波/FIFO 输出标签。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sar20_ad4080_top #(
    parameter int FIFO_DEPTH = 16384
) (
    input  logic        clk,
    input  logic        rst_n,
    // 两个脉冲均复位 DSP 配置、滤波和 FIFO 控制状态；RAM 内容本身不初始化。
    input  logic        sar_epoch_clear,
    input  logic        dsp_restart,

    // SAR 退役接口的 flags 顺序为 {clip_high, clip_low, adc2_ovf, gain_err, acc_ovf}，
    // 五个标志均按故障处理。
    input  logic [19:0] sar_dout,
    input  logic        sar_dout_valid,
    input  logic [31:0] sar_sample_id,
    input  logic [4:0]  sar_flags,
    // SAR analog_ovf 是独立粘滞状态，不在 sar_flags 内。
    input  logic        sar_analog_ovf,

    // ID 只限定入口接受/拒绝事件，不附加到滤波或 FIFO 输出。
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

    // 并行输出与 SAR 输入均采用偏移二进制码，串行载荷仍采用 DSP 的补码格式。
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

    // 带标志的退役结果停止后续接收，直至整个 DSP 重启，避免 SAR 错误保持码进入滤波器。
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
    // 故障同时抑制更早的在途结果；重启清除滤波/FIFO 状态，不跨缺失样本自动续流。
    assign dout_valid = inner_valid && stream_active;
    assign ser_data = stream_active ? inner_ser_data : 2'b00;
    assign ser_frame = stream_active ? inner_ser_frame : 2'b00;
    assign dco = stream_active && inner_dco;
    assign correction_clip_low = inner_clip_low && stream_active;
    assign correction_clip_high = inner_clip_high && stream_active;
endmodule

`default_nettype wire
