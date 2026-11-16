`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_idle_partition.sv
// 模块名     : tb_idle_partition
// 版本       : v1.0
// 功能       : 检查 ACQ/CONV/IDLE 三集合互斥且并集恒为 18 片。
//
// 设计说明   :
//   * 反例来源（独立 VCS 实测，A 转换/B 采集稳定态）：
//       ACQ=3bd00(8) CONV=000ff(8) IDLE=00200(1)  -> 只覆盖 17 片
//     根因是 IDLE 直接取 sched_idle（推进后位图），与从冻结片号
//     展开的 ACQ/CONV 错开一组。
//   * 修法：IDLE = 18 片掩码扣掉实际 ACQ∪CONV 的补集，
//     使互斥且并集=18成为构造性性质。
//   * 本测试覆盖三个时点：复位释放、A 已采、A 转 CONV / B 在 ACQ。
// 变更记录   :
//   2026-11-16  初版。
// =============================================================================
// IDLE 分区专项检查：三集合互斥 + 并集 == 18 片
module tb_idle_partition;
  logic clk=0; always #5 clk=~clk;
  logic rst_n=0;
  logic [17:0] acq_sel, conv_sel, idle_sel;
  logic [17:0] sched_acq=0, sched_conv=0, sched_idle=0;
  logic [39:0] sched_ids=0;
  logic sched_seed_we=0; logic [31:0] sched_seed=0;
  logic prepare_valid=0; logic [31:0] prepare_tag=0;
  logic [5:0] prepare_word=0; logic [2:0] prepare_h=0, prepare_v=0;
  logic prepare_half=0, prepare_coin=0, prepare_qid=0, stop_i=0;
  logic sample_event=0, input_captured=0, residue_captured=0, hold_ack=0;
  logic [71:0] pre_state; logic [17:0] pre_wr;
  logic [359:0] post_state; logic [17:0] post_wr;
  logic [31:0] b_q24_cfg=0; logic [7:0] cfg_epoch=0;
  logic [39:0] w_main [0:17][0:15]; logic [39:0] w_sub [0:17][0:3];

  adc_digital_ip_v21 dut (
    .clk(clk), .rst_n(rst_n),
    .prepare_valid(prepare_valid), .prepare_ready(),
    .prepare_tag(prepare_tag), .prepare_word(prepare_word),
    .prepare_h(prepare_h), .prepare_v(prepare_v),
    .prepare_half(prepare_half), .prepare_coin(prepare_coin),
    .prepare_qid(prepare_qid), .stop_i(stop_i),
    .sample_event(sample_event), .input_captured(input_captured),
    .residue_captured(residue_captured), .hold_ack(hold_ack),
    .sched_ext_sel(1'b0),
    .sched_acq(sched_acq), .sched_conv(sched_conv),
    .sched_idle(sched_idle), .sched_ids(sched_ids),
    .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
    .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
    .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
    .rdac_post_state_o(post_state), .post_wr_o(post_wr),
    .b_q24_cfg(b_q24_cfg), .cfg_epoch(cfg_epoch),
    .w_main_tbl(w_main), .w_sub_tbl(w_sub));

  int fails=0;
  function automatic int pc(input logic [17:0] v); return $countones(v); endfunction

  task automatic chk(input string tag);
    int a=pc(acq_sel), c=pc(conv_sel), i=pc(idle_sel);
    logic [17:0] uni = acq_sel|conv_sel|idle_sel;
    logic [17:0] inter = (acq_sel&conv_sel)|(acq_sel&idle_sel)|(conv_sel&idle_sel);
    $display("  %0s: ACQ=%0d CONV=%0d IDLE=%0d 并集=%0d 交集=%05h",
             tag, a, c, i, pc(uni), inter);
    if (inter !== 18'd0) begin fails++; $display("    FAIL: 三集合有交叠"); end
    if (pc(uni) !== 18) begin fails++; $display("    FAIL: 并集不是 18 片"); end
  endtask

  initial begin
    for (int s=0;s<18;s++) begin
      for (int j=0;j<16;j++) w_main[s][j]=40'd268435456;
      for (int j=0;j<4;j++) w_sub[s][j]=40'd8388608;
    end
    repeat(3) @(negedge clk); rst_n=1; repeat(2) @(negedge clk);
    $display("---- 复位释放，尚未 prepare ----");
    chk("reset");

    $display("---- A 首笔 prepare ----");
    @(negedge clk);
    prepare_valid=1; prepare_tag=32'hA1; prepare_word=6'd63;
    prepare_h=3'd0; prepare_v=3'd3;
    @(posedge clk); @(negedge clk);
    prepare_valid=0;
    sample_event=1; input_captured=1;
    @(posedge clk); @(negedge clk);
    sample_event=0; input_captured=0;
    repeat(4) @(negedge clk);
    chk("A-acq");

    $display("---- B prepare（A 转 CONV，B 进 ACQ）----");
    @(negedge clk);
    prepare_valid=1; prepare_tag=32'hB2; prepare_word=6'd63;
    prepare_h=3'd0; prepare_v=3'd3;
    @(posedge clk); @(negedge clk);
    prepare_valid=0;
    sample_event=1; input_captured=1;
    @(posedge clk); @(negedge clk);
    sample_event=0; input_captured=0;
    repeat(4) @(negedge clk);
    chk("A-conv-B-acq");

    $display("===============================================");
    if (fails==0) $display("RESULT: PASS (IDLE 完整分区)");
    else          $display("RESULT: FAIL (%0d)", fails);
    $finish;
  end
endmodule
