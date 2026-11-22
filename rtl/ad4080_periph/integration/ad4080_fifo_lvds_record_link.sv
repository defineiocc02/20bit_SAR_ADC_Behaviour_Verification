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
//   * 预取额度只放行同步头。FIFO 状态事件来自真发送字的装载沿，首字与
//     头最后一对同沿，末字与上一字最后一对同沿，N=1 时两事件重合。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 为连续记录接入同步预取资格，并输出真正的首末样本发射字装载事件。
// =============================================================================
module ad4080_fifo_lvds_record_link #(
    parameter int unsigned FIFO_ADDR_WIDTH = 3, // 仅搬运队列，非存储结果的 16K 宏
    parameter bit SPI_RESULT_BOUND = 1'b0,      // 只有实接 DCS/DCLK 的产品才能启用
    parameter bit SPI_CRC_TAIL_BOUND = 1'b0     // 约定的工程尾，默认只读固件前缀
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
    output logic        crc_ready_o,                   // 最后载荷位对交付的唯一 CRC 接受沿
    input  logic [23:0] crc_i,                         // 允许来自其它实现，外部保证位序
    input  logic        crc_error_i,                   // 伴随有效余数的异常
    output logic        crc_cancel_o,                  // 异常后要求外部清本次计算
    output logic        record_done_o,                 // 尾末 pair 已交付，重新 start 须另看 ready
    output logic        record_error_o,                // 命令拒绝或已开始记录中止
    output logic [2:0]  record_error_code_o,           // 沿用 framer 的错误编码
    output logic        first_payload_word_load_io_o, // 本 IO 沿首样本进入发射字寄存器，非 SRAM 预取
    output logic        last_payload_word_load_io_o,  // 本 IO 沿末样本进入发射字寄存器，非 CRC 退休

    // --- 可选的共享结果端口 ---
    input  logic [1:0]  result_mode_io_i,              // 同域已应用的配置，0 LVDS/1 SPI1/2 SPI4
    input  logic        result_capture_full_core_i,    // 真实冻结记录，可据 FIFO_FULL 下 DCS，无另加 ready
    input  logic        result_dcs_ni,                 // 结果侧 DCS，不是寄存器配置 CS
    input  logic        result_dclk_i,                 // 模式 3，下降沿发出一个物理位组
    output logic [3:0]  result_sdo_o,                  // [3:0] A/B/C/D，单线只驱动 SDOB
    output logic [3:0]  result_sdo_oe_o,               // 四线 D 未选仍可驱动，电源撤销全隔离
    output logic        result_spi_ready_io_o,         // 诊断：载荷邮箱已备，主机只需 FIFO_FULL
    output logic        result_spi_selected_async_o,   // 第一个合法 DCLK 已发头，给核心真实读授权

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
    input  logic        beat_ready_i,                  // AA 之前可等待，开始后每个原始拍都须接收
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
    logic [14:0] words_to_load_io_q;                       // 只扣真发射装载，独立于核心 SRAM 额度
    logic [14:0] words_for_spi_cache_q;                    // 分离前置 SPI 填充与实际引脚首组交付
    logic [1:0] record_mode_q;                            // 剩余描述符与模式一起冻结
    (* ASYNC_REG = "TRUE" *) logic [1:0] request_core_q, seen_io_q, fault_io_q;
    logic can_command, framer_ready, read_credit, lower_ready;
    logic raw_word_load, actual_word_load;                 // 真实 LX 进入发射寄存器的事件
    logic record_is_spi, spi_abort, spi_tick, frame_word_start;
    logic parallel_valid, parallel_ready;
    logic [19:0] parallel_word;
    logic [7:0] sync_header;
    logic [3:0] frame_beat;
    logic frame_valid, frame_first, frame_last;
    logic [1:0] frame_kind;

    ad4080_pwr_reset_sync #(.HOLD_COUNT(3)) u_io_epoch (
        .clk_i(clk_io_i), .hold_ni({fifo_reset_ni, io_reset_ni, io_session_allow_i}), .ready_no(io_epoch_no)
    );
    ad4080_pwr_reset_sync #(.HOLD_COUNT(3)) u_core_epoch (
        .clk_i(clk_core_i), .hold_ni({fifo_reset_ni, io_reset_ni, io_session_allow_i}), .ready_no(core_epoch_no)
    );

    assign can_command = link_online_io && !request_io_q && !closing_io_q && !seen_io_q[1];
    assign record_start_ready_o = can_command && framer_ready;
    assign read_credit = seen_core_q && request_core_q[1] && remaining_core_q != 0;
    assign read_req_ready_core_o = read_credit && lower_ready;
    assign record_idle_core_o = link_idle_core && !request_core_q[1] && !seen_core_q;
    assign record_is_spi = SPI_RESULT_BOUND && (record_mode_q == 2'd1 || record_mode_q == 2'd2);

    // [状态] 按图 89 的首末样本进入接口解释。这里明确输出寄存器装载沿，
    // 不把 FIFO 预取映射为已出线。两事件各至多一次，外部可捕获并跨域。
    // SPI 状态事件跟真实 DCLK 上这一字的首位组关联，由邮箱回执对回服务
    // 域。不能将进入 SPI 两字缓冲视为已送到外部。
    assign actual_word_load = raw_word_load || (record_is_spi && spi_tick && frame_word_start);
    assign first_payload_word_load_io_o = actual_word_load && words_to_load_io_q != 0 &&
                                        words_to_load_io_q == count_io_q;
    assign last_payload_word_load_io_o = actual_word_load && words_to_load_io_q == 15'd1;

    // [冲刷] 运行故障要清掉旧队列与单拍预留。两端仍完成控制握手后才
    // 撤销冲刷，避免一个中止很快的记录授权以迟到的同步位越过新会话。
    always_ff @(posedge clk_io_i or negedge io_epoch_no) begin
        if (!io_epoch_no) begin
            request_io_q <= 1'b0;
            closing_io_q <= 1'b0;
            flush_io_q <= 1'b0;
            count_io_q <= '0;
            words_to_load_io_q <= '0;
            words_for_spi_cache_q <= '0;
            record_mode_q <= '0;
            seen_io_q <= '0;
            fault_io_q <= '0;
        end else begin
            seen_io_q <= {seen_io_q[0], seen_core_q};
            fault_io_q <= {fault_io_q[0], read_fault_core_o};
            if (record_start_accept_o) begin
                request_io_q <= 1'b1;
                count_io_q <= record_count_i;
                words_to_load_io_q <= record_count_i;
                words_for_spi_cache_q <= record_count_i;
                record_mode_q <= SPI_RESULT_BOUND ? result_mode_io_i : 2'd0;
            end else begin
                if (actual_word_load && words_to_load_io_q != 0) words_to_load_io_q <= words_to_load_io_q - 1'b1;
                if (parallel_valid && parallel_ready) words_for_spi_cache_q <= words_for_spi_cache_q - 1'b1;
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
    logic [1:0] raw_prefetch_words;                      // 两级同步已确认可用，避免提前发送头
    logic da_fall, da_rise, db_fall, db_rise;
    logic payload_start, protocol_error, mode_error, dropped, overflow;
    logic readback_allow_io_q, session_power_io_q;
    logic unused_core_online, unused_core_full, unused_busy;
    logic [2:0] unused_beat_width;

    // [异常] 组合状态仅进入 I/O 域寄存器。物理 epoch 撤销仍异步关闭，
    // 活动错误由本域采纳后冲刷 raw 链，并保持到核心旧授权退场。
    always_ff @(posedge clk_io_i or negedge io_epoch_no) begin
        if (!io_epoch_no) begin
            readback_allow_io_q <= 1'b0;
            session_power_io_q <= 1'b0;
        end else begin
            readback_allow_io_q <= !flush_io_q && !(record_error_o && request_io_q);
            session_power_io_q <= 1'b1;
        end
    end
    // 在目标域两拍同步会话位之前，来源已经建立字数；退场完成前保持总线。
