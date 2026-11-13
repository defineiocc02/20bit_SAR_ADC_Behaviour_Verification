// D5 权重路径重构（整数）：x̂ = D_eff + r̂ + TΔ，D_eff = 6·Wm/G − 3
//   Wm = Σ w·m（主+子阵列，Q30），G = Σ w（仅 16 个采样主列，Q30）
//   t 已折叠：主 m=1→−3/m=0→+3，子 m=1→−6/m=0→0  =>  K = 3G − 6Wm
// 与 digital_chain.run_transaction 的 x_weight_v 逐位一致。
module recon_d5_w (
    input  logic        [43:0] wm_q30,     // Σ w·m
    input  logic        [43:0] g_q30,      // Σ w（主列）
    input  logic        [11:0] adc2_i,
    input  logic        [5:0]  dither_i,
    output logic        [19:0] code_o,
    output logic               overflow_o
);
    localparam signed [24:0] THREE_V_Q24 = 25'sd50331648;   // 3 V in Q24

    logic        [3:0]  upper;
    logic        [7:0]  t_num;
    logic signed [63:0] num, den, dac_q24, r_q24, rc, x_q24, xa, qa, q;
    logic signed [63:0] num_a, num_q;

    assign upper = {2'b0, dither_i[5]} * 4'd4 + {3'b0, dither_i[3]};
    assign t_num = upper * 8 + {3'b0, dither_i[4]} * 16
                 + {5'b0, dither_i[2]} * 4 + {6'b0, dither_i[1]} * 2 + {7'b0, dither_i[0]};

    always_comb begin
        // D_eff = (6·Wm − 3·G) / G  （Q24）
        num = (($signed({20'b0, wm_q30}) * 6) - ($signed({20'b0, g_q30}) * 3)) * 64'sd16777216;
        den = $signed({20'b0, g_q30});
        // 半数远离零：先取绝对值再舍入，避免负数截断
        num_a = (num >= 0) ? num : -num;
        num_q = (num_a + (den >>> 1)) / den;
        dac_q24 = (den != 0) ? ((num >= 0) ? num_q : -num_q) : 64'sd0;

        rc = (($signed({1'b0, adc2_i}) * 2 + 1) * 144 + 2) * 64'sd838861 + 64'sd2097152;
        r_q24 = rc >>> 22;
        x_q24 = dac_q24 + r_q24 + $signed({56'b0, t_num}) * 64'sd24576;
    end

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
