//===========================================================================
// dem_state_gen.sv -- 按 bank 独立推进的确定性 DEM 状态序列
//===========================================================================
// 职责一句话
//   产生每个 bank 的 DEM 状态索引 sid，序列**逐项等于** Python 模型的
//   `sid(n) = (n * 2654435761) mod 512`（n = 该 bank 内的样本序号）。
//   本模块**不做**开关译码、不做地址置换（那是 dem_addr_gen / unit_therm 的事）。
//
// 来源
//   adi_model/mapper.dem_state_sequence()：`sid = (seq * _LCG_A) % N_DEM_STATES`，
//   其中 seq 是 **bank 内序号**。
//   历史坑（勿回退）：按**全局样本序号**推进时 gcd(2a, 512) = 2，两个 bank 各自只落在
//   偶数/奇数序号上，于是各只遍历 256/512 个状态 —— 另一半 LUT 永远用不到。
//
// 推导（本模块采用加性递推，而不是每次做一次乘法）
//   A = 2654435761，A mod 2**W = 433（W = 9）。
//       sid(n)      = (n * A) mod 2**W
//       sid(n + 1)  = (n*A + A) mod 2**W = (sid(n) + 433) mod 2**W
//   故只需一个 W 位累加器 + 常数加法；`mod 2**W` 就是按位与。
//   这条递推与定义式**逐项等价**，由 `p1_m1_sid.hex` 的 1024 拍逐拍比对钉住
//   （验证的是**状态顺序与样本对齐**，不只是"走满 512 态"）。
//
// 单位契约
//   全为无量纲整数。sid_a / sid_b ∈ [0, 2**W)，W = log2(DEM_STATES) = 9。
//
// 参数来源分级
//   A_RED = _LCG_A mod 2**W 是 [推导] 值，由 tools/export_rtl_params.py 从
//   adi_model 导出（`DEM_LCG_A_MOD`）。**RTL 不得手算这个常数** ——
//   契约 §9 禁止第二份参数表；`tests/unit/test_rtl_export.py` 会核对导出值。
//
// 契约与不变量 / 适用域
//   * **先使用、后推进**：`sid_*` 是**寄存器输出**，表示"本拍要用的状态"；
//     `en_*` 在时钟沿把对应 bank 推进一格。消费者必须在 `en_*` 为高的那一拍
//     采样 `sid_*`；延迟 = 0（当拍有效）。
//   * 复位后初态 = 0，即该 bank 的第 0 个样本用 sid = 0（与模型一致）。
//   * `load` 优先于 `en_*`（载入初值同拍不推进）。
//   * `en_a` 与 `en_b` 可同时为高（两 bank 独立计数），与模型一致。
//   * `dem_en` 为低时对外 `sid_*` 恒 0**且内部状态不推进**（对应模型
//     `dem_enable=False` 时 `dem_state_sequence` 返回全 0）。
//   * `gcd(A, 2**W) = 1` 是"512 个状态全遍历"的结构性前提；由 TB 的
//     "每 bank 看满 512 个不同 sid" 断言把关（A 与 2**W 不互质时该断言必红）。
//===========================================================================
`include "rtl_params.vh"

module dem_state_gen #(
    parameter int A_RED = DEM_LCG_A_MOD,
    parameter int W     = 9
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         dem_en,
    input  logic         load,
    input  logic [W-1:0] init_a,
    input  logic [W-1:0] init_b,
    input  logic         en_a,
    input  logic         en_b,
    output logic [W-1:0] sid_a,
    output logic [W-1:0] sid_b
);

  // 只在能装下 A_RED 的宽度里截取，且**不用 size cast 造无符号常量**：
  // RTL 里"表达式含一个无符号操作数就整条按无符号算"是踩过的坑（见 swap_decode.sv）。
  localparam logic [W-1:0] STEP = A_RED[W-1:0];
  localparam logic [W-1:0] MASK = {W{1'b1}};

  logic [W-1:0] state_a, state_b;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state_a <= {W{1'b0}};
      state_b <= {W{1'b0}};
    end else if (load) begin
      state_a <= init_a;
      state_b <= init_b;
    end else if (dem_en) begin
      if (en_a) state_a <= (state_a + STEP) & MASK;
      if (en_b) state_b <= (state_b + STEP) & MASK;
    end
  end

  assign sid_a = dem_en ? state_a : {W{1'b0}};
  assign sid_b = dem_en ? state_b : {W{1'b0}};

endmodule
