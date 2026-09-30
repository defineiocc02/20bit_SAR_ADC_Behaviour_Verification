//===========================================================================
// weight_store.sv -- 18 x 71 Q30 权重存储（带写入窗口与合法性守卫）
//===========================================================================
// 职责一句话
//   保存 18 个物理 slice x 71 个单位（63 主 + 8 子）的 Q30 权重，向 recon_core
//   提供只读的 `w_q` 与精确行和缓存；写端口在**未生效期**接受单点写入，并对每个写做合法性与
//   容量守卫。本模块**不做**一致性校验的最终裁决（那是 calib_regs.validate 的
//   职责范围内的部分）；维护写入期总和以检查容量，不执行样本重构算术。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §9（M12）；rtl/README.md §3.2（读写窗口）；
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §5（载入期算术的口径）。
//   寄存器镜像的字段名与顺序对齐 sim/vectors/registers_*.json 的 `weights_q`。
//
// 单位契约
//   权重为 Q30 无量纲整数（W_BITS = 48 位**无符号**存放，合法值落在 (0, 2^47)）。
//   契约 §5 的载入期算术保证真实权重为正，故这里按无符号比较即可，不需要符号扩展。
//
// 参数来源分级
//   P_N_SLICES / P_N_UNITS 默认取自 rtl_params.vh（[推导]）。允许重例化是为了让
//   TB 能用小尺寸构造定向用例；它不是第二份参数表。
//
// 契约与不变量 / 适用域
//   * **写入窗口**：`cfg_ready = 1`（配置已生效 / 转换进行中）时**拒绝写入**，
//     `err_write` 拉高一拍，数据不变。这是 rtl/README.md §3.2 承诺的契约。
//   * **写入守卫（本模块把两条权重合法性检查做成了结构性不变量）**：
//       1. `0 < W < 2^47`              —— 超范围的一次写被拒；
//       2. 写入后 `Sigma W < 2^60`      —— 会使总和越界的一次写被拒。
//     写入守卫只保证已写项合法；reset 后的零和遗漏项由 written bitmap 检出。
//     clear_load 开启新装载 epoch，保留数值但清空 bitmap；只有合法接受的写置位。
//     load_complete 必须参与最终 validate，详见 ADR 0016。
//   * `Sigma W` 的累加宽度取 `SUM_BITS = 64`，**与 recon_core 内部同一口径**
//     （P2 §9 明令："两者都用 SUM_BITS = 64，并在 TB 里用同一个向量核对"）。
//     64 位对 18*71 = 1278 个 < 2^48 的项（上界 < 2^59）余量充足。
//   * 越界的 `wr_slice` / `wr_unit` 一律判为拒绝（不是截断或环绕）：读路径用
//     掩蔽后的索引，避免越界读产生 X。
//   * 复位后全 0。全 0 权重**不是**合法配置（`gain = 0` -> recon_core 报 gain_err），
//     load_complete=0 阻止该不完整配置生效。
//===========================================================================
`include "rtl_params.vh"

module weight_store #(
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_N_UNITS  = int'(N_UNIT_TOTAL)
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              cfg_ready,     // 1 = 已生效 -> **禁止写**
    input  logic              clear_load,    // new configuration epoch; invalidate written bitmap
    output logic              load_complete, // every physical weight written in this epoch
    input  logic              wr_en,         // 一拍脉冲
    input  logic [4:0]        wr_slice,
    input  logic [6:0]        wr_unit,
    input  logic [W_BITS-1:0] wr_data,
    output logic              err_write,     // 1 = 本次写被拒
    output logic [P_N_SLICES-1:0][P_N_UNITS-1:0][W_BITS-1:0] w_q,
    output wire [P_N_SLICES-1:0][63:0] row_total,
    output wire [W_BITS-1:0] selected_weight // same address as write; invalid address reads zero
);

  initial begin
    if (P_N_SLICES < 1 || P_N_SLICES > 32 || P_N_UNITS < 1 || P_N_UNITS > 128)
      $fatal(1, "weight_store: dimensions exceed address width");
  end

  localparam int SUM_BITS = 64;            // 与 recon_core 同一口径（P2 §9）

  localparam logic [W_BITS-1:0]   W_ZERO  = {W_BITS{1'b0}};
  localparam logic [W_BITS-1:0]   W_MAX   = 48'd1 << 47;   // 2^47
  localparam logic [SUM_BITS-1:0] SUM_MAX = 64'd1 << 60; // 2^60
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
  logic [P_N_SLICES-1:0][ROW_BITS-1:0] row_q;

  logic [P_N_SLICES-1:0][P_N_UNITS-1:0] written;
  assign load_complete = &written;

  logic                idx_ok;
  logic                w_ok;
  logic                accept;
  logic [4:0]          s_idx;
  logic [6:0]          u_idx;
  logic [SUM_BITS-1:0] sum_all;
  logic [SUM_BITS-1:0] sum_excl;
  logic [SUM_BITS-1:0] sum_new;
  logic [W_BITS-1:0]   cur_w;
  wire [P_N_SLICES-1:0][W_BITS-1:0] old_by_slice;

  // Maintain the exact sum on accepted writes instead of rebuilding a
  // 1278-word combinational reduction. Replacement subtracts the old word.
  // clear_load invalidates completeness only; weights and sum both survive.

  // ---- 守卫 ----
  assign idx_ok  = (int'(wr_slice) < P_N_SLICES) && (int'(wr_unit) < P_N_UNITS);
  assign w_ok    = (wr_data != W_ZERO) && (wr_data < W_MAX);

  // 用的是**掩蔽后**的索引：越界地址不会去读超出声明维度的位置（否则仿真出 X、
  // 综合出锁存/越界网）。
  assign s_idx   = idx_ok ? wr_slice : 5'd0;
  assign u_idx   = idx_ok ? wr_unit  : 7'd0;
  assign cur_w   = old_by_slice[s_idx];
  assign selected_weight = idx_ok ? cur_w : '0;

  // sum_all >= cur_w 恒成立（无符号），故减法不回绕。
  assign sum_excl = sum_all - {{(SUM_BITS - int'(W_BITS)){1'b0}}, cur_w};
  assign sum_new  = sum_excl + {{(SUM_BITS - int'(W_BITS)){1'b0}}, wr_data};

  assign accept    = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok && (STATIC_SUM_SAFE || (sum_new < SUM_MAX));
  assign err_write = wr_en && (!accept);

  for (genvar s = 0; s < P_N_SLICES; s++) begin : g_row_output
    assign row_total[s] = {{(64-ROW_BITS){1'b0}}, row_q[s]};
    // Keep each row replacement local to its physical slice. A global
    // row_q[s_idx]/w_q[s_idx][u_idx] mux made the selected-slice state traverse
    // the configuration readback tree before reaching every row register.
    assign old_by_slice[s] = w_q[s][u_idx];
    wire [W_BITS-1:0] old_in_row = old_by_slice[s];
    wire [ROW_BITS-1:0] next_row =
        row_q[s] - ROW_BITS'(old_in_row) + ROW_BITS'(wr_data);
    always_ff @(posedge clk) begin
      if (!rst_n) row_q[s] <= '0;
      else if (accept && wr_slice == 5'(s)) row_q[s] <= next_row;
    end
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int s = 0; s < P_N_SLICES; s++)
        for (int u = 0; u < P_N_UNITS; u++) w_q[s][u] <= '0;
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
