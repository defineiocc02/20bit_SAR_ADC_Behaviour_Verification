// Verification testbench for unit_therm — numerical thermometer expansion.
//
// Contract: verification/contracts/digital_interfaces.yaml :: unit_therm
// Output:   unit_therm.out, one line per vector: "<main_on hex> <sub_on hex>"
//
// The generated vectors carry one rank permutation and replay it on all eight
// active slices. That is deliberate: the module's per-slice behaviour is
// identical by construction, so varying the permutation (not the slice) is what
// actually exercises the comparison. Per-slice independence is covered
// separately by the recon_core vectors, which use per-slice masks.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_unit_therm;
  localparam int MAXV = 256;
  localparam int N_ACT = int'(N_ACTIVE);
  localparam int N_MAIN = int'(N_UNIT_MAIN);
  localparam int N_SUB = int'(N_UNIT_SUB);
  localparam int RB_MAIN = 6;
  localparam int RB_SUB = 3;
  localparam int CB_MAIN = 6;
  localparam int CB_SUB = 4;

  logic [N_MAIN*RB_MAIN-1:0] main_logical_mem [0:MAXV-1];
  logic [N_SUB*RB_SUB-1:0]   sub_logical_mem  [0:MAXV-1];
  logic [CB_MAIN-1:0]        main_count [0:MAXV-1];
  logic [CB_SUB-1:0]         sub_count  [0:MAXV-1];

  logic [N_ACT-1:0][N_MAIN-1:0][RB_MAIN-1:0] main_logical;
  logic [N_ACT-1:0][N_SUB-1:0][RB_SUB-1:0]   sub_logical;
  logic [N_ACT-1:0][CB_MAIN-1:0]             main_count_d;
  logic [N_ACT-1:0][CB_SUB-1:0]              sub_count_d;
  logic [N_ACT-1:0][N_MAIN-1:0]              main_on;
  logic [N_ACT-1:0][N_SUB-1:0]               sub_on;

  integer n_vectors, fd, i, a, p;

  unit_therm #(
      .P_N_ACTIVE(N_ACT), .P_N_MAIN(N_MAIN), .P_N_SUB(N_SUB)
  ) dut (
      .main_logical(main_logical),
      .sub_logical (sub_logical),
      .main_count  (main_count_d),
      .sub_count   (sub_count_d),
      .main_on     (main_on),
      .sub_on      (sub_on)
  );

  // Pack the 2-D mask into one wide integer for the dump.
  function automatic logic [N_ACT*N_MAIN-1:0] pack_main();
    integer lane, bit_index;
    begin
      pack_main = '0;
      for (lane = 0; lane < N_ACT; lane++)
        for (bit_index = 0; bit_index < N_MAIN; bit_index++)
          pack_main[lane*N_MAIN + bit_index] = main_on[lane][bit_index];
    end
  endfunction

  function automatic logic [N_ACT*N_SUB-1:0] pack_sub();
    integer lane, bit_index;
    begin
      pack_sub = '0;
      for (lane = 0; lane < N_ACT; lane++)
        for (bit_index = 0; bit_index < N_SUB; bit_index++)
          pack_sub[lane*N_SUB + bit_index] = sub_on[lane][bit_index];
    end
  endfunction

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/main_logical.mem", main_logical_mem);
    $readmemh("vectors/sub_logical.mem", sub_logical_mem);
    $readmemh("vectors/main_count.mem", main_count);
    $readmemh("vectors/sub_count.mem", sub_count);
    fd = $fopen("unit_therm.out", "w");
    if (fd == 0) $fatal(1, "cannot open unit_therm.out");
    for (i = 0; i < n_vectors; i = i + 1) begin
      for (a = 0; a < N_ACT; a = a + 1) begin
        main_logical[a] = main_logical_mem[i];
        sub_logical[a]  = sub_logical_mem[i];
        main_count_d[a] = main_count[i];
        sub_count_d[a]  = sub_count[i];
      end
      #1;
      $fdisplay(fd, "%0h %0h", pack_main(), pack_sub());
    end
    $fclose(fd);
    $display("UNIT_THERM_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
