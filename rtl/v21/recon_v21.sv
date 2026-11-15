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

    // ================================================================ 四级流水
    //
    // **为什么必须流水**（2026-11-16 定位，见 docs/v21/CHECKPOINT_I5_SYNTH_v3.md）：
    // 原实现把所有运算塞在 code_o_reg.D 这一个锥里：
    //   两个 64 位除法器 + 41 位乘加 + 32 位乘移位，串在同一个 25 ns 周期。
    // DC 在这个锥上做时序驱动优化不收敛：
    //   · 无 IO 约束：块级 3 分钟"完成"，但报的路径全是自环（假象）
    //   · 加 IO 约束：块级 10 分钟卡在 code_o_reg[8]/D，顶层卡在 code_o_reg[9]/D
    //
    // 现在按方案 §9.3 的计算顺序切成四级，每级只做一件事：
    //   S1: Y -> Z            （一次 64 位除法 rnd64，alpha 旁路）
    //   S2: N                （41 位乘加，无除法）
    //   S3: I = RND(2^60,G)  （第二次 64 位除法，独立一级）
    //   S4: X -> Q -> code    （41x27 乘 + 32 位乘移位 + 钳位）
    //
    // **同笔关系**：valid / tag / epoch / err 与数据一起逐级寄存，
    // 每级只接受上一级传来的那一笔，不会跨笔混用。
    // **代价**：out_valid 相对 in_valid 延迟从 1 拍变成 4 拍。
    // `tb_i4_recon` 已按新延迟更新；位精确定义不变。

    // ---------------------------------------------------------------- 第 0 级：输入检查
    wire alpha_bypass = (alpha_q30 == 32'd1073741824);   // 2^30
    wire bad_g        = (G_q30 == 64'd0);
    // alpha 合法域：0.5 <= alpha <= 2（方案 §9.2），即 [2^29, 2^31]。
    // 域外值会让 Z 过大、突破 |X| < 2^31，使 32 位 RND 溢出。
    wire bad_alpha    = (alpha_q30 < 32'd536870912) || (alpha_q30 > 32'd2147483648);
    wire bad_epoch    = (in_epoch != cfg_epoch);
    wire input_err    = bad_g | bad_alpha | bad_epoch;

    // ---------------------------------------------------------------- 函数
    // RND(n,d)：符号对称最近舍入，恰半离零（方案 §9.3）
    function automatic logic signed [63:0] rnd64(input logic signed [63:0] n,
                                                 input logic [63:0] d);
        logic        s;
        logic [63:0] a, q, r, two_r;
        begin
            s = n[63];
            a = s ? (~n + 64'd1) : n;
            q = a / d;
            r = a - q * d;
            two_r = r << 1;
            rnd64 = s ? -(q + ((two_r >= d) ? 64'd1 : 64'd0))
                      :  (q + ((two_r >= d) ? 64'd1 : 64'd0));
        end
    endfunction

    // ceil(2^34/3) = 0x1_5555_5556 = 5,726,623,062，最少需要 33 位。
    // 这里写 35 位只是把常数写全；真正的保障是
    // tools/check_rtl_rnd96.py 从本文件抽取后做逐点校验。
    localparam logic [34:0] MAGIC_3_34 = 35'h1_5555_5556;

    function automatic logic signed [31:0] rnd96_const(input logic signed [31:0] n);
        logic        s;
        logic [31:0] a;
        logic [66:0] prod;
        logic [31:0] q96, r96, q, two_r;
        begin
            s = n[31];
            a = s ? (~n + 32'd1) : n;
            prod = {35'd0, a} * MAGIC_3_34;
            q96  = (prod >> 34) >> 5;
            r96   = a - q96 * 32'd96;
            two_r = r96 << 1;
            q     = q96 + ((two_r >= 32'd96) ? 32'd1 : 32'd0);
            rnd96_const = s ? -q : q;
        end
    endfunction

    // ---------------------------------------------------------------- 级间寄存器
    logic               v1_q, v2_q, v3_q;
    logic [31:0]        tag1_q, tag2_q, tag3_q;
    logic               err1_q, err2_q, err3_q;

    logic signed [63:0] z1_q;        // S1 输出：Z
    logic [63:0]        i2_q;        // S2 输出：I（在 S2 算，供 S3 用）
    logic signed [95:0] n2_q;        // S2 输出：N
    logic signed [95:0] x3_q;        // S3 输出：X

    // ---------------------------------------------------------------- S1：Y -> Z
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v1_q <= 1'b0; tag1_q <= 32'd0; err1_q <= 1'b0; z1_q <= 64'sd0;
        end else begin
            v1_q   <= in_valid;
            tag1_q <= in_tag;
            err1_q <= input_err;
            if (in_valid) begin
                logic signed [31:0] y;
                logic signed [63:0] yb;
                y  = -32'sd3145728 + 32'sd768 + ($signed({19'd0, adc2_raw}) * 32'sd1536);
                // 27 位有符号 -> 64 位：高位补符号位，不是补 0
                yb = {{37{b_q24[26]}}, b_q24};
                if (alpha_bypass)
                    z1_q <= y - yb;
                else
                    z1_q <= rnd64((y - yb) * 64'sd1073741824, {32'd0, alpha_q30});
            end
        end
    end

    // ---------------------------------------------------------------- S2：N 与 I
    //
    // I = RND(2^60, G) 现在由 **recip_rnd_v21** 实现（无除法器）。
    //
    // 为什么要拆成 S2a/S2b/S2c 三级：
    //   原来 `rnd64(2^60, G)` 是纯组合，与 N 同级一拍就出。
    //   换成 recip_rnd_v21 后，它自带 recip_q60 的两级 Newton 流水
    //   加出口一拍，共 **3 拍**延迟。若仍与 N 同级，I 会比 N 晚两拍
    //   到，S3 就会拿错笔。
    //
    //   所以把 N 连同 tag/err/valid 一起再打两拍，与 I 汇合后再进 S3。
    //   N 与 I 都进 i2_q/n2_q 的最终一级，S3 接口不变。
    //
    //   代价：out_valid 相对 in_valid 的延迟由 4 拍变成 6 拍。
    //   tb_i4_recon / tb_i4b_e2e 的等待拍数已同步调整。
    logic        v2a_q, v2b_q, v2c_q;
    logic [31:0] tag2a_q, tag2b_q;
    logic        err2a_q, err2b_q;
    logic signed [95:0] n2a_q, n2b_q;

    logic        ri_valid, ri_err;
    logic [26:0] ri_i;

    recip_rnd_v21 u_recip_rnd (
        .clk(clk), .rst_n(rst_n),
        .valid_i(v1_q), .g_q30(G_q30),
        .valid_o(ri_valid), .i_q60(ri_i), .err_o(ri_err));

    // S2a：算 N，并启动 I 的倒数流水（valid_i = v1_q 已接在上面）
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v2a_q <= 1'b0; tag2a_q <= 32'd0; err2a_q <= 1'b0; n2a_q <= 96'sd0;
        end else begin
            v2a_q   <= v1_q;
            tag2a_q <= tag1_q;
            err2a_q <= err1_q;
            n2a_q   <= ($signed(z1_q) * 96'sd64)
                     + (96'sd6 * $signed({32'd0, Wm_q30}))
                     - (96'sd3 * $signed({32'd0, G_q30}))
                     - (96'sd6 * $signed({32'd0, Wp_q30}));
        end
    end

    // S2b
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v2b_q <= 1'b0; tag2b_q <= 32'd0; err2b_q <= 1'b0; n2b_q <= 96'sd0;
        end else begin
            v2b_q   <= v2a_q;
            tag2b_q <= tag2a_q;
            err2b_q <= err2a_q;
            n2b_q   <= n2a_q;
        end
    end

    // S2c：N 与 I 汇合（I 恰在本拍有效）
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v2_q <= 1'b0; tag2_q <= 32'd0; err2_q <= 1'b0;
            n2_q <= 96'sd0; i2_q <= 64'd0;
        end else begin
            v2_q   <= v2b_q;
            tag2_q <= tag2b_q;
            err2_q <= err2b_q | ri_err;
            n2_q   <= n2b_q;
            i2_q   <= {37'd0, ri_i};
        end
    end

    // ---------------------------------------------------------------- S3：X
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v3_q <= 1'b0; tag3_q <= 32'd0; err3_q <= 1'b0; x3_q <= 96'sd0;
        end else begin
            v3_q   <= v2_q;
            tag3_q <= tag2_q;
            err3_q <= err2_q;
            if (v2_q)
                // X = floor(N*I / 2^36)；算术右移即 floor
                x3_q <= (n2_q * $signed({32'd0, i2_q})) >>> 36;
        end
    end

    // ---------------------------------------------------------------- S4：Q -> code
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            code_o    <= 20'd0;
            out_sat   <= 1'b0;
            out_err   <= 1'b0;
            out_tag   <= 32'd0;
        end else begin
            out_valid <= v3_q;
            if (v3_q) begin
                logic signed [31:0] x32, q32;
                logic signed [95:0] qw, uncl;
                out_err <= err3_q;
                out_tag <= tag3_q;
                if (err3_q) begin
                    // 合同违例：不出结果，但保留 tag 供追溯
                    code_o  <= 20'd0;
                    out_sat <= 1'b0;
                end else begin
                    // X_wide[31:0] 是位选、无符号，必须先 $signed 截断。
                    // |X| < 2^31 由 tools/prove_xwidth.py 证明（前提 A1-A5）。
                    x32 = $signed(x3_q[31:0]);
                    q32 = rnd96_const(x32);
                    qw  = {{64{q32[31]}}, q32};
                    uncl = qw + 96'sd524288;
                    if (uncl < 0) begin
                        code_o <= 20'd0;      out_sat <= 1'b1;
                    end else if (uncl > 96'sd1048575) begin
                        code_o <= 20'hFFFFF;  out_sat <= 1'b1;
                    end else begin
                        code_o <= uncl[19:0]; out_sat <= 1'b0;
                    end
                end
            end
        end
    end

endmodule
