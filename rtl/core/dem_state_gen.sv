// 模块：dem_state_gen；A/B bank独立推进的确定性DEM状态。
// 职责：sid_b(n)=(n*A) mod2^W，以W位加性递推实现；默认A mod512=433、W=9。
// 端口/单位：sid/init为无符号状态，en_a/en_b分别消耗对应bank当前状态，可同时有效。
// 时钟/复位：clk上升沿、同步低有效rst_n；优先级reset>load>dem_en下的独立enable>保持。
// 对齐：消费者在enable沿使用旧sid，该沿随后推进；load同沿不推进。
// 关闭：dem_en=0时对外输出0、内部状态保持，load仍可装初值，重新使能不会自动重播种。
// 适用域：W=1..32、A_RED为奇数；奇数步长保证全W位状态周期，不等同随机熵源。
// 来源：mapper.dem_state_sequence与参数导出器；状态顺序、位宽和bank归属均属数字接口合同。
`include "rtl_params.vh"

module dem_state_gen #(
    parameter int A_RED = int'(DEM_LCG_A_MOD),
    parameter int W     = 9
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         dem_en,
    input  logic         load,
    input  logic [W - 1:0] init_a,
    input  logic [W - 1:0] init_b,
    input  logic         en_a,
    input  logic         en_b,
    output logic [W - 1:0] sid_a,
    output logic [W - 1:0] sid_b
);

  initial begin
    if (W < 1 || W > 32 || (A_RED & 1) == 0)
      $fatal(1, "dem_state_gen: W must be 1..32 and step must be odd");
  end

  // 状态与 STEP 都是无符号 W 位整数；低 W 位累加实现模 2^W。
  // 不需要通用乘法或运行时取模；奇数步长保证遍历整个 W 位状态空间。
  localparam logic [W - 1:0] STEP = A_RED[W - 1:0];
  localparam logic [W - 1:0] MASK = {W{1'b1}};

  logic [W - 1:0] state_a, state_b;

  // 时序边界：reset > load > dem_en 下独立的 en_a/en_b > 保持。
  // 不把两路 enable 合成全局样本计数，避免每 bank 只访问半数 DEM 状态。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      state_a <= {W{1'b0}};
      state_b <= {W{1'b0}};
    end else if (load) begin
      state_a <= init_a;
      state_b <= init_b;
    end else if (dem_en) begin
      if (en_a)
        state_a <= (state_a + STEP) & MASK;
      if (en_b)
        state_b <= (state_b + STEP) & MASK;
    end
  end

  // 组合输出门控；DEM 关闭不会清除 state，重新使能也不会自动重新播种。
  assign sid_a = dem_en ? state_a : {W{1'b0}};
  assign sid_b = dem_en ? state_b : {W{1'b0}};

endmodule
