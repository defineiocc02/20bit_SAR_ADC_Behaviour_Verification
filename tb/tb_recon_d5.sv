`timescale 1ns/1ps
// 逐行十进制：k adc2 dither expected_code
module tb_recon_d5;
    logic signed [8:0]  k;
    logic        [11:0] adc2;
    logic        [5:0]  dither;
    logic        [19:0] code;
    logic               ovf;
    integer fd, r, n, err;
    integer kk, aa, dd, ee;

    recon_d5 dut (.k_i(k), .adc2_i(adc2), .dither_i(dither),
                  .code_o(code), .overflow_o(ovf));

    initial begin
        err = 0; n = 0; fd = $fopen("vec.txt", "r");
        if (fd == 0) begin $display("FATAL: cannot open vec.txt"); $finish; end
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d\n", kk, aa, dd, ee);
            if (r == 4) begin
                k = kk[8:0]; adc2 = aa[11:0]; dither = dd[5:0]; #1;
                n = n + 1;
                if (code !== ee[19:0]) begin
                    err = err + 1;
                    if (err <= 5) $display("MISMATCH n=%0d k=%0d adc=%0d d=%0d got=%0d exp=%0d",
                                           n, kk, aa, dd, code, ee);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches", n, err);
        if (err == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
