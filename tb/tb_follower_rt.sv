`timescale 1ns/1ps
module tb_follower_rt;
    logic clk = 0, rstn = 0, start = 0, bv = 0, bit_i;
    logic [2:0] upper = 0;
    logic evv, done;
    logic [2:0] evi;
    logic [8:0] kev;
    follower_rt dut(.clk(clk), .reset_n(rstn), .start_i(start), .bit_valid_i(bv),
                    .bit_i(bit_i), .upper_i(upper), .ev_valid_o(evv),
                    .ev_idx_o(evi), .k_ev_o(kev), .done_o(done));
    always #5 clk = ~clk;

    integer errors = 0, n = 0;
    task automatic run_code(input [8:0] c);
        integer i;
        logic [8:0] want;
        begin
            @(negedge clk); start = 1; @(negedge clk); start = 0;
            for (i = 8; i >= 0; i = i - 1) begin
                @(negedge clk);
                bv = 1; bit_i = c[i];
                want = (i == 0) ? c : ((c >> i) << i);
                #1;
                if (kev !== want) begin
                    $display("MISMATCH c=%0d ev=%0d got %0d want %0d", c, 8-i, kev, want);
                    errors = errors + 1;
                end
                n = n + 1;
            end
            @(negedge clk); bv = 0;
        end
    endtask

    integer k;
    initial begin
        repeat (3) @(negedge clk); rstn = 1;
        for (k = 0; k < 512; k = k + 1) run_code(k[8:0]);
        $display("RESULT: %0d events checked, %0d mismatches", n, errors);
        if (errors == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
