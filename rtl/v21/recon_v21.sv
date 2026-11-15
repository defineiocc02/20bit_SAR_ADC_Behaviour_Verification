// =============================================================================
// 文件名     : recon_v21.sv
// 模块名     : recon_v21
// 版本       : v1.0
// 功能       : 13 位 ADC2 raw → 20 位定点重构（方案 v1.4 §9.1–§9.3）。
//              逐字实现 §9.3 的整数 oracle，保证与金标位精确一致。
// 设计说明   :
//   * §9.3 的整数规则（必须逐字采用，不得"改进"）：
//       RND(n,d)，d>0：符号对称的最近舍入，恰半时离零
//       Y  = -3145728 + 1536*raw + 768        （ADC2 码 → Q24）
//       Z  = Y - b                             （alpha == 2^30 的旁路）
//       Z  = RND((Y-b)*2^30, alpha_q30)        （其余合法 alpha）
//       N  = Z*64 + 6*Wm - 3*G - 6*Wp          （Q30 权重口径）
//       I  = RND(2^60, G)
//       X  = floor((N*I)/2^36)                 （与有符号算术右移一致）
//       Q  = RND(X, 96)
//       unclipped = Q + 524288
//       code = min(1048575, max(0, unclipped))
//       out_sat = (code != unclipped)
//
//   * 为什么乘 64 / 除 96：Y 是 Q24，权重是 Q30，差 6 位（64）。
//     I 是 2^60/G 的倒数，N(≈Q30) * I(≈Q24) 后要落到 Q24 再除 96
//     得到 20 位 LSB 单位。这些常数是 §9.3 给定的，不能改。
//
//   * 位宽（§9.2 已证明）：
//       |Y-b| ≤ 2,156,920,064
//       |Z|   ≤ 4,313,840,128
//       |N|   ≤ 688,402,628,608 < 2^40
//       I     ≤ 2^26
//       完整乘积 < 2^66 -> signed64 分子 + signed96 乘积有余量
//
//   * **本版用精确除法**（§9.3："初版倒数用精确整数定义作为金标；
//     无法证明差值界时采用精确除法基线"）。倒数迭代留 I5 做等价替换。
//     代价是 96 位乘 + 大位宽除法，PPA 大 —— 已在 I3 检查点记录。
//
//   * 配置与 epoch（§9.4）：coefficient_epoch 随每笔携带；
//     epoch 不匹配时输出 err 且不产生结果，防止旧系数被用于新笔。
// 变更记录   :
//   2026-11-15  初版。I4 阶段。
// =============================================================================
module recon_v21 (
    input  logic         clk,
    input  logic         rst_n,

    // ---------------------------------------------------------------- 输入
    input  logic         in_valid,       // 本拍有一笔待重构
    input  logic [12:0]  adc2_raw,       // 13 位 ADC2 码
    input  logic [63:0]  Wm_q30,         // 本笔实际 post 的加权和
    input  logic [63:0]  G_q30,          // 本笔 8 片主行和
    input  logic [63:0]  Wp_q30,         // 本笔采样 pre 的加权和
    input  logic [31:0]  alpha_q30,      // 增益校正（2^30 = 1.0 旁路）
    input  logic signed [31:0] b_q24,    // 偏置
    input  logic [7:0]   in_epoch,       // 本笔系数 epoch
    input  logic [31:0]  in_tag,

    // ---------------------------------------------------------------- 配置
    input  logic [7:0]   cfg_epoch,      // 当前生效的系数 epoch

    // ---------------------------------------------------------------- 输出
    output logic         out_valid,
    output logic [19:0]  code_o,
    output logic         out_sat,        // 末级码饱和（≠ ADC2 越窗）
    output logic         out_err,        // 合同违例（G=0 / epoch 不匹配 / alpha=0）
    output logic [31:0]  out_tag
);

    // ---------------------------------------------------------------- 输入检查
    wire alpha_bypass = (alpha_q30 == 32'd1073741824);   // 2^30
    wire bad_g        = (G_q30 == 64'd0);
    wire bad_alpha    = (alpha_q30 == 32'd0);
    wire bad_epoch    = (in_epoch != cfg_epoch);
    wire input_err    = bad_g | bad_alpha | bad_epoch;

    // ---------------------------------------------------------------- Y
    // Y = -3145728 + 1536*raw + 768
    // 1536*raw 最大 1536*8191 = 12,581,376 < 2^24，用 signed32 足够。
    logic signed [31:0] Y_q24;
    always_comb begin
        Y_q24 = -32'sd3145728 + 32'sd768 + ($signed({19'd0, adc2_raw}) * 32'sd1536);
    end

    // ---------------------------------------------------------------- Z
    // alpha == 2^30 时精确旁路；否则 RND((Y-b)*2^30, alpha)
    logic signed [63:0] Z_q24;
    logic signed [63:0] yb;

    // RND(n,d)：符号对称最近舍入，恰半离零
    function automatic logic signed [63:0] rnd64(input logic signed [63:0] n,
                                                 input logic [63:0] d);
        logic        s;
        logic [63:0] a, q, r, two_r;
        begin
            s = n[63];
            a = s ? (~n + 64'd1) : n;      // abs
            q = a / d;
            r = a - q * d;
            two_r = r << 1;
            rnd64 = s ? -(q + ((two_r >= d) ? 64'd1 : 64'd0))
                      :  (q + ((two_r >= d) ? 64'd1 : 64'd0));
        end
    endfunction

    always_comb begin
        yb = $signed({{32{b_q24[31]}}, b_q24});
        if (alpha_bypass)
            Z_q24 = Y_q24 - yb;
        else
            Z_q24 = rnd64((Y_q24 - yb) * 64'sd1073741824, {32'd0, alpha_q30});
    end

    // ---------------------------------------------------------------- N
    // N = Z*64 + 6*Wm - 3*G - 6*Wp
    logic signed [95:0] N_wide;
    always_comb begin
        N_wide = ($signed(Z_q24) * 96'sd64)
               + (96'sd6 * $signed({32'd0, Wm_q30}))
               - (96'sd3 * $signed({32'd0, G_q30}))
               - (96'sd6 * $signed({32'd0, Wp_q30}));
    end

    // ---------------------------------------------------------------- I / X / Q
    logic [63:0] I_q60;
    logic signed [95:0] NI;
    logic signed [95:0] X_wide;
    logic signed [95:0] Q_wide;
    logic signed [95:0] unclipped;

    always_comb begin
        // I = RND(2^60, G)
        I_q60 = rnd64(64'sh1000000000000000, G_q30);
        // X = floor((N*I)/2^36)
        // 有符号 floor：算术右移即 floor；这里用 >>>（N*I 是 signed96）
        NI = N_wide * $signed({32'd0, I_q60});
        X_wide = NI >>> 36;
        // Q = RND(X, 96)
        Q_wide = rnd64(X_wide, 96'sd96);
        // unclipped = Q + 524288
        unclipped = Q_wide + 96'sd524288;
    end

    logic [19:0] code_c;
    logic        sat_c;
    always_comb begin
        if (unclipped < 0) begin
            code_c = 20'd0;              sat_c = 1'b1;
        end else if (unclipped > 96'sd1048575) begin
            code_c = 20'hFFFFF;          sat_c = 1'b1;
        end else begin
            code_c = unclipped[19:0];    sat_c = 1'b0;
        end
    end

    // ---------------------------------------------------------------- 输出寄存
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            code_o    <= 20'd0;
            out_sat   <= 1'b0;
            out_err   <= 1'b0;
            out_tag   <= 32'd0;
        end else begin
            out_valid <= in_valid;
            if (in_valid) begin
                out_err <= input_err;
                out_tag <= in_tag;
                if (input_err) begin
                    // 合同违例：不出结果，但保留 tag 供追溯
                    code_o  <= 20'd0;
                    out_sat <= 1'b0;
                end else begin
                    code_o  <= code_c;
                    out_sat <= sat_c;
                end
            end
        end
    end

endmodule
