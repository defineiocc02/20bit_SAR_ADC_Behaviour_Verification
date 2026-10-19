//===========================================================================
// fifo_capture.sv -- AD4080 result FIFO capture controller (subset)
//===========================================================================
// 数据手册依据：AD4080 Rev.B p.57-63
//   FIFO_MODE:
//     00 = disabled
//     01 = immediate trigger: capture WATERMARK results after arm
//     10 = event trigger, read latest WATERMARK: wait event, capture WATERMARK
//     11 = event trigger, read all FIFO: circular overwrite (NOT implemented)
//
//  本模块实现 00/01/10；11 按 10 处理并在 README 标注未实现。
//  底层使用 result_fifo（16K x 20-bit，满时拒绝写、不覆盖）。
//  真正的 mode 11 需要 circular overwrite FIFO，不能用本 FIFO 冒充。
//===========================================================================
`timescale 1ns/1ps

module fifo_capture #(
    parameter int DATA_W = 20,
    parameter int DEPTH  = 16384
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              arm,
    input  logic              event,
    input  logic              rd_en,
    input  logic [1:0]        mode,
    input  logic [14:0]       watermark,
    input  logic              valid_in,
    input  logic [DATA_W-1:0] data_in,
    output logic [DATA_W-1:0] rd_data,
    output logic              rd_valid,
    output logic              full,
    output logic              empty,
    output logic              overflow,
    output logic              underflow,
    output logic              read_done,
    output logic              captured
);

    localparam int CNT_W = 15;

    logic [CNT_W-1:0] wr_cnt, rd_cnt;
    logic             armed, event_seen, done_r;
    logic             wr_en;
    logic [CNT_W-1:0] wm_eff;

    assign wm_eff = (watermark == 15'd0) ? 15'd1 : watermark;

    // arm / event / capture state
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            armed      <= 1'b0;
            event_seen <= 1'b0;
            wr_cnt     <= '0;
            rd_cnt     <= '0;
            done_r     <= 1'b0;
            captured   <= 1'b0;
        end else begin
            if (arm) begin
                armed      <= 1'b1;
                event_seen <= 1'b0;
                wr_cnt     <= '0;
                rd_cnt     <= '0;
                done_r     <= 1'b0;
                captured   <= 1'b0;
            end else begin
                if (event) event_seen <= 1'b1;

                if (wr_en) begin
                    if (wr_cnt == wm_eff - 1'b1) begin
                        wr_cnt   <= wr_cnt + 1'b1;
                        captured <= 1'b1;
                        armed    <= 1'b0;   // stop after WATERMARK results
                    end else begin
                        wr_cnt <= wr_cnt + 1'b1;
                    end
                end

                if (rd_en && !empty) begin
                    if (rd_cnt == wm_eff - 1'b1) begin
                        rd_cnt  <= rd_cnt + 1'b1;
                        done_r  <= 1'b1;
                    end else begin
                        rd_cnt <= rd_cnt + 1'b1;
                    end
                end
            end
        end
    end

    // capture enable by mode
    logic cap_en;
    always_comb begin
        case (mode)
            2'b00:   cap_en = 1'b0;
            2'b01:   cap_en = armed;
            2'b10:   cap_en = armed && event_seen;
            2'b11:   cap_en = armed && event_seen; // subset: same as 10
            default: cap_en = 1'b0;
        endcase
    end

    assign wr_en = cap_en && valid_in && (wr_cnt < wm_eff);

    result_fifo #(.DATA_W(DATA_W), .DEPTH(DEPTH)) u_fifo (
        .clk(clk), .rst_n(rst_n),
        .wr_en(wr_en), .wr_data(data_in),
        .rd_en(rd_en), .rd_data(rd_data), .rd_valid(rd_valid),
        .full(full), .empty(empty),
        .almost_full(), .almost_empty(),
        .overflow(overflow), .underflow(underflow)
    );

    assign read_done = done_r;

endmodule
