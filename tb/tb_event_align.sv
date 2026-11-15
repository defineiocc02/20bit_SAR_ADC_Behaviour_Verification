`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_event_align.sv
// 模块名     : tb_event_align
// 版本       : v1.0
// 功能       : 逐事件验证 k、160 位物理 mask、有效/完成标志是否对应同一事件。
//
// 设计说明   :
//   * 为什么需要它：原 tb_d3d4_ip 只检查"k 严格增长 8 次、mask 改变 8 次"，
//     那是 smoke 级判据，抓不到"mask 落后 k 一个事件"这类错位。
//     实测曾出现：EV0 时 k_ev=1 而 mask 仍是复位值；EV1 时 k_ev=3 而
//     mask 是 k=1 的 —— 每个事件都配错了 k。
//   * 判据：对每个有效事件，用**参考算法按该事件的 k、该笔冻结的相位**
//     重算 160 位 mask，与 RTL 同拍输出逐位比较。k 与 mask 必须来自同一事件。
//   * 覆盖：连续事件、插空泡、最后一事件后的自然排空（不补假判决）、
//     复位、错误事件（乱序索引 / 错 qid）。
//
// 参考实现 : src/adi_model/dem_dither.py（与叶子级对拍同一份）
// 金标生成 : tools/gen_event_align.py 产出 tb/vec_event_align.txt
// =============================================================================
module tb_event_align;
    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rstn = 1'b0;
    logic ev_mode = 1'b1, ev_start = 1'b0, ev_bv = 1'b0, ev_bit = 1'b0, ev_qid = 1'b0;
    logic [3:0] ev_idx = 4'd0;
    logic [2:0] ev_flash = 3'd0, ev_up = 3'd0;
    logic valid_i = 1'b0, sample_adv = 1'b1;
    logic [9:0] k_i = 10'd0;
    logic [5:0] dither_i = 6'd0;

    logic ready_o, ev_valid_o, ev_done_o, ev_err_o, ev_proto_err_o, k_range_err_o;
    logic [8:0] k_ev_o;
    logic [159:0] mask_o;
    logic [31:0] pre_o;
    logic [2:0] upper_o;
    logic [3:0] f_o, res_o, hp_o;
    logic [2:0] vp_o;
    logic [17:0] acq_m, conv_m, idle_m;
    logic [39:0] acq_ids, conv_ids;

    d3d4_ip dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .k_i(k_i), .dither_i(dither_i),
        .sample_adv_i(sample_adv), .ready_o(ready_o),
        .mask_o(mask_o), .pre_o(pre_o), .upper_o(upper_o), .f_o(f_o),
        .reserved_o(res_o), .h_phase_o(hp_o), .v_phase_o(vp_o),
        .acq_mask_o(acq_m), .conv_mask_o(conv_m), .idle_mask_o(idle_m),
        .acq_ids_o(acq_ids), .conv_ids_o(conv_ids),
        .ev_mode_i(ev_mode), .ev_start_i(ev_start),
        .ev_bit_valid_i(ev_bv), .ev_bit_i(ev_bit),
        .ev_sar_index_i(ev_idx), .ev_sar_qid_i(ev_qid),
        .ev_flash_code_i(ev_flash), .ev_upper_i(ev_up),
        .ev_valid_o(ev_valid_o), .k_ev_o(k_ev_o), .ev_done_o(ev_done_o),
        .ev_err_o(ev_err_o), .ev_proto_err_o(ev_proto_err_o),
        .k_range_err_o(k_range_err_o));

    int errors = 0;
    int checks = 0;

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    // 金标：每个事件一行 seq k mask[8] 段 U f res hp vp
    logic [159:0] v_mask [0:63];
    int v_k [0:63];
    int v_res [0:63], v_hp [0:63], v_vp [0:63];
    int N_VEC = 0;

    task automatic load_vectors(input string path);
        int fd, r, n;
        int i0, i1, s0,s1,s2,s3,s4,s5,s6,s7;
        int rs, hs, vs;
        fd = $fopen(path, "r");
        if (fd == 0) begin $display("FATAL: cannot open %s", path); $finish; end
        n = 0;
        while (!$feof(fd) && n < 64) begin
            r = $fscanf(fd, "%d %d %h %h %h %h %h %h %h %h %d %d %d\n",
                        i0, i1, s0,s1,s2,s3,s4,s5,s6,s7, rs, hs, vs);
            if (r == 13) begin
                v_k[n]    = i1;
                v_mask[n] = { s7[19:0], s6[19:0], s5[19:0], s4[19:0],
                              s3[19:0], s2[19:0], s1[19:0], s0[19:0] };
                v_res[n] = rs; v_hp[n] = hs; v_vp[n] = vs;
                n = n + 1;
            end
        end
        $fclose(fd);
        N_VEC = n;
        $display("      载入 %0d 个事件金标", n);
    endtask

    // 一个事件：驱动 -> 在 posedge 后检查 k 与 mask 是否同属本事件
    task automatic do_event(input int arg_i, input logic bitv, input logic [3:0] idxv,
                            input logic qidv, input int expect_k, input logic [159:0] expect_mask,
                            input logic expect_err);
        @(negedge clk);
        ev_bv = 1'b1; ev_bit = bitv; ev_idx = idxv; ev_qid = qidv;
        @(posedge clk); #1;
        checks = checks + 1;
        if (ev_valid_o !== 1'b1)
            fail($sformatf("事件 %0d 未产生 ev_valid_o", arg_i));
        if (k_ev_o !== 9'(expect_k))
            fail($sformatf("事件 %0d: k_ev=%0d, 期望 %0d", arg_i, k_ev_o, expect_k));
        if (mask_o !== expect_mask) begin
            fail($sformatf("事件 %0d: mask 与 k=%0d 不配套（错位）", arg_i, expect_k));
            if (errors <= 3)
                $display("     got  %040h", mask_o);
            if (errors <= 3)
                $display("     want %040h", expect_mask);
        end
        if (ev_err_o !== expect_err)
            fail($sformatf("事件 %0d: err=%b, 期望 %b", arg_i, ev_err_o, expect_err));
        @(negedge clk); ev_bv = 1'b0;
    endtask

    initial begin
        load_vectors("vec_event_align.txt");

        // ---- 复位 ----
        repeat (3) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);
        if (ready_o !== 1'b0) fail("事件流模式下 ready_o 应为 0");

        // ---- 场景 A：连续 9 个事件（合法位序 8->0，bit 全 1）----
        $display("      场景 A: 连续事件");
        @(negedge clk); ev_start = 1'b1; ev_up = 3'd0; ev_flash = 3'd0;
        @(negedge clk); ev_start = 1'b0;
        for (int i = 0; i < 9; i++)
            do_event(i, 1'b1, 4'(8 - i), 1'b0, v_k[i], v_mask[i], 1'b0);
        @(negedge clk);
        if (ev_done_o !== 1'b1) fail("第 9 事件后 ev_done_o 未置位");
        // 最后一事件后：不再发假判决，检查 mask 是否停在该事件的 mask 上
        repeat (3) @(negedge clk);
        if (mask_o !== v_mask[8])
            fail("最后一事件后 mask 未停在该事件的值（排空不正确）");

        // ---- 场景 B：插空泡（每事件间隔 2 拍）----
        $display("      场景 B: 插空泡");
        repeat (2) @(negedge clk);
        rstn = 1'b0; @(negedge clk); rstn = 1'b1; @(negedge clk);
        @(negedge clk); ev_start = 1'b1; @(negedge clk); ev_start = 1'b0;
        for (int i = 0; i < 9; i++) begin
            do_event(100 + i, 1'b1, 4'(8 - i), 1'b0, v_k[i], v_mask[i], 1'b0);
            @(negedge clk);   // 空泡
        end
        if (errors == 0) $display("      场景 B 通过");

        // ---- 场景 C：错误事件（索引乱序）----
        $display("      场景 C: 错误事件（乱序索引）");
        repeat (2) @(negedge clk);
        rstn = 1'b0; @(negedge clk); rstn = 1'b1; @(negedge clk);
        @(negedge clk); ev_start = 1'b1; @(negedge clk); ev_start = 1'b0;
        // 第一个事件用非法索引（应为 8，给 3）
        @(negedge clk); ev_bv = 1'b1; ev_bit = 1'b1; ev_idx = 4'd3;
        @(posedge clk); #1;
        if (ev_proto_err_o !== 1'b1)
            fail("非法索引未触发 ev_proto_err_o");
        @(negedge clk); ev_bv = 1'b0;

        // ---- 场景 D：错误 qid ----
        $display("      场景 D: 错误 qid");
        repeat (2) @(negedge clk);
        rstn = 1'b0; @(negedge clk); rstn = 1'b1; @(negedge clk);
        @(negedge clk); ev_start = 1'b1; ev_qid = 1'b0; @(negedge clk); ev_start = 1'b0;
        // 第一个合法事件
        @(negedge clk); ev_bv = 1'b1; ev_bit = 1'b1; ev_idx = 4'd8; ev_qid = 1'b0;
        @(posedge clk); #1;
        @(negedge clk); ev_bv = 1'b0;
        // 第二个事件 qid 翻转
        @(negedge clk); ev_bv = 1'b1; ev_bit = 1'b1; ev_idx = 4'd7; ev_qid = 1'b1;
        @(posedge clk); #1;
        if (ev_proto_err_o !== 1'b1)
            fail("qid 翻转未触发 ev_proto_err_o");
        @(negedge clk); ev_bv = 1'b0;

        $display("---------------------------------------------------------------");
        $display("event_align: %0d 次事件检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d events, k/mask 逐事件配套)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end
endmodule
