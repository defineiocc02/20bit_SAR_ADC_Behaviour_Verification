//=============================================================================
// @file    ad4080_dsp_chain.sv
// @brief   系统校正、可选抽取滤波与结果 FIFO 的组合通路。
// @details 数据采用 IN_W 位补码，校正后选择直通、SINC1、SINC5 或 SINC5+FIR。
//          仅选中分支接收新 valid；filter_sync 取消滤波在途状态，切换并非无损事务。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；配置与数据同域，不含跨域握手。
// 复位: rst_n 异步低有效；filter_sync 优先于滤波器 valid。
// 延迟: 直通路径经过校正级的两个完整 clk 间隔；滤波/FIFO 延迟见各模块接口规则。
// 吞吐: 校正最多一个样本/clk；输出受所选抽取率限制，FIFO 读出由 rd_en 决定。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: sys_error_corr、sinc1_decim、sinc5_decim、sinc5_comp、result_fifo。
// 边界: USE_FIFO 默认关闭；本工程使用 IN_W=20，其他位宽须与子模块一并验证。
//     clip 是校正级事件；settled 不随 FIFO 读出延迟，不能作为逐结果标签。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module ad4080_dsp_chain #(
    parameter int  IN_W     = 20,  // IN_W: 补码样本位宽，工程配置为 20 bit。
    parameter bit  USE_FIFO = 1'b0  // USE_FIFO: 为 1 时在所选滤波器后启用独立结果 FIFO。
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

    // 仅选中滤波器接受新样本，未选分支仍可能完成已有输出流水。选择切换前
    // 须由 filter_sync 取消旧状态；该切换不保证无损。
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
