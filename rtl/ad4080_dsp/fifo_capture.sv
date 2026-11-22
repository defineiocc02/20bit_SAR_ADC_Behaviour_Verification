//=============================================================================
// @file    fifo_capture.sv
// @brief   保留已完成记录的捕获会话与全 RAM 快照控制。
// @details clear_capture 明确取消会话，每次合法 arm 锁存模式和水位。模式 11
//          在首次回绕前后都读取全部 RAM 地址；尚未写入的历史内容保持未定义。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.56–63/91、Table 27；未披露复位交互采用 DSP_G1_ACTIVE_2。
// 时钟域: clk；arm、事件、读取、样本和配置均同步。
// 复位: rst_n 清会话；显式 FIFO_MODE=00 清逻辑状态，软件配置复位不清。
// 延迟: RAM 读口寄存；与事件同沿接受的样本计入事件前历史。
// 吞吐: 每个 clk 最多一次捕获写入或一次已完成记录读取。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: result_fifo。
// 边界: 拒绝重新 arm 时保留原会话的合法样本和读取；不建模 N+3 的物理 CNV 时序。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
module fifo_capture #(
    parameter int DATA_W=20,  // DATA_W: 只支持 20 bit 结果。
    parameter int DEPTH=16384  // DEPTH: 存储字数，须为 4..16384 范围内的二次幂。
) (
    input logic clk, rst_n, clear_capture,
    input logic arm, event_in, rd_en,
    input logic [1:0] mode,
    input logic [14:0] watermark,
    input logic valid_in,
    input logic [DATA_W-1:0] data_in,
    output logic [DATA_W-1:0] rd_data,
    output logic rd_valid, full, empty, overflow, underflow,
    output logic read_done, captured, full_flag,
    output logic wm_wb_valid,
    output logic [14:0] wm_wb_data,
    output logic capture_active, capture_incomplete,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason
);
    localparam int MEM_AW=$clog2(DEPTH);
    localparam int CNT_W=MEM_AW+1;
    logic armed, event_seen;
    logic [1:0] mode_r;
    logic [14:0] watermark_r;
    logic [CNT_W-1:0] written_count, post_count, read_count;
    logic [MEM_AW-1:0] next_write_addr, event_addr, snapshot_start;
    logic almost_full_unused, almost_empty_unused, ram_access_reject;
    wire watermark_valid=(watermark>=1 && watermark<=15'(DEPTH)) &&
                         (mode!=2'd3 || watermark[1:0]==0);
    wire arm_ok=arm && !clear_capture && !capture_active && mode!=0 && watermark_valid;
    wire overwrite=mode_r==2'd3;
    wire capture_enabled=armed && (mode_r!=2'd2 || event_seen);
    wire write_fire=capture_enabled && valid_in && !clear_capture;
    wire request_read=rd_en && captured && !read_done && !clear_capture;
    wire queue_read=request_read && !overwrite && !empty;
    wire snapshot_read=request_read && overwrite;
    wire [MEM_AW-1:0] snapshot_addr=snapshot_start+read_count[MEM_AW-1:0];
    wire [CNT_W-1:0] read_target=overwrite ? CNT_W'(DEPTH) : CNT_W'(watermark_r);
    wire fifo_clear=clear_capture || arm_ok;
    initial begin
        if (DATA_W!=20 || DEPTH<4 || DEPTH>16384 || (DEPTH&(DEPTH-1))!=0)
            $fatal(1,"fifo_capture: signed20 and power-of-two depth in [4,16384] required");
    end
    assign capture_incomplete=armed;
    assign capture_active=armed || (captured && !read_done);
    result_fifo #(.DATA_W(DATA_W),.DEPTH(DEPTH),
                  .P_AF_MARGIN((DEPTH>16)?16:1),.P_AE_LEVEL((DEPTH>16)?16:1)) u_fifo (
        .clk(clk),.rst_n(rst_n),.clear(fifo_clear),
        .wr_en(write_fire),.overwrite_en(overwrite),.wr_data(data_in),
        .rd_en(queue_read),.snapshot_rd_en(snapshot_read),.snapshot_addr(snapshot_addr),
        .rd_data(rd_data),.rd_valid(rd_valid),.full(full),.empty(empty),
        .almost_full(almost_full_unused),.almost_empty(almost_empty_unused),
        .overflow(overflow),.underflow(underflow),.access_reject(ram_access_reject)
    );
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            armed<=1'b0; event_seen<=1'b0; mode_r<=2'd0; watermark_r<=15'd1;
            written_count<='0; post_count<='0; read_count<='0;
            next_write_addr<='0; event_addr<='0; snapshot_start<='0;
            read_done<=1'b0; captured<=1'b0; full_flag<=1'b0;
            wm_wb_valid<=1'b0; wm_wb_data<='0;
            cfg_reject<=1'b0; cfg_reject_reason<='0;
        end else begin
            wm_wb_valid<=1'b0; cfg_reject<=1'b0; cfg_reject_reason<='0;
            if (clear_capture) begin
                armed<=1'b0; event_seen<=1'b0; mode_r<=2'd0;
                written_count<='0; post_count<='0; read_count<='0;
                next_write_addr<='0; event_addr<='0; snapshot_start<='0;
                read_done<=1'b0; captured<=1'b0; full_flag<=1'b0;
            end else if (arm_ok) begin
                armed<=1'b1; event_seen<=1'b0; mode_r<=mode; watermark_r<=watermark;
                written_count<='0; post_count<='0; read_count<='0;
                next_write_addr<='0; event_addr<='0; snapshot_start<='0;
                read_done<=1'b0; captured<=1'b0; full_flag<=1'b0;
            end else begin
                // 拒绝控制请求时仍处理原会话的合法样本或读取，避免重新 arm 丢失已接受数据。
                if (arm) begin
                    cfg_reject<=1'b1;
                    cfg_reject_reason<=capture_active ? 4'd8 : 4'd9;
                end
                if (event_in && armed && !event_seen) begin
                    event_seen<=1'b1;
                    // 与事件同沿的写入属于最后一个事件前样本，事件地址指向其后位置。
                    event_addr<=next_write_addr+(write_fire ? MEM_AW'(1) : MEM_AW'(0));
                end
                if (write_fire) begin
                    next_write_addr<=next_write_addr+MEM_AW'(1);
                    if (overwrite) begin
                        if (event_seen) begin
                            if (post_count==CNT_W'(watermark_r)-CNT_W'(1)) begin
                                armed<=1'b0; captured<=1'b1; full_flag<=1'b1;
                                snapshot_start<=next_write_addr+MEM_AW'(1);
                                wm_wb_valid<=1'b1; wm_wb_data<=15'(event_addr);
                            end else post_count<=post_count+CNT_W'(1);
                        end
                    end else begin
                        if (written_count==CNT_W'(watermark_r)-CNT_W'(1)) begin
                            armed<=1'b0; captured<=1'b1; full_flag<=1'b1;
                        end else written_count<=written_count+CNT_W'(1);
                    end
                end
                if (queue_read || snapshot_read) begin
                    full_flag<=1'b0;
                    if (read_count==read_target-CNT_W'(1)) read_done<=1'b1;
                    else read_count<=read_count+CNT_W'(1);
                end
                if (ram_access_reject) begin cfg_reject<=1'b1; cfg_reject_reason<=4'd9; end
            end
        end
    end
endmodule
`default_nettype wire
