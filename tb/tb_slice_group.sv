`timescale 1ns/1ps
// 1) 9 组：sid_base 与 8 个片 ID 必须等于 (8+8g+i)%18
// 2) 长跑 66000 笔：grp_cnt 必须在 0..8 环上，回绕后不与片 ID 错位
module tb_slice_group;
    logic clk = 0, rstn = 0;
    logic valid_i = 0, stall_i = 0;
    logic [8:0] c_i = 0;
    logic [11:0] adc2_i = 0;
    logic [5:0] dither_i = 0;
    logic [3:0] reserved_i = 0, h_phase_i = 0;
    logic [2:0] v_phase_i = 0;
    logic [23:0] tag_i = 0;
    logic in_ready, busy, ovf_o, err_o, valid_o, perr;
    logic [39:0] slice_ids;
    logic [19:0] code_o;
    logic [23:0] tag_o;
    logic cfg_we = 0, cfg_inv_we = 0;
    logic [4:0] cfg_slice = 0, cfg_unit = 0;
    logic [43:0] cfg_w = 0;
    logic [63:0] cfg_inv = 0;
    logic [3:0] cfg_grp = 0;

    digital_top_w dut (
        .clk(clk), .reset_n(rstn), .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i),
        .reserved_i(reserved_i), .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
        .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b0), .ev_start_i(1'b0), .ev_valid_i(1'b0), .ev_bit_i(1'b0),
        .sar_index_i(4'd0), .sar_qid_i(1'b0), .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(), .ev_idx_o(), .ev_k_o(), .ev_err_o(), .ev_proto_err_o(),
        .ev_mask_valid_o(), .ev_mask_o(), .ev_ids_o(),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    always #5 clk = ~clk;

    integer errors = 0;
    integer n;
    integer g, i, exp_base, exp_id;

    initial begin
        repeat (4) @(negedge clk); rstn = 1;
        // ---- 1) 9 组映射 ----
        for (n = 0; n < 9; n = n + 1) begin
            // 等空闲
            while (busy) @(negedge clk);
            @(negedge clk); valid_i = 1; c_i = 9'd100; adc2_i = 12'd1000; tag_i = n[23:0];
            @(negedge clk); valid_i = 0;
            #1;
            g = dut.grp_cnt;                       // 该笔使用的组号
            exp_base = ((g + 1) * 8) % 18;
            if (dut.sid_base !== exp_base[31:0]) begin
                errors = errors + 1;
                $display("SID_BASE g=%0d got %0d want %0d", g, dut.sid_base, exp_base);
            end
            for (i = 0; i < 8; i = i + 1) begin
                exp_id = (exp_base + i) % 18;
                if (slice_ids[i*5 +: 5] !== exp_id[4:0]) begin
                    errors = errors + 1;
                    if (errors <= 6) $display("SID g=%0d i=%0d got %0d want %0d",
                                              g, i, slice_ids[i*5 +: 5], exp_id);
                end
            end
            // 排空
            while (busy) @(negedge clk);
        end

        // ---- 2) 长跑 66000 笔，检查回绕 ----
        for (n = 0; n < 66000; n = n + 1) begin
            while (!in_ready) @(negedge clk);
            @(negedge clk); valid_i = 1; c_i = n[8:0]; adc2_i = n[11:0]; tag_i = n[23:0];
            @(negedge clk); valid_i = 0;
            if ((n % 9) == 8) begin
                #1;
                if (dut.grp_cnt !== 4'd0) begin
                    errors = errors + 1;
                    if (errors <= 10) $display("GRP_WRAP n=%0d grp=%0d", n, dut.grp_cnt);
                end
            end
        end
        $display("LONG: 66000 transactions done, sample_cnt=%0d grp_cnt=%0d",
                 dut.sample_cnt, dut.grp_cnt);
        $display("RESULT: %0d errors", errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
