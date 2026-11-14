`timescale 1ns/1ps
// 验证 digital_top_w 里的实时 Follower 事件口：
//   送入 9 位判决流，检查每个事件的 ev_k_o 与 ev_mismatch_o，最后 ev_done_o。
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
    logic ev_done, ev_mismatch;
    logic [2:0] ev_idx;
    logic [8:0] ev_k;

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
        .ev_start_i(ev_start), .ev_valid_i(ev_valid), .ev_bit_i(ev_bit),
        .ev_done_o(ev_done), .ev_idx_o(ev_idx), .ev_k_o(ev_k),
        .ev_mismatch_o(ev_mismatch),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    always #5 clk = ~clk;

    integer errors = 0, k, i;
    initial begin
        repeat (3) @(negedge clk); rstn = 1;
        for (k = 0; k < 512; k = k + 1) begin
            @(negedge clk); c_i = k[8:0]; dither_i = 6'd0;
            ev_start = 1; @(negedge clk); ev_start = 0;
            // 逐位送入；每拍在时钟沿后检查（此时 idx 与 acc 已同步）
            for (i = 8; i >= 0; i = i - 1) begin
                @(negedge clk);
                ev_valid = 1; ev_bit = c_i[i];
                @(posedge clk); #1;
                if (ev_mismatch) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("MISMATCH c=%0d idx=%0d k=%0d want=%0d",
                                               k, ev_idx, ev_k, ((c_i >> (8-ev_idx)) << (8-ev_idx)));
                end
            end
            @(negedge clk); ev_valid = 0;
            #1;
            if (!ev_done && k == 511) begin
                errors = errors + 1; $display("done 未置位");
            end
        end
        $display("RESULT: 512 codes x 9 events, %0d mismatches", errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
