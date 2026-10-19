//=============================================================================
// @file    fifo_capture.sv
// @brief   结果 FIFO 四种触发模式的捕获控制
// @details mode 00 关闭 / 01 立即 / 10 事件后最近 WATERMARK /
//          11 环形保留全部 16K（事件后再写 WATERMARK 个）。
//          full_flag 在收满 WATERMARK 时置位、首个读出时清除（手册 Figure 86）；
//          mode 11 捕获完成时把事件位置自动写回 FIFO_WATERMARK。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 3.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.57-63（Table 27, Figure 86-92）、p.90-91（Table 55-56）
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : arm 后按模式计数；读延迟由 result_fifo 决定（1 clk）
// 吞吐     : 1 sample/clk
// 可综合性 : 是；组合比较链为关键路径候选
// 依赖     : result_fifo
// 修订记录 :
//   1.0.0 2026-10-19 初版
//   2.0.0 2026-10-19 mode 11 四倍数对齐；读侧 captured 门控；event 改名 event_in
//   3.0.0 2026-10-19 增加 full_flag（首读即清）、mode 00 清内部状态、
//                    mode 11 事件位置回写 WATERMARK
//-----------------------------------------------------------------------------
// 设计说明：
//   手册约束：
//     - WATERMARK < 1 夹到 1，> 16384 夹到 16384（已在 ad4080_regs 完成）。
//     - mode 11 的 WATERMARK 必须是 4 的倍数；非法值向下取整到 4 的倍数
//       （NOTE(choice): 不报错，只取整；手册只说"必须"，未定义非法行为）。
//     - 写 FIFO_MODE 即 arm（arm 由 ad4080_regs 的 fifo_arm 提供）。
//     - mode 11：环形写直到事件，事件后再写 WATERMARK 个；捕获完成后
//       事件位置写回 FIFO_WATERMARK（p.57）。
//     - "FIFO_WATERMARK + three conversion clocks" 是主机侧时序要求；
//       本模型中 valid_in 已是转换完成后的结果，无需实现（NOTE）。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module fifo_capture #(
    parameter int DATA_W = 20,      // 样本位宽
    parameter int DEPTH  = 16384    // FIFO 深度，必须是 2 的幂
) (
    input  logic              clk,                        // 系统时钟
    input  logic              rst_n,                      // 异步复位，低有效
    input  logic              arm,                          // arm 脉冲（寄存器写或外部）
    input  logic              event_in,                     // 事件触发输入
    input  logic              rd_en,                        // 读使能
    input  logic [1:0]        mode,                         // FIFO_MODE 00/01/10/11
    input  logic [14:0]       watermark,                    // WATERMARK，1..16384
    input  logic              valid_in,                     // 样本有效
    input  logic [DATA_W-1:0] data_in,                      // 样本（20-bit 补码）
    output logic [DATA_W-1:0] rd_data,                      // 读出数据，晚 1 拍
    output logic              rd_valid,                     // 读出有效，单拍
    output logic              full,                         // 物理 FIFO 满
    output logic              empty,                        // 物理 FIFO 空
    output logic              overflow,                     // 写溢出，粘滞
    output logic              underflow,                    // 读下溢，粘滞
    output logic              read_done,                    // 读满目标个数
    output logic              captured,                     // 捕获完成
    output logic              full_flag,                    // FIFO_FULL：收满置位、首读即清
    output logic              wm_wb_valid,                  // mode 11 回写脉冲
    output logic [14:0]       wm_wb_data                    // 回写的事件位置
);

    localparam int CNT_W = 15;

    logic [CNT_W-1:0] wr_cnt;      // 01/10：向 watermark 计数
    logic [CNT_W-1:0] post_cnt;    // 11：事件后的写入计数
    logic [CNT_W-1:0] rd_cnt;
    logic [CNT_W-1:0] wr_total;    // 11：arm 以来的总写入数（mod DEPTH）
    logic [CNT_W-1:0] evt_pos;     // 11：事件发生时的写入位置
    logic             armed, event_seen, done_r;
    logic [CNT_W-1:0] wm_eff, rd_target;
    logic             cap_en, overwrite, wr_en, rd_fire;
    logic             almost_full_unused, almost_empty_unused;

    wire [CNT_W-1:0] wm_raw   = (watermark == 15'd0) ? 15'd1 : watermark;
    // mode 11 必须 4 的倍数：向下取整，最小 4
    wire [CNT_W-1:0] wm_quad  = (wm_raw < 15'd4) ? 15'd4 : {wm_raw[CNT_W-1:2], 2'b00};

    always_comb begin
        if (mode == 2'b11) wm_eff = wm_quad;
        else               wm_eff = wm_raw;
    end

    assign overwrite = (mode == 2'b11);
    assign rd_target = overwrite ? 15'(DEPTH) : wm_eff;

    always_comb begin
        case (mode)
            2'b00:   cap_en = 1'b0;
            2'b01:   cap_en = armed;
            2'b10:   cap_en = armed && event_seen;
            2'b11:   cap_en = armed;
            default: cap_en = 1'b0;
        endcase
    end

    assign wr_en   = cap_en && valid_in && (overwrite || (wr_cnt < wm_eff));
    assign rd_fire = rd_en && !empty && (mode != 2'b00) && captured;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            armed        <= 1'b0;
            event_seen   <= 1'b0;
            wr_cnt       <= '0;
            post_cnt     <= '0;
            rd_cnt       <= '0;
            wr_total     <= '0;
            evt_pos      <= '0;
            done_r       <= 1'b0;
            captured     <= 1'b0;
            full_flag    <= 1'b0;
            wm_wb_valid  <= 1'b0;
            wm_wb_data   <= '0;
        end else begin
            wm_wb_valid <= 1'b0;

            // mode 00：FIFO 关闭，同时复位捕获状态（手册 p.57：写 00 即复位
            // 并可在写目标模式后重新 arm）
            if (mode == 2'b00) begin
                armed      <= 1'b0;
                event_seen <= 1'b0;
                wr_cnt     <= '0;
                post_cnt   <= '0;
                rd_cnt     <= '0;
                wr_total   <= '0;
                done_r     <= 1'b0;
                captured   <= 1'b0;
                full_flag  <= 1'b0;
            end else if (arm) begin
                armed      <= 1'b1;
                event_seen <= 1'b0;
                wr_cnt     <= '0;
                post_cnt   <= '0;
                rd_cnt     <= '0;
                wr_total   <= '0;
                done_r     <= 1'b0;
                captured   <= 1'b0;
                full_flag  <= 1'b0;
            end else begin
                if (event_in && !event_seen) begin
                    event_seen <= 1'b1;
                    evt_pos    <= wr_total;   // mode 11：事件位置
                end

                // ---- write side ----
                if (wr_en) begin
                    if (overwrite) begin
                        wr_total <= (wr_total == 15'd16383) ? 15'd0 : wr_total + 15'd1;
                        if (event_seen) begin
                            if (post_cnt == wm_eff - 1'b1) begin
                                captured    <= 1'b1;
                                full_flag   <= 1'b1;
                                armed       <= 1'b0;
                                wm_wb_valid <= 1'b1;          // 回写事件位置
                                wm_wb_data  <= evt_pos;
                            end else begin
                                post_cnt <= post_cnt + 1'b1;
                            end
                        end
                    end else begin
                        if (wr_cnt == wm_eff - 1'b1) begin
                            captured  <= 1'b1;
                            full_flag <= 1'b1;
                            armed     <= 1'b0;
                        end else begin
                            wr_cnt <= wr_cnt + 1'b1;
                        end
                    end
                end

                // ---- read side ----
                if (rd_fire) begin
                    full_flag <= 1'b0;                        // 首个读出即清 FIFO_FULL
                    if (rd_cnt == rd_target - 1'b1) begin
                        done_r <= 1'b1;
                    end else begin
                        rd_cnt <= rd_cnt + 1'b1;
                    end
                end
            end
        end
    end

    result_fifo #(.DATA_W(DATA_W), .DEPTH(DEPTH)) u_fifo (
        .clk(clk), .rst_n(rst_n),
        .wr_en(wr_en), .overwrite_en(overwrite), .wr_data(data_in),
        // 必须用门控后的 rd_fire：captured=1 之前不许真正弹栈，否则
        // 未完成的捕获会被提前读空，rd_cnt 与 FIFO 指针错位、underflow 误置。
        .rd_en(rd_fire), .rd_data(rd_data), .rd_valid(rd_valid),
        .full(full), .empty(empty),
        .almost_full(almost_full_unused), .almost_empty(almost_empty_unused),
        .overflow(overflow), .underflow(underflow)
    );

    assign read_done = done_r;

endmodule

`default_nettype wire
