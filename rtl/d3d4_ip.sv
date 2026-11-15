// D3/D4 数字 IP 顶层（可综合、单时钟域）
//
// 本模块把三个叶子模块接成一条真正的 IP 数据通路：
//
//   k_i ──────────────┐
//   dither_i ─ dither_pre ── pre_o / U / f
//   sample_adv_i ─ dem_phase_gen ── reserved / h_phase / v_phase
//                     └─ dem_mask_v3 ── mask（8×20）
//
// 为什么需要这一层
// ----------------
// `dem_mask_v3` 与 `dither_pre` 都是**纯组合**模块：没有 clk、没有 reset，
// 相位与 coin 全从端口喂。这意味着它们单独既不能作为 IP 交付，也无法通过
// 任何"单时钟域"的综合门禁（synth/run_dc.tcl 要求恰好一个时钟域）。
// 更重要的一点：D3 的"三维随机化"在叶子层是**不存在的**——相位由外部给。
// 本层把 `dem_phase_gen` 接进来，随机化才第一次真正落到 RTL 里。
//
// 时序约定（关键，容易接错）
// --------------------------
// * 一笔事务 = 一个 `valid_i & ready_o` 的握手拍。
// * 相位在"事务被接受"的那一拍推进（sample_adv 与握手同沿），
//   因此本拍输出的是**推进后**的相位；下游必须与 mask 同拍采样。
// * mask / pre / U / f 全部是寄存器输出，与相位、k、dither 严格同拍，
//   不存在组合穿透到下游的问题（这是原叶子模块做不到的）。
// * coin 由相位发生器之外的 PRNG 位给出。这里用 rng 的最低位以外的位，
//   避免与 v_phase 共用 idx[2:0] 造成相关性：
//   见 dem_phase_gen 的 idx 分解（v_phase = idx[2:0]），故 coin 取 rng[31]，
//   与 idx 的低位无直接耦合。
//
// 位宽与顺序
// ----------
// mask_o 是打包向量：bit = 20*s + j，s ∈ 0..7（片），j ∈ 0..19（单元）。
// pre_o  是打包向量：bit = 4*s + b，  s ∈ 0..7（片），b ∈ 0..3（开关位）。
// 之所以打包而不是保持非打包数组：非打包数组端口在 DC 里会被自动展平，
// 且展平顺序与源码下标相反（实测见 docs/SYNTH_D3D4.md），
// 显式打包可以把这个约定固定下来，门级仿真才不会错位。
module d3d4_ip (
    input  logic        clk,
    input  logic        reset_n,

    // ---- 事务入口 ----
    input  logic        valid_i,
    input  logic [9:0]  k_i,          // RDAC 码 0..520（超出由上游报告错误）
    input  logic [5:0]  dither_i,     // D6..D1
    input  logic        sample_adv_i, // 本笔事务是否推进相位（通常 = valid_i & ready_o）
    output logic        ready_o,

    // ---- 结果（全部寄存器输出，与输入同拍对齐）----
    output logic [159:0] mask_o,      // 8 片 × 20 单元，bit = 20*s + j
    output logic [31:0]  pre_o,       // 8 片 × 4 位，    bit = 4*s + b
    output logic [2:0]   upper_o,     // U（粗码偏移）
    output logic [3:0]   f_o,         // f（预置片数）

    // ---- 相位观测口（供 TB 与顶层核对，不参与数据通路）----
    output logic [3:0]   reserved_o,
    output logic [3:0]   h_phase_o,
    output logic [2:0]   v_phase_o
);

    // 本 IP 无内部反压：只要不在复位就绪。
    // 之所以显式给出 ready_o，是为了让上层能用统一的 valid/ready 握手接入，
    // 而不是靠"调用方自己数拍"——后者在流水重叠时极易错位。
    assign ready_o = reset_n;

    logic        adv;
    assign adv = sample_adv_i & valid_i & reset_n;

    // ---------------------------------------------------------------- 相位
    // 相位发生器的输出是**寄存器输出**，它在 adv 的同一个 posedge 更新。
    // 因此"握手沿上组合可见的 hp_w"仍是**上一笔**的相位，而下一笔要用的
    // 是新相位。为了让 mask/pre 与本拍输出严格同拍（不自带一拍滞后），
    // 这里不在组合路径上用 hp_w，而是显式取"下一个相位"：
    //
    //     nxt_*  = 本拍 adv 之后 hp_w 将要变成的值
    //
    // 代价是需要把相位发生器的"下一态"再算一遍（xorshift 一步 + 取模）。
    // 这比让整条数据通路滞后一拍更划算：滞后的后果是 mask 与 k 错拍，
    // 下游按同拍消费时会把第 i 笔的相位配到第 i-1 笔的 k 上，且极难发现。
    logic [3:0] res_w, hp_w;
    logic [2:0] vp_w;
    logic [31:0] rng_w;
    logic [3:0] res_n, hp_n;
    logic [2:0] vp_n;
    logic [31:0] rng_n;

    // 相位发生器选择（编译期宏）
    // ---------------------------
    // 默认用 round-robin（`dem_phase_rr`），依据：
    //   1) ISSCC2024 §9.8 PPT 第 29–31 页原文是 `Round robin shuffler`；
    //   2) 实测统计全面更优：χ²=0、三轴 MI=0.0000、一周期零重复；
    //   3) 综合面积降 5.1 倍（392.78 -> 77.71 µm²）、延迟降 7.7 倍（4.38 -> 0.57 ns）。
    // 详见 docs/PHASE_AB.md。
    //
    // 定义 D3D4_PHASE_XORSHIFT 可切回旧的 xorshift 版做 A/B 对照。
    // 两版**端口语义完全一致**（含 nxt_* 观察口），因此切换不影响下游。
