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
    logic run_io = 1'b1;                 // 独立停钟以确认 I/O 恢复须等待目的时钟
    logic por_d_ok_async_i = 1'b0, vdd33_ok_async_i = 1'b0, ref_ok_async_i = 1'b0;
    logic iovdd_ok_async_i = 1'b0, ana_ready_async_i = 1'b0;
    logic sw_reset_req_aon_i = 1'b0, sw_reset_accept_aon_o;
    logic clients_idle_aon_i = 1'b1, capture_incomplete_aon_i = 1'b0;
    logic mode_req_valid_aon_i = 1'b0, mode_req_accept_aon_o;
    logic [1:0] mode_req_aon_i = 2'b00, operating_mode_aon_o;
    logic wake_recover_done_aon_i = 1'b0, wake_busy_aon_o, wake_recover_req_aon_o;
    logic por_ana_event_aon_o;
    logic por_ana_event_core_o;
    int por_event_count = 0;
    int por_core_consumed = 0;            // 按 CSR 消费沿计数，停钟不等于事件清除
    // 以观察到的 AON 上升沿编号量参考故障交付，不从模拟阈值推周期。
    int aon_edge_count = 0;
    int last_por_event_edge;
    realtime last_por_event_at_ns;
    realtime mode_accept_at_ns;
    logic cfg_hw_reset_no, core_reset_no, fifo_reset_no, io_reset_no, cfg_init_done_aon_o;
    logic conversion_allow_aon_o, conversion_allow_core_o;
    // 只检查服务真实 AON 消费和回执保持；SPI 实体由独立产品台架生成。
    logic pwr_cmd_valid_core_i = 1'b0, pwr_cmd_ready_core_o, pwr_rsp_valid_core_o, pwr_rsp_accept_core_o;
    logic pwr_cmd_evaluate_aon_i;         // 模拟最后数据位已到、CS 仍未完成退场
    logic [1:0] pwr_cmd_op_core_i = 2'b00, pwr_cmd_value_core_i = 2'b00;
    logic [1:0] pwr_rsp_op_core_o, pwr_mode_core_o;

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
    always #9ns  clk_io_i = run_io && !clk_io_i;

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
        .por_ana_event_aon_o, .por_ana_event_core_o,
        .pwr_cmd_valid_core_i, .pwr_cmd_op_core_i, .pwr_cmd_value_core_i,
        .pwr_cmd_evaluate_aon_i,
        .pwr_cmd_ready_core_o, .pwr_rsp_valid_core_o, .pwr_rsp_accept_core_o,
        .pwr_rsp_op_core_o, .pwr_mode_core_o, .pwr_ldo_pd_core_o(), .vdd11_ldo_en_o(),
        .fifo_record_allow_io_o(), .cfg_poll_allow_aon_o(),
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
        // #1ps 置于寄存器更新之后，读到的是该沿实际发布到 CSR 的事件。
        #1ps;
        aon_edge_count++;
        if (por_ana_event_aon_o) begin
            por_event_count++;
            last_por_event_edge = aon_edge_count;
            last_por_event_at_ns = $realtime;
        end
        if (!otp_shadow_valid_aon_o && (otp_shadow_image_aon_o != '0 || conversion_allow_aon_o ||
                                       conversion_allow_core_o || io_word_ready_o))
            $fatal(1, "Uncommitted OTP enabled a publisher");
    end

    // 与实际 CSR 同样在核心沿采样。不能等 NBA 后再数，否则读到的是确认后的电平。
    always @(posedge clk_core_i) begin
        if (fifo_reset_no && por_ana_event_core_o) por_core_consumed++;
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
        // 从接受命令的沿起算，不能从后续撤回 valid 的负沿量唤醒守卫。
        if (mode_req_accept_aon_o) mode_accept_at_ns = $realtime;
        @(negedge clk_aon_i);
        mode_req_valid_aon_i = 1'b0;
    endtask

    // [模式] 始终送同一固定字请求。低功耗要同时封新转换和 LVDS，退出
    // 后须经历完整守卫和本轮附加恢复。done 的刻意延迟只是台架激励。
    task automatic check_pattern_wake(input logic [1:0] low_mode, input int guard_cycles);
        realtime start_at_ns, recover_request_at_ns;
        request_mode(low_mode, 1'b1);
        if (io_word_ready_o || io_edge_words_valid_o || conversion_allow_aon_o)
            $fatal(1, "Low-power mode %b left conversion or fixed word enabled", low_mode);
        request_mode(2'b00, 1'b1);
        start_at_ns = mode_accept_at_ns;
        // 扫完整守卫窗口，连结束沿一起检查。只比首末两点会漏掉中间短放行。
        // AON 的恢复请求要先看见一次低 done，守卫结束沿仍不应产生请求。
        repeat (guard_cycles) begin
            @(posedge clk_aon_i);
            #2ps;
            if (wake_recover_req_aon_o || conversion_allow_aon_o || io_edge_words_valid_o)
                $fatal(1, "Mode %b released before wake guard and recovery handshake", low_mode);
        end
        @(posedge clk_aon_i);
        #2ps;
        if (!wake_recover_req_aon_o || conversion_allow_aon_o || io_edge_words_valid_o)
            $fatal(1, "Mode %b did not request additional recovery on guard+1", low_mode);
        recover_request_at_ns = $realtime;
        // 故意让外部握手停顿，验证 t_wake-up 用完后也不会自行发字。
        // 下一个负沿再给 done，便于与目的域恢复延迟分开读数。
        repeat (3) @(negedge clk_aon_i);
        if (!wake_busy_aon_o || conversion_allow_aon_o || io_edge_words_valid_o)
            $fatal(1, "Mode %b emitted conversion or fixed word while waiting for fresh done", low_mode);
        wake_recover_done_aon_i = 1'b1;
        wait (conversion_allow_core_o && io_edge_words_valid_o);
        $display("PASS REFIN_WAKE mode=%b recover_req_AON=%0d dt=%0.3f ns test_and_conversion_release_dt=%0.3f ns",
                 low_mode, guard_cycles+1, recover_request_at_ns-start_at_ns, $realtime-start_at_ns);
        @(negedge clk_aon_i);
        wake_recover_done_aon_i = 1'b0;
    endtask

    // [状态交付] 短失效完全在 AON 两沿之间，仍由真实参考监视链捕获。
    // 再保持一次低压直到核心恢复，覆盖在途请求及次级待发位。
    task automatic check_por_stop_and_restart();
        int aon_before, core_before;
        repeat (6) @(negedge clk_aon_i); // 隔开前序场景的确认返回延迟。
        @(negedge clk_core_i);
        run_core = 1'b0;
        // I/O 掉电可关 Pad/读回，但核心及 AON 还由 VDD11 供电，事件交付应独立。
        iovdd_ok_async_i = 1'b0;
        aon_before = por_event_count;
        core_before = por_core_consumed;
        @(negedge clk_aon_i);
        #1ns;
        ref_ok_async_i = 1'b0;
        #6ns;
        ref_ok_async_i = 1'b1;
        wait (por_event_count == aon_before + 1);
        repeat (5) @(negedge clk_aon_i);
        #1ns;
        ref_ok_async_i = 1'b0;
        wait (por_event_count == aon_before + 2);
        repeat (7) @(negedge clk_aon_i);
        if (por_core_consumed != core_before || por_ana_event_core_o ||
            por_event_count != aon_before + 2)
            $fatal(1, "POR delivery escaped stopped core or duplicated a continuous monitor fault");

        // 两级请求同步后，第三沿才由实际接收方取走本笔。随后返回的确认
        // 再释放第二笔，不依赖对端采到某个指定的原始脉冲宽度。
        run_core = 1'b1;
        repeat (2) begin
            @(posedge clk_core_i);
            #2ps;
            if (por_core_consumed != core_before) $fatal(1, "POR consumed before request synchronizer settled");
        end
        @(posedge clk_core_i);
        #2ps;
        if (por_core_consumed != core_before + 1) $fatal(1, "Stopped core lost first POR event");
        wait (por_core_consumed == core_before + 2);
        repeat (6) @(negedge clk_aon_i);
        // 两笔交付时 Pad 仍关闭，而且参考持续低。恢复前不能出现第三笔。
        if (por_core_consumed != core_before + 2 || por_event_count != aon_before + 2 || io_reset_no)
            $fatal(1, "Unexpected POR repeat or I/O still live without IOVDD");
        $display("PASS POR_ANA_CDC stopped core and IOVDD off, 6 ns pulse plus continuous fault, first on core edge 3 and two arrivals");
        iovdd_ok_async_i = 1'b1;
        ref_ok_async_i = 1'b1;
        repeat (6) @(negedge clk_aon_i);

        // [换代] 硬 POR 取消尚未交付的旧事件。等待已存在的 AON 事件完成登记
        // 再复位，确保不是一个尚未被监视器看到的空试验。
        @(negedge clk_core_i);
        run_core = 1'b0;
        aon_before = por_event_count;
        core_before = por_core_consumed;
        @(negedge clk_aon_i);
        #1ns;
        ref_ok_async_i = 1'b0;
        #6ns;
        ref_ok_async_i = 1'b1;
        wait (por_event_count == aon_before + 1);
        repeat (3) @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b0;
        #2ps;
        if (fifo_reset_no || por_ana_event_core_o) $fatal(1, "Hard POR did not revoke pending delivery");
        repeat (2) @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b1;
        run_core = 1'b1;
        // 留足两端释放和新的同步沿，重启后比对的是一次完整的新复位会话。
        repeat (8) @(negedge clk_aon_i);
        if (!fifo_reset_no || por_core_consumed != core_before || por_event_count != aon_before + 1)
            $fatal(1, "Old POR check event leaked into the next digital reset epoch");
        $display("PASS POR_ANA_CDC hard POR discarded a queued old event without a phantom event on restart");
    endtask

    // 请求先于 CS 退场到达 AON。即便停核心，CS 退场前保留请求，退场后
    // 只裁决一次。核心重新运行前，读取的仍是上次已收到的 AON 状态。
    task automatic check_power_reply_stopped_core();
        wait (pwr_cmd_ready_core_o);
        @(negedge clk_aon_i);
        pwr_cmd_evaluate_aon_i = 1'b0;
        @(negedge clk_core_i);
        pwr_cmd_value_core_i = 2'b10;
        pwr_cmd_valid_core_i = 1'b1;
        @(negedge clk_core_i);
        pwr_cmd_valid_core_i = 1'b0;
        run_core = 1'b0;
        // 等五个 AON 沿覆盖请求同步及任何误响应。若第一笔在退场前被
        // 弹出，即使核心停钟，主管仍会先进入待机并触发下面的模式比较。
        repeat (5) @(negedge clk_aon_i);
        if (operating_mode_aon_o != 2'b00 || pwr_cmd_ready_core_o)
            $fatal(1, "AON consumed a pending power command before CS release");
        pwr_cmd_evaluate_aon_i = 1'b1;
        wait (operating_mode_aon_o == 2'b10);
        // 主管已经执行，但核心仍停钟。此时比较旧读回可捕获误用 AON
        // 组合状态的写法，和启动后丢失一次回执是不同错误。
        repeat (5) @(negedge clk_aon_i);
        if (pwr_cmd_ready_core_o || pwr_rsp_valid_core_o || pwr_mode_core_o != 2'b00)
            $fatal(1, "Stopped core did not hold the in-flight power readback");
        run_core = 1'b1;
        wait (pwr_rsp_valid_core_o);
        if (!pwr_rsp_accept_core_o || pwr_rsp_op_core_o != 2'b00 || pwr_mode_core_o != 2'b10)
            $fatal(1, "Lost AON reply on core restart");
        $display("PASS AON_CFG_CDC true standby, retained response and coherent readback with core stopped");

        // 恢复正常再进入后续参考故障场景，避免无意改变它的供电前提。
        wait (pwr_cmd_ready_core_o);
        @(negedge clk_core_i);
        pwr_cmd_value_core_i = 2'b00;
        pwr_cmd_valid_core_i = 1'b1;
        @(negedge clk_core_i);
        pwr_cmd_valid_core_i = 1'b0;
        wait (pwr_rsp_valid_core_o);
        if (!pwr_rsp_accept_core_o || pwr_mode_core_o != 2'b00) $fatal(1, "AON wake response mismatch");
        wait (wake_recover_req_aon_o);
        @(negedge clk_aon_i);
        wake_recover_done_aon_i = 1'b1;
        wait (conversion_allow_core_o);
        @(negedge clk_aon_i);
        wake_recover_done_aon_i = 1'b0;

        // CS 完成只打开裁决沿。客户仍忙的候选应收到拒绝，客户后来空闲
        // 不得重放该旧命令，也不能出现迟到的第二次应答。
        clients_idle_aon_i = 1'b0;
        wait (pwr_cmd_ready_core_o);
        @(negedge clk_core_i);
        pwr_cmd_value_core_i = 2'b10;
        pwr_cmd_valid_core_i = 1'b1;
        @(negedge clk_core_i);
        pwr_cmd_valid_core_i = 1'b0;
        wait (pwr_rsp_valid_core_o);
        // 允许裁决仍须返回终态。单看模式未变，无法分辨明确拒绝和卡住。
        if (pwr_rsp_accept_core_o || pwr_mode_core_o != 2'b00)
            $fatal(1, "CS completion waived AON client-idle qualification");
        @(negedge clk_aon_i);
        clients_idle_aon_i = 1'b1;
        repeat (5) @(negedge clk_aon_i);
        if (operating_mode_aon_o != 2'b00 || !pwr_cmd_ready_core_o)
            $fatal(1, "Rejected AON command was replayed after clients became idle");
        $display("PASS AON_CFG_CDC CS release defers evaluation, busy rejects without replay");
    endtask

    initial begin
        int por_events_before_reference, aon_edge_before_reference;
        realtime ref_lost_at_ns, first_ref_at_ns, io_qualified_at_ns;
        // [冷启动] 验证 OTP 启动独立于模拟参考和 IO。随后让模拟与核心合格，
        // 特意将通过判决留到供电许可之后，明确是谁封住转换。
        @(negedge clk_aon_i);
        pwr_cmd_evaluate_aon_i = 1'b1;
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
        // 已存结果按独立读许可可取出；同一上电会话已确认过参考，固定字继续有效。
        @(negedge clk_io_i);
        io_word_valid_i = 1'b1;
        io_intf_check_i = 1'b1;
        io_dual_lane_i = 1'b1;
        @(negedge clk_core_i);
        #2ns; // 选两域时钟之间的时刻，直接观察撤销，无需等 AON 上升沿。
        por_events_before_reference = por_event_count;
        aon_edge_before_reference = aon_edge_count;
        result_write_core_i = 1'b1;
        ref_lost_at_ns = $realtime;
        ref_ok_async_i = 1'b0;
        // 只施加参考监视结果，不改变 OTP、IOVDD、模拟电路模型或字输入。
        #2ps;
        if (conversion_allow_aon_o || conversion_allow_core_o || result_req_ready_core_o || !otp_shadow_valid_aon_o)
            $fatal(1, "Reference collapse failed to contain result writes");
        if (!io_word_ready_o || !io_edge_words_valid_o ||
            {io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o} != {10'h00B, 10'h011, 10'h01E, 10'h00C})
            $fatal(1, "Qualified REFIN collapsed but fixed word was lost");
        $display("PASS REFIN_LOSS t=%0.3f ns ref=0 new_conversion=0 test_valid=1 DA=%h/%h DB=%h/%h",
                 ref_lost_at_ns, io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o);
        access_result(1'b0, SAMPLE_ADDR, SAMPLE);
        @(negedge clk_io_i);
        io_word_valid_i = 1'b0;
        ref_ok_async_i = 1'b1;
        wait (conversion_allow_core_o);
        repeat (5) @(negedge clk_aon_i);
        // 即使低脉冲已结束，已捕获的参考事件仍要在第 3 个 AON 沿送达一次。
        if (por_event_count != por_events_before_reference+1 || last_por_event_edge != aon_edge_before_reference+3)
            $fatal(1, "Expected one reference fault event on the third subsequent AON rising edge");
        $display("PASS POR_ANA_EVENT AON+%0d dt=%0.3f ns", last_por_event_edge-aon_edge_before_reference,
                 last_por_event_at_ns-ref_lost_at_ns);
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

        // [Rev. B p.24] 新的硬 POR 会话用正常的 OTP 接口提交，VDD33/IOVDD
        // 和 I/O 目的时钟均正常；唯一缺少的启动条件是真实监视器没确认过参考。
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b0;
        ref_ok_async_i = 1'b0;
        ana_ready_async_i = 1'b0;
        otp_adapters_quiescent_aon_i = 1'b0;
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1'b1;
        load_to_verifier();
        checker_verdict(1'b1);
        wait (io_reset_no);
        repeat (4) @(negedge clk_io_i);
        // 让其余 I/O 许可有充足释放时间，排除冷启只因普通同步器未完成的假通过。
        io_word_valid_i = 1'b1;
        io_intf_check_i = 1'b1;
        io_dual_lane_i = 1'b1;
        #1ps;
        if (!otp_shadow_valid_aon_o || !io_reset_no || conversion_allow_core_o)
            $fatal(1, "Cold reference negative setup did not isolate the reference condition");
        if (io_word_ready_o || io_edge_words_valid_o)
            $fatal(1, "Cold REFIN never qualified produced test word at %0.3f ns, ready=%b valid=%b DA=%h/%h DB=%h/%h",
                   $realtime, io_word_ready_o, io_edge_words_valid_o, io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o);
        $display("PASS REFIN_COLD ref=0 OTP_valid=1 IO_reset_released=1 ready=0 valid=0 t=%0.3f ns", $realtime);

        // [首次资格] 参考监控高在非时钟沿输入；经过两个同步沿及第三个
        // AON 历史登记沿，再观察 I/O 两拍放行。此时模拟 ready 仍未提供。
        @(negedge clk_aon_i);
        #1ns;
        first_ref_at_ns = $realtime;
        ref_ok_async_i = 1'b1;
        repeat (3) begin
            @(posedge clk_aon_i);
            #2ps;
            // AON 最后一拍更新历史时，字许可仍受 I/O 目的同步链约束。
            if (io_word_ready_o || io_edge_words_valid_o || conversion_allow_core_o)
                $fatal(1, "First REFIN qualification bypassed AON/IO releases");
        end
        for (int edge_index = 1; edge_index <= 2; edge_index++) begin
            @(posedge clk_io_i);
            #2ps;
            // 第一拍早放行和第二拍未释放都属于错误，完整字同步有效位也需同行。
            if (io_word_ready_o != (edge_index == 2) || io_edge_words_valid_o != (edge_index == 2))
                $fatal(1, "First REFIN test eligibility did not release on I/O edge 2");
        end
        if ({io_da_fall_o, io_da_rise_o, io_db_fall_o, io_db_rise_o} != {10'h00B, 10'h011, 10'h01E, 10'h00C})
            $fatal(1, "First reference qualification released incorrect fixed word");
        $display("PASS REFIN_FIRST AON=3 IO=2 dt=%0.3f ns test_valid=1 analog_ready=0", $realtime-first_ref_at_ns);
        ana_ready_async_i = 1'b1;
        wait (conversion_allow_core_o);

        // [停钟] IOVDD 掉压时已接受的固定字资格立即撤销。重供不能在
        // 目的时钟停住时交付，复钟后从实际许可请求起观察 I/O 两拍。
        @(negedge clk_io_i);
        run_io = 1'b0;
        #1ns;
        iovdd_ok_async_i = 1'b0;
        #2ps;
        if (io_word_ready_o || io_edge_words_valid_o || io_reset_no || !conversion_allow_core_o)
            $fatal(1, "Clock-stopped IOVDD withdrawal escaped the fixed-word gate");
        @(negedge clk_aon_i);
        iovdd_ok_async_i = 1'b1;
        // AON 连续运行，I/O 已停。重供后等待多个常供周期也不能替代目的时钟沿。
        repeat (3) @(negedge clk_aon_i);
        if (io_word_ready_o || io_edge_words_valid_o || io_reset_no)
            $fatal(1, "IOVDD restoration released fixed word with no IO clocks");
        run_io = 1'b1;
        // 第一层 I/O 复位释放和 AON 返回资格都有自己的同步链。从实际字许可
        // 请求进入 I/O 同步器的时刻算两拍，避免把全部重供路径误只记成两拍。
        wait (dut.io_word_release_ni);
        io_qualified_at_ns = $realtime;
        for (int edge_index = 1; edge_index <= 2; edge_index++) begin
            @(posedge clk_io_i);
            #2ps;
            if (io_word_ready_o != (edge_index == 2) || io_edge_words_valid_o != (edge_index == 2))
                $fatal(1, "Restored IOVDD fixed word released on wrong IO edge");
        end
        $display("PASS REFIN_IO_STOP restore_valid=1 IO=2 dt=%0.3f ns from permission request", $realtime-io_qualified_at_ns);
        check_pattern_wake(2'b10, 1000);
        check_pattern_wake(2'b11, 1800);
        io_word_valid_i = 1'b0;
        io_intf_check_i = 1'b0;
        check_power_reply_stopped_core();
        check_por_stop_and_restart();
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
