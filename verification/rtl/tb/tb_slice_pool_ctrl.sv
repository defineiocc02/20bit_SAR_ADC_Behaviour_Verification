// Verification testbench for slice_pool_ctrl — causal 8-of-18 ownership.
//
// Contract: verification/contracts/digital_interfaces.yaml :: slice_pool_ctrl
//           verification/contracts/timing.yaml :: slice_pool_ctrl
// Output:   slice_pool_ctrl.out, one line per clock edge:
//             "<acq_ids> <conv_ids> <acq_mask> <conv_mask> <valid> <sample_id> <primed> <cycle>"
//
// Every vector is one clock edge. The bench records the *state after* that edge,
// which is what the reference model computes, so no phase guessing is needed.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_slice_pool_ctrl;
  localparam int MAXV = 512;
  localparam int SEED = int'(POOL_SHUFFLE_SEED);  // single source: rtl_params.vh

  logic [0:0] advance_mem [0:MAXV-1];
  logic [0:0] enable_mem  [0:MAXV-1];
  logic [0:0] shuffle_mem [0:MAXV-1];
  logic [0:0] rst_mem     [0:MAXV-1];

  logic clk = 1'b0;
  logic rst_n, enable, advance, shuffle;
  logic [7:0][4:0] acquiring_ids, converting_ids;
  logic [17:0] acquiring_mask, converting_mask;
  logic conversion_valid;
  logic [31:0] sample_id;
  logic acquisition_primed;

  integer n_vectors, fd, i, cycle = 0;

  always #5 clk = ~clk;

  slice_pool_ctrl #(.P_SEED(SEED)) dut (
      .clk(clk), .rst_n(rst_n), .enable(enable), .advance(advance), .shuffle(shuffle),
      .acquiring_ids(acquiring_ids), .converting_ids(converting_ids),
      .acquiring_mask(acquiring_mask), .converting_mask(converting_mask),
      .conversion_valid(conversion_valid), .sample_id(sample_id),
      .acquisition_primed(acquisition_primed)
  );

  function automatic logic [39:0] pack_acq();
    integer a;
    begin
      pack_acq = '0;
      for (a = 0; a < 8; a++) pack_acq[a*5 +: 5] = acquiring_ids[a];
    end
  endfunction

  function automatic logic [39:0] pack_conv();
    integer a;
    begin
      pack_conv = '0;
      for (a = 0; a < 8; a++) pack_conv[a*5 +: 5] = converting_ids[a];
    end
  endfunction

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/advance.mem", advance_mem);
    $readmemh("vectors/enable.mem", enable_mem);
    $readmemh("vectors/shuffle.mem", shuffle_mem);
    $readmemh("vectors/rst_n.mem", rst_mem);

    fd = $fopen("slice_pool_ctrl.out", "w");
    if (fd == 0) $fatal(1, "cannot open slice_pool_ctrl.out");

    rst_n = 1'b1; enable = 1'b1; advance = 1'b0; shuffle = 1'b0;
    repeat (2) @(negedge clk);

    for (i = 0; i < n_vectors; i = i + 1) begin
      @(negedge clk);
      rst_n   = rst_mem[i][0];
      enable  = enable_mem[i][0];
      advance = advance_mem[i][0];
      shuffle = shuffle_mem[i][0];
      @(posedge clk);
      #1;
      cycle = cycle + 1;
      $fdisplay(fd, "%0h %0h %0h %0h %0b %0d %0b %0d",
                pack_acq(), pack_conv(), acquiring_mask, converting_mask,
                conversion_valid, sample_id, acquisition_primed, cycle);
    end
    $fclose(fd);
    $display("SLICE_POOL_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
