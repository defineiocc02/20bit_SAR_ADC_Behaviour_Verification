//===========================================================================
// sinc5_decim_tb.sv -- smoke test for the 5-stage CIC decimator
//===========================================================================
// 本机没有 Verilator/VCS，本 TB 未在本机执行；用于仓库 CI。
//===========================================================================
`timescale 1ns/1ps

module sinc5_decim_tb;
    localparam int IN_W = 20;
    localparam logic [3:0] DEC_SEL = 4'd1;   // D = 2^(1+1) = 4

    logic clk = 0;
    always #1 clk = ~clk;       // 500 MHz sim clock; functional only

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

        // DC 1000: after enough settling, every decimated output must be 1000.
        for (int i = 0; i < 64; i++) push(1000);
        if (!settled) begin
            $display("FAIL: settled low after 64 samples (target 26)");
            errors++;
        end

        // Drain and check the last outputs
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
            // 手册 p.69：SINC5 需要 2+5D+4 个样本才建立完成。
            // 建立期内（settled=0）的输出是瞬态，不能按 1000 判。
            if (settled && (data_out < 999 || data_out > 1001)) begin
                $display("FAIL: decimated output=%0d exp=1000 (settled)", data_out);
                errors++;
            end
        end
    end

endmodule
