`timescale 1ps/1ps
`default_nettype none
// =============================================================================
// 文件名     : cfg_spi_pin_edges_tb.sv
// 模块名     : cfg_spi_pin_edges_tb
// 版本       : v1.0
// 功能       : 在真实 DSP GPIO0 上逐沿核对 Rev. B 配置 SPI。
// 设计说明   :
//   * 只通过 CS、SCLK、SDI 访问冻结 ad4080_top。期望数据从写入事务、
//     手册表值及独立计算的 CRC 八位向量获得。
//   * 2.5 ns 的执行时钟是数字过采样夹具，扫相位时保持 SDI 到下一降沿。
//     模型检查时序关系，外部 pad 延迟及任意 0 ns 保持窗口另行验收。
//   * 失败时仍结束其他场景，便于用同一 CSV 对比修复前后的引脚行为。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module cfg_spi_pin_edges_tb;
    // =========================================================================
    // 1. 实际配置、GPIO 与工程错误接点
    // =========================================================================
    logic clk = 0, rst_n = 0;
    logic cs_n = 1, sclk = 1, sdi = 1;
    logic interface_available = 1'b1;
    wire sdo, sdo_oe, cfg_reject;
    wire [3:0] gpio_o, gpio_oe, cfg_reject_reason;
    int reject_count = 0, last_reject = 0;
    logic [15:0] last_reject_addr;  // 窗口回绕/保留地址由实际拒绝沿的地址观测
    int checks = 0, fails = 0, cases = 0;
    always #1250 clk = ~clk;

`ifdef CFG_SPI_LVDS_PROFILE
    logic data_config_ready = 1'b1;  // 夹具只提供可控的下游受理试验信号
    wire [7:0] cfg_data_a, cfg_data_b, cfg_data_c;
`endif

`ifdef CFG_SPI_INIT_POLL
    logic pwr_full_allow, pwr_poll_allow, pwr_rsp_valid;
    wire pwr_cmd_valid;
    wire [1:0] pwr_cmd_op;
    int pwr_reset_commands = 0;
    // 只计来自实际两步 SPI 写的复位候选，夹具不直接清寄存器。
    always @(negedge clk)
        if (rst_n && pwr_cmd_valid && pwr_cmd_op == 2'd1) pwr_reset_commands++;
`endif

    ad4080_top #(
        .P_WRITE_SDO_DRIVE(1), .P_SHORT_ADDR_BITS(7),
        .P_MULTIBYTE_ORDER(0), .P_OFFSET_LSB_ADDR(37), .P_PRODUCT_ID_L(80)
