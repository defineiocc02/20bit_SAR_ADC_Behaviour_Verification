// =============================================================================
// 文件名     : sched18.sv
// 模块名     : D1 采集与转换片组调度
// 版本       : 随 Git 源版本
// 功能       : 在 18 片间维持 ACQ、CONV 互斥分组。
// 设计说明   : 重播种只接管 PRNG 写回。当前已受理的片权推进照常完成。
// 变更记录   : 明确 seed_we 和 adv 同沿时的寄存器优先级。
// =============================================================================
// D1 · random18 片组调度器（ACQ / CONV / IDLE 三态）
//
// 生产顶层通过受理脉冲推进本模块；sched_ext_sel 仅供片组接口定向测试。
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
    // **下一周期**采集组的片号（adv 推进后的值）。
    //
    // 这是推进后的预览值。v21 接受 prepare 时冻结沿前 ids_o，同时推进调度器，
    // 下一笔才使用推进后的片组；不应把预览值替换成本笔实际绑定的片号。
    output logic [39:0] ids_next_o,
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
    assign avail = ~acq_q;          // 当前 ACQ 将进 CONV，下一次的 ACQ 只能选其补集

    // 候选表：把 avail 里置位的 10 个片号按升序压进 cand[0..9]。
    //
    // 为什么不用 `always_comb + int k + cand[k]=i`：
    // 那种写法在**作为子模块内联到顶层**时会被综合器推断成 50 个未时钟
    // 驱动的寄存器（实测 u_sched/cand_reg[0..49]），触发综合门禁
    // "unclocked sequential cells present"。根因是"变量下标写入 +
    // 过程内累加"，综合器无法证明全路径赋值完备，只能保守按锁存器处理。
    // 单独 elaborate sched18 作顶层时不暴露，因为它被完整优化掉了。
    //
    // 现在改成定长展开：第 n 个置位的位置，用一次归约表达
    //   第 n 个置位 = 满足 avail[i] 且 popcount(低位掩码) == n 的那个 i
    // ---------------------------------------------------------------- 第 n 个置位
    //
    // 原实现：对每个 gci（0..9）再对每个 i（0..17）调一次 popc18，
    // 即 10 x 18 = 180 次 popcount，每次 popcount 又是一棵 18 位加法树
    // -> DC 报出约 500 个 DW01_add，单这一步就跑了 20 分钟以上。
    //
    // 第一版改成 nth_set() 函数：每个调用仍各自做一遍 18 级前缀扫描。
    // cand(10) + ids(8) + ids_next(8) = 26 个实例 -> 26 x 18 = 468 级加法。
    //
    // 现在改为**三张共享前缀表**：avail / acq_q / acq_next 各算一次
    // pc[i] = popcount(v[0..i])，然后第 n 个置位 = "pc[i]==n 且 v[i]" 的 i。
    // 每张表 18 次加 1，共 54 级 —— 比 468 级少 88%。
    //
    // 功能完全等价（都是"第 n 个置位的位置"），由 tb_sched18 的 4096 拍
    // 逐位对拍与 L2.5 的 20000 拍统计回归覆盖。
    // i1 ∈ 0..9，j0 ∈ 0..8，i2 跳过 i1
    //
    // 为什么必须显式加宽到 32 位（原写法是 `(r1 * 16'd10) >> 16`）：
    //   Verilog 按"操作数最大位宽"定结果位宽，r1 与 16'd10 都是 16 位，
    //   乘法结果仍按 16 位截断。r1*10 最大 655350（需要 20 位），
    //   溢出后右移 16 位恒为 0，于是 i1 与 j0 永远为 0、i2 永远为 1，
    //   每拍固定丢掉候选表里最小的两片，随机性完全消失。
    //   实测 seed=0xACE10001 首拍：r1=43089，正确 (r1*10)>>16 = 6，
    //   而 16 位截断后 (37674)>>16 = 0。下面用 32'(...) 强制提升位宽。
    logic [3:0] i1, j0, i2;
    assign i1 = 4'((32'(r1) * 32'd10) >> 16);
    assign j0 = 4'((32'(r2) * 32'd9) >> 16);
    assign i2 = j0 + (j0 >= i1 ? 4'd1 : 4'd0);

    logic [17:0] drop, acq_next;

    logic [4:0] pc_avail  [0:17];
    logic [4:0] pc_acq    [0:17];
    logic [4:0] pc_next   [0:17];

    // pc[i] = **第 i 位之前**已有几个置位（不含自身）。
    // 这样"第 n 个置位"就是 "v[i] 且 pc[i]==n" 的那个 i。
    // 早前写成"含自身"会整体错位一位，实测 20396 个失配。
    always_comb begin
        logic [4:0] c;
        c = 5'd0;
        for (int i = 0; i < 18; i++) begin
            pc_avail[i] = c;
            if (avail[i]) c = c + 5'd1;
        end
    end
    always_comb begin
        logic [4:0] c;
        c = 5'd0;
        for (int i = 0; i < 18; i++) begin
            pc_acq[i] = c;
            if (acq_q[i]) c = c + 5'd1;
        end
    end
    always_comb begin
        logic [4:0] c;
        c = 5'd0;
        for (int i = 0; i < 18; i++) begin
            pc_next[i] = c;
            if (acq_next[i]) c = c + 5'd1;
        end
    end

    // 第 n 个置位：在对应的 pc 表里找第一个 "有效且 pc==n" 的位
    function automatic logic [4:0] pick(input logic [17:0] v,
                                        input logic [4:0] pc [0:17],
                                        input int n);
        logic [4:0] r;
        begin
            r = 5'd0;
            for (int i = 0; i < 18; i++)
                if (v[i] && (pc[i] == 5'(n))) r = 5'(i);
            pick = r;
        end
    endfunction

    logic [4:0] cand [0:9];
    genvar gci;
    generate
        for (gci = 0; gci < 10; gci++) begin : g_cand
            assign cand[gci] = pick(avail, pc_avail, gci);
        end
    endgenerate

    assign drop     = (18'd1 << cand[i1]) | (18'd1 << cand[i2]);
    assign acq_next = avail & ~drop;

    // ---------------------------------------------------------------- 状态
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            rng_q  <= SEED_DEFAULT;
            acq_q  <= 18'h000FF;    // 上电固定取 0..7（启动瞬态，不是随机序列）
            conv_q <= 18'h00000;
        end else begin
            // 同沿推进消耗沿前的随机数和片组。新种子由下一笔使用。
            // 片权转移与随机数写回分开，防止已经受理的事务漏推进。
            if (seed_we) rng_q <= (seed_i == 32'd0) ? SEED_DEFAULT : seed_i;
            else if (adv) rng_q <= x2;
            if (adv) begin
                conv_q <= acq_q;
                acq_q  <= acq_next;
            end
        end
    end

    // ---------------------------------------------------------------- 输出
    assign acq_mask_o  = acq_q;
    assign conv_mask_o = conv_q;
    assign idle_mask_o = ~(acq_q | conv_q);
    assign rng_o       = rng_q;

    // 采集组 8 个片号（升序）。与 cand 共用前缀表，不再各自重算。
    genvar gii;
    generate
        for (gii = 0; gii < 8; gii++) begin : g_ids
            assign ids_o[gii*5 +: 5]      = pick(acq_q,    pc_acq,  gii);
            assign ids_next_o[gii*5 +: 5] = pick(acq_next, pc_next, gii);
        end
    endgenerate

endmodule
