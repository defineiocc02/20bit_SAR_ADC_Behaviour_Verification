`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_trace.sv
// 模块名     : tb_trace
// 版本       : v1.0
// 功能       : 输出逐拍轨迹，供 RTL 与门级网表比对。
//
// 设计说明   :
//   * 为什么分成"跑两次、比轨迹"而不是"两个 DUT 同一仿真"：
//     综合网表的顶层模块名与 RTL 相同（都叫 adc_digital_ip_v21），
//     同一仿真里无法同时例化两份。分别编译、各自输出轨迹文件，
//     再用脚本比对，是更简单且不会混淆的做法。
//   * 激励由 +SEED 决定，保证两次跑的激励完全一致。
//   * 轨迹每行：时间 + 所有关键输出（十六进制），便于 diff。
// 变更记录   :
//   2026-11-16  初版。
// =============================================================================
module tb_trace;

    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;

    logic        prepare_valid = 0;
    logic [31:0] prepare_tag = 0;
    logic [5:0]  prepare_word = 0;
    logic [2:0]  prepare_h = 0, prepare_v = 0;
    logic [2:0]  prepare_U = 0, prepare_flash = 0;
    logic        prepare_half = 0, prepare_coin = 0, prepare_qid = 0;
    logic        stop_i = 0;
    logic        sample_event = 0, input_captured = 0, residue_captured = 0;
    logic        hold_ack = 0;
    logic        ev_bit_valid = 0, ev_bit = 0;
    logic [3:0]  ev_bit_index = 0, ev_event_index = 0;
    logic        ev_qid = 0, ev_flash_valid = 0;
    logic [2:0]  ev_flash_code = 0;
    // **必须接全 sched_* 输入**。早前只接了 sched_seed_*，
    // 未接 sched_ext_sel / sched_acq / sched_conv / sched_idle / sched_ids，
    // 它们悬空为 X，经 sched18 传出后 acq_sel_o 全程是 xxxxx
    // （与门级轨迹 diff 时才暴露）。门级 TB 由脚本按网表端口生成，
    // 天然接全，所以只有 RTL 侧出问题。
    logic        sched_ext_sel = 0;
    logic [17:0] sched_acq = 0, sched_conv = 0, sched_idle = 0;
    logic [39:0] sched_ids = 0;
    logic        sched_seed_we = 0;
    logic [31:0] sched_seed = 0;
    logic        adc2_valid = 0;
    logic [12:0] adc2_raw = 0;
    logic        adc2_range_err = 0;
    logic [39:0] w_main [0:17][0:15];
    logic [39:0] w_sub  [0:17][0:3];
    logic [7:0]  cfg_epoch = 8'd1;
    logic signed [26:0] b_q24 = 27'sd0;

    logic        ready, out_valid, out_sat, out_err;
    logic [19:0] out_code;
    logic [31:0] out_tag;
    logic [17:0] acq_sel, conv_sel, idle_sel, pre_wr, post_wr;
    logic [71:0] pre_state;
    logic [359:0] post_state;
    logic        arm, rsp_valid, rsp_qid, rsp_range, late_err, release_ok;
    logic [31:0] rsp_tag;
    logic [12:0] rsp_raw;
    logic [159:0] post_slot;
    logic        apply, kbad, fin, err, done, qid;
    logic [9:0]  k, final_k;
    logic [31:0] tag;
    logic        pf, cf, rf, wv;
    logic [63:0] wG, wWp, wWm;
    logic [159:0] node_post;
    logic [31:0]  node_pre, node_tag;
    logic [39:0]  node_ids;
    logic         node_qid, node_valid;
    logic [2:0]   node_h, node_v;

    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_U(prepare_U), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid), .ev_bit_i(ev_bit),
        .ev_bit_index_i(ev_bit_index), .ev_event_index_i(ev_event_index),
        .ev_qid_i(ev_qid), .ev_flash_valid_i(ev_flash_valid),
        .ev_flash_code_i(ev_flash_code),
        .sched_ext_sel(sched_ext_sel),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
        .rdac_post_state_o(post_state), .post_wr_o(post_wr),
        .adc2_valid_i(adc2_valid), .adc2_ready_o(),
        .adc2_raw_i(adc2_raw), .adc2_range_err_i(adc2_range_err),
        .adc2_capture_arm_o(arm),
        .adc2_rsp_valid_o(rsp_valid), .adc2_rsp_tag_o(rsp_tag),
        .adc2_rsp_qid_o(rsp_qid), .adc2_rsp_raw_o(rsp_raw),
        .adc2_rsp_range_o(rsp_range), .adc2_late_err_o(late_err),
        .adc2_release_ok_o(release_ok),
        .node_post_o(node_post), .node_pre_o(node_pre),
        .node_ids_o(node_ids), .node_qid_o(node_qid),
        .node_tag_o(node_tag), .node_valid_o(node_valid),
        .node_h_o(node_h), .node_v_o(node_v),
        .w_main_tbl(w_main), .w_sub_tbl(w_sub),
        .cfg_epoch(cfg_epoch), .b_q24_cfg(b_q24),
        .out_valid(out_valid), .out_code(out_code),
        .out_sat(out_sat), .out_err(out_err), .out_tag(out_tag),
        .apply_fire_o(apply), .k_o(k), .k_bad_o(kbad),
        .final_applied_o(fin), .final_k_o(final_k),
        .err_o(err), .done_o(done), .tag_o(tag), .qid_o(qid),
        .post_slot_o(post_slot));

    int fd;

    // 每拍记录（negedge，组合输出已稳定）。
    // 逐字段独立 $fdisplay：早前手数 %0d/%0b 个数改字段就对不上
    // （踩过两次 MEAFFS）。每字段一行，字段增减不用重新数格式符。
    always @(negedge clk) if (rst_n) begin
        $fdisplay(fd, "T %0d", $time);
        $fdisplay(fd, "prepare_ready %0b", ready);
        $fdisplay(fd, "out_valid %0b", out_valid);
        $fdisplay(fd, "out_code %0h", out_code);
        $fdisplay(fd, "out_sat %0b", out_sat);
        $fdisplay(fd, "out_err %0b", out_err);
        $fdisplay(fd, "out_tag %0h", out_tag);
        $fdisplay(fd, "acq_sel_o %0h", acq_sel);
        $fdisplay(fd, "conv_sel_o %0h", conv_sel);
        $fdisplay(fd, "idle_sel_o %0h", idle_sel);
        $fdisplay(fd, "pre_wr_o %0h", pre_wr);
        $fdisplay(fd, "post_wr_o %0h", post_wr);
        $fdisplay(fd, "adc2_capture_arm_o %0b", arm);
        $fdisplay(fd, "adc2_rsp_valid_o %0b", rsp_valid);
        $fdisplay(fd, "adc2_rsp_tag_o %0h", rsp_tag);
        $fdisplay(fd, "apply_fire_o %0b", apply);
        $fdisplay(fd, "k_o %0h", k);
        $fdisplay(fd, "final_applied_o %0b", fin);
        $fdisplay(fd, "final_k_o %0h", final_k);
        $fdisplay(fd, "err_o %0b", err);
        $fdisplay(fd, "done_o %0b", done);
        $fdisplay(fd, "node_valid_o %0b", node_valid);
        $fdisplay(fd, "node_ids_o %0h", node_ids);
        $fdisplay(fd, "---");
    end

    initial begin
        reg [1023:0] opath;
        if (!$value$plusargs("OUT=%s", opath)) opath = "trace.txt";
        fd = $fopen(opath, "w");
        if (fd == 0) begin $display("FATAL: cannot open %0s", opath); $finish; end

        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) w_main[s][j] = 40'd268435456;
            w_sub[s][0] = 40'd33554432; w_sub[s][1] = 40'd16777216;
            w_sub[s][2] = 40'd8388608;  w_sub[s][3] = 40'd8388608;
        end

        repeat (3) @(negedge clk);
        rst_n = 1;
        repeat (4) @(negedge clk);

        // **激励必须与 tools/gen_gate_tb.py 生成的门级 TB 逐字一致**，
        // 否则两侧轨迹无法 diff。
        @(negedge clk);
        prepare_valid = 1; prepare_tag = 32'hA1; prepare_word = 6'd63;
        prepare_h = 3'd0; prepare_v = 3'd3; prepare_qid = 1'b0;
        prepare_U = 3'd5; prepare_flash = 3'd7;
        @(posedge clk); @(negedge clk);
        prepare_valid = 0;
        sample_event = 1; input_captured = 1;
        @(posedge clk); @(negedge clk);
        sample_event = 0; input_captured = 0;

        for (int i = 0; i < 5; i++) begin
            ev_bit_valid = 1; ev_bit = (i == 0) ? 1'b1 : 1'b0;
            ev_bit_index = 4'(9 - i); ev_event_index = 4'(1 + i);
            ev_flash_valid = (i == 0); ev_flash_code = 3'd7;
            @(posedge clk); @(negedge clk);
        end
        ev_bit_valid = 0; ev_flash_valid = 0;
        repeat (12) @(negedge clk);

        adc2_valid = 1; adc2_raw = 13'd5120;
        @(posedge clk); @(negedge clk);
        adc2_valid = 0;
        hold_ack = 1;
        repeat (24) @(negedge clk);
        hold_ack = 0;
        repeat (12) @(negedge clk);

        $fclose(fd);
        $display("trace written");
        $finish;
    end

endmodule
