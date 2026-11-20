`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : peripheral_services_tb.sv
// 模块名     : peripheral_services_tb
// 版本       : v1.0
// 功能       : 在三个不同时钟下验证外围壳层正常启动、拒绝发布及 BIST 退场。
// 设计说明   :
//   * 台架只驱动外部合同，OTP 宏和校验端由各笔握手的定向激励代替。
//   * 3×8 OTP 镜像是测试数据。AON 运行在 10 MHz，保留上电等待计数。
//   * 把实读 SRAM 字通过台架送到 I/O 完整字接口。该步骤等价于外部测试
//     主机在 I/O 域提供一笔已转交的数据，DUT 内未引入跨域搬运。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [审计] 增加未完成记录、同沿请求、时序授权和旧完成诊断。
// =============================================================================
module peripheral_services_tb;
    // =========================================================================
    // 1. 台架端口与试验字
    // =========================================================================
    localparam logic [23:0] TEST_IMAGE = 24'h5C_A7_12; // 字 0 = 12，整像形式可查边界顺序
    localparam logic [19:0] SAMPLE = 20'h95E2B;        // DA/DB 四个向量均有区别
    localparam logic [13:0] SAMPLE_ADDR = 14'h3002;    // 银行 3，银行内字地址 2

    // --- 电源与命令 ---
    logic clk_aon_i = 1'b0, clk_core_i = 1'b0, clk_io_i = 1'b0;
    logic run_core = 1'b1;               // 测试往返互锁时可在低相保持核心停钟
    logic por_d_ok_async_i = 1'b0, vdd33_ok_async_i = 1'b0, ref_ok_async_i = 1'b0;
    logic iovdd_ok_async_i = 1'b0, ana_ready_async_i = 1'b0;
    logic sw_reset_req_aon_i = 1'b0, sw_reset_accept_aon_o;
    logic clients_idle_aon_i = 1'b1, capture_incomplete_aon_i = 1'b0;
    logic mode_req_valid_aon_i = 1'b0, mode_req_accept_aon_o;
    logic [1:0] mode_req_aon_i = 2'b00, operating_mode_aon_o;
    logic wake_recover_done_aon_i = 1'b0, wake_busy_aon_o, wake_recover_req_aon_o;
    logic por_ana_event_aon_o;
    int por_event_count = 0;
    logic cfg_hw_reset_no, core_reset_no, fifo_reset_no, io_reset_no, cfg_init_done_aon_o;
    logic conversion_allow_aon_o, conversion_allow_core_o;

    // --- OTP 待回应事务 ---
    logic otp_power_good_aon_i = 1'b1;  // 台架单独控制，与模拟参考互不依赖
    logic otp_adapters_quiescent_aon_i = 1'b0, otp_adapters_cancel_aon_o;
    logic otp_req_valid_aon_o, otp_req_ready_aon_i = 1'b0;
    logic [1:0] otp_req_addr_aon_o;
    logic otp_rsp_valid_aon_i = 1'b0, otp_rsp_ready_aon_o;
    logic [7:0] otp_rsp_data_aon_i = '0;
    logic otp_verify_req_valid_aon_o, otp_verify_req_ready_aon_i = 1'b0;
    logic [23:0] otp_verify_image_aon_o;
    logic otp_verify_rsp_valid_aon_i = 1'b0, otp_verify_rsp_ready_aon_o;
    logic otp_verify_rsp_pass_aon_i = 1'b0;
    logic otp_shadow_valid_aon_o;
    logic [23:0] otp_shadow_image_aon_o;
    logic [3:0] otp_error_code_aon_o;

    // --- 功能单口、测试和 IO 观察 ---
    logic bist_mode_req_aon_i = 1'b0, bist_mode_ready_aon_o, bist_mode_grant_aon_o;
    logic bist_func_hold_core_o, bist_func_quiesced_core_i = 1'b0;
    logic bist_start_core_i = 1'b0, bist_start_ready_core_o;
    logic bist_func_reinit_done_core_i = 1'b0, bist_contents_invalid_core_o;
    logic bist_busy_core_o, bist_done_core_o;
    logic [1:0] bist_fail_reason_core_o;
    logic result_req_valid_core_i = 1'b0, result_req_ready_core_o;
    logic result_write_core_i = 1'b1, result_read_valid_core_o;
    logic [13:0] result_addr_core_i = '0;
    logic [19:0] result_write_data_core_i = '0, result_read_data_core_o, last_read_word;
    logic io_word_valid_i = 1'b0, io_word_ready_o, io_edge_words_valid_o;
    logic [19:0] io_sample_i = '0;
    logic io_dual_lane_i = 1'b0, io_intf_check_i = 1'b0;
    logic [9:0] io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o;
    logic [3:0] io_slot_count_o;

    always #50ns clk_aon_i = ~clk_aon_i;
    always #7ns  clk_core_i = run_core && !clk_core_i;
    always #9ns  clk_io_i = ~clk_io_i;

    ad4080_peripheral_services #(
        .AON_MAX_CLK_HZ(10_000_000), .OTP_IMAGE_WORDS(3), .OTP_WORD_WIDTH(8), .OTP_TIMEOUT_CYCLES(24)
    ) dut (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i(1'b1),
        .por_d_ok_async_i, .vdd33_ok_async_i, .ref_ok_async_i, .iovdd_ok_async_i, .ana_ready_async_i,
        .clients_idle_aon_i, .capture_incomplete_aon_i, .sw_reset_req_aon_i, .mode_req_valid_aon_i,
        .mode_req_aon_i, .wake_recover_done_aon_i,
        .cfg_hw_reset_no, .core_reset_no, .fifo_reset_no, .io_reset_no, .ana_reset_no(),
        .cfg_init_done_aon_o, .cfg_control_write_allow_aon_o(), .cfg_full_access_allow_aon_o(),
        .operating_mode_aon_o, .wake_busy_aon_o, .wake_recover_req_aon_o,
        .mode_req_accept_aon_o, .sw_reset_accept_aon_o, .isolate_io_o(), .iovdd_ldo_en_o(), .ref_cmo_en_o(),
        .por_ana_event_aon_o,
        .conversion_allow_aon_o, .conversion_allow_core_o,
        .otp_power_good_aon_i, .otp_adapters_quiescent_aon_i, .otp_adapters_cancel_aon_o,
        .otp_req_valid_aon_o, .otp_req_ready_aon_i, .otp_req_addr_aon_o,
        .otp_rsp_valid_aon_i, .otp_rsp_ready_aon_o, .otp_rsp_data_aon_i, .otp_rsp_error_aon_i(1'b0),
        .otp_verify_req_valid_aon_o, .otp_verify_req_ready_aon_i, .otp_verify_image_aon_o,
        .otp_verify_rsp_valid_aon_i, .otp_verify_rsp_ready_aon_o, .otp_verify_rsp_pass_aon_i,
        .otp_busy_aon_o(), .otp_shadow_valid_aon_o, .otp_shadow_image_aon_o, .otp_error_code_aon_o,
        .bist_mode_req_aon_i, .bist_mode_ready_aon_o, .bist_mode_grant_aon_o,
        .bist_func_hold_core_o, .bist_func_quiesced_core_i, .bist_start_core_i, .bist_start_ready_core_o,
        .bist_checkerboard_core_i(1'b0), .bist_func_reinit_done_core_i, .bist_contents_invalid_core_o,
        .bist_busy_core_o, .bist_done_core_o, .bist_fail_reason_core_o,
        .bist_fail_addr_valid_core_o(), .bist_fail_addr_core_o(), .bist_fail_expected_core_o(), .bist_fail_actual_core_o(),
        .result_req_valid_core_i, .result_req_ready_core_o, .result_write_core_i, .result_addr_core_i,
        .result_write_data_core_i, .result_read_data_core_o, .result_read_valid_core_o,
        .io_word_valid_i, .io_word_ready_o, .io_sample_i, .io_dual_lane_i, .io_intf_check_i,
        .io_edge_words_valid_o, .io_da_fall_o, .io_da_rise_o, .io_db_fall_o, .io_db_rise_o, .io_slot_count_o
    );

    // =========================================================================
    // 2. 手工握手与跨域观察
    // =========================================================================
    // [拒绝发布] 任何 AON 上升沿后的未提交状态都必须遮住镜像、两域转换
    // 和 I/O 字。监控覆盖 OTP 字间、校验等待和各轮冷复位。
    always @(posedge clk_aon_i) begin
        #1ps;
        if (por_ana_event_aon_o) por_event_count++;
        if (!otp_shadow_valid_aon_o && (otp_shadow_image_aon_o != '0 || conversion_allow_aon_o ||
                                       conversion_allow_core_o || io_word_ready_o))
            $fatal(1, "Uncommitted OTP enabled a publisher");
    end

    // [冲刷] 这些 ready 由台架逐拍确认。请求从逻辑字 0 开始，给后续响应
    // 提供对应数据。校验命令只允许在三字齐全后提取原子快照。
    task automatic load_to_verifier();
        wait (cfg_init_done_aon_o);
        repeat (3) @(negedge clk_aon_i);
        if (!otp_adapters_cancel_aon_o || otp_req_valid_aon_o)
            $fatal(1, "Adapter flush was bypassed");
        otp_adapters_quiescent_aon_i = 1'b1;
        for (int idx = 0; idx < 3; idx++) begin
            wait (otp_req_valid_aon_o);
            @(negedge clk_aon_i);
            otp_req_ready_aon_i = 1'b1;
            @(posedge clk_aon_i);
            if (!(otp_req_valid_aon_o && otp_req_addr_aon_o == 2'(idx)))
                $fatal(1, "OTP request address did not follow word order");
            @(negedge clk_aon_i);
            otp_req_ready_aon_i = 1'b0;
            otp_rsp_data_aon_i = TEST_IMAGE[8*idx +: 8];
            otp_rsp_valid_aon_i = 1'b1;
            @(posedge clk_aon_i);
            if (!otp_rsp_ready_aon_o) $fatal(1, "OTP response not admitted for outstanding request");
            @(negedge clk_aon_i);
            otp_rsp_valid_aon_i = 1'b0;
        end
        wait (otp_verify_req_valid_aon_o);
        @(negedge clk_aon_i);
        otp_verify_req_ready_aon_i = 1'b1;
        @(posedge clk_aon_i);
        if (!otp_verify_req_valid_aon_o || otp_verify_image_aon_o != TEST_IMAGE || otp_shadow_valid_aon_o)
            $fatal(1, "Checker did not receive the complete unpublished image");
        @(negedge clk_aon_i);
        otp_verify_req_ready_aon_i = 1'b0;
    endtask

    // [提交] 只有完整校验请求后的应答才获准。等待应答前写口保持阻塞，
    // 避免有电但尚无有效 OTP 时送入或输出结果。
    task automatic checker_verdict(input bit pass);
        @(negedge clk_aon_i);
        if (!otp_verify_rsp_ready_aon_o || conversion_allow_aon_o || result_req_ready_core_o)
            $fatal(1, "Illegal verifier window or unqualified result admission");
        otp_verify_rsp_pass_aon_i = pass;
        otp_verify_rsp_valid_aon_i = 1'b1;
        @(posedge clk_aon_i);
        @(negedge clk_aon_i);
        otp_verify_rsp_valid_aon_i = 1'b0;
    endtask

    // [读延时] 地址及类型在负沿设置，读沿之后核对本笔而非上一笔。写沿
    // 不能看到读返回。成功读取作为边沿映射输入的唯一台架数据源。
    task automatic access_result(input bit wr, input logic [13:0] addr, input logic [19:0] word);
        @(negedge clk_core_i);
        result_write_core_i = wr;
        result_addr_core_i = addr;
        result_write_data_core_i = word;
        result_req_valid_core_i = 1'b1;
        @(posedge clk_core_i);
        if (!result_req_ready_core_o) $fatal(1, "Expected result port access was blocked");
        #1ps;
        if (result_read_valid_core_o == wr || (!wr && result_read_data_core_o != word))
            $fatal(1, "Unexpected result valid or bank data");
        if (!wr) last_read_word = result_read_data_core_o;
        @(negedge clk_core_i);
        result_req_valid_core_i = 1'b0;
    endtask

    // [位序] 期望向量由独立的逐位字序给出。单 lane 的 DB 恒零，
    // 双 lane 用五槽且高五槽全零；协议模式与完整字在 IO 同沿采纳。
    task automatic expect_io_words(
        input bit dual_lane, input bit check_pattern,
        input logic [9:0] da_f, da_r, db_f, db_r
    );
        @(negedge clk_io_i);
        io_word_valid_i = 1'b1;
        io_sample_i = last_read_word;
        io_dual_lane_i = dual_lane;
        io_intf_check_i = check_pattern;
        @(posedge clk_io_i);
        if (!io_word_ready_o || !io_edge_words_valid_o || io_slot_count_o != (dual_lane ? 4'd5 : 4'd10) ||
            {io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o} != {da_f, da_r, db_f, db_r})
            $fatal(1, "I/O edge vectors are not in expected DA/DB order");
        @(negedge clk_io_i);
        io_word_valid_i = 1'b0;
    endtask

    // =========================================================================
    // 3. 场景与证据点
    // =========================================================================
    // [控制] 在真实受理沿采样，避免 NBA 后状态已换导致误读确认。
    task automatic request_mode(input logic [1:0] mode, input bit accepted);
        @(negedge clk_aon_i);
        mode_req_aon_i = mode;
        mode_req_valid_aon_i = 1'b1;
        @(posedge clk_aon_i);
        if (mode_req_accept_aon_o != accepted) $fatal(1, "Unexpected AON mode acceptance");
        @(negedge clk_aon_i);
        mode_req_valid_aon_i = 1'b0;
    endtask

    initial begin
        int por_events_before_reference;
        // [冷启动] 验证 OTP 启动独立于模拟参考和 IO。随后让模拟与核心合格，
        // 特意将通过判决留到供电许可之后，明确是谁封住转换。
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b1;
        load_to_verifier();
        if (conversion_allow_aon_o || io_reset_no) $fatal(1, "Rails unexpectedly ready in OTP-only startup");
        vdd33_ok_async_i = 1'b1;
        ref_ok_async_i = 1'b1;
        iovdd_ok_async_i = 1'b1;
        ana_ready_async_i = 1'b1;
        wait (dut.pwr_conversion_allow_aon && io_reset_no);
        checker_verdict(1'b1);
        wait (conversion_allow_core_o && io_word_ready_o);
        if (!otp_shadow_valid_aon_o || otp_shadow_image_aon_o != TEST_IMAGE)
            $fatal(1, "Conversion opened without a complete OTP commit");
        $display("PASS POR -> OTP image %06h -> conversion, OTP loaded before REFIN and IOVDD", otp_shadow_image_aon_o);

        access_result(1'b1, SAMPLE_ADDR, SAMPLE);
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        expect_io_words(1'b0, 1'b0, 10'h3B1, 10'h21E, 10'h000, 10'h000);
        expect_io_words(1'b1, 1'b0, 10'h015, 10'h01C, 10'h006, 10'h013);
        expect_io_words(1'b1, 1'b1, 10'h00B, 10'h011, 10'h01E, 10'h00C);
        $display("PASS SRAM bank 3 result %05h, single/dual-lane words and interface check", last_read_word);

        // [记录] 两个 SRAM 访问之间总线可以空闲，记录状态仍必须独立鉴别。
        // 同沿低功耗、软复位及测试请求都不得停住尚未完成的采集。
        // 台架先放置已完成记录的样本，再用独立上游状态模拟下一次采集，
        // 让“已有读数据”和“当前记录仍未完”可以同时成立。
        // 先单独用保留模式码检验译码拒绝，再换成合法的待机码检验上游联锁。
        // 三个同沿请求都拒绝时，新转换许可仍须为高，否则未完记录无法自行完成。
        request_mode(2'b01, 1'b0);
        @(negedge clk_aon_i);
        capture_incomplete_aon_i = 1'b1;
        mode_req_aon_i = 2'b10;
        mode_req_valid_aon_i = 1'b1;
        sw_reset_req_aon_i = 1'b1;
        bist_mode_req_aon_i = 1'b1;
        @(posedge clk_aon_i);
        if (mode_req_accept_aon_o || sw_reset_accept_aon_o || bist_mode_ready_aon_o || !conversion_allow_aon_o)
            $fatal(1, "Incomplete capture admitted reset/mode/BIST or could no longer finish");
        @(negedge clk_aon_i);
        capture_incomplete_aon_i = 1'b0;
        mode_req_valid_aon_i = 1'b0;
        bist_mode_req_aon_i = 1'b0;
        // 记录标志撤销后仍保持软复位请求，单独检查普通客户忙也能挡住受理。
        // 下一合法待机请求开始前，两个 AON 准入条件才都恢复为空闲。
        clients_idle_aon_i = 1'b0;
        @(posedge clk_aon_i);
        if (sw_reset_accept_aon_o) $fatal(1, "Active client allowed software reset");
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b0;
        clients_idle_aon_i = 1'b1;
        $display("PASS illegal mode and incomplete-capture reset/mode/BIST blocked without stopping capture");

        // [模式] 已完成记录可保留。外供时独立 SRAM 读许可保留，LVDS 与
        // 新转换关闭。尚未建立新恢复时，提前存在的 done 不可放行启动。
        request_mode(2'b10, 1'b1);
        if (operating_mode_aon_o != 2'b10 || conversion_allow_core_o || io_word_ready_o || !fifo_reset_no)
            $fatal(1, "Standby changed the retained record boundary");
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        @(negedge clk_aon_i);
        wake_recover_done_aon_i = 1'b1;
        request_mode(2'b00, 1'b1);
        // 10 MHz 下等待 1010 拍跨过 100 us 待机唤醒计数。若此时开放转换，
        // 表明主管把命令之前已经为高的完成位当成了本轮恢复应答。
        repeat (1010) @(negedge clk_aon_i);
        if (!wake_busy_aon_o || wake_recover_req_aon_o || conversion_allow_aon_o)
            $fatal(1, "Stale wake acknowledgement released a new conversion");
        wake_recover_done_aon_i = 1'b0;
        wait (wake_recover_req_aon_o);
        @(negedge clk_aon_i);
        if (conversion_allow_core_o) $fatal(1, "Wake released before external confirmation");
        wake_recover_done_aon_i = 1'b1;
        wait (conversion_allow_core_o && io_word_ready_o);
        @(negedge clk_aon_i);
        wake_recover_done_aon_i = 1'b0;
        $display("PASS finished record retained in standby; stale/no wake acknowledgement held conversion closed");

        // [软复位] 确认初始化再次关闭转换，但硬复位域和 OTP 仍保存状态。
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b1;
        @(posedge clk_aon_i);
        if (!sw_reset_accept_aon_o) $fatal(1, "Idle soft reset not accepted");
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b0;
        if (core_reset_no || !fifo_reset_no || !otp_shadow_valid_aon_o || cfg_init_done_aon_o)
            $fatal(1, "Soft reset crossed into retained domains");
        sw_reset_req_aon_i = 1'b1;
        @(posedge clk_aon_i);
        if (sw_reset_accept_aon_o) $fatal(1, "Second reset accepted during initialization no-ready window");
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b0;
        wait (conversion_allow_core_o && io_word_ready_o);
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        $display("PASS soft reset preserves OTP and retained SRAM result");

        // [参考失效] 异步撤销新写事务。已提交 OTP 和外供 IOVDD 保持，
        // 已存结果按独立读许可可取出。
        @(negedge clk_core_i);
        por_events_before_reference = por_event_count;
        result_write_core_i = 1'b1;
        ref_ok_async_i = 1'b0;
        #1ns;
        if (conversion_allow_aon_o || conversion_allow_core_o || result_req_ready_core_o || !otp_shadow_valid_aon_o)
            $fatal(1, "Reference collapse failed to contain result writes");
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        ref_ok_async_i = 1'b1;
        wait (conversion_allow_core_o);
        repeat (5) @(negedge clk_aon_i);
        if (por_event_count != por_events_before_reference+1) $fatal(1, "Expected one AON reference fault event");
        $display("PASS REFIN loss freezes new writes and retains prior readable result");

        // [I/O 失效] 模拟与核心电源保持。外供 IOVDD 被撤销后，仍可按许可
        // 生产结果，现有读和全部边沿字立即关闭，复电待目标域重新释放。
        @(negedge clk_io_i);
        io_word_valid_i = 1'b1;
        result_write_core_i = 1'b0;
        iovdd_ok_async_i = 1'b0;
        #1ns;
        if (io_reset_no || io_edge_words_valid_o || result_req_ready_core_o || !conversion_allow_core_o)
            $fatal(1, "IOVDD collapse affected the wrong service boundary");
        // 上游在接口复位时清除曾经有效的字。复电后重新读已存载荷，以辨
        // 单纯 I/O 停止和 FIFO 保留电源失效。
        io_word_valid_i = 1'b0;
        iovdd_ok_async_i = 1'b1;
        wait (io_word_ready_o && result_req_ready_core_o);
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        $display("PASS IOVDD loss blocks reads and edge words, keeps conversion and retained result");

        // [AON 同沿] 无未完记录时测试申请优先于软复位和模式。故意在 hold
        // 已到核心但 grant 尚未返回时请求启动，确认 SRAM 始终无破坏性访问。
        @(negedge clk_aon_i);
        bist_mode_req_aon_i = 1'b1;
        sw_reset_req_aon_i = 1'b1;
        mode_req_valid_aon_i = 1'b1;
        mode_req_aon_i = 2'b10;
        @(posedge clk_aon_i);
        if (sw_reset_accept_aon_o || mode_req_accept_aon_o) $fatal(1, "BIST priority overlapped configuration commands");
        wait (bist_func_hold_core_o);
        @(negedge clk_core_i);
        bist_func_quiesced_core_i = 1'b1;
        bist_start_core_i = 1'b1;
        // 核心沿前查准入，沿后再查作废状态。两项分别抓到提前 ready 与
        // 忽略 ready 采到 start 的隐蔽破坏路径。
        @(posedge clk_core_i);
        if (bist_mode_grant_aon_o || bist_start_ready_core_o) $fatal(1, "BIST start ready during ENTER only");
        #1ps;
        if (bist_contents_invalid_core_o) $fatal(1, "Early BIST start invalidated memory");
        @(negedge clk_core_i);
        bist_start_core_i = 1'b0;
        bist_func_quiesced_core_i = 1'b0;
        @(negedge clk_aon_i);
        mode_req_valid_aon_i = 1'b0;
        sw_reset_req_aon_i = 1'b0;
        bist_mode_req_aon_i = 1'b0;
        wait (bist_mode_ready_aon_o && conversion_allow_core_o);
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        $display("PASS BIST hold before grant is harmless and excludes same-edge reset/mode");

        // [短请求与停钟] AON 已受理后，核心停钟时不得提前恢复。恢复钟后
        // 送达保持与退出回执，无 start 也无 SRAM 内容作废。
        @(negedge clk_core_i);
        run_core = 1'b0;
        @(negedge clk_aon_i);
        bist_mode_req_aon_i = 1'b1;
        @(negedge clk_aon_i);
        bist_mode_req_aon_i = 1'b0;
        repeat (3) @(negedge clk_aon_i);
        if (bist_mode_ready_aon_o || conversion_allow_core_o || bist_mode_grant_aon_o)
            $fatal(1, "AON left test transition while core clock was stopped");
        run_core = 1'b1;
        wait (bist_func_hold_core_o);
        wait (bist_mode_ready_aon_o && conversion_allow_core_o);
        if (bist_contents_invalid_core_o) $fatal(1, "No-start session devalidated the SRAM");
        $display("PASS BIST request survives stopped core clock and short AON pulse");

        // [中止] 观察实际 MBIST 读响应，测试流量须留在物理口。撤销测试后
        // 错误码应为 3，恢复须由外部对已经被覆写的记录元数据确认。
        // 测试使用 bank 0 的新结果验证恢复后的重新填充，旧数据未被当作
        // 可消费载荷；功能 IP 负责持有重建前后对应的指针和有效边界。
        @(negedge clk_aon_i);
        bist_mode_req_aon_i = 1'b1;
        wait (bist_mode_grant_aon_o);
        @(negedge clk_core_i);
        bist_func_quiesced_core_i = 1'b1;
        // 故意让声明的静默和实际 valid 相冲突，确认控制器以实口为准。
        // held 请求没有 ready，测试台之后明确撤销，避免意外消费。
        result_req_valid_core_i = 1'b1;
        result_write_core_i = 1'b0;
        repeat (3) @(negedge clk_core_i);
        if (bist_start_ready_core_o || result_req_ready_core_o) $fatal(1, "Held valid was mistaken for test quiescence");
        result_req_valid_core_i = 1'b0;
        wait (bist_start_ready_core_o);
        @(negedge clk_core_i);
        bist_start_core_i = 1'b1;
        @(posedge clk_core_i);
        if (!bist_start_ready_core_o) $fatal(1, "BIST could not enter an idle single port");
        @(negedge clk_core_i);
        bist_start_core_i = 1'b0;
        wait (dut.store_read_valid);
        @(negedge clk_core_i);
        io_word_valid_i = 1'b1;
        #1ps;
        if (!bist_busy_core_o || result_read_valid_core_o || result_req_ready_core_o || io_edge_words_valid_o)
            $fatal(1, "BIST operation leaked onto a functional port");
        io_word_valid_i = 1'b0;
        @(negedge clk_aon_i);
        bist_mode_req_aon_i = 1'b0;
        wait (bist_done_core_o);
        repeat (3) @(negedge clk_aon_i);
        if (bist_fail_reason_core_o != 2'd3 || !bist_contents_invalid_core_o || !bist_func_hold_core_o ||
            bist_mode_ready_aon_o || conversion_allow_aon_o || result_req_ready_core_o)
            $fatal(1, "Aborted destructive BIST released invalid memory");
        // 重建完成与旧功能 valid 故意同时送达。若仲裁只看门控后的有效，
        // 第一拍可能误解除作废，下一拍便把旧地址送入刚移交的 SRAM。
        // 观察窗同时查内容作废、请求 ready 和读返回，覆盖状态与实际访问两侧。
        @(negedge clk_core_i);
        result_req_valid_core_i = 1'b1;
        bist_func_reinit_done_core_i = 1'b1;
        repeat (3) @(negedge clk_core_i);
        if (!bist_contents_invalid_core_o || result_read_valid_core_o || result_req_ready_core_o)
            $fatal(1, "Recovery reopened an old held SRAM request");
        result_req_valid_core_i = 1'b0;
        wait (bist_mode_ready_aon_o && conversion_allow_core_o);
        @(negedge clk_core_i);
        bist_func_reinit_done_core_i = 1'b0;
        bist_func_quiesced_core_i = 1'b0;
        access_result(1'b1, 14'h0000, 20'h31A62);
        access_result(1'b0, 14'h0000, 20'h31A62);
        $display("PASS MBIST exclusive read, abort reason %0d, metadata reinit and new result", bist_fail_reason_core_o);

        // [宏掉电] AON 域独立宏资格撤销时立即遮蔽影子；错误在下一沿锁定。
        @(negedge clk_aon_i);
        result_write_core_i = 1'b1;
        io_word_valid_i = 1'b1;
        otp_power_good_aon_i = 1'b0;
        #1ns;
        if (otp_shadow_valid_aon_o || result_req_ready_core_o || io_edge_words_valid_o || !otp_adapters_cancel_aon_o)
            $fatal(1, "OTP supply loss failed to revoke publication");
        @(negedge clk_aon_i);
        if (otp_error_code_aon_o != 4'd1) $fatal(1, "OTP supply loss not latched");
        io_word_valid_i = 1'b0;
        $display("PASS OTP macro supply loss revoked shadow, write and edge validity");

        // [硬 POR] 返回一次新的好判决时拉低 POR，确保同一窗口无提交。
        // 宏和校验输入只在收到取消后撤销旧 valid，下一轮重新做冲刷。
        por_d_ok_async_i = 1'b0;
        otp_adapters_quiescent_aon_i = 1'b0;
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b1;
        otp_power_good_aon_i = 1'b1;
        load_to_verifier();
        @(negedge clk_aon_i);
        if (!otp_verify_rsp_ready_aon_o) $fatal(1, "Wanted in-flight checker reply before POR");
        otp_verify_rsp_valid_aon_i = 1'b1;
        otp_verify_rsp_pass_aon_i = 1'b1;
        por_d_ok_async_i = 1'b0;
        #1ns;
        if (otp_shadow_valid_aon_o || cfg_hw_reset_no || fifo_reset_no || result_req_ready_core_o ||
            otp_verify_rsp_ready_aon_o || !otp_adapters_cancel_aon_o)
            $fatal(1, "POR did not win over pending OTP check reply");
        @(negedge clk_aon_i);
        otp_verify_rsp_valid_aon_i = 1'b0;
        otp_adapters_quiescent_aon_i = 1'b0;
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b1;
        $display("PASS hard POR suppressed in-flight passing OTP reply");

        load_to_verifier();
        checker_verdict(1'b0);
        repeat (3) @(negedge clk_aon_i);
        if (otp_error_code_aon_o != 4'd3 || otp_shadow_valid_aon_o || otp_shadow_image_aon_o != '0 ||
            conversion_allow_aon_o || conversion_allow_core_o || result_req_ready_core_o || io_word_ready_o)
            $fatal(1, "Rejected OTP image enabled a publisher");
        $display("PASS checker-rejected OTP result stays quarantined, error %0d", otp_error_code_aon_o);
        $display("PERIPHERAL_SERVICES PASS");
        $finish;
    end

    // [退出] 等待中的 CDC 或适配器不推进时，给出可定位的阶段超时报错。
    initial begin
        #3ms;
        $fatal(1, "Peripheral integration watchdog expired");
    end
endmodule

`default_nettype wire
