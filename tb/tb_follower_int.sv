`timescale 1ns/1ps
// 顶层事件口对拍：期望前缀由 TB 记分板按"已发送的位"独立累加，
// 不读 RTL 内部、也不用最终码反推。
module tb_follower_int;
    logic clk = 0, rstn = 0;
    logic valid_i = 0, stall_i = 0;
    logic [8:0] c_i = 0;
    logic [11:0] adc2_i = 0;
    logic [5:0] dither_i = 0;
    logic [3:0] reserved_i = 0, h_phase_i = 0;
    logic [2:0] v_phase_i = 0;
    logic [23:0] tag_i = 0;
    logic in_ready, busy, ovf_o, err_o, valid_o;
    logic [39:0] slice_ids;
    logic [19:0] code_o;
    logic [23:0] tag_o;
    logic ev_start = 0, ev_valid = 0, ev_bit = 0;
    logic ev_done, ev_err, ev_mv;
    logic [159:0] ev_mask;
    logic [39:0] ev_ids;
    logic [3:0] ev_idx;
    logic [8:0] ev_k;
    logic [3:0] ev_idx_i;
    logic       ev_perr;

    logic cfg_we = 0, cfg_inv_we = 0;
    logic [4:0] cfg_slice = 0, cfg_unit = 0;
    logic [43:0] cfg_w = 0;
    logic [63:0] cfg_inv = 0;
    logic [3:0] cfg_grp = 0;

    digital_top_w dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i),
        .reserved_i(reserved_i), .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
        .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b1), .ev_start_i(ev_start), .ev_valid_i(ev_valid), .ev_bit_i(ev_bit),
        .sar_index_i(ev_idx_i), .sar_qid_i(1'b0),
        .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(ev_done), .ev_idx_o(ev_idx), .ev_k_o(ev_k), .ev_err_o(ev_err),
        .ev_mask_valid_o(ev_mv), .ev_mask_o(ev_mask), .ev_ids_o(ev_ids),
        .ev_proto_err_o(ev_perr),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    always #5 clk = ~clk;

    integer errors = 0, events = 0;

    task automatic wait_tx(input [23:0] tg, output logic [19:0] cd, output logic hit);
        begin
            hit = 1'b0; cd = 20'd0;
            for (int w = 0; w < 20; w++) begin
                @(posedge clk); #1;
                if (valid_o && tag_o == tg) begin cd = code_o; hit = 1'b1; w = 99; end
            end
        end
    endtask

    // 整码模式：拉 valid_i 发一笔
    logic [8:0] ev_mode_sel;
    logic       hit_ev;
    task automatic issue_tx(input [8:0] cc, input [11:0] a2, input [23:0] tg,
                            output logic [19:0] cd);
        begin
            @(negedge clk); valid_i = 1; c_i = cc; adc2_i = a2; tag_i = tg;
            @(negedge clk); valid_i = 0;
            for (int w = 0; w < 12; w++) begin
                @(posedge clk); #1;
                if (valid_o && tag_o == tg) begin cd = code_o; w = 99; end
            end
        end
    endtask

    // 独立期望：按 Python _horizontal_columns / vertical_bits 的语义重建，
    // 用"从起始位置起置 H 个 1"的方式，不复制 RTL 的取模表达式。
    function automatic logic [159:0] exp_mask(input logic [8:0] k,
                                              input logic [3:0] res,
                                              input logic [3:0] hp,
                                              input logic [2:0] vp);
        int H, M, start, idx, col;
        logic [15:0] h;
        logic [7:0]  y;
        begin
            H = k[8:5]; M = k[4:2];
            h = 16'd0; y = 8'd0;
            start = hp % 15; idx = 0;
            // 在 15 个非保留列上按起点轮转，置前 H 个
            for (int t = 0; t < 15; t++) begin
                idx = (start + t) % 15;          // 第 idx 个有效列（0..14）
                // 映射回真实列号：跳过错开 reserved 的那一列
                col = (idx >= res) ? idx + 1 : idx;
                if (t < H) h[col] = 1'b1;
            end
            for (int s_ = 0; s_ < 8; s_++)
                if (((s_ + 8 - vp) % 8) < M) y[s_] = 1'b1;
            for (int s_ = 0; s_ < 8; s_++) begin
                for (int j = 0; j < 16; j++)
                    exp_mask[s_*20 + j] = (j == res) ? y[s_] : h[j];
                exp_mask[s_*20 + 16] = 1'b0;
                exp_mask[s_*20 + 17] = 1'b0;
                exp_mask[s_*20 + 18] = k[1];
                exp_mask[s_*20 + 19] = k[0];
            end
        end
    endfunction
    // 事件模式下不需要 valid_i：run_code 送完 9 位，DUT 自行提交。
    task automatic run_code(input [8:0] c, input [2:0] up);
        integer i, n;
        logic [8:0] exp_p, exp_k;
        begin
            // 只把 D6/D4 摆成对应 U（bit5=D6, bit3=D4）
            // U = 4*D6 + D4；bit5=D6、bit3=D4，其余位清零
            while (busy) @(negedge clk);   // start 受 busy 保护，须等空闲
            @(negedge clk); c_i = c; dither_i = {up[2], 1'b0, up[0], 3'b0};
            ev_start = 1; @(negedge clk); ev_start = 0;
            exp_p = 9'd0; n = 0;
            for (i = 8; i >= 0; i = i - 1) begin
                @(negedge clk); ev_valid = 1; ev_bit = c[i];
                ev_idx_i = 4'(n);                  // n=0..8 对应原码 bit8..bit0
                exp_p = exp_p | (c[i] ? (9'd1 << (4'd8 - n)) : 9'd0);
                exp_k = (exp_p >= {6'b0, up}) ? (exp_p - {6'b0, up}) : 9'd0;
                @(posedge clk); #1;
                if (ev_idx !== n[3:0]) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("IDX c=%0d n=%0d got %0d", c, n, ev_idx);
                end else if (ev_k !== exp_k) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("K c=%0d n=%0d got %0d want %0d", c, n, ev_k, exp_k);
                end else if (ev_mv && (ev_mask !== exp_mask(ev_k, 4'd0, 4'd0, 3'd0))) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("MASK c=%0d n=%0d 不符", c, n);
                end
                events = events + 1; n = n + 1;
            end
            @(negedge clk); ev_valid = 0;
            @(posedge clk); #1;
            if (!ev_done) begin errors = errors + 1; $display("DONE c=%0d 未置位", c); end
        end
    endtask

    integer k, u;
    initial begin
        repeat (4) @(negedge clk); rstn = 1;
        for (k = 0; k < 512; k = k + 1) run_code(k[8:0], 3'd0);
        // U = 4*D6 + D4，只可能是 {0,1,4,5}
        for (u = 0; u < 4; u = u + 1)
            for (k = 0; k < 512; k = k + 53) run_code(k[8:0], {u[1], 1'b0, u[0]});
        // ---- 两路等价：事件模式 vs 整码模式，同 c/adc2/dither 必须同 code20 ----
        begin
            logic [19:0] cd_ev, cd_c;
            integer eq_err = 0, m;
            for (m = 0; m < 64; m = m + 1) begin
                logic [8:0] cc; logic [11:0] a2; logic [23:0] tg;
                cc = (m * 7 + 3) % 512; a2 = (m * 331) % 4096; tg = m[23:0] + 24'd900;
                // 事件模式：置好 adc2/tag，只送事件，**不拉 valid_i**
                force dut.ev_mode_i = 1'b1;
                @(negedge clk); adc2_i = a2; tag_i = tg;
                run_code(cc, 3'd0);                      // 9 个事件，DUT 自行提交
                wait_tx(tg, cd_ev, hit_ev);
                if (!hit_ev) begin
                    eq_err = eq_err + 1;
                    if (eq_err <= 5) $display("NO_TX c=%0d 事件模式未产生输出", cc);
                end
                // 整码模式：传统 valid_i 通路
                force dut.ev_mode_i = 1'b0;
                issue_tx(cc, a2, tg + 24'd1, cd_c);
                release dut.ev_mode_i;
                if (hit_ev && cd_ev !== cd_c) begin
                    eq_err = eq_err + 1;
                    if (eq_err <= 5) $display("EQ c=%0d a2=%0d ev=%0d c=%0d", cc, a2, cd_ev, cd_c);
                end
            end
            $display("EQUIV: 64 组, %0d 不一致", eq_err);
            errors = errors + eq_err;
        end
        $display("RESULT: %0d events, %0d errors", events, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
