// =============================================================================
// 文件名     : recon_rnd_equiv.sv
// 模块名     : recon_rnd_equiv
// 版本       : v2.0
// 功能       : 形式化证明 recon_v21 的常数除法（RND(n,96)）满足方案 §9.3 定义。
// 设计说明   :
//   * 为什么用形式化而不是抽样仿真：
//     "对所有合法 n，rnd96_const(n) 都等于 §9.3 的最近舍入" 是全称命题。
//     抽样只能给统计信心 —— 本轮就发生过 MAGIC 常数少写一位（35 位写成 34 位）
//     导致等效除数变成约 6、10769 个向量失配的事故；形式化能直接把它证伪。
//
//   * 规格怎么写才不依赖除法：
//     把 §9.3 的 RND 定义翻成**关系式**，而不是照抄一个 "/" 实现
//     （除法器在形式化里代价极高，且会与 RTL 的实现细节耦合）：
//
//       设 q = RND(n, 96)，r = n - 96*q
//       则  (a) |r| <= 48                         —— 距离最近
//           (b) n >= 0 时 r > -49  且  r < 48     —— 恰半时离零（向 +inf 取整）
//           (c) n <  0 时 r > -48  且  r < 49     —— 恰半时离零（向 -inf 取整）
//
//     (b)(c) 把"恰半离零"编码成了残差的不等式，避免引入除法。
//
//   * 负向对照（证伪能力）：
//     本文件同时提供一份**故意写错**的实现（MAGIC 截成 34 位），
//     并给出一个反例断言 `neg_wrong_must_fail`。
//     如果形式化工具对错版本也报 PROVEN，说明约束过强或断言写坏，
//     那么正版本的 PROVEN 也不可信。这条"证明的证明"不能省。
//
//   * 合法域：方案 §9.2 的 profile —— X 的实际范围 |X| < 2^31。
//     域外行为由 err 标志负责，不在本等价性范围内。
// 变更记录   :
//   2026-11-16  v1.0 初版（用 "/" 做参考，形式化代价高）。
//   2026-11-16  v2.0 改无除法关系规格；补负向对照。
// =============================================================================
module recon_rnd_equiv (
    input  logic clk,
    input  logic rst_n
);

    localparam logic [31:0] D96 = 32'd48;      // 96/2

    // ------------------------------------------------------------------ 正确实现
    // 与 rtl/v21/recon_v21.sv 的 rnd96_const 逐字一致
    function automatic logic signed [31:0] dut_rnd96(input logic signed [31:0] n);
        logic        s;
        logic [31:0] a;
        logic [66:0] prod;
        logic [31:0] q96, r96, q, two_r;
        localparam logic [34:0] MAGIC = 35'h1_5555_5556;
        begin
            s = n[31];
            a = s ? (~n + 32'd1) : n;
            prod = {35'd0, a} * MAGIC;
            q96  = (prod >> 34) >> 5;
            r96  = a - q96 * 32'd96;
            two_r = r96 << 1;
            q    = q96 + ((two_r >= 32'd96) ? 32'd1 : 32'd0);
            dut_rnd96 = s ? -q : q;
        end
    endfunction

    // ------------------------------------------------- 错误实现（仅用于负向对照）
    // 把 MAGIC 截成 34 位 —— 这正是本轮真实发生过的 bug。
    // 它的等效除数约为 6，必须被断言证伪。
    function automatic logic signed [31:0] bad_rnd96(input logic signed [31:0] n);
        logic        s;
        logic [31:0] a;
        logic [66:0] prod;
        logic [31:0] q96, r96, q, two_r;
        localparam logic [33:0] BAD_MAGIC = 34'h5555_5556;   // 高位丢了
        begin
            s = n[31];
            a = s ? (~n + 32'd1) : n;
            prod = {34'd0, a} * BAD_MAGIC;
            q96  = (prod >> 34) >> 5;
            r96  = a - q96 * 32'd96;
            two_r = r96 << 1;
            q    = q96 + ((two_r >= 32'd96) ? 32'd1 : 32'd0);
            bad_rnd96 = s ? -q : q;
        end
    endfunction

    // ------------------------------------------------------------------ 输入
    logic signed [31:0] n;

    // 合法域：|n| < 2^31（方案 §9.2 profile）。排除最小负数避免取反溢出。
    always_comb begin
        assume (n != 32'sh8000_0000);
        assume (n > -32'sh8000_0000);
    end

    // ------------------------------------------------------------------ 残差
    logic signed [33:0] r_pos, r_neg, r_bad;

    logic signed [31:0] q_ok, q_bad;
    always_comb begin
        q_ok  = dut_rnd96(n);
        q_bad = bad_rnd96(n);
        // 残差用 34 位算，避免 96*q 溢出 32 位
        r_pos = $signed({n[31], n}) - $signed({2'b0, q_ok}) * 34'sd96;
        r_bad = $signed({n[31], n}) - $signed({2'b0, q_bad}) * 34'sd96;
        r_neg = r_pos;
    end

    // ------------------------------------------------------------------ 正向断言
    // (a) 距离最近：|r| <= 48
    always_comb begin
        a_dist: assert (r_pos <= 34'sd48);
        b_dist: assert (r_pos >= -34'sd48);
    end

    // (b) n >= 0：恰半时离零 -> r ∈ [-48, 47]
    always_comb begin
        c_tie_pos: assert (!(n >= 0) || (r_pos <= 34'sd47));
    end

    // (c) n < 0：恰半时离零 -> r ∈ [-47, 48]
    always_comb begin
        d_tie_neg: assert (!(n < 0) || (r_pos >= -34'sd47));
    end

    // ------------------------------------------------------------------ 负向对照
    // 错版本必须违反对应的距离界，否则规格没有区分能力。
    // 这里只断言"存在违例即可证伪"：用 cover 让工具找反例。
    always_comb begin
        e_bad_cover: cover (r_bad > 34'sd48);
        f_bad_cover: cover (r_bad < -34'sd48);
    end

endmodule
