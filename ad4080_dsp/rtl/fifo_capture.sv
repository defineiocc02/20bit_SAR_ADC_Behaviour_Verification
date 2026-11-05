//=============================================================================
// @file    fifo_capture.sv
// @brief   四种 FIFO 捕获模式与捕获后读取控制。
// @details mode00 禁用并清捕获控制；01 立即捕获，10 事件后捕获，11 环形写入
//          并在事件后计数。完成捕获时置 full_flag，首读清除；模式 11 回写事件位置。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；arm、event、rd_en、valid 和配置须同步。
// 复位: rst_n 异步低有效；控制优先级依次为 mode00、arm、正常写读。
// 延迟: 捕获按有效样本计数；寄存读口遵循 result_fifo 接口规则。
// 吞吐: 每个 clk 最多写入一个样本、读出一个结果。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: result_fifo。
// 边界: 完整模式约定面向 DEPTH=16384；wr_total 按 16384 回绕，其他深度未完成全模式验证。
//     mode00/arm 只清控制，不清存储 FIFO；未读数据重新 arm 的会话边界由系统约束。
//     arm 同沿可能仍由旧 armed 组合产生写入，运行中 arm 不等于原子冲洗。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module fifo_capture #(
    parameter int DATA_W = 20,      // DATA_W: 样本位宽，默认 20 bit。
    parameter int DEPTH  = 16384    // DEPTH: FIFO 字数，须为二次幂；全模式约定面向 16384。
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
    // mode11 的水位按 4 的倍数向下取整，最小为 4 个样本。
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

            // mode00 关闭并复位捕获控制；再写目标模式可重新 arm（手册 p.57）。
            // 这里只清控制，未清除 result_fifo 的存储和指针。
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
        // 仅用门控后的 rd_fire 弹出数据，captured=1 前禁止真实读取。否则未完成
        // 捕获会提前读空，使 rd_cnt 与 FIFO 指针错位，并误置 underflow。
        .rd_en(rd_fire), .rd_data(rd_data), .rd_valid(rd_valid),
        .full(full), .empty(empty),
        .almost_full(almost_full_unused), .almost_empty(almost_empty_unused),
        .overflow(overflow), .underflow(underflow)
    );

    assign read_done = done_r;

endmodule

`default_nettype wire
