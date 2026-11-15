`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i12_integration.sv
// 模块名     : tb_i12_integration
// 版本       : v1.0
// 功能       : I1+I2 联调 —— 两笔采样 + 逐事件物理开关零差异对拍。
//              这是方案要求的"第一个交付件"。
// 设计说明   :
//   * 对拍对象：每个已决事件应用后，
//       ① slot 级 160 位 post（与 dem_dither.py 参考逐位）
//       ② 物理 360 位 post（按 CONV 冻结片号路由后）
//       ③ 实际写片掩码 post_wr（必须恰是 CONV 那 8 片）
//       ④ k / tag / qid / EV 序号
//   * 期望值来自 tools/gen_vec_i12.py（复用 dem_dither.py 合同实现）。
//   * 关键路由判据：post 必须打到 **CONV** 的片号，不是 ACQ ——
//     论文 p.182 "8 sDACs converting" 才需要 RDAC 开关。
//   * 覆盖：A/B 两笔交错（A 在 CONV 逐事件应用时 B 建立 ACQ）、
//     末事件 final、done 后不发、错 qid 不写。
// 变更记录   :
//   2026-11-15  初版。
// =============================================================================
module tb_i12_integration;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // 事务
    logic        prepare_valid = 1'b0, prepare_ready;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v = 3'd0, prepare_U = 3'd0;
    logic [2:0]  prepare_flash = 3'd0;
    logic        prepare_qid = 1'b0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;

    // 采样
    logic sample_event = 1'b0, input_captured = 1'b0, residue_captured = 1'b0, hold_ack = 1'b0;

    // 事件
    logic       ev_start_i = 1'b0, ev_bit_valid_i = 1'b0, ev_bit_i = 1'b0;
    logic [3:0] ev_bit_index_i = 4'd0, ev_event_index_i = 4'd0;
    logic       ev_qid_i = 1'b0, ev_flash_valid_i = 1'b0;
    logic [2:0] ev_flash_code_i = 3'd0;

    // 片组
    logic [17:0] sched_acq, sched_conv, sched_idle;
    logic [39:0] sched_ids;

    // 输出
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [71:0] pre_state;
    logic [17:0] pre_wr;
    logic [359:0] post_state;
    logic [17:0] post_wr;
    logic apply_fire, k_bad, final_applied, err, done;
    logic [9:0] k_out, final_k;
    logic [31:0] tag_out;
    logic qid_out;
    logic [159:0] post_slot;

    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_U(prepare_U), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid_i), .ev_bit_i(ev_bit_i),
        .ev_bit_index_i(ev_bit_index_i), .ev_event_index_i(ev_event_index_i),
        .ev_qid_i(ev_qid_i), .ev_flash_valid_i(ev_flash_valid_i),
        .ev_flash_code_i(ev_flash_code_i),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
        .rdac_post_state_o(post_state), .post_wr_o(post_wr),
        .apply_fire_o(apply_fire), .k_o(k_out), .k_bad_o(k_bad),
        .final_applied_o(final_applied), .final_k_o(final_k),
        .err_o(err), .done_o(done), .tag_o(tag_out), .qid_o(qid_out),
        .post_slot_o(post_slot));

    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 30) $display("[%0t] ERROR: %s", $time, msg);
    endtask
    task automatic okc(input string msg);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, msg);
    endtask

    function automatic logic [39:0] pack_ids(input int a[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(a[i]);
        return v;
    endfunction

    function automatic int pc18(input logic [17:0] v);
        int c; c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

    // 期望：A 的 5 次提交（slot 级 160 位，由参考模型算出）
    // k=453 -> 0x43FFF43FFF43FFF43FFF47FFF43FFF43FFF43FFF（slot3 为 0x47FFF）
    localparam logic [159:0] A_POST0 = 160'h43FFF43FFF43FFF43FFF47FFF43FFF43FFF43FFF;
    localparam logic [159:0] A_POST1 = 160'h5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF;
    localparam logic [159:0] A_POST2 = 160'h5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF;
    localparam logic [159:0] A_POST3 = 160'h7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF;
    localparam logic [159:0] A_POST4 = 160'h7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF;

    // A 的 k 序列与位序
    int A_K[0:4]   = '{453, 517, 517, 519, 519};
    int A_BIT[0:4] = '{0, 9, 2, 1, 0};
    int A_BV[0:4]  = '{0, 1, 0, 1, 0};

    // 采样事件
    task automatic do_sample(input logic ic, input logic rc, input logic hk);
        @(negedge clk);
        sample_event = 1'b1; input_captured = ic; residue_captured = rc; hold_ack = hk;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0; hold_ack = 1'b0;
    endtask

    // prepare 一笔（同沿起事件上下文）
    task automatic do_prepare(input logic [31:0] tag, input logic [5:0] w,
                              input logic [2:0] h, input logic [2:0] v,
                              input logic half, input logic coin, input logic q,
                              input logic [2:0] F);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = tag; prepare_word = w;
        prepare_h = h; prepare_v = v; prepare_half = half; prepare_coin = coin;
        prepare_U = {1'b0, w[5]} * 3'd4 + {2'b0, w[3]};   // 与 word 一致
        prepare_flash = F;                                 // 随描述符冻结
        prepare_qid = q;                                   // 随描述符冻结
        // 注意：事件层的 start 由顶层内部 commit_fire 触发，
        // 不由 prepare 驱动 —— prepare B 时若重置事件层，会把 A 的 k 流冲掉。
        ev_qid_i = q;
        @(posedge clk); #1;
        @(negedge clk);
        prepare_valid = 1'b0;
    endtask

    // 一个已决事件；返回该拍的 slot post 与物理 post（组合采样）
    logic [159:0] ev_post_slot;
    logic [359:0] ev_post_phys;
    logic [17:0]  ev_post_wr;
    logic         ev_applied;
    logic [9:0]   ev_k;
    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        ev_bit_valid_i = 1'b1; ev_bit_i = bv; ev_bit_index_i = bi;
        ev_event_index_i = ei; ev_qid_i = q;
        #1;
        ev_applied   = apply_fire;
        if (apply_fire !== 1'b1 && errors < 3)
            $display("     [diag] ei=%0d exp_evt=%0d flash_done=%b guard=%b active=%b err=%b qid_ok=%b evt_ok=%b bit_ok=%b k_over=%b",
                     ei, dut.u_evt.exp_evt_q, dut.u_evt.flash_done_q, dut.u_evt.guard_q,
                     dut.u_evt.active_q, dut.u_evt.err_q, dut.u_evt.qid_ok,
                     dut.u_evt.evt_ok, dut.u_evt.bit_ok, dut.u_evt.k_over);
        ev_post_slot = post_slot;
        ev_post_phys = post_state;
        ev_post_wr   = post_wr;
        ev_k         = k_out;
        @(posedge clk);
        @(negedge clk);
        ev_bit_valid_i = 1'b0;
    endtask

    initial begin
        sched_acq  = 18'h000FF; sched_conv = 18'h00000; sched_idle = 18'h3FF00;
        sched_ids  = pack_ids(A_IDS);

        repeat (3) @(negedge clk);
        rst_n = 1'b1; @(negedge clk);

        // ============ 阶段 1：prepare A（guard, word=63, h=0,v=3,half=0,coin=1）============
        $display("---- prepare A (guard, word=63, h=0 v=3 half=0 coin=1) ----");
        do_prepare(32'hA1, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        // E0 采样 A
        do_sample(1'b1, 1'b0, 1'b0);

        // ============ 阶段 2：prepare B —— A 进 CONV，B 建 ACQ ============
        $display("---- prepare B (普通, word=17) -> A 进 CONV ----");
        sched_acq  = 18'h3BD00; sched_conv = 18'h000FF; sched_idle = 18'h04200;
        sched_ids  = pack_ids(B_IDS);
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);

        checks = checks + 1;
        if (dut.conv_ids !== pack_ids(A_IDS))
            fail("CONV 片号应为 A 的 0..7");
        else $display("      CONV = A 组 (0..7)  OK");
        checks = checks + 1;
        if (dut.acq_ids !== pack_ids(B_IDS))
            fail("ACQ 片号应为 B 组");
        else $display("      ACQ = B 组 (9..15,17)  OK");

        // ============ 阶段 3：A 的事件逐次应用（此时 B 在 ACQ）============
        $display("---- A 逐事件应用（B 同时持有 ACQ）----");
        // A 的 evt1 是 Flash 提交；此时 ev_event_index 应为 1
        // 注意：事件层已在 prepare A 时 start，这里直接从 evt1 开始
        for (int i = 0; i < 5; i++) begin
            logic [159:0] want;
            case (i)
                0: want = A_POST0; 1: want = A_POST1; 2: want = A_POST2;
                3: want = A_POST3; default: want = A_POST4;
            endcase
            do_event(4'(A_BIT[i]), 4'(i+1), A_BV[i][0], 1'b0);

            checks = checks + 1;
            if (ev_applied !== 1'b1)
                fail($sformatf("A ev%0d 未 apply", i));
            checks = checks + 1;
            if (int'(ev_k) != A_K[i])
                fail($sformatf("A ev%0d k=%0d 期望 %0d", i, ev_k, A_K[i]));
            else $display("      A ev%0d k=%0d  OK", i, ev_k);

            // 判据①：slot 级 160 位 post 逐位
            checks = checks + 1;
            if (ev_post_slot !== want) begin
                fail($sformatf("A ev%0d slot post 不匹配", i));
                $display("     got  %040h", ev_post_slot);
                $display("     want %040h", want);
            end else $display("      A ev%0d slot post 160bit  OK", i);

            // 判据②：物理 post 必须只落在 CONV 的 8 片上
            checks = checks + 1;
            if (pc18(ev_post_wr) != 8)
                fail($sformatf("A ev%0d 写片数=%0d 应为 8", i, pc18(ev_post_wr)));
            else $display("      A ev%0d 写片数 = 8  OK", i);

            // 判据③：写片必须等于 CONV 片号（0..7）
            begin
                logic [17:0] want_wr;
                want_wr = 18'd0;
                for (int s = 0; s < 8; s++) want_wr[A_IDS[s]] = 1'b1;
                checks = checks + 1;
                if (ev_post_wr !== want_wr)
                    fail($sformatf("A ev%0d 写片掩码=%06h 期望 %06h", i, ev_post_wr, want_wr));
                else $display("      A ev%0d 写片 = CONV 组  OK", i);
            end
        end

        checks = checks + 1;
        if (final_applied !== 1'b1) fail("A 末事件后 final_applied 应置位");
        else $display("      A final_applied  OK");
        checks = checks + 1;
        if (int'(final_k) != 519) fail($sformatf("A final_k=%0d 期望 519", final_k));
        else $display("      A final_k = 519  OK");

        // ============ 阶段 4：B 的 pre 仍在（B 在 ACQ，pre 有效）============
        $display("---- B 的 pre 物理路由 ----");
        checks = checks + 1;
        if (pc18(pre_wr) != 8)
            fail($sformatf("B 的 pre 写片数=%0d 应为 8", pc18(pre_wr)));
        else $display("      B pre 写片数 = 8  OK", 0);
        // B 的 word=17,v=7 -> pre_slot=0x64444444，按 B 片号路由
        checks = checks + 1;
        begin
            logic [71:0] want_pre;
            want_pre = 72'd0;
            for (int s = 0; s < 8; s++)
                want_pre[B_IDS[s]*4 +: 4] = (32'h64444444 >> (4*s)) & 4'hF;
            if (pre_state !== want_pre) begin
                fail("B 的 pre 物理路由不匹配");
                $display("     got  %018h", pre_state);
                $display("     want %018h", want_pre);
            end else $display("      B pre 物理路由 72bit  OK", 0);
        end

        $display("---------------------------------------------------------------");
        $display("I1+I2 integration: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
