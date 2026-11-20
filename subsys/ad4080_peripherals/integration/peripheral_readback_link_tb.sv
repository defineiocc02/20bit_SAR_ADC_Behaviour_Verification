`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : peripheral_readback_link_tb.sv
// 模块名     : peripheral_readback_link_tb
// 版本       : v1.0
// 功能       : 从真实外围 SRAM 读回，经真实 CDC 到 I/O 域逐 pair 重组。
// 设计说明   :
//   * 台架核心侧只写已知字和发读描述符。I/O 监视器只看 PHY pair，按
//     已知序列比对。没有跨域的台架载荷赋值。
//   * 四字异步队列便于验证满态。复电前故意留一笔半帧和一笔尚未发的字。
//   * OTP 宏及校验器为本台架握手端点，沿用外层 POR 与整字提交路径。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [审计] 追加完整单 lane 记录、未绑定拒绝与旧应答失配。
// =============================================================================
module peripheral_readback_link_tb;
    // =========================================================================
    // 1. 独立时钟、测试载荷与握手观察
    // =========================================================================
    // 四个银行均置独立载荷，容量与位序检查无法靠重复返回首地址蒙混过去。
    // 同地址稍后再以合法模式请求，确认拒绝不会覆盖内容或产生幽灵帧。
    localparam int BATCH_WORDS = 5;                // 四格存满后的一笔需要等下游释放
    localparam logic [13:0] BATCH_ADDR [BATCH_WORDS] =
        '{14'h0002, 14'h1003, 14'h2004, 14'h3005, 14'h0035};
    localparam logic [19:0] BATCH_DATA [BATCH_WORDS] =
        '{20'h95E2B, 20'h80001, 20'h3F317, 20'hFEDC9, 20'h1AB07};
    localparam logic [19:0] RESTART_WORD = 20'h3BC71;
    localparam logic [13:0] RESTART_ADDR = 14'h3A50;
    localparam logic [7:0] OTP_TEST_WORD = 8'hA5;  // 台架见证字，用于验证真实提交路径
    localparam logic [23:0] FIXTURE_CRC_POLY = 24'h86_4CFB;
    localparam logic [23:0] FIXTURE_CRC_SEED = 24'hB7_04CE;
    // 独立整串 GF(2) 长除法所得余数 81A4D4，覆盖 AA 与两个实读字。
    // 测试多项式与种子仅绑定此台架，与产品 CRC 工程合同分开。
    localparam logic [71:0] RECORD_GOLDEN = 72'hAA_95E2B_80001_81A4D4;
    localparam logic [51:0] ONE_WORD_GOLDEN = 52'hAA_95E2B_413150;
    // 成功帧和故障帧共享同一组前两字。这样错误分段也必须先发对已承接的位，
    // 比只检查最终错误码更容易找出晚 CRC 前篡改载荷的回归。
    typedef enum logic [2:0] {SCORE_OFF, SCORE_FULL, SCORE_LATE_CRC, SCORE_UNDERRUN, SCORE_ONE_WORD} score_t;

    // --- 供电、时钟与 OTP 两个适配器 ---
    logic clk_aon_i = 1'b0, clk_core_i = 1'b0, clk_io_i = 1'b0;
    time io_half = 11000ps;             // 静默窗口切换。整记录按 2.5 ns 原始 CLK 逐拍接收
    logic por_good = 1'b0, iovdd_good = 1'b1;
    logic fifo_reset_n, io_reset_n, io_session_allow, conversion_allow_core;
    logic otp_cancel, otp_req_valid, otp_rsp_ready, verify_req_valid, verify_rsp_ready;
    logic otp_rsp_valid = 1'b0, verify_rsp_valid = 1'b0, verify_rsp_pass = 1'b0;
    logic [7:0] verify_image;
    logic otp_req_ready, verify_req_ready;

    // --- 写仲裁、读预留及错误注入 ---
    logic write_valid = 1'b0, sram_ready, sram_rsp_valid, sram_read_valid;
    logic [13:0] write_addr = '0, sram_read_addr;
    logic [19:0] write_data = '0, sram_rsp_data;
    logic read_valid = 1'b0, read_ready;
    logic [13:0] read_addr = '0;
    logic read_dual = 1'b0, read_check = 1'b0;
    logic mask_response = 1'b0; // 注入返回 valid 丢失，真实读仍进入 SRAM
    logic extra_response = 1'b0; // 模拟未受理读时出现的迟到有效
    logic link_idle, link_online, link_full, mode_error, read_error, transport_drop, transport_overflow;
    logic bist_request = 1'b0, bist_start = 1'b0, bist_reinit = 1'b0;
    logic bist_granted, bist_start_ready, bist_busy, bist_done;
    logic [1:0] bist_fail_reason;

    // --- I/O 输出监视器 ---
    logic io_start_allow = 1'b0, io_online, pair_valid, pair_first, pair_last, pair_dual, pair_check;
    logic da_fall, da_rise, db_fall, db_rise;
    int completed_frames = 0, slots_seen = 0;
    bit frame_open = 1'b0;
    logic [19:0] frame_word;

    // --- 同一物理读口的整记录接收器与台架 CRC 服务 ---
    // 记录选择是 I/O 域管理变量，只在 payload 退休为空时改。完整记录
    // 接收与原有裸载荷评分器互斥，来源仍是同一指针链和同一物理 SRAM。
    logic record_selected = 1'b0, record_start = 1'b0, record_bound = 1'b0;
    logic record_crc_service_ready = 1'b0, record_start_ready, record_accept;
    logic record_done, record_error, record_abort = 1'b0, record_idle, record_read_fault;
    logic [2:0] record_error_code;
    logic record_beat_valid, record_beat_first, record_beat_last;
    // 装载在本沿，首位对在下一沿。两项都比较才能排除把 SRAM 回复或首位
    // 的对外交付当成 FIFO 状态事件。末装载不等整记录成功，尾仍可能出错。
    logic record_first_load, record_last_load;
    logic [1:0] record_beat;
    logic [1:0] record_beat_kind;
    logic record_crc_valid, record_crc_ready, record_crc_cancel;
    logic [23:0] record_crc, record_crc_acc;
    logic [14:0] record_count = 15'd2;    // 负例可宣告三字，实际只提交两笔真读
    score_t score_case = SCORE_OFF;
    logic record_trace_active = 1'b0;    // 自 AA 起逐原始 CLK，失败场景也保留缺拍
    longint signed first_record_ps;      // 保留 1 ps 精度，不能用以 ns 取整的 $time
    logic [71:0] record_word;
    int record_pair_count;
    logic raw_read_ready, raw_req, record_read_ready, record_req;
    logic [13:0] raw_addr, record_addr;
    (* ASYNC_REG = "TRUE" *) logic [1:0] record_select_core_q;

    always #50ns clk_aon_i = ~clk_aon_i;
    always #7ns clk_core_i = ~clk_core_i;
    // 只有接收器静默时程序改 io_half。记录发出后没有单拍停钟手段，故连续
    // 性断言和 2.5 ns 的实际时间戳都能揭露基于 valid 的边沿压缩。
    initial forever #(io_half) clk_io_i = ~clk_io_i;

    // =========================================================================
    // 2. 生产 RTL 实例与台架宏端点
    // =========================================================================
    ad4080_peripheral_services #(.OTP_WORD_WIDTH(8), .OTP_TIMEOUT_CYCLES(16)) u_services (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i(1'b1),
        .por_d_ok_async_i(por_good), .vdd33_ok_async_i(1'b1), .ref_ok_async_i(1'b1),
        .iovdd_ok_async_i(iovdd_good), .ana_ready_async_i(1'b1),
        .clients_idle_aon_i(1'b1), .capture_incomplete_aon_i(1'b0), .sw_reset_req_aon_i(1'b0),
        .mode_req_valid_aon_i(1'b0), .mode_req_aon_i(2'b00), .wake_recover_done_aon_i(1'b0),
        .cfg_hw_reset_no(), .core_reset_no(), .fifo_reset_no(fifo_reset_n), .io_reset_no(io_reset_n), .ana_reset_no(),
        .cfg_init_done_aon_o(), .cfg_control_write_allow_aon_o(), .cfg_full_access_allow_aon_o(),
        .operating_mode_aon_o(), .wake_busy_aon_o(), .wake_recover_req_aon_o(),
        .mode_req_accept_aon_o(), .sw_reset_accept_aon_o(), .isolate_io_o(), .iovdd_ldo_en_o(), .ref_cmo_en_o(),
        .por_ana_event_aon_o(), .por_ana_event_core_o(),
        .pwr_cmd_evaluate_aon_i(1'b1), .pwr_cmd_ready_core_o(), .pwr_rsp_valid_core_o(), .pwr_rsp_accept_core_o(),
        .pwr_rsp_op_core_o(), .pwr_mode_core_o(), .pwr_ldo_pd_core_o(), .vdd11_ldo_en_o(),
        .fifo_record_allow_io_o(), .cfg_poll_allow_aon_o(),
        .conversion_allow_aon_o(), .conversion_allow_core_o(conversion_allow_core),
        .otp_power_good_aon_i(1'b1), .otp_adapters_quiescent_aon_i(otp_cancel && !otp_rsp_valid && !verify_rsp_valid),
        .otp_adapters_cancel_aon_o(otp_cancel), .otp_req_valid_aon_o(otp_req_valid),
        .otp_req_ready_aon_i(otp_req_ready), .otp_req_addr_aon_o(),
        .otp_rsp_valid_aon_i(otp_rsp_valid), .otp_rsp_ready_aon_o(otp_rsp_ready),
        .otp_rsp_data_aon_i(OTP_TEST_WORD), .otp_rsp_error_aon_i(1'b0),
        .otp_verify_req_valid_aon_o(verify_req_valid), .otp_verify_req_ready_aon_i(verify_req_ready),
        .otp_verify_image_aon_o(verify_image), .otp_verify_rsp_valid_aon_i(verify_rsp_valid),
        .otp_verify_rsp_ready_aon_o(verify_rsp_ready), .otp_verify_rsp_pass_aon_i(verify_rsp_pass),
        .otp_busy_aon_o(), .otp_error_code_aon_o(), .otp_shadow_valid_aon_o(), .otp_shadow_image_aon_o(),
        .bist_mode_req_aon_i(bist_request), .bist_mode_ready_aon_o(), .bist_mode_grant_aon_o(bist_granted),
        .bist_func_hold_core_o(), .bist_func_quiesced_core_i(link_idle && record_idle && !read_valid && !write_valid),
        .bist_start_core_i(bist_start), .bist_start_ready_core_o(bist_start_ready), .bist_checkerboard_core_i(1'b0),
        .bist_func_reinit_done_core_i(bist_reinit), .bist_contents_invalid_core_o(), .bist_busy_core_o(bist_busy),
        .bist_done_core_o(bist_done), .bist_fail_reason_core_o(bist_fail_reason),
        .bist_fail_addr_valid_core_o(), .bist_fail_addr_core_o(),
        .bist_fail_expected_core_o(), .bist_fail_actual_core_o(),
        .result_req_valid_core_i(write_valid || sram_read_valid), .result_req_ready_core_o(sram_ready),
        .result_write_core_i(write_valid), .result_addr_core_i(write_valid ? write_addr : sram_read_addr),
        .result_write_data_core_i(write_data), .result_read_valid_core_o(sram_rsp_valid),
        .result_read_data_core_o(sram_rsp_data),
        .io_word_valid_i(1'b0), .io_word_ready_o(io_session_allow), .io_sample_i(20'b0),
        .io_dual_lane_i(1'b0), .io_intf_check_i(1'b0), .io_edge_words_valid_o(), .io_da_fall_o(),
        .io_da_rise_o(), .io_db_fall_o(), .io_db_rise_o(), .io_slot_count_o()
    );

    // [绑定] 台架代表上游单口写仲裁。给读适配器的 ready 明确包含写未获选；
    // 真正的 SRAM 返回原样接入适配器。独立的壳层并行向量没有接到 PHY。
    ad4080_result_readback_link #(.FIFO_ADDR_WIDTH(2)) u_readback (
        .clk_core_i, .clk_io_i, .fifo_reset_ni(fifo_reset_n), .io_reset_ni(io_reset_n),
        .io_session_allow_i(io_session_allow), .io_start_allow_i(!record_selected && io_start_allow),
        .read_req_valid_core_i(!record_select_core_q[1] && read_valid), .read_req_ready_core_o(raw_read_ready),
        .read_addr_core_i(read_addr), .read_dual_lane_core_i(read_dual), .read_intf_check_core_i(read_check),
        .sram_read_req_valid_core_o(raw_req),
        .sram_read_req_ready_core_i(sram_ready && !write_valid && !record_select_core_q[1]),
        .sram_read_addr_core_o(raw_addr),
        .sram_read_rsp_valid_core_i(!record_select_core_q[1] && ((sram_rsp_valid && !mask_response) || extra_response)),
        .sram_read_rsp_data_core_i(sram_rsp_data),
        .link_idle_core_o(link_idle), .link_online_core_o(link_online), .link_full_core_o(link_full),
        .read_mode_error_core_o(mode_error),
        .read_protocol_error_core_o(read_error), .transport_drop_core_o(transport_drop),
        .transport_overflow_core_o(transport_overflow), .io_online_o(io_online), .io_pair_valid_o(pair_valid),
        .io_prefetch_words_o(), .io_word_load_o(),
        .io_parallel_mode_i(1'b0), .io_parallel_ready_i(1'b0), .io_parallel_valid_o(), .io_parallel_word_o(),
        .io_first_o(pair_first), .io_last_o(pair_last), .io_dual_lane_o(pair_dual), .io_intf_check_o(pair_check),
        .io_da_fall_o(da_fall), .io_da_rise_o(da_rise), .io_db_fall_o(db_fall), .io_db_rise_o(db_rise)
    );

    // [整记录] 验证独立的可综合读回绑定。裸载荷测试和整记录测试共享唯一
    // SRAM。只有没有请求和返回时切换仲裁目的，选择位经两级同步到核心。
    always @(posedge clk_core_i or negedge fifo_reset_n) begin
        if (!fifo_reset_n) record_select_core_q <= '0;
        else record_select_core_q <= {record_select_core_q[0], record_selected};
    end
    assign read_ready = record_select_core_q[1] ? record_read_ready : raw_read_ready;
    assign sram_read_valid = record_select_core_q[1] ? record_req : raw_req;
    assign sram_read_addr = record_select_core_q[1] ? record_addr : raw_addr;

    ad4080_fifo_lvds_record_link #(.FIFO_ADDR_WIDTH(2)) u_record (
        .clk_core_i, .clk_io_i, .fifo_reset_ni(fifo_reset_n), .io_reset_ni(io_reset_n),
        .io_session_allow_i(io_session_allow), .abort_i(record_abort),
        .record_start_valid_i(record_start), .record_start_ready_o(record_start_ready),
        .record_start_accept_o(record_accept), .record_count_i(record_count), .format_bound_i(record_bound),
        .crc_service_ready_i(record_crc_service_ready),
        .crc_valid_i(record_crc_valid), .crc_ready_o(record_crc_ready), .crc_i(record_crc), .crc_error_i(1'b0),
        .crc_cancel_o(record_crc_cancel),
        .read_req_valid_core_i(record_select_core_q[1] && read_valid), .read_req_ready_core_o(record_read_ready),
        .read_addr_core_i(read_addr), .sram_read_req_valid_core_o(record_req),
        .sram_read_req_ready_core_i(sram_ready && !write_valid && record_select_core_q[1]),
        .sram_read_addr_core_o(record_addr), .sram_read_rsp_valid_core_i(record_select_core_q[1] && sram_rsp_valid),
        .sram_read_rsp_data_core_i(sram_rsp_data), .record_idle_core_o(record_idle), .read_fault_core_o(record_read_fault),
        .beat_valid_o(record_beat_valid), .beat_ready_i(1'b1), .beat_pair_o(record_beat),
        .beat_first_o(record_beat_first), .beat_last_o(record_beat_last), .beat_kind_o(record_beat_kind),
        .first_payload_word_load_io_o(record_first_load), .last_payload_word_load_io_o(record_last_load),
        .result_mode_io_i(2'd0), .result_capture_full_core_i(1'b0), .result_dcs_ni(1'b1), .result_dclk_i(1'b1),
        .result_sdo_o(), .result_sdo_oe_o(), .result_spi_ready_io_o(), .result_spi_selected_async_o(),
        .record_done_o(record_done), .record_error_o(record_error), .record_error_code_o(record_error_code)
    );

    // [握手] 两个空闲端点才可受理。将 response 保持到 ready，冲刷时
    // 清空，保证首个 AON 会话不依赖 ready 常开或台架直接置 shadow。
    assign otp_req_ready = !otp_cancel && !otp_rsp_valid;
    assign verify_req_ready = !otp_cancel && !verify_rsp_valid;
    always @(posedge clk_aon_i) begin
        if (otp_cancel) begin
            otp_rsp_valid <= 1'b0;
            verify_rsp_valid <= 1'b0;
            verify_rsp_pass <= 1'b0;
        end else begin
            if (otp_req_valid && otp_req_ready) otp_rsp_valid <= 1'b1;
            else if (otp_rsp_ready) otp_rsp_valid <= 1'b0;
            if (verify_req_valid && verify_req_ready) begin
                verify_rsp_valid <= 1'b1;
                verify_rsp_pass <= (verify_image == OTP_TEST_WORD);
            end else if (verify_rsp_ready) verify_rsp_valid <= 1'b0;
        end
    end

    // =========================================================================
    // 3. I/O 域独立重组与核心端事务产生
    // =========================================================================
    // [评分] 按 FIFO 单 lane 发送约定的顺序移入 DA 双沿位。DB 无值、
    // 首尾、槽数和完整字均核对。资格撤销时丢弃半帧，下一有效槽重新标首。
    always @(posedge clk_io_i) begin
        if (!io_online || record_selected) begin
            frame_open = 1'b0;
        end else if (pair_valid) begin
            if (pair_first == frame_open) $fatal(1, "Invalid PHY first-pair marking");
            if (pair_first) begin
                frame_open = 1'b1;
                slots_seen = 0;
                frame_word = '0;
            end
            if (pair_dual || pair_check || db_fall || db_rise)
                $fatal(1, "FIFO payload left single-lane data mode");
            frame_word = {frame_word[17:0], da_fall, da_rise};
            slots_seen++;
            if (pair_last != (slots_seen == 10))
                $fatal(1, "Wrong PHY slot count or last-pair marker");
            if (pair_last) begin
                if (completed_frames < BATCH_WORDS) begin
                    if (frame_word != BATCH_DATA[completed_frames])
                        $fatal(1, "Reconstructed frame %0d differs: %05h", completed_frames, frame_word);
                end else if (completed_frames != BATCH_WORDS || frame_word != RESTART_WORD) begin
                    $fatal(1, "Stale or wrong post-flush frame: %05h", frame_word);
                end
                $display("PAIR reconstructed frame %0d word %05h slots %0d", completed_frames, frame_word, slots_seen);
                completed_frames++;
                frame_open = 1'b0;
            end
        end else if (frame_open || (completed_frames > 0 && completed_frames < BATCH_WORDS)) begin
            // 五笔成组满足预填条件，首笔退休后每个槽和帧间边界都应连续。
            $fatal(1, "Unexpected gap in continuously queued PHY pairs");
        end
    end

    // [CRC] 余数服务使用头/载荷位对。末对从组合下一余数同沿交给外部
    // CRC 接口，避免寄存上一拍部分结果或追加空拍。参数只用于此台架。
    function automatic logic [23:0] fixture_crc_pair(input logic [23:0] seed, input logic [1:0] pair);
        logic [23:0] remainder;
        remainder = seed;
        for (int index = 1; index >= 0; index--) begin
            remainder = (remainder << 1) ^ ((remainder[23] ^ pair[index]) ? FIXTURE_CRC_POLY : 24'd0);
        end
        return remainder;
    endfunction

    assign record_crc_valid = record_crc_ready && score_case != SCORE_LATE_CRC;
    assign record_crc = fixture_crc_pair(record_crc_acc, record_beat);
    // 每个已发头/载荷位对之后才寄存状态。用末对的组合下一过渡同沿交付，
    // 否则产出的尾会漏掉最后两位。下一条 record_accept 从幂等种子重算。
    always @(posedge clk_io_i) begin
        if (record_accept || record_crc_cancel) record_crc_acc <= FIXTURE_CRC_SEED;
        else if (record_selected && record_beat_valid && record_beat_kind != 2'd2)
            record_crc_acc <= record_crc;
    end

    // [验收] 从第一个 AA 位对起，每个上升沿都有一条预期，绝不等 valid。
    // bits 从独立的整串常量取出。未写齐的第三字故意给一拍无效预期，
    // 真链必须在该拍结束报欠载，不能把它缩成正常前缀和尾继续发送。
    always @(posedge clk_io_i) begin : observe_raw_record
        int prefix_ticks, output_ticks, last_load_tick, vector_pairs;
        logic [71:0] golden;
        logic [1:0] expected;
        longint signed observed_ps;
        prefix_ticks = score_case == SCORE_ONE_WORD ? 14 : 24;
        vector_pairs = score_case == SCORE_ONE_WORD ? 26 : 36;
        output_ticks = score_case == SCORE_ONE_WORD || score_case == SCORE_FULL ? vector_pairs : prefix_ticks;
        last_load_tick = score_case == SCORE_ONE_WORD ? 3 : score_case == SCORE_UNDERRUN ? -1 : 13;
        golden = score_case == SCORE_ONE_WORD ? {20'b0, ONE_WORD_GOLDEN} : RECORD_GOLDEN;
        observed_ps = longint'($time);
        if (record_accept) begin
            record_word <= '0;
            record_pair_count <= 0;
            record_trace_active <= 1'b0;
        end else if (record_selected && score_case != SCORE_OFF && (record_trace_active || record_beat_first)) begin
            // 首 AA 当且仅当首次对外交付。此处不观察 ready 后才推进索引，
            // 以免测试自身放过由接口假设错误引入的一个停顿拍。
            if (record_pair_count == 0) first_record_ps <= observed_ps;
            else if (observed_ps != first_record_ps + 64'sd2500*longint'(record_pair_count))
                $fatal(1, "Record did not follow each free-running 2.5 ns CLK");
            // 原始轨迹保留缺失的拍及信号状态。用独立的稀疏边界行供人工查看，
            // CSV 则能逐拍重建波形及比较状态事件，无需截取有效流。
            $display("RAW_RECORD_CSV,%0d,%0d,%0d,%0b,%0d,%b,%0b,%0b,%0b,%0b,%0b,%0b,%0d",
                     score_case, record_pair_count, observed_ps, record_beat_valid, record_beat_kind, record_beat,
                     record_beat_first, record_beat_last, record_first_load, record_last_load, record_crc_ready,
                     record_error, record_error_code);
            if ((score_case == SCORE_UNDERRUN || score_case == SCORE_LATE_CRC) && record_pair_count == prefix_ticks) begin
                // 晚 CRC 在末载荷拍已判决。缺样本是在当前空拍判决，两者都应
                // 停止有效输出，下面的激励在失效沿之后再检查寄存的错误。
                if (record_beat_valid || record_first_load || record_last_load || record_done)
                    $fatal(1, "Failed record emitted a beat or completion beyond its prefix");
                if (score_case == SCORE_LATE_CRC && (!record_error || record_error_code != 3'd4))
                    $fatal(1, "CRC deadline was not reported before the first would-be tail cycle");
                $display("TRACE RECORD MISSING case=%0d raw=%0d valid=0 t_ps=%0d",
                         score_case, record_pair_count, observed_ps);
                record_trace_active <= 1'b0;
            end else begin
                // 原始拍次允许独立对照时钟、PAIRS 和标记。故障场景仍检查
                // 前缀和装载脉冲，表示已经出去的样本不能被当成可重放的零长度。
                expected = 2'(golden >> (2*(vector_pairs - 1 - record_pair_count)));
                if (!record_beat_valid || record_beat !== expected ||
                    record_beat_first != (record_pair_count == 0) ||
                    record_beat_last != (record_pair_count == vector_pairs - 1) ||
                    record_beat_kind != (record_pair_count < 4 ? 2'd0 : record_pair_count < prefix_ticks ? 2'd1 : 2'd2) ||
                    record_first_load != (record_pair_count == 3) || record_last_load != (record_pair_count == last_load_tick))
                    $fatal(1, "Incorrect raw clock %0d pair=%b expected=%b valid=%b load=%b%b",
                           record_pair_count, record_beat, expected, record_beat_valid, record_first_load, record_last_load);
                if ((record_pair_count inside {0, 3, 4}) || record_pair_count == prefix_ticks-1 ||
                    record_pair_count == prefix_ticks || record_pair_count == output_ticks-1 ||
                    record_first_load || record_last_load)
                    $display("TRACE RECORD case=%0d raw=%0d t_ps=%0d kind=%0d pair=%b first_load=%b last_load=%b crc_ready=%b",
                             score_case, record_pair_count, observed_ps, record_beat_kind, record_beat,
                             record_first_load, record_last_load, record_crc_ready);
                record_word <= {record_word[69:0], record_beat};
                record_pair_count <= record_pair_count + 1;
                record_trace_active <= record_pair_count != output_ticks-1 ||
                                       score_case == SCORE_UNDERRUN || score_case == SCORE_LATE_CRC;
            end
        end
    end

    // [准入] 核心下降沿更新请求，上升沿检查受理。保持读描述符至 ready，
    // 允许同一任务在队列满的一段时间持续提出原地址。
    task automatic write_word(input logic [13:0] addr, input logic [19:0] data);
        @(negedge clk_core_i);
        write_valid = 1'b1;
        write_addr = addr;
        write_data = data;
        @(posedge clk_core_i);
        if (!sram_ready) $fatal(1, "Expected real SRAM write acceptance");
        @(negedge clk_core_i);
        write_valid = 1'b0;
    endtask

    task automatic present_read(input logic [13:0] addr, input bit dual, input bit check);
        @(negedge clk_core_i);
        read_valid = 1'b1;
        read_addr = addr;
        read_dual = dual;
        read_check = check;
    endtask

    task automatic await_read_fire();
        do @(posedge clk_core_i); while (!read_ready);
        @(negedge clk_core_i);
        read_valid = 1'b0;
    endtask

    // [模式] 单 lane 与真实载荷是 FIFO 入口合同。拒绝无需占用 SRAM，
    // 上层观察 mode_error 可结束非法命令，稍后从同一地址重新提交。
    task automatic reject_fifo_mode(input logic [13:0] addr, input bit dual, input bit check);
        present_read(addr, dual, check);
        repeat (3) begin
            @(posedge clk_core_i);
            if (!mode_error || read_ready || sram_read_valid || sram_rsp_valid)
                $fatal(1, "Illegal FIFO mode consumed an SRAM request");
        end
        @(negedge clk_core_i);
        read_valid = 1'b0;
    endtask

    // [拒绝] 能检查命令不意味着受理了记录。格式或 CRC 服务未绑定时，
    // 同步头、读启动资格和在发状态全无，只给拒绝原因。
    task automatic start_record(input bit accepted);
        @(negedge clk_io_i);
        record_start = 1'b1;
        @(posedge clk_io_i);
        if (!record_start_ready || record_accept != accepted) $fatal(1, "Record command acceptance mismatch");
        @(negedge clk_io_i);
        record_start = 1'b0;
        if (accepted) begin
            if (record_start_ready || record_error) $fatal(1, "Accepted record did not enter active state");
        end else if (!record_error || record_error_code != 3'd2 || record_beat_valid || record_crc_valid) begin
            $fatal(1, "Unbound record leaked a header, CRC session or payload authorization");
        end
    endtask

    // =========================================================================
    // 4. 联合验收次序
    // =========================================================================
    initial begin
        @(negedge clk_aon_i);
        por_good = 1'b1;
        wait (conversion_allow_core && link_online);
        for (int idx = 0; idx < BATCH_WORDS; idx++) write_word(BATCH_ADDR[idx], BATCH_DATA[idx]);
        write_word(RESTART_ADDR, RESTART_WORD);

        reject_fifo_mode(BATCH_ADDR[0], 1'b1, 1'b0);
        reject_fifo_mode(BATCH_ADDR[1], 1'b0, 1'b1);
        $display("PASS FIFO dual-lane and INTF_CHECK commands rejected without SRAM acceptance");

        // [满态] 先堵住 I/O 的新帧领取，再连续提交四个实读返回。第五笔
        // 保持 valid 检查确实没有透传到 SRAM。解除领取后沿序恢复并输出。
        for (int idx = 0; idx < BATCH_WORDS-1; idx++) begin
            present_read(BATCH_ADDR[idx], 1'b0, 1'b0);
            await_read_fire();
        end
        wait (link_full);
        present_read(BATCH_ADDR[4], 1'b0, 1'b0);
        repeat (5) begin
            @(posedge clk_core_i);
            if (read_ready || sram_read_valid || transport_drop || transport_overflow || pair_valid)
                $fatal(1, "Full readback queue failed to block SRAM issue");
        end
        @(negedge clk_io_i);
        io_start_allow = 1'b1;
        await_read_fire();
        wait (completed_frames == BATCH_WORDS);
        wait (link_idle);
        if (read_error || transport_drop || transport_overflow) $fatal(1, "Reserved read dropped or errored");
        $display("PASS full-queue reservation blocked fifth read and retired five consecutive frames");

        // [复电] 在发第一槽被 PHY 采过后拉低 IOVDD，另有完整旧字还没发。
        // 共享会话使在发与排队同时作废。保持 VDD11，复电后重新读一个字。
        @(negedge clk_io_i);
        io_start_allow = 1'b0;
        present_read(BATCH_ADDR[0], 1'b0, 1'b0);
        await_read_fire();
        present_read(BATCH_ADDR[1], 1'b0, 1'b0);
        await_read_fire();
        @(negedge clk_io_i);
        io_start_allow = 1'b1;
        wait (pair_valid && pair_first);
        @(posedge clk_io_i);
        @(negedge clk_io_i);
        if (!frame_open || link_idle) $fatal(1, "Expected an unfinished I/O frame before IOVDD loss");
        iovdd_good = 1'b0;
        #1ns;
        if (pair_valid || io_online || link_online || !link_idle || read_ready || !fifo_reset_n)
            $fatal(1, "IOVDD loss did not flush active and queued readback");
        repeat (4) @(negedge clk_io_i);
        iovdd_good = 1'b1;
        wait (link_online && io_online);
        repeat (16) begin
            @(negedge clk_io_i);
            if (pair_valid || completed_frames != BATCH_WORDS) $fatal(1, "Stale frame resurfaced after reset");
        end
        present_read(RESTART_ADDR, 1'b0, 1'b0);
        await_read_fire();
        wait (completed_frames == BATCH_WORDS+1);
        wait (link_idle);
        $display("PASS IOVDD flush invalidated partial and queued frames, fresh first word %05h", RESTART_WORD);

        // [整记录] 此处 cutover 已无 in-flight 字。不直接给 record DUT
        // 20-bit 输入，两个已知地址仅通过之前同一个物理 SRAM 读端提交。
        @(negedge clk_io_i);
        io_half = 1250ps;
        record_selected = 1'b1;
        record_crc_service_ready = 1'b1;
        wait (record_select_core_q[1]);
        // 两次拒绝各只缺一种绑定，分别闭合格式资格与 CRC 服务资格。
        start_record(1'b0);
        record_bound = 1'b1;
        record_crc_service_ready = 1'b0;
        start_record(1'b0);
        // 输入命令被拒后即使上游有待发地址，也不得取得单口资格。
        // 将地址维持三拍覆盖正常命令的跨域资格传播，排除迟到的误受理。
        present_read(BATCH_ADDR[0], 1'b0, 1'b0);
        repeat (3) @(negedge clk_core_i);
        if (read_ready || sram_read_valid) $fatal(1, "Rejected record consumed an SRAM address");
        read_valid = 1'b0;
        $display("PASS unbound record format or CRC service rejected before header and payload");

        record_crc_service_ready = 1'b1;
        start_record(1'b1);
        // 试读一个和黄金不同的地址来产生旧会话载荷，便于证明重新发记录
        // 时不会混进原帧已经离开 SRAM 的第一个字。
        present_read(BATCH_ADDR[4], 1'b0, 1'b0);
        await_read_fire();
        present_read(BATCH_ADDR[3], 1'b0, 1'b0);
        await_read_fire();
        wait (record_beat_valid && record_beat_kind == 2'd1);
        @(negedge clk_io_i);
        record_abort = 1'b1;
        @(negedge clk_io_i);
        if (!record_error || record_error_code != 3'd6 || record_done || record_beat_valid)
            $fatal(1, "Active record cancellation did not revoke stale beats");
        record_abort = 1'b0;
        // 错误脉冲已到 I/O，核心旧许可仍可能在同步器内。等两域退场回执
        // 都成立才发新描述符，让完整黄金也能判定是否串入旧会话载荷。
        wait (record_start_ready && record_idle);
        $display("PASS record abort flushed active old payload before the next epoch");

        score_case = SCORE_FULL;
        start_record(1'b1);
        present_read(BATCH_ADDR[0], 1'b0, 1'b0);
        await_read_fire();
        // 首读真已交付，留出足够同步时间再查 AA 仍无效。下一笔命令必须
        // 在本记录成功或冲刷后发，预取等待和错误恢复由两条用例分别触发。
        repeat (12) begin
            @(negedge clk_io_i);
            if (record_beat_valid || record_first_load || record_last_load)
                $fatal(1, "Started a two-word record with only the first SRAM result");
        end
        $display("PASS first word alone held AA and launch during 12 raw I/O clocks");
        present_read(BATCH_ADDR[1], 1'b0, 1'b0);
        await_read_fire();
        // 两个参与黄金的读已受理，FIFO 此时仍可能有空位。再给地址检验
        // 关口来自同笔记录预算，不能仅靠整帧器少消费造成“表面帧长正确”。
        present_read(BATCH_ADDR[2], 1'b0, 1'b0);
        repeat (5) @(negedge clk_core_i);
        if (read_ready || sram_read_valid) $fatal(1, "Record count allowed a third SRAM address");
        read_valid = 1'b0;
        wait (record_done);
        @(negedge clk_io_i);
        if (record_error || record_read_fault || record_pair_count != 36 || record_word != RECORD_GOLDEN)
            $fatal(1, "Full-record oracle mismatch: %0d pairs word %018h", record_pair_count, record_word);
        wait (record_idle && record_start_ready);
        @(negedge clk_io_i);
        $display("RECORD_LVDS_GOLDEN PASS");
        $display("RECORD_RAW_N2 PASS");
        $display("PASS real SRAM full record %018h on 36 consecutive raw clocks (2.5 ns), load first=3 last=13", record_word);

        // [晚 CRC] 真实 SRAM 数据完整，故意只缺末载荷同沿的外部 CRC。
        // 已有首末装载事件仍有意义，但输出没有尾，整个记录绝不能报完成。
        score_case = SCORE_LATE_CRC;
        start_record(1'b1);
        // 地址和正常黄金完全一致，故唯一变化是 CRC 服务超出时间预算。
        for (int idx = 0; idx < 2; idx++) begin
            present_read(BATCH_ADDR[idx], 1'b0, 1'b0);
            await_read_fire();
        end
        wait (record_error);
        @(negedge clk_io_i);
        if (record_error_code != 3'd4 || record_done || record_beat_valid || record_pair_count != 24)
            $fatal(1, "Late external CRC incorrectly committed a tail or successful record");
        wait (record_start_ready && record_idle);
        $display("PASS late CRC: 24 consecutive header/data clocks, error=4, no tail and no done");
        $display("RECORD_LATE_CRC PASS");

        // [断供] 只给足启动水位两字，第三字未请求。必须留下第 24 拍
        // 缺失和明确错误，不能跳过再等待其到达。末字未进移位器不报 last。
        score_case = SCORE_UNDERRUN;
        record_count = 15'd3;
        start_record(1'b1);
        // 外部读描述符身体停止，原单口不制造异常返回。真实缺字来自 FIFO
        // 流控，和后面专门注入的 SRAM 少回复用例是两个不同失效点。
        for (int idx = 0; idx < 2; idx++) begin
            present_read(BATCH_ADDR[idx], 1'b0, 1'b0);
            await_read_fire();
        end
        wait (record_error);
        @(negedge clk_io_i);
        if (record_error_code != 3'd7 || record_done || record_beat_valid || record_pair_count != 24)
            $fatal(1, "Raw FIFO starvation was silently compressed or left pending");
        wait (record_start_ready && record_idle);
        $display("PASS two-word prefill followed by withheld third: raw=24 invalid, error=7, no last load or done");
        $display("RECORD_PAYLOAD_UNDERRUN PASS");

        // [单字] 证明两状态事件在同一装载沿均置位，并且只需一个预取字。
        score_case = SCORE_ONE_WORD;
        record_count = 15'd1;
        start_record(1'b1);
        present_read(BATCH_ADDR[0], 1'b0, 1'b0);
        await_read_fire();
        wait (record_done);
        @(negedge clk_io_i);
        if (record_error || record_pair_count != 26 || record_word != {20'b0, ONE_WORD_GOLDEN})
            $fatal(1, "Single-word clock or shared first/last load mismatch");
        wait (record_start_ready && record_idle);
        $display("PASS real SRAM one-word record on 26 consecutive raw clocks, first/last load both at raw=3");
        $display("RECORD_RAW_N1 PASS");
        @(negedge clk_io_i);
        score_case = SCORE_OFF;
        record_selected = 1'b0;
        wait (!record_select_core_q[1]);
        io_half = 11000ps;

        // [测试保持] 已排空的搬运器在 MBIST 开始时被共同冲刷。空闲确认
        // 不能因 transport 下线而撤销，否则仲裁器会误判功能流量恢复并中止。
        @(negedge clk_aon_i);
        bist_request = 1'b1;
        wait (bist_granted && bist_start_ready);
        @(negedge clk_core_i);
        bist_start = 1'b1;
        @(negedge clk_core_i);
        bist_start = 1'b0;
        repeat (4) begin
            @(negedge clk_core_i);
            if (!bist_busy || !link_idle || link_online || pair_valid)
                $fatal(1, "BIST lost its drained handshake when linked I/O reset");
        end
        @(negedge clk_aon_i);
        bist_request = 1'b0;
        wait (bist_done);
        @(negedge clk_core_i);
        if (bist_fail_reason != 2'd3) $fatal(1, "Expected explicit BIST abort before reinitialization");
        bist_reinit = 1'b1;
        wait (link_online && conversion_allow_core);
        @(negedge clk_core_i);
        bist_reinit = 1'b0;
        write_word(BATCH_ADDR[2], BATCH_DATA[2]);
        $display("PASS transport quiescence held while BIST flushed the I/O session");

        // [错误] 实际 SRAM 正常返回，故意在绑定接口掩去该 valid，验证
        // 适配器发现一拍契约破坏、锁存错误、封住后续申请且不发假帧。
        @(negedge clk_core_i);
        mask_response = 1'b1;
        present_read(BATCH_ADDR[2], 1'b0, 1'b0);
        await_read_fire();
        if (!sram_rsp_valid) $fatal(1, "Missing exclusive real SRAM response for fault injection");
        repeat (2) @(negedge clk_core_i);
        mask_response = 1'b0;
        present_read(BATCH_ADDR[3], 1'b0, 1'b0);
        repeat (16) begin
            @(negedge clk_io_i);
            if (!read_error || read_ready || sram_read_valid || pair_valid || transport_overflow)
                $fatal(1, "Missing-response error did not fault-close the link");
        end
        $display("PASS missing response latched protocol error and blocked further reads");

        // [失配] 冲刷流水后注入一笔没有匹配请求的 valid，并同拍提出新地址。
        // 旧应答必须先被判错，不能占用新地址或被当作其有效载荷。
        @(negedge clk_core_i);
        read_valid = 1'b0;
        @(negedge clk_io_i);
        iovdd_good = 1'b0;
        repeat (4) @(negedge clk_io_i);
        iovdd_good = 1'b1;
        wait (link_online && io_online);
        // 共同复位清除前一个缺失返回的粘滞故障。本次注入与下一合法请求
        // 放在同一核心沿，检测返回是否错误借用新请求的保留槽。
        present_read(RESTART_ADDR, 1'b0, 1'b0);
        extra_response = 1'b1;
        @(posedge clk_core_i);
        if (read_ready || sram_read_valid) $fatal(1, "Untracked response accepted a new read on its edge");
        @(negedge clk_core_i);
        extra_response = 1'b0;
        read_valid = 1'b0;
        repeat (16) begin
            @(negedge clk_io_i);
            if (!read_error || pair_valid) $fatal(1, "Untracked response was not quarantined");
        end
        $display("PASS untracked response latched error before same-edge request could issue");
        $display("PERIPHERAL_READBACK_LINK PASS");
        $finish;
    end

    initial begin
        // AON 真实初始化预期约 300 us，后续时序不依赖检查器的隐式超时。
        // 两时钟队列不推进或本域握手未闭合时，由此看门狗使回归失败。
        #500us;
        $fatal(1, "Peripheral readback link watchdog expired");
    end
endmodule

`default_nettype wire
