// 模块：weight_store；物理权重、装载完整位图与精确行和缓存。
// 端口/单位：默认18×71个48位unsigned Q30系数，合法0<W<2^47；row_total零扩展为64位。
// 时钟/复位：clk上升沿、同步低有效rst_n；reset清数值/行和/位图；clear只清位图并保留数值。
// 接受：wr_en&&!clear_load&&!cfg_ready且地址/数值/容量合法时写；拒绝标志err_write为组合电平。
// 替换：row_new=row_old-old_weight+new_weight，clear后仍须减去保留旧值；每row共享读旧值路径。
// 读回：selected_weight为同写地址的组合值，越界返回0；load_complete表示本epoch全部物理项写过。
// 适用域：1..32slice、1..128unit；静态容量界<2^59，可移除全局容量反馈但不改变合法写集合。
// 实现边界：当前并行读系数映射为寄存器；改成RAM须重新建立带宽、延迟与调度合同。
// 来源：RTL_ARITHMETIC_CONTRACT.md §5、P2_INTERFACE.md §9；不运行参数学习。
`include "rtl_params.vh"

module weight_store #(
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_N_UNITS = int'(N_UNIT_TOTAL)
) (
    input logic clk,
    input logic rst_n,
    input logic cfg_ready, // 1 = 已生效 -> **禁止写**
    input logic clear_load, // new configuration epoch; invalidate written bitmap
    output logic load_complete, // every physical weight written in this epoch
    input logic wr_en, // 一拍脉冲
    input logic [4:0] wr_slice,
    input logic [6:0] wr_unit,
    input logic [W_BITS - 1:0] wr_data,
    output logic err_write, // 1 = 本次写被拒
    output logic [P_N_SLICES - 1:0][P_N_UNITS - 1:0][W_BITS - 1:0] w_q,
    output wire [P_N_SLICES - 1:0][63:0] row_total,
    output wire [W_BITS - 1:0] selected_weight // same address as write; invalid address reads zero
);

  initial begin
    if (P_N_SLICES < 1 || P_N_SLICES > 32 || P_N_UNITS < 1 || P_N_UNITS > 128)
      $fatal(1, "weight_store: dimensions exceed address width");
  end

  localparam int SUM_BITS = 64; // 与 recon_core 同一口径（P2 §9）

  localparam logic [W_BITS - 1:0] W_ZERO = {W_BITS{1'b0}};
  localparam logic [W_BITS - 1:0] W_MAX = 48'd1 << 47; // 2^47
  localparam logic [SUM_BITS - 1:0] SUM_MAX = 64'd1 << 60; // 2^60
  // For every supported production geometry the capacity predicate is
  // statically true: 32*128*(2^47-1) < 2^59 < 2^60.  Keep the dynamic guard
  // as an elaboration-time fallback if either width/limit changes later.
  // Synthesis can prune the global subtract/add/compare path and sum_all
  // register. The old-word read is shared by row replacement and cfg readback.
  localparam logic STATIC_SUM_SAFE =
      (64'(P_N_SLICES) * 64'(P_N_UNITS) * (64'(W_MAX) - 64'd1)) < SUM_MAX;

  // Each accepted coefficient is < 2^47. Therefore a row of U coefficients
  // is < U*2^47 <= 2^(47+ceil(log2(U))); ROW_BITS is exact for all U=1..128.
  // A replacement subtracts the retained old word, even after clear_load:
  // the bitmap describes this epoch, not the numeric contents of the store.
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

  // Accepted replacement writes update exact global/row sums incrementally.
  // Production STATIC_SUM_SAFE lets synthesis remove the unused global sum.
  // clear_load invalidates completeness only; numeric weights/sums survive.

  // ---- 守卫 ----
  assign idx_ok = (int'(wr_slice) < P_N_SLICES) && (int'(wr_unit) < P_N_UNITS);
  assign w_ok = (wr_data != W_ZERO) && (wr_data < W_MAX);

  // Mask both dimensions together before any array read. An invalid address
  // selects safe internal element [0][0] but exposes zero on selected_weight.
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
    // Keep each row replacement local to its physical slice. A global
    // row_q[s_idx]/w_q[s_idx][u_idx] mux made the selected-slice state traverse
    // the configuration readback tree before reaching every row register.
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
