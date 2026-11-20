`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : adi_receiver_tb.sv
// 模块名     : adi_receiver_tb
// 版本       : v1.0
// 功能       : 本包 TX 和冻结的原版 ADI 7-series RX 在连续 LVDS 模式联测。
// 设计说明   :
//   * 发送只接用户自己的 transport/mapper，收端输入是 DA、DB、DCO
//     实际逐边沿位流。TB 不做逆映射，也不直接向 ADI packer 填字。
//   * 通过控制口先要求 ADC 发固定测试字，再触发实际 ADI 位滑移扫描。
//     对锁定后接收的带符号数据直接对比提交的并行样本。
//   * 源同步时钟选理想采样相位。不存在板级眼图、CNV 抖动、采样延时量测。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module adi_receiver_tb #(
    parameter bit DUAL = 1'b0,             // 同一 ADI PHY 用运行时 lane 数切换 4 或 8 位解包
    parameter int HOLD_PAIRS = 8            // 可调首帧相对 DCO 分组的相位，不改变数据内容
);
    localparam time UI_PS = DUAL ? 2500ps : 1250ps;
    localparam int TRAIN_WORDS = 128;
    localparam int DATA_WORDS = 14;
    localparam int TRAILING_WORDS = 8;
    localparam int TOTAL_WORDS = TRAIN_WORDS + DATA_WORDS + TRAILING_WORDS;
    localparam logic [31:0] TRAIN_RX = 32'hFFFAC5D6;

    // =========================================================================
    // 1. 源域列队和已提交字的正负序列
    // =========================================================================
    logic clk_core = 1'b0, clk_pair = 1'b0;
    logic rst_n = 1'b0, allow_new_word = 1'b0;
    initial forever #700ps clk_core = !clk_core;     // 刻意与装载对的时钟不同周期
    initial forever #(UI_PS) clk_pair = !clk_pair;

    logic submit_valid = 1'b0, check_pattern = 1'b0;
    logic [19:0] submit_word = '0;
    wire core_online, core_full, core_accept, core_drop, core_overflow;
    wire pair_valid, pair_last, pair_first, tx_dual, tx_check, da_fall, da_rise, db_fall, db_rise;
    int unsigned submitted = 0, transmitted = 0, positions = 0;
    bit streaming = 1'b0;

    // 递增数、正负极限与非对称码用于辨认末位/符号、A/B、帧位置和错字交换。
    function automatic logic [19:0] payload(input int unsigned index);
        case (index)
            0: return 20'h00000;
            1: return 20'h00001;
            2: return 20'h00002;
            3: return 20'h00003;
            4: return 20'h00004;
            5: return 20'h01234;
            6: return 20'h3579A;
            7: return 20'h7FFFF;
            8: return 20'h80000;
            9: return 20'h80001;
           10: return 20'hF1234;
           11: return 20'hFFFFE;
           12: return 20'hFFFFF;
           13: return 20'h531A7;
      default: return 20'h246BC;
        endcase
    endfunction

    ad4080_result_transport #(.FIFO_ADDR_WIDTH(3)) tx (
        .clk_core_i(clk_core), .clk_io_i(clk_pair), .core_reset_ni(rst_n), .io_reset_ni(rst_n),
        .power_good_async_i(rst_n), .result_core_i(submit_word), .result_valid_core_i(submit_valid),
        .dual_lane_core_i(DUAL), .intf_check_core_i(check_pattern), .core_online_o(core_online),
        .core_full_o(core_full), .core_accept_o(core_accept), .core_drop_o(core_drop),
        .core_overflow_o(core_overflow), .core_drained_o(), .io_start_allow_i(allow_new_word),
        .io_online_o(), .io_prefetch_words_o(), .io_word_load_o(),
        .io_parallel_mode_i(1'b0), .io_parallel_ready_i(1'b0), .io_parallel_valid_o(), .io_parallel_word_o(),
        .io_pair_valid_o(pair_valid), .io_first_o(pair_first), .io_last_o(pair_last),
        .io_dual_lane_o(tx_dual), .io_intf_check_o(tx_check), .io_da_fall_o(da_fall),
        .io_da_rise_o(da_rise), .io_db_fall_o(db_fall), .io_db_rise_o(db_rise)
    );

    // FIFO 先预装多字。训练期送与训练码不同的并行源词，验证原模式覆盖功能。
    // 取样级依照源端 valid/accept，跑满速的 TX 输出期间出现字间隙即失败。
    always @(negedge clk_core) begin
        submit_valid = rst_n && core_online && !core_full && submitted < TOTAL_WORDS;
        check_pattern = submitted < TRAIN_WORDS;
        submit_word = check_pattern ? 20'h9A501 : payload(submitted - TRAIN_WORDS);
    end
    always @(posedge clk_core) begin
        if (rst_n && (core_drop || core_overflow || (submit_valid && !core_accept)))
            $fatal(1, "Source lost an input while setting up the continuous burst");
        if (core_accept) submitted++;
    end

    // =========================================================================
    // 2. 引脚前理想 DDR 发射，下降沿先出，之后上升沿继续
    // =========================================================================
    logic serial_da = 1'b0, serial_db = 1'b0, held_da_rise = 1'b0, held_db_rise = 1'b0;
    wire dco_p;
    // 令采样时钟与未延时的 TX 发位相隔四分之一 UI。这里是 IDELAY/采样窗
    // 的理想代理，不将这个延迟解释为商业 ADC 的 tDCO/tDCOD 或物理余量。
    assign #(UI_PS/4) dco_p = rst_n && !clk_pair;

    always @(posedge clk_pair or negedge clk_pair or negedge rst_n) begin
        if (!rst_n) begin
            {serial_da, serial_db, held_da_rise, held_db_rise} <= '0;
        end else if (clk_pair) begin
            {serial_da, serial_db} <= pair_valid ? {da_fall, db_fall} : '0;
            {held_da_rise, held_db_rise} <= pair_valid ? {da_rise, db_rise} : '0;
        end else begin
            {serial_da, serial_db} <= {held_da_rise, held_db_rise};
        end
    end

    // 帧首尾标志只用于元数据断言，绝不从它恢复 RX 分帧，也不向 PHY 注入。
    always @(posedge clk_pair) begin
        if (pair_valid) begin
            if (tx_dual != DUAL || tx_check != (transmitted < TRAIN_WORDS) ||
                pair_first != (positions == 0) || pair_last != (positions == (DUAL ? 4 : 9)))
                $fatal(1, "TX metadata is inconsistent with its serialized burst");
            streaming = 1'b1;
            if (pair_last) begin
                transmitted++;
                positions = 0;
            end else positions++;
        end else if (streaming && transmitted < TOTAL_WORDS) begin
            $fatal(1, "Invalid clock gap within prepared continuous burst");
        end
    end

    // =========================================================================
    // 3. 冻结原版 ADI PHY，输入只见时钟和每 lane 的串行差分值
    // =========================================================================
    wire [31:0] received;
    wire valid_rx, clk_rx, in_sync, delay_locked;
    logic bitslip_enable = 1'b0;
    ad408x_phy #(.FPGA_TECHNOLOGY(1), .NUM_LANES(2), .ADC_N_BITS(20), .ADC_DATA_WIDTH(32)) rx (
        .dclk_in_p(dco_p), .dclk_in_n(!dco_p), .data_a_in_p(serial_da), .data_a_in_n(!serial_da),
        .data_b_in_p(serial_db), .data_b_in_n(!serial_db), .cnv_in_p(1'b0), .cnv_in_n(1'b1),
        .sync_n(rst_n), .num_lanes(DUAL ? 5'd2 : 5'd1), .device_code(2'd0),
        .self_sync(1'b1), .bitslip_enable(bitslip_enable), .filter_enable(1'b0), .filter_rdy_n(1'b1),
        .up_clk(clk_core), .up_adc_dld(2'b00), .up_adc_dwdata(10'b0), .up_adc_drdata(),
        .delay_clk(clk_core), .delay_rst(!rst_n), .delay_locked(delay_locked), .adc_rst(!rst_n),
        .adc_clk(clk_rx), .adc_data(received), .adc_valid(valid_rx), .sync_status(in_sync)
    );

    int unsigned training_checks = 0, payload_checks = 0;
    logic [4:0] shift_on_lock = '0;

    // [Oracle] 不重新解串。直接与源输入码制的 32 位二补码比较。允许
    // 启动时有错位的训练数据，锁定之后第一笔非训练值必须是源表首字。
    always @(negedge clk_rx) begin
        if (in_sync && valid_rx && payload_checks < DATA_WORDS) begin
            if (payload_checks == 0 && received == TRAIN_RX) begin
                training_checks++;
            end else begin
                if (training_checks < 3 || received !== 32'($signed(payload(payload_checks))))
                    $fatal(1, "ADI RX mode=%0d idx=%0d got=%08h expected=%08h shift=%0d train=%0d",
                           DUAL, payload_checks, received, 32'($signed(payload(payload_checks))),
                           rx.shift_cnt, training_checks);
                payload_checks++;
            end
        end
    end

    // 固定码有意从非零旋转开始。运行原 ADI 比较/扫描，逐笔检查连续采样，
    // 第二种启动位置由 runner 改 HOLD_PAIRS。在源对齐后才验 ADC 符号码。
    initial begin
        repeat (4) @(negedge clk_pair);
        rst_n = 1'b1;
        wait (submitted >= 4);
        repeat (HOLD_PAIRS) @(negedge clk_pair);
        allow_new_word = 1'b1;
        wait (transmitted >= 12);
        @(negedge clk_rx);
        if (received == TRAIN_RX) $fatal(1, "Expected a rotated pre-lock training stream");
        bitslip_enable = 1'b1;
        repeat (3) @(negedge clk_rx);
        bitslip_enable = 1'b0;
        wait (in_sync);
        shift_on_lock = rx.shift_cnt;
        if (shift_on_lock == 0 || !delay_locked) $fatal(1, "No nonzero ADI word realignment observed");
        wait (payload_checks == DATA_WORDS);
        wait (transmitted == TOTAL_WORDS);
        if (submitted != TOTAL_WORDS) $fatal(1, "Not all words crossed the original TX");
        $display("PASS ADI receiver lane=%0d hold_pairs=%0d shift=%0d fixed=%0d words=%0d tx=%0d",
                 DUAL ? 2 : 1, HOLD_PAIRS, shift_on_lock, training_checks, payload_checks, transmitted);
        $finish;
    end

    initial begin
        #20_000_000ps;
        $fatal(1, "ADI RX timeout lane=%0d sent=%0d shift=%0d locked=%0d train=%0d words=%0d",
               DUAL ? 2 : 1, transmitted, rx.shift_cnt, in_sync, training_checks, payload_checks);
    end
endmodule

`default_nettype wire