`ifdef D3D4_PHASE_XORSHIFT
    dem_phase_gen u_phase (
        .clk        (clk),
        .reset_n    (reset_n),
        .sample_adv (adv),
        .reserved_o (res_w),
        .h_phase_o  (hp_w),
        .v_phase_o  (vp_w),
        .rng_o      (rng_w),
        .nxt_reserved_o (res_n),
        .nxt_h_phase_o  (hp_n),
        .nxt_v_phase_o  (vp_n),
        .nxt_rng_o      (rng_n)
    );
`else
    dem_phase_rr u_phase (
        .clk        (clk),
        .reset_n    (reset_n),
        .sample_adv (adv),
        .reserved_o (res_w),
        .h_phase_o  (hp_w),
        .v_phase_o  (vp_w),
        .idx_o      (rng_w),   // 语义由"随机态"变为"相位索引 0..1919"，位宽一致
        .nxt_reserved_o (res_n),
        .nxt_h_phase_o  (hp_n),
        .nxt_v_phase_o  (vp_n),
        .nxt_idx_o      (rng_n)
    );
`endif

    // 本拍真正生效的相位：有握手就是"推进后"，无握手就是保持不变。
    logic [3:0] res_use, hp_use;
    logic [2:0] vp_use;
    logic [31:0] rng_use;
    assign res_use = adv ? res_n : res_w;
    assign hp_use  = adv ? hp_n  : hp_w;
    assign vp_use  = adv ? vp_n  : vp_w;
    assign rng_use = adv ? rng_n : rng_w;

    // coin：bridge 交换是否发生（binary-to-unary bridging 的使能）。
    //
    // 这里踩过一个坑，必须记下来：
    //   原来写 `coin = rng_use[31]`，因为 xorshift 版 rng 是 32 位随机态，
    //   最高位混合良好。但切到 round-robin 后 `rng_use` 变成**相位索引**
    //   （0..1919，实际只占 11 位），bit[31] 恒为 0 —— bridge 永远不会触发，
    //   而且不会报任何错，只是"看起来对了但功能没发生"。
    //
    // 现在的做法：从索引的高位区派生，且保证与三轴取值不相关。
    //   v_phase = idx % 8        -> 用 idx[2:0]
    //   h_phase = (idx/8) % 15   -> 与 idx[3:6] 有关
    //   因此 coin 取 idx[10:7]（索引最高可到 1919，即 bit10 有效）
    //   的奇偶校验：与 v_phase 无重叠、与 h_phase 的模 15 也非同源。
    // 对 xorshift 版同样成立（rng 高位同样可用），所以两版共用这一条。
    logic coin;
    assign coin = ^rng_use[10:7];

    // ---------------------------------------------------------------- 叶子模块
    logic [159:0] mask_c;     // dem_mask_v3 的组合输出（已打包）
    logic [31:0]  pre_c;      // dither_pre 的组合输出（已打包）

    dem_mask_v3 u_dem (
        .k_i        (k_i),
        .h_phase_i  (hp_use[2:0]),
        .v_phase_i  (vp_use),
        .coin_i     (coin),
        .acq_seq_i  ({res_use[0], 3'b0} | {3'b0, hp_use[0]}),  // bit3 = half 选择
        .mask_o     (mask_c)
    );

    dither_pre u_dither (
        .word_i     (dither_i),
        .v_phase_i  (vp_use),
        .pre_o      (pre_c),
        .U_o        (),
        .f_o        ()
    );

    // ---------------------------------------------------------------- 输出寄存
    // 三个结果寄存器在同一个 always_ff 里，保证严格同拍。
    // 不用 generate 展开，是为了让"全部输出同一沿更新"这件事在源码上一眼可见。
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            mask_o     <= '0;
            pre_o      <= '0;
            upper_o    <= '0;
            f_o        <= '0;
            reserved_o <= '0;
            h_phase_o  <= '0;
            v_phase_o  <= '0;
        end else if (valid_i && ready_o) begin
            mask_o     <= mask_c;
            pre_o      <= pre_c;
            // U 与 f 直接从 dither 字派生，口径与 dither_pre 内部一致：
            //   U = 4*D6 + D4
            //   f = 4*D3 + 2*D2 + D1
            // 这里重算一遍而不接线叶子输出，是为了让综合能把 U/f 与 pre 一起
            // 放在同一个时钟沿之前，避免 dither_pre 的 U_o/f_o 端口形成额外扇出。
            upper_o    <= {1'b0, dither_i[5]} * 3'd4 + {2'b0, dither_i[3]};
            f_o        <= {1'b0, dither_i[2], dither_i[1], dither_i[0]};
            reserved_o <= res_use;
            h_phase_o  <= hp_use;
            v_phase_o  <= vp_use;
        end
    end

endmodule
