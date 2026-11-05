//===========================================================================
// ad4080_cal_tb.sv: 仿射系统校正与正向两点拟合的自检测试。
//
// 预期整数值来自固定点 Python 模型，面向 Verilator/XSim 回归；
// 不将正向拟合与应用结果解释为逆向校准有效性或器件精度证据。
//===========================================================================
`timescale 1ns/1ps

module ad4080_cal_tb;
    localparam int IN_W = 20;
    localparam int FS   = (1 << IN_W) - 1;

    logic clk = 0;
    always #12.5 clk = ~clk;   // 40 MHz

    logic rst_n = 0;
    integer errors = 0;

    // 检查固定横坐标的正向两点拟合。
    logic start;
    logic [IN_W-1:0] y1, y2;
    logic [9:0] gain_code;
    logic signed [11:0] offset_code;
    logic done, cal_error;

    cal_two_point #(.IN_W(IN_W)) dut_cal (
        .clk(clk), .rst_n(rst_n), .start(start),
        .y1(y1), .y2(y2),
        .gain_code(gain_code), .offset_code(offset_code),
        .done(done), .error(cal_error)
    );

    // 将拟合的正向系数用于仿射数值通路比较。
    logic valid_in;
    logic [IN_W-1:0] x, y;
    logic valid_out, clip_low, clip_high;

    sys_error_corr #(.IN_W(IN_W)) dut_corr (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in),
        .x(x), .offset_code(offset_code), .gain_code(gain_code),
        .y(y), .valid_out(valid_out), .clip_low(clip_low), .clip_high(clip_high)
    );

    task automatic check_cal(
        input string name,
        input logic [IN_W-1:0] ty1,
        input logic [IN_W-1:0] ty2,
        input int exp_gain,
        input int exp_off
    );
        begin
            y1 = ty1; y2 = ty2;
            @(posedge clk); start <= 1'b1;
            @(posedge clk); start <= 1'b0;
            wait (done);
            @(posedge clk);
            if (gain_code !== exp_gain[9:0]) begin
                $display("FAIL %s: GAIN=%0d exp=%0d", name, gain_code, exp_gain);
                errors++;
            end else if (offset_code !== exp_off[11:0]) begin
                $display("FAIL %s: OFFSET=%0d exp=%0d", name, offset_code, exp_off);
                errors++;
            end else begin
                $display("PASS %s: GAIN=%0d OFFSET=%0d", name, gain_code, offset_code);
            end
        end
    endtask

    task automatic check_corr(
        input string name,
        input logic [IN_W-1:0] tx,
        input int tol,
        input int exp_y
    );
        begin
            // 避开 DUT 有效采样沿驱动，并在下降沿观察寄存结果，减少不同模拟器的调度竞争。
            @(negedge clk); x = tx; valid_in = 1'b1;
            @(negedge clk); valid_in = 1'b0;
            for (int cycle = 0; cycle < 5 && !valid_out; cycle++) @(negedge clk);
            if (!valid_out) begin
                $display("FAIL %s: valid_out low", name);
                errors++;
            end else if (($signed(y) > exp_y + tol) || ($signed(y) + tol < exp_y)) begin
                $display("FAIL %s: y=%0d exp=%0d tol=%0d", name, $signed(y), exp_y, tol);
                errors++;
            end else begin
                $display("PASS %s: y=%0d exp=%0d", name, $signed(y), exp_y);
            end
        end
    endtask

    initial begin
        start = 0; valid_in = 0; y1 = 0; y2 = 0; x = 0;
        repeat (4) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        // 两点拟合预期值来自 Python 定点模型，输入采用补码。
        check_cal("ideal",       -419430, 419430, 512,    0);
        check_cal("gain +1%",    -423624, 423624, 840,    0);
        check_cal("gain -1%",    -415236, 415236, 184,    0);
        check_cal("offset +100", -419330, 419530, 512,  100);
        check_cal("offset -100", -419530, 419330, 512, -100);
        check_cal("gain+0.5 off+50", -421477, 421577, 676, 50);

        // 对 x=500000 应用最后得到的 gain=676、off=50，比较
        // round(1.005*500000+50)=502550，容差为 8 LSB。
        check_corr("corr x=500000", 500000, 8, 502550);
        // 负半轴同时检查符号处理，避免无符号解释只在正码点通过。
        check_corr("corr x=-500000", -500000, 8, -502450);

        if (errors == 0) $display("TB PASS");
        else             $display("TB FAIL: %0d errors", errors);
        $finish;
    end

endmodule
