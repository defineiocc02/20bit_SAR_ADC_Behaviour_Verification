//===========================================================================
// sinc5_decim_tb.sv: 五级 CIC 的有符号直流 smoke 测试。
//
// 使用 D=4、输入 1000，检查输出存在及建立后的直流数值；启动输出视为瞬态。
// 具体工具执行结果记录在运行日志，本测试不证明物理滤波建立时序。
//===========================================================================
`timescale 1ns/1ps

module sinc5_decim_tb;
    localparam int IN_W = 20;
    localparam logic [3:0] DEC_SEL = 4'd1;   // D = 2^(1+1) = 4

    logic clk = 0;
    always #1 clk = ~clk;       // 500 MHz 功能仿真时钟，不构成工作频率验收。

    logic rst_n = 0;
    logic filter_sync = 0;
    logic valid_in = 0;
    logic [IN_W-1:0] data_in = 0;
    logic [IN_W-1:0] data_out;
    logic valid_out;
    logic settled;

    sinc5_decim #(.IN_W(IN_W)) dut (
        .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync),
        .valid_in(valid_in), .data_in(data_in), .dec_sel(DEC_SEL),
        .data_out(data_out), .valid_out(valid_out), .settled(settled)
    );

    int errors = 0;
    int outs = 0;

    task automatic push(input int v);
        begin
            data_in = v[IN_W-1:0]; valid_in = 1'b1;
            @(posedge clk); valid_in = 1'b0;
        end
    endtask

    initial begin
        repeat (4) @(posedge clk);
        rst_n = 1;

        // 输入直流 1000，充分建立后的抽取结果须保持 1000。
        for (int i = 0; i < 64; i++) push(1000);
        if (!settled) begin
            $display("FAIL: settled low after 64 samples (target 26)");
            errors++;
        end

        // 排空流水后核对末次结果，避免只检查启动瞬态。
        repeat (8) @(posedge clk);
        if (outs == 0) begin
            $display("FAIL: no valid_out");
            errors++;
        end else if (data_out < 999 || data_out > 1001) begin
            $display("FAIL: DC output=%0d exp=1000", data_out);
            errors++;
        end else begin
            $display("PASS: DC output=%0d", data_out);
        end

        if (errors == 0) $display("TB PASS");
        else             $display("TB FAIL: %0d", errors);
        $finish;
    end

    always_ff @(posedge clk) begin
        if (valid_out) begin
            outs++;
            // 建立门限参考手册 p.69 的 2+5D+4 数值，实际 settled 由 RTL 的
            // 数字计数产生。settled=0 时输出仍为瞬态，不与稳态值 1000 比较。
            if (settled && (data_out < 999 || data_out > 1001)) begin
                $display("FAIL: decimated output=%0d exp=1000 (settled)", data_out);
                errors++;
            end
        end
    end

endmodule
