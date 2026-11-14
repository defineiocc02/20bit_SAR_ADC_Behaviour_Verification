`timescale 1ns/1ps
// DEM 相位覆盖：9 片组 × 16 保留列 × 15 横向 × 8 纵向
// 每笔：start(冻结相位) -> 9 事件 -> 逐事件检查 ev_mask_o 与独立期望
module tb_dem_cov;
    logic clk=0, rstn=0, valid_i=0, stall_i=0;
    logic [8:0] c_i=0; logic [11:0] adc2_i=0; logic [5:0] dither_i=0;
    logic [3:0] reserved_i=0, h_phase_i=0; logic [2:0] v_phase_i=0;
    logic [23:0] tag_i=0;
    logic in_ready, busy, ovf_o, err_o, valid_o, perr, rdy, abort_o, done;
    logic [23:0] abort_tag;
    logic [39:0] slice_ids; logic [19:0] code_o; logic [23:0] tag_o;
    logic ev_start=0, ev_valid=0, ev_bit=0; logic [3:0] ev_idx=0;
    logic [8:0] kev; logic [2:0] evi_o;
    logic [159:0] ev_mask; logic ev_mv;
    logic cfg_we=0, cfg_inv_we=0; logic [4:0] cfg_slice=0, cfg_unit=0;
    logic [43:0] cfg_w=0; logic [63:0] cfg_inv=0; logic [3:0] cfg_grp=0;

    digital_top_w dut(.clk(clk), .reset_n(rstn), .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i), .reserved_i(reserved_i),
        .h_phase_i(h_phase_i), .v_phase_i(v_phase_i), .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b1), .ev_start_i(ev_start), .ev_valid_i(ev_valid), .ev_bit_i(ev_bit),
        .sar_index_i(ev_idx), .sar_qid_i(1'b0), .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(done), .ev_idx_o(evi_o), .ev_k_o(kev), .ev_err_o(),
        .ev_proto_err_o(perr), .ev_ready_o(rdy), .ev_abort_o(abort_o),
        .ev_abort_tag_o(abort_tag),
        .ev_mask_valid_o(ev_mv), .ev_mask_o(ev_mask), .ev_ids_o(),
        .busy_o(busy), .slice_ids_o(slice_ids), .code_o(code_o),
        .overflow_o(ovf_o), .err_o(err_o), .valid_o(valid_o), .tag_o(tag_o));
    always #5 clk = ~clk;

    integer errors = 0, combos = 0, evs = 0;

    function automatic logic [159:0] exp_mask(input logic [8:0] k,
                                              input logic [3:0] res,
                                              input logic [3:0] hp,
                                              input logic [2:0] vp);
        int H, M, start, t, idx, col;
        logic [15:0] h; logic [7:0] y;
        begin
            H = k[8:5]; M = k[4:2]; h = 16'd0; y = 8'd0; start = hp % 15;
            for (t = 0; t < 15; t++) begin
                idx = (start + t) % 15;
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

    task automatic one_combo(input int g, input int res, input int hp, input int vp,
                             input int code, input int tg);
        integer n, try_n; logic ok;
        begin
            reserved_i = res[3:0]; h_phase_i = hp[3:0]; v_phase_i = vp[2:0];
            tag_i = tg[23:0]; c_i = code[8:0];
            ok = 0;
            for (try_n = 0; try_n < 8 && !ok; try_n = try_n + 1) begin
                while (!rdy) @(negedge clk);
                ev_start = 1;
                @(negedge clk);
                ok = dut.ev_active_q;      // 读寄存器判定
                ev_start = 0;
            end
            if (!ok) begin
                errors = errors + 1;
                $display("COV_START_REJ g=%0d res=%0d hp=%0d vp=%0d", g, res, hp, vp);
                disable one_combo;
            end
            for (n = 0; n < 9; n = n + 1) begin
                @(negedge clk); ev_valid = 1; ev_bit = code[8-n]; ev_idx = n[3:0];
                @(posedge clk); #1;
                evs = evs + 1;
                if (ev_mv && (ev_mask !== exp_mask(kev, res[3:0], hp[3:0], vp[2:0]))) begin
                    errors = errors + 1;
                    if (errors <= 6)
                        $display("COV_MASK g=%0d res=%0d hp=%0d vp=%0d code=%0d n=%0d",
                                 g, res, hp, vp, code, n);
                end
            end
            @(negedge clk); ev_valid = 0; ev_idx = 0;
            combos = combos + 1;
            while (busy) @(posedge clk);
        end
    endtask

    integer g, res, hp, vp, k, seed;
    initial begin
        repeat (4) @(negedge clk); rstn = 1; @(negedge clk);
        // 层 1：9 片组 × 16 保留列（hp=vp=0）
        for (g = 0; g < 9; g = g + 1)
            for (res = 0; res < 16; res = res + 1)
                one_combo(g, res, 0, 0, (g*16+res) % 512, 100 + g*16 + res);
        $display("TIER1 done: combos=%0d errors=%0d", combos, errors);
        // 层 2：15 横向 × 8 纵向（g=0,res=0）
        for (hp = 0; hp < 15; hp = hp + 1)
            for (vp = 0; vp < 8; vp = vp + 1)
                one_combo(0, 0, hp, vp, (hp*8+vp) % 512, 300 + hp*8 + vp);
        $display("TIER2 done: combos=%0d errors=%0d", combos, errors);
        // 层 3：随机全组合 40 组
        seed = 7;
        for (k = 0; k < 40; k = k + 1) begin
            g = (seed*7 + k*13) % 9; res = (seed*11 + k*5) % 16;
            hp = (seed*3 + k*17) % 15; vp = (seed*5 + k*19) % 8;
            one_combo(g, res, hp, vp, (k*37) % 512, 600 + k);
        end
        $display("COV_RESULT: combos=%0d events=%0d errors=%0d", combos, evs, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