`ifndef SYNTHESIS
    assert property (@(posedge clk_io_i) disable iff (!io_epoch_no)
                     request_io_q && $past(request_io_q) |-> $stable(count_io_q));
`endif
    assign read_fault_core_o = protocol_error || mode_error || dropped || overflow;
    ad4080_result_readback_link #(.FIFO_ADDR_WIDTH(FIFO_ADDR_WIDTH)) u_readback (
        .clk_core_i, .clk_io_i, .fifo_reset_ni, .io_reset_ni,
        .io_session_allow_i(readback_allow_io_q),
        .io_start_allow_i(payload_start), .read_req_valid_core_i(read_req_valid_core_i && read_credit),
        .read_req_ready_core_o(lower_ready), .read_addr_core_i,
        .read_dual_lane_core_i(1'b0), .read_intf_check_core_i(1'b0),
        .sram_read_req_valid_core_o, .sram_read_req_ready_core_i, .sram_read_addr_core_o,
        .sram_read_rsp_valid_core_i, .sram_read_rsp_data_core_i, .link_idle_core_o(link_idle_core),
        .link_online_core_o(unused_core_online), .link_full_core_o(unused_core_full),
        .read_mode_error_core_o(mode_error), .read_protocol_error_core_o(protocol_error),
        .transport_drop_core_o(dropped), .transport_overflow_core_o(overflow),
        .io_online_o(link_online_io), .io_prefetch_words_o(raw_prefetch_words), .io_word_load_o(raw_word_load),
        .io_parallel_mode_i(record_is_spi), .io_parallel_ready_i(parallel_ready),
        .io_parallel_valid_o(parallel_valid), .io_parallel_word_o(parallel_word),
        .io_pair_valid_o(raw_valid), .io_first_o(raw_first), .io_last_o(raw_last),
        .io_dual_lane_o(raw_dual), .io_intf_check_o(raw_check),
        .io_da_fall_o(da_fall), .io_da_rise_o(da_rise), .io_db_fall_o(db_fall), .io_db_rise_o(db_rise)
    );
    ad4080_fifo_record_framer #(.SPI_PIN_HEADER(SPI_RESULT_BOUND), .SPI_CRC_TAIL_BOUND(SPI_CRC_TAIL_BOUND)) u_framer (
        .clk_i(clk_io_i), .rst_ni(io_epoch_no), .power_good_i(session_power_io_q),
        .abort_i(abort_i || fault_io_q[1] || (request_io_q && record_is_spi && spi_abort) ||
                 (raw_valid && (raw_dual || raw_check || db_fall || db_rise))),
        .start_valid_i(record_start_valid_i && can_command), .start_ready_o(framer_ready),
        .start_accept_o(record_start_accept_o), .mode_i(SPI_RESULT_BOUND ? result_mode_io_i : 2'd0),
        .sample_count_i(record_count_i),
        .format_bound_i, .crc_service_ready_i,
        .payload_prefetch_words_i(raw_prefetch_words), .payload_pair_valid_i(raw_valid), .payload_pair_i({da_fall, da_rise}),
        .payload_pair_first_i(raw_first), .payload_pair_last_i(raw_last), .payload_start_allow_o(payload_start),
        .sample_valid_i(parallel_valid), .sample_ready_o(parallel_ready), .sample_i(parallel_word),
        .sample_last_i(words_for_spi_cache_q == 15'd1),
        .crc_valid_i, .crc_ready_o, .crc_i, .crc_error_i, .crc_cancel_o,
        .beat_valid_o(frame_valid), .beat_ready_i(record_is_spi || beat_ready_i), .beat_o(frame_beat),
        .spi_pin_beat_taken_i(spi_tick), .sync_header_o(sync_header), .payload_word_start_o(frame_word_start),
        .beat_width_o(unused_beat_width), .beat_first_o(frame_first), .beat_last_o(frame_last),
        .beat_kind_o(frame_kind), .busy_o(unused_busy),
        .done_o(record_done_o), .error_o(record_error_o), .error_code_o(record_error_code_o)
    );

    assign beat_valid_o = !record_is_spi && frame_valid;
    assign beat_pair_o = beat_valid_o ? frame_beat[1:0] : '0;
    assign beat_first_o = beat_valid_o && frame_first;
    assign beat_last_o = beat_valid_o && frame_last;
    assign beat_kind_o = beat_valid_o ? frame_kind : '0;

    generate
        if (SPI_RESULT_BOUND) begin : g_spi_result
            ad4080_result_spi_pins u_pins (
                .clk_io_i, .rst_ni(io_epoch_no), .mode_io_i(result_mode_io_i),
                .capture_full_i(result_capture_full_core_i),
                .session_io_i(record_is_spi && request_io_q && !closing_io_q && !record_error_o),
                .header_i(sync_header), .group_valid_io_i(record_is_spi && frame_valid),
                .group_io_i(frame_beat), .group_last_io_i(frame_last), .group_taken_io_o(spi_tick),
                .payload_ready_io_o(result_spi_ready_io_o), .abort_io_o(spi_abort),
                .dcs_ni(result_dcs_ni), .dclk_i(result_dclk_i), .sdo_o(result_sdo_o), .sdo_oe_o(result_sdo_oe_o),
                .selected_async_o(result_spi_selected_async_o)
            );
        end else begin : g_lvds_only
            // 固定 LVDS 的旧壳不启用物理 SPI。保持相同可接叉口，综合会
            // 消掉这些仅用于区分未绑定端口的静态连接。
            logic unused_spi_pins;
            assign unused_spi_pins = ^{result_capture_full_core_i, result_dcs_ni, result_dclk_i,
                                       sync_header, frame_beat[3:2]};
            assign spi_tick = 1'b0;
            assign spi_abort = 1'b0;
            assign result_spi_ready_io_o = 1'b0;
            assign result_spi_selected_async_o = 1'b0;
            assign result_sdo_o = '0;
            assign result_sdo_oe_o = '0;
        end
    endgenerate
endmodule

`default_nettype wire
