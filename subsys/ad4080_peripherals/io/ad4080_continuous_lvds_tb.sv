`timescale 1ps/1ps

// =============================================================================
// 文件名     : ad4080_continuous_lvds_tb.sv
// 模块名     : ad4080_continuous_lvds_tb
// 版本       : v1.0
// 功能       : 用同一时间轴的 CNV、CLK、DCO 和串行 DA/DB 核验两条连续路径。
// 设计说明   :
//   * 表 20 使用独立查表常量。参考帧从 CNV 预约时刻推导，不读取 DUT
//     计数器、发射脉冲或 PHY 掩码。每个物理方向的逻辑边沿逐位解码。
//   * 理想 DDR Fixture 在 FALL 锁存同一 pair，分别送给当次 FALL 和下次
//     RISE。CNV 保证提前 100 ps，模拟端口传播和 tCCA 公差需另行实测。
//   * 结果源在定向试验可直送，在跨域试验经两槽 CDC，二者使用同一叶。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [图97/表49] 增加滤波真字保持及逐码元 Manchester 对拍。
// =============================================================================

module ad4080_continuous_lvds_tb;
    // =========================================================================
    // 1. 独立时钟、输入绑定和理想 DDR 观察点
    // =========================================================================
    // Rev. B p49 图 79 的紧随 CNV 的 FALL 已标成 10，第二个 FALL
    // 才进 count 1。图 80 同理以 5 开头，因此 max 档距离原 CNV
    // 分别是第 21/11 个 FALL（单/双）；dual 的延迟箭头在后一沿。
    localparam time SINGLE_HALF = 1250ps, DUAL_HALF = 2500ps;
    localparam time CNV_SETUP = 100ps, SETTLE = 20ps;
    // 表 20 的编码是寄存器值，后面的 count 是实际本相位时钟数。
    // 表 21 示例的 corrected 代码 7/2 是两个 lane 档的最长悬挂。
    localparam int SINGLE_COUNT[10] = '{3, 4, 5, 6, 7, 8, 9, 10, 1, 2};
    localparam int DUAL_COUNT[5] = '{3, 4, 5, 1, 2};
    localparam int REF_DEPTH = 32;
    localparam int REF_INDEX_WIDTH = $clog2(REF_DEPTH);
    localparam int CDC_TEST_COUNT = 24;            // 指针多次回绕、时钟域相位滑动

    logic dclk = 1'b0, core_clk = 1'b0, io_run = 1'b1;
    time half_clk = SINGLE_HALF;                  // 表 21 两个 40MS/s 的标称时钟
    // 夹具同时观察异步清空与同步受理。两种用法均需引用同一复位事件。
    /* verilator lint_off SYNCASYNCNET */
    logic reset_n = 1'b0;
    /* verilator lint_on SYNCASYNCNET */
    logic enable = 1'b0;
    logic filter_mode = 1'b0, clear_epoch = 1'b0;  // 单独验证已发布真字与旧会话清除
    logic [7:0] cfg_a = '0, cfg_b = '0;
    logic cnv_pin = 1'b0, cnv_pin_q, cnv_event;
    logic [15:0] cnv_tag = '0;
    // 同一调度叶在定向测试使用两种来源，区别只在字的可用时刻。
    logic cdc_select = 1'b0;
    logic direct_valid = 1'b0, core_valid = 1'b0;
    logic [15:0] direct_tag = '0, core_tag = '0;
    logic [19:0] direct_word = '0, core_word = '0;
    logic bridge_valid, bridge_ready, bridge_drop;
    logic [15:0] bridge_tag;
    logic [19:0] bridge_word;
    logic pair_valid, pair_first, pair_last, dco, dco_oe, da_oe, db_oe;
    logic da_fall, da_rise, db_fall, db_rise, cfg_ok, idle;
    logic cnv_rejected, result_rejected, missing;
    logic [15:0] pair_tag, pair_source_tag, missing_tag;
    logic pair_repeat;                            // 原始模式和训练仍须恒低
    logic da_fall_q, da_rise_q, db_fall_q, db_rise_q, launched_valid_q;
    logic da_serial, db_serial;                    // 用 DCO 边沿还原 PHY 两条数字线

    initial forever begin
        #(half_clk);
        if (io_run) dclk = ~dclk;
    end
    initial forever #3500ps core_clk = ~core_clk;   // 与 LVDS CLK 非整相，跨域结果不插空锁频

    // [激励边界] CNV 两个边都由 run_cycle 在 CLK 上升附近放置。物理接收
    // 和同步恢复假定已经可用，用前态做上升沿事件，保留第一拍的真实轴。
    always_ff @(posedge dclk or negedge reset_n) begin
        if (!reset_n) cnv_pin_q <= 1'b0;
        else cnv_pin_q <= cnv_pin;
    end
    assign cnv_event = cnv_pin && !cnv_pin_q;

    ad4080_continuous_result_cdc u_cdc (
        .clk_core_i(core_clk), .clk_io_i(dclk), .reset_ni(reset_n),
        .valid_core_i(core_valid), .tag_core_i(core_tag), .word_core_i(core_word),
        .ready_core_o(bridge_ready), .drop_core_o(bridge_drop),
        .valid_io_o(bridge_valid), .tag_io_o(bridge_tag), .word_io_o(bridge_word)
    );

    ad4080_continuous_lvds dut (
        .clk_dclk_i(dclk), .rst_ni(reset_n), .enable_i(enable),
        .config_a_i(cfg_a), .config_b_i(cfg_b), .filter_mode_i(filter_mode), .clear_i(clear_epoch),
        .cnv_event_i(cnv_event), .cnv_tag_i(cnv_tag),
        .result_valid_i(cdc_select ? bridge_valid : direct_valid),
        .result_tag_i(cdc_select ? bridge_tag : direct_tag),
        .result_i(cdc_select ? bridge_word : direct_word),
        .pair_valid_o(pair_valid), .pair_first_o(pair_first), .pair_last_o(pair_last),
        .pair_tag_o(pair_tag), .pair_source_tag_o(pair_source_tag), .pair_repeat_o(pair_repeat),
        .da_fall_o(da_fall), .da_rise_o(da_rise),
        .db_fall_o(db_fall), .db_rise_o(db_rise), .dco_clk_o(dco), .dco_oe_o(dco_oe),
        .da_oe_o(da_oe), .db_oe_o(db_oe), .config_ok_o(cfg_ok), .idle_o(idle),
        .cnv_rejected_o(cnv_rejected), .result_rejected_o(result_rejected),
        .frame_missing_o(missing), .frame_missing_tag_o(missing_tag)
    );

    // [物理观察点] FALL 同时锁住两个位，保证下一 RISE 不取上升沿后才
    // 更新的槽。这片理想夹具模拟数字边序，不能用来量取最大传播时延。
    always_ff @(negedge dco or negedge reset_n) begin
        if (!reset_n) begin
            da_fall_q <= 1'b0;
            da_rise_q <= 1'b0;
            db_fall_q <= 1'b0;
            db_rise_q <= 1'b0;
            launched_valid_q <= 1'b0;
        end else begin
            da_fall_q <= da_fall;
            da_rise_q <= da_rise;
            db_fall_q <= db_fall;
            db_rise_q <= db_rise;
            launched_valid_q <= pair_valid;
        end
    end
    assign da_serial = dco ? da_rise_q : da_fall_q;
    assign db_serial = dco ? db_rise_q : db_fall_q;

    // =========================================================================
    // 2. 引脚级参考模型与脚本
    // =========================================================================
    typedef struct packed {
        int first_cycle;
        logic [15:0] tag;
        logic [15:0] source_tag;
        logic [19:0] word;
        bit repeated;
    } ref_frame_t;
    // 参考排程由激励点和手册值预先生成，只在普通帧预期完整时登记。
    // 非法配置、启动未到期、缺字过期都用空时隙检测，不过滤这些拍。
    ref_frame_t frames[REF_DEPTH];                   // 缺帧不加入参考发送排程
    int frame_count = 0, current_frame = -1, current_slot = -1;
    int period, bits_per_edge;
    int da_high_half = 0, db_high_half = 0;        // 两条 lane 一字内高电平半码计数
    time origin_rise;
    int bits_checked = 0, frames_completed = 0, negative_cases = 0;

    // [位序 oracle] 从实际占用的边沿数推导第几位。没有复用 DUT 的 pair
    // 拆解公式，也不会根据 DUT 给的 slot_count 放宽验证范围。
    task automatic check_edge(input bit rising, input time edge_time);
        int bit_offset;
        int half_cycles;
        time expected_edge;
        bit want_da, want_db;
        if (dco !== rising || launched_valid_q !== (current_frame >= 0))
            $fatal(1, "DCO direction or DDR launched validity mismatch at %0t", edge_time);
        if (current_frame >= 0) begin
            half_cycles = 2 * (frames[current_frame].first_cycle + current_slot) + 1 + int'(rising);
            expected_edge = origin_rise + time'(half_cycles) * half_clk;
            if (edge_time != expected_edge)
                $fatal(1, "tag %h edge %0t expected %0t", frames[current_frame].tag,
                       edge_time, expected_edge);
            // 单 lane 一沿一位；双 lane 在同一个沿先看 DA，再看 DB。
            // 槽号沿时间增长，首槽的 RISE 始终是下一 CLK 周期的边沿。
            bit_offset = bits_per_edge * (2 * current_slot + int'(rising));
            if (cfg_b[2]) bit_offset = 2 * current_slot;
            want_da = frames[current_frame].word[19-bit_offset];
            want_db = bits_per_edge == 2 && frames[current_frame].word[18-bit_offset];
            // [IEEE 802.3] 后半是位值，前半应为其反相。逐沿检查同时
            // 要求每条 lane 一字恰有 10 高/10低，便于发现不对称填充。
            if (cfg_b[2]) begin
                if (!rising) begin
                    want_da = !want_da;
                    want_db = !want_db;
                end
                if (!rising && current_slot == 0) begin
                    da_high_half = 0;
                    db_high_half = 0;
                end
                da_high_half += int'(da_serial);
                db_high_half += int'(db_serial);
            end
            if (da_serial !== want_da || db_serial !== want_db)
                $fatal(1, "tag %h slot %0d rising=%0b DA/DB=%b%b word=%h",
                       frames[current_frame].tag, current_slot, rising, da_serial, db_serial,
                       frames[current_frame].word);
            bits_checked += bits_per_edge;
            if (rising && current_slot == period - 1) begin
                if (cfg_b[2] && (da_high_half != 10 || db_high_half != 10))
                    $fatal(1, "Manchester word was not balanced on both lanes");
                frames_completed++;
            end
        end else if ({da_serial, db_serial} != 2'b00) begin
            $fatal(1, "invalid slot drove nonzero DA/DB");
        end
    endtask

    // [预约 oracle] first_cycle 来自试验预定的 CNV 上升编号、表 20 count
    // 和每转换时钟数。每组用自己的时钟原点，覆盖第一个帧和跨转换尾沿。
    task automatic add_frame(input int first_cycle, input logic [15:0] tag,
                             input logic [19:0] word, input logic [15:0] source_tag = '0,
                             input bit repeated = 1'b0);
        if (frame_count == REF_DEPTH) $fatal(1, "test oracle overflow");
        frames[frame_count] = '{first_cycle, tag, filter_mode ? source_tag : tag, word, repeated};
        frame_count++;
    endtask

    // [轴切换] 单线与双线用不同 CLK，但 CNV 周期都是 25 ns。
    // 修改周期后先空跑；此处的复位释放间隔只处理本地同步流水。
    task automatic prepare(input logic [7:0] a, input logic [7:0] b,
                           input time half_period, input bit active = 1'b1,
                           input bit filter_active = 1'b0);
        reset_n = 1'b0;
        enable = 1'b0;
        cnv_pin = 1'b0;
        direct_valid = 1'b0;
        core_valid = 1'b0;
        cdc_select = 1'b0;
        io_run = 1'b1;
        half_clk = half_period;
        cfg_a = a;
        cfg_b = b;
        filter_mode = filter_active;
        clear_epoch = 1'b0;
        period = a[2] && !b[2] ? 5 : 10;
        bits_per_edge = a[2] ? 2 : 1;
        frame_count = 0;
        current_frame = -1;
        current_slot = -1;
        repeat (4) @(posedge dclk);
        #(SETTLE);
        reset_n = 1'b1;
        repeat (8) @(posedge dclk);
        #(SETTLE);
        enable = active;
    endtask

    task automatic run_cycle(
        input int tick,
        input bit cnv_level = 1'b0,
        input logic [15:0] input_cnv_tag = '0,
        input bit source_valid = 1'b0,
        input logic [15:0] source_tag = '0,
        input logic [19:0] source_word = '0,
        input bit expect_cnv_reject = 1'b0,
        input bit expect_result_reject = 1'b0,
        input bit expect_missing = 1'b0,
        input logic [15:0] expect_missing_tag = '0
    );
        time edge_time;
        logic [REF_INDEX_WIDTH-1:0] selected;
        @(negedge dclk);
        edge_time = $time;
        #(SETTLE);
        check_edge(1'b0, edge_time);
        direct_valid = source_valid;
        direct_tag = source_tag;
        direct_word = source_word;
        // CNV 高/低变化都预留稳定时间再遇 DCLK+ 的上升。相位值只是
        // fixture 选择，真实 pad 对齐须依表 2 测 535 ps 公差。
        #(half_clk - CNV_SETUP - SETTLE);
        cnv_pin = cnv_level;
        cnv_tag = input_cnv_tag;
        @(posedge dclk);
        edge_time = $time;
        #(SETTLE);
        check_edge(1'b1, edge_time);
        if (tick == 0) origin_rise = edge_time;

        // [验收] 新 pair 只在本上升后观察。前一 pair 已在两个真实边沿取完，
        // 每个无效槽也比较，不会在测试中误把空拍跳过去。
        current_frame = -1;
        current_slot = -1;
        for (int frame = 0; frame < frame_count; frame++) begin
            if (tick >= frames[frame].first_cycle && tick < frames[frame].first_cycle + period) begin
                if (current_frame >= 0) $fatal(1, "overlapping reference frames");
                current_frame = frame;
                current_slot = tick - frames[frame].first_cycle;
            end
        end
        selected = current_frame >= 0 ? REF_INDEX_WIDTH'(current_frame) : '0;
        if (pair_valid !== (current_frame >= 0) ||
            pair_first !== (current_slot == 0) ||
            pair_last !== (current_slot == period - 1) ||
            pair_tag !== (current_frame >= 0 ? frames[selected].tag : 16'h0000) ||
            pair_source_tag !== (current_frame >= 0 ? frames[selected].source_tag : 16'h0000) ||
            pair_repeat !== (current_frame >= 0 && frames[selected].repeated))
            $fatal(1, "tick %0d first/last/valid/tag %b/%b/%b/%h, expected frame=%0d slot=%0d tag=%h",
                   tick, pair_first, pair_last, pair_valid, pair_tag, current_frame, current_slot,
                   frames[selected].tag);
        // 三路诊断独立核验。早 CNV 和错字可能同拍，缺旧字不会自动
        // 触发其它 tag 的拒绝，也不会破坏已经就绪的后一笔。
        if ({cnv_rejected, result_rejected, missing} !==
            {expect_cnv_reject, expect_result_reject, expect_missing} ||
            (expect_missing && missing_tag !== expect_missing_tag))
            $fatal(1, "tick %0d diagnostics CNV/result/missing/tag %b/%b/%b/%h expected %b/%b/%b/%h",
                   tick, cnv_rejected, result_rejected, missing, missing_tag,
                   expect_cnv_reject, expect_result_reject, expect_missing, expect_missing_tag);
        if (expect_cnv_reject || expect_result_reject || expect_missing) negative_cases++;
    endtask

    // =========================================================================
    // 3. 表 20 的所有合法码、相邻连续、跨周期、tag 回绕
    // =========================================================================
    task automatic all_slots(input bit dual, input int cfg_code, input int count_number);
        const logic [19:0] data_words[4] = '{20'h8AF31, 20'h714CD, 20'h00FA1, 20'hB5637};
        const logic [15:0] tags[4] = '{16'hFFFE, 16'hFFFF, 16'h0000, 16'h0001};
        int ready_tick[4];
        int delay;
        int ordered;
        int begin_frames;
        int last_cycle;
        prepare(dual ? 8'h44 : 8'h40, 8'(cfg_code << 4), dual ? DUAL_HALF : SINGLE_HALF);
        delay = period + count_number;
        last_cycle = delay + period;
        begin_frames = frames_completed;
        // [边界] 开始同沿、提前、截止同沿均可交字。最大档的第二个字正好
        // 在另一个 CNV 申请时到期，测最终读取与复用槽写入的原子性。
        ready_tick = '{0, period + delay, 2*period + delay - 1, 3*period + delay - 2};
        for (int sample = 0; sample < 4; sample++)
            add_frame(sample * period + delay, tags[sample], data_words[sample]);
        // 连续四个 CNV 每帧不额外插空。循环多过末字一个上升，既吃掉
        // 最后一个上升位，也能观察 PHY 能否从正常退休回到无有效帧。
        for (int tick = 0; tick <= 4*period + delay; tick++) begin
            ordered = -1;
            foreach (ready_tick[sample]) begin
                if (tick == ready_tick[sample]) ordered = sample;
            end
            if (ordered >= 0)
                run_cycle(tick, tick < 4*period && tick % period == 0,
                          tags[(tick/period) % 4], 1'b1, tags[ordered], data_words[ordered]);
            else
                run_cycle(tick, tick < 4*period && tick % period == 0, tags[(tick/period) % 4]);
            if (!cfg_ok || !dco_oe || !da_oe || db_oe !== dual)
                $fatal(1, "valid profile lost expected pad ownership");
        end
        if (frames_completed - begin_frames != 4 || !idle)
            $fatal(1, "reference profiles must complete four frames and drain");
        $display("PASS table20 lane=%0d code=0x%0h count=%0d first_ps=%0d last_ps=%0d frames=4",
                 bits_per_edge, cfg_code, count_number, time'(2*delay+1)*half_clk,
                 (2*time'(last_cycle))*half_clk);
    endtask

    // =========================================================================
    // 4. 晚字、错号、过早 CNV、非法模式和训练
    // =========================================================================
    task automatic deadline_and_reset;
        prepare(8'h40, 8'h70, SINGLE_HALF);
        // 原本在第 20 拍装载的 tag 0100 迟到，预期整个槽段为空。
        // 后面的 tag 0101 在自己的第 30 拍起发，重复写不得替换它。
        add_frame(30, 16'h0101, 20'hAED85);
        for (int tick = 0; tick <= 33; tick++) begin
            case (tick)
                0:  run_cycle(tick, 1'b1, 16'h0100);
                2:  run_cycle(tick, 1'b1, 16'hFE00, 1'b1, 16'hFF00, 20'hFFFFF, 1'b1, 1'b1);
                10: run_cycle(tick, 1'b1, 16'h0101);
                12: run_cycle(tick, 1'b0, '0, 1'b1, 16'h0101, 20'hAED85);
                13: run_cycle(tick, 1'b0, '0, 1'b1, 16'h0101, 20'h18529, 1'b0, 1'b1);
                20: run_cycle(tick, 1'b0, '0, 1'b0, '0, '0, 1'b0, 1'b0, 1'b1, 16'h0100);
                21: run_cycle(tick, 1'b0, '0, 1'b1, 16'h0100, 20'h3812D, 1'b0, 1'b1);
                default: run_cycle(tick);
            endcase
        end
        // [复位] 中途截断第二帧，OE 和有效立即撤销，晚到旧 tag 不能在
        // 下个会话借住任何在途槽。来自新会话的新字仍能按新时隙完整输出。
        reset_n = 1'b0;
        #(SETTLE);
        if (pair_valid || dco_oe || da_oe || db_oe || !idle)
            $fatal(1, "reset did not isolate an in-flight frame");
        prepare(8'h44, 8'h20, DUAL_HALF);
        add_frame(10, 16'h0200, 20'h884DA);
        for (int tick = 0; tick <= 15; tick++) begin
            case (tick)
                0: run_cycle(tick, 1'b1, 16'h0200, 1'b1, 16'h0200, 20'h884DA);
                1: run_cycle(tick, 1'b0, '0, 1'b1, 16'h0101, 20'hAED85, 1'b0, 1'b1);
                default: run_cycle(tick);
            endcase
        end
        $display("PASS deadline: missing tag=0100, subsequent word and reset isolation");
    endtask

    // [错误入口] 向非法配置同时提交一个 CNV 和整字，要求返回两路拒绝。
    // 只看 cfg_ok 会漏掉接到了错误 PHY 的实现，所以同时看各 OE。
    task automatic unsupported(input logic [7:0] a, input logic [7:0] b, input bit with_filter = 1'b0);
        prepare(a, b, a[2] ? DUAL_HALF : SINGLE_HALF, 1'b1, with_filter);
        run_cycle(0, 1'b1, 16'h0001, 1'b1, 16'h0001, 20'hFFFFF, 1'b1, 1'b1);
        run_cycle(1);
        if (cfg_ok || dco_oe || da_oe || db_oe || !idle)
            $fatal(1, "unsupported profile energized outputs, config %h/%h", a, b);
    endtask

    // [训练] 没有 CNV 的最早时隙也持续出固定码。中间插入假转换及
    // 结果，不得污染训练流；self 的串行位与 echo 一致但关闭输出驱动。
    task automatic check_training(input bit dual, input bit self_clock);
        prepare(dual ? 8'h54 : 8'h50, self_clock ? 8'h49 : 8'h40,
                dual ? DUAL_HALF : SINGLE_HALF);
        for (int frame = 0; frame < 4; frame++)
            add_frame(frame*period, 16'h0000, 20'hAC5D6);
        for (int tick = 0; tick <= 3*period; tick++) begin
            if (tick == 3)
                run_cycle(tick, 1'b1, 16'hAB11, 1'b1, 16'hAB11, 20'hFFFFF, 1'b1, 1'b1);
            else run_cycle(tick);
            if (!cfg_ok || dco_oe !== !self_clock || !da_oe || db_oe !== dual)
                $fatal(1, "echo/self training pad enable mismatch");
        end
        // [新档] 活跃会话下把 self 位写反，立即封口。写回旧值不能解除
        // 锁定，直到撤销 enable 跨一沿，避免旧模式半帧继续送出。
        cfg_b[3] = ~cfg_b[3];
        @(posedge dclk);
        #(SETTLE);
        if (cfg_ok || pair_valid || da_oe) $fatal(1, "live config change was not rejected");
        cfg_b[3] = ~cfg_b[3];
        #(SETTLE);
        if (cfg_ok || pair_valid || da_oe) $fatal(1, "old config improperly resumed");
        enable = 1'b0;
        @(posedge dclk);
        #(SETTLE);
        if (!cfg_ok || !idle) $fatal(1, "disabled epoch did not clear live change fault");
        $display("PASS training lane=%0d self=%0b fixed 0xAC5D6 without CNV/CNT dependency",
                 bits_per_edge, self_clock);
    endtask

    // =========================================================================
    // 5. 两槽 CDC 与调度叶的实际串接
    // =========================================================================
    typedef struct packed {
        logic [15:0] tag;
        logic [19:0] word;
    } bridge_word_t;
    bridge_word_t expected_cdc[$];                  // 只记录真正被桥受理的写沿
    int cdc_received = 0, cdc_dropped = 0;

    // [跨域 oracle] 只在源沿观测受理，不能由 IO 的 valid 逆推出发送
    // 次序。共同复位取消未退休条目，旧内容再次出现即为错误。
    always @(negedge reset_n) expected_cdc.delete();
    always @(posedge core_clk) begin
        if (reset_n && core_valid && bridge_ready)
            expected_cdc.push_back({core_tag, core_word});
        #(SETTLE);
        // 观察计数在本模拟事件立即入账，维持既有定向读数。
        /* verilator lint_off BLKSEQ */
        if (bridge_drop) cdc_dropped++;
        /* verilator lint_on BLKSEQ */
    end
    always @(posedge dclk) begin
        bridge_word_t expected;
        if (reset_n && bridge_valid) begin
            if (expected_cdc.size() == 0) $fatal(1, "CDC produced a stale word");
            expected = expected_cdc.pop_front();
            if ({bridge_tag, bridge_word} !== expected)
                $fatal(1, "CDC changed tag/word order");
            /* verilator lint_off BLKSEQ */
            cdc_received++;
            /* verilator lint_on BLKSEQ */
        end
    end

    task automatic write_core(input logic [15:0] tag, input logic [19:0] data);
        @(negedge core_clk);
        core_valid = 1'b1;
        core_tag = tag;
        core_word = data;
        @(posedge core_clk);
        #(SETTLE);
        core_valid = 1'b0;
    endtask

    task automatic bridge_stream(input bit dual);
        int delay;
        int received_before;
        int dropped_before;
        logic [15:0] tag;
        logic [19:0] payload;
        int cnvs_sent;
        prepare(dual ? 8'h44 : 8'h40, dual ? 8'h20 : 8'h70, dual ? DUAL_HALF : SINGLE_HALF);
        cdc_select = 1'b1;
        delay = 2*period;
        received_before = cdc_received;
        dropped_before = cdc_dropped;
        cnvs_sent = 0;
        for (int sample = 0; sample < CDC_TEST_COUNT; sample++) begin
            tag = 16'h4980 + 16'(sample);
            payload = 20'h52DE * 20'(sample + 1) + 20'hB5500;
            add_frame(sample*period + delay, tag, payload);
        end

        fork
            begin
                for (int tick = 0; tick <= CDC_TEST_COUNT*period + delay; tick++) begin
                    run_cycle(tick, tick < CDC_TEST_COUNT*period && tick % period == 0,
                              16'h4980 + 16'(tick/period));
                    if (tick < CDC_TEST_COUNT*period && tick % period == 0) cnvs_sent++;
                end
            end
            begin
                for (int sample = 0; sample < CDC_TEST_COUNT; sample++) begin
                    wait (cnvs_sent > sample);
                    // [真实界面] 固定的是上游完成延迟范围，写沿来自独立 core。
                    // DSP 字/ID 首先经过两个 Gray 公布沿，再进预约叶作配对。
                    #(17ns);
                    write_core(16'h4980 + 16'(sample), 20'h52DE * 20'(sample + 1) + 20'hB5500);
                end
            end
        join
        if (!idle || cdc_received - received_before != CDC_TEST_COUNT || cdc_dropped != dropped_before)
            $fatal(1, "bridge streaming loss");
        $display("PASS CDC→scheduler lane=%0d 40MS/s frames=%0d", bits_per_edge, CDC_TEST_COUNT);
    endtask

    // [负向时序预算] 让结果在 50 ns 的装载截止前真正越过 core
    // 提交沿。再走两级写指针同步和叶消费，必跨过截止。下一笔
    // 尽管更早经过桥也不能补前一笔的空缺。
    task automatic bridge_deadline;
        int late_destination_tick;
        bit first_cnv_seen;
        time late_source_edge, planned_valid_time;
        prepare(8'h44, 8'h20, DUAL_HALF);
        cdc_select = 1'b1;
        late_destination_tick = -1;
        first_cnv_seen = 1'b0;
        add_frame(15, 16'h0015, 20'h6A89D);
        fork
            begin
                for (int tick = 0; tick <= 20; tick++) begin
                    run_cycle(tick, tick == 0 || tick == 5,
                              tick == 0 ? 16'h0014 : 16'h0015,
                              1'b0, '0, '0, 1'b0, tick == late_destination_tick,
                              tick == 10, 16'h0014);
                    if (tick == 0) first_cnv_seen = 1'b1;
                end
            end
            begin
                wait (first_cnv_seen);
                #(30ns);
                write_core(16'h0015, 20'h6A89D);
                planned_valid_time = origin_rise + time'(42020ps);
                if ($time < planned_valid_time) #(planned_valid_time - $time);
                core_valid = 1'b1;
                core_tag = 16'h0014;
                core_word = 20'hBA773;
                @(posedge core_clk);
                late_source_edge = $time;
                // core/IP 对齐时，两边触发器同沿都看旧 Gray。向下取整
                // 再 +1 定位第一个可能看到写指针的 CLK，+2 是交付。
                late_destination_tick = int'((late_source_edge-origin_rise)/(2*half_clk)) + 3;
                #(SETTLE);
                core_valid = 1'b0;
            end
        join
        if (late_source_edge - origin_rise >= time'(50000ps) || late_destination_tick <= 10 || !idle)
            $fatal(1, "CDC deadline fixture did not isolate crossing latency");
        $display("PASS CDC deadline: source_ps=%0d due_ps=50000 dst_ps=%0d, tag=0014 missing and rejected",
                 late_source_edge-origin_rise, time'(late_destination_tick)*2*half_clk);
    endtask

    task automatic bridge_full_reset;
        int received_before;
        int dropped_before;
        prepare(8'h40, 8'h70, SINGLE_HALF, 1'b0);
        received_before = cdc_received;
        dropped_before = cdc_dropped;
        io_run = 1'b0;
        write_core(16'h0010, 20'h12345);
        write_core(16'h0011, 20'h98765);
        if (bridge_ready) $fatal(1, "depth-two bridge did not report full");
        write_core(16'h0012, 20'h9A9B8);
        #(SETTLE);
        if (cdc_dropped - dropped_before != 1) $fatal(1, "bridge full drop not visible");

        // [判满恢复] 失败的第三笔不能改已提交槽。先恢复时钟让前两
        // 笔按原序接收，再次停钟并检查复位作废的另一笔。
        io_run = 1'b1;
        repeat (8) @(posedge dclk);
        #(SETTLE);
        if (cdc_received - received_before != 2 || expected_cdc.size() != 0)
            $fatal(1, "accepted bridge slots did not survive full drop");
        received_before = cdc_received;
        // 让回送的读状态释放写满，再暂停接收，留下一笔等共同复位。
        repeat (4) @(posedge core_clk);
        #(SETTLE);
        io_run = 1'b0;
        write_core(16'h0017, 20'h76543);

        // [停钟复位] 恢复后先等待空队列，证明旧寄存器数据不会随指针
        // 从零启动被发布。重新提交只能收到新字。
        reset_n = 1'b0;
        repeat (2) @(posedge core_clk);
        #(SETTLE);
        if (bridge_ready || bridge_valid) $fatal(1, "bridge was not isolated by reset");
        reset_n = 1'b1;
        io_run = 1'b1;
        repeat (9) @(posedge dclk);
        #(SETTLE);
        if (cdc_received != received_before || bridge_valid)
            $fatal(1, "stale bridge data after the common reset");
        write_core(16'h0013, 20'h6DB11);
        repeat (8) @(posedge dclk);
        #(SETTLE);
        if (cdc_received - received_before != 1 || expected_cdc.size() != 0)
            $fatal(1, "bridge did not deliver first fresh word after reset");
        $display("PASS CDC full with stopped DCLK, common reset and fresh first word");
    endtask

    // =========================================================================
    // 6. 图97保持、换代以及双线串行曼彻斯特
    // =========================================================================
    localparam logic [15:0] SOURCE_ONE = 16'hFA51, SOURCE_TWO = 16'hFA52;
    localparam logic [19:0] DATA_ONE = 20'h50ABC, DATA_TWO = 20'hD6902;
    task automatic filtered_slots;
        prepare(8'h40, 8'h00, SINGLE_HALF, 1'b1, 1'b1);

        // [图97] 期望前两笔无有效字，首次真字在截止拍到达。A 占四个
        // 相邻窗口，B 在 A 的末帧中途发布，只允许在下一帧替换整字。
        for (int sample = 2; sample < 6; sample++)
            add_frame(13 + 10*sample, 16'h9000 + 16'(sample), DATA_ONE, SOURCE_ONE, sample != 2);
        for (int sample = 6; sample < 9; sample++)
            add_frame(13 + 10*sample, 16'h9000 + 16'(sample), DATA_TWO, SOURCE_TWO, sample != 6);
        for (int tick = 0; tick <= 103; tick++) begin
            case (tick)
                13, 23: run_cycle(tick, 0, '0, 0, '0, '0, 0, 0, 1, 16'h9000 + 16'(tick/10-1));
                33: run_cycle(tick, 0, '0, 1, SOURCE_ONE, DATA_ONE);
                51: run_cycle(tick, 0, '0, 1, SOURCE_ONE, 20'hFFFFF, 0, 1);
                68: run_cycle(tick, 0, '0, 1, SOURCE_TWO, DATA_TWO);
                default: run_cycle(tick, tick % 10 == 0 && tick < 90, 16'h9000 + 16'(tick/10));
            endcase
        end

        // [换代] 新会话保留 line clock 并禁止旧滤波字，重新发布可重复用
        // 之前的序号，接收端仍核对新字和本槽身份。
        frame_count = 0;
        clear_epoch = 1'b1;
        run_cycle(104, 0, '0, 1, SOURCE_TWO, DATA_TWO, 0, 1);
        if (!idle || !dco_oe) $fatal(1, "filter flush did not clear data while preserving echo");
        clear_epoch = 1'b0;
        add_frame(23, 16'hA001, 20'hDE879, SOURCE_ONE);
        for (int tick = 0; tick <= 33; tick++) begin
            case (tick)
                0:  run_cycle(tick, 1, 16'hA000);
                10: run_cycle(tick, 1, 16'hA001);
                13: run_cycle(tick, 0, '0, 0, '0, '0, 0, 0, 1, 16'hA000);
                15: run_cycle(tick, 0, '0, 1, SOURCE_ONE, 20'hDE879);
                default: run_cycle(tick);
            endcase
        end
        $display("PASS Fig97: four copies of one real result, second result and generation flush");
    endtask

    task automatic manchester_slots;
        const logic [19:0] WORDS[5] = '{20'h001F0, 20'hA35B6, 20'hF0001, 20'h55555, 20'h2468B};
        int result_index;
        prepare(8'h44, 8'h24, SINGLE_HALF);
        // 每条线 400Mb/s 原载荷，编码半码速率 800Mchip/s。有效的
        // 5 组相邻转换和不够10拍的额外 CNV 均在同一真实时轴上。
        for (int sample = 0; sample < 5; sample++)
            add_frame(20 + 10*sample, 16'hB600 + 16'(sample), WORDS[sample]);
        for (int tick = 0; tick <= 70; tick++) begin
            result_index = (tick - 19)/10;
            if (tick == 5) run_cycle(tick, 1, 16'hB6AA, 0, '0, '0, 1);
            else if (tick >= 19 && (tick - 19) % 10 == 0 && result_index < 5)
                run_cycle(tick, 0, '0, 1, 16'hB600 + 16'(result_index), WORDS[result_index]);
            else run_cycle(tick, tick % 10 == 0 && tick < 50, 16'hB600 + 16'(tick/10));
            if (!cfg_ok || !dco_oe || !da_oe || !db_oe)
                $fatal(1, "valid double-lane Manchester profile was rejected");
        end
        $display("PASS Manchester: 5 words, equal high/low halves on both lanes, 40MS/s and 400MHz");
    endtask

    // =========================================================================
    // 7. 回归入口
    // =========================================================================
    initial begin
        foreach (SINGLE_COUNT[code]) all_slots(1'b0, code, SINGLE_COUNT[code]);
        foreach (DUAL_COUNT[code]) all_slots(1'b1, code, DUAL_COUNT[code]);
        deadline_and_reset();
        for (int code = 10; code < 16; code++) unsupported(8'h40, 8'(code << 4));
        for (int code = 5; code < 16; code++) unsupported(8'h44, 8'(code << 4));
        unsupported(8'h40, 8'h24);                  // Manchester 不允许单 lane
        unsupported(8'h44, 8'h24, 1'b1);            // 原件限定 FILTER_SEL=0
        unsupported(8'h44, 8'h54);                  // 编码模式仍用双 lane 表20 白名单
        unsupported(8'h40, 8'h02);                  // 重配保留位后不能误闯连续状态
        unsupported(8'h41, 8'h00);                  // SPI 走独立入口
        unsupported(8'h00, 8'h00);                  // 0x15 保留位 6 强制为 1
        check_training(1'b0, 1'b0);
        check_training(1'b1, 1'b1);
        bridge_stream(1'b0);
        bridge_stream(1'b1);
        bridge_deadline();
        bridge_full_reset();
        filtered_slots();
        manchester_slots();
        $display("PASS continuous LVDS: %0d checked bits, %0d completed words, %0d rejection/missing cycles",
                 bits_checked, frames_completed, negative_cases);
        $finish;
    end
    // [归档] 可选保留理想管脚波形。无 trace 时同一批断言仍完整执行。
    initial if ($test$plusargs("trace")) begin
        $dumpfile("continuous-lvds.vcd");
        $dumpvars(1, ad4080_continuous_lvds_tb);
    end

    // [退场] 时钟或握手若异常停止，失败有统一的确定时限。
    initial begin
        #(100us);
        $fatal(1, "watchdog expired");
    end
endmodule