`ifdef CFG_SPI_LVDS_PROFILE
        ,.P_CONTINUOUS_LVDS_BOUND(1'b1)
`endif
`ifdef CFG_SPI_INIT_POLL
        ,.P_AON_CFG_BOUND(1'b1)
`endif
    ) dut (
`ifdef CFG_SPI_LVDS_PROFILE
`ifndef CFG_SPI_INIT_POLL
        // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
        .pwr_cfg_full_allow_i(1'b1), .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(),
        .pwr_cmd_op_o(), .pwr_cmd_value_o(), .pwr_rsp_valid_i(1'b0),
        .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
        .pwr_ldo_pd_i(2'b00), .pwr_cfg_poll_allow_i(1'b0),
`endif
        .rom_crc_error_i(1'b0),
`endif

        .clk(clk), .rst_n(rst_n), .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        .sdo(sdo), .sdo_oe(sdo_oe), .interface_ready(interface_available), .adc_idle(1'b1),
        .valid_in(1'b0), .x(20'b0), .filter_sync(1'b0), .ext_event(1'b0), .arm(1'b0), .rd_en(1'b0),
        .gpio_i(4'b0), .gpio_o(gpio_o), .gpio_oe(gpio_oe), .dout(), .dout_valid(),
        .status(), .correction_clip_low(), .correction_clip_high(), .cfg_reject(cfg_reject),
        .cfg_reject_reason(cfg_reject_reason), .processing_reject()
`ifdef CFG_SPI_LVDS_PROFILE
        ,.cnv_tick_i(1'b0),.conversion_timing_error_i(1'b0),
        .phy_fifo_full_i(1'b0),.phy_fifo_read_done_i(1'b0),
        .csr_fifo_full_i(1'b0),.csr_fifo_read_done_i(1'b0),.cnv_data_timing_error_o(),
        .data_config_ready_i(data_config_ready),.sample_tag_i(32'd0),
        .por_ana_event_i(1'b0),
        .data_intf_config_a_o(cfg_data_a),.data_intf_config_b_o(cfg_data_b),
        .data_intf_config_c_o(cfg_data_c),
        .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(),.continuous_word_valid_o(),.continuous_word_o(),.continuous_word_tag_o()
`endif
`ifdef CFG_SPI_INIT_POLL
        ,.pwr_cfg_full_allow_i(pwr_full_allow), .pwr_cfg_poll_allow_i(pwr_poll_allow),
        .pwr_cmd_ready_i(1'b1), .pwr_cmd_valid_o(pwr_cmd_valid), .pwr_cmd_op_o(pwr_cmd_op),
        .pwr_cmd_value_o(), .pwr_rsp_valid_i(pwr_rsp_valid), .pwr_rsp_accept_i(1'b1),
        .pwr_rsp_op_i(2'd1), .pwr_mode_i(2'd0), .pwr_ldo_pd_i(2'd0)
`endif
    );

    // 优先观测实际 GPIO0。此错误端用于区分收时钟出错和寄存器回读未更新。
    always @(negedge clk) begin
        if (rst_n && cfg_reject) begin
            reject_count++;
            last_reject = int'(cfg_reject_reason);
            last_reject_addr = dut.addr;
        end
    end

    task automatic check(input bit ok, input string tag, input string what, input int index);
        checks++;
        if (!ok) begin
            fails++;
            if (fails <= 64)
                $display("PIN_DIFF,%s,%s,%0d,%0t,CS=%0b,SCLK=%0b,SDI=%0b,GP0=%0b,OE=%0b",
                         tag, what, index, $time, cs_n, sclk, sdi, gpio_o[0], gpio_oe[0]);
        end
    endtask

    // =========================================================================
    // 2. 逐降沿、上升采样和 CS 边界
    // =========================================================================
    task automatic frame(
        input string tag, input int nbits, input logic [63:0] tx,
        input logic [63:0] want, input logic [63:0] want_oe,
        input int low_ps = 9000, input int high_ps = 11000,
        input int phase_ps = 113, input int start_style = 0,
        input int end_ps = 8000, input int want_reject = 0
    );
        int old_fails, old_rejects, actual_rejects;
        logic natural_cs_fall, prev_enabled, prev_data, release_level;
        logic [63:0] fall_o, fall_oe, rise_o, rise_oe;
        old_fails = fails;
        old_rejects = reject_count;
        fall_o = 0; fall_oe = 0; rise_o = 0; rise_oe = 0;
        prev_enabled = 0; prev_data = 0;
        @(negedge clk);
        #(phase_ps);
        check(cs_n && sclk && !gpio_oe[0], tag, "CS_idle_release", -1);
        // 0 在首降沿前选片，1 同首降沿，2 同首采样上升沿。
        // Fig.67/70 的 tCSSCK 箭头到首采样沿，因此 2 用于无 header SDO 的事务。
        natural_cs_fall = start_style == 0;
        if (natural_cs_fall) begin cs_n = 0; #8000; end
        for (int n = nbits-1; n >= 0; n--) begin
            if (n == nbits-1 && start_style == 1) cs_n = 0;
            sdi = tx[n];
            sclk = 0;
            // Table 2 tHSDO=0.7 ns，数字模型在此时刻仍须保留先前已驱动的位。
            #700;
            if (prev_enabled && (want_reject == 0 || want_oe[n]))
                check(gpio_oe[0] && gpio_o[0] == prev_data, tag, "previous_bit_hold", n);
            #7300;
            fall_o[n] = gpio_o[0];
            fall_oe[n] = gpio_oe[0];
            check((gpio_oe[0] == want_oe[n]) && (!want_oe[n] || gpio_o[0] == want[n]),
                  tag, "fall_plus_8ns", n);
            #(low_ps-8000);
            if (n == nbits-1 && start_style == 2) cs_n = 0;
            sclk = 1;
            rise_o[n] = gpio_o[0];
            rise_oe[n] = gpio_oe[0];
            check((gpio_oe[0] == want_oe[n]) && (!want_oe[n] || gpio_o[0] == want[n]),
                  tag, "rising_sample", n);
            check(sdo_oe == gpio_oe[0] && (!sdo_oe || sdo == gpio_o[0]),
                  tag, "selected_GPIO0_equals_raw_SDO", n);
            prev_enabled = want_oe[n];
            prev_data = want[n];
            if (n != 0) #(high_ps);
        end
        // end_ps=0 使 CS 的结束与最后的采样边沿落进同一同步节拍。
        if (end_ps != 0) #(end_ps);
        cs_n = 1;
        #8000;
        release_level = gpio_oe[0];
        check(!release_level, tag, "CS_release_8ns", -1);
        #8000;
        actual_rejects = reject_count - old_rejects;
        check((want_reject == 0 ? actual_rejects == 0 :
               actual_rejects == 1 && last_reject == want_reject), tag, "frame_reject", -1);
        cases++;
        $display("PIN_CASE,%s,%0d,%0d,%0d,%0d,%0d,%0d,%016h,%016h,%016h,%016h,%016h,%016h,%016h,%0d,%0d,%0d,%0d",
                 tag, low_ps, high_ps, phase_ps, start_style, end_ps, nbits, tx,
                 want_oe, fall_oe, rise_oe, want, fall_o, rise_o, want_reject,
                 actual_rejects == 0 ? 0 : last_reject, release_level, fails-old_fails);
    endtask

    task automatic idle_clocks;
        // Fig.73 在 CS 高时仍可摆动 SCLK。CS 未选中期间没有 GPIO0 驱动权。
        for (int k = 0; k < 3; k++) begin
            sclk = 0; #9000;
            check(cs_n && !gpio_oe[0], "continuous_idle", "outside_fall", k);
            sclk = 1; #11000;
            check(cs_n && !gpio_oe[0], "continuous_idle", "outside_rise", k);
        end
    endtask

`ifdef CFG_SPI_LVDS_PROFILE
    task automatic run_lvds_config;
        int prior_failures;
        prior_failures = fails;
        // 表 20 的时钟序号与字段编码分别核对。单 lane 序号 8 是码 5。
        // 双 lane 序号 5 是码 2，切换 lane 前先把 B 写入合法的共同范围。
        frame("lvds_default_a", 24, 64'h8015ff, 64'h40, 64'hff);
        frame("lvds_default_b", 24, 64'h8016ff, 0, 64'hff);
        frame("lvds_single_pattern", 24, 64'h001550, 0, 64'hff);
        frame("lvds_single_8_write", 24, 64'h001658, 0, 64'hff);
        frame("lvds_single_8_read", 24, 64'h8016ff, 64'h58, 64'hff);
        // Manchester 只向双 lane、直通无滤波和无FIFO放行，单 lane 尝试
        // 即使计数合法，也应保留此前已接受的 0x58。
        frame("lvds_single_manchester_reject", 24, 64'h00165c, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("lvds_single_manchester_preserved", 24, 64'h8016ff, 64'h58, 64'hff);
        frame("lvds_change_lane_invalid", 24, 64'h001554, 0, 64'hff, 9000, 11000, 113, 0, 8000, 9);
        frame("lvds_lane_preserved", 24, 64'h8015ff, 64'h50, 64'hff);
        frame("lvds_single_clock2_code9", 24, 64'h001690, 0, 64'hff);
        frame("lvds_single_invalid10", 24, 64'h0016a0, 0, 64'hff, 9000, 11000, 113, 0, 8000, 9);
        frame("lvds_single_preserved", 24, 64'h8016ff, 64'h90, 64'hff);
        frame("lvds_prepare_dual_5", 24, 64'h001628, 0, 64'hff);
        frame("lvds_enter_dual", 24, 64'h001554, 0, 64'hff);
        frame("lvds_dual_5_read", 24, 64'h8016ff, 64'h28, 64'hff);
        frame("lvds_dual_lane_read", 24, 64'h8015ff, 64'h54, 64'hff);
        // 保持同一 GPIO0/SCLK 路径，分别触发无效时钟码、保留位和配置忙。
        // 每次否决都核对 CSR 保持，同时确认真实导出的 A/B 与回读同源。
        frame("lvds_dual_invalid5", 24, 64'h001650, 0, 64'hff, 9000, 11000, 113, 0, 8000, 9);
        frame("lvds_dual_cnt_preserved", 24, 64'h8016ff, 64'h28, 64'hff);
        frame("lvds_A_missing_fixed_bit6", 24, 64'h001514, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("lvds_A_reserved_bit1", 24, 64'h001556, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("spi_and_fixed_word_invalid", 24, 64'h001555, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("lvds_B_reserved_bit1", 24, 64'h00162a, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("lvds_reserved_A_preserved", 24, 64'h8015ff, 64'h54, 64'hff);
        frame("lvds_reserved_B_preserved", 24, 64'h8016ff, 64'h28, 64'hff);
        // 正向选双 lane 直通 Manchester 和差分 CNV，然后恢复可切 SPI 的常规 B。
        frame("lvds_dual_manchester_cnv", 24, 64'h001625, 0, 64'hff);
        frame("lvds_dual_manchester_cnv_read", 24, 64'h8016ff, 64'h25, 64'hff);
        frame("lvds_reset_B_before_switch", 24, 64'h001628, 0, 64'hff);
        frame("result_spi_single_switch", 24, 64'h001541, 0, 64'hff);
        frame("result_spi_single_read", 24, 64'h8015ff, 64'h41, 64'hff);
        frame("result_spi_four_switch", 24, 64'h001545, 0, 64'hff);
        frame("result_spi_four_read", 24, 64'h8015ff, 64'h45, 64'hff);
        frame("lvds_return_to_dual", 24, 64'h001554, 0, 64'hff);
        // 表 50：VOD 111 无实际幅度档，但 0x17 必须原样读回。有效档在产品
        // 配置快照之后转换，故这个夹具核对实际 DSP 导出的 raw C。
        frame("lvds_default_c", 24, 64'h8017ff, 64'h20, 64'hff);
        frame("lvds_raw_C_invalid_vod_write", 24, 64'h0017f0, 0, 64'hff);
        frame("lvds_raw_C_invalid_vod_read", 24, 64'h8017ff, 64'hf0, 64'hff);
        frame("lvds_C_reserved_low", 24, 64'h0017f1, 0, 64'hff, 9000, 11000, 113, 0, 8000, 2);
        frame("lvds_C_reserved_preserved", 24, 64'h8017ff, 64'hf0, 64'hff);
        data_config_ready = 0;
        frame("lvds_busy_A", 24, 64'h001544, 0, 64'hff, 9000, 11000, 113, 0, 8000, 8);
        frame("lvds_busy_B", 24, 64'h001620, 0, 64'hff, 9000, 11000, 113, 0, 8000, 8);
        frame("lvds_busy_C", 24, 64'h001720, 0, 64'hff, 9000, 11000, 113, 0, 8000, 8);
        frame("lvds_busy_A_preserved", 24, 64'h8015ff, 64'h54, 64'hff);
        frame("lvds_busy_B_preserved", 24, 64'h8016ff, 64'h28, 64'hff);
        frame("lvds_busy_C_preserved", 24, 64'h8017ff, 64'hf0, 64'hff);
        data_config_ready = 1;
        check(cfg_data_a == 8'h54 && cfg_data_b == 8'h28 && cfg_data_c == 8'hf0,
              "lvds_ports", "accepted_profile_ports", -1);
        if (fails == prior_failures)
            $display("LVDS_CFG_SPI_COMPLETE A=%02h B=%02h rawC=%02h single_clock8_code5=1 dual_clock5_code2=1 SPI_41_45=1",
                 cfg_data_a, cfg_data_b, cfg_data_c);
    endtask
`endif

`ifdef CFG_SPI_CRC_STREAM
    task automatic run_crc_stream;
        int prior_failures;
        prior_failures = fails;
        // 表 18/19：后续读的主机校验位置故意给任意 padding，每个器件 CRC 仍核黄金值。
        // 先清上一个首实体错误；严格降序的 OFFSET 从 0x26 走向 0x24 的 LO 阈值。
        frame("stream_clear_previous_crc", 32, 64'h119f1e, 64'hc8001e, 64'hffffffff);
        frame("stream_desc_write_pair", 64, 64'h2601341604f94f, 64'hc000001600004f, 64'hffffffffffffffff);
        frame("stream_desc_read_pair", 64, 64'h8026ffff9a393921, 64'hc001342704f94f, 64'hffffffffffffffff);
        // GPIO0 选择 nibble 保持为 0，两个单字节实体仍在同一真实选择输出上比对。
        frame("stream_gpio_good", 48, 64'h1b760d4081, 64'hc0000d0081, 64'hffffffffffff);
        // 后续写坏 CRC 后继续打时钟：首实体已受理、第二旧值保留、第三实体无输出权。
        frame("stream_second_crc_bad", 64, 64'h1c600455ec7011, 64'hc0000400ed0011, 64'hffffffffffff0000, 9000, 11000, 113, 0, 8000, 6);
        frame("stream_crc_sticky", 32, 64'h8011ff32, 64'hc808f9, 64'hffffffff);
        frame("stream_first_commit_retained", 32, 64'h801cffdb, 64'hc8600f, 64'hffffffff);
        frame("stream_bad_second_preserved", 32, 64'h801bffb0, 64'hc87606, 64'hffffffff);
        frame("stream_after_bad_ignored", 32, 64'h801affa5, 64'hc84091, 64'hffffffff);
        // W1C 本身在独立新帧受 CRC 保护。清状态后重放正确的首/后续实体并回读。
        frame("stream_bad_crc_w1c", 32, 64'h1108f2, 64'hc800f2, 64'hffffffff);
        frame("stream_gpio_retry", 48, 64'h1b55e47011, 64'hc000e40011, 64'hffffffffffff);
        frame("stream_gpio_retry_read", 48, 64'h801bffb03900, 64'hc055ef7011, 64'hffffffffffff);
        // 后续流读无主机 CRC 义务，少走器件 CRC 的 4 个时钟记 CLOCK_COUNT。
        frame("stream_short_device_crc", 44, 64'h801bffb0392, 64'hc055ef701, 64'hfffffffffff, 9000, 11000, 113, 0, 8000, 7);
        frame("stream_only_clock_error", 32, 64'h8011ff32, 64'hd010b1, 64'hffffffff);
        frame("stream_clock_w1c", 32, 64'h1110ba, 64'hd000ba, 64'hffffffff);
        // 有效 Scratch 下一地址 0x09 是保留空洞。它与 0x0000 回滚到 0x7FFF 同样由真实 bank 拒绝。
        frame("stream_reserved_next", 48, 64'h800afff2ff00, 64'hc0c65d0000, 64'hffffffff0000, 9000, 11000, 113, 0, 8000, 3);
        check(last_reject_addr == 16'h0009, "stream_reserved_next", "actual_addr", -1);
        frame("stream_reserved_w1c", 32, 64'h1101cd, 64'hc100cd, 64'hffffffff);
        frame("stream_long_wrap", 48, 64'h8000ff70ff00, 64'hc010f30000, 64'hffffffff0000, 9000, 11000, 113, 0, 8000, 3);
        check(last_reject_addr == 16'h7fff, "stream_long_wrap", "actual_addr", -1);
        frame("stream_wrap_w1c", 32, 64'h1101cd, 64'hc100cd, 64'hffffffff);
        // 图 60 的升序形式：首单字节 GENERAL_CONFIG，后续严格双字节 WATERMARK。
        frame("stream_asc_select", 32, 64'h2068, 64'hc00068, 64'hffffffff);
        // p80 的两个地址有文档复位值。零写可通，未确认的非零循环不改变旧值。
        // 从 VENDOR_H 读流穿过 0x0E/0x0F，两个后续 CRC 均取自己的地址。
        frame("stream_loop_fields_write_zero", 48, 64'h000e005e002d, 64'h00c0005e002d, 64'hffffffffffff);
        frame("stream_loop_fields_defined_read", 64, 64'h800dff99ffa7ffa7, 64'h00c00476002a002d, 64'hffffffffffffffff);
        frame("stream_loop_count_nonzero_reject", 32, 64'h000e0357, 64'h00c00057, 64'hffffffff, 9000, 11000, 113, 0, 8000, 2);
        frame("stream_keep_count_nonzero_reject", 32, 64'h000f0457, 64'h00c00057, 64'hffffffff, 9000, 11000, 113, 0, 8000, 2);
        frame("stream_loop_fields_preserved", 64, 64'h800dff99ffa7ffa7, 64'h00c00476002a002d, 64'hffffffffffffffff);
        frame("stream_asc_mixed_write", 56, 64'h1c60042a0195, 64'hc00004000095, 64'hffffffffffffff);
        frame("stream_asc_mixed_read", 56, 64'h801cffdb3939e7, 64'hc0600f2a0195, 64'hffffffffffffff);
        // 图 62：取消严格后，多字节寄存器的每个普通字节有自己的 CRC 和起址种子。
        frame("stream_desc_restore", 32, 64'h88, 64'hc00088, 64'hffffffff);
        frame("stream_ordinary_enable", 32, 64'h105266, 64'hc00066, 64'hffffffff);
        frame("stream_ordinary_write", 48, 64'h2001217832, 64'hc000210032, 64'hffffffffffff);
        frame("stream_ordinary_read", 48, 64'h8020ffde39c3, 64'hc0012a7832, 64'hffffffffffff);
        frame("stream_strict_restore", 32, 64'h107286, 64'hc00086, 64'hffffffff);
        // 图 64：首指令改为短格式，起始 7 位地址之后仍用同一 15 位窗口滚转。
        frame("stream_short_enable", 32, 64'h108a5, 64'hc000a5, 64'hffffffff);
        frame("stream_short_wrap", 40, 64'h80ff1cff00, 64'h40109f0000, 64'hffffff0000, 9000, 11000, 113, 0, 8000, 3);
        check(last_reject_addr == 16'h7fff, "stream_short_wrap", "actual_addr", -1);
        frame("stream_short_wrap_w1c", 24, 64'h11011c, 64'h41001c, 64'hffffff);
        frame("stream_short_valid_pair", 56, 64'ha6ffff993939b2, 64'h4001342404f94f, 64'hffffffffffffff);
        // 图 61 单指令多实体：同 CS 的两条地址均按新指令从 0xA5 起算，各需有效主机 CRC。
        frame("stream_short_single_enable", 24, 64'h188fd, 64'h4000fd, 64'hffffff);
        frame("stream_single_short_two", 48, 64'h8aff9e83aa8f, 64'h40c6314007c5, 64'hffffffffffff);
        frame("stream_restore_long", 24, 64'h1004c, 64'h40004c, 64'hffffff);
        if (fails == prior_failures)
            $display("CFG_SPI_STREAM_CRC_COMPLETE first_and_later=1 write_abort_retried=1 short_wrap=7fff");
    endtask
`endif

`ifdef CFG_SPI_INIT_POLL
    task automatic enter_initialization(input string stage, input bit test_before_rsp);
        int prior_cmds;
        prior_cmds = pwr_reset_commands;
        // p27 两次真实 SPI 帧启动用户软复位。下游夹具保留待回应命令，
        // 等实际 bank 出候选才应答。DUT 自己按应答恢复制动前的状态。
        frame({stage, "_reset_arm"}, 24, 64'h000081, 0, 64'hff);
        frame({stage, "_reset_confirm"}, 24, 64'h000000, 0, 64'hff);
        check(pwr_reset_commands == prior_cmds + 1, stage, "one_real_soft_reset_command", -1);
        interface_available = 0;
        pwr_full_allow = 0;
        pwr_poll_allow = 1;
        // 先发的芯片资格不应越过 bank 的末次待回应。本用例在应答前探测禁止状态。
        if (test_before_rsp)
            frame({stage, "_before_bank_rsp_blocked"}, 24, 64'h8011ff, 0, 0,
                  9000, 11000, 113, 0, 8000, 8);
        @(negedge clk);
        pwr_rsp_valid = 1;
        @(negedge clk);
        pwr_rsp_valid = 0;
    endtask

    task automatic run_reset_poll;
        int prior_failures;
        prior_failures = fails;
        // 前一组选用了配置 CRC，先依当前实际格式恢复复位默认的无 CRC、无附带状态。
`ifdef CFG_SPI_CRC_STREAM
        frame("poll_restore_default_format", 32, 64'h00102336, 64'h00c00036, 64'hffffffff);
`else
        frame("poll_restore_default_format", 32, 64'h00102336, 64'h00c80036, 64'hffffffff);
`endif
        enter_initialization("pure_poll", 0);
        frame("pure_poll_0x11_busy", 24, 64'h8011ff, 64'h80, 64'hff);
        frame("pure_poll_0x11_busy_again", 24, 64'h8011ff, 64'h80, 64'hff);
        // 模型中的实际 Pad 资格出现后，合法纯读不应把瞬时初始化忙变成历史粘滞值。
        interface_available = 1;
        pwr_full_allow = 1;
        pwr_poll_allow = 0;
        frame("pure_poll_0x11_done", 24, 64'h8011ff, 0, 64'hff);
        enter_initialization("illegal_poll", 1);
        frame("poll_forbid_other_read", 24, 64'h800aff, 0, 0, 9000, 11000, 113, 0, 8000, 8);
        frame("poll_ignore_scratch_write", 24, 64'h000a31, 0, 0, 9000, 11000, 113, 0, 8000, 8);
        frame("poll_ignore_status_W1C", 24, 64'h001180, 0, 0, 9000, 11000, 113, 0, 8000, 8);
        frame("poll_after_illegal_still_busy", 24, 64'h8011ff, 64'h80, 64'hff);
        interface_available = 1;
        pwr_full_allow = 1;
        pwr_poll_allow = 0;
        // 表 46 的历史非法事务粘滞值与 p28 的纯轮询忙态是两条路径。
        frame("poll_violation_stays_sticky", 24, 64'h8011ff, 64'h80, 64'hff);
        frame("poll_scratch_not_written", 24, 64'h800aff, 0, 64'hff);
        frame("poll_legal_post_init_W1C", 24, 64'h001180, 0, 64'hff);
        frame("poll_status_clear_after_W1C", 24, 64'h8011ff, 0, 64'hff);

        // 失去正常接口权限但没有专用供电/初始化证明，绝不打开任意旁路。
        interface_available = 0;
        pwr_full_allow = 0;
        frame("poll_no_qualifier_no_bypass", 24, 64'h8011ff, 0, 0, 9000, 11000, 113, 0, 8000, 8);
        interface_available = 1;
        pwr_full_allow = 1;
        if (fails == prior_failures)
            $display("CFG_SPI_RESET_POLL_COMPLETE legal_80_to_00=1 ignored_write=1 sticky_W1C=1 unqualified_block=1");
    endtask
`endif

    // =========================================================================
    // 3. 定向边界及合法时钟模型扫相位
    // =========================================================================
    initial begin
        int low_ps, high_ps;
        logic [7:0] pattern;
        repeat (4) @(negedge clk);
`ifdef CFG_SPI_INIT_POLL
        // 在释放复位的同一负沿显式驱动夹具资格。Verilator 5.30 展开含延时的
        // 轮询任务时可能覆盖声明初始化，首帧会误以为 Pad 尚未经上电许可。
        // 初期扫相位只开放普通配置。轮询资格和复位应答由各自的定向用例提供。
        pwr_full_allow = 1'b1;
        pwr_poll_allow = 1'b0;
        pwr_rsp_valid = 1'b0;
`endif
        rst_n = 1;
        repeat (8) @(negedge clk);
        $display("PIN_COLUMNS,name,low_ps,high_ps,core_phase_ps,start_style,end_ps,bits,tx,expect_oe,fall_oe,rise_oe,expect_sdo,fall_sdo,rise_sdo,expect_reject,actual_reject,release_oe_8ns,failed_checks");
        idle_clocks();
        for (int rate = 0; rate < 3; rate++) begin
            // 总周期分别为 20、24、32 ns。包含最窄 45% 低相和 45% 高相，
            // 相位跨越一个 2.5 ns 执行周期，所有采样均避开模拟的零时序竞争。
            low_ps = rate == 0 ? 9000 : rate == 1 ? 12000 : 17600;
            high_ps = rate == 0 ? 11000 : rate == 1 ? 12000 : 14400;
            for (int phase = 0; phase < 5; phase++) begin
                pattern = 8'(32'h63 + 16*rate + phase);
                frame($sformatf("scan_w_%0d_%0d", rate, phase), 24, {40'd0, 16'h000a, pattern},
                      0, 64'hff, low_ps, high_ps, 113 + 500*phase);
                frame($sformatf("scan_r_%0d_%0d", rate, phase), 24, 64'h800aff,
                      {56'd0, pattern}, 64'hff, low_ps, high_ps, 113 + 500*phase);
            end
        end
        idle_clocks();

        // 图 68/71 的短地址、图 69/72 的多实体字节流及单指令重起。
        // 0x29→0x28/27→0x26/25 下降，保持 gain unity、设置 OFFSET=0x0134。
        frame("short_enable", 24, 64'h000108, 0, 64'hff);
        frame("short_single_w", 16, 64'h0a9d, 0, 64'hff);
        frame("short_single_r", 16, 64'h8aff, 64'h9d, 64'hff);
        frame("short_stream_w", 48, 64'h290002000134, 0, 64'h00ffffffffff);
        frame("short_stream_r", 48, 64'ha9ffffffffff, 64'h000002000134, 64'h00ffffffffff);
        frame("single_inst_enable", 16, 64'h0188, 0, 64'hff);
        frame("single_inst_two_commands", 32, 64'h8aff83ff, 64'h009d0007, 64'h00ff00ff);
        frame("long_restore", 16, 64'h0100, 0, 64'hff);

        // 半字节和严格实体首字节中止后均回读原值，区别于单纯计数报错。
        frame("abort_4bits", 20, 64'h000ab, 0, 64'hf, 9000, 11000, 113, 0, 8000, 7);
        frame("abort_4bits_readback", 24, 64'h800aff, 64'h9d, 64'hff);
        frame("abort_half_entity", 24, 64'h002609, 0, 64'hff, 9000, 11000, 113, 0, 8000, 5);
        frame("abort_half_entity_readback", 32, 64'h8026ffff, 64'h0134, 64'hffff);

`ifdef CFG_SPI_LVDS_PROFILE
        run_lvds_config();
`endif

        // 明确从普通帧得到已知旧值。两个边界负例之间重新建立基准，
        // 避免一个丢位使后续另一用例的回读也产生误导。
        frame("tail_zero_write", 24, 64'h000ac7, 0, 64'hff, 9000, 11000, 113, 0, 0);
        frame("tail_zero_readback", 24, 64'h800aff, 64'hc7, 64'hff);
        frame("edge_reseed_1", 24, 64'h000aa5, 0, 64'hff);
        frame("lead_zero_write", 24, 64'h000a69, 0, 64'hff, 9000, 11000, 113, 2);
        frame("lead_zero_readback", 24, 64'h800aff, 64'h69, 64'hff);
        frame("edge_reseed_2", 24, 64'h000aa5, 0, 64'hff);

        // POR 两位未清，长附带状态为 0x00C0。先清 0x11 的被测接口错误。
        frame("clear_interface_status", 24, 64'h00119f, 0, 64'hff);
        frame("send_status_enable", 24, 64'h001033, 0, 64'hff);
        frame("cs_and_first_fall_status", 24, 64'h800aff, 64'h00c0a5,
              64'hffffff, 9000, 11000, 113, 1);
        frame("crc_enable", 24, 64'h001072, 64'h00c000, 64'hffffff);

        // Golden CRC-8 独立按字节预计算。写 [00,0A,C6]→56；
        // 读主机 [80,0A,FF]→F2，器件 [80,0A,C6]→5D。
        frame("crc_write", 32, 64'h000ac656, 64'h00c00056, 64'hffffffff);
        frame("crc_read", 32, 64'h800afff2, 64'h00c0c65d, 64'hffffffff);
        frame("crc_wrong", 32, 64'h000a7400, 64'h00c00041, 64'hffffffff,
              9000, 11000, 113, 0, 8000, 6);
        frame("crc_wrong_readback", 32, 64'h800afff2, 64'h00c8c65d, 64'hffffffff);
`ifdef CFG_SPI_CRC_STREAM
        run_crc_stream();
`endif
`ifdef CFG_SPI_INIT_POLL
        run_reset_poll();
`endif
        idle_clocks();
        $display("CFG_SPI_PIN_EDGES_COMPLETE cases=%0d checks=%0d failures=%0d", cases, checks, fails);
        if (fails != 0) $fatal(1, "GPIO0 configuration SPI edge errors");
        $finish;
    end

    initial begin
        #150000000;
        $fatal(1, "GPIO0 configuration SPI watchdog");
    end
endmodule
`default_nettype wire
