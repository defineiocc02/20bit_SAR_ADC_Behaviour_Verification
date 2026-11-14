`timescale 1ns/1ps
// recip_q60（动态 G 的 Q60 倒数单元）独立对拍
//
// 判据：
//   1) 43,758 个真实片组 G + 8 个边界值，inv 与 Python（adi_model.recip_q60）逐位一致
//      —— 两者是同一套整数运算，因此要求 0 误差，不是"近似一致"
//   2) 合同违例必须报 err，不静默输出：G=0、G<2^34（inv 放不进 27 bit）
//   3) 流水吞吐 1 笔/拍：背靠背输入，valid_o 与输入同序
//
// 向量：tb/vec_r18_recip.txt（由 tools/gen_vec_r18.py 生成）
module tb_recip_q60;

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic        rstn = 1'b0;
    logic        valid_i = 1'b0;
    logic [43:0] g_i = 44'd0;
    logic        valid_o;
    logic [26:0] inv_o;
    logic        err_o;

    recip_q60 dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .g_q30(g_i),
        .valid_o(valid_o), .inv_q60(inv_o), .err_o(err_o));

    longint exp_q [$];
    int errors = 0;
    int n_cmp = 0;

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 20) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    // 出口比对
    always @(posedge clk) begin
        if (rstn && valid_o) begin
            longint e;
            if (exp_q.size() == 0) fail("valid_o 但没有待比值");
            else begin
                e = exp_q.pop_front();
                n_cmp = n_cmp + 1;
                if (err_o) fail($sformatf("合同内输入报 err（第 %0d 笔）", n_cmp));
                if (longint'(inv_o) != e)
                    fail($sformatf("inv 不符：got %0d exp %0d", inv_o, e));
            end
        end
    end

    initial begin
        int fd, r;
        longint g, iv;
        int n_vec;
        n_vec = 0;
        fd = $fopen("vec_r18_recip.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_r18_recip.txt");
            $finish;
        end

        repeat (3) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);

        // ---- 1) 逐位对拍（背靠背，1 笔/拍） ----
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d\n", g, iv);
            if (r == 2) begin
                n_vec = n_vec + 1;
                exp_q.push_back(iv);
                valid_i = 1'b1;
                g_i     = g[43:0];
                @(negedge clk);
            end
        end
        $fclose(fd);
        valid_i = 1'b0;
        repeat (4) @(negedge clk);
        if (exp_q.size() != 0) fail($sformatf("还有 %0d 笔未出结果", exp_q.size()));

        // ---- 2) 合同违例 ----
        // G = 0：inv 必须为 0，且 err 必须报
        @(negedge clk);
        valid_i = 1'b1; g_i = 44'd0;
        @(negedge clk);
        valid_i = 1'b0;
        @(posedge clk); #1;
        if (!valid_o)           fail("G=0 未产生 valid_o");
        if (!err_o)             fail("G=0 未报 err");
        if (inv_o !== 27'd0)    fail("G=0 的 inv 不是 0");
        repeat (2) @(negedge clk);

        // G = 2^33（低于合同 2^34）：inv 装不进 27 bit，必须报 err
        @(negedge clk);
        valid_i = 1'b1; g_i = 44'd8589934592;
        @(negedge clk);
        valid_i = 1'b0;
        @(posedge clk); #1;
        if (!valid_o)           fail("G=2^33 未产生 valid_o");
        if (!err_o)             fail("G=2^33（<2^34）未报 err，等于静默截断");
        repeat (2) @(negedge clk);

        // G = 2^34（合同下边界）：err 必须为 0
        @(negedge clk);
        valid_i = 1'b1; g_i = 44'd17179869184;
        @(negedge clk);
        valid_i = 1'b0;
        @(posedge clk); #1;
        if (!valid_o)           fail("G=2^34 未产生 valid_o");
        if (err_o)              fail("G=2^34（合同内）误报 err");
        repeat (2) @(negedge clk);

        $display("---------------------------------------------------------------");
        $display("recip_q60: %0d vectors compared, %0d errors", n_cmp, errors);
        if (errors == 0) $display("RESULT: PASS (%0d inverses bit-exact vs Python)", n_cmp);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
