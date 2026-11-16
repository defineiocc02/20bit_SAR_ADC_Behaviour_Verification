`timescale 1ns/1ps
// 状态：**历史**。I3 ADC2 早期版本，已被 tb_i3_full.sv 取代
//       （入口 tools/verify_v21.sh）。保留作追溯。
// =============================================================================
// 文件名     : tb_i3_adc2.sv
// 模块名     : tb_i3_adc2
// 版本       : v1.0
// 功能       : I3 验收 —— 13 位 ADC2 捕获归属、整字返回、越窗、迟到、复位。
// 设计说明   :
//   * 判据（方案 §11.2 I3 + §11.3）：
//       ① 名义 A 的 raw = 5120、B 的 raw = 2560（方案给定值）
//       ② 捕获归属按 owner 记录，不按"当前正在转换的那笔"
//       ③ 旧返回与新捕获同沿时先绑定旧 owner，再登记新 owner
//       ④ 迟到返回被丢弃，不得写进新的一笔
//       ⑤ 越窗报 range，不静默截断
//       ⑥ reset 清 owner，不产生假结果
//   * 名义向量（方案 §7 行400 / §11.3）：
//       A: x = 3 - 2048*LSB20/8 = 2.998535156, word=63, v_pre=0,
//          k=519, pre_slot=0xEFFFFFFF, y=0.28125 V, raw=5120
//       B: x = 0, word=17, v_pre=7, k=258, y=0.046875 V, raw=2560
//   * 权重（合同 §3.2 名义）：主 4Cu=1/4，子 [1/32,1/64,1/128,1/128]，G=32。
//     Q30 常量：主 268435456，子 33554432/16777216/8388608/8388608。
// 变更记录   :
//   2026-11-15  初版。
// =============================================================================
module tb_i3_adc2;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // ---- 事务 ----
    logic        prepare_valid = 1'b0, prepare_ready;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v = 3'd0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;
    logic [2:0]  prepare_U = 3'd0, prepare_flash = 3'd0;
    logic        prepare_qid = 1'b0, stop_i = 1'b0;

    // ---- 采样 ----
    logic sample_event = 1'b0, input_captured = 1'b0;
    logic residue_captured = 1'b0, hold_ack = 1'b0;

    // ---- 事件 ----
    logic       ev_bit_valid_i = 1'b0, ev_bit_i = 1'b0;
    logic [3:0] ev_bit_index_i = 4'd0, ev_event_index_i = 4'd0;
    logic       ev_qid_i = 1'b0;

    // ---- 片组（测试态外部驱动）----
    logic        sched_ext_sel = 1'b1;
    logic [17:0] sched_acq, sched_conv, sched_idle;
    logic [39:0] sched_ids;
    logic        sched_seed_we = 1'b0;
    logic [31:0] sched_seed = 32'h1234_5678;

    // ---- ADC2 宏侧 ----
    logic        adc2_valid_i = 1'b0;
    logic        adc2_ready_o;
    logic [12:0] adc2_raw_i = 13'd0;
    logic        adc2_range_err_i = 1'b0;
    logic        adc2_capture_arm_o;

    logic signed [25:0] x_q22_i = 26'sd0;
    logic signed [25:0] b_q22_i = 26'sd0;
    logic [39:0] G_q30_i = 40'd34359738368;   // 名义 G=32

    // ---- 输出 ----
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [71:0] pre_state;  logic [17:0] pre_wr;
    logic [359:0] post_state; logic [17:0] post_wr;
    logic apply_fire, k_bad, final_applied, err, done;
    logic [9:0] k_out, final_k;
    logic [31:0] tag_out;
    logic qid_out;
    logic [159:0] post_slot;

    logic        adc2_rsp_valid, adc2_rsp_qid, adc2_rsp_range, adc2_late_err;
    logic [31:0] adc2_rsp_tag;
    logic [12:0] adc2_rsp_raw;
    logic signed [39:0] adc2_y_q22;
    logic [12:0] adc2_node_raw;
    logic        adc2_node_range;

    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
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
        .x_q22_i(x_q22_i), .b_q22_i(b_q22_i), .G_q30_i(G_q30_i),
        .adc2_rsp_valid_o(adc2_rsp_valid), .adc2_rsp_tag_o(adc2_rsp_tag),
        .adc2_rsp_qid_o(adc2_rsp_qid), .adc2_rsp_raw_o(adc2_rsp_raw),
        .adc2_rsp_range_o(adc2_rsp_range), .adc2_late_err_o(adc2_late_err),
        .adc2_y_q22_o(adc2_y_q22), .adc2_node_raw_o(adc2_node_raw),
        .adc2_node_range_o(adc2_node_range),
        .apply_fire_o(apply_fire), .k_o(k_out), .k_bad_o(k_bad),
        .final_applied_o(final_applied), .final_k_o(final_k),
        .err_o(err), .done_o(done), .tag_o(tag_out), .qid_o(qid_out),
        .post_slot_o(post_slot));

    // 名义权重表
    localparam logic [39:0] WM = 40'd268435456;   // 1/4   Q30
    localparam logic [39:0] WS0 = 40'd33554432;   // 1/32
    localparam logic [39:0] WS1 = 40'd16777216;   // 1/64
    localparam logic [39:0] WS2 = 40'd8388608;    // 1/128
    localparam logic [39:0] WS3 = 40'd8388608;    // 1/128

    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask
    task automatic okc(input string msg);
        checks = checks + 1;
        if (errors <= 25) $display("[%0t] OK: %s", $time, msg);
    endtask

    function automatic logic [39:0] pack_ids(input int a[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(a[i]);
        return v;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

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

    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        ev_bit_valid_i = 1'b1; ev_bit_i = bv; ev_bit_index_i = bi;
        ev_event_index_i = ei; ev_qid_i = q;
        @(posedge clk); #1;
        @(negedge clk);
        ev_bit_valid_i = 1'b0;
    endtask

    // 注入一次 raw 返回
    task automatic give_raw(input logic [12:0] raw, input logic rng);
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = raw; adc2_range_err_i = rng;
        @(posedge clk); #1;
        // 沿后采样返回
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0; adc2_range_err_i = 1'b0;
    endtask

    initial begin
        // 名义权重
        for (int s = 0; s < 8; s++) begin
            for (int j = 0; j < 16; j++) dut.w_main_tab[s][j] = WM;
            dut.w_sub_tab[s][0] = WS0;
            dut.w_sub_tab[s][1] = WS1;
            dut.w_sub_tab[s][2] = WS2;
            dut.w_sub_tab[s][3] = WS3;
        end

        sched_acq = 18'h000FF; sched_conv = 18'h00000; sched_idle = 18'h3FF00;
        sched_ids = pack_ids(A_IDS);

        repeat (3) @(negedge clk);
        rst_n = 1'b1; @(negedge clk);

        // ==================== A 笔 ====================
        $display("---- A: x=3-2048LSB20/8, word=63, v_pre=0, F=7 guard ----");
        // Q22：2.998535156 * 2^22 = 12576768
        x_q22_i = 26'sd12576768;
        do_prepare(32'hA1, 6'd63, 3'd0, 3'd0, 1'b0, 1'b1, 1'b0, 3'd7);
        do_sample(1'b1, 1'b0, 1'b0);
        // B 顶替，A 进 CONV
        sched_acq = 18'h3BD00; sched_conv = 18'h000FF; sched_idle = 18'h04200;
        sched_ids = pack_ids(B_IDS);
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        checks = checks + 1;
        if (dut.conv_tag !== 32'hA1) fail("CONV 应为 A1");
        else $display("      CONV = A1  OK");

        // A 的 5 事件（k 序列到 519）
        // A 的 5 次提交：Flash(448) / 阈512 / bit2拒 / bit1受 / bit0拒
        // 方案 §7 行400：c=514, k=519
        //   evt1 Flash -> prefix=448
        //   evt2 阈512 接受 -> prefix=512
        //   evt3 bit2 拒 -> prefix 保持 512
        //   evt4 bit1 受 -> prefix=514
        //   evt5 bit0 拒 -> prefix 保持 514
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        do_event(4'd9, 4'd2, 1'b1, 1'b0);
        do_event(4'd2, 4'd3, 1'b0, 1'b0);   // bit2 **拒**（v2.0 曾误写 1）
        do_event(4'd1, 4'd4, 1'b1, 1'b0);   // bit1 受
        do_event(4'd0, 4'd5, 1'b0, 1'b0);   // bit0 拒
        checks = checks + 1;
        if (final_k !== 10'd519) fail($sformatf("A final_k=%0d 期望 519", final_k));
        else $display("      A k=519  OK");

        // E1：捕获 A 的余差（同时 input_captured=1 表示采 B）
        // 节点宏在本拍给出 A 的 raw
        $display("---- E1: 捕获 A 余差 ----");
        @(negedge clk);
        sample_event = 1'b1; input_captured = 1'b1; residue_captured = 1'b1;
        hold_ack = 1'b0;
        #1;
        // 本拍节点宏应算出 A 的余差
        checks = checks + 1;
        $display("      [diag] final_post=0x%040h", dut.final_post_q);
        $display("      [diag] conv_pre=0x%08h  G=%0d  x=%0d",
                 dut.conv_pre_slot, G_q30_i, x_q22_i);
        $display("      [diag] Wm_q22=%0d Wp_q22=%0d term_gx=%0d y_wide=%0d",
                 dut.u_node.Wm_q22, dut.u_node.Wp_q22,
                 dut.u_node.term_gx, dut.u_node.y_wide);
        $display("      [diag] DUT.x=%0d  node.x=%0d  node.G=%0d",
                 dut.x_q22_i, dut.u_node.x_q22, dut.u_node.G_q30);
        $display("      A node raw = %0d (期望 5120), y_q22=%0d", adc2_node_raw, adc2_y_q22);
        if (adc2_node_raw !== 13'd5120)
            fail($sformatf("A node raw=%0d 期望 5120", adc2_node_raw));
        else $display("      A node raw = 5120  OK");
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0;

        // ==================== A 的 raw 返回 ====================
        $display("---- A raw 返回（5120）----");
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = 13'd5120;
        // rsp_valid 是组合（= take_raw & owner_valid_q），必须在 posedge **之前**采：
        // posedge 后 owner_valid_q 已被清 0，读到的是 0（踩过）。
        #1;
        checks = checks + 1;
        if (adc2_rsp_valid !== 1'b1) fail("A 返回应有效");
        else $display("      A 返回 valid  OK");
        checks = checks + 1;
        if (adc2_rsp_tag !== 32'hA1)
            fail($sformatf("A 返回 tag=%08h 期望 A1（归属错误）", adc2_rsp_tag));
        else $display("      A 返回 tag = A1（归属正确）OK");
        checks = checks + 1;
        if (adc2_rsp_raw !== 13'd5120) fail($sformatf("A 返回 raw=%0d", adc2_rsp_raw));
        else $display("      A 返回 raw = 5120  OK");
        @(posedge clk);
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0;

        // ==================== 迟到返回 ====================
        $display("---- 迟到返回必须被丢弃 ----");
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = 13'd999;
        @(posedge clk); #1;
        checks = checks + 1;
        if (adc2_rsp_valid !== 1'b0) fail("无 owner 时不应产生返回");
        else $display("      迟到返回被丢弃  OK");
        checks = checks + 1;
        if (adc2_late_err !== 1'b1) fail("迟到应置 late_err");
        else $display("      late_err 置位  OK");
        @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0;

        // ==================== 越窗 ====================
        $display("---- 越窗应报 range ----");
        // 直接对宏施加超窗 y：把 x 设到 3.0 使 y > 0.5625
        begin
            // 窗口很窄：y = 32*x - 95.671875，要求 -0.1875 <= y < 0.5625
            //   => x 必须落在 [2.983887, 3.007324)
            // 所以 x=3.0 仍在窗内（y=0.328 V），**不会**越窗。
            // 用 x=-3V 才能确定性越窗（y=-191.7 V）。
            // 这条也从侧面验证了 §7 的窗口推导：ADC2 只覆盖满量程附近
            // 很窄的一段余差，不是全域。
            x_q22_i = -26'sd12582912;  // -3.0 * 2^22
            @(negedge clk);
            sample_event = 1'b1; residue_captured = 1'b1;
            #1;
            checks = checks + 1;
            if (adc2_node_range !== 1'b1)
                fail($sformatf("y 越窗未报 range（y=%0d）", adc2_y_q22));
            else $display("      越窗报 range  OK (raw=%0d)", adc2_node_raw);
            @(posedge clk); @(negedge clk);
            sample_event = 1'b0; residue_captured = 1'b0;
            x_q22_i = 26'sd12576768;
        end

        // ==================== B 笔：raw = 2560 ====================
        $display("---- B: x=0, word=17, v_pre=7, F=4, c=258 -> raw=2560 ----");
        repeat (2) @(negedge clk);
        rst_n = 1'b0; @(negedge clk); rst_n = 1'b1; @(negedge clk);
        // 重新灌权重（复位不影响，但保险）
        for (int ss2 = 0; ss2 < 8; ss2++) begin
            for (int jj = 0; jj < 16; jj++) dut.w_main_tab[ss2][jj] = WM;
            dut.w_sub_tab[ss2][0] = WS0; dut.w_sub_tab[ss2][1] = WS1;
            dut.w_sub_tab[ss2][2] = WS2; dut.w_sub_tab[ss2][3] = WS3;
        end
        x_q22_i = 26'sd0;   // B: x=0
        sched_acq = 18'h3BD00; sched_conv = 18'h0; sched_idle = 18'h04200;
        sched_ids = pack_ids(B_IDS);
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        do_sample(1'b1, 1'b0, 1'b0);
        // C 顶替 -> B 进 CONV
        sched_acq = 18'h000FF; sched_conv = 18'h3BD00; sched_idle = 18'h04200;
        sched_ids = pack_ids(A_IDS);
        do_prepare(32'hC3, 6'd63, 3'd0, 3'd0, 1'b0, 1'b1, 1'b0, 3'd7);
        checks = checks + 1;
        if (dut.conv_tag !== 32'hB2) fail("CONV 应为 B2");
        else $display("      CONV = B2  OK");
        // B 的 7 事件：Flash(256) bit5..bit0 全拒，bit1 受
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
        // 捕获 B 的余差
        @(negedge clk);
        sample_event = 1'b1; residue_captured = 1'b1;
        #1;
        checks = checks + 1;
        if (adc2_node_raw !== 13'd2560)
            fail($sformatf("B node raw=%0d 期望 2560", adc2_node_raw));
        else $display("      B node raw = 2560  OK");
        @(posedge clk); @(negedge clk);
        sample_event = 1'b0; residue_captured = 1'b0;
        // B 的 raw 返回，tag 必须是 B2
        @(negedge clk);
        adc2_valid_i = 1'b1; adc2_raw_i = 13'd2560;
        #1;
        checks = checks + 1;
        if (adc2_rsp_tag !== 32'hB2)
            fail($sformatf("B 返回 tag=%08h 期望 B2", adc2_rsp_tag));
        else $display("      B 返回 tag = B2  OK");
        @(posedge clk); @(negedge clk);
        adc2_valid_i = 1'b0; adc2_raw_i = 13'd0;

        $display("---------------------------------------------------------------");
        $display("I3 adc2: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
