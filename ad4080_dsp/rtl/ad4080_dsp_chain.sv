//=============================================================================
// @file    ad4080_dsp_chain.sv
// @brief   数字处理链：校正 -> 滤波选择 -> 可选 FIFO
// @details 三条滤波器并联但只有被选中的那条收到 valid_in；
//          FILTER_SEL 00 直通 / 01 SINC1 / 10 SINC5 / 11 SINC5+补偿。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.64-66 Table 28/29/62
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : 校正 2 clk + 滤波器延迟（见各滤波器文件头）
// 吞吐     : 1 sample/clk
// 可综合性 : 是；三条滤波器常驻，面积可优化（见 synthesis_review.md）
// 依赖     : sys_error_corr, sinc1_decim, sinc5_decim, sinc5_comp, result_fifo
// 修订记录 :
//   1.0.0 2026-10-19 初版
//   2.0.0 2026-10-19 运行时 dec_sel；未选中滤波器不翻转
//   3.0.0 2026-10-19 增加 settled 选择输出
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据通路：
//     x -> sys_error_corr (y = m*x + c)
//       -> FILTER_SEL 选择（00 直通 / 01 SINC1 / 10 SINC5 / 11 SINC5+补偿）
//       -> 可选 result_fifo（USE_FIFO=1 时）
//       -> y / valid_out
//
//     - 0x29 FILTER_SEL：[1:0] 00 关闭 / 01 SINC1 / 10 SINC5 / 11 SINC5+补偿
//     - 0x29 SINC_DEC_RATE：[6:3] 运行时可改，N = 2^(code+1)
//     - FILTER_SEL 改变后必须用 FILTER_SYNC 复位滤波器相位（p.94 原文）
//
//   实现说明：
//     1. 三条滤波器并联例化，但只有被选中的那条收到 valid_in（其余不翻转），
//        这是"面积换简单"的折中；综合报告里登记为可优化项（见 docs/synthesis_review.md）。
//     2. USE_FIFO 默认 0：FIFO 由顶层 fifo_capture 统一管理（触发模式在那一层）。
//     3. 所有数据均为 20-bit 无符号结果码；符号处理只在校正模块内部。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module ad4080_dsp_chain #(
    parameter int  IN_W     = 20,
    parameter bit  USE_FIFO = 1'b0
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    valid_in,
    input  logic [IN_W-1:0]         x,
    input  logic signed [11:0]      offset_code,
    input  logic [9:0]              gain_code,
    input  logic [1:0]              filter_sel,   // 00 bypass, 01 SINC1, 10 SINC5, 11 SINC5+comp
    input  logic [3:0]              dec_sel,      // SINC_DEC_RATE
    input  logic                    filter_sync,
    input  logic                    rd_en,
    output logic [IN_W-1:0]         y,
    output logic                    valid_out,
    output logic                    settled,     // 所选滤波器建立完成（bypass 恒 1）
    output logic                    clip_low,
    output logic                    clip_high,
    output logic                    fifo_full,
    output logic                    fifo_empty,
    output logic                    fifo_overflow,
    output logic                    fifo_underflow        // FIFO 下溢（直通模式下恒 0）
);

    logic [IN_W-1:0] corr_data;
    logic            corr_valid;

    sys_error_corr #(.IN_W(IN_W)) u_corr (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in),
        .x(x), .offset_code(offset_code), .gain_code(gain_code),
        .y(corr_data), .valid_out(corr_valid),
        .clip_low(clip_low), .clip_high(clip_high)
    );

    logic [IN_W-1:0] s1_data, s5_data, s3_data;
    logic            s1_valid, s5_valid, s3_valid;
    logic            s1_settled, s5_settled, s3_settled;

    // 只有被选中的滤波器接收样本，其余保持静止（省动态功耗，也避免
    // 未选中通道在后台累积出无意义的状态）。
    wire v1 = corr_valid && (filter_sel == 2'd1);
    wire v2 = corr_valid && (filter_sel == 2'd2);
    wire v3 = corr_valid && (filter_sel == 2'd3);

    sinc1_decim #(.IN_W(IN_W)) u_sinc1 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(v1), .data_in(corr_data), .dec_sel(dec_sel),
        .data_out(s1_data), .valid_out(s1_valid), .settled(s1_settled)
    );

    sinc5_decim #(.IN_W(IN_W)) u_sinc5 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(v2), .data_in(corr_data), .dec_sel(dec_sel),
        .data_out(s5_data), .valid_out(s5_valid), .settled(s5_settled)
    );

    sinc5_comp #(.IN_W(IN_W)) u_sinc5c (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(v3), .data_in(corr_data), .dec_sel(dec_sel),
        .data_out(s3_data), .valid_out(s3_valid), .settled(s3_settled)
    );

    logic [IN_W-1:0] filt_data;
    logic            filt_valid;

    always_comb begin
        case (filter_sel)
            2'd1: begin filt_data = s1_data; filt_valid = s1_valid; settled = s1_settled; end
            2'd2: begin filt_data = s5_data; filt_valid = s5_valid; settled = s5_settled; end
            2'd3: begin filt_data = s3_data; filt_valid = s3_valid; settled = s3_settled; end
            default: begin filt_data = corr_data; filt_valid = corr_valid; settled = 1'b1; end
        endcase
    end

    generate
        if (USE_FIFO) begin : g_fifo
            logic [IN_W-1:0] fifo_rd_data;
            logic            fifo_rd_valid;
            logic            af_unused, ae_unused;
            result_fifo #(.DATA_W(IN_W), .DEPTH(16384)) u_fifo (
                .clk(clk), .rst_n(rst_n),
                .wr_en(filt_valid), .overwrite_en(1'b0), .wr_data(filt_data),
                .rd_en(rd_en), .rd_data(fifo_rd_data), .rd_valid(fifo_rd_valid),
                .full(fifo_full), .empty(fifo_empty),
                .almost_full(af_unused), .almost_empty(ae_unused),
                .overflow(fifo_overflow), .underflow(fifo_underflow)
            );
            assign y         = fifo_rd_data;
            assign valid_out = fifo_rd_valid;
        end else begin : g_direct
            assign y              = filt_data;
            assign valid_out      = filt_valid;
            assign fifo_full      = 1'b0;
            assign fifo_empty     = 1'b1;
            assign fifo_overflow  = 1'b0;
            assign fifo_underflow = 1'b0;
        end
    endgenerate

endmodule

`default_nettype wire
