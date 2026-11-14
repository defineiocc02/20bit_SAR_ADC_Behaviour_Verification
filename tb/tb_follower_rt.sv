`timescale 1ns/1ps
// 期望前缀由 TB 自己按"已发送的位"累加，不看 RTL 内部、也不看最终码。
module tb_follower_rt;
    logic clk = 0, rstn = 0, start = 0, bv = 0, bit_i = 0;
    logic [2:0] upper = 0;
    logic evv, done, err;
    logic [3:0] evi;
    logic [8:0] kev;
    follower_rt dut(.clk(clk), .reset_n(rstn), .start_i(start), .bit_valid_i(bv),
                    .bit_i(bit_i), .upper_i(upper), .ev_valid_o(evv),
                    .ev_idx_o(evi), .k_ev_o(kev), .done_o(done), .err_o(err));
    always #5 clk = ~clk;

    integer errors = 0, events = 0;

    task automatic run_code(input [8:0] c, input [2:0] up);
        integer i, n;
        logic [8:0] exp_prefix, exp_k;
        begin
            @(negedge clk); start = 1; upper = up;
            @(negedge clk); start = 0;
            exp_prefix = 9'd0; n = 0;
            for (i = 8; i >= 0; i = i - 1) begin
                @(negedge clk);
                bv = 1; bit_i = c[i];
                exp_prefix = exp_prefix | (c[i] ? (9'd1 << (4'd8 - n)) : 9'd0);
                exp_k = (exp_prefix >= {6'b0, up}) ? (exp_prefix - {6'b0, up}) : 9'd0;
                @(posedge clk); #1;              // 寄存输出在此沿后稳定
                if (!evv) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("NO_EVENT c=%0d n=%0d", c, n);
                end else if (evi !== n[3:0]) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("IDX c=%0d got %0d want %0d", c, evi, n);
                end else if (kev !== exp_k) begin
                    errors = errors + 1;
                    if (errors <= 5) $display("K c=%0d n=%0d got %0d want %0d", c, n, kev, exp_k);
                end
                events = events + 1;
                n = n + 1;
            end
            @(negedge clk); bv = 0;
            @(posedge clk); #1;
            if (!done) begin errors = errors + 1; $display("DONE c=%0d 未置位", c); end
        end
    endtask

    integer k, u;
    initial begin
        repeat (3) @(negedge clk); rstn = 1;
        // 512 码 × Upper 0
        for (k = 0; k < 512; k = k + 1) run_code(k[8:0], 3'd0);
        // Upper 变化覆盖：U=0..12
        for (u = 0; u <= 12; u = u + 1)
            for (k = 0; k < 512; k = k + 37) run_code(k[8:0], u[2:0]);
        // 暂停事件：中途插入无事件拍
        @(negedge clk); start = 1; upper = 0; @(negedge clk); start = 0;
        for (k = 8; k >= 0; k = k - 1) begin
            @(negedge clk); bv = 1; bit_i = k[0];
            @(posedge clk); #1;
            @(negedge clk); bv = 0;              // 气泡
            @(posedge clk); #1;
            if (evv) begin errors = errors + 1; $display("BUBBLE 期间仍产生事件"); end
        end
        // reset 中断
        @(negedge clk); start = 1; @(negedge clk); start = 0;
        @(negedge clk); bv = 1; bit_i = 1; @(posedge clk);
        @(negedge clk); bv = 0; rstn = 0; @(negedge clk); rstn = 1;
        @(posedge clk); #1;
        if (done || evv) begin errors = errors + 1; $display("reset 后未清空"); end
        $display("RESULT: %0d events checked, %0d errors", events, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
