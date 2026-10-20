//=============================================================================
// @file    result_fifo_fv.sv
// @brief   result_fifo 的形式验证顶层（小深度实例，避免 16K 存储展开）
//=============================================================================
`timescale 1ns/1ps
module result_fifo_fv (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       wr_en,
    input  logic       rd_en,
    input  logic       overwrite_en,
    input  logic [3:0] wr_data
);
    logic [3:0] rd_data;
    logic       rd_valid, full, empty, almost_full, almost_empty, overflow, underflow;

    result_fifo #(.DATA_W(4), .DEPTH(8)) dut (
        .clk(clk), .rst_n(rst_n),
        .wr_en(wr_en), .overwrite_en(overwrite_en), .wr_data(wr_data),
        .rd_en(rd_en), .rd_data(rd_data), .rd_valid(rd_valid),
        .full(full), .empty(empty),
        .almost_full(almost_full), .almost_empty(almost_empty),
        .overflow(overflow), .underflow(underflow)
    );
endmodule
