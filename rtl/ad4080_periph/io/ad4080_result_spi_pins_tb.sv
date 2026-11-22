`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_result_spi_pins_tb.sv
// 模块名     : ad4080_result_spi_pins_tb
// 版本       : v1.0
// 功能       : 对实际 DCLK 边沿核验固件前缀和约定的 CRC24 扩展尾。
// 设计说明   : 上游独立给符号非对称的固定字与约定尾，不把内部阶段或
//              valid 变成主机时钟。先由 FIFO_FULL 降 DCS，再等合法首拍
//              此后七拍继续发头，供数方在这段窗口内准备第一字。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_result_spi_pins_tb #(parameter bit EXTERNAL_TAIL = 1'b0);
    // --- 两个独立时基和真实主机引脚 ---
    logic clk_io = 0, rst_n = 0;
    initial forever #1250ps clk_io = !clk_io;
    logic dcs_n = 1, dclk = 1, capture_done = 0;
    logic [1:0] mode = 1;
    logic [3:0] sdo, oe;

    // --- 供数与约定外部 CRC ---
    logic start = 0, start_ready, start_accept, busy, done, error;
    logic sample_valid = 0, sample_ready, pin_selected, pin_abort;
    logic [2:0] error_code, unused_width;
    logic [3:0] beat;
    logic beat_valid, beat_last, actual_taken, crc_ready, mailbox_ready;
    logic unused_payload_start, unused_word_start, unused_beat_first, unused_crc_cancel;
    logic [1:0] unused_kind;
    logic [7:0] header;
    int done_count = 0, error_count = 0, take_count = 0;

    ad4080_fifo_record_framer #(.SPI_PIN_HEADER(1'b1), .SPI_CRC_TAIL_BOUND(EXTERNAL_TAIL)) framer (
        .clk_i(clk_io), .rst_ni(rst_n), .power_good_i(1'b1), .abort_i(busy && pin_abort),
        .start_valid_i(start), .start_ready_o(start_ready), .start_accept_o(start_accept),
        .mode_i(mode), .sample_count_i(15'd1), .format_bound_i(1'b1), .crc_service_ready_i(EXTERNAL_TAIL),
        .payload_prefetch_words_i(2'd0), .payload_pair_valid_i(1'b0), .payload_pair_i(2'd0),
        .payload_pair_first_i(1'b0), .payload_pair_last_i(1'b0), .payload_start_allow_o(unused_payload_start),
        .sample_valid_i(sample_valid), .sample_ready_o(sample_ready), .sample_i(20'h81234), .sample_last_i(1'b1),
        .crc_valid_i(crc_ready && EXTERNAL_TAIL), .crc_ready_o(crc_ready), .crc_i(24'hA5C39E), .crc_error_i(1'b0),
        .crc_cancel_o(unused_crc_cancel), .beat_valid_o(beat_valid), .beat_ready_i(1'b1), .beat_o(beat),
        .spi_pin_beat_taken_i(actual_taken), .sync_header_o(header), .payload_word_start_o(unused_word_start),
        .beat_width_o(unused_width), .beat_first_o(unused_beat_first), .beat_last_o(beat_last),
        .beat_kind_o(unused_kind), .busy_o(busy), .done_o(done), .error_o(error), .error_code_o(error_code)
    );

    ad4080_result_spi_pins pins (
        .clk_io_i(clk_io), .rst_ni(rst_n), .mode_io_i(mode), .capture_full_i(capture_done), .session_io_i(busy),
        .header_i(header), .group_valid_io_i(beat_valid), .group_io_i(beat), .group_last_io_i(beat_last),
        .group_taken_io_o(actual_taken), .payload_ready_io_o(mailbox_ready), .abort_io_o(pin_abort),
        .dcs_ni(dcs_n), .dclk_i(dclk), .sdo_o(sdo), .sdo_oe_o(oe), .selected_async_o(pin_selected)
    );

    always @(posedge clk_io) begin
        // 独立计数实际回执，与外部头周期分开。越界多领一组会使这个总数
        // 不同，即使它的静态数据碰巧与上一个组相同也能被检出。
        if (done) done_count <= done_count + 1;
        if (error) begin
            if (error_code != 3'd6) $fatal(1, "Wrong pin deadline error %0d", error_code);
            error_count <= error_count + 1;
        end
        if (actual_taken) take_count <= take_count + 1;
    end

    task automatic supply_after_first_dclk(input bit send_sample);
        // 明确等第一组已经实际装到 SDO 后才启动 I/O 侧描述符，模拟来自
        // 核心的较慢授权。真实第一 DCLK 即使没有前置载荷邮箱也要发头。
        wait (pin_selected);
        repeat (14) @(negedge clk_io);
        if (!start_ready) $fatal(1, "Test record command not ready after first physical header group");
        start = 1;
        @(posedge clk_io);
        if (!start_accept) $fatal(1, "Bound descriptor rejected after real DCS");
        @(negedge clk_io);
        start = 0;
        if (send_sample) begin
            sample_valid = 1;
            @(posedge clk_io);
            if (!sample_ready) $fatal(1, "Bound sample not accepted");
            @(negedge clk_io);
            sample_valid = 0;
        end
    endtask

    task automatic raw_group(input logic [3:0] expected, input logic [3:0] direction, input int position);
        dclk = 0;
        #10ns;
        dclk = 1;
        // 这是主机 Mode 3 上升沿数据，DUT 的被采样值必须是本次下降沿更新的。
        if (sdo !== expected || oe !== direction)
            $fatal(1, "Raw DCLK %0d: SDO=%b OE=%b expected=%b/%b", position, sdo, oe, expected, direction);
        #10ns;
    endtask

    task automatic run_frame(input bit quad);
        int words_groups, tail_groups, total, old_done, old_errors, old_takes;
        logic [79:0] expected_wire;
        logic [3:0] expect_oe, expect_group, held_group;
        old_done = done_count;
        old_errors = error_count;
        old_takes = take_count;
        mode = quad ? 2'd2 : 2'd1;
        expect_oe = quad ? 4'b1111 : 4'b0100;
        words_groups = quad ? 6 : 24;
        tail_groups = EXTERNAL_TAIL ? words_groups : 0;
        total = 8 + words_groups + tail_groups;
        // 物理四线的首数据组复制符号，随后的物理 ABCD nibble 是手排的
        // F184C2。尾作为外部给定的线序，不用此值计算任何商用算法。
        if (quad) expected_wire = EXTERNAL_TAIL ? 80'hF0F0F0F0_F184C2_A5C39E : 80'hF0F0F0F0_F184C2;
        else expected_wire = EXTERNAL_TAIL ? 80'hAA_F81234_A5C39E : 80'hAA_F81234;
        #10ns;
        if (oe !== (quad ? 4'b0001 : 4'b0000)) $fatal(1, "SDOD unselected drive missing");
        capture_done = 1;
        dcs_n = 0;
        #1ps;
        fork
            supply_after_first_dclk(1'b1);
            begin
                for (int k=0; k<total; k++) begin
                    expect_group = quad ? 4'(expected_wire >> ((total-1-k)*4)) :
                                          {1'b0, expected_wire[total-1-k], 2'b00};
                    raw_group(expect_group, expect_oe, k+1);
                end
                // 超过当前已验的长度不产生另一个可退休字。原器件额外 SCLK
                // 输出值留待实物，此处只保证原型没有虚构校验、没有额外读取。
                held_group = sdo;
                for (int k=0; k<3; k++) raw_group(held_group, expect_oe, total+k+1);
                dcs_n = 1;
            end
        join
        capture_done = 0;
        #25ns;
        if (done_count != old_done+1 || take_count != old_takes+words_groups+tail_groups ||
            error_count != old_errors || mailbox_ready || pin_selected)
            $fatal(1, "Result prefix, optional tail or extension clocks completed incorrectly");
        $display("PASS RESULT_SPI_RAW lanes=%0d prefix=%0d engineering_tail_groups=%0d engineering_tail_bits=%0d post_record_takes=0",
                 quad ? 4 : 1, 8+words_groups, tail_groups, EXTERNAL_TAIL ? 24 : 0);
    endtask

    task automatic no_first_payload();
        int failures_before, completions_before;
        failures_before = error_count;
        completions_before = done_count;
        mode = 1;
        capture_done = 1;
        dcs_n = 0;
        #1ps;
        fork
            supply_after_first_dclk(1'b0);
            begin
                for (int k=0; k<8; k++) raw_group((k[0] ? 4'b0000 : 4'b0100), 4'b0100, k+1);
                // 第九物理 DCLK 才到读数据时点。即使 AAA 头可发，缺真正
                // 载荷就立即撤销有效驱动，不能延长头或复用旧数据。
                dclk = 0;
                #10ns;
                dclk = 1;
                if (oe != 0) $fatal(1, "Missing first payload was exposed as valid SDOB");
                #30ns;
                dcs_n = 1;
            end
        join
        capture_done = 0;
        #25ns;
        if (error_count != failures_before+1 || done_count != completions_before)
            $fatal(1, "Physical DCLK deadline failed without an explicit terminal error");
        $display("PASS RESULT_SPI_RAW_DEADLINE actual_fall=9 no_first_payload error=6 no_done");
    endtask

    initial begin
        #20ns;
        rst_n = 1;
        run_frame(1'b0);
        run_frame(1'b1);
        no_first_payload();
        $display("AD4080_RESULT_SPI_PIN_ENGINE PASS tail=%0d", EXTERNAL_TAIL);
        $finish;
    end

    // 防止绑定或手握失效变为测试翻绿后的无界等待。
    initial begin
        #10us;
        $fatal(1, "Result SPI pin-engine watchdog expired");
    end
// 首部占用真实 DCLK 边沿，允许前端在此窗口从 SRAM 提前交付首字。
// 提前停钟和移除片选的路径均通过实际引脚观察，不提前改写预期完成状态。
// 工程尾允许测试额外 24 位，缺载时不计入已成功发送的前缀。
endmodule

`default_nettype wire
