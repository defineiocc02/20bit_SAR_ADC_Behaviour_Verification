// D3 DEM 三维相位发生器（方案乙：确定性 round-robin 轮转）
//
// 为什么要有这一版
// ----------------
// ISSCC 2024 §9.8 的 PPT（第 29–31 页）在 DEM 的三维度里明确写的是
// **`Round robin shuffler`**，不是伪随机。仓库里的 `dem_phase_gen.sv`
// 用的是 xorshift32 伪随机，二者语义不同：
//
//   * round-robin：确定性轮转，一个计数器就能实现，无 PRNG 周期性伪影，
//     硬件极简（面积/功耗都更低），行为可完全预测与复现。
//   * xorshift：伪随机，能保证"三轴互信息≈0、联合覆盖满"，但需要
//     32 位状态 + 常数取模（`%1920` 在综合里展开成大片组合逻辑），
//     且序列有周期（2^32-1 量级，对本应用足够长）。
//
// 本模块实现 round-robin 版本，与 `dem_phase_gen.sv` **接口完全一致**
// （含 `nxt_*` 观察口），以便：
//   1) `d3d4_ip` 可以用一个参数在两者之间切换；
//   2) 用同一套 TB 与金标做 A/B 对比，用数据而不是口味来选。
//
// 轮转规则（与 PPT 图的三条箭头一一对应）
// --------------------------------------
// 相位索引按 0..1919 线性递增，每笔事务 +STEP：
//   res  = idx / 120        （1920/16）   片间纵向轮转（对应右侧大箭头）
//   h    = (idx / 8) % 15   （1920/240）  片内横向轮转（对应顶部箭头）
//   v    = idx % 8                        单元纵向轮转
//
// 为什么 STEP 取 1 且用"线性递增 + 混合进制分解"而不是三条独立计数器：
// 三条独立计数器会退化成"三轴同步推进"，使 (res,h,v) 只在一条对角线上取值，
// 联合覆盖永远只有 1920 中的一小部分。用**单一索引线性递增再分解**，
// 等价于把 1920 个组合按混合进制排成一个环依次遍历 —— 一个周期恰好
// 覆盖全部 1920 组，且每个组合只出现一次。这是 round-robin 能同时满足
// "确定性"与"满覆盖"的关键，也是它相对三段独立计数器的本质优势。
//
// 与 xorshift 版的取舍（由 tools/phase_ab.py 实测，数据见 docs/PHASE_AB.md）：
//   * round-robin 覆盖一定是 1920/1920（结构保证，不是概率）
//   * xorshift 覆盖靠概率，17280 拍实测也达到 1920/1920
//   * round-robin 的三轴互信息**不严格为 0**（因为是线性分解），
//     但它的分布是"每 1920 拍恰好均匀一次"，对失配平均更可控
module dem_phase_rr (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        sample_adv,     // 一笔事务接受时推进一次
    output logic [3:0]  reserved_o,
    output logic [3:0]  h_phase_o,
    output logic [2:0]  v_phase_o,
    output logic [31:0] idx_o,          // 当前相位索引 0..1919（替代 rng_o）

    // "下一态"观察口，语义与 dem_phase_gen 的 nxt_* 完全一致：
    // 本拍 sample_adv 生效后，上面四个输出将要变成的值。
    output logic [3:0]  nxt_reserved_o,
    output logic [3:0]  nxt_h_phase_o,
    output logic [2:0]  nxt_v_phase_o,
    output logic [31:0] nxt_idx_o
);

    localparam logic [10:0] PHASE_TOTAL = 11'd1920;
    localparam logic [6:0]  RES_SPAN    = 7'd120;   // 1920 / 16
    localparam logic [6:0]  H_STEP      = 7'd8;     // 1920 / 240
    localparam logic [3:0]  H_MOD       = 4'd15;

    // 索引寄存器：11 位足够（0..1919），但对外按 32 位给出，
    // 便于与 dem_phase_gen 的 rng_o 在位宽上互换（顶层不用改位宽）。
    logic [10:0] idx_q;

    // 下一索引：取模 1920。因为 idx_q < 1920，`idx_q + 1` 最多到 1920，
    // 所以这里等价于"加 1 后判断是否回 0"，不需要真正的取模运算器。
    // 这一点是 round-robin 相对 xorshift 的**最大综合优势**：
    // xorshift 版要算 `t3 % 1920`（32 位常数取模，展开后 146 级逻辑），
    // 这里只要一个 11 位比较 + 清零。
    wire [10:0] idx_n = (idx_q == PHASE_TOTAL - 11'd1) ? 11'd0
                                                       : (idx_q + 11'd1);

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n)     idx_q <= 11'd0;
        else if (sample_adv) idx_q <= idx_n;
    end

    // 混合进制分解。注意这些除法都是**常数除法**，综合器会展开成乘加，
    // 但常数是 120/8/15 这类小数值，规模远小于 %1920。
    assign reserved_o = 4'(idx_q / RES_SPAN);
    assign h_phase_o  = 4'((idx_q / H_STEP) % H_MOD);
    assign v_phase_o  = 3'(idx_q % 8);
    assign idx_o      = 32'(idx_q);

    assign nxt_reserved_o = 4'(idx_n / RES_SPAN);
    assign nxt_h_phase_o  = 4'((idx_n / H_STEP) % H_MOD);
    assign nxt_v_phase_o  = 3'(idx_n % 8);
    assign nxt_idx_o      = 32'(idx_n);

endmodule
