`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_fifo_record_framer_tb.sv
// 模块名     : ad4080_fifo_record_framer_tb
// 版本       : v1.0
// 功能       : 对 IO FIFO 的参考线序、记录边界和异常退出逐 beat 核验。
// 设计说明   : 期望字用手算的线序常量和消费者 shift 取得。其中 SPI4
//              有独立字面量，能查出符号扩展、四线互换或头宽推断错误。
//              模型 CRC 源只提供可观测的测试响应值，不代表 ADC 算法。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_fifo_record_framer_tb #(
    parameter bit BIND_SPI_TAIL = 1'b0  // 分别构建默认消费长度和实物绑定尾两种合同
);
    // =========================================================================
    // 1. 测试台交付及错误协议
    // =========================================================================
    // --- 单域控制与资格 ---
    // 故障测试单独拉低供电资格或复位，以免复位把待核对的错误脉冲盖掉。
    logic clk = 1'b0, rst_n = 1'b0, power_good = 1'b1, abort_now = 1'b0;
    initial forever #5 clk = !clk;    // 节拍仅用于测试台调度
    logic start_valid = 1'b0, start_ready, start_accept;
    logic [1:0] mode = '0;
    logic [14:0] sample_count = '0;
    logic format_bound = 1'b1, crc_service_ready = 1'b1;

    // --- 已展开的 LVDS 与并行 SPI 两种来源 ---
    // SPI 模型在前一个字发射期间保持后一个字，直到观察到真实的空位。
    logic payload_valid = 1'b0, payload_first = 1'b0, payload_last = 1'b0, payload_allow;
    logic [1:0] payload_pair = '0;
    logic [1:0] payload_prefetch = 2'd2;  // 单元夹具默认预置足量，集成另从真实读回得出
    logic sample_valid = 1'b0, sample_last = 1'b0, sample_ready;
    logic [19:0] sample = '0;

    // --- CRC 服务与物理出线代理 ---
    // CRC 测试字的首尾不对称，使数据移位或端序错误出现可观测差异。
    logic crc_valid = 1'b0, crc_ready, crc_error = 1'b0, crc_cancel;
    logic [23:0] crc_tail = 24'hA5C39E;
    logic beat_valid, beat_ready = 1'b1, beat_first, beat_last, busy, done, error;
    logic [3:0] beat;
    logic [2:0] beat_width, error_code;
    logic [1:0] beat_kind;
    logic [7:0] sync_header;
    logic payload_word_start;
    int unsigned beat_checks = 0, fault_checks = 0;
    int unsigned raw_ticks = 0, first_raw_tick = 0, record_ticks = 0;
    // 对照计数从未暂停，包括输出无效期间。若测试改成先等 valid 再验位，
    // 首末边沿跨度的断言仍必定看到被跳过的原始时钟。
    always @(posedge clk) raw_ticks <= raw_ticks + 1;

    ad4080_fifo_record_framer #(.SPI_CRC_TAIL_BOUND(BIND_SPI_TAIL)) dut (
        .clk_i(clk), .rst_ni(rst_n), .power_good_i(power_good), .abort_i(abort_now),
        .start_valid_i(start_valid), .start_ready_o(start_ready), .start_accept_o(start_accept),
        .mode_i(mode), .sample_count_i(sample_count), .format_bound_i(format_bound),
        .crc_service_ready_i(crc_service_ready),
        .payload_prefetch_words_i(payload_prefetch), .payload_pair_valid_i(payload_valid), .payload_pair_i(payload_pair),
        .payload_pair_first_i(payload_first), .payload_pair_last_i(payload_last),
        .payload_start_allow_o(payload_allow),
        .sample_valid_i(sample_valid), .sample_ready_o(sample_ready), .sample_i(sample),
        .sample_last_i(sample_last), .crc_valid_i(crc_valid), .crc_ready_o(crc_ready),
        .crc_i(crc_tail), .crc_error_i(crc_error), .crc_cancel_o(crc_cancel),
        .beat_valid_o(beat_valid), .beat_ready_i(beat_ready), .beat_o(beat),
        .spi_pin_beat_taken_i(1'b0), .sync_header_o(sync_header), .payload_word_start_o(payload_word_start),
        .beat_width_o(beat_width), .beat_first_o(beat_first), .beat_last_o(beat_last),
        .beat_kind_o(beat_kind), .busy_o(busy), .done_o(done), .error_o(error),
        .error_code_o(error_code)
    );

    // =========================================================================
    // 2. 独立读端断言工具
    // =========================================================================
    // 所有驱动从下降沿开始，经过上升沿后回到下降沿，避免测试台竞争。
    task automatic tick_edge;
        @(posedge clk);
        @(negedge clk);
    endtask

    // [退出] 既查错误码也查接口是否回到安全终态。故障若只置错误、仍留
    // 倒灌新字的准入或末尾 valid，接收机可能把部分记录当成完整结果。
    task automatic check_fault(input logic [2:0] expected_code, input bit cancelled);
        #1;
        if (!error || error_code != expected_code || crc_cancel != cancelled ||
            busy || beat_valid || done || payload_allow || crc_ready)
            $fatal(1, "fault contract: expected %0d cancel %0d, got code %0d cancel %0d",
                   expected_code, cancelled, error_code, crc_cancel);
        fault_checks++;
        tick_edge();
    endtask

    // [启动] 命令消费和成功提交分别检查，避免无 CRC 服务的情形仅仅挂住
    // 在 ready=0，被误认为确定地拒绝且对启动方有回报。
    task automatic issue_command(
        input logic [1:0] selected_mode,
        input logic [14:0] announced_count,
        input logic [2:0] reject_reason
    );
        mode = selected_mode;
        sample_count = announced_count;
        start_valid = 1'b1;
        #1;
        if (!start_ready || start_accept != (reject_reason == 0))
            $fatal(1, "unexpected start admission for mode %0d count %0d", mode, sample_count);
        tick_edge();
        start_valid = 1'b0;
        if (reject_reason != 0) check_fault(reject_reason, 1'b0);
        else if (!busy || error || done) $fatal(1, "new command not in flight");
    endtask

    // [独立参考] 读端逐组从预先手排的二进制时间序列右移取位。不会调用
    // DUT 的位选、固定头变量、子模块或符号扩展逻辑。
    function automatic logic [3:0] on_wire(
        input logic [31:0] wire_vector, input int width, input int beats, input int index
    );
        logic [31:0] group_mask;
        group_mask = (32'd1 << width) - 1;
        return 4'((wire_vector >> ((beats-1-index)*width)) & group_mask);
    endfunction

    // [同沿] 断言先读本沿发送前的组合字及元数据。从 AA 数原始 CLK，
    // 丢掉无效拍或等待 valid 都会违反预期时间序列。
    task automatic check_beat(
        input logic [3:0] expected, input int width, input logic [1:0] phase,
        input bit first, input bit last
    );
        #1;
        if (!beat_valid || !beat_ready || beat !== expected || beat_width != 3'(width) || beat_kind != phase ||
            beat_first != first || beat_last != last)
            $fatal(1, "beat %0d mode %0d: got %h %0d/%0d %b%b wanted %h %0d/%0d %b%b",
                   beat_checks, mode, beat, beat_width, beat_kind, beat_first, beat_last,
                   expected, width, phase, first, last);
        if (first) begin
            // 预取多久不收费，只有实际第一拍出 AA 才开始本轮物理时基。
            first_raw_tick = raw_ticks;
            record_ticks = 0;
        end
        if (raw_ticks != first_raw_tick + record_ticks) $fatal(1, "Skipped raw CLK inside a record");
        if (sync_header != 8'hAA || payload_word_start !=
            (phase == 1 && (record_ticks-(mode==0 ? 4 : 8))%(mode==0 ? 10 : mode==1 ? 24 : 6)==0))
            $fatal(1, "Wire header constant or payload boundary disagreement");
        record_ticks++;
        beat_checks++;
        tick_edge();
    endtask

    task automatic check_finished;
        #1;
        if (!done || error || busy || beat_valid || payload_allow || sample_ready || crc_ready)
            $fatal(1, "record did not retire on desired last beat or extra position leaked");
        $display("PASS RAW CLK RECORD mode=%0d words=%0d consecutive=%0d", mode, sample_count, record_ticks);
        // SPI 主机此时升 DCS，下一拍无需再索取数据。检查前缀终态无旧数据。
        beat_ready = 1'b0;
        tick_edge();
        if (done || beat_valid) $fatal(1, "done held or stale beat after host ends access");
        beat_ready = 1'b1;
    endtask

    task automatic header_lvds(input bit wait_before_start);
        // AA 前可等 PHY 许可。四个固定 pair 另查 first 与装载点，
        // 避免 0xAA 的重复位掩盖长度或首位错位。
        if (wait_before_start) begin
            beat_ready = 1'b0;
            repeat (3) begin
                #1;
                if (beat_valid || payload_allow || !busy) $fatal(1, "AA started without PHY ready");
                tick_edge();
            end
            beat_ready = 1'b1;
        end
        tick_edge();
        for (int idx = 0; idx < 4; idx++) begin
            #1;
            if (payload_allow != (idx == 3)) $fatal(1, "LVDS transport admission not on header boundary");
            check_beat(4'h2, 2, 0, idx == 0, 1'b0);
        end
    endtask

    task automatic source_lvds(
        input logic [19:0] codeword, input bit end_record, input bit early_crc
    );
        // 预先给好的输入位流模拟已验证的 transport。该模块集成另有真实 SRAM 测例。
        // CRC 恰在末载荷位对有效，直接覆盖最紧的同沿时限。较早字末只能
        // 请求装下一字，不能收尾。
        for (int idx = 0; idx < 10; idx++) begin
            payload_valid = 1'b1;
            payload_first = idx == 0;
            payload_last = idx == 9;
            payload_pair = 2'(on_wire({12'b0, codeword}, 2, 10, idx));
            crc_valid = early_crc && end_record && idx == 9;
            #1;
            if (payload_allow != (!end_record && idx == 9))
                $fatal(1, "back-to-back LVDS admission crossed record count");
            if (crc_ready != (end_record && idx == 9))
                $fatal(1, "CRC response enabled outside end-of-payload");
            check_beat({2'b00, payload_pair}, 2, 1, 1'b0, 1'b0);
        end
        payload_valid = 1'b0;
        crc_valid = 1'b0;
    endtask

    // [预取] 第一个 AA 之前取两字。真实接收端不能消掉两次 SRAM 回读
    // 之间的时钟拍，因此这个检查要在任何输出出现前完成。
    task automatic push_spi(input logic [19:0] word_value, input bit last_value);
        sample_valid = 1'b1;
        sample = word_value;
        sample_last = last_value;
        #1;
        if (!sample_ready || beat_valid) $fatal(1, "SPI prefetch consumed incorrectly");
        tick_edge();
        sample_valid = 1'b0;
    endtask

    // =========================================================================
    // 3. 三种串行排列及 FIFO 固定长度
    // =========================================================================
    task automatic run_spi(input bit quad, input bit withhold_third);
        int width, payload_beats, tail_beats;
        logic [31:0] header_vector, negative_vector, positive_vector;
        width = quad ? 4 : 1;
        payload_beats = quad ? 6 : 24;
        tail_beats = quad ? 6 : 24;
        // 图 88 的四线数据按 A/B/C/D 排。24'hF81234 的线序为 F184C2，
        // 24'h012468 的线序为 084261。CRC 的假定测试响应已预先按线打包。
        header_vector = quad ? 32'hF0F0F0F0 : 32'h000000AA;
        negative_vector = quad ? 32'h00F184C2 : 32'h00F81234;
        positive_vector = quad ? 32'h00084261 : 32'h00012468;
        crc_service_ready = BIND_SPI_TAIL;
        issue_command(quad ? 2'd2 : 2'd1, withhold_third ? 15'd3 : 15'd2, 3'd0);

        // `push_spi` 在下一原始边沿后才撤 valid。末字标识取本轮声明的固定
        // 字数而非来源实际到位字数，因此少一个字必然变成断供而不是短帧。
        push_spi(20'h81234, 0);
        push_spi(20'h12468, !withhold_third);
        tick_edge();
        for (int idx = 0; idx < 8; idx++)
            check_beat(on_wire(header_vector, width, 8, idx), width, 0, idx == 0, 1'b0);
        for (int idx = 0; idx < payload_beats; idx++)
            check_beat(on_wire(negative_vector, width, payload_beats, idx), width, 1, 0, 0);
        // 末字之后强制查 ready 关闭。扩展尾模式不能把末样本当作整条完成，
        // 默认模式才允许固件在该时钟末端停止索取数据。
        for (int idx = 0; idx < payload_beats; idx++) begin
            crc_valid = !withhold_third && BIND_SPI_TAIL && idx == payload_beats-1;
            #1;
            if (sample_ready != withhold_third || crc_ready != crc_valid)
                $fatal(1, "SPI sample/CRC deadline mismatch");
            check_beat(on_wire(positive_vector, width, payload_beats, idx), width, 1, 0,
                       !withhold_third && !BIND_SPI_TAIL && idx == payload_beats-1);
        end
        crc_valid = 1'b0;
        if (withhold_third) begin
            // 两字预取只保证起发，真实第三字不来仍须在下一个原始 DCLK 失败。
            tick_edge();
            check_fault(7, BIND_SPI_TAIL);
            // 已释放记录而无旧输出，下一条用合法命令独自重新开始。
            $display("PASS SPI raw starvation mode=%0d valid_prefix=%0d error=7", mode, record_ticks);
            crc_service_ready = 1'b1;
            return;
        end
        if (BIND_SPI_TAIL) begin
            for (int idx = 0; idx < tail_beats; idx++)
                check_beat(on_wire(32'h00A5C39E, width, tail_beats, idx), width, 2, 0,
                           idx == tail_beats-1);
        end
        check_finished();
        crc_service_ready = 1'b1;
    endtask

    task automatic run_full_depth_prefix;
        // [计数] 满深度走完整的 16,384 个字，验证 14 位边界以上的末字，
        // 不依赖层级读 DUT 计数。两字轮换码核对满时同沿进出，避免零字遮错。
        crc_service_ready = 1'b0;
        issue_command(2, 15'd16384, 0);
        push_spi(20'h81234, 0);
        push_spi(20'h12468, 0);
        tick_edge();
        for (int idx = 0; idx < 8; idx++)
            check_beat(on_wire(32'hF0F0F0F0, 4, 8, idx), 4, 0, idx == 0, 0);
        for (int word = 0; word < 16384; word++) begin
            sample = word[0] ? 20'h12468 : 20'h81234;
            sample_last = word == 16381;
            for (int idx = 0; idx < 6; idx++) begin
                sample_valid = word < 16382 && idx == 5;
                #1;
                if (sample_ready != sample_valid) $fatal(1, "SPI whole-depth refill qualification");
                check_beat(on_wire(word[0] ? 32'h00084261 : 32'h00F184C2, 4, 6, idx),
                           4, 1, 0, word == 16383 && idx == 5);
            end
        end
        check_finished();
        crc_service_ready = 1'b1;
    endtask

    initial begin
        tick_edge();
        rst_n = 1'b1;
        // [顺序] 两字连续 LVDS，最后一对载荷才接 CRC，同一尾最后一拍退役。
        issue_command(0, 15'd2, 0);
        header_lvds(1'b1);
        source_lvds(20'h81234, 0, 0);
        source_lvds(20'h12468, 1, 1);
        for (int idx = 0; idx < 12; idx++)
            check_beat(on_wire(32'h00A5C39E, 2, 12, idx), 2, 2, 0, idx == 11);
        check_finished();
        run_spi(1'b0, 0);
        run_spi(1'b1, 0);
        run_spi(1'b0, 1);
        run_spi(1'b1, 1);
        // 扩展尾已在短记录检查，只有默认 SPI 运行最大长度的公开前缀。
        if (!BIND_SPI_TAIL) run_full_depth_prefix();

        // [拒绝] 三种命令错误以及外部无会话、无绑定、残留响应均不得出头。
        issue_command(3, 15'd1, 1);
        issue_command(0, 15'd0, 1);
        issue_command(0, 15'd16385, 1);
        format_bound = 1'b0;
        issue_command(0, 15'd1, 2);
        format_bound = 1'b1;
        crc_service_ready = 1'b0;
        issue_command(0, 15'd1, 2);
        crc_service_ready = 1'b1;
        // 上一会话未收走的 CRC 若在空闲时残留，应视为服务没有排清。
        crc_valid = 1'b1;
        issue_command(0, 15'd1, 2);
        crc_valid = 1'b0;

        // [字数] SPI 输入 last 过早和缺失均拒绝。单 LVDS 查槽标记，
        // 次数和 PHY 非反压边界与同一个中止机制衔接。
        issue_command(1, 15'd2, 0);
        sample_valid = 1'b1;
        sample_last = 1'b1;
        tick_edge();
        sample_valid = 1'b0;
        check_fault(3, BIND_SPI_TAIL);
        issue_command(2, 15'd1, 0);
        sample_valid = 1'b1;
        sample_last = 1'b0;
        tick_edge();
        sample_valid = 1'b0;
        check_fault(3, BIND_SPI_TAIL);

        issue_command(0, 15'd1, 0);
        header_lvds(0);
        payload_valid = 1'b1;
        payload_first = 1'b1;
        payload_last = 1'b1;
        tick_edge();
        payload_valid = 1'b0;
        check_fault(3, 1);

        // 漏 pair 产生断供故障。读满后额外一字产生协议/边界故障。
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        payload_valid = 1'b1;
        payload_first = 1'b1;
        payload_last = 1'b0;
        check_beat({2'b00, payload_pair}, 2, 1, 0, 0);
        payload_valid = 1'b0;
        tick_edge();
        check_fault(7, 1);

        issue_command(0, 15'd1, 0);
        header_lvds(0);
        source_lvds(20'h24816, 1, 1);
        payload_valid = 1'b1;
        payload_first = 1'b1;
        payload_last = 1'b0;
        tick_edge();
        payload_valid = 1'b0;
        check_fault(3, 1);

        // 原始记录运行中已出线，撤销 ready 不能等到下一拍继续发送。
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        payload_valid = 1'b1;
        payload_first = 1'b1;
        payload_last = 1'b0;
        beat_ready = 1'b0;
        tick_edge();
        payload_valid = 1'b0;
        beat_ready = 1'b1;
        check_fault(6, 1);

        // 完整 payload 已发的情况下 CRC 服务仍能拒绝，终态须缺尾且无完成。
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        crc_error = 1'b1;
        source_lvds(20'h00ABC, 1, 1);
        crc_error = 1'b0;
        check_fault(4, 1);

        // 最末载荷一拍没有 CRC。随后到来的响应也不得恢复记录。
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        source_lvds(20'h089AB, 1, 0);
        crc_valid = 1'b1;
        check_fault(4, 1);
        crc_valid = 1'b0;

        // 两字只确认一字时停在预备，观察多个 raw CLK，之后由调用者撤销。
        payload_prefetch = 2'd1;
        issue_command(0, 15'd2, 0);
        repeat (5) begin
            tick_edge();
            if (beat_valid || payload_allow || !busy) $fatal(1, "AA emitted without two-word prefetch");
        end
        abort_now = 1'b1;
        tick_edge();
        abort_now = 1'b0;
        payload_prefetch = 2'd2;
        check_fault(6, 1);

        // 头刚出一拍或尾刚出一拍后撤销准入，两个分段均必须中止。
        issue_command(0, 15'd1, 0);
        tick_edge();
        check_beat(4'h2, 2, 0, 1, 0);
        beat_ready = 1'b0;
        tick_edge();
        beat_ready = 1'b1;
        check_fault(6, 1);
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        source_lvds(20'h55AA0, 1, 1);
        check_beat(on_wire(32'h00A5C39E, 2, 12, 0), 2, 2, 0, 0);
        beat_ready = 1'b0;
        tick_edge();
        beat_ready = 1'b1;
        check_fault(6, 1);

        // [电源] 掉电不等运行沿才撤销 valid。时钟仍活着时还须上报中止。
        issue_command(0, 15'd16384, 0);
        power_good = 1'b0;
        #1;
        if (beat_valid || start_ready || payload_allow) $fatal(1, "live output on power failure");
        tick_edge();
        power_good = 1'b1;
        check_fault(5, 1);

        // [DCS 替身与复位] 上游 abort 模拟未完成时主机撤销 DCS。
        // 单独硬复位可在停钟时立即隔离，第二笔须从 0xAA 起恢复。
        issue_command(1, 15'd1, 0);
        abort_now = 1'b1;
        tick_edge();
        abort_now = 1'b0;
        check_fault(6, BIND_SPI_TAIL);
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        source_lvds(20'h45678, 1, 1);
        rst_n = 1'b0;
        #1;
        if (beat_valid || busy || done) $fatal(1, "hard reset leaked CRC or busy");
        tick_edge();
        rst_n = 1'b1;
        // 更换样本和 CRC，旧尾在硬复位后不能混进新有效数据。
        crc_tail = 24'h693FA1;
        issue_command(0, 15'd1, 0);
        header_lvds(0);
        source_lvds(20'h54321, 1, 1);
        for (int idx = 0; idx < 12; idx++)
            check_beat(on_wire(32'h00693FA1, 2, 12, idx), 2, 2, 0, idx == 11);
        check_finished();

        $display("PASS FIFO record SPI_TAIL=%0d beats=%0d fault_checks=%0d", BIND_SPI_TAIL, beat_checks, fault_checks);
        $finish;
    end
endmodule

`default_nettype wire
