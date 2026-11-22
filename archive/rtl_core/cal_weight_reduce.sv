// 模块：cal_weight_reduce；AllSIGNAL严格子集的物理权重归约。
// T=ΣW，G=T，R=ΣW*(1-2*actual_conversion_mask)。物理ID选权重，mask只来自已接受的实际转换开关；不生
// 成SID、bridge、dither或未披露采集连接。
// 所选binding必须独立证明每unit a=1、termQ=commonQ+R_unit*Vscale。
// 纯组合树；invalid_slice标出越界或重复ID。原始非法ID的数值结果不是合法样本。
// row_total仅允许使用与w_rom同epoch的精确缓存；它不是估计的增益。
`timescale 1ns/1ps
`include "rtl_params.vh"
module cal_weight_reduce #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter bit P_USE_ROW_TOTALS = 0,
    parameter int SUM_BITS = 64,
    parameter int W_RAIL = SUM_BITS + 2
) (
    input wire [P_N_ACTIVE-1:0][4:0] slice_id,
    input wire [P_N_ACTIVE-1:0][P_N_MAIN-1:0] main_on,
    input wire [P_N_ACTIVE-1:0][P_N_SUB-1:0] sub_on,
    input wire [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    input wire [P_N_SLICES-1:0][SUM_BITS-1:0] row_total,
    output wire [SUM_BITS-1:0] sum_W,
    output wire [SUM_BITS-1:0] sum_Wa,
    output wire signed [W_RAIL-1:0] rails,
    output logic invalid_slice
);
  localparam int N_U = P_N_MAIN + P_N_SUB;
  localparam int N_TERMS = P_N_SLICES * N_U;
  localparam int LEAVES = 1 << $clog2(N_TERMS);
  localparam int ROW_LEAVES = 1 << $clog2(P_N_SLICES);
  logic [P_N_SLICES-1:0] active;
  logic [P_N_SLICES-1:0][N_U-1:0] physical_on;
  initial begin
    if (P_N_ACTIVE < 1 || P_N_ACTIVE > P_N_SLICES || P_N_SLICES > 32 ||
        P_N_MAIN < 1 || P_N_SUB < 1 || SUM_BITS < int'(W_BITS) + $clog2(N_TERMS) ||
        W_RAIL != SUM_BITS + 2)
      $fatal(1, "cal_weight_reduce: unsupported numerical dimensions");
  end
  always_comb begin
    active = '0;
    physical_on = '0;
    invalid_slice = 1'b0;
    for (int a = 0; a < P_N_ACTIVE; a++) begin
      invalid_slice |= int'(slice_id[a]) >= P_N_SLICES;
      for (int b = 0; b < a; b++)
        invalid_slice |= slice_id[a] == slice_id[b];
    end
    for (int s = 0; s < P_N_SLICES; s++) begin
      for (int a = 0; a < P_N_ACTIVE; a++) begin
        if (slice_id[a] == 5'(s)) begin
          active[s] = 1'b1;
          physical_on[s] |= {sub_on[a], main_on[a]};
        end
      end
    end
  end
  // Distinct generated nets expose the acyclic tree to older HDL tools.
  for (genvar t = 1; t < 2 * LEAVES; t++) begin : g_unit
    wire [SUM_BITS-1:0] total, on_sum;
    if (t < LEAVES) begin : g_branch
      assign total = g_unit[2*t].total + g_unit[2*t+1].total;
      assign on_sum = g_unit[2*t].on_sum + g_unit[2*t+1].on_sum;
    end else if (t - LEAVES < N_TERMS) begin : g_leaf
      localparam int S = (t - LEAVES) / N_U;
      localparam int U = (t - LEAVES) % N_U;
      wire [SUM_BITS-1:0] weight = active[S] ?
          {{(SUM_BITS-int'(W_BITS)){1'b0}}, w_rom[S][U]} : '0;
      assign total = weight;
      assign on_sum = physical_on[S][U] ? weight : '0;
    end else begin : g_padding
      assign total = '0;
      assign on_sum = '0;
    end
  end
  for (genvar t = 1; t < 2 * ROW_LEAVES; t++) begin : g_row
    wire [SUM_BITS-1:0] total;
    if (t < ROW_LEAVES) begin : g_branch
      assign total = g_row[2*t].total + g_row[2*t+1].total;
    end else if (t - ROW_LEAVES < P_N_SLICES) begin : g_leaf
      localparam int S = t - ROW_LEAVES;
      assign total = active[S] ? row_total[S] : '0;
    end else begin : g_padding
      assign total = '0;
    end
  end
  assign sum_W = P_USE_ROW_TOTALS ? g_row[1].total : g_unit[1].total;
  assign sum_Wa = sum_W;
  assign rails = $signed({2'b0, sum_W}) - $signed({1'b0, g_unit[1].on_sum, 1'b0});
endmodule
