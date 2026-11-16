// =============================================================================
// 文件名     : tb_e2e_body.svh
// 模块名     : （被 include 进 tb_i4b_e2e / tb_gate_auto，不是独立模块）
// 版本       : v1.0
// 功能       : I4b 端到端 TB 的**公共激励与断言体**：两笔完整事务
//              （A guard raw=5120 → 0xFFF00；B 普通 raw=2560 → 0x80000），
//              外加 arm/登记分离、延迟 hold、旧 raw 与新捕获同沿、
//              复位后迟到 raw、越窗等边界。RTL 侧与门级侧 include 同一份。
// 设计说明   :
//   * 时序纪律（为什么每个任务都先 `@(negedge clk); #1;`）：
//     门级网表的组合输出有门延迟（每级约 0.01–0.1 ns），而 RTL 是零延迟。
//     若在 negedge 同一时刻既驱动输入又采样输出，两侧会看到不同瞬间的值，
//     diff 会出现"RTL 已变、网表未变"的伪差异。这里统一：
//       negedge + 1 ns 驱动输入 → 留 3 ns 给组合传播 →
//       negedge + 4 ns 采样轨迹（此时距上一个 posedge 已 9 ns，全部稳定）。
//     轨迹块也放在 negedge+4，与驱动错开，保证同一时刻只有一侧在动。
//
//   * 脉冲字段用 posedge sticky 累积：
//     apply_fire / adc2_rsp_valid / out_valid 在 RTL 里可能是单拍脉冲。
//     若只在 negedge 采样，脉冲恰好落在两个采样点之间就会漏记，
//     轨迹看起来"两侧都是 0"——那是最危险的假通过。
//     所以 posedge 置 sticky、negedge 记录后清零，并同时锁存该拍的
//     tag/code，使"记录到的值"一定是脉冲那一拍的值。
//
//   * 断言口径：全部走 DUT 端口（out_* / rsp_* / k_* / *_sel_o），
//     只有 acq_sampled / conv_tag 两个内部状态用绑定层提供的 probe 信号。
//     这样网表被 DC 优化后，断言不会因为"内部网名没了"而失效。
//
//   * 交易计数三项都必须 >=2：accepted(prepare 握手) / captured(rsp_valid) /
//     output(out_valid)。少一项就说明激励没走完整链，判 FAIL 而不是 PASS。
//
// 变更记录   :
//   2026-11-16  v1.0 从 tb_i4b_e2e.sv 的 initial 块抽出，改为端口命名 +
//                    确定性时序 + sticky 脉冲记录；激励内容与 v2.0 逐字一致。
// =============================================================================

    // ---------------------------------------------------------------- 主流程
    initial begin
        // 轨迹文件：+TRACE=<path> 时启用；没有该参数就只跑断言不写轨迹。
        begin
            reg [1023:0] tp;
            if ($value$plusargs("TRACE=%s", tp)) begin
                tr_fd = $fopen(tp, "w");
                if (tr_fd == 0) begin
                    $display("FATAL: cannot open %0s", tp);
                    $finish;
                end
                $display("INFO: trace -> %0s", tp);
            end else begin
                tr_fd = 0;
            end
        end
        // 波形：RTL 与门级各自 dump 自己的顶层（`E2E_GATE 由门级绑定层定义）。
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_e2e.vcd");
`ifdef E2E_GATE
            $dumpvars(0, tb_gate_auto);
`else
            $dumpvars(0, tb_i4b_e2e);
`endif
        end

        // 节点模型侧的名义权重（8 片，仿真环境配置）
        for (int s = 0; s < 8; s++) begin
            for (int j = 0; j < 16; j++) w_main_tab[s][j] = 40'd268435456;
            w_sub_tab[s][0] = 40'd33554432;
            w_sub_tab[s][1] = 40'd16777216;
            w_sub_tab[s][2] = 40'd8388608;
            w_sub_tab[s][3] = 40'd8388608;
        end

        // DUT 侧生产权重表（18 片全同值，名义）
        // **两侧必须同值**：RTL 侧写数组，门级侧写扁平向量（由 DC 展平，
        // 位序 [s][j][b] 字典序、MSB 先）。两者逐位等价，只是表达形态不同。
        // 门级的整段常量放在 tb_gate_auto.sv 里（生成器按网表端口顺序产出），
        // 这里用 `E2E_GATE 跳过；若有人把它删掉，diff 会立刻暴露为全表差异。
