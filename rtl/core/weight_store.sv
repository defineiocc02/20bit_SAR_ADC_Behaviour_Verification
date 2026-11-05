// 模块：weight_store；物理权重、装载完整位图与精确行和缓存。
// 端口/单位：默认18×71个48位unsigned Q30系数，合法0<W<2^47；row_total零扩展为64位。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset清数值/行和/位图；clear只清位图并保留数值。
// 接受：wr_en&&!clear_load&&!cfg_ready且地址/数值/容量合法时写；拒绝标志err_write为组合电平。
// 替换：row_new=row_old-old_weight+new_weight，clear后仍须减去保留旧值；每row共享读旧值路径。
// 读回：selected_weight为同写地址的组合值，越界返回0；load_complete表示本epoch全部物理项写过。
// 适用域：1..32slice、1..128unit；静态容量界<2^59，可移除全局容量反馈但不改变合法写集合。
// 实现边界：当前并行读系数映射为寄存器；改成RAM须重新建立带宽、延迟与调度合同。
// 外部给定有效C/Cf（名义值或拟合后冻结值）；默认unit0..62为main、63..70为sub，不是物理C真值。
// load_complete只证明本epoch每项都被合法写过，不证明训练满秩、拟合精度或动态误差已校准。
// 来源：RTL_ARITHMETIC_CONTRACT.md §5、P2_INTERFACE.md §9；不运行参数学习。
`include "rtl_params.vh"

module weight_store #(
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_N_UNITS = int'(N_UNIT_TOTAL)
) (
    input logic clk,
    input logic rst_n,
    input logic cfg_ready, // 1 = 已生效 -> **禁止写**
    input logic clear_load, // 新epoch只撤销完整位图，旧系数和行和留待逐项覆盖
    output logic load_complete, // 本epoch每个物理项均曾接受合法写入
    input logic wr_en, // 一拍脉冲
    input logic [4:0] wr_slice,
    input logic [6:0] wr_unit,
    input logic [W_BITS - 1:0] wr_data,
    output logic err_write, // 1 = 本次写被拒
    output logic [P_N_SLICES - 1:0][P_N_UNITS - 1:0][W_BITS - 1:0] w_q,
    output wire [P_N_SLICES - 1:0][63:0] row_total,
    output wire [W_BITS - 1:0] selected_weight // 同写地址组合读回；越界读0且不能写入
);

  initial begin
    if (P_N_SLICES < 1 || P_N_SLICES > 32 || P_N_UNITS < 1 || P_N_UNITS > 128)
      $fatal(1, "weight_store: dimensions exceed address width");
  end

  localparam int SUM_BITS = 64; // SUM_BITS: 与 recon_core 同一口径（P2 §9）

  localparam logic [W_BITS - 1:0] W_ZERO = {W_BITS{1'b0}};
  localparam logic [W_BITS - 1:0] W_MAX = 48'd1 << 47; // W_MAX: 2^47
  localparam logic [SUM_BITS - 1:0] SUM_MAX = 64'd1 << 60; // SUM_MAX: 2^60
  // For every supported production geometry the capacity predicate is
  // statically true: 32*128*(2^47-1) < 2^59 < 2^60.  Keep the dynamic guard as an elaboration-time
  // fallback if either width/limit changes later.
  // Synthesis can prune the global subtract/add/compare path and sum_all
  // register. The old-word read is shared by row replacement and cfg readback.
  localparam logic STATIC_SUM_SAFE =
      (64'(P_N_SLICES) * 64'(P_N_UNITS) * (64'(W_MAX) - 64'd1)) < SUM_MAX;

  // 每项<2^47，U项行和<2^(47+ceil(log2(U)))，故ROW_BITS足够且不丢精度。
  // written描述“本epoch已写过”，不描述存储内容是否为0；clear后替换仍须减旧值，
  // 否则第二次装载会把保留的数值重复累加，破坏重构分母与系数一致性。
  localparam int ROW_BITS = 47 + $clog2(P_N_UNITS);
  logic [P_N_SLICES - 1:0][ROW_BITS - 1:0] row_q;

  logic [P_N_SLICES - 1:0][P_N_UNITS - 1:0] written;
  assign load_complete = &written;

  logic idx_ok;
  logic w_ok;
  logic accept;
  logic [4:0] s_idx;
  logic [6:0] u_idx;
  logic [SUM_BITS - 1:0] sum_all;
  logic [SUM_BITS - 1:0] sum_excl;
  logic [SUM_BITS - 1:0] sum_new;
  logic [W_BITS - 1:0] cur_w;
  wire [P_N_SLICES - 1:0][W_BITS - 1:0] old_by_slice;

  // reset清数据，clear_load优先于任何写；accept已排除clear及ACTIVE配置写入。
  // err_write是当前请求被拒的组合电平，不是完成脉冲，调用方应在wr_en有效沿消费。

  // 守卫
  assign idx_ok = (int'(wr_slice) < P_N_SLICES) && (int'(wr_unit) < P_N_UNITS);
  assign w_ok = (wr_data != W_ZERO) && (wr_data < W_MAX);

  // Mask both dimensions together before any array read. An invalid address selects safe internal
  // element [0][0] but exposes zero on selected_weight.
  assign s_idx = idx_ok ? wr_slice : 5'd0;
  assign u_idx = idx_ok ? wr_unit : 7'd0;
  assign cur_w = old_by_slice[s_idx];
  assign selected_weight = idx_ok ? cur_w : '0;

  // sum_all >= cur_w 恒成立（无符号），故减法不回绕。
  assign sum_excl = sum_all - {{(SUM_BITS - int'(W_BITS)){1'b0}}, cur_w};
  assign sum_new = sum_excl + {{(SUM_BITS - int'(W_BITS)){1'b0}}, wr_data};

  assign accept = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok &&
      (STATIC_SUM_SAFE || (sum_new < SUM_MAX));
  assign err_write = wr_en && (!accept);

  for (genvar s = 0; s < P_N_SLICES; s++) begin : g_row_output
    assign row_total[s] = {{(64 - ROW_BITS){1'b0}}, row_q[s]};
    // 每row只更新自己的旧值减法；全局地址只负责选中该row，避免把宽行和绕经读回树。
    assign old_by_slice[s] = w_q[s][u_idx];
    wire [W_BITS - 1:0] old_in_row = old_by_slice[s];
    wire [ROW_BITS - 1:0] next_row =
        row_q[s] - ROW_BITS'(old_in_row) + ROW_BITS'(wr_data);
    always_ff @(posedge clk) begin
      if (!rst_n)
        row_q[s] <= '0;
      else if (accept && wr_slice == 5'(s))
        row_q[s] <= next_row;
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int s = 0; s < P_N_SLICES; s++)
        for (int u = 0; u < P_N_UNITS; u++)
          w_q[s][u] <= '0;
      written <= '0;
      sum_all <= '0;
    end else if (clear_load) begin
      written <= '0;
    end else if (accept) begin
      // accept already guarantees 0 < wr_data < W_MAX. Expose the proven
      // zero high bit to synthesis without changing the wide port, precision,
      // or rejection of out-of-range writes.
      w_q[wr_slice][wr_unit] <= wr_data & (W_MAX - W_BITS'(1));
      sum_all <= sum_new;
      written[wr_slice][wr_unit] <= 1'b1;
    end
  end

endmodule
