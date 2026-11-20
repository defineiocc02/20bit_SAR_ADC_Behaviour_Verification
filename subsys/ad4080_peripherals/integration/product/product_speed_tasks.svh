// =============================================================================
// 文件名     : product_speed_tasks.svh
// 模块名     : 产品顶层的标称速率验证
// 版本       : v1.0
// 功能       : 独立用 Rev. B 表2、20、21 比对高速配置与实际连续码流。
// 设计说明   : 配置 SCLK 为独立 50 MHz 输入；连续 CNV 源在 25 ns 后
//              交付上一笔补码和标签。验证真实逻辑边沿，延迟以译码前
//              本台架同源事件为基准。DCO 的物理传播时间不在此模型中。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

    localparam int SPEED_WORDS = 128; // 足够跨越启动和多次标签、槽和指针翻转

    // =========================================================================
    // 1. 表2 的配置时钟角落
    // =========================================================================
    task automatic speed_spi_byte(
        input logic [7:0] value, input realtime low_width, input bit short_hold,
        output logic [7:0] received
    );
        for (int idx = 7; idx >= 0; idx--) begin
            sclk = 0;
            sdi = value[idx];
            #(low_width) sclk = 1;
            // 紧接采样沿扰动 SDI，验证旧位是否被 SPI 时钟沿锁存。
            // 取 1 ps 消除模型的同一时刻竞争；建立取完整低半拍。
            received[idx] = sdo;
            #1ps;
            if (short_hold) sdi = !value[idx];
            #(20ns - low_width - 1ps);
        end
    endtask

    task automatic speed_spi_transfer(
        input bit read_cmd, input logic [14:0] address, input logic [7:0] data,
        input realtime low_width, input bit short_hold, output logic [7:0] reply
    );
        logic [7:0] discard;
        #20ns cs_n = 0;
        #10ns;
        speed_spi_byte({read_cmd, address[14:8]}, low_width, short_hold, discard);
        speed_spi_byte(address[7:0], low_width, short_hold, discard);
        speed_spi_byte(data, low_width, short_hold, reply);
        cs_n = 1;
        #20ns;
        if (sdo_oe !== 1'b0) $fatal(1, "50 MHz configuration frame did not release SDO");
    endtask

    task automatic check_speed_config_spi();
        logic [7:0] reply, pattern;
        realtime low_width;
        for (int duty = 0; duty < 2; duty++) begin
            low_width = duty == 0 ? 9ns : 11ns;
            for (int idx = 0; idx < 4; idx++) begin
                // 更换比特图样和 CS 相位，避免只验一个对采样有利的相位。
                pattern = 8'hA6 ^ (8'(idx) * 8'h37);
                #(375ps * idx);
                speed_spi_transfer(0, 15'h000A, pattern, low_width, 1'b1, reply);
                speed_spi_transfer(1, 15'h000A, '0, low_width, 1'b1, reply);
                if (reply !== pattern)
                    $fatal(1, "Table 2 SDI hold at 50 MHz failed: low=%0.2f ns phase=%0d read=%h expected=%h",
                           low_width, idx, reply, pattern);
            end
            speed_spi_transfer(1, 15'h0004, '0, low_width, 1'b1, reply);
            if (reply !== 8'h50) $fatal(1, "50 MHz configuration product ID read failed");
        end
        $display("PRODUCT_SPEED_CFG PASS sclk_mhz=50 low_ns=9/11 sdi_hold_ps=1 scratch_roundtrips=8 id_reads=2");
    endtask

    // =========================================================================
    // 2. 连续转换的独立序号、补码及位序
    // =========================================================================
    function automatic int signed speed_input(input int index);
        // 输入跨正负两侧且与 CNV 标签一一对应，避免重复固定码掩盖错槽。
        return ((index * 7919 + 85789) % 800001) - 400000;
    endfunction

    function automatic logic [19:0] speed_expected(input int index, input bit corrected);
        int signed sample, rounded_delta;
        sample = speed_input(index);
        // 产品夹具 K=1，SHIFT=14，GAIN 0x0210 比单位大 16，独立求
        // 1/1024 的增量。此输入范围始终在 20 位以内。
        rounded_delta = corrected ? ((sample + 512) >>> 10) : 0;
        return 20'(sample + rounded_delta);
    endfunction

    task automatic exercise_product_rate(
        input bit dual_lane, corrected, input logic [31:0] base_tag
    );
        int lanes, slots, count, delay_cycles, observed_words;
        longint io_ps, origin_ps, edge_ps, expected_latency_ps;
        logic [31:0] prior_tag, tag_value;
        logic [19:0] prior_word, trial_word;
        int prior_slot, slot, frame;
        bit prior_valid, word_valid;

        lanes = dual_lane ? 2 : 1;
        io_ps = dual_lane ? 5000 : 2500;
        slots = dual_lane ? 5 : 10;
        count = dual_lane ? 5 : corrected ? 10 : 8;
        delay_cycles = slots + count;
        // p.48 实例与表20 对应的首位时间。此断言不从 RTL 时隙状态反推。
        expected_latency_ps = dual_lane ? 52500 : corrected ? 51250 : 46250;
        write_reg(15'h0028, 2, corrected ? 16'h0210 : 16'h0200);
        write_reg(15'h001C, 1, 16'h0000);
        wait (continuous_config_ready_core_o && continuous_cnv_allowed_io_o);

        observed_words = 0;
        prior_tag = '0;
        prior_word = '0;
        prior_slot = 0;
        prior_valid = 0;
        @(negedge clk_io_i);
        sample_source_idle_core_i = 0;
        fork
            begin : deliver_raw
                // DCO 与核心时钟同源但各有边界。后一笔 CNV 提供上一
                // 笔的完成码，核心拍上保留实际标签。尾拍交末码后停供。
                @(posedge clk_io_i);
                @(negedge clk_core_i);
                for (int i = 0; i <= SPEED_WORDS; i++) begin
                    cnv_tick_core_i = 1;
                    sample_valid_core_i = i > 0;
                    sample_tag_core_i = base_tag + 32'(i - 1);
                    sample_signed_core_i = i > 0 ? 20'(speed_input(i - 1)) : '0;
                    @(posedge clk_core_i);
                    #1ps;
                    if (cnv_data_timing_error_core_o || conversion_timing_error_core_o ||
                        continuous_result_drop_core_o || processing_reject_o)
                        $fatal(1, "400 MHz core dropped or mistimed conversion %0d", i);
                    @(negedge clk_core_i);
                    cnv_tick_core_i = 0;
                    sample_valid_core_i = 0;
                    repeat (9) @(negedge clk_core_i);
                end
            end
            begin : check_shifted_words
                origin_ps = -1;
                for (int tick = 0; tick <= delay_cycles + slots*SPEED_WORDS; tick++) begin
                    cnv_tick_io_i = tick < SPEED_WORDS*slots && tick % slots == 0;
                    cnv_tag_io_i = base_tag + 32'(tick / slots);
                    @(posedge clk_io_i);
                    edge_ps = ad4080_product_test_time::now_ps();
                    // 把时钟检查和位检查分开，便于定位时钟缺边与数据缺字。
                    if (origin_ps < 0) origin_ps = edge_ps;
                    #1ps;
                    if (edge_ps != origin_ps + longint'(tick)*io_ps ||
                        continuous_cnv_rejected_io_o || continuous_result_rejected_io_o ||
                        continuous_frame_missing_io_o)
                        $fatal(1, "40 MS/s I/O continuity lane=%0d clock=%0d actual_ps=%0d expected_ps=%0d cnv_reject=%b result_reject=%b missing=%b",
                               lanes, tick, edge_ps, origin_ps + longint'(tick)*io_ps,
                               continuous_cnv_rejected_io_o, continuous_result_rejected_io_o, continuous_frame_missing_io_o);
                    check_continuous_bit("SPEED", 1'b1, prior_valid, lanes, prior_slot, prior_tag, prior_word, edge_ps);
                    if (prior_valid && prior_slot == slots-1) observed_words++;

                    frame = tick < delay_cycles ? -1 : (tick - delay_cycles)/slots;
                    slot = tick < delay_cycles ? 0 : (tick - delay_cycles)%slots;
                    word_valid = frame >= 0 && frame < SPEED_WORDS;
                    tag_value = word_valid ? base_tag + 32'(frame) : '0;
                    trial_word = word_valid ? speed_expected(frame, corrected) : '0;
                    @(negedge clk_io_i);
                    edge_ps = ad4080_product_test_time::now_ps();
                    // 在更新边沿后的同一精度格读取数据和使能，避开调度区竞争。
                    #1ps;
                    if (!continuous_dco_oe_o || !continuous_da_oe_o || continuous_db_oe_o != dual_lane)
                        $fatal(1, "Incorrect echo clock or lane enable");
                    if (word_valid && slot == 0 &&
                        edge_ps - origin_ps - longint'(frame)*25000 != expected_latency_ps)
                        $fatal(1, "Table 21 first-bit latency failed");
                    check_continuous_bit("SPEED", 1'b0, word_valid, lanes, slot, tag_value, trial_word, edge_ps);
                    prior_valid = word_valid;
                    prior_tag = tag_value;
                    prior_word = trial_word;
                    prior_slot = slot;
                end
                cnv_tick_io_i = 0;
            end
        join
        sample_source_idle_core_i = 1;
        if (observed_words != SPEED_WORDS) $fatal(1, "Missing continuous results");
        $display("PRODUCT_SPEED_LVDS PASS lanes=%0d corrected=%0d fs_msps=40 words=%0d first_bit_ps=%0d",
                 lanes, corrected, observed_words, expected_latency_ps);
    endtask

    task automatic audit_product_speed(input bit dual_lane);
        check_speed_config_spi();
        // 表20 编码：单线 count 8/10 分别对应 5/7，双线 count 5 为 2。
        write_reg(15'h0015, 1, dual_lane ? 16'h0044 : 16'h0040);
        wait (continuous_config_ready_core_o);
        if (!dual_lane) begin
            write_reg(15'h0016, 1, 16'h0050);
            exercise_product_rate(1'b0, 1'b0, 32'h53C0_0000);
        end
        write_reg(15'h0016, 1, dual_lane ? 16'h0020 : 16'h0070);
        exercise_product_rate(dual_lane, 1'b1, 32'h53C0_1000);
    endtask
