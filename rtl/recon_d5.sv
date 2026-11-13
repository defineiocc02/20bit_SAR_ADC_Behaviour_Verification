// D5 定点重构：x̂ = D(k) + r̂ + TΔ，20 位 offset-binary 输出。
// 全部整数运算，单位 2^-24 V（Q24）。与 src/adi_model/digital_chain.py 的
// reconstruct_input_v 逐位一致；任何改动必须同步 Python 并重跑向量。
module recon_d5 (
    input  logic signed [8:0]  k_i,          // RDAC 控制码 0..511
    input  logic        [11:0] adc2_i,       // ADC2 原始码 0..4095
    input  logic        [5:0]  dither_i,     // D6..D1
    output logic        [19:0] code_o,
    output logic               overflow_o
);
    // Δ = 6/512 = 3/256 V  =>  Q24: 3*2^24/256 = 196608
    localparam signed [31:0] D_OFFSET_Q24 = -32'sd50331648;   // -3 V
    localparam signed [31:0] D_STEP_Q24   =  32'sd196608;

    // T = (8U + 16D5 + 4D3 + 2D2 + D1) / 8  （粗 LSB 单位，分母 8 为工程模式）
    // T*Δ in Q24 = (8U+16D5+4D3+2D2+D1) * 3*65536/8 = num * 24576
    localparam signed [31:0] T_STEP_Q24 = 32'sd24576;

    logic        [3:0] upper;
    logic        [7:0] t_num;      // 8U + 16D5 + 4D3 + 2D2 + D1
    logic signed [31:0] d_q24, t_q24, r_q24, x_q24;
    logic signed [31:0] num, q;

    assign upper = {2'b0, dither_i[5]} * 4'd4 + {3'b0, dither_i[3]};
    assign t_num = upper * 8 + {3'b0, dither_i[4]} * 16
                 + {5'b0, dither_i[2]} * 4 + {6'b0, dither_i[1]} * 2 + {7'b0, dither_i[0]};

    // r̂ = (adc2 + 0.5) * (0.45/4096)/32  =>  Q24 = round((2a+1) * 144 / 5)
    always_comb begin
        d_q24 = D_OFFSET_Q24 + D_STEP_Q24 * $signed({1'b0, k_i});
        t_q24 = $signed({24'b0, t_num}) * T_STEP_Q24;
        r_q24 = ((($signed({1'b0, adc2_i}) * 2 + 1) * 144) + 2) / 5;   // 就近舍入
        x_q24 = d_q24 + r_q24 + t_q24;
    end

    // code20 = round(x_q24 / 96) + 2^19，94 = 3*2^5 V 的一个 LSB20 的 Q24 值
    always_comb begin
        num = x_q24;
        if (num >= 0) q = (num + 48) / 96;
        else          q = -((-num + 48) / 96);
        overflow_o = (q + 32'sd524288) > 32'sd1048575 || (q + 32'sd524288) < 32'sd0;
        if (q > 32'sd524287)       code_o = 20'hFFFFF;
        else if (q < -32'sd524288) code_o = 20'h00000;
        else                       code_o = 20'(q + 32'sd524288);
    end
endmodule
