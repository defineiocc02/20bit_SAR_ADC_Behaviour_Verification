`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_fifo_lvds_record_link.sv
// 模块名     : ad4080_fifo_lvds_record_link
// 版本       : v1.0
// 功能       : 通过已有单口读回和边沿槽，把采后记录组装成单 lane LVDS 位对。
// 设计说明   :
//   * 仅一套 readback+transport 和一套 framer，结果 SRAM 在模块外。
//   * I/O 域校验命令后持有字数和一条会话请求。核心同步后按字数接纳
//     外部地址，仅本拍受理时消耗一格。回执降到零才可接受下一轮命令。
//   * 功能侧拥有地址列表、重新读策略和主机记录状态，CRC 服务提供已绑定
//     算法。若链路失败，旧搬运 epoch 被冲刷，错误脉冲由 I/O 域观察。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_fifo_lvds_record_link #(
    parameter int unsigned FIFO_ADDR_WIDTH = 3 // 仅搬运队列，非存储结果的 16K 宏
) (
    // --- 共代供电与软/硬复位边界 ---
    input  logic        clk_core_i,                     // 与功能 SRAM 同域
    input  logic        clk_io_i,                       // 逻辑 PHY pair 的装载域
    input  logic        fifo_reset_ni,                  // 壳层 fifo_reset_no
    input  logic        io_reset_ni,                    // 壳层 io_reset_no
    input  logic        io_session_allow_i,             // 壳层 io_word_ready_o，低时撤销全会话

    // --- I/O 域记录与 CRC 服务 ---
    input  logic        record_start_valid_i,          // count 和绑定状态保持至命令握手
    output logic        record_start_ready_o,          // 可以检查命令
    output logic        record_start_accept_o,         // 真正启动，可以启动本轮 CRC 服务
    input  logic [14:0] record_count_i,                // 1..16384，核心域同笔预算
    input  logic        format_bound_i,                // 实际格式来源已绑定
    input  logic        crc_service_ready_i,           // 旧 CRC 响应已经冲刷
    input  logic        abort_i,                       // I/O 侧明确废弃记录，下一会话须重新定位地址
    input  logic        crc_valid_i,                   // 与本次记录对应的线序余数
    output logic        crc_ready_o,                   // 最后载荷位对或等待尾字段
    input  logic [23:0] crc_i,                         // 允许来自其它实现，外部保证位序
    input  logic        crc_error_i,                   // 伴随有效余数的异常
    output logic        crc_cancel_o,                  // 异常后要求外部清本次计算
    output logic        record_done_o,                 // 尾末 pair 已交付，重新 start 须另看 ready
    output logic        record_error_o,                // 命令拒绝或已开始记录中止
    output logic [2:0]  record_error_code_o,           // 沿用 framer 的错误编码

    // --- 核心域地址描述符与真实单口 ---
    input  logic        read_req_valid_core_i,         // 仅命令授权及剩余预算允许受理，地址属外部记录器
    output logic        read_req_ready_core_o,         // 节奏由在途 SRAM 与异步队列容量共同限定
    input  logic [13:0] read_addr_core_i,              // 上游决定环形顺序与每条记录的范围
    output logic        sram_read_req_valid_core_o,    // 与写端及 MBIST 做真实单口仲裁
    input  logic        sram_read_req_ready_core_i,    // 来自已选读事务的 SRAM 接点
    output logic [13:0] sram_read_addr_core_o,          // 读有效时锁定地址
    input  logic        sram_read_rsp_valid_core_i,    // 手握后下一核心沿返回，专供本模块
    input  logic [19:0] sram_read_rsp_data_core_i,      // 真实物理读回完整结果
    output logic        record_idle_core_o,            // 本轮命令退场且末槽退休，供静默确认
    output logic        read_fault_core_o,             // 读协议或搬运错误，本地诊断

    // --- 整记录交给物理 PHY 的逻辑位对 ---
    input  logic        beat_ready_i,                  // 一旦输出载荷，同拍必须接收位对
    output logic        beat_valid_o,                  // 本上升沿交付 {FALL, RISE}
    output logic [1:0]  beat_pair_o,                   // [1] 下降沿，[0] 随后上升沿
    output logic        beat_first_o,                  // 同步头第一对
    output logic        beat_last_o,                   // CRC 最后一对
    output logic [1:0]  beat_kind_o                    // 0 头，1 数据，2 CRC
);
    // =========================================================================
    // 1. 记录级授权与预算返回
    // =========================================================================
    logic core_epoch_no, io_epoch_no, link_idle_core, link_online_io;
    logic request_io_q, closing_io_q, flush_io_q, seen_core_q;
    logic [14:0] count_io_q, remaining_core_q;             // 描述符字数入队后保持到完整往返
    (* ASYNC_REG = "TRUE" *) logic [1:0] request_core_q, seen_io_q, fault_io_q;
    logic can_command, framer_ready, read_credit, lower_ready;

    ad4080_pwr_reset_sync u_io_epoch (
        .clk_i(clk_io_i), .hold_ni(fifo_reset_ni && io_reset_ni && io_session_allow_i), .ready_no(io_epoch_no)
    );
    ad4080_pwr_reset_sync u_core_epoch (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_ni && io_reset_ni && io_session_allow_i), .ready_no(core_epoch_no)
    );

    assign can_command = link_online_io && !request_io_q && !closing_io_q && !seen_io_q[1];
    assign record_start_ready_o = can_command && framer_ready;
    assign read_credit = seen_core_q && request_core_q[1] && remaining_core_q != 0;
    assign read_req_ready_core_o = read_credit && lower_ready;
    assign record_idle_core_o = link_idle_core && !request_core_q[1] && !seen_core_q;

    // [冲刷] 运行故障要清掉旧队列与单拍预留。两端仍完成控制握手后才
    // 撤销冲刷，避免一个中止很快的记录授权以迟到的同步位越过新会话。
    always_ff @(posedge clk_io_i or negedge io_epoch_no) begin
        if (!io_epoch_no) begin
            request_io_q <= 1'b0;
            closing_io_q <= 1'b0;
            flush_io_q <= 1'b0;
            count_io_q <= '0;
            seen_io_q <= '0;
            fault_io_q <= '0;
        end else begin
            seen_io_q <= {seen_io_q[0], seen_core_q};
            fault_io_q <= {fault_io_q[0], read_fault_core_o};
            if (record_start_accept_o) begin
                request_io_q <= 1'b1;
                count_io_q <= record_count_i;
            end
            if (request_io_q && (record_done_o || record_error_o)) begin
                closing_io_q <= 1'b1;
                flush_io_q <= record_error_o;
            end
            if (closing_io_q && seen_io_q[1]) request_io_q <= 1'b0;
            if (closing_io_q && !request_io_q && !seen_io_q[1]) begin
                closing_io_q <= 1'b0;
                flush_io_q <= 1'b0;
            end
        end
    end

    // [CDC] 稳定字数跟随两级同步的会话位。只在第一次观察该会话时取
    // 字数，来源保持它直到核心已看到置位/撤销，目标库需约束这组数据。
    always_ff @(posedge clk_core_i or negedge core_epoch_no) begin
        if (!core_epoch_no) begin
            request_core_q <= '0;
            seen_core_q <= 1'b0;
            remaining_core_q <= '0;
        end else begin
            request_core_q <= {request_core_q[0], request_io_q};
            if (!request_core_q[1]) begin
                seen_core_q <= 1'b0;
                remaining_core_q <= '0;
            end else if (!seen_core_q) begin
                seen_core_q <= 1'b1;
                remaining_core_q <= count_io_q;
            end else if (read_req_valid_core_i && read_req_ready_core_o) begin
                remaining_core_q <= remaining_core_q - 1'b1;
            end
        end
    end

    // =========================================================================
    // 2. 唯一的读回、CDC 和 I/O 组帧绑定
    // =========================================================================
    logic raw_valid, raw_first, raw_last, raw_dual, raw_check;
    logic da_fall, da_rise, db_fall, db_rise;
    logic payload_start, protocol_error, mode_error, dropped, overflow;
    logic [1:0] unused_high_beat;
    logic unused_core_online, unused_core_full, unused_spi_ready, unused_busy;
    logic [2:0] unused_beat_width;

    // [异常] 失配读经同步触发组帧中止。已活动错误即时封 raw 链并保持到
    // 核心旧授权退场。无绑定的命令从未启动读回，无需启动这种冲刷。
    assign read_fault_core_o = protocol_error || mode_error || dropped || overflow;
    ad4080_result_readback_link #(.FIFO_ADDR_WIDTH(FIFO_ADDR_WIDTH)) u_readback (
        .clk_core_i, .clk_io_i, .fifo_reset_ni, .io_reset_ni,
        .io_session_allow_i(io_session_allow_i && !flush_io_q && !(record_error_o && request_io_q)),
        .io_start_allow_i(payload_start), .read_req_valid_core_i(read_req_valid_core_i && read_credit),
        .read_req_ready_core_o(lower_ready), .read_addr_core_i,
        .read_dual_lane_core_i(1'b0), .read_intf_check_core_i(1'b0),
        .sram_read_req_valid_core_o, .sram_read_req_ready_core_i, .sram_read_addr_core_o,
        .sram_read_rsp_valid_core_i, .sram_read_rsp_data_core_i, .link_idle_core_o(link_idle_core),
        .link_online_core_o(unused_core_online), .link_full_core_o(unused_core_full),
        .read_mode_error_core_o(mode_error), .read_protocol_error_core_o(protocol_error),
        .transport_drop_core_o(dropped), .transport_overflow_core_o(overflow),
        .io_online_o(link_online_io), .io_pair_valid_o(raw_valid), .io_first_o(raw_first), .io_last_o(raw_last),
        .io_dual_lane_o(raw_dual), .io_intf_check_o(raw_check),
        .io_da_fall_o(da_fall), .io_da_rise_o(da_rise), .io_db_fall_o(db_fall), .io_db_rise_o(db_rise)
    );
    ad4080_fifo_record_framer u_framer (
        .clk_i(clk_io_i), .rst_ni(io_epoch_no), .power_good_i(io_session_allow_i),
        .abort_i(abort_i || fault_io_q[1] || (raw_valid && (raw_dual || raw_check || db_fall || db_rise))),
        .start_valid_i(record_start_valid_i && can_command), .start_ready_o(framer_ready),
        .start_accept_o(record_start_accept_o), .mode_i(2'd0), .sample_count_i(record_count_i),
        .format_bound_i, .crc_service_ready_i,
        .payload_pair_valid_i(raw_valid), .payload_pair_i({da_fall, da_rise}),
        .payload_pair_first_i(raw_first), .payload_pair_last_i(raw_last), .payload_start_allow_o(payload_start),
        .sample_valid_i(1'b0), .sample_ready_o(unused_spi_ready), .sample_i(20'b0), .sample_last_i(1'b0),
        .crc_valid_i, .crc_ready_o, .crc_i, .crc_error_i, .crc_cancel_o,
        .beat_valid_o, .beat_ready_i, .beat_o({unused_high_beat, beat_pair_o}), .beat_width_o(unused_beat_width),
        .beat_first_o, .beat_last_o, .beat_kind_o, .busy_o(unused_busy),
        .done_o(record_done_o), .error_o(record_error_o), .error_code_o(record_error_code_o)
    );
endmodule

`default_nettype wire
