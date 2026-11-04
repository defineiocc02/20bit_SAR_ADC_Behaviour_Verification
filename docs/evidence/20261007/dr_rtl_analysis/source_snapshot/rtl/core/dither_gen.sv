// Integer sampling dither: same PMF as round(uniform(-D,D)) in sampler.py.
// Endpoints have half the probability of an interior integer. This is not a
// bit-exact replacement for PCG64; the PRNG sequence is an RTL design choice.
//
// Right-shift Galois LFSR, period 2^32-1 for every nonzero seed. The reduction
// polynomial is verified independently in tests/unit/test_rtl_review_regressions.py.
// IMPORTANT: shift in ZERO before XOR. Rotating bit0 into bit31 cancels the
// feedback MSB and makes raw[31:24] collapse to zero (review R1, 2026-09-19).
//
// Advance eight LFSR bits per draw to avoid overlapping 8-bit windows.
// gcd(8, 2^32-1)=1, so decimation preserves the full state period.
// en=0 holds state. Rejected draws have valid=0 and must not be consumed.
// D=0 produces zero. Supported D is 0..63; a zero seed is prohibited.
//
// 工程边界
//   当前 LFSR 状态组合生成 raw/valid/dither_code；en 只推进状态，不门控 valid。
//   调用方必须在需要抽样且 valid=1 时才冻结 dither_code；无效抽样仍需推进
//   LFSR 后重新抽样，不能把 invalid 的数值当成零或停在同一个被拒绝状态。
//   rst_n 为同步低有效复位，优先于 en；没有运行时 seed 重载接口。
//   raw/reduced 为无符号 8 位；rounded 为 9 位；centered 是有符号 10 位，
//   合法抽样映射到 [-D,+D]，再截为接口的 8 位二补数。无浮点或高斯噪声模型。
//   上述 PMF 指理想均匀 raw 下的整数映射；实际 LFSR 序列的分布与相关性
//   仍需统计回归，不应据此宣称与软件随机数序列或模拟噪声逐项等价。
`include "rtl_params.vh"

module dither_gen #(
    parameter int D = int'(DITHER_UNITS_RANGE),
    parameter logic [31:0] SEED = 32'h1357_9BDF
) (
    input  logic clk,
    input  logic rst_n,
    input  logic en,
    output logic signed [7:0] dither_code,
    output logic valid
);
  localparam int SPAN = (D == 0) ? 1 : 4 * D;
  localparam int LIMIT = (256 / SPAN) * SPAN;
  localparam logic [31:0] TAPS = 32'h8020_0003;
  logic [31:0] lfsr, next_lfsr;
  logic [7:0] raw, reduced;
  logic [8:0] rounded;
  logic signed [9:0] centered;

  initial begin
    if (D < 0 || D > 63)
      $fatal(1, "dither_gen: D must be in 0..63");
    if (SEED == 0)
      $fatal(1, "dither_gen: SEED must be nonzero");
  end

  // 组合段 1：LIMIT 是不超过 256 的最大 SPAN 整倍数，拒绝尾端剩余区间。
  // valid 比较扩到 9 位，因此 LIMIT=256 可表示，不会被截成零。
  // SPAN 是 elaboration 常量；默认 D=2 时余数为模 8，可由综合化简为低位。
  assign raw = lfsr[31:24];
  assign reduced = raw % 8'(SPAN);
  assign valid = (9'(raw) < 9'(LIMIT));
  // 4D equiprobable cells -> 1 endpoint cell, 2 per interior, 1 endpoint.
  assign rounded = ({1'b0, reduced} + 9'd1) >> 1;
  assign centered = $signed({1'b0, rounded}) - 10'(D);
  assign dither_code = (D == 0) ? 8'sd0 : centered[7:0];

  // 组合段 2：展开八次 Galois 反馈，构造下一次抽样状态；这不是八拍状态机。
  // 保留右移补零与 XOR 的先后关系，不能改成旋转移位。
  always_comb begin
    next_lfsr = lfsr;
    for (int k = 0; k < 8; k++)
      next_lfsr = {1'b0, next_lfsr[31:1]} ^ (next_lfsr[0] ? TAPS : 32'h0);
  end

  // 唯一时序边界：每个 en 上升沿消耗当前抽样状态并进入 next_lfsr。
  always_ff @(posedge clk) begin
    if (!rst_n)
      lfsr <= SEED;
    else if (en)
      lfsr <= next_lfsr;
  end
endmodule