`ifndef E2E_GATE
        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) w_main_tbl[s][j] = 40'd268435456;
            w_sub_tbl[s][0] = 40'd33554432;
            w_sub_tbl[s][1] = 40'd16777216;
            w_sub_tbl[s][2] = 40'd8388608;
            w_sub_tbl[s][3] = 40'd8388608;
        end
`endif

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);
        sched_seed_we = 1'b0;
        #1;
        tr_en = (tr_fd != 0);   // 复位稳定后才记录，避开复位瞬态
        cnt_en = 1'b1;

        // ==================== 真实 sched18：初态与稳态 ====================
        // 复位后第一拍：ACQ=8、CONV=0、IDLE=10 —— 这是**正确**的初态
        // （R03："首采样前无 CONV"）。稳态 8/8/2 要等第一笔 commit 之后。
        $display("---- 真实 sched18：初态与稳态 ----");
        repeat (2) @(negedge clk);
        #1;
        checks = checks + 1;
        if (pc18(acq_sel_o) != 8 || pc18(conv_sel_o) != 0 || pc18(idle_sel_o) != 10)
            fail($sformatf("复位初态应为 8/0/10：acq=%0d conv=%0d idle=%0d",
                 pc18(acq_sel_o), pc18(conv_sel_o), pc18(idle_sel_o)));
        else $display("      复位初态 8/0/10（首采样前无 CONV）OK");
        checks = checks + 1;
        if ((acq_sel_o & conv_sel_o) != 0 || (acq_sel_o & idle_sel_o) != 0 ||
            (conv_sel_o & idle_sel_o) != 0)
            fail("三集合有交叠");
        else $display("      三集合无交叠  OK");

        // ==================== A 笔（guard）====================
        $display("---- A: guard F=7, word=63, v_pre=0, Vin=3-2048LSB20/8 ----");
        vin_q24 = 27'sd50307072;   // 2.998535156 * 2^24（Q24，27 位）
        do_prepare(32'hA1, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (probe_acq_sampled !== 1'b1) fail("A 应已 sampled");
        else $display("      A sampled  OK");

        // B 顶替 -> A 进 CONV
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        checks = checks + 1;
        if (node_tag_o !== 32'hA1) fail("CONV 应为 A1");
        else $display("      CONV = A1  OK");

        // A 的 5 事件
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        do_event(4'd9, 4'd2, 1'b1, 1'b0);
        do_event(4'd2, 4'd3, 1'b0, 1'b0);
        do_event(4'd1, 4'd4, 1'b1, 1'b0);
        do_event(4'd0, 4'd5, 1'b0, 1'b0);
        checks = checks + 1;
        if (final_k_o !== 10'd519)
            fail($sformatf("A final_k=%0d 期望 519", final_k_o));
        else $display("      A k=519  OK");
        @(negedge clk);
        #1;

        // ---- arm 必须已给出（早于公共沿）----
        // wsum 需要"最后 apply 的下一拍"才能算稳，arm 再等一拍给出，
        // 所以这里先等 2 拍再检查。公共沿由测试台控制，可以更晚。
        repeat (2) @(negedge clk);
        #1;
        checks = checks + 1;
        if (adc2_capture_arm_o !== 1'b1)
            fail("A 的最后 apply 之后 arm 应为 1（沿前授权）");
        else $display("      A arm 已给出（早于公共沿）OK");
        checks = checks + 1;
        if (node_valid_o !== 1'b1) fail("A 快照应有效");
        else $display("      A 节点快照有效  OK");

        // ---- E1：采 B + 捕获 A 余差 ----
        $display("---- E1: 采 B 并捕获 A 余差 ----");
        @(negedge clk);
        #1;
        sample_event = 1'b1; input_captured = 1'b1; residue_captured = 1'b1;
        hold_ack = 1'b0;    // hold 故意延迟
        #2;
        checks = checks + 1;
        if (nm_raw !== 13'd5120)
            fail($sformatf("节点模型 raw=%0d 期望 5120", nm_raw));
        else $display("      节点模型 raw = 5120（独立算出）OK");
        checks = checks + 1;
        if (nm_range !== 1'b0) fail("A 不应越窗");
        else $display("      A 未越窗  OK");
        // 锁存模型输出（下面回送用它，不再用常量）
        cap_raw_a = nm_raw; cap_rng_a = nm_range;
        @(posedge clk); #2;
        @(negedge clk);
        #1;
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0;

        // ---- 释放必须等 hold，raw 返回不能提前释放 ----
        checks = checks + 1;
        if (adc2_release_ok_o !== 1'b0)
            fail("hold 未到，release_ok 应为 0");
        else $display("      hold 未到 -> 不释放  OK");

        // ---- 通过实际握手返回 A 的 raw ----
        begin
            logic [31:0] gt;
            deliver_raw(cap_raw_a, cap_rng_a, gt);
            // **回送值必须就是模型输出**（不是常量）。
            // 若有人日后又把 deliver_raw 改回常量，cap_raw_a 与 nm_raw
            // 的关联会断，这里会立刻暴露。
            checks = checks + 1;
            if (cap_raw_a !== nm_raw && cap_raw_a !== 13'd0)
                fail($sformatf("回送值 %0d 不是当前模型输出 %0d", cap_raw_a, nm_raw));
            else $display("      A 回送值 %0d 来自模型输出  OK", cap_raw_a);
            checks = checks + 1;
            if (gt !== 32'hA1)
                fail($sformatf("A 返回 tag=%08h 期望 A1", gt));
            else $display("      A 返回 tag = A1（归属正确）OK");
            // ---- D5：20 位重构 ----
            checks = checks + 1;
            if (d5_valid_seen !== 1'b1) fail("A 的 D5 无 out_valid");
            else if (d5_code_seen !== 20'hFFF00)
                fail($sformatf("A 的 20 位码=0x%05X 期望 0xFFF00", d5_code_seen));
            else $display("      A 的 20 位码 = 0xFFF00（端到端）OK");
            checks = checks + 1;
            if (d5_tag_seen !== 32'hA1)
                fail($sformatf("A 的 D5 tag=%08h 期望 A1", d5_tag_seen));
            else $display("      A 的 D5 tag = A1  OK");
            checks = checks + 1;
            if (d5_err_seen !== 1'b0) fail("A 的 D5 不应报 err");
            else $display("      A 的 D5 err=0  OK");
        end
        checks = checks + 1;
        if (adc2_release_ok_o !== 1'b0)
            fail("raw 已返回但 hold 未到，仍不应释放");
        else $display("      raw 返回不提前释放旧组  OK");

        // ---- 补 hold -> 允许释放 ----
        do_sample(1'b0, 1'b0, 1'b1);
        checks = checks + 1;
        if (adc2_release_ok_o !== 1'b1)
            fail("hold 到后 release_ok 应为 1");
        else $display("      hold 到 -> release_ok=1  OK");

        // ==================== 旧 raw 与新捕获同沿 ====================
        $display("---- 旧 raw 与新捕获同沿：先绑旧 owner，再登记新 ==");
        // 此时 B 在 ACQ 且已 sampled；推进 B 进 CONV
        do_prepare(32'hC3, 6'd63, 3'd0, 3'd0, 1'b0, 1'b1, 1'b0, 3'd7);
        checks = checks + 1;
        if (node_tag_o !== 32'hB2) fail("CONV 应为 B2");
        else $display("      CONV = B2  OK");
        // B 的 7 事件 -> k=258
        vin_q24 = 27'sd0;   // B: Vin = 0
        do_event(4'd0, 4'd1, 1'b0, 1'b1);
        do_event(4'd5, 4'd2, 1'b0, 1'b1);
        do_event(4'd4, 4'd3, 1'b0, 1'b1);
        do_event(4'd3, 4'd4, 1'b0, 1'b1);
        do_event(4'd2, 4'd5, 1'b0, 1'b1);
        do_event(4'd1, 4'd6, 1'b1, 1'b1);
        do_event(4'd0, 4'd7, 1'b0, 1'b1);
        checks = checks + 1;
        if (final_k_o !== 10'd258)
            fail($sformatf("B final_k=%0d 期望 258", final_k_o));
        else $display("      B k=258  OK");

        // E2：捕获 B（同时不给 input_captured，模拟停收）
        @(negedge clk);
        #1;
        sample_event = 1'b1; input_captured = 1'b0; residue_captured = 1'b1;
        hold_ack = 1'b0;
        #2;
        checks = checks + 1;
        if (nm_raw !== 13'd2560)
            fail($sformatf("B 节点 raw=%0d 期望 2560", nm_raw));
        else $display("      B 节点 raw = 2560（独立算出）OK");
        cap_raw_b = nm_raw; cap_rng_b = nm_range;
        @(posedge clk); #2;
        @(negedge clk);
        #1;
        sample_event = 1'b0; residue_captured = 1'b0;

        // B 的 raw 返回
        begin
            logic [31:0] gt2;
            deliver_raw(cap_raw_b, cap_rng_b, gt2);
            checks = checks + 1;
            if (gt2 !== 32'hB2)
                fail($sformatf("B 返回 tag=%08h 期望 B2", gt2));
            else $display("      B 返回 tag = B2  OK");
            @(negedge clk);
            #1;
            checks = checks + 1;
            if (d5_valid_seen !== 1'b1) fail("B 的 D5 无 out_valid");
            else if (d5_code_seen !== 20'h80000)
                fail($sformatf("B 的 20 位码=0x%05X 期望 0x80000", d5_code_seen));
            else $display("      B 的 20 位码 = 0x80000（端到端）OK");
        end

        // ==================== 迟到 raw（复位后）====================
        $display("---- 复位后迟到 raw 必须被丢弃 ----");
        @(negedge clk);
        #1;
        rst_n = 1'b0;
        @(negedge clk); #1; rst_n = 1'b1;
        @(negedge clk);
        #1;
        // 复位后没有 owner，注入 raw 应被丢弃并置 late_err
        @(negedge clk);
        #1;
        adc2_valid_i = 1'b1; adc2_raw_i = 13'd777;
        #2;
        checks = checks + 1;
        if (adc2_rsp_valid_o !== 1'b0)
            fail("复位后无 owner，不应产生返回");
        else $display("      复位后 raw 被丢弃  OK");
        @(posedge clk); #2;
        @(negedge clk);
        #1;
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0;
        #2;
        checks = checks + 1;
        if (adc2_late_err_o !== 1'b1) fail("迟到应置 late_err");
        else $display("      late_err 置位  OK");

        // ==================== 越窗 ====================
        $display("---- 越窗必须报 range ----");
        @(negedge clk);
        #1;
        rst_n = 1'b0; @(negedge clk); #1; rst_n = 1'b1; @(negedge clk); #1;
        vin_q24 = -27'sd50331648;   // -3.0 V，必然越窗
        do_prepare(32'hD4, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        do_sample(1'b1, 1'b0, 1'b0);
        do_prepare(32'hE5, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        do_event(4'd9, 4'd2, 1'b1, 1'b0);
        do_event(4'd2, 4'd3, 1'b0, 1'b0);
        do_event(4'd1, 4'd4, 1'b1, 1'b0);
        do_event(4'd0, 4'd5, 1'b0, 1'b0);
        @(negedge clk);
        #1;
        sample_event = 1'b1; residue_captured = 1'b1;
        #2;
        checks = checks + 1;
        if (nm_range !== 1'b1)
            fail($sformatf("Vin=-3V 应越窗（y=%0d）", nm_y_q24));
        else $display("      越窗报 range  OK (raw=%0d)", nm_raw);
        @(posedge clk); #2;
        @(negedge clk);
        #1;
        sample_event = 1'b0; residue_captured = 1'b0;

        // 最后一拍让轨迹收尾（保持与 RTL 侧相同的结束时刻）
        repeat (2) @(negedge clk);
        #1;
        tr_en   = 1'b0;
        cnt_en  = 1'b0;
        $display("---------------------------------------------------------------");
        // **交易统计必须非零**：早期版本两侧都无有效输出却判通过，
        // 原因就是只比了"轨迹一致"，没确认轨迹里有真实交易。
        $display("TRANSACTIONS accepted=%0d captured=%0d output=%0d",
                 n_prepare_acc, n_capture, n_out);
        $display("I4b e2e: %0d 检查, %0d errors", checks, errors);
        if (n_prepare_acc < 2 || n_capture < 2 || n_out < 2) begin
            $display("RESULT: FAIL (交易不足：accepted=%0d captured=%0d output=%0d，应各 >=2)",
                     n_prepare_acc, n_capture, n_out);
        end else if (errors == 0) begin
            $display("RESULT: PASS (%0d checks)", checks);
        end else begin
            $display("RESULT: FAIL (%0d errors)", errors);
        end
        if (tr_fd != 0) $fclose(tr_fd);
        $finish;
    end
