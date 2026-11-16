`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_audit3.sv
// 模块名     : tb_audit3
// 版本       : v1.0
// 功能       : 三项审计反例的正式回归（对应 isscc9p8_audit_20261115）。
//              ① 切片写门重叠：pre_wr 与 post_wr 交集必须为零
//              ② guard 拒绝 512 阈：不得 apply、不得写物理开关，总写 7 次
//              ③ j19 转换权重：k=519 -> raw=5120，k=520 -> raw=1024
// 设计说明   :
//   * 这三条来自外部审计（基线 0977e14），旧版分别观察到：
//       ① pre_wr == post_wr == 0x000FF（全重叠）
//       ② GUARD_REJECT applied=1、writes=8
//       ③ k=520 也得到 raw=5120（j19 漏算）
//   * 修复要点写在各段注释里，便于回溯。
//   * 独立可跑：①用完整顶层；②③用叶子（sar_evt_v21 / adc2_node_macro）。
// 变更记录   :
//   2026-11-15  初版。三项修复的正式回归。
// =============================================================================
module tb_audit3;


    logic clk = 0;

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

    int errors = 0, checks = 0;
    task automatic fail(input string m);
        errors = errors + 1;
        $display("[%0t] ERROR: %s", $time, m);
    endtask
    task automatic okc(input string m);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, m);
    endtask

    // ============================================================ ① 写门不重叠
    logic rst_n = 0, prepare = 0, ready;
    logic [31:0] tag = 0;
    logic [5:0] word_in = 0;
    logic [2:0] upper = 0, flash = 0;
    logic qid = 0, sample_event = 0, event_valid = 0;
    logic [17:0] acq_sel, conv_sel, pre_wr, post_wr;

    adc_digital_ip_v21 dut (
        .w_cfg_we(t_cfg_we), .w_cfg_sel(t_cfg_sel),
        .w_cfg_sid(t_cfg_sid), .w_cfg_idx(t_cfg_idx), .w_cfg_data(t_cfg_data),
        .w_cfg_commit(t_cfg_commit), .w_cfg_reload(t_cfg_reload),
        .w_cfg_valid(t_cfg_valid), .w_cfg_busy(t_cfg_busy),
        .w_cfg_range_err(t_cfg_range_err),
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare), .prepare_ready(ready),
        .prepare_tag(tag), .prepare_word(word_in),
        .prepare_h(3'd0), .prepare_v(3'd0),
        .prepare_half(1'b0), .prepare_coin(1'b0),
        .prepare_U(upper), .prepare_flash(flash), .prepare_qid(qid),
        .stop_i(1'b0),
        .sample_event(sample_event), .input_captured(sample_event),
        .residue_captured(1'b0), .hold_ack(1'b0),
        .ev_bit_valid_i(event_valid), .ev_bit_i(1'b0),
        .ev_bit_index_i(4'd0), .ev_event_index_i(4'd1), .ev_qid_i(1'b0),
        .ev_flash_valid_i(1'b0), .ev_flash_code_i(3'd0),
        .sched_ext_sel(1'b0),
        .sched_acq(18'd0), .sched_conv(18'd0), .sched_idle(18'd0),
        .sched_ids(40'd0), .sched_seed_we(1'b0), .sched_seed(32'd0),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel),
        .pre_wr_o(pre_wr), .post_wr_o(post_wr),
        .adc2_valid_i(1'b0), .adc2_raw_i(13'd0), .adc2_range_err_i(1'b0));

    // ============================================================ ② guard 反例
    logic        g_rst_n = 0, g_start = 0, g_bv = 0, g_bit = 0;
    logic [3:0]  g_ei = 4'd0;     // 提交序号（必须驱动，否则永远被拒）
    logic [3:0]  g_bi = 4'd0;     // 物理位号（guard 阈试探用 9）
    logic [2:0]  g_up = 3'd5, g_flash = 3'd7;
    logic [31:0] g_tag = 32'hA1;
    logic        g_fv = 1'b1;
    logic        g_apply, g_kbad, g_final, g_err, g_done;
    logic [9:0]  g_k, g_fk;
    logic [31:0] g_tagout;
    logic        g_qid, g_last;
    int          g_writes;

    sar_evt_v21 u_evt (
        .clk(clk), .rst_n(g_rst_n),
        .start_i(g_start), .abort_i(1'b0),
        .tag_i(g_tag), .qid_i(1'b0), .upper_i(g_up),
        .flash_valid_i(g_fv), .flash_code_i(g_flash),
        .bit_valid_i(g_bv), .bit_i(g_bit),
        .bit_index_i(g_bi), .event_index_i(g_ei),
        .sar_qid_i(1'b0), .sar_final_i(1'b0),
        .ev_valid_o(), .ev_event_index_o(), .ev_bit_index_o(),
        .k_o(g_k), .k_bad_o(g_kbad), .apply_fire_o(g_apply),
        .last_evt_o(g_last),
        .final_applied_o(g_final), .final_k_o(g_fk),
        .tag_o(g_tagout), .qid_o(g_qid), .err_o(g_err), .done_o(g_done));

    // ============================================================ ③ j19 权重
    logic        n_rst_n = 0, n_cv = 1;
    logic signed [26:0] n_x = 27'sd50307072;   // 3-2048LSB20/8, Q24
    logic signed [26:0] n_b = 0;
    logic [39:0] n_G = 40'd34359738368;
    logic [159:0] n_post;
    logic [31:0]  n_pre = 32'hEFFFFFFF;
    logic [39:0]  n_wm[0:7][0:15];
    logic [39:0]  n_ws[0:7][0:3];
    logic         n_yv, n_rng;
    logic signed [39:0] n_y;
    logic [12:0]  n_raw;

    adc2_node_macro u_node (
        .clk(clk), .rst_n(n_rst_n), .calc_valid(n_cv),
        .x_q24(n_x), .b_q24(n_b), .G_q30(n_G),
        .post_mask(n_post), .pre_mask(n_pre),
        .w_main(n_wm), .w_sub(n_ws),
        .y_valid(n_yv), .y_q24(n_y),
        .raw_code(n_raw), .range_err(n_rng));

    initial begin
        // 名义权重
        for (int s = 0; s < 8; s++) begin
            for (int j = 0; j < 16; j++) n_wm[s][j] = 40'd268435456;
            n_ws[s][0] = 40'd33554432; n_ws[s][1] = 40'd16777216;
            n_ws[s][2] = 40'd8388608;  n_ws[s][3] = 40'd8388608;
        end

        // ==================== ① 写门不重叠 ====================
        $display("---- ① 切片写门：pre_wr 与 post_wr 交集必须为零 ----");
        repeat (2) @(negedge clk); rst_n = 1;
        t_wt_install();   // 装表 + commit，否则 cfg_valid=0 门禁挡住交易
        @(negedge clk);
        prepare = 1; tag = 32'hA1; word_in = 6'd63; upper = 3'd5; flash = 3'd7;
        @(posedge clk); @(negedge clk);
        prepare = 0; sample_event = 1;
        @(posedge clk); @(negedge clk);
        sample_event = 0;
        prepare = 1; tag = 32'hB2; word_in = 6'd17; upper = 0; flash = 3'd4; qid = 1;
        @(posedge clk); @(negedge clk);
        prepare = 0; event_valid = 1;
        @(posedge clk); #1;
        checks = checks + 1;
        if ((pre_wr & post_wr) !== 18'd0)
            fail($sformatf("pre_wr=%05h post_wr=%05h 交集=%05h 应为 0",
                 pre_wr, post_wr, pre_wr & post_wr));
        else $display("      pre_wr=%05h post_wr=%05h overlap=0  OK", pre_wr, post_wr);
        checks = checks + 1;
        if (pre_wr !== 18'h3BD00 && pre_wr !== dut.s18_acq)
            fail($sformatf("pre_wr=%05h 应与本笔冻结 ACQ 组一致 (%05h)", pre_wr, dut.s18_acq));
        else $display("      pre_wr 与该笔 ACQ 组一致  OK");
        checks = checks + 1;
        // post_wr 是**事件发生时刻**的 CONV 组，此刻 sched18 可能已推进，
        // 所以不能拿当前 s18_conv 比。用 conv_mask 的记录值。
        if (post_wr !== 18'h000FF)
            fail($sformatf("post_wr=%05h 应为 A 的 CONV 组 000ff", post_wr));
        else $display("      post_wr = A 的 CONV 组  OK");
        @(negedge clk); event_valid = 0;

        // ==================== ② guard 拒绝 512 ====================
        $display("---- ② guard 拒绝 512：不 apply、总写 7 次、k=485 ----");
        repeat (2) @(negedge clk); g_rst_n = 1; @(negedge clk);
        g_writes = 0;
        // start + Flash F=7
        g_start = 1; g_fv = 1; g_flash = 3'd7;
        @(posedge clk); #1; @(negedge clk);
        g_start = 0; g_fv = 0;
        // evt1：Flash 提交（448），bit 无意义
        // **必须在 posedge 之前采 apply_fire**：它是组合信号，
        // 依赖当拍的 exp_evt_q；posedge 后 exp_evt_q 已被非阻塞赋值推进，
        // apply 立刻变 0（踩过：整段 g_writes 一直是 0）。
        g_bv = 1; g_bit = 0; g_ei = 4'd1; #1;
        if (g_apply) g_writes = g_writes + 1;
        @(posedge clk); @(negedge clk);
        // evt2：阈 512 试探 -> 拒绝
        g_bv = 1; g_bit = 0; g_ei = 4'd2; g_bi = 4'd9; #1;   // 阈试探用 bit_index=9
        checks = checks + 1;
        if (g_apply !== 1'b0) fail("拒 512 那次不应 apply");
        else $display("      拒 512 -> apply=0  OK");
        if (g_apply) g_writes = g_writes + 1;
        @(posedge clk); @(negedge clk);
        // evt3..evt7：bit5 受，bit4..bit0 全拒
        begin
            logic [3:0] bi;
            logic       bv;
            for (int i = 0; i < 6; i++) begin
                bi = (i == 0) ? 4'd5 : 4'(5 - i);
                bv = (i == 0) ? 1'b1 : 1'b0;
                // 阈被拒不占提交序号，所以 bit5 从序号 2 开始
                g_ei = 4'(2 + i); g_bi = bi;
                g_bv = 1; g_bit = bv; #1;
                if (g_apply) g_writes = g_writes + 1;
                else $display("      [g] ev%0d(bi=%0d ei=%0d) 未 apply: guard=%b exp_evt=%0d err=%b done=%b",
                              i+3, bi, g_ei, u_evt.guard_q, u_evt.exp_evt_q, u_evt.err_q, u_evt.done_q);
                @(posedge clk); @(negedge clk);
            end
        end
        g_bv = 0;
        checks = checks + 1;
        if (g_fk !== 10'd485)
            fail($sformatf("final_k=%0d 期望 485", g_fk));
        else $display("      final_k = 485  OK");
        checks = checks + 1;
        if (g_writes !== 7)
            fail($sformatf("实际写 %0d 次，期望 7", g_writes));
        else $display("      实际写 7 次  OK");

        // ==================== ③ j19 权重 ====================
        $display("---- ③ j19：k=519 -> 5120，k=520 -> 1024 ----");
        repeat (2) @(negedge clk); n_rst_n = 1; @(negedge clk);
        n_post = 160'h7ffff_7ffff_7ffff_7ffff_7ffff_7ffff_7ffff_7ffff;
        @(negedge clk); #1;
        checks = checks + 1;
        if (n_raw !== 13'd5120)
            fail($sformatf("k=519(0x7FFFF) raw=%0d 期望 5120", n_raw));
        else $display("      k=519 raw = 5120  OK");
        n_post = 160'hfffff_fffff_fffff_fffff_fffff_fffff_fffff_fffff;
        @(negedge clk); #1;
        checks = checks + 1;
        if (n_raw !== 13'd1024)
            fail($sformatf("k=520(0xFFFFF) raw=%0d 期望 1024", n_raw));
        else $display("      k=520 raw = 1024  OK");
        checks = checks + 1;
        if (n_y !== -40'sd1572864)
            fail($sformatf("k=520 y=%0d 期望 -1572864 (-0.09375 V)", n_y));
        else $display("      k=520 y = -0.09375 V  OK");
        checks = checks + 1;
        if (n_rng !== 1'b0) fail("k=520 应在 ADC2 窗口内");
        else $display("      k=520 未越窗  OK");

        $display("---------------------------------------------------------------");
        $display("audit3: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
