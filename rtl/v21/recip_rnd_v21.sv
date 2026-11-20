// =============================================================================
// 文件名     : recip_rnd_v21.sv
// 模块名     : recip_rnd_v21
// 版本       : v2.0
// 功能       : 用 recip_q60 的近似倒数 + 余数修正，精确实现 §9.3 的
//              `I = RND(2^60, G)`，**不使用除法器**。
//
// 设计说明   :
//   * 背景：recon_v21 里 `I = RND(2^60, G)` 的 64 位可配置除数除法器是
//     DC 综合不收敛的唯一瓶颈（见 docs/v21/CHECKPOINT_I5_SYNTH_v4.md）。
//
//   * 算法（docs/v21/CHECKPOINT_RECIP.md，50013 样本失配 0）：
//       u = recip_q60(G)                 // 近似商，误差 ∈ {-1, 0, +1}
//       q = u；用**真实 G** 双向夹逼到 floor(2^60/G)
//       r = 2^60 - q*G
//       I = q + (2r >= G ? 1 : 0)        // 最近舍入、恰半离零
//
//   * **为什么 v2.0 把中间级全部去掉**：
//     v1.0 用了 s1/s2 两级寄存器做条件加减，但级间数据与 valid 的对齐
//     反复出错（实测结果普遍大 1、域外点误判）。根因是：
//     在 `always_ff` 里写 `s2_v <= s1_v` 与 `if (s1_v) s2_q <= ...` 时，
//     数据与 valid 的可见时刻受非阻塞赋值语义影响，极易错位；
//     外部再补延迟寄存器只会叠加不确定性（试过：当前 g / 1 级 / 2 级都不对）。
//
//     v2.0 把"夹逼 + 舍入"整体做成**一段组合逻辑**，只在入口和出口各打一拍：
//       入口拍：g_q30 送入 recip_q60，同时把 g 存进 g_hold 队列头
//       出口拍：recip_q60 给出 valid_o/inv，此时用**同拍**的 g（由
//               recip_q60 的 g_q30_o 给出，它内部保证与 inv 同笔）
//               组合算出 I，寄存输出
//     组合路径 = 一次乘法 + 两次比较 + 一次减法 + 一次比较。
//     这仍远小于 64/64 除法，且没有任何级间对齐问题。
//
//   * 误差界前提：`recip_q60` 在合同域 [2^34, 2^36] 上误差 <= 1。
//     由 tools/check_recip_correction.py（50013 样本）与 tb_recip_q60
//     （43766 笔 RTL 对齐）共同支撑。**域若放宽必须重验。**
// 变更记录   :
//   2026-11-16  v1.0 两级条件加减（级间对齐反复出错）。
//   2026-11-16  v2.0 改为单级组合 + 入口/出口寄存，消除级间对齐问题。
// =============================================================================
module recip_rnd_v21 (
    input  logic        clk,
    input  logic        rst_n,

    input  logic        valid_i,
    input  logic [43:0] g_q30,          // G，合同域 [2^34, 2^36]

    output logic        valid_o,
    output logic [26:0] i_q60,          // I = RND(2^60, G)
    output logic        err_o           // G 越出合同域 / G = 0
);

    localparam logic [63:0] TWO60 = 64'h1000_0000_0000_0000;

    // ------------------------------------------------------------------ 倒数
    logic        r_valid, r_err;
    logic [26:0] r_inv;
    logic [43:0] r_g;        // 与 r_inv 同笔的 G（recip_q60 内部对齐输出）

    recip_q60 u_recip (
        .clk(clk), .reset_n(rst_n),
        .valid_i(valid_i), .g_q30(g_q30),
        .valid_o(r_valid), .inv_q60(r_inv),
        .g_q30_o(r_g), .err_o(r_err));

    // ------------------------------------------------------------------ 修正（纯组合）
    //
    // 用 r_g（与 r_inv 严格同笔）算 q*G 与余数，不需要任何额外延迟。
    logic [63:0] g64;
    logic [63:0] p0, p1;          // q*G、(q+1)*G
    logic [26:0] q0, q1;
    logic [63:0] prod;
    logic [63:0] rem, two_rem;
    logic [26:0] i_c;
    logic        g_ok;
    logic        e_c;

    always_comb begin
        g64 = {20'd0, r_g};

        // 第一步：向下夹逼（q*G 超过 2^60 就减一）
        p0 = {37'd0, r_inv} * g64;
        q0 = (p0 > TWO60) ? (r_inv - 27'd1) : r_inv;

        // 第二步：向上夹逼（(q+1)*G 仍不超过 2^60 就加一）
        p1 = {37'd0, q0 + 27'd1} * g64;
        q1 = (p1 <= TWO60) ? (q0 + 27'd1) : q0;

        // 此时 q1 == floor(2^60/G)；再做最近舍入、恰半离零
        prod    = {37'd0, q1} * g64;
        rem     = TWO60 - prod;
        two_rem = rem << 1;
        i_c     = q1 + ((two_rem >= g64) ? 27'd1 : 27'd0);

        // 域检查用同笔的 G
        g_ok = (r_g >= 44'd17179869184) && (r_g <= 44'd68719476736);
        e_c  = r_err | ~g_ok;
    end

    // ------------------------------------------------------------------ 出口寄存
    //
    // 只有这一处寄存，与 r_valid 同拍，不存在级间对齐问题。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_o <= 1'b0;
            i_q60   <= 27'd0;
            err_o   <= 1'b0;
        end else begin
            valid_o <= r_valid;
            i_q60   <= (~g_ok) ? 27'd0 : i_c;
            err_o   <= e_c;
        end
    end

endmodule
