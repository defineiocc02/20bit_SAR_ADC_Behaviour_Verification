//===========================================================================
// weight_store.sv -- 18 x 71 Q30 权重存储（带写入窗口与合法性守卫）
//===========================================================================
// 职责一句话
//   保存 18 个物理 slice x 71 个单位（63 主 + 8 子）的 Q30 权重，向 recon_core
//   提供只读的 `w_q`；写端口在**未生效期**接受单点写入，并对每个写做合法性与
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
    parameter int P_N_SLICES = N_SLICES,
    parameter int P_N_UNITS  = N_UNIT_TOTAL
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
    output logic [P_N_SLICES-1:0][P_N_UNITS-1:0][W_BITS-1:0] w_q
);

  localparam int SUM_BITS = 64;            // 与 recon_core 同一口径（P2 §9）

  localparam logic [W_BITS-1:0]   W_ZERO  = {W_BITS{1'b0}};
  localparam logic [W_BITS-1:0]   W_MAX   = {{(W_BITS-47){1'b0}}, 1'b1} << 47;   // 2^47
  localparam logic [SUM_BITS-1:0] SUM_MAX = {{(SUM_BITS-60){1'b0}}, 1'b1} << 60; // 2^60

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

  // Maintain the exact sum on accepted writes instead of rebuilding a
  // 1278-word combinational reduction. Replacement subtracts the old word.
  // clear_load invalidates completeness only; weights and sum both survive.

  // ---- 守卫 ----
  assign idx_ok  = (wr_slice < 5'(P_N_SLICES)) && (wr_unit < 7'(P_N_UNITS));
  assign w_ok    = (wr_data != W_ZERO) && (wr_data < W_MAX);

  // 用的是**掩蔽后**的索引：越界地址不会去读超出声明维度的位置（否则仿真出 X、
  // 综合出锁存/越界网）。
  assign s_idx   = idx_ok ? wr_slice : 5'd0;
  assign u_idx   = idx_ok ? wr_unit  : 7'd0;
  assign cur_w   = w_q[s_idx][u_idx];

  // sum_all >= cur_w 恒成立（无符号），故减法不回绕。
  assign sum_excl = sum_all - {{(SUM_BITS - W_BITS){1'b0}}, cur_w};
  assign sum_new  = sum_excl + {{(SUM_BITS - W_BITS){1'b0}}, wr_data};

  assign accept    = wr_en && (!clear_load) && (!cfg_ready) && idx_ok && w_ok && (sum_new < SUM_MAX);
  assign err_write = wr_en && (!accept);

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      w_q <= '0;
      written <= '0;
      sum_all <= '0;
    end else if (clear_load) begin
      written <= '0;
    end else if (accept) begin
      w_q[wr_slice][wr_unit] <= wr_data;
      sum_all <= sum_new;
      written[wr_slice][wr_unit] <= 1'b1;
    end
  end

endmodule
