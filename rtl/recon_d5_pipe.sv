// recon_d5 的两级流水版本（组合版 recon_d5 保留，供现有对拍使用）。
// S1: D(k) + r̂ + TΔ -> x_q24 寄存器
// S2: ÷96 + 舍入 + 饱和 -> code_o
// 时钟域 clk，低有效异步复位。
module recon_d5_pipe (
    input  logic        clk,
    input  logic        reset_n,
    input  logic signed [8:0]  k_i,
    input  logic        [11:0] adc2_i,
    input  logic        [5:0]  dither_i,
    output logic        [19:0] code_o,
    output logic               overflow_o
);
    localparam signed [31:0] D_OFFSET_Q24 = -32'sd50331648;
    localparam signed [31:0] D_STEP_Q24   =  32'sd196608;
    localparam signed [31:0] T_STEP_Q24   =  32'sd24576;

    logic        [3:0] upper;
    logic        [7:0] t_num;
    logic signed [63:0] d_q24, t_q24, r_q24, rc, x_next;
    logic signed [63:0] x_q24;

    assign upper = {2'b0, dither_i[5]} * 4'd4 + {3'b0, dither_i[3]};
    assign t_num = upper * 8 + {3'b0, dither_i[4]} * 16
                 + {5'b0, dither_i[2]} * 4 + {6'b0, dither_i[1]} * 2 + {7'b0, dither_i[0]};

    // ---- S1 ----
    always_comb begin
        d_q24 = D_OFFSET_Q24 + D_STEP_Q24 * $signed({1'b0, k_i});
        t_q24 = $signed({56'b0, t_num}) * T_STEP_Q24;
        rc    = (($signed({1'b0, adc2_i}) * 2 + 1) * 144 + 2);
        r_q24 = (rc * 64'sd838861 + 64'sd2097152) >>> 22;
        x_next = d_q24 + r_q24 + t_q24;
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) x_q24 <= 64'sd0;
        else          x_q24 <= x_next;
    end

    // ---- S2 ----
    logic signed [63:0] xa, qa, q;
    always_comb begin
        xa = (x_q24 >= 0) ? x_q24 : -x_q24;
        qa = (xa * 64'sd44739243 + 64'sd2147483648) >>> 32;
        q  = (x_q24 >= 0) ? qa : -qa;
        overflow_o = (q + 64'sd524288) > 64'sd1048575 || (q + 64'sd524288) < 64'sd0;
        if (q > 64'sd524287)       code_o = 20'hFFFFF;
        else if (q < -64'sd524288) code_o = 20'h00000;
        else                       code_o = q[19:0] + 20'd524288;
    end
endmodule
