`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : cnv_ctrl_contract_tb.sv
// 模块名     : cnv_ctrl_contract_tb
// 版本       : v1.0
// 功能       : 验证译码电平兼容入口和核心 tick 的数字最短间隔合同。
// 设计说明   :
//   * 160 MHz 恰以四拍覆盖 25 ns，100 MHz 必须把 2.5 拍上取整成三拍。
//     40 MHz 的一拍边界用于验证相邻输入合法并覆盖计数位宽退化。
//   * 过快序列的 busy 恒为零。间隔判定依赖观察到的所有 tick，包括上一笔
//     已报错的事件。另置忙验证旧错误来源仍独立。
//   * 核心停钟期间没有时间戳更新，异步复位后首 tick 开始新历史。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 保留既有电平模式回归，增加三个时钟点和暂停、复位的数字边界验证。
// =============================================================================
module cnv_ctrl_contract_tb;

    // =========================================================================
    // 1. 既有已译码电平与 busy 合同
    // =========================================================================
    // --- 原接口，只在稳定电平高期间产生一个上升沿事件 ---
    logic legacy_clk = 1'b0;
    logic legacy_rst_n = 1'b0;
    logic legacy_level = 1'b0;
    logic legacy_busy = 1'b0;
    logic legacy_pulse, legacy_error;
    logic observe_legacy = 1'b0;
    logic legacy_done = 1'b0;
    logic [2:0] timing_done;
    int pulses = 0, errors = 0, checks = 0;
    bit previous_pulse = 1'b0;

    initial forever #5ns legacy_clk = !legacy_clk;

    cnv_ctrl u_legacy (
        .clk(legacy_clk), .rst_n(legacy_rst_n), .cnv_p(legacy_level), .busy(legacy_busy),
        .cnv_pulse(legacy_pulse), .cnv_err(legacy_error), .cnv_tick_i(1'b0)
    );

    task automatic legacy_advance(input int cycles);
        repeat (cycles) begin
            @(negedge legacy_clk);
            #1ns;
        end
    endtask

    task automatic require_legacy(input bit condition, input string reason);
        checks++;
        if (!condition) $fatal(1, "Legacy CNV: %s", reason);
    endtask

    // [兼容] 本入口仍期待稳定高电平只产生一次观察事件，对忙写记录两个错误。
    initial forever begin
        @(posedge legacy_clk);
        #1ns;
        if (observe_legacy) begin
            require_legacy(!(previous_pulse && legacy_pulse), "wide decoded event");
            if (legacy_pulse) pulses++;
            if (legacy_error) errors++;
            previous_pulse = legacy_pulse;
        end else begin
            previous_pulse = 1'b0;
        end
    end

    initial begin
        legacy_advance(3);
        legacy_rst_n = 1'b1;
        observe_legacy = 1'b1;
        legacy_advance(6);
        require_legacy(pulses == 0 && errors == 0, "idle release produced an event");
        for (int index = 0; index < 5; index++) begin
            legacy_busy = index >= 3;
            legacy_level = 1'b1;
            legacy_advance(5);
            legacy_level = 1'b0;
            legacy_advance(5);
        end
        require_legacy(pulses == 5 && errors == 2, "decoded event/busy counts");
        legacy_rst_n = 1'b0;
        observe_legacy = 1'b0;
        legacy_advance(3);
        legacy_rst_n = 1'b1;
        observe_legacy = 1'b1;
        legacy_busy = 1'b0;
        legacy_advance(5);
        require_legacy(pulses == 5 && errors == 2, "reset release produced an event");
        require_legacy(checks > 40, "vacuous level checks");
        observe_legacy = 1'b0;
        legacy_done = 1'b1;
    end

    // =========================================================================
    // 2. 三档真实仿真时钟覆盖精确、取整以及一拍下限
    // =========================================================================
    for (genvar timing_case = 0; timing_case < 3; timing_case++) begin : g_fixture
        localparam int unsigned CORE_HZ = timing_case == 0 ? 160_000_000 :
                                         timing_case == 1 ? 100_000_000 : 40_000_000;
        localparam time HALF_PERIOD_PS = timing_case == 0 ? 3125 :
                                        timing_case == 1 ? 5000 : 12500;  // 本测试台时间单位为 ps
        localparam int EXPECTED_INTERVAL = timing_case == 0 ? 4 : timing_case == 1 ? 3 : 1;

        // --- 同域每沿 tick 以及可暂停的物理试验时钟 ---
        logic core_clk = 1'b0;
        logic clock_running = 1'b1;
        logic reset_n = 1'b0;
        logic tick_in = 1'b0;
        logic busy_in = 1'b0;
        logic pulse_out, error_out;
        logic finished = 1'b0;
        int compared_edges = 0;

        assign timing_done[timing_case] = finished;

        initial forever begin
            #(HALF_PERIOD_PS);
            if (clock_running) core_clk = !core_clk;
        end

        cnv_ctrl #(.P_USE_CNV_TICK(1'b1), .CORE_CLOCK_HZ(CORE_HZ)) u_timing (
            .clk(core_clk), .rst_n(reset_n), .cnv_p(1'b1), .busy(busy_in),
            .cnv_pulse(pulse_out), .cnv_err(error_out), .cnv_tick_i(tick_in)
        );

        // [取样] 负沿建立输入，正沿后比较新注册的错误，首尾两边均无竞争。
        // 逐拍比较保证过快错误恰好对应请求，而非晚一拍追上或粘滞不清。
        task automatic step(input bit event_tick, input bit busy_value,
                            input bit want_error, input string reason);
            @(negedge core_clk);
            tick_in = event_tick;
            busy_in = busy_value;
            #1ps;
            if (pulse_out !== event_tick)
                $fatal(1, "CNV %0d Hz did not preserve the event (%s)", CORE_HZ, reason);
            @(posedge core_clk);
            #1ps;
            if (error_out !== want_error)
                $fatal(1, "CNV %0d Hz error=%b expected=%b (%s)",
                       CORE_HZ, error_out, want_error, reason);
            compared_edges++;
        endtask

        initial begin
            repeat (2) @(negedge core_clk);
            reset_n = 1'b1;
            step(0, 0, 0, "reset idle, legacy pin ignored");
            step(1, 0, 0, "first tick");
            repeat (EXPECTED_INTERVAL - 1) step(0, 0, 0, "before legal boundary");
            step(1, 0, 0, "equal to quantized interval");

            if (EXPECTED_INTERVAL > 1) begin
                repeat (EXPECTED_INTERVAL - 2) step(0, 0, 0, "before short interval");
                // [反例] busy 为零，且从错事件之后紧接一笔。若只记合法 tick，
                // 第二次会离上次合法事件刚好到边界而被错误放过。
                step(1, 0, 1, "one core edge early with busy low");
                step(1, 0, 1, "adjacent to previous erroneous tick");
            end else begin
                step(1, 0, 0, "adjacent ticks at one-period resolution");
            end

            // [饱和] 多于计数位宽能表示的拍数，检查空闲不会重新进入过快窗口。
            repeat (256) step(0, 0, 0, "long idle");
            step(1, 0, 0, "first tick after long idle");
            tick_in = 1'b0;
            clock_running = 1'b0;
            #200ns;
            if (pulse_out || error_out) $fatal(1, "CNV changed diagnostics while stopped");
            clock_running = 1'b1;
            step(1, 0, EXPECTED_INTERVAL > 1, "first core edge after clock pause");

            repeat (EXPECTED_INTERVAL - 1) step(0, 0, 0, "wrap avoided before busy test");
            step(1, 1, 1, "busy collision at legal interval");
            reset_n = 1'b0;
            #1ps;
            if (pulse_out || error_out) $fatal(1, "CNV reset left a prior event visible");
            @(negedge core_clk);
            tick_in = 1'b0;
            busy_in = 1'b0;
            reset_n = 1'b1;
            step(0, 0, 0, "no stale event after release");
            step(1, 0, 0, "first tick in new reset epoch");

            $display("CNV_TCONV_CASE_PASS core_hz=%0d min_cycles=%0d checks=%0d",
                     CORE_HZ, EXPECTED_INTERVAL, compared_edges);
            finished = 1'b1;
        end
    end

    // [结束] 既有结束标志在全部定时实例完成后才出现，防止部分回归被报为通过。
    initial begin
        wait (legacy_done && (&timing_done));
        $display("CNV_CTRL_CONTRACT_COMPLETE input_edges=5 busy_errors=%0d pulses=%0d checks=%0d",
                 errors, pulses, checks);
        $finish;
    end

    initial begin
        #20us;
        $fatal(1, "CNV watchdog");
    end
endmodule

`default_nettype wire
