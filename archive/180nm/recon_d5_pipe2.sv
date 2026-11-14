// D5 权重路径重构 · 三级流水（25 ns 目标）
//   x̂_q24 = D_eff + r̂ + TΔ，D_eff = (6·Wm − 3·G) / G  （Q24）
//   Wm = Σ w·m（主 + 子阵列，Q30），G = Σ w（仅 16 个采样主列，Q30）
//
// 与 recon_d5_pipe 的区别：多了 S1/S2 之间的一级寄存器，把「除法」和
// 「r̂ + TΔ 相加」分开；端口因此换成算术来源 wm_q30/g_q30（除法需要它们），
// 不再是 recon_d5_pipe 的 k_i（k_i 是已经除完、只做线性 DAC 的索引）。
//
// 级划分：
//   S1：adc2_i/dither_i 打拍 + D_eff = (6·Wm − 3·G)·2^24 / G   ← 除法在这一级
//   S2：r̂ = ((2a+1)·144+2)/5 与 TΔ = t_num·24576 相加得 x_q24
//   S3：÷96 就近舍入 + 20 位 offset-binary 饱和输出 + overflow
//
// 数值上与 src/adi_model/digital_chain.py 的 run_transaction 逐位对齐：
//   · D_eff 用「半数远离零」舍入，与 _div_round 同规则
//   · r̂ 用 floor(((2a+1)·144+2)/5)（倒数乘 838861>>22 在 0..1,179,506 上精确），
//     与 recon_d5_w 一致（都不加 +2^21 舍入常量）；recon_d5_pipe 的 S1 加过 +2^21，
//     等于做了二次舍入，见 docs/TIMING_PIPE2.md
module recon_d5_pipe2 (
    input  logic        clk,
    input  logic        reset_n,
    input  logic [43:0] wm_q30,     // Σ w·m（主 + 子，Q30）
    input  logic [43:0] g_q30,      // Σ w（16 个采样主列，Q30）
    input  logic [11:0] adc2_i,     // ADC2 原始码 0..4095
    input  logic [5:0]  dither_i,   // D6..D1
    output logic [19:0] code_o,
    output logic        overflow_o
);
    localparam signed [63:0] Q24 = 64'sd16777216;

    // ---------------------------------------------------------------- S1
    // 位宽：|6·Wm − 3·G| ≤ 2^38（Wm ≤ G ≤ 8·16·2^31），×2^24 → ≤ 2^62，64 位足够
    logic signed [63:0] num, den, num_a, num_q;
    logic signed [27:0] d_q24;              // |D_eff| ≤ 3·2^24 = 5.0e7 < 2^26
    logic        [11:0] adc2_s1;
    logic        [5:0]  dither_s1;

    always_comb begin
        num   = (($signed({20'b0, wm_q30}) * 6) - ($signed({20'b0, g_q30}) * 3)) * Q24;
        den   = $signed({20'b0, g_q30});
        num_a = (num >= 0) ? num : -num;            // 半数远离零：先取绝对值
        num_q = (num_a + (den >>> 1)) / den;
    end

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            d_q24     <= 28'sd0;
            adc2_s1   <= 12'd0;
            dither_s1 <= 6'd0;
        end else begin
            d_q24     <= (den != 0) ? ((num >= 0) ? num_q[27:0] : -num_q[27:0]) : 28'sd0;
            adc2_s1   <= adc2_i;
            dither_s1 <= dither_i;
        end
    end

    // ---------------------------------------------------------------- S2
    // r̂ = ((2a+1)·144 + 2)/5，倒数乘在 0..1,179,506 上与原式逐位一致
    logic signed [41:0] r_prod;
    logic signed [18:0] r_q24;             // max 235901，19 位有符号
    logic        [3:0]  upper;
    logic        [7:0]  t_num;              // 8·T（T 以粗 LSB 为单位）
    logic signed [21:0] t_q24;             // max 63·24576 = 1548288，22 位有符号
    logic signed [26:0] x_next, x_q24;

    assign r_prod = (($signed({1'b0, adc2_s1}) * 2 + 1) * 144 + 2) * 42'sd838861;
    assign r_q24  = r_prod >>> 22;

    assign upper = {2'b0, dither_s1[5]} * 4'd4 + {3'b0, dither_s1[3]};
    assign t_num = upper * 8 + {3'b0, dither_s1[4]} * 16
                 + {5'b0, dither_s1[2]} * 4 + {6'b0, dither_s1[1]} * 2
                 + {7'b0, dither_s1[0]};
    assign t_q24 = $signed({3'b0, t_num}) * 22'sd24576;

    assign x_next = d_q24 + r_q24 + t_q24;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) x_q24 <= 27'sd0;
        else          x_q24 <= x_next;
    end

    // ---------------------------------------------------------------- S3
    logic signed [26:0] xa, q;
    logic signed [53:0] q_prod;

    always_comb begin
        xa     = (x_q24 >= 0) ? x_q24 : -x_q24;
        q_prod = xa * 54'sd44739243 + 54'sd2147483648;      // round(x/96) 的倒数乘
        q      = (x_q24 >= 0) ? (q_prod >>> 32) : -(q_prod >>> 32);
        overflow_o = (q + 27'sd524288) > 27'sd1048575 || (q + 27'sd524288) < 27'sd0;
        if (q > 27'sd524287)       code_o = 20'hFFFFF;
        else if (q < -27'sd524288) code_o = 20'h00000;
        else                       code_o = q[19:0] + 20'd524288;
    end
endmodule
