// Physical-cell weighted correction terms. No quantizer truth, floating point,
// or fitted analog state is accessible here. Coefficients are supplied by the
// committed physical slice/unit store; caller snapshots these sums with a sample.
// Paper [00] specifies externally derived weights corrected on chip; this exact
// Q30/Q32 arithmetic realization is an engineering implementation (ADR 0014).
`include "rtl_params.vh"
module cal_weight_reduce #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_DIT_N = 2*int'(DITHER_UNITS_RANGE),
    parameter int P_DIT_END = int'(N_UNIT_TOTAL),
    // Direct coefficient-only users retain the uncached interface behavior.
    // The production top supplies exact configuration-time row totals.
    parameter bit P_USE_ROW_TOTALS = 0,
    parameter int SUM_BITS = 64,
    parameter int W_RAIL = SUM_BITS+2
) (
    input wire sampling_mask_en,
    input wire [P_N_ACTIVE-1:0][4:0] slice_id,
    input wire [P_N_ACTIVE-1:0][P_N_MAIN-1:0] main_on,
    input wire [P_N_ACTIVE-1:0][P_N_SUB-1:0] sub_on,
    input wire [P_DIT_N-1:0] dither_rail,
    input wire [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    input wire [P_N_SLICES-1:0][SUM_BITS-1:0] row_total,
    output wire [SUM_BITS-1:0] sum_W,
    output wire [SUM_BITS-1:0] sum_Wa,
    output wire signed [W_RAIL-1:0] rails,
    output logic invalid_slice
);
  localparam int N_U = P_N_MAIN+P_N_SUB;
  localparam int DIT_BEG = P_DIT_END-P_DIT_N;
  localparam int N_TERMS = P_N_SLICES * N_U;
  localparam int TREE_LEAVES = 1 << $clog2(N_TERMS);
  localparam int COL_LEAVES = 1 << $clog2(P_N_SLICES);
  localparam int MASK_LEAVES = 1 << $clog2(P_DIT_N);
  // Module-scope edges avoid Vivado 2018.3's undriven forward-generate nodes.
  // Every read/write index is an elaboration-time constant. split_var tells
  // the simulator to analyze each edge separately; it is not a warning waiver.
  wire [SUM_BITS-1:0] total_tree [1:2*TREE_LEAVES-1] /* verilator split_var */;
  wire [SUM_BITS-1:0] on_tree [1:2*TREE_LEAVES-1] /* verilator split_var */;
  wire [SUM_BITS-1:0] column_tree [0:P_DIT_N-1][1:2*COL_LEAVES-1] /* verilator split_var */;
  wire [SUM_BITS-1:0] mask_tree [1:2*MASK_LEAVES-1] /* verilator split_var */;
  wire signed [W_RAIL-1:0] rail_tree [1:2*MASK_LEAVES-1] /* verilator split_var */;
  wire [SUM_BITS-1:0] row_tree [1:2*COL_LEAVES-1] /* verilator split_var */;
  assign sum_W = P_USE_ROW_TOTALS ? row_tree[1] : total_tree[1];
  wire [SUM_BITS-1:0] mask_total = mask_tree[1];
  assign sum_Wa = sampling_mask_en ? (sum_W - mask_total) : sum_W;
  wire [SUM_BITS-1:0] sum_Won = on_tree[1];
  wire signed [W_RAIL-1:0] sum_Wr = sampling_mask_en ? rail_tree[1] : '0;

  initial begin
    if (P_N_ACTIVE < 1 || P_N_MAIN < 1 || P_N_SUB < 1 ||
        P_N_SLICES < P_N_ACTIVE || P_N_SLICES > 32 || P_DIT_N < 1 || DIT_BEG < 0 ||
        SUM_BITS < int'(W_BITS)+$clog2(N_TERMS) || W_RAIL != SUM_BITS+2)
      $fatal(1, "cal_weight_reduce: unsupported dimensions");
  end
  always_comb begin
    invalid_slice = sampling_mask_en && (P_DIT_END > N_U);
    for (int a = 0; a < P_N_ACTIVE; a++)
      invalid_slice |= (int'(slice_id[a]) >= P_N_SLICES);
    for (int a = 0; a < P_N_ACTIVE; a++)
      for (int b = 0; b < a; b++)
        invalid_slice |= (slice_id[a] == slice_id[b]);
  end

  // Move the narrow switch mask to its physical row, rather than selecting
  // 48-bit coefficient buses through eight independent 18:1 crossbars.
  // Every coefficient is statically wired to its own physical unit. This trades
  // more zero-gated adder leaves for substantially narrower selection wiring;
  // mapped area/timing still require a target-library synthesis comparison.
  logic [P_N_SLICES-1:0] active;
  logic [P_N_SLICES-1:0][N_U-1:0] physical_on;
  always_comb begin
    active='0;physical_on='0;
    for(int s=0;s<P_N_SLICES;s++) begin
      for(int a=0;a<P_N_ACTIVE;a++) begin
        if(slice_id[a]==5'(s)) begin
          active[s]=1'b1;
          physical_on[s] |= {sub_on[a],main_on[a]};
        end
      end
    end
  end


  // Only the physical-row active mask changes per sample. Coefficients remain
  // fixed during conversions, so the loader can cache each row's exact sum.
  // Duplicate IDs still activate a physical row once. No pipeline stage is added.
  for (genvar t=1;t<2*COL_LEAVES;t++) begin:g_row_tree
    if (!P_USE_ROW_TOTALS) begin:g_unused
      assign row_tree[t]='0;
    end else if (t<COL_LEAVES) begin:g_branch
      assign row_tree[t]=row_tree[2*t]+row_tree[2*t+1];
    end else if (t-COL_LEAVES<P_N_SLICES) begin:g_leaf
      localparam int S=t-COL_LEAVES;
      assign row_tree[t]=active[S] ? row_total[S] : '0;
    end else begin:g_padding
      assign row_tree[t]='0;
    end
  end

  // The acquisition dither rail for unit J is shared across every active slice.
  // C[J] = sum_s W[s,J]; M = sum_J C[J]. Factor the sign AFTER each column:
  // gain = sampling ? total-M : total; Wr = sampling ? sum_J sign[J]*C[J] : 0.
  // All coefficients enter as unsigned bit patterns. M is a subset of total,
  // and the width guard above bounds both exactly, including invalid-ID inputs.
  // Production 18x4 dither cells need 4 conditional negations instead of 72.
  // No registers, narrowed operands, configuration state, or latency are added.
  for (genvar j=0;j<P_DIT_N;j++) begin:g_col
    localparam int U=DIT_BEG+j;
    for (genvar t=1;t<2*COL_LEAVES;t++) begin:g_sum
      if(t<COL_LEAVES) begin:g_branch
        assign column_tree[j][t]=column_tree[j][2*t]+column_tree[j][2*t+1];
      end else if(t-COL_LEAVES<P_N_SLICES && U<N_U) begin:g_leaf
        localparam int S=t-COL_LEAVES;
        // Reuse the exact physical leaf; do not add a second coefficient selector.
        assign column_tree[j][t]=total_tree[TREE_LEAVES+S*N_U+U];
      end else begin:g_padding
        assign column_tree[j][t]='0;
      end
    end
  end
  for (genvar t=1;t<2*MASK_LEAVES;t++) begin:g_mask_tree
    if(t<MASK_LEAVES) begin:g_branch
      assign mask_tree[t]=mask_tree[2*t]+mask_tree[2*t+1];
      assign rail_tree[t]=rail_tree[2*t]+rail_tree[2*t+1];
    end else if(t-MASK_LEAVES<P_DIT_N) begin:g_leaf
      localparam int J=t-MASK_LEAVES;
      wire signed [W_RAIL-1:0] signed_column=$signed({{(W_RAIL-SUM_BITS){1'b0}},column_tree[J][1]});
      assign mask_tree[t]=column_tree[J][1];
      assign rail_tree[t]=dither_rail[J] ? signed_column : -signed_column;
    end else begin:g_padding
      assign mask_tree[t]='0;
      assign rail_tree[t]='0;
    end
  end

  // Tree nodes refer to predeclared net elements, never later generate scopes.
  for (genvar t = 1; t < 2*TREE_LEAVES; t++) begin : g_node
    if (t < TREE_LEAVES) begin : g_branch
      if (P_USE_ROW_TOTALS) begin:g_cached
        // Leaves remain live: the dither-column tree reuses them below.
        assign total_tree[t] = '0;
      end else begin:g_uncached
        assign total_tree[t] = total_tree[2*t] + total_tree[2*t+1];
      end
      assign on_tree[t] = on_tree[2*t] + on_tree[2*t+1];
    end else if (t-TREE_LEAVES < N_TERMS) begin : g_leaf
      localparam int S = (t-TREE_LEAVES) / N_U;
      localparam int U = (t-TREE_LEAVES) % N_U;
      wire [W_BITS-1:0] weight = active[S] ? w_rom[S][U] : '0;
      wire [SUM_BITS-1:0] extended = {{(SUM_BITS-int'(W_BITS)){1'b0}}, weight};
      assign total_tree[t] = extended;
      assign on_tree[t] = physical_on[S][U] ? extended : '0;
    end else begin : g_padding
      assign total_tree[t] = '0;
      assign on_tree[t] = '0;
    end
  end
  assign rails = $signed({2'b0, sum_W}) - $signed({1'b0, sum_Won, 1'b0}) + sum_Wr;

endmodule
