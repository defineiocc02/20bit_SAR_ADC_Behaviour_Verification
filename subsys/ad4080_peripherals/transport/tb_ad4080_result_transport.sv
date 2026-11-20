`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : tb_ad4080_result_transport.sv
// 模块名     : tb_ad4080_result_transport
// 版本       : v1.0
// 功能       : 独立跨时钟收发记分板，检查位序、满速无缝续发和会话冲刷。
// 设计说明   :
//   * 输入由核心域负沿驱动，输出以 IO 正沿提供的 FALL→RISE 装载对接收。
//     时钟的起始相位错开 413 ps，队列的生产和消费不会在测试台发生同沿争用。
//   * 接收端仅逐拍移入看到的位，不引用 DUT 映射向量、地址或内部状态。
//   * 512 笔单/双 lane 各自满速。另测空起和服务过载、模式同行、
//     暂停启动、满容量、停钟、任意一侧先复位、掉电和重入无旧字。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 审查后增加启动前拒收、空起首槽时间和活动读端过载回归。
// =============================================================================
module tb_ad4080_result_transport;

    // =========================================================================
    // 1. 接口与测试时钟
    // =========================================================================
    localparam int FIFO_ADDR_WIDTH = 3;  // 回归使用默认八格，另有一帧可在发
    localparam int FIFO_DEPTH = 1 << FIFO_ADDR_WIDTH;
    localparam int NOMINAL_COUNT = 512;  // 多次越过两个 Gray 回绕位

    typedef struct packed {
        logic [19:0] word;
        logic dual_lane;
        logic intf_check;
    } packet_t;

    // --- 独立时钟、复位和共有电源请求 ---
    logic clk_core = 1'b0;
    logic clk_io = 1'b0;
    logic run_core = 1'b1;
    logic run_io = 1'b1;
    time io_half_ps = 1250;  // 仿真单位 ps，运行中仅于复位窗口换频
    logic core_reset_n, io_reset_n, power_good;

    // --- 每个核心沿的一次发布机会及可观测入队行为 ---
    packet_t tx;
    logic result_valid;
    logic core_online, core_full, core_accept, core_drop, core_overflow, core_drained;

    // --- IO 的物理装载替身，仅从这些端口重组帧 ---
    logic io_start_allow, io_online, io_valid, io_first, io_last, io_dual, io_test;
    logic da_fall, da_rise, db_fall, db_rise;
    logic test_startup_attempt = 1'b0;  // 本域释放后故意在对侧未上线时送一笔

    ad4080_result_transport #(.FIFO_ADDR_WIDTH(FIFO_ADDR_WIDTH)) dut (
        // --- 任何一侧置低都开启新的传输会话 ---
        .clk_core_i(clk_core), .clk_io_i(clk_io),
        .core_reset_ni(core_reset_n), .io_reset_ni(io_reset_n), .power_good_async_i(power_good),
        // --- 源端保持每核心周期尝试一次，满时仍照常提供新值 ---
        .result_core_i(tx.word), .result_valid_core_i(result_valid),
        .dual_lane_core_i(tx.dual_lane), .intf_check_core_i(tx.intf_check),
        .core_online_o(core_online), .core_full_o(core_full), .core_accept_o(core_accept),
        .core_drop_o(core_drop), .core_overflow_o(core_overflow), .core_drained_o(core_drained),
        // --- 采走有效双沿位，准入仅影响新字 ---
        .io_start_allow_i(io_start_allow), .io_online_o(io_online), .io_pair_valid_o(io_valid),
        .io_first_o(io_first), .io_last_o(io_last), .io_dual_lane_o(io_dual),
        .io_intf_check_o(io_test), .io_da_fall_o(da_fall), .io_da_rise_o(da_rise),
        .io_db_fall_o(db_fall), .io_db_rise_o(db_rise)
    );

    // [停钟] 相位以各自原时基继续计数，门控仅冻结实际电平，恢复不补沿。
    initial forever begin
        #12500ps;
        if (run_core) clk_core = !clk_core;
    end

    initial begin
        #413ps;
        forever begin
            #(io_half_ps);
            if (run_io) clk_io = !clk_io;
        end
    end

    // =========================================================================
    // 2. 端口级参考模型
    // =========================================================================
    packet_t expected[$];  // 从核心端受理至 IO 末槽受理始终占一项
    int attempts, accepted, dropped, frames, contiguous_goal;
    int rx_slots;
    logic [19:0] rx_word;

    task automatic require_ok(input logic condition, input string reason);
        if (condition !== 1'b1) $fatal(1, "%s t=%0t", reason, $time);
    endtask

    // [计数] 入队判据在核心受理沿前读取，丢弃标志是在同一沿寄存的结果。
    // 将已领出且未发完的字留在队列里，可检出用领取指针误报已排空。
    initial forever begin : observe_core
        logic expect_drop;
        @(posedge clk_core);
        expect_drop = core_online && core_full && result_valid;
        require_ok(core_accept == (core_online && result_valid && !core_full), "write qualification");
        if (core_drained)
            require_ok(expected.size() == 0, "drain asserted before last pair retired");

        if (core_online && result_valid) begin
            attempts++;
            if (core_accept) begin
                expected.push_back(tx);
                accepted++;
            end else begin
                dropped++;
            end
        end
        #1ps;
        if (test_startup_attempt) begin
            require_ok(!core_online && !core_accept && core_drop && !core_overflow,
                       "startup result silently discarded after local reset release");
        end else begin
            require_ok(core_drop == expect_drop, "drop pulse did not match the full write");
        end
        if (expect_drop) require_ok(core_overflow, "drop did not latch overflow");
    end

    // [重组] 每个有效槽按实际观察的两个边沿依次左移。双 lane 在一个
    // 边沿按 DA、DB 收两位；单 lane 上出现 DB 数据会被单独判错。
    // 只在末槽移除期望字，因此提前/延后结束、重复和旧会话字均能定位。
    initial forever begin : receive_io
        packet_t wanted;
        logic [19:0] expected_word;
        int pair_goal;
        @(posedge clk_io);
        if (io_valid) begin
            require_ok(io_online && expected.size() > 0, "unaccepted or old-session frame leaked");
            wanted = expected[0];
            require_ok(io_first == (rx_slots == 0), "incorrect first slot");
            require_ok(io_dual == wanted.dual_lane && io_test == wanted.intf_check,
                       "lane/test settings changed or crossed between results");
            if (io_dual) begin
                rx_word = {rx_word[15:0], da_fall, db_fall, da_rise, db_rise};
                pair_goal = 5;
            end else begin
                require_ok(!db_fall && !db_rise, "single-lane frame drove DB data");
                rx_word = {rx_word[17:0], da_fall, da_rise};
                pair_goal = 10;
            end
            rx_slots++;
            require_ok(io_last == (rx_slots == pair_goal), "incorrect final slot or extra pair");
            if (io_last) begin
                wanted = expected.pop_front();
                expected_word = wanted.intf_check ? 20'hac5d6 : wanted.word;
                require_ok(rx_word == expected_word, "reassembled payload differs from oldest accepted sample");
                frames++;
                rx_slots = 0;
                rx_word = '0;
            end
        end else begin
            require_ok({io_first, io_last, io_dual, io_test, da_fall, db_fall, da_rise, db_rise} == 0,
                       "invalid beat exposed active lane bits");
            // [复位] 异步撤销可能先于测试台清队列而撞上接收沿，此时
            // 已收半帧允许作废；只有会话仍有效时才把缺少下一槽判为断流。
            if (core_reset_n && io_reset_n && power_good) begin
                require_ok(rx_slots == 0, "bubble within a frame");
                if (frames != 0 && frames < contiguous_goal)
                    require_ok(1'b0, "bubble between nominal-rate frames");
            end
        end
    end

    // =========================================================================
    // 3. 激励、会话切换和有界等待
    // =========================================================================
    task automatic clear_scoreboard;
        // [会话] 只在共同复位保持有效期间执行；部分接收帧随旧队列丢弃。
        expected.delete();
        attempts = 0;
        accepted = 0;
        dropped = 0;
        frames = 0;
        rx_slots = 0;
        rx_word = '0;
        contiguous_goal = 0;
    endtask

    task automatic wait_online;
        // [上电] 两时钟都运行后才允许继续，分别检查两向已释放通知。
        repeat (16) begin
            @(negedge clk_core);
            if (core_online && io_online) return;
        end
        $fatal(1, "link did not return online");
    endtask

    task automatic new_epoch(input time new_io_half_ps);
        core_reset_n = 1'b0;
        io_reset_n = 1'b0;
        power_good = 1'b0;
        result_valid = 1'b0;
        tx = '0;
        io_start_allow = 1'b1;
        run_core = 1'b1;
        run_io = 1'b1;
        io_half_ps = new_io_half_ps;
        #30100ps;
        clear_scoreboard();
        power_good = 1'b1;
        core_reset_n = 1'b1;
        io_reset_n = 1'b1;
        wait_online();
    endtask

    // [样本] 具有多种高位和低位组合，样本身份主要靠实际内容和接收顺序。
    // 连续驱动不等满空。模式交错形成帧内配置冻结的反例激励。
    task automatic drive_burst(input int count, input int base, input logic dual, input logic mixed);
        for (int index = 0; index < count; index++) begin
            @(negedge clk_core);
            result_valid = 1'b1;
            tx.word = 20'((base + index) * 32'd65521 ^ 32'h0008f11e);
            tx.dual_lane = mixed ? (index % 3 != 1) : dual;
            tx.intf_check = index % 11 == 3 || index % 11 == 7;
        end
        @(negedge clk_core);
        result_valid = 1'b0;
    endtask

    task automatic wait_drained(input int target);
        // [完成] 排空资格必须包含最后一槽；核心观察延迟给足返回同步时间。
        repeat (100) begin
            @(negedge clk_core);
            if (core_drained && frames == target) begin
                require_ok(expected.size() == 0 && accepted == target, "unexpected acceptance count");
                return;
            end
        end
        $fatal(1, "drain deadline frames=%0d accepted=%0d target=%0d", frames, accepted, target);
    endtask

    task automatic check_flush_outputs;
        // [异步] 此处远少于两侧周期，且用例可停钟，避免顺序复位掩盖漏隔离。
        #10ps;
        require_ok(!core_online && !io_online && !core_accept && !core_drained && !io_valid,
                   "reset or lost power did not close both sides");
        require_ok(!core_full && !core_overflow && !core_drop &&
                   {io_first, io_last, io_dual, io_test, da_fall, db_fall, da_rise, db_rise} == 0,
                   "old beat or fault survived flush");
    endtask

    task automatic check_empty_after_restart;
        // [复位] 出复位并空转多个时钟。若任一旧地址误公开，接收器也会因
        // 无期望字报错；首个新字用不同内容检验旧阵列槽不能混入。
        wait_online();
        repeat (20) begin
            @(negedge clk_io);
            require_ok(!io_valid, "old contents became readable after reset");
        end
        drive_burst(7, 50000, 1'b1, 1'b1);
        wait_drained(7);
    endtask

    // [复现] I/O 停钟使核心本地复位已释放而双域 online 尚未建立。
    // 对处于硬复位和本地同步释放的样本保持静默，对本域已可记账而
    // 尚未上线的样本报告一次丢弃。未占 FIFO 格，满溢出不应粘滞。
    task automatic check_startup_discard;
        new_epoch(1250);
        @(negedge clk_io);
        run_io = 1'b0;
        io_reset_n = 1'b0;
        check_flush_outputs();
        @(negedge clk_core);
        tx.word = 20'h64ad3;
        result_valid = 1'b1;
        @(posedge clk_core);
        #2ps;
        require_ok(!core_accept && !core_drop && !core_overflow, "hard reset charged an input drop");

        @(negedge clk_core);
        io_reset_n = 1'b1;
        repeat (2) begin
            @(posedge clk_core);
            #2ps;
            require_ok(!core_accept && !core_drop && !core_overflow, "local reset release charged an input drop");
        end
        @(negedge clk_core);
        result_valid = 1'b0;
        repeat (4) @(negedge clk_core);
        require_ok(!core_online && !io_online, "IO stop bypassed peer startup");
        test_startup_attempt = 1'b1;
        result_valid = 1'b1;
        @(negedge clk_core);
        result_valid = 1'b0;
        test_startup_attempt = 1'b0;
        @(negedge clk_core);
        require_ok(!core_drop && !core_overflow, "startup drop was sticky or looked like FIFO overflow");

        run_io = 1'b1;
        check_empty_after_restart();
        $display("PASS startup discard visible after local reset release; hard reset and overflow remain distinct");
    endtask

    // [起发] 在空队列上从真实核心受理沿计数。两级同步、领取和输出
    // 采纳是不同的边界；按端口数比根据内部状态推测首发更容易发现偏一。
    task automatic check_empty_start(input time io_period_half, input logic dual);
        time accepted_at;
        new_epoch(io_period_half);
        @(negedge clk_core);
        tx = '{word: 20'h91ab6, dual_lane: dual, intf_check: 1'b0};
        result_valid = 1'b1;
        @(posedge clk_core);
        require_ok(core_accept, "empty link rejected its first word");
        accepted_at = $time;
        #1ps;
        result_valid = 1'b0;
        repeat (3) begin
            @(posedge clk_io);
            require_ok(!io_valid, "empty link exposed the first pair before the fourth IO edge");
        end
        @(posedge clk_io);
        require_ok(io_valid && io_first && !io_last, "empty link missed the fourth-edge first pair");
        $display("PASS empty start %0d lane(s): first pair on IO edge 4, %0d ps after acceptance",
                 dual ? 2 : 1, $time - accepted_at);
        wait_drained(1);
    endtask

    // =========================================================================
    // 4. 定向验收
    // =========================================================================
    initial begin : scenarios
        logic [7:0] paused_pair;
        // [可重放] 备份 RTL 可只运行首次丢样诊断，不依赖速率回归。
        if ($test$plusargs("transport_startup_drop")) begin
            check_startup_discard();
            $finish;
        end

        // [带宽] 接收器要求从首帧开始到第 512 帧末槽无空拍，每笔都须入队。
        new_epoch(1250);
        contiguous_goal = NOMINAL_COUNT;
        drive_burst(NOMINAL_COUNT, 100, 1'b0, 1'b0);
        wait_drained(NOMINAL_COUNT);
        require_ok(attempts == NOMINAL_COUNT && dropped == 0 && !core_overflow, "single-lane throughput");
        $display("PASS single 40/400 MHz: %0d accepted and reassembled without bubbles", frames);

        new_epoch(2500);
        contiguous_goal = NOMINAL_COUNT;
        drive_burst(NOMINAL_COUNT, 6000, 1'b1, 1'b0);
        wait_drained(NOMINAL_COUNT);
        require_ok(attempts == NOMINAL_COUNT && dropped == 0 && !core_overflow, "dual-lane throughput");
        $display("PASS dual 40/200 MHz: %0d accepted and reassembled without bubbles", frames);

        // [模式] 2.52 ns IO 周期使相位逐笔滑动。在两种帧长之间切换，
        // 期望允许帧间空拍，禁止帧中断槽。混合载荷保有足够服务带宽。
        new_epoch(1260);
        drive_burst(160, 15000, 1'b0, 1'b1);
        wait_drained(160);
        require_ok(attempts == 160 && dropped == 0, "mixed-mode loss");
        $display("PASS 160 mixed lane/test frames with drifting CDC phase");

        // [装载许可] 先排队两帧，再只让首帧启动。撤许可发生在首帧中间，
        // 已发帧须继续到末槽，候选下一帧保持等待，不能随末槽越过准入。
        new_epoch(1250);
        io_start_allow = 1'b0;
        drive_burst(2, 20000, 1'b0, 1'b0);
        require_ok(!io_valid && !core_drained, "queued frames escaped start permission");
        @(negedge clk_io);
        io_start_allow = 1'b1;
        @(negedge clk_io);
        require_ok(io_first, "pending frame was not available on launch");
        io_start_allow = 1'b0;
        repeat (14) @(negedge clk_io);
        require_ok(frames == 1 && !io_valid && !core_drained, "launch gate interrupted or admitted wrong frame");
        io_start_allow = 1'b1;
        wait_drained(2);
        $display("PASS start permission blocks new frames and never stalls an active frame");

        // [满] 保留一帧在 IO 的发射寄存器，等待领取指针回核心。停钟后
        // 再送深度加四笔，恰好写满八格，保护在发帧和已入的最老八字。
        new_epoch(1250);
        drive_burst(1, 21000, 1'b0, 1'b0);
        @(negedge clk_io);
        require_ok(io_valid && !io_last, "no active frame for clock-stop test");
        paused_pair = {io_first, io_last, io_dual, io_test, da_fall, db_fall, da_rise, db_rise};
        run_io = 1'b0;
        repeat (3) @(negedge clk_core);
        drive_burst(FIFO_DEPTH + 4, 22000, 1'b0, 1'b1);
        require_ok(accepted == FIFO_DEPTH + 1 && dropped == 4 && attempts == FIFO_DEPTH + 5 &&
                   core_full && core_overflow && !core_drained, "overflow at wrong capacity");
        require_ok(io_valid && paused_pair == {io_first, io_last, io_dual, io_test,
                                                da_fall, db_fall, da_rise, db_rise}, "stopped IO changed slots");
        run_io = 1'b1;
        wait_drained(FIFO_DEPTH + 1);
        drive_burst(6, 23000, 1'b0, 1'b1);
        wait_drained(FIFO_DEPTH + 7);
        require_ok(core_overflow && !core_full, "overflow not sticky or capacity failed to recover");
        $display("PASS stopped IO, one active + %0d queued, %0d visible drops, ordered resume", FIFO_DEPTH, dropped);

        // [单侧复位] IO 停钟且有排队或部分发出的旧字时，单拉 IO reset。
        // 释放后它仍未运行，核心不能先写进一个对侧没完成初始化的新会话。
        new_epoch(1250);
        drive_burst(3, 30000, 1'b0, 1'b0);
        @(negedge clk_io);
        require_ok(io_valid && expected.size() > 0, "missing old frame for IO reset test");
        run_io = 1'b0;
        io_reset_n = 1'b0;
        check_flush_outputs();
        clear_scoreboard();
        io_reset_n = 1'b1;
        repeat (6) @(negedge clk_core);
        require_ok(!core_online && !io_online && !io_valid, "IO stop did not hold restart");
        run_io = 1'b1;
        check_empty_after_restart();
        $display("PASS IO-only reset while IO stopped, old frames flushed on both sides");

        // [反向] 核心停钟时断其复位，IO 仍有活动时钟，也必须立即关闭及等待。
        new_epoch(1250);
        drive_burst(3, 31000, 1'b0, 1'b0);
        require_ok(expected.size() > 0 && io_valid, "missing old frame for core reset test");
        run_core = 1'b0;
        core_reset_n = 1'b0;
        check_flush_outputs();
        clear_scoreboard();
        core_reset_n = 1'b1;
        repeat (20) @(negedge clk_io);
        require_ok(!core_online && !io_online && !io_valid, "core stop did not hold restart");
        run_core = 1'b1;
        check_empty_after_restart();
        $display("PASS core-only reset while core stopped, old frames flushed on both sides");

        // [掉电] 双时钟均冻结时撤销统一资格，先恢复 IO 再恢复核心。
        // 旧在发帧不允许跨供电会话，即使先从 PHY 时钟侧尝试重新启动。
        new_epoch(1250);
        drive_burst(3, 32000, 1'b0, 1'b0);
        run_core = 1'b0;
        @(negedge clk_io);
        require_ok(io_valid && expected.size() > 0, "missing old frame for power test");
        run_io = 1'b0;
        power_good = 1'b0;
        check_flush_outputs();
        clear_scoreboard();
        power_good = 1'b1;
        run_io = 1'b1;
        repeat (20) @(negedge clk_io);
        require_ok(!core_online && !io_online && !io_valid, "power recovery bypassed peer release");
        run_core = 1'b1;
        check_empty_after_restart();
        $display("PASS shared power loss with both clocks stopped; restart has no prior-epoch data");
        check_startup_discard();

        check_empty_start(1250, 1'b0);
        check_empty_start(2500, 1'b1);

        // [时钟比] 单 lane 的 200 MHz 服务能力只有核心产量的一半。
        // 两边都运行时也要保序，检查满取消后的恢复和丢弃结算。
        new_epoch(2500);
        drive_burst(80, 56000, 1'b0, 1'b0);
        wait_drained(accepted);
        require_ok(attempts == 80 && accepted + dropped == attempts && dropped > 0 &&
                   core_overflow && !core_full, "active IO oversubscription was not fully accounted");
        $display("PASS 40/200 MHz single-lane overload: %0d accepted, %0d dropped and recovered in order",
                 accepted, dropped);

        $display("AD4080 RESULT TRANSPORT REGRESSION PASS");
        $finish;
    end

    // [超时] 覆盖任何因停止时钟或无输出而卡住的外层等待，不挂住回归进程。
    initial begin
        #200us;
        $fatal(1, "transport testbench watchdog expired");
    end
endmodule

`default_nettype wire
