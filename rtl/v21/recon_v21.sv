// =============================================================================
// 文件名     : recon_v21.sv
// 模块名     : recon_v21
// 版本       : v1.0
// 功能       : 将 13 位 ADC2 原码与同笔权重重构为 20 位输出。
//              按工程合同 §9 的整数顺序舍入、限幅，随标签返回错误和饱和标志。
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
//   * 范围检查采用配置合同规定的 G、Wm、Wp 和 α 输入域。
//     tools/prove_xwidth.py 以该输入域推导 X 的有符号位宽界。
//
//   * G 的倒数由 recip_rnd_v21 计算并修正余数；S3 用与 N 同笔的 I 求 X。
//     S4 只在 X 能由 32 位有符号数精确表示时舍入，结果和错误均保留原 tag。
//
//   * 配置与 epoch（§9.4）：coefficient_epoch 随每笔携带；
//     epoch 不匹配时输出 err 且不产生结果，防止旧系数被用于新笔。
// 变更记录   :
//   2026-11-15  初版。I4 阶段。
//   2026-11-19  S3 用同一笔 X 检查符号扩展，修正负值污染后续标签。
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
    output logic         out_err,        // 输入越域、epoch 不匹配或 X 不能有符号截断
    output logic [31:0]  out_tag
);

    // =============================================================================
    // 1. 受理沿、输入范围与定点运算
    // =============================================================================
    // t 为 in_valid 有效的上升沿。相邻沿可以携带不同参数和标签。
    // t    S1a  锁存 Y-b、α 及权重；t+1 S1b 计算 Z。
    // t+2  S2a  计算 N；t+3 S2b 延时；t+4 S2c 将 N 与本笔倒数 I 汇合。
    // t+5  S3   计算 X 并判定能否按有符号 32 位截断。
    // t+6  S4   对 X 舍入、限幅并退休，共经历七个寄存级。
    // out_valid 指示该标签返回，out_err 区分合同错误与正常结果。

    // ---------------------------------------------------------------- 第 0 级：输入检查
    // 本笔 alpha 的锁存副本（在 S1a 受理沿写入，见下方 S1a 段）
    logic [31:0] alpha1a_q;

    // **旁路判断必须用锁存值，不能用实时端口**。
    // 反例（scenario5 tag501，连续 3 笔，末笔 alpha=2^29）：
    //   S1b 在 t7 算 z1_q 时，alpha_q30 总线已是下一笔的 2^30，
    //   走旁路 -> Z = d1a = 3072（应 6144）
    //   -> N = 3072*64 = 196608（应 393216）
    //   -> X = 96 -> Q = 1 -> code 0x80001（应 0x80002）
    //   实测错值逐位吻合这条推导。
    wire alpha_bypass = (alpha1a_q == 32'd1073741824);   // 2^30，用锁存值
    // ---------------------------------------------------------------- 范围检查
    //
    // **必须检查完整 64 位 G，再允许截 44 位**。
    // 反例（外部审计 R2，可独立复算）：G = 2^34 + 2^44 由 128 个
    // (2^37+2^27) 主权重构造，均在 40 位容器内；截 44 位后别名成合法的 2^34，
    // 倒数侧的范围检查无法代表原始 64 位 G 已通过。
    //
    // 工程 profile（CONFIG_CONTRACT §6，非论文披露值）：
    //   Smax = 32*4*2^30;  Wp <= Smax;  Wm <= G + Smax
    //   16*2^30 <= G <= 64*2^30
    localparam logic [63:0] G_LO   = 64'd16  * 64'd1073741824;   // 16*2^30
    localparam logic [63:0] G_HI   = 64'd64  * 64'd1073741824;   // 64*2^30
    localparam logic [63:0] S_MAX  = 64'd32 * 64'd4 * 64'd1073741824;

    wire bad_g        = (G_q30 < G_LO) || (G_q30 > G_HI);
    wire bad_wp       = (Wp_q30 > S_MAX);
    wire bad_wm       = (Wm_q30 > (G_q30 + S_MAX));

    // alpha 合法域：0.5 <= alpha <= 2（方案 §9.2），即 [2^29, 2^31]。
    // 域外值会让 Z 过大、突破 |X| < 2^31，使 32 位 RND 溢出。
    wire bad_alpha    = (alpha_q30 < 32'd536870912) || (alpha_q30 > 32'd2147483648);
    wire bad_epoch    = (in_epoch != cfg_epoch);
    wire input_err    = bad_g | bad_alpha | bad_epoch | bad_wp | bad_wm;

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
    //
    // **v1_q/tag1_q/err1_q/z1_q 在 S1 段里与 v1a_q/d1a_q 一起声明**，
    // 这里不能再声明一次。
    //
    // 踩过的坑：拆分 S1 时我在本段保留了旧声明，又在 S1 段新增了
    // 同名声明 —— VCS 只给 warning 照常仿真通过（8213 项 PASS），
    // 但 DC 直接报 `The register symbol 'v1_q' has already been defined`
    // 并拒绝 link，整个 recon_v21 变成黑盒，网表里连 DW_div 都没有。
    // 结果综合"通过"、面积 76221 µm² 好看，实际不含 recon 逻辑。
    //
    // => 教训：**仿真通过不等于可综合**。重复声明这类问题只有
    //    DC 的 analyze 才会报，门级等价编译时也会暴露
    //    （VCS 报 "Module definition of recon_v21 is not found"）。
    logic               v2_q, v3_q;
    logic [31:0]        tag2_q, tag3_q;
    logic               err2_q, err3_q;

    logic [63:0]        i2_q;        // S2 输出：I（在 S2 算，供 S3 用）
    logic signed [95:0] n2_q;        // S2 输出：N
    logic signed [95:0] x3_q;        // S3 输出：X

    // ---------------------------------------------------------------- S1：Y -> Z
    //
    // **S1 拆成 S1a / S1b 两级**，理由是时序违例：
    //   原本 S1 一拍里同时做 "Y-b 的加法" 和 "alpha 除法"，
    //   网表实测 b_q24_cfg -> z1_q 的路径 arrival 27.34 ns，
    //   超 25 ns 周期 2.36 ns（docs/v21/TIMING_BUDGET_v2.md）。
    //
    //   把 alpha 除法单独放一级后，S1a 只有一次加/减，
    //   S1b 只有一次除法（32 位操作，不是 64 位），两级都能满足。
    //
    // 代价：out_valid 相对 in_valid 的延迟由 6 拍变成 7 拍。
    // tb_i4_recon / tb_i4b_e2e 的等待拍数已同步调整。
    //
    // 注意：alpha == 2^30 时走旁路，S1b 不做除法 —— 正常模式下
    // 这一级只是把 S1a 的结果打一拍，不引入额外延迟风险。
    logic        v1a_q, v1_q;
    logic [31:0] tag1a_q, tag1_q;
    logic        err1a_q, err1_q;
    logic signed [63:0] d1a_q, z1_q;   // d1a = Y - b，z1 = 结果

    // ---- 本笔参数快照（**逐级流水传递**）----
    //
    // **必须锁存，不能沿用实时端口**。
    // 反例（独立 VCS，tag 201）：A/B/C 连续受理时，S1b 与 S2a 读到的
    // 是**总线上当时的值**（B 的 G/Wm/Wp），不是 A 受理时的那一组：
    //   第 6 沿受理 A: G=32*2^30, Wm=16*2^30, Wp=0, Y-b=0 -> 应 N=0, I=2^25
    //   第 8 沿 S2a 读到 B 的: G=64*2^30, Wm=33*2^30 -> 实际 N=6*2^30
    //   第 12 沿输出 0x90000（应 0x80000）
    // 8/10 笔码错。原 tb_i4_recon 之所以通过，是因为它保持同一输入 7 拍，
    // 让实时总线"看起来"就是本笔的值。
    //
    // 所以：alpha/G/Wm/Wp 在 S1a 随 valid 一起锁存，逐级传递。
    //
    // **必须是流水寄存器，每级各存一份，与 v/tag 完全同步前进**。
    //
    // 反例（独立 VCS，tag 201/202 连续受理）：
    //   tick7: tag201 进 S1b（s1b_v=1），S1a 锁存 tag202 的总线值
    //   tick8: tag202 进 S1b，同时 tag201 进 S2a
    //   S2a(tag201) 若共用 S1a 那一份，读到的是 tag202 的 G
    //   -> N 错成 6*2^30（应 0），码 0x90000（应 0x80000）
    //
    // 所以：S1a 锁存 -> s1a 版；S1b 转发 -> s1b 版；S2a 用 s1b 版。
    logic [63:0] G1a_q, Wm1a_q, Wp1a_q;   // S1a 级（受理沿锁存）
    //
    // **recip 与 S2a 需要的版本不同，这是关键**：
    //   recip: valid_i=v1_q，其 G 必须与 v1_q 的时序链一致 -> 用 a 版
    //   S2a  : 在下一拍处理同一笔 -> 用 b 版（S1b 转发的）
    //
    // 实测（连续三笔 201/202/203，G 分别 32/16/64 ×2^30）：
    //   S2a 若用 a 版，tag202 读到的是 tag203 的 Wm=33*2^30，
    //   N = 6*33*2^30 - 3*64*2^30 = 6*2^30 = 6442450944，
    //   与实测错值**逐位吻合**。
    logic [63:0] G1b_q, Wm1b_q, Wp1b_q;   // S1b 级（转发，供 S2a 用）



    // S1a：只算 Y - b
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v1a_q <= 1'b0; tag1a_q <= 32'd0; err1a_q <= 1'b0; d1a_q <= 64'sd0;
            alpha1a_q <= 32'd1073741824;   // 复位取 2^30（旁路默认）
            G1a_q <= 64'd0; Wm1a_q <= 64'd0; Wp1a_q <= 64'd0;
        end else begin
            v1a_q   <= in_valid;
            tag1a_q <= in_tag;
            err1a_q <= input_err;
            if (in_valid) begin
                logic signed [31:0] y;
                logic signed [63:0] yb;
                y  = -32'sd3145728 + 32'sd768 + ($signed({19'd0, adc2_raw}) * 32'sd1536);
                // 27 位有符号 -> 64 位：高位补符号位，不是补 0
                yb = {{37{b_q24[26]}}, b_q24};
                d1a_q <= y - yb;
                // 同沿锁存本笔参数（受理沿快照）
                alpha1a_q <= alpha_q30;
                G1a_q    <= G_q30;
                Wm1a_q   <= Wm_q30;
                Wp1a_q   <= Wp_q30;
            end
        end
    end

    // S1b：alpha 除法（旁路时只打拍）
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v1_q <= 1'b0; tag1_q <= 32'd0; err1_q <= 1'b0; z1_q <= 64'sd0;
            G1b_q <= 64'd0; Wm1b_q <= 64'd0; Wp1b_q <= 64'd0;
        end else begin
            v1_q   <= v1a_q;
            tag1_q <= tag1a_q;
            err1_q <= err1a_q;
            // 参数随 valid/tag 同步前进（S2a 用这一版）
            G1b_q  <= G1a_q;
            Wm1b_q <= Wm1a_q;
            Wp1b_q <= Wp1a_q;
            if (alpha_bypass)
                z1_q <= d1a_q;
            else
                z1_q <= rnd64(d1a_q * 64'sd1073741824, {32'd0, alpha1a_q});
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
    logic        v2a_q, v2b_q, v2c_q;
    logic [31:0] tag2a_q, tag2b_q;
    logic        err2a_q, err2b_q;
    logic signed [95:0] n2a_q, n2b_q;

    logic        ri_valid, ri_err;
    logic [26:0] ri_i;

    recip_rnd_v21 u_recip_rnd (
        .clk(clk), .rst_n(rst_n),
        // **必须显式截断到 44 位**：recon 的 G_q30 端口是 64 位，
        // 而 recip_rnd_v21 的 g_q30 是 44 位。直接接会报
        // "Width mismatch on port 'g_q30'"（LINK-3），
        // DC 随即把整个 recip_rnd_v21 当黑盒，网表里缺定义 ——
        // 这会让人误以为综合收敛且面积只有 89693 µm²（实测就是这样）。
        // G 的合同域上限 2^36，44 位足够。
        .valid_i(v1_q), .g_q30(G1a_q[43:0]),
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
                     + (96'sd6 * $signed({32'd0, Wm1b_q}))
                     - (96'sd3 * $signed({32'd0, G1b_q}))
                     - (96'sd6 * $signed({32'd0, Wp1b_q}));
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
    // 上游乘积和右移均为有符号 96 位。截成 32 位的充要条件是丢弃位
    // 等于 bit 31 的符号扩展：-1 和 -2^31 的高位全为 1，仍然合法。
    // 只计算一份候选值供数据与范围判断共用。若读取 x3_q，会读到上一笔，
    // 即使隔了空拍，上一笔的符号仍会污染本笔的错误位。
    wire signed [95:0] x3_d = (n2_q * $signed({32'd0, i2_q})) >>> 36;
    wire x3_overflow = x3_d[95:32] != {64{x3_d[31]}};

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            v3_q <= 1'b0; tag3_q <= 32'd0; err3_q <= 1'b0; x3_q <= 96'sd0;
        end else begin
            v3_q <= v2_q;
            if (v2_q) begin
                x3_q   <= x3_d;
                tag3_q <= tag2_q;
                err3_q <= err2_q | x3_overflow;
            end
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
                out_err <= err3_q;   // 已含 bad_x（S3 并入）
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
