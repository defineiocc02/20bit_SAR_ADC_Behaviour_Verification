//===========================================================================
// ad4080_dsp_chain.sv -- AD4080 DSP chain reference
//===========================================================================
// 数据通路（可选）：
//   ADC result x
//     -> sys_error_corr (y = m*x + c, 12-bit offset / 10-bit gain)
//     -> filter select (bypass / SINC1 / SINC5)
//     -> optional 16K result FIFO
//     -> y / valid_out
//
// FILTER_SEL: 0 = bypass, 1 = SINC1, 2 = SINC5
// USE_FIFO  : 0 = direct, 1 = through result_fifo
//
// 说明：
//   - 本 chain 是**参考集成**，不含 SPI/LVDS/事件触发/时钟域切换。
//   - SINC5+comp 未实现；需要数据手册图 105-107 的补偿系数。
//   - 所有模块均为 20-bit 无符号结果码；offset/gain 由外部寄存器提供。
//===========================================================================
`timescale 1ns/1ps

module ad4080_dsp_chain #(
    parameter int  IN_W       = 20,
    parameter int  DECIM      = 4,
    parameter bit  USE_FIFO   = 1'b0
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    valid_in,
    input  logic [IN_W-1:0]         x,
    input  logic signed [11:0]      offset_code,
    input  logic [9:0]              gain_code,
    input  logic [1:0]              filter_sel,   // 0 bypass, 1 SINC1, 2 SINC5
    input  logic                    filter_sync,
    input  logic                    rd_en,
    output logic [IN_W-1:0]         y,
    output logic                    valid_out,
    output logic                    clip_low,
    output logic                    clip_high,
    output logic                    fifo_full,
    output logic                    fifo_empty,
    output logic                    fifo_overflow,
    output logic                    fifo_underflow
);

    logic [IN_W-1:0] corr_data;
    logic            corr_valid;

    sys_error_corr #(.IN_W(IN_W)) u_corr (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in),
        .x(x), .offset_code(offset_code), .gain_code(gain_code),
        .y(corr_data), .valid_out(corr_valid),
        .clip_low(clip_low), .clip_high(clip_high)
    );

    logic [IN_W-1:0] filt_data;
    logic            filt_valid;
    logic [IN_W-1:0] s1_data, s5_data;
    logic            s1_valid, s5_valid;

    sinc1_decim #(.IN_W(IN_W), .DECIM(DECIM)) u_sinc1 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corr_valid), .data_in(corr_data),
        .data_out(s1_data), .valid_out(s1_valid)
    );

    sinc5_decim #(.IN_W(IN_W), .DECIM(DECIM)) u_sinc5 (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(corr_valid), .data_in(corr_data),
        .data_out(s5_data), .valid_out(s5_valid)
    );

    always_comb begin
        case (filter_sel)
            2'd1: begin filt_data = s1_data; filt_valid = s1_valid; end
            2'd2: begin filt_data = s5_data; filt_valid = s5_valid; end
            default: begin filt_data = corr_data; filt_valid = corr_valid; end
        endcase
    end

    generate
        if (USE_FIFO) begin : g_fifo
            logic [IN_W-1:0] fifo_rd_data;
            logic            fifo_rd_valid;
            result_fifo #(.DATA_W(IN_W), .DEPTH(16384)) u_fifo (
                .clk(clk), .rst_n(rst_n),
                .wr_en(filt_valid), .wr_data(filt_data),
                .rd_en(rd_en), .rd_data(fifo_rd_data), .rd_valid(fifo_rd_valid),
                .full(fifo_full), .empty(fifo_empty),
                .almost_full(), .almost_empty(),
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
