// Verification testbench for sadc_enc — combinational population count.
//
// Contract: verification/contracts/digital_interfaces.yaml :: sadc_enc
// Vectors:  verification/vectors/generated/sadc_enc/cmp_raw.mem
// Output:   sadc_enc.out, one line per vector: "<sadc_code hex>"
//
// This bench only applies stimulus and records what the DUT produced. It does
// not know the expected value; the scoreboard owns that.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_sadc_enc;
  localparam int MAXV = 1024;
  localparam int B1_W = int'(B1);

  logic [511:0] cmp_raw [0:MAXV-1];
  logic [510:0] raw;
  logic [B1_W-1:0] code;

  integer n_vectors, fd, i;

  sadc_enc #(
      .P_B1(B1_W), .P_N_CMP(511)) dut (
      .cmp_raw  (raw),
      .sadc_code(code)
  );

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 128;
    $readmemh("vectors/cmp_raw.mem", cmp_raw);
    fd = $fopen("sadc_enc.out", "w");
    if (fd == 0) $fatal(1, "cannot open sadc_enc.out");
    for (i = 0; i < n_vectors; i = i + 1) begin
      raw = cmp_raw[i][510:0];
      #1;
      $fdisplay(fd, "%0h", code);
    end
    $fclose(fd);
    $display("SADC_ENC_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
