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
module dem_phase_gen (
    input  logic       clk,
    input  logic       reset_n,
    input  logic       sample_adv,     // 一笔事务接受时推进一拍
    output logic [3:0] reserved_o,
    output logic [3:0] h_phase_o,
    output logic [2:0] v_phase_o,
    output logic [31:0] rng_o
);

    localparam logic [31:0] SEED = 32'h1234_5678;

    logic [31:0] rng_q;
    wire  [31:0] x  = rng_q;
    wire  [31:0] t1 = x ^ (x << 13);
    wire  [31:0] t2 = t1 ^ (t1 >> 17);
    wire  [31:0] t3 = t2 ^ (t2 << 5);

    wire  [31:0] idx = t3 % 32'd1920;   // 常数取模，综合会展开为乘加

    assign reserved_o = 4'(idx / 32'd120);   // idx<1920 -> 0..15
    assign h_phase_o  = 4'((idx / 32'd8) % 32'd15);
    assign v_phase_o  = idx[2:0];
    assign rng_o      = rng_q;

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n)      rng_q <= SEED;
        else if (sample_adv) rng_q <= t3;
    end

endmodule
