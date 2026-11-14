// recon_d5 两级流水（位宽按实际范围收窄）：
//   rc = (2a+1)*144+2 ≤ 1,179,506 < 2^21 ;  ×838861 < 2^41   -> 42 位
//   |x| < 5.4e7 < 2^26                   ;  ×44739243 < 2^52  -> 52 位
//   寄存器 x_q24: 27 位有符号
// 组合版 recon_d5 保留供现有对拍；本模块为 G5 优化候选。
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
    localparam signed [23:0] T_STEP_Q24   =  24'sd24576;

    logic        [3:0]  upper;
    logic        [7:0]  t_num;
    logic signed [26:0] x_next, x_q24;
    logic signed [31:0] d_q24;
    logic signed [23:0] t_q24;
    logic signed [41:0] rc, r_prod;
    logic signed [26:0] r_q24;
    logic signed [53:0] q_prod;   // |x|<=2^26, K=2^25.4 => 乘积 <=2^52，留 54 位
    logic signed [26:0] xa, q;

    assign upper = {2'b0, dither_i[5]} * 4'd4 + {3'b0, dither_i[3]};
    assign t_num = upper * 8 + {3'b0, dither_i[4]} * 16
                 + {5'b0, dither_i[2]} * 4 + {6'b0, dither_i[1]} * 2 + {7'b0, dither_i[0]};

    // ---- S1 ----
    always_comb begin
        d_q24  = D_OFFSET_Q24 + D_STEP_Q24 * $signed({1'b0, k_i});
        t_q24  = $signed({16'b0, t_num}) * T_STEP_Q24;
        rc     = (($signed({1'b0, adc2_i}) * 2 + 1) * 144 + 2);
        r_prod = rc * 42'sd838861 + 42'sd2097152;
        r_q24  = r_prod >>> 22;
        x_next = d_q24 + r_q24 + t_q24;
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) x_q24 <= 27'sd0;
        else          x_q24 <= x_next;
    end

    // ---- S2 ----
    always_comb begin
        xa = (x_q24 >= 0) ? x_q24 : -x_q24;
        q_prod = xa * 54'sd44739243 + 54'sd2147483648;
        q  = (x_q24 >= 0) ? (q_prod >>> 32) : -(q_prod >>> 32);
        overflow_o = (q + 27'sd524288) > 27'sd1048575 || (q + 27'sd524288) < 27'sd0;
        if (q > 27'sd524287)       code_o = 20'hFFFFF;
        else if (q < -27'sd524288) code_o = 20'h00000;
        else                       code_o = q[19:0] + 20'd524288;
    end
endmodule
