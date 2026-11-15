// Verification testbench for adc2_dec — mid-rise bin centre with ties-to-even.
//
// Contract: verification/contracts/digital_interfaces.yaml :: adc2_dec
//           verification/contracts/fixed_point.yaml :: rounding.adc2_bin_center
// Output:   adc2_dec.out, one line per vector: "<fine_q hex> <ovf>"
//
// Uses #1 between vectors so the combinational settle is unambiguous; there is
// no clock in this module.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_adc2_dec;
  localparam int MAXV = 8192;
  localparam int ADC2_W = int'(ADC2_BITS);
  localparam int V_W = int'(V_BITS);

  logic [ADC2_W-1:0]       code  [0:MAXV-1];
  logic signed [V_W-1:0]     min_q [0:MAXV-1];
  logic signed [V_W-1:0]     max_q [0:MAXV-1];

  logic [ADC2_W-1:0]       d_code;
  logic signed [V_W-1:0]     d_min, d_max;
  logic signed [V_W-1:0]     fine_q;
  logic                         ovf;

  integer n_vectors, fd, i;

  adc2_dec #(.P_ADC2_BITS(ADC2_W)) dut (
      .adc2_code (d_code),
      .adc2_min_q(d_min),
      .adc2_max_q(d_max),
      .fine_q    (fine_q),
      .ovf       (ovf)
  );

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/adc2_code.mem", code);
    $readmemh("vectors/adc2_min_q.mem", min_q);
    $readmemh("vectors/adc2_max_q.mem", max_q);
    fd = $fopen("adc2_dec.out", "w");
    if (fd == 0) $fatal(1, "cannot open adc2_dec.out");
    for (i = 0; i < n_vectors; i = i + 1) begin
      d_code = code[i];
      d_min  = min_q[i];
      d_max  = max_q[i];
      #1;
      $fdisplay(fd, "%0h %0b", fine_q, ovf);
    end
    $fclose(fd);
    $display("ADC2_DEC_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
