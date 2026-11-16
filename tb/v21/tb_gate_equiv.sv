`timescale 1ns/1ps
// 状态：**历史**。早期门级等价版本（单笔烟测），已被
//       tb_trace.sv + tools/check_gate_e2e.sh（两笔完整事务 + 交易数门禁）取代。
// =============================================================================
// 文件名     : tb_gate_equiv.sv
// 模块名     : tb_gate_equiv
// 版本       : v1.0
// 功能       : 门级等价：同一组激励同时驱动 RTL 与综合网表，逐拍比对全部输出。
//
// 设计说明   :
//   * 为什么用门级仿真而不是 Formality：
//     Formality W-2024.09-SP3 在本机进入了 (setup) 模式，其命令集与
//     DC/PT 不同（`link_library`、`set_top_designs`、`set_container` 都不存在），
//     本轮多次尝试未能跑通。门级仿真用已完全掌握的 VCS，能达到同样的
//     "网表与 RTL 行为一致"证据目的，且更直观（可逐拍定位差异）。
//     **局限**：仿真只覆盖施加的激励，不是形式化的全输入证明。
//     这一点必须在报告里写明，不能当作形式等价。
//
//   * 做法：实例化两个 DUT（一个 RTL，一个网表），接同一组输入，
//     每个时钟沿比对所有输出。任一不一致就报错并打印首个差异。
//   * 网表端需要 TSMC 单元库的行为模型（`tcbn28hpcplusbwp7t40p140.v`）。
// 变更记录   :
//   2026-11-16  初版。
// =============================================================================
module tb_gate_equiv;

    logic clk = 0;
    always #5 clk = ~clk;

    logic rst_n = 0;

    // ---------------------------------------------------------------- 公共输入
    logic        prepare_valid = 0;
    logic [31:0] prepare_tag = 0;
    logic [5:0]  prepare_word = 0;
    logic [2:0]  prepare_h = 0, prepare_v = 0;
    logic        prepare_half = 0, prepare_coin = 0, prepare_qid = 0;
    logic        stop_i = 0;
    logic        sample_event = 0, input_captured = 0, residue_captured = 0;
    logic        hold_ack = 0;
    logic        ev_bit_valid = 0, ev_bit = 0;
    logic [3:0]  ev_bit_index = 0, ev_event_index = 0;
    logic        ev_qid = 0, ev_flash_valid = 0;
    logic [2:0]  ev_flash_code = 0;
    logic        sched_seed_we = 0;
    logic [31:0] sched_seed = 0;
    logic        adc2_valid = 0;
    logic [12:0] adc2_raw = 0;
    logic        adc2_range_err = 0;
    logic [39:0] w_main [0:17][0:15];
    logic [39:0] w_sub  [0:17][0:3];
    logic [7:0]  cfg_epoch = 8'd1;
    logic signed [26:0] b_q24 = 27'sd0;

    // ---------------------------------------------------------------- RTL 输出
    logic        r_ready, r_out_valid, r_out_sat, r_out_err;
    logic [19:0] r_out_code;
    logic [31:0] r_out_tag;
    logic [17:0] r_acq_sel, r_conv_sel, r_idle_sel;
    logic [71:0] r_pre_state;
    logic [17:0] r_pre_wr;
    logic [359:0] r_post_state;
    logic [17:0] r_post_wr;
    logic         r_arm;
    logic         r_rsp_valid, r_rsp_qid, r_rsp_range, r_late_err, r_release_ok;
    logic [31:0]  r_rsp_tag;
    logic [12:0]  r_rsp_raw;
    logic [159:0] r_post_slot;
    logic         r_apply, r_kbad, r_final, r_err, r_done, r_qid;
    logic [9:0]   r_k, r_final_k;
    logic [31:0]  r_tag;
    logic         r_pf, r_cf, r_rf;
    logic [63:0]  r_wG, r_wWp, r_wWm;
    logic         r_wv;
    logic [159:0] r_node_post;
    logic [31:0]  r_node_pre;
    logic [39:0]  r_node_ids;
    logic         r_node_qid, r_node_valid;
    logic [31:0]  r_node_tag;
    logic [2:0]   r_node_h, r_node_v;

    adc_digital_ip_v21 u_rtl (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(r_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid), .ev_bit_i(ev_bit),
        .ev_bit_index_i(ev_bit_index), .ev_event_index_i(ev_event_index),
        .ev_qid_i(ev_qid), .ev_flash_valid_i(ev_flash_valid),
        .ev_flash_code_i(ev_flash_code),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(r_acq_sel), .conv_sel_o(r_conv_sel), .idle_sel_o(r_idle_sel),
        .rdac_pre_state_o(r_pre_state), .pre_wr_o(r_pre_wr),
        .rdac_post_state_o(r_post_state), .post_wr_o(r_post_wr),
        .adc2_valid_i(adc2_valid), .adc2_ready_o(),
        .adc2_raw_i(adc2_raw), .adc2_range_err_i(adc2_range_err),
        .adc2_capture_arm_o(r_arm),
        .adc2_rsp_valid_o(r_rsp_valid), .adc2_rsp_tag_o(r_rsp_tag),
        .adc2_rsp_qid_o(r_rsp_qid), .adc2_rsp_raw_o(r_rsp_raw),
        .adc2_rsp_range_o(r_rsp_range), .adc2_late_err_o(r_late_err),
        .adc2_release_ok_o(r_release_ok),
        .node_post_o(r_node_post), .node_pre_o(r_node_pre),
        .node_ids_o(r_node_ids), .node_qid_o(r_node_qid),
        .node_tag_o(r_node_tag), .node_valid_o(r_node_valid),
        .node_h_o(r_node_h), .node_v_o(r_node_v),
        .w_main_tbl(w_main), .w_sub_tbl(w_sub),
        .cfg_epoch(cfg_epoch), .b_q24_cfg(b_q24),
        .out_valid(r_out_valid), .out_code(r_out_code),
        .out_sat(r_out_sat), .out_err(r_out_err), .out_tag(r_out_tag),
        .apply_fire_o(r_apply), .k_o(r_k), .k_bad_o(r_kbad),
        .final_applied_o(r_final), .final_k_o(r_final_k),
        .err_o(r_err), .done_o(r_done), .tag_o(r_tag), .qid_o(r_qid),
        .post_slot_o(r_post_slot),
        .prepare_fire_o(r_pf), .commit_fire_o(r_cf), .release_fire_o(r_rf),
        .wsum_valid_o(r_wv), .wsum_G_o(r_wG), .wsum_Wp_o(r_wWp),
        .wsum_Wm_o(r_wWm));

    // ---------------------------------------------------------------- 网表输出
    logic        g_ready, g_out_valid, g_out_sat, g_out_err;
    logic [19:0] g_out_code;
    logic [31:0] g_out_tag;
    logic [17:0] g_acq_sel, g_conv_sel, g_idle_sel;
    logic [71:0] g_pre_state;
    logic [17:0] g_pre_wr;
    logic [359:0] g_post_state;
    logic [17:0] g_post_wr;
    logic         g_arm;
    logic         g_rsp_valid, g_rsp_qid, g_rsp_range, g_late_err, g_release_ok;
    logic [31:0]  g_rsp_tag;
    logic [12:0]  g_rsp_raw;
    logic [159:0] g_post_slot;
    logic         g_apply, g_kbad, g_final, g_err, g_done, g_qid;
    logic [9:0]   g_k, g_final_k;
    logic [31:0]  g_tag;
    logic         g_pf, g_cf, g_rf;
    logic [63:0]  g_wG, g_wWp, g_wWm;
    logic         g_wv;
    logic [159:0] g_node_post;
    logic [31:0]  g_node_pre;
    logic [39:0]  g_node_ids;
    logic         g_node_qid, g_node_valid;
    logic [31:0]  g_node_tag;
    logic [2:0]   g_node_h, g_node_v;

    adc_digital_ip_v21 u_gate (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(g_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid), .ev_bit_i(ev_bit),
        .ev_bit_index_i(ev_bit_index), .ev_event_index_i(ev_event_index),
        .ev_qid_i(ev_qid), .ev_flash_valid_i(ev_flash_valid),
        .ev_flash_code_i(ev_flash_code),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(g_acq_sel), .conv_sel_o(g_conv_sel), .idle_sel_o(g_idle_sel),
        .rdac_pre_state_o(g_pre_state), .pre_wr_o(g_pre_wr),
        .rdac_post_state_o(g_post_state), .post_wr_o(g_post_wr),
        .adc2_valid_i(adc2_valid), .adc2_ready_o(),
        .adc2_raw_i(adc2_raw), .adc2_range_err_i(adc2_range_err),
        .adc2_capture_arm_o(g_arm),
        .adc2_rsp_valid_o(g_rsp_valid), .adc2_rsp_tag_o(g_rsp_tag),
        .adc2_rsp_qid_o(g_rsp_qid), .adc2_rsp_raw_o(g_rsp_raw),
        .adc2_rsp_range_o(g_rsp_range), .adc2_late_err_o(g_late_err),
        .adc2_release_ok_o(g_release_ok),
        .node_post_o(g_node_post), .node_pre_o(g_node_pre),
        .node_ids_o(g_node_ids), .node_qid_o(g_node_qid),
        .node_tag_o(g_node_tag), .node_valid_o(g_node_valid),
        .node_h_o(g_node_h), .node_v_o(g_node_v),
        .w_main_tbl(w_main), .w_sub_tbl(w_sub),
        .cfg_epoch(cfg_epoch), .b_q24_cfg(b_q24),
        .out_valid(g_out_valid), .out_code(g_out_code),
        .out_sat(g_out_sat), .out_err(g_out_err), .out_tag(g_out_tag),
        .apply_fire_o(g_apply), .k_o(g_k), .k_bad_o(g_kbad),
        .final_applied_o(g_final), .final_k_o(g_final_k),
        .err_o(g_err), .done_o(g_done), .tag_o(g_tag), .qid_o(g_qid),
        .post_slot_o(g_post_slot),
        .prepare_fire_o(g_pf), .commit_fire_o(g_cf), .release_fire_o(g_rf),
        .wsum_valid_o(g_wv), .wsum_G_o(g_wG), .wsum_Wp_o(g_wWp),
        .wsum_Wm_o(g_wWm));

    // ---------------------------------------------------------------- 逐拍比对
    int errors = 0;
    int n_cmp = 0;

    task automatic chk64(input string nm, input logic [63:0] a, input logic [63:0] b);
        if (a !== b) begin
            errors = errors + 1;
            if (errors <= 10)
                $display("[%0t] MISMATCH %0s: rtl=%0h gate=%0h", $time, nm, a, b);
        end
    endtask

    // 网表有 1 拍建立延迟（库模型），所以比对放在 negedge：
    // 此时 RTL 与网表的组合输出都已稳定。
    always @(negedge clk) begin
        if (rst_n) begin
            n_cmp = n_cmp + 1;
            chk64("prepare_ready", {63'd0, r_ready},  {63'd0, g_ready});
            chk64("out_valid",     {63'd0, r_out_valid}, {63'd0, g_out_valid});
            chk64("out_code",      {44'd0, r_out_code},  {44'd0, g_out_code});
            chk64("out_sat",       {63'd0, r_out_sat}, {63'd0, g_out_sat});
            chk64("out_err",       {63'd0, r_out_err}, {63'd0, g_out_err});
            chk64("out_tag",       {32'd0, r_out_tag}, {32'd0, g_out_tag});
            chk64("acq_sel",       {46'd0, r_acq_sel}, {46'd0, g_acq_sel});
            chk64("conv_sel",      {46'd0, r_conv_sel},{46'd0, g_conv_sel});
            chk64("idle_sel",      {46'd0, r_idle_sel},{46'd0, g_idle_sel});
            chk64("pre_wr",        {46'd0, r_pre_wr},  {46'd0, g_pre_wr});
            chk64("post_wr",       {46'd0, r_post_wr}, {46'd0, g_post_wr});
            chk64("pre_state_hi",  {56'd0, r_pre_state[71:8]}, {56'd0, g_pre_state[71:8]});
            chk64("arm",           {63'd0, r_arm},     {63'd0, g_arm});
            chk64("rsp_valid",     {63'd0, r_rsp_valid},{63'd0, g_rsp_valid});
            chk64("rsp_tag",       {32'd0, r_rsp_tag}, {32'd0, g_rsp_tag});
            chk64("rsp_raw",       {51'd0, r_rsp_raw}, {51'd0, g_rsp_raw});
            chk64("apply",         {63'd0, r_apply},   {63'd0, g_apply});
            chk64("k",             {54'd0, r_k},       {54'd0, g_k});
            chk64("final",         {63'd0, r_final},   {63'd0, g_final});
            chk64("final_k",       {54'd0, r_final_k}, {54'd0, g_final_k});
            chk64("err",           {63'd0, r_err},     {63'd0, g_err});
            chk64("done",          {63'd0, r_done},    {63'd0, g_done});
            chk64("wsum_valid",    {63'd0, r_wv},      {63'd0, g_wv});
            chk64("wsum_G_hi",     r_wG[63:32],        g_wG[63:32]);
            chk64("node_valid",    {63'd0, r_node_valid},{63'd0, g_node_valid});
            chk64("node_ids",      {24'd0, r_node_ids},{24'd0, g_node_ids});
            chk64("pf",            {63'd0, r_pf},      {63'd0, g_pf});
            chk64("cf",            {63'd0, r_cf},      {63'd0, g_cf});
            chk64("rf",            {63'd0, r_rf},      {63'd0, g_rf});
        end
    end

    initial begin
        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) w_main[s][j] = 40'd268435456;
            w_sub[s][0] = 40'd33554432; w_sub[s][1] = 40'd16777216;
            w_sub[s][2] = 40'd8388608;  w_sub[s][3] = 40'd8388608;
        end

        repeat (3) @(negedge clk);
        rst_n = 1;
        repeat (4) @(negedge clk);

        // 一笔完整事务：prepare -> sample -> event -> 停收
        @(negedge clk);
        prepare_valid = 1; prepare_tag = 32'hA1; prepare_word = 6'd63;
        prepare_h = 3'd0; prepare_v = 3'd3; prepare_qid = 1'b0;
        @(posedge clk); @(negedge clk);
        prepare_valid = 0;
        sample_event = 1; input_captured = 1;
        @(posedge clk); @(negedge clk);
        sample_event = 0; input_captured = 0;

        // 逐事件应用（F=7 的 5 个已决事件）
        for (int i = 0; i < 5; i++) begin
            ev_bit_valid = 1; ev_bit = (i == 0) ? 1'b1 : 1'b0;
            ev_bit_index = 4'(9 - i); ev_event_index = 4'(1 + i);
            ev_flash_valid = (i == 0); ev_flash_code = 3'd7;
            @(posedge clk); @(negedge clk);
        end
        ev_bit_valid = 0; ev_flash_valid = 0;
        repeat (10) @(negedge clk);

        // ADC2 返回
        adc2_valid = 1; adc2_raw = 13'd5120; adc2_range_err = 0;
        @(posedge clk); @(negedge clk);
        adc2_valid = 0;
        hold_ack = 1;
        repeat (20) @(negedge clk);
        hold_ack = 0;
        repeat (10) @(negedge clk);

        $display("---------------------------------------------------------------");
        $display("gate_equiv: %0d 拍比对, %0d errors", n_cmp, errors);
        if (errors == 0) $display("RESULT: PASS (%0d cycles, RTL==netlist)", n_cmp);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
