// D3 DEM 三维相位发生器（方案甲：RTL 内部实时生成）
//
// 每笔事务推进一次 xorshift32，取 r % 1920 作为相位索引，
// 混合进制分解为 (reserved, h_phase, v_phase)。
//
// 为什么不用三个独立 LCG 各自取模：
//   v2 实测 MI(reserved,v_phase)=1.278 bit、联合覆盖仅 41.4%。
//   改用单字取模后三轴由同一均匀整数导出，实测覆盖 1920/1920、
//   自相关 0.013~0.018（排列零分布 95 分位约 0.066~0.079）。
//
// sample_adv 的行为（由顶层保证）：
//   - 仅在"事务被真正接受"时拉高一拍
//   - stall / 被拒 start / 非法模式切换 都不得推进
//   - reset 把种子恢复为初值，相位回到确定起点
//
// 输出时序（2026-11-15 修正）
// ---------------------------
// 原实现把 (reserved_o, h_phase_o, v_phase_o) 接在组合的 t3 = xs(rng_q) 上，
// 而 rng_q 要到同一个 posedge 才更新。后果有两层：
//   1) 相位比 rng_q 早一拍：sample_adv 拉高的那一拍，下游看到的已经是
//      "下一笔的相位"，与本模块文档所说"每笔推进一次"不符；
//   2) xs() -> %1920 -> /120 这条长组合链直接挂在输出上，采样沿前必须稳定，
//      给下游 USE 的建立时间额外压力。
// 现在改为在 always_ff 里同时寄存 rng_q 与三轴相位，输出即寄存器输出：
// 复位后输出为 SEED 的相位（确定起点），此后每次 sample_adv 前进一格。
module dem_phase_gen (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        sample_adv,     // 一笔事务接受时推进一拍
    output logic [3:0]  reserved_o,
    output logic [3:0]  h_phase_o,
    output logic [2:0]  v_phase_o,
    output logic [31:0] rng_o,

    // "下一态"观侧口：本拍 sample_adv 生效后，上面四个输出将要变成的值。
    // 供 d3d4_ip 在握手沿上组合取用，避免整条数据通路滞后一拍。
    // 这几个端口是纯组合，不参与寄存器更新，综合后就是同一份 xorshift 逻辑
    // 的另一个扇出，不会额外复制状态。
    output logic [3:0]  nxt_reserved_o,
    output logic [3:0]  nxt_h_phase_o,
    output logic [2:0]  nxt_v_phase_o,
    output logic [31:0] nxt_rng_o
);

    localparam logic [31:0] SEED = 32'h1234_5678;

    // 相位索引的合法范围 0..1919 由 idx 的取模保证；三轴共用同一个 idx，
    // 这是"三轴由同一均匀整数导出"的实现方式（见文件头）。
    localparam logic [31:0] PHASE_TOTAL = 32'd1920;
    localparam logic [31:0] RES_SPAN    = 32'd120;   // 1920 / 16
    localparam logic [31:0] H_STEP      = 32'd8;     // 1920 / 240
    localparam logic [31:0] H_MOD       = 32'd15;

    logic [31:0] rng_q;
    logic [3:0]  res_q, hp_q;
    logic [2:0]  vp_q;

    // 一步 xorshift32（13/17/5）。写成函数而不是三段 wire，
    // 是为了让"当前态"和"下一个态"共用同一段逻辑，避免两处各写一遍产生偏差。
    function automatic logic [31:0] xs32(input logic [31:0] x);
        logic [31:0] t;
        t = x ^ (x << 13);
        t = t ^ (t >> 17);
        t = t ^ (t << 5);
        return t;
    endfunction

    // 把 32 位整数拆成混合进制三轴。注意 %1920 是常数取模，
    // 综合会展开成乘加（见 docs/SYNTH_* 的面积记录）。
    function automatic logic [31:0] phase_index(input logic [31:0] r);
        return r % PHASE_TOTAL;
    endfunction

    wire [31:0] nxt   = xs32(rng_q);          // 下一个随机态
    wire [31:0] idx_n = phase_index(nxt);     // 下一个相位索引

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            rng_q    <= SEED;
            // 复位即输出"种子对应的相位"，保证起点可复现
            res_q    <= 4'(phase_index(SEED) / RES_SPAN);
            hp_q     <= 4'((phase_index(SEED) / H_STEP) % H_MOD);
            vp_q     <= 3'(phase_index(SEED) % 8);
        end else if (sample_adv) begin
            rng_q    <= nxt;
            res_q    <= 4'(idx_n / RES_SPAN);
            hp_q     <= 4'((idx_n / H_STEP) % H_MOD);
            vp_q     <= 3'(idx_n % 8);
        end
    end

    assign reserved_o = res_q;
    assign h_phase_o  = hp_q;
    assign v_phase_o  = vp_q;
    assign rng_o      = rng_q;

    assign nxt_reserved_o = 4'(idx_n / RES_SPAN);
    assign nxt_h_phase_o  = 4'((idx_n / H_STEP) % H_MOD);
    assign nxt_v_phase_o  = 3'(idx_n % 8);
    assign nxt_rng_o      = nxt;

endmodule
