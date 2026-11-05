//=============================================================================
// @file    result_fifo_fv.sv
// @brief   result_fifo 的小深度形式属性入口，避免展开 16K 存储。
// @details 仅提供属性环境实例；源码存在不代表形式工具已执行或属性已证明。
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
