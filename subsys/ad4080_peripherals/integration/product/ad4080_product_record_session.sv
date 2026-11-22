`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_product_record_session.sv
// 模块名     : ad4080_product_record_session
// 版本       : v1.0
// 功能       : 将捕获控制器已完成的记录授权给 I/O，报告退场并保持同笔字数。
// 设计说明   : 地址和指针由原 DSP 持有。此处只冻结长度，使用单比特请求/结束
//              返回。已消费部分记录遇故障则让原 clear 路径作废，避免隐式拼帧。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_product_record_session (
    // --- 时钟与许可 ---
    input  logic        clk_core_i,             // FIFO 状态机与读受理域
    input  logic        clk_io_i,               // 记录组帧域
    input  logic        rst_core_ni,            // 保留记录使用核心硬复位
    input  logic        io_reset_ni,            // I/O 侧失去复位即撤销在途记录
    input  logic        io_allow_i,             // 异步撤销，两个域分别释放

    // --- 已完成的源记录 ---
    input  logic        command_valid_core_i,  // 已在核心域分发的读整记录请求
    output logic        command_ready_core_o,  // 可检查记录资格
    input  logic        can_lock_core_i,       // 其它控制面与读链都已静默
    input  logic        captured_core_i,       // 原 FIFO 有完整的可读记录
    input  logic [14:0] captured_words_core_i, // 原记录剩余字数，冻结首笔
    input  logic        read_taken_core_i,     // 真实单口接受一个记录地址
    output logic        locked_core_o,         // 读取期间禁止配置改当前记录
    output logic        reject_core_o,         // 无完整记录，命令未进入 I/O
    output logic        done_core_o,           // 末尾已实际交付
    output logic        failed_core_o,         // I/O 异常或掉电退场
    output logic        discard_core_o,        // 发生过真实读的失败记录需 clear

    // --- I/O 域组帧器 ---
    output logic        start_valid_io_o,      // 命令消费之后不重复启动
    input  logic        start_ready_io_i,      // 组帧器接受检查的时钟沿
    output logic [14:0] count_io_o,            // 仅在 start 有效时采纳
    input  logic        done_io_i,             // 完整记录的末沿已经发送
    input  logic        error_io_i             // 格式拒绝或记录被截断
);
    // =========================================================================
    // 1. 请求、归属及覆盖关系
    // =========================================================================
    logic io_live_core, io_live_io;
    (* ASYNC_REG = "TRUE" *) logic [1:0] request_io_q, returned_core_q;
    logic [14:0] length_q;             // 核心保持至完成往返，无逐位同步
    logic consumed_q;                 // 截断后区分完整可再读与部分消耗
    logic launched_io_q, terminal_io_q, failed_io_q;

    ad4080_pwr_reset_sync #(.HOLD_COUNT(3)) u_live_core (
        .clk_i(clk_core_i), .hold_ni({rst_core_ni, io_reset_ni, io_allow_i}), .ready_no(io_live_core)
    );
    ad4080_pwr_reset_sync #(.HOLD_COUNT(3)) u_live_io (
        .clk_i(clk_io_i), .hold_ni({rst_core_ni, io_reset_ni, io_allow_i}), .ready_no(io_live_io)
    );

    assign command_ready_core_o = io_live_core && can_lock_core_i && !locked_core_o && !returned_core_q[1];
    assign start_valid_io_o = io_live_io && request_io_q[1] && !launched_io_q;
    assign count_io_o = length_q;

    // [返回] 终态在 I/O 域保持直到核心撤回请求。失败位和长度使用稳定
    // 捆绑数据，真实实现须对两组捆绑路径给物理延迟和时钟域约束。
    always_ff @(posedge clk_io_i or negedge io_live_io) begin
        if (!io_live_io) begin
            request_io_q <= '0;
            launched_io_q <= 1'b0;
            terminal_io_q <= 1'b0;
            failed_io_q <= 1'b0;
        end else begin
            request_io_q <= {request_io_q[0], locked_core_o};
            if (!request_io_q[1]) begin
                launched_io_q <= 1'b0;
                terminal_io_q <= 1'b0;
            end else if (start_valid_io_o && start_ready_io_i) begin
                launched_io_q <= 1'b1;
            end else if (launched_io_q && (done_io_i || error_io_i)) begin
                terminal_io_q <= 1'b1;
                failed_io_q <= error_io_i;
            end
        end
    end

    // [退场] 撤销许可优先于同时抵达的成功。读过部分时才作废原记录，
    // 格式在首头前拒绝的记录仍可由下一已绑定请求发出。
    always_ff @(posedge clk_core_i or negedge rst_core_ni) begin
        if (!rst_core_ni) begin
            returned_core_q <= '0;
            locked_core_o <= 1'b0;
            length_q <= '0;
            consumed_q <= 1'b0;
            done_core_o <= 1'b0;
            reject_core_o <= 1'b0;
            failed_core_o <= 1'b0;
            discard_core_o <= 1'b0;
        end else begin
            returned_core_q <= {returned_core_q[0], terminal_io_q};
            done_core_o <= 1'b0;
            reject_core_o <= 1'b0;
            failed_core_o <= 1'b0;
            discard_core_o <= 1'b0;
            if (locked_core_o && (!io_live_core || returned_core_q[1])) begin
                locked_core_o <= 1'b0;
                done_core_o <= io_live_core && !failed_io_q;
                failed_core_o <= !io_live_core || failed_io_q;
                discard_core_o <= (!io_live_core || failed_io_q) && (consumed_q || read_taken_core_i);
            end else if (read_taken_core_i) begin
                consumed_q <= 1'b1;
            end else if (command_valid_core_i && command_ready_core_o) begin
                reject_core_o <= !captured_core_i;
                locked_core_o <= captured_core_i;
                length_q <= captured_words_core_i;
                consumed_q <= 1'b0;
            end
        end
    end
endmodule

`default_nettype wire
