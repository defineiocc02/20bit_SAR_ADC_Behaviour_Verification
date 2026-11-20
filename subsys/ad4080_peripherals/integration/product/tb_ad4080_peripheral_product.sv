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
// =============================================================================
module tb_ad4080_peripheral_product #(parameter int CALIBRATION_RUN = 0);
    // =========================================================================
    // 1. 三域时钟及专用试验余数
    // =========================================================================
    // 正向格式取 AA、三个已知数值字及按 68 位前缀长除得到的工程 CRC。
    // 业务验收的三个数值来自固定 DSP 算法，CRC 算法只属于此试验绑定。
    localparam logic [91:0] FRAME_GOLDEN = 92'hAA_0075E_009E8_FF30E_71F3EC;
    // 两个整帧的 24 位余数用独立长除固定。三个理想值是 −300000、0、250000，
    // 同一偏置/增益误差下提供负、零、正，既检查极性也定位 1 LSB 整数边界。
    localparam logic [91:0] NEUTRAL_GOLDEN = 92'hAA_B6694_00050_3D5C2_4AB21A;
    localparam logic [91:0] CALIBRATED_GOLDEN = 92'hAA_B6C1F_00000_3D090_D25BD4;
    localparam int FIXTURE_GAIN_SHIFT = CALIBRATION_RUN == 0 ? 14 : 15;
    localparam logic [23:0] CRC_INIT = 24'hB704CE, CRC_POLY = 24'h864CFB;
    localparam logic [7:0] OTP_TEST_WORD = 8'hA5;

    logic clk_aon_i = 0, clk_core_i = 0, clk_io_i = 0; // 不同周期，位对只在 I/O 域采样
    logic por_d_ok_async_i = 0, iovdd_ok_async_i = 1;   // 独立外供 I/O 掉电，核心仍保留
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
    logic [19:0] sample_signed_core_i;       // 补码负值不在试验端预校正
    logic ext_event_core_i = 0;              // 满史快照用独立同域事件锁存位置
    logic cfg_reject_o, processing_reject_o;
    logic [3:0] cfg_reject_reason_o;

    logic record_command_core_i = 0, record_ready_core_o, record_rejected_core_o;
    logic record_done_core_o, record_failed_core_o, record_available_core_o, capture_incomplete_core_o;
    logic fifo_full_core_o, fifo_empty_core_o, capture_write_loss_core_o; // 区别真容量、水位和写失败
    logic format_bound_io_i = 0, crc_valid_io_i = 0, crc_start_io_o, crc_ready_io_o, crc_cancel_io_o; // 首轮须拒绝
    logic abort_io_i = 0;                    // 快照用例在核完跨界头两字后主动截断
    logic [23:0] crc_io_i;
    logic beat_valid_io_o, beat_first_io_o, beat_last_io_o, io_record_epoch_o, frame_error_io_o;
    logic [1:0] beat_pair_io_o, beat_kind_io_o;
    logic [2:0] frame_error_code_io_o;
    logic check_golden = 0;                  // 故障用例主动撤销，不产生成功帧余数
    logic [91:0] wire_record;                 // 头 8、载荷 60、完整余数 24
    int pair_count;                          // 数已交付的 pair，以段标志定位错误槽

    logic bist_request_aon_i = 0, bist_ready_aon_o, bist_grant_aon_o;
    logic bist_start_core_i = 0, bist_start_ready_core_o, bist_reinit_request_core_i = 0;
    logic bist_contents_invalid_core_o, bist_busy_core_o, bist_done_core_o;
    logic [1:0] bist_fail_reason_core_o;
    int physical_writes = 0, physical_reads = 0, config_rejects = 0, bound_rejects = 0; // 对照独立拒绝源
    logic [13:0] last_write_addr;             // 第四银行真正末址，而非推算写计数

    always #50ns clk_aon_i = ~clk_aon_i;
    always #7ns clk_core_i = ~clk_core_i;
    always #11ns clk_io_i = ~clk_io_i;

    ad4080_peripheral_product #(
        .OTP_WORD_WIDTH(8), .GAIN_BOUND(1'b1), .GAIN_K(1), .GAIN_SHIFT(FIXTURE_GAIN_SHIFT),
        .SPI_MULTIBYTE_ORDER(0), .OFFSET_LSB_ADDRESS(37), .SPI_WRITE_SDO_DRIVE(0)
    ) dut (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i(1'b1),
        .por_d_ok_async_i, .vdd33_ok_async_i(1'b1), .ref_ok_async_i(1'b1),
        .iovdd_ok_async_i, .ana_ready_async_i(1'b1), .sw_reset_req_aon_i, .mode_req_valid_aon_i,
        .mode_req_aon_i(2'b10), .wake_recover_done_aon_i(1'b0),
        .sw_reset_accept_aon_o, .mode_req_accept_aon_o, .wake_recover_req_aon_o(),
        .otp_power_good_aon_i(1'b1),
        .otp_adapters_quiescent_aon_i(otp_adapters_cancel_aon_o && !otp_rsp_valid_aon_i && !otp_verify_rsp_valid_aon_i),
        .otp_adapters_cancel_aon_o, .otp_req_valid_aon_o,
        .otp_req_ready_aon_i(!otp_adapters_cancel_aon_o && !otp_rsp_valid_aon_i), .otp_req_addr_aon_o(),
        .otp_rsp_valid_aon_i, .otp_rsp_error_aon_i(1'b0), .otp_rsp_data_aon_i(OTP_TEST_WORD), .otp_rsp_ready_aon_o,
        .otp_verify_req_valid_aon_o,
        .otp_verify_req_ready_aon_i(!otp_adapters_cancel_aon_o && !otp_verify_rsp_valid_aon_i), .otp_verify_image_aon_o,
        .otp_verify_rsp_valid_aon_i, .otp_verify_rsp_pass_aon_i, .otp_verify_rsp_ready_aon_o,
        .sample_valid_core_i, .sample_signed_core_i, .sample_source_idle_core_i, .conversion_allow_core_o,
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

    // 余数来自实际看到的头和载荷，黄金来自整串 GF(2) 长除。记录切断时
    // 丢弃测试余数，避免下一次 CRC 响应混入旧会话。
    always @(posedge clk_io_i) begin
        if (frame_error_io_o && frame_error_code_io_o == 3'd2) bound_rejects++;
        if (!io_record_epoch_o || crc_cancel_io_o) crc_valid_io_i <= 0;
        else if (crc_start_io_o) begin
            crc_valid_io_i <= 0;
            crc_io_i <= CRC_INIT;
            pair_count <= 0;
            wire_record <= '0;
        end else if (check_golden) begin
            if (crc_valid_io_i && crc_ready_io_o) crc_valid_io_i <= 0;
            if (beat_valid_io_o) begin
                if (beat_first_io_o != (pair_count == 0) || beat_last_io_o != (pair_count == 45) ||
                    beat_kind_io_o != (pair_count < 4 ? 2'd0 : pair_count < 34 ? 2'd1 : 2'd2))
                    $fatal(1, "Incorrect LVDS record framing at pair %0d", pair_count);
                wire_record <= {wire_record[89:0], beat_pair_io_o};
                pair_count <= pair_count + 1;
                if (pair_count < 34) crc_io_i <= crc_step(crc_io_i, beat_pair_io_o);
                if (pair_count == 33) crc_valid_io_i <= 1;
            end
        end
    end

    // =========================================================================
    // 3. 原配置 SPI 与样本/记录请求
    // =========================================================================
    // [SPI] CPOL 高，预置输入过负沿，在正沿取 SDO。半拍远长于内部同步链，
    // 这段只验协议和实际数值控制，目的不包括配置口极限频率。
    task automatic spi_byte(input logic [7:0] value, output logic [7:0] received);
        for (int idx = 7; idx >= 0; idx--) begin
            sdi = value[idx];
            #80ns sclk = 0;
            #80ns sclk = 1;
            received[idx] = sdo;
            #80ns;
        end
    endtask

    task automatic spi_access(input bit read_cmd, input logic [14:0] address,
                              input int nbytes, input logic [15:0] value, output logic [15:0] returned);
        logic [7:0] reply;
        returned = 0;
        @(negedge clk_core_i);
        cs_n = 0;
        repeat (8) @(negedge clk_core_i);
        spi_byte({read_cmd, address[14:8]}, reply);
        spi_byte(address[7:0], reply);
        // 严格长指令和降序实体从高地址开始，完整传输才有配置提交。
        for (int idx = nbytes-1; idx >= 0; idx--) begin
            spi_byte(value[8*idx+:8], reply);
            returned[8*idx+:8] = reply;
        end
        // 尾部等同步 CS 退回，检查读端不会仍占着配置 SDO。
        cs_n = 1;
        repeat (8) @(negedge clk_core_i);
        if (sdo_oe) $fatal(1, "SPI kept SDO ownership after CS");
    endtask

    task automatic write_reg(input logic [14:0] address, input int nbytes, input logic [15:0] value);
        logic [15:0] unused_reply;
        spi_access(0, address, nbytes, value, unused_reply);
    endtask

    task automatic quartet(input int a,b,c,d);
        // 原 SINC1 按样本 valid 计数。以 8 个核心沿作管线排空边界，
        // 四输入确实在同一窗口，间隔空拍不会被当成样本。
        int x[4];
        x = '{a,b,c,d};
        sample_source_idle_core_i = 0;
        for (int idx = 0; idx < 4; idx++) begin
            @(negedge clk_core_i);
            sample_valid_core_i = 1;
            sample_signed_core_i = 20'(x[idx]);
        end
        @(negedge clk_core_i);
        sample_valid_core_i = 0;
        repeat (8) @(negedge clk_core_i);
        sample_source_idle_core_i = 1;
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
        // 数值旁路仍经原校正阶段。等待尾部后才宣称来源静默，避免在途
        // 样本与 MBIST 的新所有权交叠。
        @(negedge clk_core_i);
        sample_source_idle_core_i = 0;
        sample_valid_core_i = 1;
        sample_signed_core_i = word;
        @(negedge clk_core_i);
        sample_valid_core_i = 0;
        repeat (8) @(negedge clk_core_i);
        sample_source_idle_core_i = 1;
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
        write_one_result(20'hB6694);
        write_one_result(20'h00050);
        write_one_result(20'h3D5C2);
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
        sample_source_idle_core_i = 0;
        for (int idx = 0; idx < 16384; idx++) begin
            @(negedge clk_core_i);
            sample_valid_core_i = 1;
            sample_signed_core_i = 20'(idx);
        end
        @(negedge clk_core_i);
        sample_valid_core_i = 0;
        repeat (8) @(negedge clk_core_i);
        sample_source_idle_core_i = 1;
        if (!fifo_full_core_o || !record_available_core_o || capture_incomplete_core_o ||
            last_write_addr != 14'h3FFF || physical_writes != first_write_count+16384)
            $fatal(1, "Physical full/bank traversal not reached");
        // 满且已完成时仍送一个验证字，检查原控制器结束捕获而非覆盖首字。
        write_one_result(20'h5E321);
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
        sample_source_idle_core_i = 0;
        for (int idx = 0; idx < 32763; idx++) begin
            @(negedge clk_core_i);
            sample_valid_core_i = 1;
            sample_signed_core_i = 20'(idx);
        end
        @(negedge clk_core_i);
        sample_valid_core_i = 0;
        repeat (8) @(negedge clk_core_i);
        if (!fifo_full_core_o || physical_writes != initial_writes+32763 || !capture_incomplete_core_o)
            $fatal(1, "Pre-trigger full-history ring did not preserve actual overwrite flow");

        // 事件前先排掉数值管线。不存在同沿写/事件的归属歧义，WATERMARK
        // 写回应等于真实事件指针，冻结读址须独立使用已配置的四个后样本。
        ext_event_core_i = 1;
        @(negedge clk_core_i);
        ext_event_core_i = 0;
        for (int idx = 32763; idx < 32767; idx++) write_one_result(20'(idx));
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
    task automatic audit_product();
        int rejects_before, writes_before, reads_before;
        logic [15:0] readback;
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
        @(negedge clk_core_i);
        gpio_i[1] = 0;
        repeat (6) @(negedge clk_core_i);
        gpio_i[1] = 1;
        write_reg(15'h001C, 1, 16'h00C2);
        wait (capture_incomplete_core_o);

        // 已 ARM 却未捕完时，空闲的物理口不能用来放行软复位、待机、BIST 或读出。
        request_record();
        if (!record_rejected_core_o || physical_reads != 0) $fatal(1, "Unfinished record entered readback");
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

        // 阈值码 4 对应 1024 个 20 位码。196 不触发，1106 触发开闸但
        // 本身还在旧窗口，三笔实际写应全部取自事件之后。
        quartet(1280, -256, 0, 0);
        if (status_o[5] || physical_writes != 0) $fatal(1, "Subthreshold burst triggered capture");
        quartet(960, 1088, 1216, 1344);
        if (!status_o[5] || !gpio_oe[0] || gpio_o[0] || physical_writes != 0)
            $fatal(1, "Threshold trigger or post-event write ordering mismatch");
        quartet(1728, 1856, 1984, 2112);
        quartet(2368, 2496, 2624, 2752);
        quartet(-3392, -3264, -3136, -3008);
        if (physical_writes != 3 || !status_o[7] || !record_available_core_o || capture_incomplete_core_o)
            $fatal(1, "DSP did not complete the three physical writes");

        // 未绑定时在头前拒绝，真实地址和原 FIFO 游标仍未消耗。
        request_record();
        wait (record_failed_core_o);
        @(negedge clk_core_i);
        if (bound_rejects != 1 || physical_reads != 0 || !record_available_core_o)
            $fatal(1, "Unbound frame consumed captured SRAM");
        // 对同一记录补齐绑定再发，黄金还能检查拒绝期间有没有推进读址。
        @(negedge clk_io_i);
        format_bound_io_i = 1;
        check_golden = 1;
        request_record();
        wait (record_done_core_o);
        @(negedge clk_core_i);
        if (physical_reads != 3 || pair_count != 46 || wire_record != FRAME_GOLDEN || !fifo_empty_core_o ||
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
        $display("AD4080_PRODUCT_BINDING PASS");
    endtask

    initial begin
        @(negedge clk_aon_i);
        por_d_ok_async_i = 1;
        wait (conversion_allow_core_o && record_ready_core_o);
        request_record();
        if (!record_rejected_core_o || !fifo_empty_core_o || physical_reads != 0)
            $fatal(1, "Empty capture created a record request");
        if (CALIBRATION_RUN != 0) audit_calibration();
        else audit_product();
        $finish;
    end

    initial begin
        // 包含 300 us 冷初始化和全深度写，过期表示某处握手未退出。
        #3ms;
        $fatal(1, "Product binding watchdog expired");
    end
endmodule

`default_nettype wire
