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
    parameter int SUM_BITS = 64,
    parameter int W_RAIL = SUM_BITS+2
) (
    input wire sampling_mask_en,
    input wire [P_N_ACTIVE-1:0][4:0] slice_id,
    input wire [P_N_ACTIVE-1:0][P_N_MAIN-1:0] main_on,
    input wire [P_N_ACTIVE-1:0][P_N_SUB-1:0] sub_on,
    input wire [P_DIT_N-1:0] dither_rail,
    input wire [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    output wire [SUM_BITS-1:0] sum_W,
    output wire [SUM_BITS-1:0] sum_Wa,
    output wire signed [W_RAIL-1:0] rails,
    output logic invalid_slice
);
  localparam int N_U = P_N_MAIN+P_N_SUB;
  localparam int DIT_BEG = P_DIT_END-P_DIT_N;
  localparam int N_TERMS = P_N_SLICES * N_U;
  localparam int TREE_LEAVES = 1 << $clog2(N_TERMS);
  assign sum_W = g_node[1].total;
  assign sum_Wa = g_node[1].gain;
  wire [SUM_BITS-1:0] sum_Won = g_node[1].on_sum;
  wire signed [W_RAIL-1:0] sum_Wr = g_node[1].dither_sum;

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

  // Each generated node owns distinct nets: dependencies always point from
  // t to 2*t/2*t+1. Do not coalesce them into one unpacked array: older tools
  // conservatively report a loop on that aggregate despite this acyclic graph.
  for (genvar t = 1; t < 2*TREE_LEAVES; t++) begin : g_node
    wire [SUM_BITS-1:0] total, gain, on_sum;
    wire signed [W_RAIL-1:0] dither_sum;
    if (t < TREE_LEAVES) begin : g_branch
      assign total = g_node[2*t].total + g_node[2*t+1].total;
      assign gain = g_node[2*t].gain + g_node[2*t+1].gain;
      assign on_sum = g_node[2*t].on_sum + g_node[2*t+1].on_sum;
      assign dither_sum = g_node[2*t].dither_sum + g_node[2*t+1].dither_sum;
    end else if (t-TREE_LEAVES < N_TERMS) begin : g_leaf
      localparam int S = (t-TREE_LEAVES) / N_U;
      localparam int U = (t-TREE_LEAVES) % N_U;
      wire [W_BITS-1:0] weight = active[S] ? w_rom[S][U] : '0;
      wire [SUM_BITS-1:0] extended = {{(SUM_BITS-int'(W_BITS)){1'b0}}, weight};
      assign total = extended;
      assign on_sum = physical_on[S][U] ? extended : '0;
      if (U >= DIT_BEG && U < P_DIT_END) begin : g_dither
        wire signed [W_RAIL-1:0] signed_weight = $signed({{(W_RAIL-int'(W_BITS)){1'b0}}, weight});
        assign gain = sampling_mask_en ? '0 : extended;
        assign dither_sum = !sampling_mask_en ? '0 :
          (dither_rail[U-DIT_BEG] ? signed_weight : -signed_weight);
      end else begin : g_signal
        assign gain = extended;
        assign dither_sum = '0;
      end
    end else begin : g_padding
      assign total = '0;
      assign gain = '0;
      assign on_sum = '0;
      assign dither_sum = '0;
    end
  end
  assign rails = $signed({2'b0, sum_W}) - $signed({1'b0, sum_Won, 1'b0}) + sum_Wr;

endmodule
