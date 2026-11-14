// D1 · random18 片组调度器（ACQ / CONV / IDLE 三态）
//
// 与 legacy9（slice_pool.sv 的 8 片连续轮转）并列，由顶层 sched_mode_i 选择。
//
// 每拍推进一次（adv = 一笔事务被接收）：
//   1) conv' = acq                上一周期的采集组进入转换，整周期冻结
//   2) avail = ~conv'             10 片候选
//   3) xorshift32 走两步，取 r1/r2
//        i1 = (r1*10)>>16, j0 = (r2*9)>>16, i2 = j0 + (j0 >= i1)
//   4) cand[] 为 avail 中置位的片号（升序），把 cand[i1]/cand[i2] 置 IDLE
//   5) acq' = avail − {cand[i1], cand[i2]}     （8 片）
//
// 布局：cand 用 10×5 bit 的紧凑列表。等于把"在 10 片里选 2 片空出"做成
// 一次部分 Fisher-Yates（i2 的 (j0>=i1) 修正保证不重复），均匀覆盖 45 组空出对。
//
// 不变量（硬连线的结构性保证，不靠自检）：
//   * acq' ⊆ ~conv'  ⇒ ACQ 与 CONV 恒不相交
//   * |acq'| = 10 − 2 = 8，|conv'| = 8，IDLE = 2，并集 = 18
//
// PRNG：32 位 xorshift（13/17/5）。**不用 Fibonacci LFSR**——LFSR 的低 16 位是
// 同一 m 序列的连续 16 拍，作为随机数高度相关（实测 IDLE 对 4000 拍下
// min=1/max=181，严重不均）。xorshift 每拍整体混合，实测片使用不平衡度 0.24%。
// 明确不宣称与 ADI 实际选片序列逐位相同：论文未公开种子与状态序列。
module sched18 (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        seed_we,        // 同步装载种子（只在流水空闲时用）
    input  logic [31:0] seed_i,         // 不能为 0（xorshift 的零点）
    input  logic        adv,            // 一笔事务被接收 -> 推进一个采样周期
    output logic [17:0] acq_mask_o,     // 本周期采集的 8 片
    output logic [17:0] conv_mask_o,    // 本周期转换的 8 片（= 上周期 acq）
    output logic [17:0] idle_mask_o,    // 本周期空闲的 2 片
    output logic [39:0] ids_o,          // 本周期采集组，8×5 bit，按片号升序
    output logic [31:0] rng_o
);

    localparam logic [31:0] SEED_DEFAULT = 32'hACE1_0001;

    logic [31:0] rng_q;
    logic [17:0] acq_q, conv_q;

    // ---------------------------------------------------------------- PRNG
    function automatic logic [31:0] xs32(input logic [31:0] x);
        logic [31:0] t;
        t = x ^ (x << 13);
        t = t ^ (t >> 17);
        t = t ^ (t << 5);
        return t;
    endfunction

    logic [31:0] x1, x2;
    logic [15:0] r1, r2;
    assign x1 = xs32(rng_q);
    assign x2 = xs32(x1);
    assign r1 = x1[15:0];
    assign r2 = x2[31:16];

    // ---------------------------------------------------------------- 候选表
    logic [17:0] avail;
    assign avail = ~acq_q;          // 与 ~conv_q 等价： conv_q == acq_q 的上一版

    logic [4:0] cand [0:9];
    always_comb begin
        int k;
        k = 0;
        for (int i = 0; i < 18; i++) begin
            if (avail[i]) begin
                if (k < 10) cand[k] = 5'(i);
                k = k + 1;
            end
        end
    end

    // i1 ∈ 0..9，j0 ∈ 0..8，i2 跳过 i1
    logic [3:0] i1, j0, i2;
    assign i1 = 4'((r1 * 16'd10) >> 16);
    assign j0 = 4'((r2 * 16'd9) >> 16);
    assign i2 = j0 + (j0 >= i1 ? 4'd1 : 4'd0);

    logic [17:0] drop, acq_next;
    assign drop     = (18'd1 << cand[i1]) | (18'd1 << cand[i2]);
    assign acq_next = avail & ~drop;

    // ---------------------------------------------------------------- 状态
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            rng_q  <= SEED_DEFAULT;
            acq_q  <= 18'h000FF;    // 上电固定取 0..7（启动瞬态，不是随机序列）
            conv_q <= 18'h00000;
        end else begin
            if (seed_we) rng_q <= (seed_i == 32'd0) ? SEED_DEFAULT : seed_i;
            if (adv) begin
                conv_q <= acq_q;
                acq_q  <= acq_next;
                rng_q  <= x2;
            end
        end
    end

    // ---------------------------------------------------------------- 输出
    assign acq_mask_o  = acq_q;
    assign conv_mask_o = conv_q;
    assign idle_mask_o = ~(acq_q | conv_q);
    assign rng_o       = rng_q;

    always_comb begin
        logic [4:0] ids [0:7];
        int k;
        k = 0;
        for (int i = 0; i < 18; i++) begin
            if (acq_q[i]) begin
                if (k < 8) ids[k] = 5'(i);
                k = k + 1;
            end
        end
        for (int j = 0; j < 8; j++) ids_o[j*5 +: 5] = ids[j];
    end

endmodule
