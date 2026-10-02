//===========================================================================
// weight_store.sv -- 物理权重、装载完整性与精确行和缓存
//===========================================================================
// 职责：默认保存 18x71 个物理单元的 Q30 无符号系数（W_BITS=48），并输出
// 每行精确总和。权重求解在片外，完整配置的最终 validate 属于 calib_regs；
// 本模块只维护权重写入守卫与 written bitmap，不运行校准学习算法。
//
// 捕获：wr_en 是写请求；仅 !cfg_ready && !clear_load 且地址/数值/容量
// 合法时，于时钟上升沿接受一次写。err_write 为组合拒绝标志；不是锁存的
// 一拍事件，若 wr_en 保持为 1 则拒绝标志也会保持。
// selected_weight 是同一写地址的组合读回，越界返回零；无内部读延迟。
//
// 合法性：0 < W < 2^47，总和 < 2^60。支持维度最大 32x128，因而静态
// 上界 32*128*(2^47-1) <2^59 已满足容量限制；STATIC_SUM_SAFE 使综合器
// 可删掉全局总和反馈路径。动态总和检查仍保留为源码回退，行缓存始终存在。
//
// 复位/epoch：rst_n 为同步低有效复位，清权重、行缓存、总和及 bitmap。
// clear_load 优先于写，只清 bitmap，数值和行缓存保留；新 epoch 的合法
// 替换必须减去保留的旧系数，不能把未写 bitmap 当作数值零。load_complete
// 是所有物理项均在本 epoch 合法写过的组合 AND；缺项配置不得生效。
//
// 综合关键：每个 slice 共享一次 old_by_slice[s]=w_q[s][u_idx]，本地
// 行替换和全局配置读回复用它。行更新只经过本行旧值，避免全局选 slice
// 的读回 mux 再反馈到所有行寄存器。ROW_BITS=47+ceil(log2(P_N_UNITS))
// 精确容纳该行所有合法权重；row_total 输出再零扩展到 64 位。
// 当前并行系数接口映射成寄存器存储；改为 RAM 会改变读取带宽与调度契约，
// 不能仅替换数组声明就宣称得到 BRAM/SRAM。后续应先证明分银行读取预算。
// 来源：P2_INTERFACE.md §9；RTL_ARITHMETIC_CONTRACT.md §5；ADR 0016。
//===========================================================================
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
