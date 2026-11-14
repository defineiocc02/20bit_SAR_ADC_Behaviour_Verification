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
    logic ev_done, ev_err;
    logic [3:0] ev_idx;
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
        .ev_done_o(ev_done), .ev_idx_o(ev_idx), .ev_k_o(ev_k), .ev_err_o(ev_err),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    always #5 clk = ~clk;

    integer errors = 0, events = 0;
    task automatic run_code(input [8:0] c, input [2:0] up);
        integer i, n;
        logic [8:0] exp_p, exp_k;
        begin
            // 只把 D6/D4 摆成对应 U（bit5=D6, bit3=D4）
            // U = 4*D6 + D4；bit5=D6、bit3=D4，其余位清零
            @(negedge clk); c_i = c; dither_i = {up[2], 1'b0, up[0], 3'b0};
            ev_start = 1; @(negedge clk); ev_start = 0;
            exp_p = 9'd0; n = 0;
            for (i = 8; i >= 0; i = i - 1) begin
                @(negedge clk); ev_valid = 1; ev_bit = c[i];
                exp_p = exp_p | (c[i] ? (9'd1 << (4'd8 - n)) : 9'd0);
                exp_k = (exp_p >= {6'b0, up}) ? (exp_p - {6'b0, up}) : 9'd0;
                @(posedge clk); #1;
                if (ev_idx !== n[3:0]) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("IDX c=%0d n=%0d got %0d", c, n, ev_idx);
                end else if (ev_k !== exp_k) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("K c=%0d n=%0d got %0d want %0d", c, n, ev_k, exp_k);
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
        $display("RESULT: %0d events, %0d errors", events, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
