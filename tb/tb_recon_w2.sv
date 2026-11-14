`timescale 1ns/1ps
// recon_d5_w2 功能对拍
//   1) 与向量里的期望值比 —— 主判据。期望值由 tools/gen_vec_recon_w2.py 用
//      **同一套倒数公式** ((6Wm-3G)*inv_g_q60)>>36 生成，不是精确除法。
//   2) 与组合版 recon_d5_w 比 —— 只是参考计数：两者的 D_eff 允许差 1~2 个
//      Q24 单位（inv 量化误差），所以这里只报差多少，不作为 PASS 判据。
// 向量格式：wm_q30 g_q30 inv_g_q60 adc2 dither code20_expected
module tb_recon_w2;
    logic        clk = 0, rstn = 0;
    logic [43:0] wm, g;
    logic [63:0] inv;
    logic [11:0] a2;
    logic [5:0]  dw;
    logic [19:0] code, code_w;
    logic        ovf, ovf_w, err_in;

    integer      fd, r, n, err, n_ovf, n_diff_w, max_delta, delta;
    longint      wme, ge, inve, ae, de, ce;

    recon_d5_w2 dut (
        .clk(clk), .reset_n(rstn), .wm_q30(wm), .g_q30(g), .inv_g_q60(inv),
        .adc2_i(a2), .dither_i(dw), .code_o(code), .overflow_o(ovf), .err_o(err_in)
    );
    recon_d5_w u_ref (
        .wm_q30(wm), .g_q30(g), .adc2_i(a2), .dither_i(dw),
        .code_o(code_w), .overflow_o(ovf_w)
    );

    always #5 clk = ~clk;

    initial begin
        err = 0; n = 0; n_ovf = 0; n_diff_w = 0; max_delta = 0;
        #12 rstn = 1;
        fd = $fopen("vec_recon_w2.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_recon_w2.txt");
            $finish;
        end
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d %d %d\n", wme, ge, inve, ae, de, ce);
            if (r == 6) begin
                @(negedge clk);
                wm = wme[43:0]; g = ge[43:0]; inv = inve[63:0];
                a2 = ae[11:0];  dw = de[5:0];
                repeat (3) @(negedge clk);          // 3 拍延迟（S1/S2/S3 各一拍）
                n = n + 1;
                if (ovf) n_ovf = n_ovf + 1;
                if (code !== ce[19:0]) begin
                    // PROBE: 打印 RTL 各级中间量
                    $display("PROBE wm=%0d g=%0d inv=%0d a=%0d w=%0d | num=%0d d=%0d x=%0d q=%0d code=%0d exp=%0d",
                             wm, g, inv, a2, dw,
                             dut.num, dut.d_q24, dut.x_q24, dut.q, code, ce);
                    err = err + 1;
                    if (err <= 5)
                        $display("MISMATCH wm=%0d g=%0d inv=%0d adc=%0d dith=%0d got=%0d exp=%0d",
                                 wme, ge, inve, ae, de, code, ce);
                end
                if (code !== code_w) begin
                    n_diff_w = n_diff_w + 1;
                    delta = (code > code_w) ? (code - code_w) : (code_w - code);
                    if (delta > max_delta) max_delta = delta;
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches, %0d overflow cases", n, err, n_ovf);
        $display("REF   : vs recon_d5_w exact-division: %0d differ, max |delta| = %0d LSB20",
                 n_diff_w, max_delta);
        if (err == 0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
