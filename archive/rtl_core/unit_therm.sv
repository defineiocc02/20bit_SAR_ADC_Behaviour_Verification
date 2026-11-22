// Generic numerical thermometer expansion with an explicit rank for each cell. This component
// supplies no physical permutation, bridge mapping or dither. ON[p] means supplied_rank[p] <
// supplied_count; counts beyond the array extent saturate the numerical mask. A physical producer
// must separately reject any command that is illegal under its binding; this primitive is not that
// producer.
`timescale 1ns/1ps
`include "rtl_params.vh"
module unit_therm #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int MAIN_RANK_BITS = P_N_MAIN > 1 ? $clog2(P_N_MAIN) : 1,
    parameter int SUB_RANK_BITS = P_N_SUB > 1 ? $clog2(P_N_SUB) : 1,
    parameter int MAIN_COUNT_BITS = $clog2(P_N_MAIN + 1),
    parameter int SUB_COUNT_BITS = $clog2(P_N_SUB + 1)
) (
    input wire [P_N_MAIN-1:0][MAIN_RANK_BITS-1:0] main_logical,
    input wire [P_N_SUB-1:0][SUB_RANK_BITS-1:0] sub_logical,
    input wire [P_N_ACTIVE-1:0][MAIN_COUNT_BITS-1:0] main_count,
    input wire [P_N_ACTIVE-1:0][SUB_COUNT_BITS-1:0] sub_count,
    output logic [P_N_ACTIVE-1:0][P_N_MAIN-1:0] main_on,
    output logic [P_N_ACTIVE-1:0][P_N_SUB-1:0] sub_on
);
  initial begin
    if (P_N_ACTIVE < 1 || P_N_MAIN < 1 || P_N_SUB < 1)
      $fatal(1, "unit_therm: positive numerical dimensions are required");
  end
  always_comb begin
    for (int a = 0; a < P_N_ACTIVE; a++) begin
      for (int p = 0; p < P_N_MAIN; p++)
        main_on[a][p] = int'(main_logical[p]) < int'(main_count[a]);
      for (int p = 0; p < P_N_SUB; p++)
        sub_on[a][p] = int'(sub_logical[p]) < int'(sub_count[a]);
    end
  end
endmodule
