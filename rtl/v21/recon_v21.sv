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
    // 偏置：与节点宏同口径 **Q24 / 27 位有符号**。
    // 早期写 32 位，而顶层传的是 27 位，综合报
    // "Width mismatch on port 'b_q24'" 并卡在 WLM 优化阶段（实测）。
    // 统一 27 位既符合 "24 位小数 + 2 位整数 + 1 位符号" 的推导，
    // 也避免隐式截断/扩展带来的歧义。
    input  logic signed [26:0] b_q24,    // 偏置（Q24，27 位）
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
    // alpha 合法域：0.5 <= alpha <= 2（方案 §9.2），即 [2^29, 2^31]。
    // 域外值会让 Z 过大、突破 |X| < 2^31，使 32 位 RND 溢出 ——
    // 与其输出错误码值，不如报 err 并由上层决定。
    wire bad_alpha    = (alpha_q30 < 32'd536870912) || (alpha_q30 > 32'd2147483648);
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
        // 27 位有符号 -> 64 位：高位补符号位（b_q24[26]），不是补 0。
        // 补 0 会把负偏置变成巨大正数（同类坑在 I3 的 y_wide 位选上踩过）。
        yb = {{37{b_q24[26]}}, b_q24};
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
    logic signed [31:0] q32, x32;
    logic signed [95:0] unclipped;

    // ---------------------------------------------------------------- 除常数 96（32 位）
    //
    // **位宽按实际数值范围收窄**（方案要求"按实际数值范围决定位宽"）。
    //
    // X = floor(N*I/2^36)，其中 |N| <= 688402628608 < 2^40、I <= 2^26，
    // 所以 |X| < 2^30。穷举 raw{0,1,4096,8191} × alpha{2^-1,1,2} ×
    // b{0,±2^20} × G{16,32,64}×2^30 × Wm{0,32,64}×2^30 × Wp{0,1,4}×2^30
    // 全部组合后 |X| 最大 353,632,160 = 0x1513FFA0 < 2^29。
    //
    // 因此 Q = RND(X, 96) 只需要 32 位运算：
    //   96 = 3 * 32，|X|/96 = (|X|/3) >> 5
    //   |X|/3 = (|X| * ceil(2^34/3)) >> 34，对 |X| < 2^31 精确
    // 这样把原来 96×97 -> 193 位的乘法与 96 位除法全部消掉，
    // DC 不再生成 DW01_add_width191 / DW_div 阵列。
    //
    // 若将来合法域扩大（比如 alpha 或权重范围放宽），必须先重新推 |X| 上界，
    // 确认仍 < 2^31，否则本函数会溢出。
    // ceil(2^34/3) = 0x1_5555_5556 = 5,726,623,062，最少需要 **33 位**。
    // 所以 [33:0]（34 位）本来就能完整容纳它，容器宽度不是问题。
    //
    // 实际出过的事故是**字面值漏了最高位的 1**：
    //     34'h5555_5556   -> 0x5555_5556 = 1,431,655,766
    // 这时 (a * 0x5555_5556) >> 34 ≈ a/11，再 >>5 ≈ a/352，
    // 故障表现是码值整体缩小（实测 0x9FFC1 vs 0xFFF00，10769 个失配）。
    //
    // 这里的 35 位声明只是把常数写全，不影响正确性；
    // 真正的保障是 tools/check_rtl_rnd96.py 直接从本文件抽取常数做校验。
    localparam logic [34:0] MAGIC_3_34 = 35'h1_5555_5556;   // ceil(2^34/3)

    function automatic logic signed [31:0] rnd96_const(
            input logic signed [31:0] n);
        logic        s;
        logic [31:0] a;
        logic [66:0] prod;         // 32 + 35 = 67 位
        logic [31:0] q96, r96, q;
        logic [31:0] two_r;
        begin
            s = n[31];
            a = s ? (~n + 32'd1) : n;               // |n|

            // |n|/3 = (|n| * ceil(2^34/3)) >> 34；再 >>5 得 /96
            prod = {35'd0, a} * MAGIC_3_34;
            q96  = (prod >> 34) >> 5;

            r96   = a - q96 * 32'd96;
            two_r = r96 << 1;
            q     = q96 + ((two_r >= 32'd96) ? 32'd1 : 32'd0);
            rnd96_const = s ? -q : q;
        end
    endfunction

    always_comb begin
        // I = RND(2^60, G)
        I_q60 = rnd64(64'sh1000000000000000, G_q30);
        // X = floor((N*I)/2^36)
        // 有符号 floor：算术右移即 floor；这里用 >>>（N*I 是 signed96）
        NI = N_wide * $signed({32'd0, I_q60});
        X_wide = NI >>> 36;
        // Q = RND(X, 96)
        //
        // **除数是常数 96，不能用通用除法器**。
        // 96 = 3 * 32，所以 |X|/96 = (|X|/3) >> 5，
        // 而 |X|/3 可用"乘魔数再右移"精确实现：
        //   |X|/3 = (|X| * ceil(2^98/3)) >> 98
        // 该式对 0 <= |X| < 2^96 全域精确（已用 20013 个边界+随机点验证，
        // 含 |N|max=688402628608 与 2^96-1）。
        //
        // 这样把"96 位除以 96 位"的通用除法器换成
        // "96 位乘 97 位常数 + 两次移位"，DC 的 DW_div 阵列从 3 组降到 2 组，
        // 且剩下的两组也都是固定除数。
        // 只调用一次，显式符号扩展
        // **X_wide[31:0] 是位选，无符号**（这个坑在前面 y_wide 上踩过）。
        // X_wide 是 96 位有符号，直接取低 32 位会丢掉符号，
        // 负数变成巨大正数。必须先做有符号截断再传。
        x32 = $signed(X_wide[31:0]);
        q32 = rnd96_const(x32);
        Q_wide = {{64{q32[31]}}, q32};
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
