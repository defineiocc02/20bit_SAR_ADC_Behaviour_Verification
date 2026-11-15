`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_acq_ctx_fixed.sv
// 功能       : 外部审查两条反例的**修复版断言**。
//             审查方提供的 tb_v21_acq_ctx_repro_6609157.sv 断言"必须复现错误"，
//             本 TB 断言"错误必须不复现"——两条 TB 互为对照：
//               旧 RTL  -> repro 版 PASS / 本版 FAIL
//               修复 RTL -> repro 版 FAIL / 本版 PASS
// 设计说明   :
//   * CASE1：未被受理的输入不得改变本笔物理预置，且物理状态与冻结快照一致。
//   * CASE2：CONV 未满足释放条件时不得被新笔覆盖；合法 release/promote 同沿仍能推进。
// =============================================================================
module tb_acq_ctx_fixed;

  logic clk = 0;
  always #5 clk = ~clk;
  logic reset_n = 0;

  logic prepare_valid = 0, prepare_ready;
  logic [31:0] prepare_tag = 0;
  logic [5:0] prepare_word = 0;
  logic [2:0] prepare_h = 0, prepare_v = 0, prepare_v_pre = 0;
  logic prepare_half = 0, prepare_coin = 0;
  logic [2:0] prepare_flash = 0;
  logic prepare_qid = 0, stop_i = 0;
  logic sample_event = 0, input_captured = 0, residue_captured = 0, hold_ack = 0;
  logic [17:0] sched_acq = 18'h000FF, sched_conv = 0, sched_idle = 18'd0;
  logic [39:0] sched_ids = 40'd0, sched_ids_next = 40'd0;
  logic [17:0] acq_sel, conv_sel, idle_sel;
  logic [2:0] acq_h, acq_v, acq_v_pre, acq_U;
  logic acq_half, acq_coin;
  logic [5:0] acq_word;
  logic [31:0] acq_pre_slot;
  logic [2:0] conv_h, conv_v;
  logic conv_half, conv_coin;
  logic [5:0] conv_word;
  logic [31:0] acq_pre_slot_o, acq_tag_o;
  logic [2:0] acq_flash_o, conv_flash_o;
  logic acq_qid_o, conv_qid_o;
  logic [39:0] acq_ids_o, conv_ids_o;
  logic acq_valid_o, acq_sampled_o, conv_valid_o;
  logic [71:0] rdac_pre_state_o;
  logic [17:0] pre_wr_o;
  logic [359:0] rdac_post_state_o;
  logic [17:0] post_wr_o;
  logic prepare_fire_o, commit_fire_o, sched_adv_o, release_fire_o;
  logic [31:0] conv_tag_o;
  logic [2:0] conv_U_o;
  logic [31:0] conv_pre_slot_o;

  acq_conv_ctx dut (
    .clk(clk), .rst_n(reset_n),
    .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
    .prepare_tag(prepare_tag), .prepare_word(prepare_word),
    .prepare_h(prepare_h), .prepare_v_dem(prepare_v), .prepare_v_pre(prepare_v_pre),
    .prepare_half(prepare_half), .prepare_coin(prepare_coin),
    .prepare_flash(prepare_flash), .prepare_qid(prepare_qid),
    .stop_i(stop_i),
    .sample_event(sample_event), .input_captured(input_captured),
    .residue_captured(residue_captured), .hold_ack(hold_ack),
    .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
    .sched_ids(sched_ids), .sched_ids_next(sched_ids_next),
    .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
    .acq_h_o(acq_h), .acq_v_dem_o(acq_v), .acq_v_pre_o(acq_v_pre),
    .acq_half_o(acq_half), .acq_coin_o(acq_coin),
    .acq_word_o(acq_word), .acq_U_o(acq_U),
    .acq_pre_slot_i(acq_pre_slot), .acq_pre_slot_o(acq_pre_slot_o),
    .acq_tag_o(acq_tag_o),
    .conv_h_o(conv_h), .conv_v_dem_o(conv_v),
    .conv_half_o(conv_half), .conv_coin_o(conv_coin),
    .conv_word_o(conv_word),
    .acq_ids_o(acq_ids_o), .conv_ids_o(conv_ids_o),
    .acq_valid_o(acq_valid_o), .acq_sampled_o(acq_sampled_o),
    .conv_valid_o(conv_valid_o),
    .rdac_pre_state_o(rdac_pre_state_o), .pre_wr_o(pre_wr_o),
    .rdac_post_state_o(rdac_post_state_o), .post_wr_o(post_wr_o),
    .prepare_fire_o(prepare_fire_o), .commit_fire_o(commit_fire_o),
    .sched_adv_o(sched_adv_o), .release_fire_o(release_fire_o),
    .conv_tag_o(conv_tag_o), .conv_U_o(conv_U_o),
    .conv_pre_slot_o(conv_pre_slot_o));

  // dither_pre 提供组合 pre（模拟上游：word 一变，acq_pre_slot 就变）
  logic [2:0] dU; logic [3:0] df;
  dither_pre u_pd (.word_i(prepare_word), .v_phase_i(prepare_v_pre),
                   .pre_o(acq_pre_slot), .U_o(dU), .f_o(df));

  int errors = 0;
  task automatic chk(input logic cond, input string msg);
    if (!cond) begin errors++; $display("  FAIL: %s", msg); end
  endtask

  function automatic logic [39:0] pack_ids(input int a0,a1,a2,a3,a4,a5,a6,a7);
    logic [39:0] v; v = 40'd0;
    v[0*5+:5]=5'(a0); v[1*5+:5]=5'(a1); v[2*5+:5]=5'(a2); v[3*5+:5]=5'(a3);
    v[4*5+:5]=5'(a4); v[5*5+:5]=5'(a5); v[6*5+:5]=5'(a6); v[7*5+:5]=5'(a7);
    return v;
  endfunction

  task automatic do_reset;
    reset_n = 0; repeat (3) @(negedge clk); reset_n = 1; @(negedge clk);
  endtask

  initial begin
    sched_ids      = pack_ids(0,1,2,3,4,5,6,7);
    sched_ids_next = pack_ids(9,10,11,12,13,14,15,17);
    do_reset();
    @(negedge clk);
    $display("  [dbg0] after reset: acq_valid=%b conv_valid=%b ready=%b",
             acq_valid_o, conv_valid_o, prepare_ready);

    // ==================== CASE 1 ====================
    $display("---- CASE1: 未受理的输入不得改变本笔物理预置 ----");
    // 与原始复现 TB 一致的驱动方式：
    //   negedge 后 #1 采"posedge 前的组合 fire"，再 posedge 提交、再撤 valid。
    @(negedge clk);
    prepare_valid = 1; prepare_tag = 32'hA1; prepare_word = 6'd0;
    prepare_v_pre = 3'd0; prepare_flash = 3'd7;
    #1;
    chk(prepare_fire_o === 1'b1, "A 未被受理");
    @(posedge clk); #1;
    prepare_valid = 0;

    // 记录受理后的冻结状态
    begin
      logic [71:0] pre_before;
      logic [31:0] frozen_before;
      pre_before   = rdac_pre_state_o;
      frozen_before= acq_pre_slot_o;
      // 不给新请求，只改 word
      @(negedge clk);
      prepare_word = 6'd63;
      #1;
      chk(prepare_fire_o === 1'b0, "不应受理新事务");
      chk(pre_wr_o === 18'h000FF, "写门应仍是 A 的 8 片");
      chk(rdac_pre_state_o === pre_before,
          $sformatf("物理 pre 变了: %018h -> %018h", pre_before, rdac_pre_state_o));
      chk(acq_pre_slot_o === frozen_before,
          $sformatf("冻结快照变了: %08h -> %08h", frozen_before, acq_pre_slot_o));
      $display("  physical_pre 保持 %018h, frozen 保持 %08h",
               rdac_pre_state_o, acq_pre_slot_o);
    end

    // ==================== CASE 2 ====================
    $display("---- CASE2: 未释放的 CONV 不得被覆盖 ----");
    do_reset();
    sched_ids      = pack_ids(0,1,2,3,4,5,6,7);
    sched_ids_next = pack_ids(9,10,11,12,13,14,15,17);
    // A 受理并采样
    @(negedge clk);
    prepare_valid = 1; prepare_tag = 32'hA1; prepare_word = 6'd0; prepare_flash = 3'd7;
    @(posedge clk); #1; prepare_valid = 0;
    @(negedge clk); sample_event = 1; input_captured = 1;
    @(posedge clk); @(negedge clk); sample_event = 0; input_captured = 0;
    // B 顶替 -> A 进 CONV
    @(negedge clk);
    prepare_valid = 1; prepare_tag = 32'hB2; prepare_word = 6'd1; prepare_flash = 3'd4;
    @(posedge clk); #1; prepare_valid = 0;
    chk(conv_tag_o === 32'hA1, "CONV 应为 A1");
    @(negedge clk); sample_event = 1; input_captured = 1;
    @(posedge clk); @(negedge clk); sample_event = 0; input_captured = 0;

    // CONV 未释放（hold=0），C 请求必须被拒
    chk(conv_valid_o === 1'b1, "A 应仍在 CONV");
    chk(release_fire_o === 1'b0, "A 不应已释放");
    @(negedge clk);
    prepare_valid = 1; prepare_tag = 32'hC3; prepare_word = 6'd2; prepare_flash = 3'd2;
    #1;
    chk(prepare_ready === 1'b0, "CONV 未释放时 prepare 应被拒");
    chk(prepare_fire_o === 1'b0, "CONV 未释放时不应受理");
    @(posedge clk); #1; prepare_valid = 0;
    chk(conv_tag_o === 32'hA1, $sformatf("CONV 被覆盖成 %08h", conv_tag_o));
    chk(acq_valid_o === 1'b1, "B 应仍在 ACQ");
    $display("  CONV 仍为 A1，B 仍在 ACQ，C 被拒");

    // ==================== 合法释放后应能推进 ====================
    $display("---- CASE2b: 合法释放后仍能推进 ----");
    @(negedge clk); residue_captured = 1; hold_ack = 1; sample_event = 1;
    @(posedge clk); @(negedge clk);
    residue_captured = 0; hold_ack = 0; sample_event = 0;
    repeat (4) @(negedge clk);
    chk(conv_valid_o === 1'b0, "hold+捕获+安静窗后 A 应释放");
    @(negedge clk);
    prepare_valid = 1; prepare_tag = 32'hC3; prepare_word = 6'd2; prepare_flash = 3'd2;
    #1;
    chk(prepare_fire_o === 1'b1, "释放后 prepare 应被接受");
    @(posedge clk); #1; prepare_valid = 0;
    chk(conv_tag_o === 32'hB2, $sformatf("B 应提升进 CONV，实为 %08h", conv_tag_o));
    $display("  A 释放 -> B 提升进 CONV，C 建立 ACQ");

    $display("---------------------------------------------------------------");
    if (errors == 0) $display("RESULT: PASS (两反例已修复)");
    else             $display("RESULT: FAIL (%0d)", errors);
    $finish;
  end
endmodule
