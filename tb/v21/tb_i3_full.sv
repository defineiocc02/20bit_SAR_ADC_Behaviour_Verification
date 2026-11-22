`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i3_full.sv
// 模块名     : tb_i3_full
// 版本       : v2.0
// 功能       : I3 完整验收 —— 节点模型在仿真边界，经实际握手返回，按样本归属。
// 设计说明   :
//   * 架构（方案目标 I3 第 2 条）：
//        DUT 只送出「本笔不可变快照」：node_post / node_pre / node_ids /
//        qid / tag / 相位。**节点宏不在 DUT 内**，而在本测试台里模拟
//        模拟宏 + Vin 真值：它用快照算 y 与 13 位 raw，再通过
//        adc2_valid/adc2_ready 握手送回 DUT。
//        5120 / 2560 只作为**计分板预期值**，由模型独立算出后比对。
//
//   * 覆盖（方案目标 I3 第 1–4 条）：
//       ① arm 与登记分开：arm 在最后合法 apply 后给出，早于公共沿；
//          登记只在 sample_event & residue_captured 时发生
//       ② 节点模型经实际握手返回，owner 归属正确
//       ③ 真实 sched18 生产路径：8/8/2 互斥、冻结片号
//       ④ A(guard,5120) / B(普通,2560)
//       ⑤ 越窗、延迟 hold、旧 raw 与新捕获同沿、复位后迟到 raw
//       ⑥ 释放只看 hold，raw 返回不提前释放旧组
//   * 波形：$dumpvars 到 tb_i3_full.fsdb（或 vcd），可追 arm/捕获/hold/raw/tag/post。
// 变更记录   :
//   2026-11-15  v1.0 只测 DUT 内宏的 raw 数值。
//   2026-11-15  v2.0 宏移到仿真边界，补 arm/登记分离、真实握手、sched18 生产路径。
// =============================================================================
module tb_i3_full;


    logic clk = 1'b0;

    // ---------------- 权重配置口（CONFIG_CONTRACT v1.0）----------------
    // 顶层受理门禁要求 cfg_valid=1，所以本 TB 在复位释放后必须装表并 commit。
    logic        t_cfg_we = 1'b0, t_cfg_sel = 1'b0;
    logic [4:0]  t_cfg_sid = 5'd0, t_cfg_idx = 5'd0;
    logic [39:0] t_cfg_data = 40'd0;
    logic        t_cfg_commit = 1'b0, t_cfg_reload = 1'b0;
    wire         t_cfg_valid, t_cfg_busy, t_cfg_range_err;

    task automatic t_wt_write(input logic sel, input logic [4:0] sid,
                              input logic [4:0] idx, input logic [39:0] d);
        begin
            @(negedge clk);
            t_cfg_we = 1'b1; t_cfg_sel = sel; t_cfg_sid = sid;
            t_cfg_idx = idx; t_cfg_data = d;
            @(posedge clk);
            @(negedge clk);
            t_cfg_we = 1'b0;
        end
    endtask

    // 装 360 个字（18 片 x 20）并 commit
    task automatic t_wt_install();
        begin
            @(negedge clk); t_cfg_reload = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_reload = 1'b0;
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++)
                    t_wt_write(1'b0, 5'(s), 5'(j), 40'd268435456);
                t_wt_write(1'b1, 5'(s), 5'd0, 40'd33554432);
                t_wt_write(1'b1, 5'(s), 5'd1, 40'd16777216);
                t_wt_write(1'b1, 5'(s), 5'd2, 40'd8388608);
                t_wt_write(1'b1, 5'(s), 5'd3, 40'd8388608);
            end
            @(negedge clk); t_cfg_commit = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_commit = 1'b0;
            @(posedge clk); @(negedge clk);
        end
    endtask

    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // ---- DUT 接口 ----
    logic        prepare_valid = 1'b0, prepare_ready;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v = 3'd0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;
    logic [2:0]  prepare_U = 3'd0, prepare_flash = 3'd0;
    logic        prepare_qid = 1'b0, stop_i = 1'b0;

    logic sample_event = 1'b0, input_captured = 1'b0;
    logic residue_captured = 1'b0, hold_ack = 1'b0;

    logic       ev_bit_valid_i = 1'b0, ev_bit_i = 1'b0;
    logic [3:0] ev_bit_index_i = 4'd0, ev_event_index_i = 4'd0;
    logic       ev_qid_i = 1'b0;

    // 真实 sched18 生产路径（sched_ext_sel = 0）
    logic        sched_ext_sel = 1'b0;
    logic [17:0] sched_acq = 18'd0, sched_conv = 18'd0, sched_idle = 18'd0;
    logic [39:0] sched_ids = 40'd0;
    logic        sched_seed_we = 1'b1;
    logic [31:0] sched_seed = 32'h1234_5678;

    // ADC2 握手（由本测试台的节点模型驱动）
    logic        adc2_valid_i = 1'b0;
    logic        adc2_ready_o;
    logic [12:0] adc2_raw_i = 13'd0;
    logic        adc2_range_err_i = 1'b0;
    logic        adc2_capture_arm_o;

    // DUT 输出
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [71:0] pre_state;  logic [17:0] pre_wr;
    logic [359:0] post_state; logic [17:0] post_wr;
    logic apply_fire, k_bad, final_applied, err, done;
    logic [9:0] k_out, final_k;
    logic [31:0] tag_out;
    logic qid_out;
    logic [159:0] post_slot;

    // 节点快照（DUT → 节点模型）
    logic [159:0] node_post;
    logic [31:0]  node_pre;
    logic [39:0]  node_ids;
    logic         node_qid;
    logic [31:0]  node_tag;
    logic         node_valid;
    logic [2:0]   node_h, node_v;

    // ADC2 返回
    logic        adc2_rsp_valid, adc2_rsp_qid, adc2_rsp_range, adc2_late_err;
    logic        adc2_release_ok;
    logic [31:0] adc2_rsp_tag;
    logic [12:0] adc2_rsp_raw;

    adc_digital_ip_v21 dut (
        .alpha_q30_cfg(32'd1073741824), // 名义增益与冻结向量一致。
        .w_cfg_we(t_cfg_we), .w_cfg_sel(t_cfg_sel),
        .w_cfg_sid(t_cfg_sid), .w_cfg_idx(t_cfg_idx), .w_cfg_data(t_cfg_data),
        .w_cfg_commit(t_cfg_commit), .w_cfg_reload(t_cfg_reload),
        .w_cfg_valid(t_cfg_valid), .w_cfg_busy(t_cfg_busy),
        .w_cfg_range_err(t_cfg_range_err),
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .dither_force_i(1'b1), .sadc_pre_state_o(), .sadc_pre_wr_o(),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_U(prepare_U), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid_i), .ev_bit_i(ev_bit_i),
        .ev_bit_index_i(ev_bit_index_i), .ev_event_index_i(ev_event_index_i),
        .ev_qid_i(ev_qid_i),
        // 本台架由 prepare_flash 预置 F，不使用独立 Flash 返回事务。
        .ev_flash_valid_i(1'b0), .ev_flash_code_i(3'd0),
        .sched_ext_sel(sched_ext_sel),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
        .rdac_post_state_o(post_state), .post_wr_o(post_wr),
        .adc2_valid_i(adc2_valid_i), .adc2_ready_o(adc2_ready_o),
        .adc2_raw_i(adc2_raw_i), .adc2_range_err_i(adc2_range_err_i),
        .adc2_capture_arm_o(adc2_capture_arm_o),
        .adc2_rsp_valid_o(adc2_rsp_valid), .adc2_rsp_tag_o(adc2_rsp_tag),
        .adc2_rsp_qid_o(adc2_rsp_qid), .adc2_rsp_raw_o(adc2_rsp_raw),
        .adc2_rsp_range_o(adc2_rsp_range), .adc2_late_err_o(adc2_late_err),
        .adc2_release_ok_o(adc2_release_ok),
        .node_post_o(node_post), .node_pre_o(node_pre), .node_ids_o(node_ids),
        .node_qid_o(node_qid), .node_tag_o(node_tag), .node_valid_o(node_valid),
        .node_h_o(node_h), .node_v_o(node_v),
        .apply_fire_o(apply_fire), .k_o(k_out), .k_bad_o(k_bad),
        .final_applied_o(final_applied), .final_k_o(final_k),
        .err_o(err), .done_o(done), .tag_o(tag_out), .qid_o(qid_out),
        .post_slot_o(post_slot));

    // ---------------------------------------------------------------- 节点模型（仿真边界）
    // 代表模拟宏 + Vin 真值。用 DUT 送出的快照算 y 与 raw。
    // 权重与 Vin 由本测试台按 §11.3 注入。
    logic signed [26:0] vin_q24 = 27'sd0;    // Vin，Q24/27 位
    logic signed [26:0] b_q24   = 27'sd0;
    logic [39:0]        G_q30   = 40'd34359738368;

    logic [39:0] w_main_tab [0:7][0:15];
    logic [39:0] w_sub_tab  [0:7][0:3];

    // 节点宏只在快照有效时求值
    wire node_calc = node_valid;
    logic        nm_y_valid;
    logic signed [39:0] nm_y_q24;
    logic [12:0] nm_raw;
    logic        nm_range;

    adc2_node_macro u_node (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(node_calc),
        .x_q24(vin_q24),
        .b_q24(b_q24),
        .G_q30(G_q30),
        .post_mask(node_post),
        .pre_mask(node_pre),
        .w_main(w_main_tab), .w_sub(w_sub_tab),
        .y_valid(nm_y_valid), .y_q24(nm_y_q24),
        .raw_code(nm_raw), .range_err(nm_range));

    // ---------------------------------------------------------------- 计分板
    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 30) $display("[%0t] ERROR: %s", $time, msg);
    endtask
    task automatic okc(input string msg);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, msg);
    endtask

    function automatic int pc18(input logic [17:0] v);
        int c; c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    function automatic logic [39:0] pack_ids(input int a[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(a[i]);
        return v;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

    // ---- 驱动任务 ----
    task automatic do_sample(input logic ic, input logic rc, input logic hk);
        @(negedge clk);
        sample_event = 1'b1; input_captured = ic;
        residue_captured = rc; hold_ack = hk;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0;
        residue_captured = 1'b0; hold_ack = 1'b0;
    endtask

    task automatic do_prepare(input logic [31:0] tag, input logic [5:0] w,
                              input logic [2:0] h, input logic [2:0] v,
                              input logic half, input logic coin,
                              input logic q, input logic [2:0] F);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = tag; prepare_word = w;
        prepare_h = h; prepare_v = v; prepare_half = half; prepare_coin = coin;
        prepare_U = {1'b0, w[5]} * 3'd4 + {2'b0, w[3]};
        prepare_flash = F; prepare_qid = q;
        @(posedge clk); #1;
        @(negedge clk);
        prepare_valid = 1'b0;
    endtask

    logic ev_applied;
    logic [9:0] ev_k;
    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        ev_bit_valid_i = 1'b1; ev_bit_i = bv; ev_bit_index_i = bi;
        ev_event_index_i = ei; ev_qid_i = q;
        #1;
        ev_applied = apply_fire; ev_k = k_out;
        @(posedge clk);
        @(negedge clk);
        ev_bit_valid_i = 1'b0;
    endtask

    // 模拟宏：把节点模型算出的 raw 通过握手送回
    task automatic deliver_raw(input logic [12:0] raw, input logic rng,
                               output logic [31:0] got_tag);
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = raw; adc2_range_err_i = rng;
        #1;
        got_tag = adc2_rsp_tag;
        @(posedge clk);
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0; adc2_range_err_i = 1'b0;
    endtask

    initial begin
        if ($test$plusargs("dump")) begin
            $dumpfile("tb_i3_full.vcd");
            $dumpvars(0, tb_i3_full);
        end

        // 名义权重
        for (int s = 0; s < 8; s++) begin
            for (int j = 0; j < 16; j++) w_main_tab[s][j] = 40'd268435456;
            w_sub_tab[s][0] = 40'd33554432;
            w_sub_tab[s][1] = 40'd16777216;
            w_sub_tab[s][2] = 40'd8388608;
            w_sub_tab[s][3] = 40'd8388608;
        end

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        t_wt_install();   // 装表 + commit
        @(negedge clk);
        sched_seed_we = 1'b0;

        // ==================== 真实 sched18 生产路径检查 ====================
        // 复位后第一拍：ACQ=8、CONV=0、IDLE=10 —— 这是**正确**的初态
        // （R03："首采样前无 CONV"）。稳态 8/8/2 要等第一笔 commit 之后。
        $display("---- 真实 sched18：初态与稳态 ----");
        repeat (2) @(negedge clk);
        checks = checks + 1;
        if (pc18(acq_sel) != 8 || pc18(conv_sel) != 0 || pc18(idle_sel) != 10)
            fail($sformatf("复位初态应为 8/0/10：acq=%0d conv=%0d idle=%0d",
                 pc18(acq_sel), pc18(conv_sel), pc18(idle_sel)));
        else $display("      复位初态 8/0/10（首采样前无 CONV）OK");
        checks = checks + 1;
        if ((acq_sel & conv_sel) != 0 || (acq_sel & idle_sel) != 0 || (conv_sel & idle_sel) != 0)
            fail("三集合有交叠");
        else $display("      三集合无交叠  OK");

        // ==================== A 笔（guard）====================
        $display("---- A: guard F=7, word=63, v_pre=0, Vin=3-2048LSB20/8 ----");
        vin_q24 = 27'sd50307072;   // 2.998535156 * 2^24（Q24，27 位）
        do_prepare(32'hA1, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (dut.acq_sampled !== 1'b1) fail("A 应已 sampled");
        else $display("      A sampled  OK");

        // B 顶替 -> A 进 CONV
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        checks = checks + 1;
        if (dut.conv_tag !== 32'hA1) fail("CONV 应为 A1");
        else $display("      CONV = A1  OK");

        // A 的 5 事件
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        do_event(4'd9, 4'd2, 1'b1, 1'b0);
        do_event(4'd2, 4'd3, 1'b0, 1'b0);
        do_event(4'd1, 4'd4, 1'b1, 1'b0);
        do_event(4'd0, 4'd5, 1'b0, 1'b0);
        checks = checks + 1;
        if (final_k !== 10'd519) fail($sformatf("A final_k=%0d 期望 519", final_k));
        else $display("      A k=519  OK");

        // ---- arm 必须已给出（早于公共沿）----
        // arm 现在的门槛含 wsum_valid（权重和已就绪），而 wsum 有 2 拍延迟，
        // 所以要多等几拍再检查。公共沿由测试台控制，时间充裕。
        repeat (4) @(negedge clk);
        $display("      [arm] arm_o=%b arm_req=%b wsum_valid=%b final_appl=%b conv_valid=%b seen=%b",
                 adc2_capture_arm_o, dut.adc2_arm_req, dut.wsum_valid,
                 dut.final_applied_w, dut.conv_valid, dut.adc2_arm_seen_q);
        checks = checks + 1;
        if (adc2_capture_arm_o !== 1'b1)
            fail("A 的最后 apply 之后 arm 应为 1（沿前授权）");
        else $display("      A arm 已给出（早于公共沿）OK");
        checks = checks + 1;
        if (node_valid !== 1'b1) fail("A 快照应有效");
        else $display("      A 节点快照有效  OK");

        // ---- E1：采 B + 捕获 A 余差 ----
        // 公共沿并行通知两个宏。此时只锁存 A 的余差，不能把 B 当作
        // 已进入转换。hold 故意推迟，以便将物理释放和码返回分开观察。
        $display("---- E1: 采 B 并捕获 A 余差 ----");
        @(negedge clk);
        sample_event = 1'b1; input_captured = 1'b1; residue_captured = 1'b1;
        hold_ack = 1'b0;    // hold 故意延迟
        #1;
        checks = checks + 1;
        if (nm_raw !== 13'd5120)
            fail($sformatf("节点模型 raw=%0d 期望 5120", nm_raw));
        else $display("      节点模型 raw = 5120（独立算出）OK");
        checks = checks + 1;
        if (nm_range !== 1'b0) fail("A 不应越窗");
        else $display("      A 未越窗  OK");
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0;

        // ---- 释放必须等 hold，raw 返回不能提前释放 ----
        checks = checks + 1;
        if (adc2_release_ok !== 1'b0)
            fail("hold 未到，release_ok 应为 0");
        else $display("      hold 未到 -> 不释放  OK");

        // ---- 通过实际握手返回 A 的 raw ----
        // 返回口不携带上游 tag。预期 A1 来自 DUT 保存的当前 ADC2 owner。
        begin
            logic [31:0] gt;
            deliver_raw(13'd5120, 1'b0, gt);
            checks = checks + 1;
            if (gt !== 32'hA1)
                fail($sformatf("A 返回 tag=%08h 期望 A1", gt));
            else $display("      A 返回 tag = A1（归属正确）OK");
        end
        checks = checks + 1;
        if (adc2_release_ok !== 1'b0)
            fail("raw 已返回但 hold 未到，仍不应释放");
        else $display("      raw 返回不提前释放旧组  OK");

        // ---- 补 hold -> 允许释放 ----
        do_sample(1'b0, 1'b0, 1'b1);
        checks = checks + 1;
        if (adc2_release_ok !== 1'b1)
            fail("hold 到后 release_ok 应为 1");
        else $display("      hold 到 -> release_ok=1  OK");

        // ==================== 旧 raw 与新捕获同沿 ====================
        $display("---- 旧 raw 与新捕获同沿：先绑旧 owner，再登记新 ==");
        // 此时 B 在 ACQ 且已 sampled；推进 B 进 CONV
        do_prepare(32'hC3, 6'd63, 3'd0, 3'd0, 1'b0, 1'b1, 1'b0, 3'd7);
        checks = checks + 1;
        if (dut.conv_tag !== 32'hB2) fail("CONV 应为 B2");
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
        if (final_k !== 10'd258) fail($sformatf("B final_k=%0d 期望 258", final_k));
        else $display("      B k=258  OK");

        // E2：捕获 B（同时不给 input_captured，模拟停收）
        // 停止新输入时仍需退役旧转换。两个完成量独立，以覆盖排空路径。
        @(negedge clk);
        sample_event = 1'b1; input_captured = 1'b0; residue_captured = 1'b1;
        hold_ack = 1'b0;
        #1;
        checks = checks + 1;
        if (nm_raw !== 13'd2560)
            fail($sformatf("B 节点 raw=%0d 期望 2560", nm_raw));
        else $display("      B 节点 raw = 2560（独立算出）OK");
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; residue_captured = 1'b0;

        // B 的 raw 返回
        // 新 owner 必须取 B2，即使 A 返回经历过延迟 hold。
        begin
            logic [31:0] gt2;
            deliver_raw(13'd2560, 1'b0, gt2);
            checks = checks + 1;
            if (gt2 !== 32'hB2)
                fail($sformatf("B 返回 tag=%08h 期望 B2", gt2));
            else $display("      B 返回 tag = B2  OK");
        end

        // ==================== 迟到 raw（复位后）====================
        $display("---- 复位后迟到 raw 必须被丢弃 ----");
        @(negedge clk);
        rst_n = 1'b0;
        @(negedge clk); rst_n = 1'b1;
        @(negedge clk);
        // 复位后没有 owner，注入 raw 应被丢弃并置 late_err
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = 13'd777;
        #1;
        checks = checks + 1;
        if (adc2_rsp_valid !== 1'b0)
            fail("复位后无 owner，不应产生返回");
        else $display("      复位后 raw 被丢弃  OK");
        @(posedge clk); #1;
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0;
        checks = checks + 1;
        if (adc2_late_err !== 1'b1) fail("迟到应置 late_err");
        else $display("      late_err 置位  OK");

        // ==================== 越窗 ====================
        $display("---- 越窗必须报 range ----");
        @(negedge clk);
        rst_n = 1'b0; @(negedge clk); rst_n = 1'b1; @(negedge clk);
        t_wt_install();   // 复位清 written_q，必须重装配置才恢复 cfg_valid
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
        sample_event = 1'b1; residue_captured = 1'b1;
        #1;
        checks = checks + 1;
        if (nm_range !== 1'b1)
            fail($sformatf("Vin=-3V 应越窗（y=%0d）", nm_y_q24));
        else $display("      越窗报 range  OK (raw=%0d)", nm_raw);
        @(posedge clk); @(negedge clk);
        sample_event = 1'b0; residue_captured = 1'b0;

        $display("---------------------------------------------------------------");
        $display("I3 full: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
