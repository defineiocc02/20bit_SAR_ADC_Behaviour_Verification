//===========================================================================
// dither_gen.sv -- dither 码生成（**统计验收**，不追求与模型逐位一致）
//===========================================================================
// 职责一句话
//   产出一个近似均匀、支撑集精确的整数 dither 码，替代模型的 `round(uniform(-D, D))`。
//
// 来源
//   adi_model/sampler.capture() 的 dither 支路：`dither_code = round(uniform(-D, D))`
//   （`dither_mode == "sampling"` 时 `D = cfg.dither_units_range`）。
//
// 单位契约
//   无量纲整数（RDAC 单位当量）。dither_code ∈ [-D, +D]，共 2D+1 个取值。
//
// 参数来源分级
//   D 默认取头文件的 DITHER_UNITS_RANGE（[假设]，由 Config 导出）。
//   SEED 是 RTL 设计选择（[假设]）—— 它只影响序列，不影响分布。
//
// 契约与不变量 / 适用域
//   * **本模块永远不可能与模型逐位一致**：模型的随机源是 numpy 的 PCG64，
//     RTL 无法复现（见 docs/rtl/P1_INTERFACE.md §0.1）。因此验收判据是
//     "支撑集精确 + 近似均匀 + 均值≈0 + 跨拍不相关 + 边界值出现"，
//     由 TB 统计后交 Python 复核。
//   * **连带后果**：任何要求 bit-exact 的链路级测试必须把 dither 码**作为激励注入**，
//     不能让两侧各自随机。本模块只是"片上随机源"的可综合占位实现。
//   * 精确均匀靠拒绝采样：`raw` 取 8 位，只接受 `raw < (256/SPAN)*SPAN`
//     （SPAN = 2D+1），再取 `raw mod SPAN`。这样每个取值恰好被映射同样多的 raw 值，
//     不留余数偏置 —— 这是"近似"变成"精确"的那一步。
//   * 被拒绝的那一拍 `valid = 0`，调用方应当**忽略**该拍输出（不要用 valid=0 的码）。
//===========================================================================
`include "rtl_params.vh"

module dither_gen #(
    parameter int          D    = DITHER_UNITS_RANGE,
    parameter logic [31:0] SEED = 32'h1357_9BDF
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              en,
    output logic signed [7:0] dither_code,
    output logic              valid
);

  localparam int SPAN  = 2 * D + 1;
  localparam int LIMIT = (256 / SPAN) * SPAN;  // 不大于 256 的 SPAN 最大倍数
  localparam logic [31:0] TAPS = 32'h8020_0003;  // x^32 + x^22 + x^2 + x + 1

  logic [31:0] lfsr;
  logic [7:0]  raw;
  logic [7:0]  reduced;

  assign raw     = lfsr[31:24];
  assign reduced = raw % 8'(SPAN);
  assign valid   = (9'(raw) < 9'(LIMIT));

  assign dither_code = $signed({1'b0, reduced}) - 8'(D);

  always_ff @(posedge clk) begin
    if (!rst_n) lfsr <= SEED;
    else if (en) lfsr <= {lfsr[0], lfsr[31:1]} ^ (lfsr[0] ? TAPS : 32'h0);
  end

endmodule
