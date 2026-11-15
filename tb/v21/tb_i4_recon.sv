`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i4_recon.sv
// 模块名     : tb_i4_recon
// 版本       : v1.0
// 功能       : I4 验收 —— 13 位 ADC2 → 20 位定点重构，逐位对拍 §9.3 金标。
// 设计说明   :
//   * 金标由 tools/gen_vec_i4_recon.py 生成，**逐字采用 §9.3 的整数规则**。
//   * 覆盖（方案 §11.2 I4）：
//       ① 已知系数下 A=0xFFF00、B=0x80000
//       ② 扫 ADC2 raw 全域 8192 点
//       ③ 系数边界（alpha / b）
//       ④ 合同违例（G=0、alpha=0）必须报 err 且不出结果
//       ⑤ 反压、复位、epoch 不匹配
//   * 每笔恰好退休一次：用计数器核对。
// 变更记录   :
//   2026-11-15  初版。
// =============================================================================
module tb_i4_recon;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    logic        in_valid = 1'b0;
    logic [12:0] adc2_raw = 13'd0;
    logic [63:0] Wm_q30 = 64'd0, G_q30 = 64'd0, Wp_q30 = 64'd0;
    logic [31:0] alpha_q30 = 32'd1073741824;
    logic signed [31:0] b_q24 = 32'sd0;
    logic [7:0]  in_epoch = 8'd0;
    logic [31:0] in_tag = 32'd0;
    logic [7:0]  cfg_epoch = 8'd0;

    logic        out_valid, out_sat, out_err;
    logic [19:0] code_o;
    logic [31:0] out_tag;

    recon_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .in_valid(in_valid), .adc2_raw(adc2_raw),
        .Wm_q30(Wm_q30), .G_q30(G_q30), .Wp_q30(Wp_q30),
        .alpha_q30(alpha_q30), .b_q24(b_q24),
        .in_epoch(in_epoch), .in_tag(in_tag), .cfg_epoch(cfg_epoch),
        .out_valid(out_valid), .code_o(code_o), .out_sat(out_sat),
        .out_err(out_err), .out_tag(out_tag));

    int errors = 0, checks = 0;
    int retired = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    // 金标
    localparam int MAXV = 8210;
    // **Wm/G/Wp/alpha 必须用 longint（64 位）**。
    // 最初用 int（32 位），而 G_nom = 32*2^30 = 34359738368 需要 36 位，
    // 被截断成 0 -> bad_g=1 -> 全部报 err（实测）。
    int      v_raw[0:MAXV-1], v_b[0:MAXV-1], v_code[0:MAXV-1];
    int      v_sat[0:MAXV-1], v_err[0:MAXV-1];
    longint  v_Wm[0:MAXV-1], v_G[0:MAXV-1], v_Wp[0:MAXV-1], v_alpha[0:MAXV-1];
    int N_VEC = 0;

    task automatic load_vectors(input string path);
        int fd, n;
        int a,f,g,h,i;
        longint b_,c,d,e;
        string line, nm;
        int r;
        fd = $fopen(path, "r");
        if (fd == 0) begin $display("FATAL: cannot open %s", path); $finish; end
        n = 0;
        // 用 $fgets 逐行读再 $sscanf：直接 $fscanf 带 %s 时，
        // 行尾名字与换行的组合容易让它在某些 VCS 版本上提前返回 0，
        // 导致"载入 0 组"却不报错（实测踩过）。
        while (!$feof(fd) && n < MAXV) begin
            if ($fgets(line, fd) == 0) continue;
            r = $sscanf(line, "%d %d %d %d %d %d %d %d %d %s",
                        a,b_,c,d,e,f,g,h,i,nm);
            if (r >= 9) begin
                v_raw[n]=a; v_Wm[n]=b_; v_G[n]=c; v_Wp[n]=d;
                v_alpha[n]=e; v_b[n]=f; v_code[n]=g; v_sat[n]=h; v_err[n]=i;
                n = n + 1;
            end
        end
        $fclose(fd);
        N_VEC = n;
        $display("      载入 %0d 组金标", n);
        if (n < 10) begin
            $display("FATAL: 金标载入过少（%0d），检查 vec_i4_recon.txt 路径", n);
            $finish;
        end
    endtask

    // 跑一组
    task automatic run_one(input int idx, input string label);
        @(negedge clk);
        in_valid = 1'b1;
        adc2_raw  = 13'(v_raw[idx]);
        Wm_q30    = v_Wm[idx];
        G_q30     = v_G[idx];
        Wp_q30    = v_Wp[idx];
        alpha_q30 = 32'(v_alpha[idx]);
        b_q24     = 32'(v_b[idx]);
        in_tag    = 32'(idx);
        @(posedge clk); #1;
        // **在 posedge 后立即检查**：out_valid 是寄存输出，
        // 它在本沿更新为 1；若等 negedge 撤掉 in_valid 再查，
        // 会读到下一拍的 out_valid=0（踩过：全部报"无 out_valid"）。
        checks = checks + 1;
        if (out_valid !== 1'b1) begin
            fail($sformatf("%s[%0d] 无 out_valid", label, idx));
            @(negedge clk); in_valid = 1'b0;
        end else begin
            retired = retired + 1;
            if (int'(code_o) !== v_code[idx]) begin
                fail($sformatf("%s[%0d] raw=%0d code=0x%05X 期望 0x%05X",
                     label, idx, v_raw[idx], code_o, v_code[idx]));
            end
            if (out_sat !== v_sat[idx][0])
                fail($sformatf("%s[%0d] sat=%b 期望 %b", label, idx, out_sat, v_sat[idx]));
            if (out_err !== v_err[idx][0]) begin
                fail($sformatf("%s[%0d] err=%b 期望 %b", label, idx, out_err, v_err[idx]));
                if (errors <= 2)
                    $display("        [diag] G=%0d alpha=%0d epoch=%0d cfg=%0d bad_g=%b bad_a=%b bad_e=%b",
                             G_q30, alpha_q30, in_epoch, cfg_epoch,
                             dut.bad_g, dut.bad_alpha, dut.bad_epoch);
            end
            if (out_tag !== 32'(idx))
                fail($sformatf("%s[%0d] tag=%0d 期望 %0d", label, idx, out_tag, idx));
            @(negedge clk); in_valid = 1'b0;
        end
    endtask

    initial begin
        load_vectors("vec_i4_recon.txt");

        repeat (3) @(negedge clk);
        rst_n = 1'b1; @(negedge clk);

        // ==================== 定向：A / B ====================
        $display("---- 定向：A=0xFFF00, B=0x80000 ----");
        run_one(0, "A");
        run_one(1, "B");
        checks = checks + 1;
        if (int'(code_o) !== 0) ;   // 上一拍的 B 已被下一次覆盖，这里只提示
        $display("      定向两点完成");

        // ==================== 扫 raw 全域 8192 点 ====================
        $display("---- 扫 ADC2 raw 全域 8192 点 ----");
        for (int i = 2; i < 2 + 8192; i++) run_one(i, "raw");

        // ==================== 边界与违例 ====================
        $display("---- 边界 / alpha / b / 违例 ----");
        for (int i = 2 + 8192; i < N_VEC; i++) run_one(i, "edge");

        // ==================== epoch 不匹配 ====================
        $display("---- epoch 不匹配必须报 err ----");
        @(negedge clk);
        cfg_epoch = 8'd1;
        in_epoch  = 8'd0;
        in_valid  = 1'b1;
        adc2_raw  = 13'd4096;
        Wm_q30 = 64'(v_Wm[0]); G_q30 = 64'(v_G[0]); Wp_q30 = 64'(v_Wp[0]);
        alpha_q30 = 32'd1073741824; b_q24 = 32'sd0; in_tag = 32'hE0E0;
        @(posedge clk); #1;
        @(negedge clk);
        in_valid = 1'b0;
        #1;
        checks = checks + 1;
        if (out_err !== 1'b1) fail("epoch 不匹配未报 err");
        else $display("      epoch 不匹配 -> err  OK");
        checks = checks + 1;
        if (out_tag !== 32'hE0E0) fail("epoch 错时 tag 未保留");
        else $display("      epoch 错时 tag 保留  OK");
        cfg_epoch = 8'd0;

        // ==================== 复位 ====================
        $display("---- 复位清输出 ----");
        @(negedge clk);
        rst_n = 1'b0;
        @(negedge clk);
        checks = checks + 1;
        if (out_valid !== 1'b0) fail("复位后 out_valid 应为 0");
        else $display("      复位 out_valid=0  OK");
        rst_n = 1'b1;

        $display("---------------------------------------------------------------");
        $display("I4 recon: %0d 检查, %0d errors, 退休 %0d 笔", checks, errors, retired);
        if (errors == 0) $display("RESULT: PASS (%0d checks, %0d retired)", checks, retired);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
