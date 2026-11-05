// 模块：dither_gen；确定种子的离散整数dither源。
// 职责：32位非零种子的右移Galois LFSR每次展开8步，按离散PMF把raw字节映射到[-D,+D]。
// 端口/单位：dither_code为8位有符号码，valid为拒绝采样资格；不输出模拟噪声电压。
// 时钟/复位：clk上升沿、同步低有效rst_n；en推进状态，en=0保持，valid不由en门控。
// 名义PMF：接受raw均匀时端点概率为内部整数一半；实际序列统计另验，invalid码不能当作正常零值。
// 适用域：D=0..63、SEED非零；默认D2的LIMIT=256，无拒绝区；无运行时seed重载接口。
// 边界：LFSR序列为工程选择，PMF合同不声称与PCG64逐项等价；相关性须独立统计验证。
// D是整数扰动范围，生产头文件当前为2；本叶不读取Python浮点幅度字段，也没有独立dR端口。
// 码对应RDAC单位当量还是量化器扰动由调用方决定；本模块不产生参考轨或模拟注入电压。
// 来源：sampler.py整数dither分布、RTL随机源接口合同。
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

  // en沿消费者取得旧lfsr对应的dither_code，本沿NBA之后才切换下一码。
  // valid只描述当前码是否落在接受区，不是新码脉冲；调用方须连同en冻结同一笔值。
  always_ff @(posedge clk) begin
    if (!rst_n)
      lfsr <= SEED;
    else if (en)
      lfsr <= next_lfsr;
  end
endmodule
