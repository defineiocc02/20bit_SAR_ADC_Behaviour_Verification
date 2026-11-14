`timescale 1ns/1ps
// recon_d5_pipe2 功能对拍：
//   1) 与 Python golden（run_transaction 的 code20）比 —— 主判据
//   2) 与组合版 recon_d5_w 比 —— 证明三级流水只改了延迟、没改数值
// 向量格式：wm_q30 g_q30 adc2 dither code20_golden
module tb_recon_pipe2;
    logic        clk = 0, rstn = 0;
    logic [43:0] wm, g;
    logic [11:0] a2;
    logic [5:0]  dw;
    logic [19:0] code, code_w3;
    logic        ovf, ovf_w3;
    integer      fd, r, n, err_g, err_r, n_ovf;
    longint      wme, ge;
    integer      ae, de, ce;

    recon_d5_pipe2 dut (
        .clk(clk), .reset_n(rstn), .wm_q30(wm), .g_q30(g),
        .adc2_i(a2), .dither_i(dw), .code_o(code), .overflow_o(ovf)
    );
    recon_d5_w u_ref (
        .wm_q30(wm), .g_q30(g), .adc2_i(a2), .dither_i(dw),
        .code_o(code_w3), .overflow_o(ovf_w3)
    );

    always #5 clk = ~clk;

    initial begin
        err_g = 0; err_r = 0; n = 0; n_ovf = 0;
        #12 rstn = 1;
        fd = $fopen("vec_recon_pipe2.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_recon_pipe2.txt");
            $finish;
        end
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d %d\n", wme, ge, ae, de, ce);
            if (r == 5) begin
                @(negedge clk);
                wm = wme[43:0]; g = ge[43:0]; a2 = ae[11:0]; dw = de[5:0];
                repeat (3) @(negedge clk);          // 3 拍延迟
                n = n + 1;
                if (ovf) n_ovf = n_ovf + 1;
                if (code !== ce[19:0]) begin
                    err_g = err_g + 1;
                    if (err_g <= 5)
                        $display("GOLDEN MISMATCH wm=%0d g=%0d adc=%0d dith=%0d got=%0d exp=%0d",
                                 wme, ge, ae, de, code, ce);
                end
                if (code !== code_w3) begin
                    err_r = err_r + 1;
                    if (err_r <= 5)
                        $display("REF MISMATCH wm=%0d g=%0d adc=%0d dith=%0d pipe2=%0d recon_d5_w=%0d",
                                 wme, ge, ae, de, code, code_w3);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d golden mismatches, %0d recon_d5_w mismatches, %0d overflow cases",
                 n, err_g, err_r, n_ovf);
        if (err_g == 0 && err_r == 0) $display("PASS");
        else                          $display("FAIL");
        $finish;
    end
endmodule
