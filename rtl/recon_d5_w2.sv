// D5 权重路径重构 · 三级流水 · 预计算倒数版（25 ns 目标）
//   x̂_q24 = D_eff + r̂ + TΔ，D_eff = 6·Wm/G − 3  （Q24）
//   Wm = Σ w·m（主 + 子阵列，Q30），G = Σ w（仅 16 个采样主列，Q30）
//
// 与 recon_d5_pipe2 的唯一区别：S1 的「64 位 ÷ 45 位阵列除法」换成
// 标定期算好的倒数乘 + 右移
//
//     inv_g_q60 = round(2^60 / G)            // 26 bit，校准时写进寄存器
//     D_eff_q24 = ((6·Wm − 3·G) · inv_g_q60) >> 36
//
// 数学上等于 (6Wm − 3G)·2^24/G，只差 inv 的量化误差（见 docs/TIMING_W2.md）。
// r̂ / TΔ / ÷96 倒数乘 / 饱和与 recon_d5_w.sv 逐字一致，只按级切开放进寄存器。
//
// 级划分（与 recon_d5_pipe2 相同）：
//   S1：6·Wm−3·G 与 ×inv 乘法 + 右移 36 → d_q24；adc2/dither 打拍
//   S2：r̂ = ((2a+1)·144+2)/5 与 TΔ = t_num·24576 相加得 x_q24
//   S3：÷96 就近舍入 + 20 位 offset-binary 饱和输出 + overflow
//
// 输入有效范围（超出则该级位宽不再保证，属于标定失效而非本模块职责）：
//   G ≥ 2^34（否则 inv ≥ 2^27 被截断）
//   |6·Wm| < 2^38（对应 G≈3.4e10 的 ±3% 权重；此时 |D_eff| ≤ 4.5 V）
//   |(6Wm − 3G)·inv| < 2^63（乘积 ≈ D_eff·2^36 ≤ 5.2e18）
// 实测（400 组向量）：|num| 最大 37 bit、|prod| 最大 62 bit。
module recon_d5_w2 (
    input  logic        clk,
    input  logic        reset_n,
    input  logic [43:0] wm_q30,      // Σ w·m（主 + 子，Q30）
    input  logic [43:0] g_q30,       // Σ w（16 个采样主列，Q30）
    input  logic [63:0] inv_g_q60,   // round(2^60 / g_q30)，校准寄存器
    input  logic [11:0] adc2_i,      // ADC2 原始码 0..4095
    input  logic [5:0]  dither_i,    // D6..D1
    output logic [19:0] code_o,
    output logic        overflow_o
);
    // ---------------------------------------------------------------- S1
    // num = 6·Wm − 3·G = 4·Wm + 2·Wm − 2·G − G（写成移位加法：常数乘法交给
    // DC 会各推一个乘法器，白占 0.9 ns 和面积）
    // 位宽 39：|6·Wm| ≤ 6·(G + Wsub) ≈ 2.3e11 < 2^38（G≈3.4e10，Wsub ≤ 2 位子阵列）
    logic signed [38:0] wm_s, g_s, num;
    logic signed [26:0] inv_s;               // inv 只取低 27 位（G ≥ 2^34 时有效）
    logic signed [65:0] prod;                // 39 × 27 = 66 位，真实值 < 2^63
    logic signed [27:0] d_q24;
    logic        [11:0] adc2_s1;
    logic        [5:0]  dither_s1;

    assign inv_s = $signed(inv_g_q60[26:0]);
    assign wm_s  = $signed(wm_q30);
    assign g_s   = $signed(g_q30);

    assign num   = (wm_s <<< 2) + (wm_s <<< 1) - (g_s <<< 1) - g_s;

    assign prod   = num * inv_s;
    assign d_q24  = prod >>> 36;             // 算术右移 = floor，与 Python 的 >> 一致

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            adc2_s1   <= 12'd0;
            dither_s1 <= 6'd0;
        end else begin
            adc2_s1   <= adc2_i;
            dither_s1 <= dither_i;
        end
    end

    // D_eff 本身随 adc2/dither 一起进 S2
    logic signed [27:0] d_q24_r;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) d_q24_r <= 28'sd0;
        else          d_q24_r <= d_q24;
    end

    // ---------------------------------------------------------------- S2
    // r̂ = ((2a+1)·144 + 2)/5，倒数乘 838861>>22 在 0..1,179,506 上与原式逐位一致
    logic signed [41:0] r_prod;
    logic signed [18:0] r_q24;
    logic        [3:0]  upper;
    logic        [7:0]  t_num;
    logic signed [21:0] t_q24;
    logic signed [27:0] x_next, x_q24;

    assign r_prod = (($signed({1'b0, adc2_s1}) * 2 + 1) * 144 + 2) * 42'sd838861;
    assign r_q24  = r_prod >>> 22;

    assign upper = {2'b0, dither_s1[5]} * 4'd4 + {3'b0, dither_s1[3]};
    assign t_num = upper * 8 + {3'b0, dither_s1[4]} * 16
                 + {5'b0, dither_s1[2]} * 4 + {6'b0, dither_s1[1]} * 2
                 + {7'b0, dither_s1[0]};
    assign t_q24 = $signed({3'b0, t_num}) * 22'sd24576;

    assign x_next = d_q24_r + r_q24 + t_q24;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) x_q24 <= 28'sd0;
        else          x_q24 <= x_next;
    end

    // ---------------------------------------------------------------- S3
    // ÷96：倒数乘 44739243>>32，加 2^31 实现就近舍入（与 recon_d5_w 同式）
    logic signed [27:0] xa, q;
    logic signed [53:0] q_prod;

    always_comb begin
        xa     = (x_q24 >= 0) ? x_q24 : -x_q24;
        q_prod = xa * 54'sd44739243 + 54'sd2147483648;
        q      = (x_q24 >= 0) ? (q_prod >>> 32) : -(q_prod >>> 32);
        overflow_o = (q + 28'sd524288) > 28'sd1048575 || (q + 28'sd524288) < 28'sd0;
        if (q > 28'sd524287)       code_o = 20'hFFFFF;
        else if (q < -28'sd524288) code_o = 20'h00000;
        else                       code_o = q[19:0] + 20'd524288;
    end
endmodule
