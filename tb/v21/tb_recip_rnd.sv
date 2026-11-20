`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_recip_rnd.sv
// 模块名     : tb_recip_rnd
// 版本       : v1.0
// 功能       : 验证 recip_rnd_v21 与精确 RND(2^60,G) 逐位一致。
// 设计说明   :
//   * 金标由 tools/gen_vec_recip_rnd.py 用精确整数生成，不依赖 RTL。
//   * 向量覆盖：合同域边界、每个 b 区间端点、名义值 ±400 步、2 万随机点；
//     另含域外点（G=0/1/2^34-1/2^36+1），要求 err=1 且 i=0。
//   * 模块延迟 4 拍（recip_q60 两级 + 修正两级），出口用队列比对，
//     每笔按到达顺序核 tag（此处无 tag，按顺序核 i 与 err）。
// 变更记录   :
//   2026-11-16  初版。
// =============================================================================
module tb_recip_rnd;

    logic clk = 0;
    always #5 clk = ~clk;

    logic        rst_n = 0;
    logic        valid_i = 0;
    logic [43:0] g_q30 = 44'd0;
    logic        valid_o;
    logic [26:0] i_q60;
    logic        err_o;

    recip_rnd_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .valid_i(valid_i), .g_q30(g_q30),
        .valid_o(valid_o), .i_q60(i_q60), .err_o(err_o));

    int errors = 0;
    int n_cmp = 0;

    longint exp_i[$];
    bit     exp_e[$];

    task automatic fail(input string m);
        errors = errors + 1;
        if (errors <= 20) $display("[%0t] ERROR: %s", $time, m);
    endtask

    // 出口比对
    always @(posedge clk) begin
        if (rst_n && valid_o) begin
            longint e;
            bit     ee;
            if (exp_i.size() == 0) fail("valid_o 但没有待比值");
            else begin
                e  = exp_i.pop_front();
                ee = exp_e.pop_front();
                n_cmp = n_cmp + 1;
                if (longint'(i_q60) != e) begin
                    fail($sformatf("i 不符：got %0d exp %0d（第 %0d 笔）",
                                   i_q60, e, n_cmp));
                end
                if (err_o !== ee)
                    fail($sformatf("err 不符：got %0b exp %0b（第 %0d 笔）",
                                   err_o, ee, n_cmp));
            end
        end
    end

    initial begin
        int fd, r;
        longint g, iv;
        int ev;
        reg [1023:0] vpath;

        if (!$value$plusargs("VEC=%s", vpath)) vpath = "tb/v21/vec_recip_rnd.txt";
        fd = $fopen(vpath, "r");
        if (fd == 0) begin
            $display("FATAL: cannot open %0s", vpath);
            $finish;
        end
        $display("INFO: vector file = %0s", vpath);

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);

        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d\n", g, iv, ev);
            if (r == 3) begin
                exp_i.push_back(iv);
                exp_e.push_back(ev[0]);
                @(negedge clk);
                valid_i = 1'b1;
                g_q30   = g[43:0];
            end
        end
        $fclose(fd);

        // 等最后一笔走完流水（4 拍）后再判队列
        begin
            int guard;
            guard = 0;
            @(negedge clk);
            valid_i = 1'b0;
            while (exp_i.size() != 0 && guard < 32) begin
                @(negedge clk);
                guard = guard + 1;
            end
        end
        if (exp_i.size() != 0)
            fail($sformatf("还有 %0d 笔未出结果", exp_i.size()));

        // 复位清输出
        rst_n = 1'b0;
        @(negedge clk);
        if (valid_o !== 1'b0) fail("复位后 valid_o 应为 0");
        rst_n = 1'b1;

        $display("---------------------------------------------------------------");
        $display("recip_rnd: %0d vectors compared, %0d errors", n_cmp, errors);
        if (errors == 0)
            $display("RESULT: PASS (%0d RND(2^60,G) bit-exact)", n_cmp);
        else
            $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
