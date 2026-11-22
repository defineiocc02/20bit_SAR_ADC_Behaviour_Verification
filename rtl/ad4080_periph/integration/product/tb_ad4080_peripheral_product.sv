`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : tb_ad4080_peripheral_product.sv
// 模块名     : tb_ad4080_peripheral_product
// 版本       : v1.0
// 功能       : 从真正配置 SPI，经固定 DSP 到四银行和完整 I/O 记录。
// 设计说明   : 输入是已重构补码。台架不直接写 SRAM 或中间滤波值。真实
//              SPI 配置沿和宏/CRC 测试绑定均可复查，跨域载荷只通过生产 RTL。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [校准] 同一 DUT 增加 SHIFT=15 的 SPI 实写和留出码整记录比较。
// [SPI] 按 Rev. B p.35 检查无 CRC 返回的写数据相驱动 0 和 CS 后释放。
// [速率] 保留各模式冷启动，使速率组与普通记录组分别验证实际时钟。
// =============================================================================
// 独立皮秒时基先用整数 $time 取边沿，避免仿真器在 ns 域过早取整。
package ad4080_product_test_time;
    timeunit 1ps;
    timeprecision 1ps;
    function automatic longint now_ps();
        return longint'($time);
    endfunction
endpackage

module tb_ad4080_peripheral_product #(
    parameter int CALIBRATION_RUN = 0,
    parameter bit PRODUCT_SPEED_RUN = 1'b0
);
    // =========================================================================
    // 1. 三域时钟及专用试验余数
    // =========================================================================
    // 正向格式取 AA、三个已知数值字及按 68 位前缀长除得到的工程 CRC。
    // 业务验收的三个数值来自固定 DSP 算法，CRC 算法只属于此试验绑定。
    localparam logic [91:0] FRAME_GOLDEN = 92'hAA_0075D_009E7_FF30D_C69FFD;
    // 两个整帧的 24 位余数用独立长除固定。三个理想值是 −300000、0、250000，
    // 同一偏置/增益误差下提供负、零、正，既检查极性也定位 1 LSB 整数边界。
    localparam logic [91:0] NEUTRAL_GOLDEN = 92'hAA_B6694_00050_3D5C2_4AB21A;
    localparam logic [91:0] CALIBRATED_GOLDEN = 92'hAA_B6C20_00000_3D090_C5A11E;
    localparam logic [91:0] FIG99_GOLDEN = 92'hAA_000C8_00258_003E8_CED817;
    localparam logic [91:0] FIG89_W1_GOLDEN = {40'b0, 52'hAA_00123_578475};
    // 在 64 码 DC 上施加 +320 的脉冲，SINC5 N2 的冲激样点为
    // 1/32、10/32、5/32，三次抽取给出 74、164、114。CRC 用独立长除。
    localparam logic [91:0] SINC5_GOLDEN = 92'hAA_0004A_000A4_00072_718B93;
    // PF1 基率2、总4。独立 (1+z^-1)^5 卷积后将第70/72/74个 CIC 字
    // 分别与现行 Q22 FIR 卷积。第129笔原码加65536，得到744/1481/480，
    // 三字均离开1024基线，可区分错误旁路与早/晚一个抽取相位。
    localparam logic [91:0] PF1_GOLDEN = 92'hAA_002E8_005C9_001E0_024943;
    localparam int FIXTURE_GAIN_SHIFT = CALIBRATION_RUN == 0 ? 14 : 15;
    // 50 MHz 配置 SPI 的八倍过采样和 25 ns 的 CNV 同时约束核心时基。
    localparam int FIXTURE_CORE_HZ = PRODUCT_SPEED_RUN ? 400_000_000 : 100_000_000;
    localparam realtime CORE_HALF_PERIOD = PRODUCT_SPEED_RUN ? 1250ps : 5ns;
    localparam logic [23:0] CRC_INIT = 24'hB704CE, CRC_POLY = 24'h864CFB;
    localparam logic [7:0] OTP_TEST_WORD = 8'hA5;

    logic clk_aon_i = 0, clk_core_i = 0, clk_io_i = 0; // 不同周期，位对只在 I/O 域采样
    bit aon_clock_enabled = 1;                 // AON 停钟时真实配置命令只能保留一笔
    bit io_clock_enabled = 1;                  // 显式清除时停钟，证明旧 READ_DONE 不依赖目标沿撤销
    realtime io_half_period = 11ns;           // 结果 SPI 单独在 400 MHz 服务时基下跑 50 MHz DCLK
    logic por_d_ok_async_i = 0, iovdd_ok_async_i = 1;   // 独立外供 I/O 掉电，核心仍保留
    logic ref_ok_async_i = 0;                 // 先测实际无参考冷启动训练，再使参考合格
    logic vdd33_ok_async_i = 1, external_io_i = 1, vdd11_ldo_pd_safe_aon_i = 0;
    logic vdd11_ldo_en_o, iovdd_ldo_en_o;
    logic wake_recover_done_aon_i = 0, wake_recover_req_aon_o;
    logic mode_req_valid_aon_i = 0, sw_reset_req_aon_i = 0;
    logic mode_req_accept_aon_o, sw_reset_accept_aon_o;
    logic otp_adapters_cancel_aon_o, otp_req_valid_aon_o, otp_rsp_ready_aon_o;
    logic otp_verify_req_valid_aon_o, otp_verify_rsp_ready_aon_o;
    logic [7:0] otp_verify_image_aon_o;
    logic otp_rsp_valid_aon_i = 0, otp_verify_rsp_valid_aon_i = 0, otp_verify_rsp_pass_aon_i = 0; // 适配器各一笔

    logic cs_n = 1, sclk = 1, sdi = 0, sdo, sdo_oe;
    logic [3:0] gpio_i = 4'hF;             // GPIO1 低有效滤波清零不可从初值误触发
    logic [3:0] gpio_o, gpio_oe;
    logic [7:0] status_o;
    logic sample_valid_core_i = 0, sample_source_idle_core_i = 1, conversion_allow_core_o; // 静默包含数值尾部排空
    logic cnv_tick_core_i = 0, cnv_data_timing_error_core_o, conversion_timing_error_core_o;
    logic [31:0] sample_tag_core_i = 0;       // 完成码来自哪个源转换，不用观察顺序重建身份
    logic [19:0] sample_signed_core_i;       // 补码负值不在试验端预校正
    logic ext_event_core_i = 0;              // 满史快照用独立同域事件锁存位置
    logic cfg_reject_o, processing_reject_o;
    logic [3:0] cfg_reject_reason_o;

    logic record_command_core_i = 0, record_ready_core_o, record_rejected_core_o;
    logic record_done_core_o, record_failed_core_o, record_available_core_o, capture_incomplete_core_o;
    logic fifo_full_core_o, fifo_empty_core_o, capture_write_loss_core_o; // 区别真容量、水位和写失败
    logic format_bound_io_i = 0, crc_valid_io_i, crc_start_io_o, crc_ready_io_o, crc_cancel_io_o; // 首轮须拒绝
    logic abort_io_i = 0;                    // 快照用例在核完跨界头两字后主动截断
    logic [23:0] crc_io_i, crc_residue_q;  // 本拍的最后载荷必须参与同沿的外部 CRC 回复
    logic beat_valid_io_o, beat_first_io_o, beat_last_io_o, io_record_epoch_o, frame_error_io_o;
    logic [1:0] beat_pair_io_o, beat_kind_io_o;
    logic [2:0] frame_error_code_io_o;
    logic first_payload_word_load_io_o, last_payload_word_load_io_o;
    logic result_dcs_ni = 1, result_dclk_i = 1;      // Mode 3 两引脚空闲为高
    logic [3:0] result_sdo_o, result_sdo_oe_o;       // 原始 A/B/C/D，主机比较实际脚电平
    logic result_spi_ready_io_o;                     // 只为证明主机未等内部预取
    logic cnv_tick_io_i = 0;
    logic [31:0] cnv_tag_io_i = 0;            // 从与 core fixture 同一事件拿 ID
    logic continuous_config_ready_core_o, continuous_cnv_allowed_io_o;
    logic continuous_pair_valid_o, continuous_pair_first_o, continuous_pair_last_o;
    logic [31:0] continuous_pair_tag_o, continuous_frame_missing_tag_o;
    logic [31:0] continuous_pair_source_tag_o;
    logic continuous_pair_repeat_o;                   // 同一真数值字是否作为下一帧保留
    logic continuous_da_fall_o, continuous_da_rise_o, continuous_db_fall_o, continuous_db_rise_o;
    logic continuous_dco_clk_o, continuous_dco_oe_o, continuous_da_oe_o, continuous_db_oe_o;
    logic continuous_cnv_rejected_io_o, continuous_result_rejected_io_o, continuous_frame_missing_io_o;
    logic continuous_result_drop_core_o;
    logic lvds_rx_double_io_o, lvds_cnv_input_select_io_o;
    logic [2:0] lvds_vod_control_io_o;
    logic check_golden = 0;                  // 故障用例主动撤销，不产生成功帧余数
    logic [91:0] wire_record;                 // 头 8、载荷 60、完整余数 24
    int pair_count;                          // 数已交付的 pair，以段标志定位错误槽
    int expected_record_words = 3;          // 仅固定试验源知道此长度，真 DUT 长度由捕获提供
    bit frame_live_q;                         // 首头至尾必须逐 I/O 沿收到有效位对
    bit fig89_trace_active = 0;
    bit fig89_first_seen = 0;                // 起发前的等待拍亦记录，不借上一记录的 pair_count
    int fig89_io_index = 0;
    int fig89_reads_base = 0;                // 量化预取先于首移入，但预取不应清 FULL
    // CNV 的物理写事务在边沿之前已经组合就绪。先取实际受理、地址和数据，
    // 独立保存以便用同一个上升沿坐标与稍后可见的 GPIO 电平作对照。
    bit cnv_write_seen;
    logic [13:0] cnv_write_addr;
    logic [19:0] cnv_write_data;
    longint cnv_edge_time_ps;

    logic bist_request_aon_i = 0, bist_ready_aon_o, bist_grant_aon_o;
    logic bist_start_core_i = 0, bist_start_ready_core_o, bist_reinit_request_core_i = 0;
    logic bist_contents_invalid_core_o, bist_busy_core_o, bist_done_core_o;
    logic [1:0] bist_fail_reason_core_o;
    int physical_writes = 0, physical_reads = 0, config_rejects = 0, bound_rejects = 0; // 对照独立拒绝源
    logic [13:0] last_write_addr;             // 第四银行真正末址，而非推算写计数
    string fixture_group;                    // 每次启动仅赋值一次，随后选择一组真实激励
    bit fixture_ready = 0, normal_io_checked = 0;

    always #50ns clk_aon_i = aon_clock_enabled && !clk_aon_i;
    always #(CORE_HALF_PERIOD) clk_core_i = ~clk_core_i;
    always #(io_half_period) clk_io_i = io_clock_enabled && !clk_io_i;

    ad4080_peripheral_product #(
        .OTP_WORD_WIDTH(8), .GAIN_BOUND(1'b1), .GAIN_K(1), .GAIN_SHIFT(FIXTURE_GAIN_SHIFT),
        .SPI_MULTIBYTE_ORDER(0), .OFFSET_LSB_ADDRESS(37), .SPI_WRITE_SDO_DRIVE(1),
        .P_CNV_TIMING_BOUND(1'b1), .CORE_CLOCK_HZ(FIXTURE_CORE_HZ)
    ) dut (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i,
        .por_d_ok_async_i, .vdd33_ok_async_i, .ref_ok_async_i,
        .iovdd_ok_async_i, .ana_ready_async_i(1'b1), .sw_reset_req_aon_i, .mode_req_valid_aon_i,
        .mode_req_aon_i(2'b10), .wake_recover_done_aon_i,
        .vdd11_ldo_pd_safe_aon_i, .vdd11_ldo_en_o, .iovdd_ldo_en_o,
        .sw_reset_accept_aon_o, .mode_req_accept_aon_o, .wake_recover_req_aon_o,
        .otp_power_good_aon_i(1'b1),
        .otp_adapters_quiescent_aon_i(otp_adapters_cancel_aon_o && !otp_rsp_valid_aon_i && !otp_verify_rsp_valid_aon_i),
        .otp_adapters_cancel_aon_o, .otp_req_valid_aon_o,
        .otp_req_ready_aon_i(!otp_adapters_cancel_aon_o && !otp_rsp_valid_aon_i), .otp_req_addr_aon_o(),
        .otp_rsp_valid_aon_i, .otp_rsp_error_aon_i(1'b0), .otp_rsp_data_aon_i(OTP_TEST_WORD), .otp_rsp_ready_aon_o,
        .otp_verify_req_valid_aon_o,
        .otp_verify_req_ready_aon_i(!otp_adapters_cancel_aon_o && !otp_verify_rsp_valid_aon_i), .otp_verify_image_aon_o,
        .otp_verify_rsp_valid_aon_i, .otp_verify_rsp_pass_aon_i, .otp_verify_rsp_ready_aon_o,
        .sample_valid_core_i, .sample_signed_core_i, .sample_source_idle_core_i, .conversion_allow_core_o,
        .conversion_reset_no(), .sample_window_core_o(),
        .cnv_tick_core_i, .sample_tag_core_i, .cnv_data_timing_error_core_o, .conversion_timing_error_core_o,
        .cs_n, .sclk, .sdi, .sdo, .sdo_oe, .gpio_i, .gpio_o, .gpio_oe,
        .filter_sync_core_i(1'b0), .ext_event_core_i,
        .status_o, .cfg_reject_o, .cfg_reject_reason_o, .processing_reject_o,
        .record_command_core_i, .record_ready_core_o, .record_rejected_core_o, .record_done_core_o, .record_failed_core_o,
        .record_available_core_o, .capture_incomplete_core_o, .fifo_full_core_o, .fifo_empty_core_o,
        .capture_write_loss_core_o, .format_bound_io_i, .crc_service_ready_io_i(!crc_valid_io_i),
        .abort_io_i, .crc_valid_io_i, .crc_error_io_i(1'b0), .crc_io_i,
        .crc_start_io_o, .crc_ready_io_o, .crc_cancel_io_o, .io_record_epoch_o,
        .beat_ready_io_i(1'b1), .beat_valid_io_o, .beat_first_io_o, .beat_last_io_o,
        .beat_pair_io_o, .beat_kind_io_o, .frame_error_io_o, .frame_error_code_io_o,
        .first_payload_word_load_io_o, .last_payload_word_load_io_o,
        .result_dcs_ni, .result_dclk_i, .result_sdo_o, .result_sdo_oe_o, .result_spi_ready_io_o,
        .cnv_tick_io_i, .cnv_tag_io_i, .continuous_config_ready_core_o, .continuous_cnv_allowed_io_o,
        .continuous_pair_valid_o, .continuous_pair_first_o, .continuous_pair_last_o, .continuous_pair_tag_o,
        .continuous_pair_source_tag_o, .continuous_pair_repeat_o,
        .continuous_da_fall_o, .continuous_da_rise_o, .continuous_db_fall_o, .continuous_db_rise_o,
        .continuous_dco_clk_o, .continuous_dco_oe_o, .continuous_da_oe_o, .continuous_db_oe_o,
        .continuous_cnv_rejected_io_o, .continuous_result_rejected_io_o, .continuous_frame_missing_io_o,
        .continuous_frame_missing_tag_o, .continuous_result_drop_core_o,
        .lvds_rx_double_io_o, .lvds_vod_control_io_o, .lvds_cnv_input_select_io_o,
        .bist_request_aon_i, .bist_ready_aon_o, .bist_grant_aon_o, .bist_start_core_i,
        .bist_checkerboard_core_i(1'b0), .bist_start_ready_core_o,
        .bist_reinit_request_core_i, .bist_contents_invalid_core_o, .bist_busy_core_o, .bist_done_core_o,
        .bist_fail_reason_core_o
    );

    // =========================================================================
    // 2. 宏服务、边沿收据与独立时序观察
    // =========================================================================
    // 模拟适配器只接受一个在途 OTP 字并等待其被消费。启动仍走整像校验。
    always @(posedge clk_aon_i) begin
        if ((fixture_group == "control" || fixture_group == "result_spi") && dut.u_services.pwr_cmd_aon)
            $display("POWER_AON t=%0t op=%0d value=%0d accept=%b cs_n=%b clients_idle=%b read_unlocked=%b capture_idle=%b",
                     $time, dut.u_services.pwr_op_aon, dut.u_services.pwr_value_aon, dut.u_services.pwr_accept_aon,
                     cs_n, dut.aon_clients_idle, dut.aon_read_unlocked, dut.aon_capture_clear);
        if (otp_adapters_cancel_aon_o) begin
            otp_rsp_valid_aon_i <= 0;
            otp_verify_rsp_valid_aon_i <= 0;
            otp_verify_rsp_pass_aon_i <= 0;
        end else begin
            if (otp_req_valid_aon_o && !otp_rsp_valid_aon_i) otp_rsp_valid_aon_i <= 1;
            else if (otp_rsp_ready_aon_o) otp_rsp_valid_aon_i <= 0;
            if (otp_verify_req_valid_aon_o && !otp_verify_rsp_valid_aon_i) begin
                otp_verify_rsp_valid_aon_i <= 1;
                otp_verify_rsp_pass_aon_i <= otp_verify_image_aon_o == OTP_TEST_WORD;
            end else if (otp_verify_rsp_ready_aon_o) otp_verify_rsp_valid_aon_i <= 0;
        end
    end

    // 数的是物理端口已经接受的操作。只就绪或只提出有效都不能记为提交。
    always @(posedge clk_core_i) begin
        if (cnv_tick_core_i) begin
            cnv_write_seen <= dut.write_req && dut.write_ready;
            cnv_write_addr <= dut.write_addr;
            cnv_write_data <= dut.write_data;
            cnv_edge_time_ps <= longint'($realtime * 1000.0);
        end
        if (dut.write_req && dut.write_ready) begin
            physical_writes++;
            last_write_addr = dut.write_addr;
        end
        if (dut.read_taken) physical_reads++;
        if (cfg_reject_o) config_rejects++;
        if (processing_reject_o) $fatal(1, "Unexpected rejection in the bound numeric source chain");
    end

    function automatic logic [23:0] crc_step(input logic [23:0] state, input logic [1:0] pair_bits);
        logic [23:0] residue;
        residue = state;
        for (int idx = 1; idx >= 0; idx--)
            residue = (residue << 1) ^ ((residue[23] ^ pair_bits[idx]) ? CRC_POLY : 24'b0);
        return residue;
    endfunction

    task automatic expect_pin(input string case_name, clock_name, signal_name,
                              input int edge_index, input longint time_ps,
                              input int observed, expected);
        $display("PIN_EDGE,%s,%s,%0d,%0d,%s,%0d,%0d,%s", case_name, clock_name, edge_index, time_ps,
                 signal_name, observed, expected, observed == expected ? "PASS" : "FAIL");
        if (observed != expected) $fatal(1, "%s %s edge %0d", case_name, signal_name, edge_index);
    endtask

    // [Fig89] 每一真实逻辑 I/O 时钟都进入 trace，包括等待起发的空拍。
    // 取位对和装载事件于接受前，取管脚标志于同一沿的 NBA 后，保留两者
    // 的归属。起发前延迟由跨域预取决定，实际 pad/DCO 仍是独立边界。
    // 预测下一个槽的位置只使用本次 W1/W3 的固定位数；真实时钟计数 elapsed
    // 始终推进。若内部丢一个 valid，不能由 pair_count 停住而掩盖。
    // 一对数据横跨 FALL/RISE，事件命名是 Tx 字寄存器装载，实际 pad
    // 偏移与板级时钟传递留待物理模型判定。
    always @(posedge clk_io_i) begin : fig89_wire_trace
        int idx, elapsed, frame_pairs, payload_end, last_load_index;
        longint edge_ps;
        bit active, accepted, want_first, want_last, want_full, want_done;
        string figure_name;
        logic [91:0] expected_frame;
        logic [1:0] expected_pair;
        if (fig89_trace_active) begin
            elapsed = fig89_io_index;
            fig89_io_index++;
            edge_ps = longint'($realtime * 1000.0);
            idx = pair_count;
            accepted = beat_valid_io_o;
            active = frame_live_q || (accepted && beat_first_io_o);
            figure_name = expected_record_words == 1 ? "FIG89_W1" : "FIG89";
            expected_frame = expected_record_words == 1 ? FIG89_W1_GOLDEN : FIG99_GOLDEN;
            payload_end = 4 + 10*expected_record_words;
            frame_pairs = payload_end + 12;
            last_load_index = 3+10*(expected_record_words-1);
            want_first = active && (idx == 3);
            want_last = active && (idx == last_load_index);
            want_full = active ? idx < 3 : !fig89_first_seen;
            want_done = active ? idx >= last_load_index : fig89_first_seen;
            if (active) begin
                fig89_first_seen = 1'b1;
                expect_pin(figure_name, "IO", "BEAT_VALID", elapsed, edge_ps, int'(accepted), 1);
                expected_pair = expected_frame[2*frame_pairs-1-2*idx -: 2];
                expect_pin(figure_name, "IO", "BEAT_PAIR", elapsed, edge_ps, int'(beat_pair_io_o), int'(expected_pair));
                expect_pin(figure_name, "IO", "BEAT_KIND", elapsed, edge_ps, int'(beat_kind_io_o), idx < 4 ? 0 : idx < payload_end ? 1 : 2);
                expect_pin(figure_name, "IO", "BEAT_FIRST", elapsed, edge_ps, int'(beat_first_io_o), int'(idx == 0));
                expect_pin(figure_name, "IO", "BEAT_LAST", elapsed, edge_ps, int'(beat_last_io_o), int'(idx == frame_pairs-1));
            end else begin
                $display("PIN_EDGE,%s,IO,%0d,%0d,BEAT_VALID,%0d,UNBOUND,UNBOUND", figure_name, elapsed, edge_ps, int'(accepted));
            end
            expect_pin(figure_name, "IO", "FIRST_WORD_LOAD", elapsed, edge_ps, int'(first_payload_word_load_io_o), int'(want_first));
            expect_pin(figure_name, "IO", "LAST_WORD_LOAD", elapsed, edge_ps, int'(last_payload_word_load_io_o), int'(want_last));
            expect_pin(figure_name, "IO", "BEAT_READY", elapsed, edge_ps, int'(dut.beat_ready_io_i), 1);
            // 实际单口预取数来自独立核心时钟，本拍没有规定的整数阈值。
            // 独立见证程序可据此查已经预取而 FIFO_FULL 尚未清除的拍。
            $display("PIN_EDGE,%s,IO,%0d,%0d,PHYSICAL_READS,%0d,UNBOUND,UNBOUND",
                     figure_name, elapsed, edge_ps, physical_reads-fig89_reads_base);
            #1ps;
            expect_pin(figure_name, "IO", "FIFO_FULL", elapsed, edge_ps, int'(gpio_o[3]), int'(want_full));
            expect_pin(figure_name, "IO", "FIFO_READ_DONE", elapsed, edge_ps, int'(gpio_o[2]), int'(want_done));
        end
    end

    // 余数来自每个真实逻辑交付位对。末载荷须在同一沿给出包含当前位对的
    // CRC，不许在下个时钟弥补一个间隙。黄金由独立多项式长除得到。
    assign crc_valid_io_i = check_golden && beat_valid_io_o && beat_kind_io_o == 2'd1 &&
                            pair_count == (3+10*expected_record_words);
    assign crc_io_i = crc_step(crc_residue_q, beat_pair_io_o);
    always @(posedge clk_io_i) begin
        if (frame_error_io_o && frame_error_code_io_o == 3'd2) bound_rejects++;
        if (!io_record_epoch_o || crc_cancel_io_o) frame_live_q <= 1'b0;
        else if (crc_start_io_o) begin
            crc_residue_q <= CRC_INIT;
            pair_count <= 0;
            wire_record <= '0;
            frame_live_q <= 1'b0;
        end else if (check_golden) begin
            if (frame_live_q && !beat_valid_io_o) $fatal(1, "Started LVDS frame contains an IO clock hole");
            if (beat_valid_io_o) begin
                if (beat_first_io_o != (pair_count == 0) || beat_last_io_o != (pair_count == 15+10*expected_record_words) ||
                    beat_kind_io_o != (pair_count < 4 ? 2'd0 : pair_count < 4+10*expected_record_words ? 2'd1 : 2'd2))
                    $fatal(1, "Incorrect LVDS record framing at pair %0d", pair_count);
                wire_record <= {wire_record[89:0], beat_pair_io_o};
                pair_count <= pair_count + 1;
                if (pair_count < 4+10*expected_record_words) crc_residue_q <= crc_io_i;
                if (crc_valid_io_i && !crc_ready_io_o) $fatal(1, "CRC service did not accept terminal payload result");
                frame_live_q <= !beat_last_io_o;
            end
        end
    end

    // =========================================================================
    // 3. 原配置 SPI 与样本/记录请求
    // =========================================================================
    // [SPI] CPOL 高，预置输入过负沿，在正沿取 SDO。半拍远长于内部同步链，
    // 这段只验协议和实际数值控制，目的不包括配置口极限频率。
    task automatic spi_byte(input logic [7:0] value, output logic [7:0] received, input bit check_write_sdo);
        for (int idx = 7; idx >= 0; idx--) begin
            sdi = value[idx];
            #80ns sclk = 0;
            #80ns sclk = 1;
            received[idx] = sdo;
            // 写相查主机采样点上的有效驱动和零电平。只查数据位，指令相
            // 的状态回传属于独立合同。测试未启用 CRC 返回。
            if (check_write_sdo && (sdo_oe !== 1'b1 || sdo !== 1'b0))
                $fatal(1, "Four-wire no-CRC SPI write bit %0d did not drive SDO to zero", idx);
            #80ns;
        end
    endtask

    task automatic spi_access(input bit read_cmd, input logic [14:0] address,
                              input int nbytes, input logic [15:0] value, output logic [15:0] returned,
                              input bit unpowered_sdo = 0);
        logic [7:0] reply;
        returned = 0;
        @(negedge clk_core_i);
        cs_n = 0;
        repeat (8) @(negedge clk_core_i);
        spi_byte({read_cmd, address[14:8]}, reply, 1'b0);
        spi_byte(address[7:0], reply, 1'b0);
        // 严格长指令和降序实体从高地址开始，完整传输才有配置提交。
        for (int idx = nbytes-1; idx >= 0; idx--) begin
            spi_byte(value[8*idx+:8], reply, !read_cmd && !unpowered_sdo);
            returned[8*idx+:8] = reply;
        end
        // 尾部等同步 CS 退回，检查读端不会仍占着配置 SDO。
        cs_n = 1;
        repeat (8) @(negedge clk_core_i);
        if (sdo_oe !== 1'b0) $fatal(1, "SPI kept SDO ownership after CS");
    endtask

    task automatic write_reg(input logic [14:0] address, input int nbytes, input logic [15:0] value);
        logic [15:0] unused_reply;
        spi_access(0, address, nbytes, value, unused_reply);
    endtask

    task automatic spi_write_without_sdo(input logic [14:0] address, input int nbytes, input logic [15:0] value);
        logic [15:0] unused_reply;
        // IO 掉电时关闭发送端，输入 pad 仍走相同的合法管理写周期。
        spi_access(0, address, nbytes, value, unused_reply, 1'b1);
    endtask

    task automatic audit_config_sdo();
        logic [15:0] readback;
        // 通用暂存寄存器的正常写不受捕获、滤波或校准状态影响。写时每位
        // 检查 SDO 驱动，片选后检查释放，再读回以确认该笔是有效配置写。
        write_reg(15'h000A, 1, 16'h00A5);
        spi_access(1, 15'h000A, 1, 0, readback);
        if (readback != 16'h00A5) $fatal(1, "SPI scratch write did not commit");
        $display("PASS four-wire config write drives SDO zero in data phase and releases it after CS");
        spi_access(1, 15'h0004, 1, 0, readback);
        if (readback != 16'h0050) $fatal(1, "Bound identification low byte did not read 0x50");
        spi_access(1, 15'h0005, 1, 0, readback);
        if (readback != 16'h0000) $fatal(1, "Bound identification high byte did not read 0x00");
        write_reg(15'h0001, 1, 16'h0008);
        short_config_access(0, 7'h0A, 8'h3C, readback[7:0]);
        short_config_access(1, 7'h0A, 8'h00, readback[7:0]);
        if (readback[7:0] != 8'h3C) $fatal(1, "Short 7-bit address scratch readback");
        short_config_access(0, 7'h01, 8'h00, readback[7:0]);
        $display("PASS configured product ID 0x0050 and actual short scratch SPI write/read");
    endtask

    task automatic short_config_access(input bit read_cmd, input logic [6:0] addr,
                                       input logic [7:0] wdata, output logic [7:0] rdata);
        logic [7:0] unused_instr;
        @(negedge clk_core_i);
        cs_n = 0;
        repeat (8) @(negedge clk_core_i);
        spi_byte({read_cmd, addr}, unused_instr, 1'b0);
        spi_byte(wdata, rdata, !read_cmd);
        cs_n = 1;
        repeat (8) @(negedge clk_core_i);
        if (sdo_oe !== 1'b0) $fatal(1, "Short SPI retained SDO after CS");
    endtask

    // [时基] 100 MHz 核心中每六拍送一次真实译码 CNV 上升事件。样本在
    // 活动沿前的半周期建立，给已接受的原数值两拍及滤波尾足够处理间隔。
    // 统计索引仅在有 cnv_tick 的核心上升沿增加，SPI 慢命令和空 core 拍
    // 无法成为隐含 CNV。校准前后、满深度和事件回归均走这一任务。
    task automatic cnv_step(input bit with_sample, input logic [19:0] word, input int idle_core_cycles = 5);
        // 留出同步 GPIO/输入相位裕量，也让 E1 后释放可在 E2 前越过两级同步器。
        repeat (idle_core_cycles) @(negedge clk_core_i);
        cnv_tick_core_i = 1'b1;
        sample_valid_core_i = with_sample;
        sample_signed_core_i = word;
        @(negedge clk_core_i);
        cnv_tick_core_i = 1'b0;
        sample_valid_core_i = 1'b0;
        if (cnv_data_timing_error_core_o || conversion_timing_error_core_o)
            $fatal(1, "CNV fixture fault at %0t: data=%b spacing=%b", $time,
                   cnv_data_timing_error_core_o, conversion_timing_error_core_o);
    endtask

    task automatic begin_bypass();
        sample_source_idle_core_i = 0;
        cnv_step(1'b0, '0);  // E1 建批，前次转换不存在本批可送原始码
    endtask

    task automatic finish_cnv_batch();
        // 在完整块边界不给下一 ADC 码，两个真实 CNV 继续公布、写入末字。
        // 下游随后才看见来源静默，不能提早锁存未完成记录。
        cnv_step(1'b0, '0);
        cnv_step(1'b0, '0);
        sample_source_idle_core_i = 1;
    endtask

    task automatic quartet(input int a,b,c,d);
        cnv_step(1'b1, 20'(a));
        cnv_step(1'b1, 20'(b));
        cnv_step(1'b1, 20'(c));
        cnv_step(1'b1, 20'(d));
    endtask

    task automatic request_record();
        // 可判断资格与数据可发送是两个事件，空与未完成也走真实请求沿。
        // 正向和反例使用相同启动程序，不能靠试验层不发命令模拟拒绝。
        wait (record_ready_core_o);
        @(negedge clk_core_i);
        record_command_core_i = 1;
        @(posedge clk_core_i);
        if (!record_ready_core_o) $fatal(1, "Record command lost qualified core acceptance");
        @(negedge clk_core_i);
        record_command_core_i = 0;
    endtask

    task automatic write_one_result(input logic [19:0] word);
        // 独立单字记录仍经 E1 建批和两次实际退场 CNV，原数值链无需替换。
        begin_bypass();
        cnv_step(1'b1, word);
        finish_cnv_batch();
    endtask

    // [实写] 每次 CS 限围一个完整双字节实体，经原严格 SPI 译码进入 CSR。
    // 高地址发高字节，尾部再独立回读两实体，保证读出有符号字节和增益均未换序。
    task automatic configure_calibration(input logic [15:0] offset_word, gain_word);
        logic [15:0] offset_back, gain_back;
        int rejects_before;
        rejects_before = config_rejects;
        write_reg(15'h0026, 2, offset_word);
        write_reg(15'h0028, 2, gain_word);
        spi_access(1, 15'h0026, 2, 0, offset_back);
        spi_access(1, 15'h0028, 2, 0, gain_back);
        if (offset_back != offset_word || gain_back != gain_word || config_rejects != rejects_before)
            $fatal(1, "Calibration register readback got %04h %04h expected %04h %04h", offset_back, gain_back,
                   offset_word, gain_word);
        $display("CAL_SPI readback OFFSET=%04h GAIN=%04h", offset_back, gain_back);
    endtask

    // 相同三笔原始码分别进中性配置和已校准配置。两轮清晰地按原队列
    // 完成/退休，接收器只观察真实 SRAM 读码和输出位对。
    task automatic replay_calibration(input string phase, input logic [91:0] expected);
        int initial_writes, initial_reads;
        initial_writes = physical_writes;
        initial_reads = physical_reads;
        write_reg(15'h001C, 1, 16'h0001);
        wait (capture_incomplete_core_o);
        begin_bypass();
        // 负、零、正的参考码不在训练集中。原始补码包含人为施加通道误差，
        // 这里在两次回归都送相同码，唯一数值变化应来自实际 SPI 寄存器。
        cnv_step(1'b1, 20'hB6694);
        cnv_step(1'b1, 20'h00050);
        cnv_step(1'b1, 20'h3D5C2);
        finish_cnv_batch();
        if (physical_writes != initial_writes+3 || !record_available_core_o || capture_incomplete_core_o)
            $fatal(1, "Calibration replay did not write one complete three-sample record");
        request_record();
        wait (record_done_core_o);
        @(negedge clk_core_i);
        if (physical_reads != initial_reads+3 || pair_count != 46 || wire_record != expected ||
            !fifo_empty_core_o || record_available_core_o)
            $fatal(1, "Calibration %s record got %023h expected %023h", phase, wire_record, expected);
        $display("CAL_RECORD %s 46 pairs %023h signed samples %0d %0d %0d", phase, wire_record,
                 $signed(wire_record[83:64]), $signed(wire_record[63:44]), $signed(wire_record[43:24]));
    endtask

    task automatic audit_calibration();
        logic [15:0] offset_word, gain_word;
        bit have_offset, have_gain;
        // 仅使用 runner 实算的主机计划。遗漏 plusarg 立刻失败，不让示例
        // 常量隐式填充，从而确保 RTL 消费的是本次实际 CLI 字节。
        have_offset = $value$plusargs("offset_word=%h", offset_word);
        have_gain = $value$plusargs("gain_word=%h", gain_word);
        if (!have_offset || !have_gain) $fatal(1, "Calibration CLI words missing");
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h001E, 2, 16'h0003);
        @(negedge clk_io_i);
        format_bound_io_i = 1;
        check_golden = 1;
        configure_calibration(16'h0000, 16'h0200);
        replay_calibration("neutral", NEUTRAL_GOLDEN);
        configure_calibration(offset_word, gain_word);
        replay_calibration("corrected", CALIBRATED_GOLDEN);
        if (capture_write_loss_core_o) $fatal(1, "Product lost a held-out sample");
        $display("AD4080_PRODUCT_CALIBRATION PASS");
    endtask

    task automatic audit_full_and_bist(input int first_write_count, first_read_count);
        // 在同一栈改回数值旁路和满深度，验证真实 16K 地址跨四银行写入。
        // 逻辑已完成与物理满是不同状态，第一条三字记录特意用较小水位。
        write_reg(15'h0026, 2, 16'h0000);
        write_reg(15'h0028, 2, 16'h0200);
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h001E, 2, 16'h4000);
        write_reg(15'h001C, 1, 16'h0001);
        wait (capture_incomplete_core_o);
        begin_bypass();
        for (int idx = 0; idx < 16384; idx++) begin
            cnv_step(1'b1, 20'(idx));
        end
        // 连续真实原始码在正常捕获结束后仍可到达 DSP。两个不该写入的字
        // 同时推进最后两个在途合法字到写沿，随后仍显式排空数值链。
        cnv_step(1'b1, 20'h5E321);
        cnv_step(1'b1, 20'h5E322);
        finish_cnv_batch();
        if (!fifo_full_core_o || !record_available_core_o || capture_incomplete_core_o ||
            last_write_addr != 14'h3FFF || physical_writes != first_write_count+16384)
            $fatal(1, "Physical full/bank traversal not reached");
        if (physical_writes != first_write_count+16384) $fatal(1, "Full record accepted an extra write");
        $display("PASS physical full: 16384 accepted writes and address 3fff, extra sample held outside record");

        // IOVDD 在任何读之前撤销，允许已完成记录保留。再次开启后，只有
        // 已开始消费的记录在掉电时作废；地址不能回到旧半条记录拼帧。
        @(negedge clk_io_i);
        iovdd_ok_async_i = 0;
        repeat (6) @(negedge clk_io_i);
        if (!fifo_full_core_o || record_ready_core_o || beat_valid_io_o)
            $fatal(1, "Power loss before read changed retained record");
        iovdd_ok_async_i = 1;
        request_record();
        wait (beat_valid_io_o && beat_kind_io_o == 2'd1);
        // 这里已见到真实载荷。再查单口读计数，确保确有地址被消费，
        // 而不是只截断同步头便把丢弃旧记录当成已经测到。
        @(negedge clk_io_i);
        if (physical_reads <= first_read_count) $fatal(1, "Expected an actual read before I/O power loss");
        iovdd_ok_async_i = 0;
        #1ns;
        if (beat_valid_io_o || io_record_epoch_o) $fatal(1, "I/O epoch failed to revoke wire record");
        wait (record_failed_core_o);
        wait (fifo_empty_core_o && !record_available_core_o);
        @(negedge clk_io_i);
        iovdd_ok_async_i = 1;
        request_record();
        if (!record_rejected_core_o) $fatal(1, "Aborted record was reused after power return");
        $display("PASS IOVDD retained unread record, then revoked and cleared the partially consumed record");

        // BIST 接管前配置一条独立已完成记录。测试实际访问同一 SRAM，随后
        // 元数据清除要先于外部已重建答复，防止将自测写入作为可读结果。
        write_reg(15'h001E, 2, 16'h0001);
        write_reg(15'h001C, 1, 16'h0001);
        write_one_result(20'h155AA);
        wait (record_available_core_o);
        @(negedge clk_aon_i);
        bist_request_aon_i = 1;
        wait (bist_grant_aon_o && bist_start_ready_core_o);
        @(negedge clk_core_i);
        bist_start_core_i = 1;
        @(negedge clk_core_i);
        bist_start_core_i = 0;
        wait (bist_busy_core_o && dut.u_services.store_req);
        if (!bist_contents_invalid_core_o || record_available_core_o || beat_valid_io_o)
            $fatal(1, "BIST failed to isolate the completed record");
        @(negedge clk_aon_i);
        bist_request_aon_i = 0;
        wait (bist_done_core_o);
        @(negedge clk_core_i);
        if (bist_fail_reason_core_o != 2'd3 || !bist_contents_invalid_core_o)
            $fatal(1, "BIST abort bypassed metadata retirement");
        // 本轮不等整条 March 完成。中止后的部分覆盖才需要验证指针
        // 作废、已重建握手和新采集的先后次序。
        bist_reinit_request_core_i = 1;
        wait (!bist_contents_invalid_core_o && conversion_allow_core_o && fifo_empty_core_o);
        @(negedge clk_core_i);
        bist_reinit_request_core_i = 0;
        if (record_available_core_o) $fatal(1, "Pretest record metadata survived BIST recovery");
        write_reg(15'h001C, 1, 16'h0001);
        write_one_result(20'h13007);
        if (!record_available_core_o || last_write_addr != 0) $fatal(1, "Fresh capture did not restart at bank 0");
        $display("PASS MBIST same-bank handoff, reason 3 abort, metadata clear, fresh record address zero");
    endtask

    // [满史] 同一 SRAM 再环写，事件在 0x3FFB，四个事件后字填到 0x3FFE。
    // 冻结路径必须从最后一银行末尾 0x3FFF 开始，再回绕到已覆盖的首址。
    task automatic audit_snapshot_prefix();
        int initial_writes;
        logic [15:0] event_address;
        logic [39:0] prefix;
        initial_writes = physical_writes;
        write_reg(15'h001C, 1, 16'h0000);
        write_reg(15'h001E, 2, 16'h0004);
        write_reg(15'h001C, 1, 16'h0003);
        wait (capture_incomplete_core_o);

        // 先填满 16K，再覆盖 16,379 笔。每个字等于提交次序，因而从
        // 首地址读 0x04000 才算真覆盖，不能由原地址或默认数据假通过。
        begin_bypass();
        for (int idx = 0; idx < 32765; idx++) begin
            cnv_step(1'b1, 20'(idx));
        end
        if (!fifo_full_core_o || physical_writes != initial_writes+32763 || !capture_incomplete_core_o)
            $fatal(1, "Pre-trigger full-history ring did not preserve actual overwrite flow");

        // 连续 CNV 上保留两个在途码，测试事件在两次真实写之间。事件回写
        // 依据已接受地址，余下四笔由真实管线推进，不能把在途按已写计。
        ext_event_core_i = 1;
        @(negedge clk_core_i);
        ext_event_core_i = 0;
        for (int idx = 32765; idx < 32769; idx++) cnv_step(1'b1, 20'(idx));
        finish_cnv_batch();
        if (!record_available_core_o || capture_incomplete_core_o || physical_writes != initial_writes+32767)
            $fatal(1, "Mode11 capture did not freeze after four post-event physical writes");
        spi_access(1, 15'h001E, 2, 0, event_address);
        if (event_address != 16'h3FFB) $fatal(1, "Original mode11 event writeback got %04h", event_address);

        request_record();
        // 两个时钟域独立观测：核心核真实读地址，I/O 核头后实际输出数据。
        // 队列可以预取，接收器不能等 SRAM 地址比较结束才开始收位对。
        fork
            begin
                for (int idx = 0; idx < 2; idx++) begin
                    do @(posedge clk_core_i); while (!dut.read_taken);
                    if (dut.read_addr != (idx == 0 ? 14'h3FFF : 14'h0000))
                        $fatal(1, "Mode11 frozen address %0d is %04h", idx, dut.read_addr);
                end
            end
            begin
                prefix = '0;
                for (int idx = 0; idx < 20; idx++) begin
                    do @(posedge clk_io_i); while (!(beat_valid_io_o && beat_kind_io_o == 2'd1));
                    prefix = {prefix[37:0], beat_pair_io_o};
                end
            end
        join
        if (prefix != 40'h03FFF_04000) $fatal(1, "Mode11 frozen wire prefix got %010h", prefix);

        // 本用例只验跨环前缀和真实作废，随后主动截断。完整 CRC 头尾已由
        // 小记录回归覆盖，未把中止的 16K 大记录统计成成功输出。
        @(negedge clk_io_i);
        abort_io_i = 1;
        @(negedge clk_io_i);
        abort_io_i = 0;
        wait (record_failed_core_o);
        wait (fifo_empty_core_o && !record_available_core_o);
        $display("PASS mode11 full-history: event 3ffb, frozen SRAM addresses 3fff->0000, wire prefix %010h", prefix);
    endtask

    // =========================================================================
    // 4. 生产绑定验收
    // =========================================================================
    `include "continuous_pin_tasks.svh"
    `include "product_speed_tasks.svh"
    `include "result_spi_tasks.svh"
    `include "control_pin_tasks.svh"
    `include "full_record_tasks.svh"

    task automatic audit_fixed_word_and_por();
        logic [15:0] csr;
        // 配置/OTP 在无参考下可就绪。写实际 0x15 固定字选择，不从试验
        // 直接给 IO mapper 一个期望值；先验证从未见参考的 cold gate。
        wait (continuous_config_ready_core_o);
        write_reg(15'h0015, 1, 16'h0050);
        wait (continuous_config_ready_core_o);
        repeat (12) begin
            @(negedge clk_io_i);
            if (continuous_pair_valid_o || continuous_da_oe_o) $fatal(1, "Cold REF absence emitted fixed data");
        end
        $display("POWER_EDGE,FIXED_WORD,IO,0,%0d,COLD_REF_NEVER_SEEN,%0d,0,PASS",
                 longint'($realtime * 1000.0), continuous_pair_valid_o);

        write_reg(15'h0014, 1, 16'h0008);
        spi_access(1, 15'h0014, 1, 0, csr);
        if (csr[3]) $fatal(1, "POR_ANA initial W1C failed");
        @(negedge clk_aon_i);
        ref_ok_async_i = 1'b1;
        wait (conversion_allow_core_o);
        collect_continuous_word(20'hAC5D6, 0, "FIXED_WORD");

        // 曾合格后掉参考，数值转换关闭，固定字仍为独立序列。POR_ANA
        // 真实进原配置 bank 的 sticky，必须可以通过 0x14 读回和 W1C。
        @(negedge clk_aon_i);
        ref_ok_async_i = 1'b0;
        repeat (6) @(negedge clk_aon_i);
        collect_continuous_word(20'hAC5D6, 0, "FIXED_WORD_AFTER_REF_LOSS");
        spi_access(1, 15'h0014, 1, 0, csr);
        if (csr[3] !== 1'b1) $fatal(1, "Actual 0x14 POR_ANA did not latch REFIN loss");
        $display("POWER_EDGE,POR_ANA,SPI,1,%0d,STICKY_ON_REF_LOSS,%0d,1,PASS", longint'($realtime * 1000.0), csr[3]);
        write_reg(15'h0014, 1, 16'h0008);
        spi_access(1, 15'h0014, 1, 0, csr);
        if (csr[3] !== 1'b0) $fatal(1, "Actual 0x14 POR_ANA did not clear");
        $display("POWER_EDGE,POR_ANA,SPI,2,%0d,CLEARED_BY_W1C,%0d,0,PASS", longint'($realtime * 1000.0), csr[3]);
        ref_ok_async_i = 1'b1;
        wait (conversion_allow_core_o);
        write_reg(15'h0015, 1, 16'h0040);
        wait (continuous_config_ready_core_o && record_ready_core_o);
        $display("PASS real 0x15 fixed word REF-history interlock and POR_ANA SPI W1C");
    endtask

    task automatic audit_lvds_pad_controls();
        logic [15:0] returned;
        // 0x17 保持真实 CSR 的写值，再分别比对已跨域应用的宏控制。
        // F0 含非法 VOD 组合，宏请求回到 010，CLK 终端 bit7 仍成功传递。
        write_reg(15'h0017, 1, 16'h00F0);
        wait (continuous_config_ready_core_o);
        spi_access(1, 15'h0017, 1, 0, returned);
        if (returned != 16'h00F0 || !lvds_rx_double_io_o || lvds_vod_control_io_o != 3'b010)
            $fatal(1, "Invalid VOD fallback or CLK termination configuration did not apply");
        $display("PAD_CONTROL,0X17,READ=%02h,RX_DOUBLE=%0d,VOD=%0d,PASS",
                 returned[7:0], lvds_rx_double_io_o, lvds_vod_control_io_o);
        write_reg(15'h0017, 1, 16'h0040);
        wait (continuous_config_ready_core_o);
        if (lvds_rx_double_io_o || lvds_vod_control_io_o != 3'b100)
            $fatal(1, "Legal VOD/CLK termination macro code did not apply");
        write_reg(15'h0016, 1, 16'h0001);
        wait (continuous_config_ready_core_o);
        spi_access(1, 15'h0016, 1, 0, returned);
        if (returned != 16'h0001 || !lvds_cnv_input_select_io_o) $fatal(1, "CNV input pad selection did not apply");
        $display("PAD_CONTROL,0X16,READ=%02h,CNV_SELECT=%0d,PASS", returned[7:0], lvds_cnv_input_select_io_o);
        // 后续图示实验采用普通单 lane 和默认宏设置。这一台架采纳的是
        // 生效后的数字控制位，电气输出幅值及阈值由外部真实宏确认。
        write_reg(15'h0016, 1, 16'h0000);
        wait (continuous_config_ready_core_o);
        write_reg(15'h0017, 1, 16'h0020);
        wait (continuous_config_ready_core_o);
        if (lvds_cnv_input_select_io_o || lvds_vod_control_io_o != 3'b010)
            $fatal(1, "Restored pad controls did not apply");
    endtask

    task automatic audit_fig83();
        logic [15:0] csr;
        int expected_alert;
        // GPIO2 选择低有效 ALERT。源结果对应前一个已译码 CNV；阈值穿越
        // 发生在 E3，输入回到阈内始于 E6，并不把完成码的时刻当作转换起点。
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h0022, 2, 16'h0004);
        write_reg(15'h0019, 1, 16'h000D);
        write_reg(15'h001A, 1, 16'h0000);
        write_reg(15'h001B, 1, 16'h0006);
        write_reg(15'h001C, 1, 16'h00C0);
        sample_source_idle_core_i = 0;
        if (!gpio_oe[2]) $fatal(1, "Fig83 ALERT not connected to GPIO2");
        for (int e = 1; e <= 8; e++) begin
            cnv_step(e >= 2, (e >= 4 && e <= 6) ? 20'd1300 : 20'd0);
            expected_alert = e < 5 || e >= 8 ? 1 : 0;
            expect_pin("FIG83", "CNV", "INPUT_ABOVE", e, cnv_edge_time_ps, int'(e >= 3 && e < 6), int'(e >= 3 && e < 6));
            expect_pin("FIG83", "CNV", "ALERT_N", e, cnv_edge_time_ps, int'(gpio_o[2]), expected_alert);
            expect_pin("FIG83", "CNV", "HI_STATUS", e, cnv_edge_time_ps, int'(status_o[5]), int'(e >= 5));
        end
        // ALERT 已因低输入解除，HI_STATUS 仍保留历史。先通过独立片选
        // 实际读，再写 W1C 和读回，避免直接查看 CSR 层内条件证明自己。
        finish_cnv_batch();
        spi_access(1, 15'h0014, 1, 0, csr);
        expect_pin("FIG83", "SPI", "HI_STATUS_BEFORE_W1C", 1, longint'($realtime * 1000.0), int'(csr[5]), 1);
        write_reg(15'h0014, 1, 16'h0020);
        spi_access(1, 15'h0014, 1, 0, csr);
        expect_pin("FIG83", "SPI", "HI_STATUS_AFTER_W1C", 2, longint'($realtime * 1000.0), int'(csr[5]), 0);
        $display("PASS Fig83 real CNV threshold +2 assertion/release, sticky SPI status and W1C");
    endtask

    task automatic audit_fig99();
        int initial_writes, initial_reads;
        bit write_expected;
        logic [15:0] csr;
        longint sync_transition_ps, window_ps, required_window_ps;
        initial_writes = physical_writes;
        initial_reads = physical_reads;
        // Fig99 走原 SINC1 N2，GPIO1 SYNC，GPIO2 RDY，GPIO3 FULL。
        // W=3；三对不同 ADC 完成码的均值为 200、600、1000。
        write_reg(15'h0029, 1, 16'h0001);
        write_reg(15'h001E, 2, 16'h0003);
        write_reg(15'h0019, 1, 16'h000D);
        write_reg(15'h001A, 1, 16'h0080);
        write_reg(15'h001B, 1, 16'h0013);
        write_reg(15'h001C, 1, 16'h0001);
        wait (capture_incomplete_core_o);
        if (!gpio_oe[2] || !gpio_oe[3]) $fatal(1, "Fig99 RDY/FULL GPIO output not enabled");
        @(negedge clk_core_i);
        sample_source_idle_core_i = 0;
        gpio_i[1] = 1'b0;
        sync_transition_ps = longint'($realtime * 1000.0);
        for (int e = 1; e <= 10; e++) begin
            // E3 至 E8 是真实已完成码输入窗口。E9/E10 保留译码 CNV 的
            // 物理拍点，但停止向 DSP 提交下一组，待最后字公布并写入。
            cnv_step(e >= 3 && e <= 8, 20'(100 + 200*(e-3)));
            // 输入由测试平台施加，数值用于逻辑样本对齐，板级传播占空另验。
            if (e <= 2) begin
                window_ps = cnv_edge_time_ps - sync_transition_ps;
                required_window_ps = e == 1 ? 15000 : 5000;
                $display("PIN_EDGE,FIG99,CNV,%0d,%0d,FILTER_SYNC_STABLE_PS,%0d,>=%0d,%s",
                         e, cnv_edge_time_ps, window_ps, required_window_ps,
                         window_ps >= required_window_ps ? "PASS" : "FAIL");
                if (window_ps < required_window_ps) $fatal(1, "Fig99 FILTER_SYNC stimulus setup");
            end
            write_expected = e == 6 || e == 8 || e == 10;
            expect_pin("FIG99", "CNV", "FILTER_SYNC_N", e, cnv_edge_time_ps, int'(gpio_i[1]), int'(e != 1));
            expect_pin("FIG99", "CNV", "FILTER_RESULT_RDY_N", e, cnv_edge_time_ps, int'(gpio_o[2]),
                       int'(e != 5 && e != 7 && e != 9));
            expect_pin("FIG99", "CNV", "FIFO_WRITE", e, cnv_edge_time_ps, int'(cnv_write_seen), int'(write_expected));
            expect_pin("FIG99", "CNV", "FIFO_FULL", e, cnv_edge_time_ps, int'(gpio_o[3]), int'(e == 10));
            if (write_expected)
            begin
                // 地址和值均取该 CNV 边沿前实际到达的单口交易，不从
                // 输出黄金反填。三组独立原始码均值固定为 200、600、1000。
                expect_pin("FIG99", "CNV", "FIFO_WRITE_ADDR", e, cnv_edge_time_ps, int'(cnv_write_addr), (e-6)/2);
                expect_pin("FIG99", "CNV", "FIFO_WRITE_DATA", e, cnv_edge_time_ps, int'($signed(cnv_write_data)), 200 + 400*((e-6)/2));
            end
            if (e == 1) begin
                gpio_i[1] = 1'b1;
                sync_transition_ps = longint'($realtime * 1000.0);
            end
        end
        sample_source_idle_core_i = 1;
        if (physical_writes != initial_writes+3 || !record_available_core_o)
            $fatal(1, "Fig99 did not leave a real completed three-word record");

        // 切 GPIO2 从 RDY 到 READ_DONE，在读相并排看两个实体状态输出。
        write_reg(15'h001B, 1, 16'h0012);
        @(negedge clk_io_i);
        format_bound_io_i = 1'b1;
        fig89_trace_active = 1'b1;
        fig89_first_seen = 1'b0;
        fig89_reads_base = physical_reads;
        fig89_io_index = 0;
        check_golden = 1'b1;
        request_record();
        wait (record_done_core_o);
        expect_pin("FIG89", "CORE", "RECORD_DONE_AFTER_CRC", 1, longint'($realtime * 1000.0), int'(record_done_core_o), 1);
        @(negedge clk_core_i);
        fig89_trace_active = 1'b0;
        check_golden = 1'b0;
        if (pair_count != 46 || wire_record != FIG99_GOLDEN || physical_reads != initial_reads+3)
            $fatal(1, "Fig99 real SRAM record mismatch: %023h", wire_record);
        $display("PIN_EDGE,FIG89,PHY,0,0,PAD_DCO_ALIGNMENT,UNBOUND,UNBOUND,UNBOUND");
        $display("PASS Fig99 SINC1 N2 actual writes E6/E8/E10, 46 contiguous Fig89 logical pairs %023h", wire_record);

        spi_access(1, 15'h0014, 1, 0, csr);
        // 两个配置标志是实际 GPIO 层双级同步到核心的副本。记录成功后
        // 才在配置 SPI 可用窗口核读，无须推断 SPI 读数与 IO 在同一相位。
        expect_pin("FIG89", "SPI", "FIFO_FULL_CSR", 1, longint'($realtime * 1000.0), int'(csr[7]), 0);
        expect_pin("FIG89", "SPI", "FIFO_READ_DONE_CSR", 2, longint'($realtime * 1000.0), int'(csr[6]), 1);

        // 不给 I/O 再发时钟。在实际 SPI 清除后，前条 DONE 的撤销必须不
        // 依赖目标时钟沿。复钟后它也不能复活。
        wait (record_ready_core_o);
        @(negedge clk_io_i);
        io_clock_enabled = 0;
        write_reg(15'h001C, 1, 16'h0000);
        expect_pin("FIG89", "STOPPED_IO", "CLEAR_FIFO_READ_DONE", 1, longint'($realtime * 1000.0), int'(gpio_o[2]), 0);
        io_clock_enabled = 1;
        repeat (4) @(negedge clk_io_i);
        expect_pin("FIG89", "IO", "RESUME_FIFO_READ_DONE", 2, longint'($realtime * 1000.0), int'(gpio_o[2]), 0);
    endtask

    task automatic audit_fig89_w1();
        // 单字时首、末负担均来自同一次 TX 字寄存器装载。此时 FIFO_FULL
        // 降低且 FIFO_READ_DONE 上升，末 CRC 仍须十对数据和十二对尾之后。
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h001E, 2, 16'h0001);
        write_reg(15'h001C, 1, 16'h0001);
        write_one_result(20'h00123);
        @(negedge clk_io_i);
        expected_record_words = 1;
        fig89_trace_active = 1;
        fig89_first_seen = 0;
        fig89_reads_base = physical_reads;
        fig89_io_index = 0;
        check_golden = 1;
        request_record();
        wait (record_done_core_o);
        expect_pin("FIG89_W1", "CORE", "RECORD_DONE_AFTER_CRC", 1, longint'($realtime * 1000.0), int'(record_done_core_o), 1);
        @(negedge clk_core_i);
        if (wire_record != FIG89_W1_GOLDEN || pair_count != 26 || physical_reads != fig89_reads_base+1)
            $fatal(1, "Fig89 W1 logical record mismatch");
        fig89_trace_active = 0;
        check_golden = 0;
        expected_record_words = 3;
        // 带着刚结束的 DONE 直接重布防，确认前一代位被清。之后清当前
        // 未启动数据的 arm，后续独立数值链用例仍从干净配置起步。
        wait (record_ready_core_o);
        write_reg(15'h001C, 1, 16'h0001);
        if (gpio_o[2] || gpio_o[3]) $fatal(1, "Rapid rearm retained old physical FIFO state");
        write_reg(15'h001C, 1, 16'h0000);
        $display("PASS Fig89 W1 first/last load same-edge, 26 contiguous logical pairs and rapid rearm");
    endtask

    task automatic expect_filter_edge(input string filter_name, signal_name,
                                      input int edge_index, observed, expected);
        $display("FILTER_EDGE,%s,CNV,%0d,%0d,%s,%0d,%0d,%s", filter_name, edge_index, cnv_edge_time_ps,
                 signal_name, observed, expected, observed == expected ? "PASS" : "FAIL");
        if (observed != expected) $fatal(1, "%s %s CNV edge %0d", filter_name, signal_name, edge_index);
    endtask

    task automatic audit_filter_record(input string filter_name, input logic [7:0] setting,
                                       input int raw_count, impulse_position, baseline, amplitude,
                                       input int first_rdy_edge, output_stride, input logic [91:0] golden);
        int writes_before, reads_before, final_edge, relative_edge, word_index, value;
        bit fifo_write, rdy_n;
        logic [15:0] readback;
        writes_before = physical_writes;
        reads_before = physical_reads;
        // W3 捕获仍来自原采集控制和真实同一 SRAM。整个滤波窗口用物理 GPIO
        // SYNC 开始，以首个实际公布、后续分频和物理写确认数据和状态对齐。
        write_reg(15'h0029, 1, {8'b0, setting});
        spi_access(1, 15'h0029, 1, 0, readback);
        if (readback != {8'b0, setting}) $fatal(1, "%s mode did not commit via actual SPI", filter_name);
        write_reg(15'h001E, 2, 16'h0003);
        write_reg(15'h0019, 1, 16'h000D);
        write_reg(15'h001A, 1, 16'h0080);
        write_reg(15'h001B, 1, 16'h0013);
        write_reg(15'h001C, 1, 16'h0001);
        wait (capture_incomplete_core_o);
        if (!gpio_oe[2] || !gpio_oe[3]) $fatal(1, "%s RDY/FULL GPIO not enabled", filter_name);
        @(negedge clk_core_i);
        sample_source_idle_core_i = 0;
        gpio_i[1] = 0;
        // 实际输入可早于最后三字的公布结束。停供只发生在已完整的原码组，
        // 仍发译码 CNV 以退役在途滤波结果，避免把启动填充值当作记录。
        final_edge = first_rdy_edge + 2*output_stride + 1;
        for (int e = 1; e <= final_edge; e++) begin
            value = baseline + (e-2 == impulse_position ? amplitude : 0);  // E3 对应第 1 个输入。
            // 每次 CNV 相隔十个 100 MHz 核心拍，给校正、四级令牌输送
            // 及 PF1 乘积/归约到原链的采纳沿足够时间。先验时序用 CNV 序号。
            cnv_step(e >= 3 && e <= raw_count+2, 20'(value), 9);
            if (e == 1) gpio_i[1] = 1;
            relative_edge = e-first_rdy_edge;
            rdy_n = relative_edge < 0 || relative_edge % output_stride == output_stride-1 || e == final_edge;
            fifo_write = relative_edge > 0 && relative_edge % output_stride == 1;
            // RDY 在公布边沿后取管脚电平，载荷事务取 NBA 前实际接受的
            // 写事务。同一时间轴上的不同采样区不应进位到下一个 CNV。
            expect_filter_edge(filter_name, "FILTER_RESULT_RDY_N", e, int'(gpio_o[2]), int'(rdy_n));
            expect_filter_edge(filter_name, "FIFO_WRITE", e, int'(cnv_write_seen), int'(fifo_write));
            expect_filter_edge(filter_name, "FIFO_FULL", e, int'(gpio_o[3]), int'(e == final_edge));
            if (fifo_write) begin
                // 黄金按 8 位首头以下三个20位样本分段读取，余数位不参与
                // 物理写数据比较。因此可区分数值误差和最终组帧顺序误差。
                word_index = relative_edge/output_stride;
                expect_filter_edge(filter_name, "FIFO_WRITE_ADDR", e, int'(cnv_write_addr), word_index);
                expect_filter_edge(filter_name, "FIFO_WRITE_DATA", e, int'($signed(cnv_write_data)),
                                   int'($signed(golden[83-20*word_index -: 20])));
            end
        end
        sample_source_idle_core_i = 1;
        if (physical_writes != writes_before+3 || !record_available_core_o)
            $fatal(1, "%s did not publish a completed W3 capture", filter_name);
        // 用原完整记录链复核同三个已写字的地址顺序、尾字和工程余数。
        // 即便 GPIO 正常，跨域丢字亦不能通过本用例。
        @(negedge clk_io_i);
        format_bound_io_i = 1;
        check_golden = 1;
        request_record();
        wait (record_done_core_o);
        @(negedge clk_core_i);
        check_golden = 0;
        if (physical_reads != reads_before+3 || pair_count != 46 || wire_record != golden)
            $fatal(1, "%s record mismatch: %023h instead of %023h", filter_name, wire_record, golden);
        write_reg(15'h001C, 1, 16'h0000);
        $display("PASS %s actual SPI/filter/RDY/three physical words/46 pairs %023h", filter_name, wire_record);
    endtask

    task automatic run_filter_checks();
        // p69 SINC5 R2 在五个完整块及流水后公布第一个真字。冲激落在
        // 第10个原码；起始填充不得写入真实记录。
        audit_filter_record("SINC5_D2", 8'h02, 14, 10, 64, 320, 16, 2, SINC5_GOLDEN);
        // PF1 第35个二倍抽取字有完整历史。p69 在此基率首次公布 E152，
        // 后续每4个 CNV 一字，E161 实际完成第三次写。
        audit_filter_record("PF1_D4", 8'h03, 148, 129, 1024, 65536, 152, 4, PF1_GOLDEN);
    endtask

    task automatic audit_short_cnv_error();
        logic [15:0] readback;
        longint first_ps;
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h0019, 1, 16'h000D);
        write_reg(15'h001B, 1, 16'h0016);
        write_reg(15'h001C, 1, 16'h0010);
        // 两个实际译码上升沿仅隔 20 ns，100 MHz 时基下违反 25 ns。
        // 第二沿同时交付上一结果，避免把缺样错误误认成物理间隔故障。
        begin_bypass();
        first_ps = cnv_edge_time_ps;
        @(negedge clk_core_i);
        cnv_tick_core_i = 1;
        sample_valid_core_i = 1;
        sample_signed_core_i = 20'd64;
        @(negedge clk_core_i);
        cnv_tick_core_i = 0;
        sample_valid_core_i = 0;
        if (cnv_edge_time_ps-first_ps != 20000 || !conversion_timing_error_core_o)
            $fatal(1, "Real product tCONV monitor did not compare the 20 ns spacing");
        repeat (4) @(negedge clk_core_i);
        sample_source_idle_core_i = 1;
        if (!gpio_oe[2] || gpio_o[2]) $fatal(1, "Actual short CNV did not drive GPIO ALERT");
        spi_access(1, 15'h0014, 1, 0, readback);
        if (!readback[2]) $fatal(1, "Actual short CNV did not latch device status");
        $display("POWER_EDGE,ADC_CNV_ERR,SPI,1,%0d,STICKY_SHORT_INTERVAL,%0d,1,PASS",
                 longint'($realtime*1000.0), readback[2]);
        // 清真实状态和重开批是两个事务，W1C 本身即应释放 ALERT。
        write_reg(15'h0014, 1, 16'h0004);
        spi_access(1, 15'h0014, 1, 0, readback);
        if (readback[2] || !gpio_o[2]) $fatal(1, "W1C did not clear device CNV error and ALERT");
        write_reg(15'h001C, 1, 16'h0000);
        if (cnv_data_timing_error_core_o) $fatal(1, "Closing guarded capture did not rearm CNV data path");
        $display("PASS actual 20 ns CNV interval set GPIO ALERT and SPI ADC_CNV_ERR, then W1C cleared");
    endtask

    task automatic audit_product();
        int rejects_before, writes_before, reads_before;
        logic [15:0] readback;
        writes_before = physical_writes;
        reads_before = physical_reads;
        // 图示实验也已经写过同一块 SRAM。后续体积/事件校核以本窗口的
        // 真实提交增量计，不能从仿真初值扣除某个地址银行。
        // 原配置采用已绑定的降序实体。GAIN=0x300 在本工程 K/SHIFT 下
        // 增量为输入的 1/64，OFFSET 为有符号 -64。
        write_reg(15'h0026, 2, 16'h0FC0);
        write_reg(15'h0028, 2, 16'h0300);
        write_reg(15'h0029, 1, 16'h0009);
        write_reg(15'h0022, 2, 16'h0004);
        write_reg(15'h001A, 1, 16'h0086);
        write_reg(15'h001E, 2, 16'h0003);
        spi_access(1, 15'h001E, 2, 0, readback);
        if (readback != 3) $fatal(1, "Watermark SPI readback mismatch");
        // 明确置低 FILTER_SYNC 对齐首个 SINC1 窗口，防止旧历史参与比较。
        write_reg(15'h001C, 1, 16'h00C2);
        wait (capture_incomplete_core_o);

        // 已 ARM 却未捕完时，空闲的物理口不能用来放行软复位、待机、BIST 或读出。
        request_record();
        if (!record_rejected_core_o || physical_reads != reads_before) $fatal(1, "Unfinished record entered readback");
        rejects_before = config_rejects;
        write_reg(15'h0000, 1, 16'h0081);
        if (config_rejects <= rejects_before || !capture_incomplete_core_o)
            $fatal(1, "Original CSR allowed software reset during capture");
        @(negedge clk_aon_i);
        mode_req_valid_aon_i = 1;
        sw_reset_req_aon_i = 1;
        bist_request_aon_i = 1;
        @(posedge clk_aon_i);
        if (mode_req_accept_aon_o || sw_reset_accept_aon_o || bist_ready_aon_o || !conversion_allow_core_o)
            $fatal(1, "Incomplete record lost its AON interlock");
        @(negedge clk_aon_i);
        mode_req_valid_aon_i = 0;
        sw_reset_req_aon_i = 0;
        bist_request_aon_i = 0;
        $display("PASS empty/incomplete rejection through DSP CSR and AON");

        // 阈值码 4 对应 1024 个 20 位码。195 不触发，1105 触发开闸但
        // 本身还在旧窗口，三笔实际写应全部取自事件之后。
        sample_source_idle_core_i = 0;
        @(negedge clk_core_i);
        gpio_i[1] = 0;
        repeat (4) @(negedge clk_core_i);
        cnv_step(1'b0, '0);
        gpio_i[1] = 1;
        cnv_step(1'b0, '0);
        quartet(1280, -256, 0, 0);
        cnv_step(1'b1, 20'd960);  // E7 真正公布第一组，其余输入继续进入下一组
        if (status_o[5] || physical_writes != writes_before) $fatal(1, "Subthreshold burst triggered capture");
        cnv_step(1'b1, 20'd1088);
        cnv_step(1'b1, 20'd1216);
        cnv_step(1'b1, 20'd1344);
        cnv_step(1'b1, 20'd1728);
        if (!status_o[5] || !gpio_oe[0] || gpio_o[0] || physical_writes != writes_before)
            $fatal(1, "Threshold trigger or post-event write ordering mismatch");
        cnv_step(1'b1, 20'd1856);
        cnv_step(1'b1, 20'd1984);
        cnv_step(1'b1, 20'd2112);
        quartet(2368, 2496, 2624, 2752);
        quartet(-3392, -3264, -3136, -3008);
        finish_cnv_batch();
        // FULL 管脚已在最后写沿更新，CSR 是另域采样的副本。
        repeat (3) @(negedge clk_core_i);
        if (physical_writes != writes_before+3 || !status_o[7] || !record_available_core_o || capture_incomplete_core_o)
            $fatal(1, "DSP physical writes delta=%0d status=%02h available=%0d armed=%0d",
                   physical_writes-writes_before, status_o, record_available_core_o, capture_incomplete_core_o);

        // 未绑定时在头前拒绝，真实地址和原 FIFO 游标仍未消耗。
        format_bound_io_i = 0;
        request_record();
        wait (record_failed_core_o);
        @(negedge clk_core_i);
        if (bound_rejects != 1 || physical_reads != reads_before || !record_available_core_o)
            $fatal(1, "Unbound frame consumed captured SRAM");
        // 对同一记录补齐绑定再发，黄金还能检查拒绝期间有没有推进读址。
        @(negedge clk_io_i);
        format_bound_io_i = 1;
        check_golden = 1;
        request_record();
        wait (record_done_core_o);
        @(negedge clk_core_i);
        if (physical_reads != reads_before+3 || pair_count != 46 || wire_record != FRAME_GOLDEN || !fifo_empty_core_o ||
            !status_o[6] || record_available_core_o)
            $fatal(1, "Expected true DSP/SRAM record %023h, got %023h", FRAME_GOLDEN, wire_record);
        check_golden = 0;
        $display("PASS SPI OFFSET/GAIN -> SINC1 -> HI event -> three SRAM words -> 46 pairs %023h", wire_record);

        // 继续测试整个地址空间、运行中掉电以及破坏性自测退场。
        writes_before = physical_writes;
        reads_before = physical_reads;
        audit_full_and_bist(writes_before, reads_before);
        audit_snapshot_prefix();
        if (capture_write_loss_core_o) $fatal(1, "Unexpected physical write loss");
    endtask

    // =========================================================================
    // 5. 同一产品实例的冷启动与独立激励过程
    // =========================================================================
    // 不同定向档从同一冷启动进入，并各自维护等待。分开时序过程让工具
    // 独立编译生成的协程，避免把多个长 SPI/采样台架合成超大函数。
    initial begin : start_fixture
        if (!$value$plusargs("product_group=%s", fixture_group)) fixture_group = "";
        case (fixture_group)
            "", "result_spi", "control", "full_record", "filters": ;
            "speed_single", "speed_dual": begin
                if (!PRODUCT_SPEED_RUN) $fatal(1, "Speed fixture not compiled");
                io_half_period = fixture_group == "speed_dual" ? 2500ps : 1250ps;
            end
            default: $fatal(1, "Unknown product_group: %s", fixture_group);
        endcase
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1;
        // I/O 与配置先起来，产品冷启动路径没有隐式假设参考已到。
        wait (record_ready_core_o && continuous_config_ready_core_o);
        $display("PRODUCT_TIMING_BOUND CORE_CLOCK_HZ=%0d P_CNV_TIMING_BOUND=1", FIXTURE_CORE_HZ);
        if (!PRODUCT_SPEED_RUN) audit_config_sdo();
        if (CALIBRATION_RUN != 0 || fixture_group != "") begin
            ref_ok_async_i = 1'b1;
            wait (conversion_allow_core_o && record_ready_core_o);
        end
        fixture_ready = 1;
    end

    task automatic assert_initial_empty();
        wait (conversion_allow_core_o && record_ready_core_o);
        request_record();
        if (!record_rejected_core_o || !fifo_empty_core_o || physical_reads != 0)
            $fatal(1, "Empty capture created a record request");
    endtask

    generate
        if (PRODUCT_SPEED_RUN) begin : g_speed_fixture
            initial begin
                wait (fixture_ready);
                audit_product_speed(fixture_group == "speed_dual");
                $display("AD4080_PRODUCT_SPEED PASS");
                $finish;
            end
        end else if (CALIBRATION_RUN != 0) begin : g_calibration_fixture
            initial begin
                wait (fixture_ready);
                assert_initial_empty();
                audit_calibration();
                $finish;
            end
        end else begin : g_product_fixture
            initial begin : continuous_and_controls
                wait (fixture_ready);
                if (fixture_group == "") begin
                    audit_fixed_word_and_por();
                    run_continuous_pin_check();
                    // 宏控制数值从已应用的同组 CSR 取得。恢复后保持
                    // 普通时基，Fig89 才能用同一连续逻辑边沿轴。
                    audit_lvds_pad_controls();
                    normal_io_checked = 1;
                end
            end
            initial begin : default_record
                wait (normal_io_checked);
                // 训练和连续配置退场后再看原捕获。固定字、连续和
                // 采后数据分别在各自有效口观察，独立验证实际退役。
                assert_initial_empty();
                audit_fig83();
                audit_fig99();
                audit_fig89_w1();
                audit_short_cnv_error();
                audit_product();
                $display("AD4080_PRODUCT_BINDING PASS");
                $display("AD4080_PRODUCT_PIN_EDGES PASS");
                $finish;
            end
            initial begin : result_spi
                wait (fixture_ready);
                if (fixture_group == "result_spi") begin
                    run_result_spi_pin_check();
                    $display("AD4080_PRODUCT_RESULT_SPI PASS");
                    $finish;
                end
            end
            initial begin : control
                wait (fixture_ready);
                if (fixture_group == "control") begin
                    run_control_pin_check();
                    $display("AD4080_PRODUCT_CONTROL_PINS PASS");
                    $finish;
                end
            end
            initial begin : full_record
                wait (fixture_ready);
                if (fixture_group == "full_record") begin
                    run_full_record_check();
                    $display("AD4080_PRODUCT_FULL_RECORD PASS");
                    $finish;
                end
            end
            initial begin : filters
                wait (fixture_ready);
                if (fixture_group == "filters") begin
                    audit_fig99();
                    run_filter_checks();
                    $display("AD4080_PRODUCT_FILTERS PASS");
                    $finish;
                end
            end
        end
    endgenerate

    initial begin
        // 包含 300 us 冷初始化和全深度写，过期表示某处握手未退出。
        // 两条跨环完整记录按真实单 lane 槽时钟逐字发送，另有定向预算。
        string run_group;
        if ($value$plusargs("product_group=%s", run_group) && run_group == "full_record") #20ms;
        else #10ms;
        $fatal(1, "Product watchdog group=%s power_mode=%0d conv=%b read_ready=%b locked=%b io_epoch=%b AON_idle=%b AON_init=%b cfg_poll=%b",
               fixture_group, dut.u_services.operating_mode_aon_o, conversion_allow_core_o, record_ready_core_o,
               dut.record_locked, io_record_epoch_o, dut.aon_clients_idle, dut.u_services.cfg_init_done_aon_o,
               dut.u_dsp.reset_poll_only);
    end
endmodule

`default_nettype wire
