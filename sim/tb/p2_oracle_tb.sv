`timescale 1ns/1ps
`include "rtl_params.vh"
// Isolate the complete T5 identity oracle from the production-size DUTs in
// p2_tb. All 2^20 codes remain checked; unrelated 568-term reductions need
// not be reevaluated for the ~27 million clock edges of this sweep.
module p2_oracle_tb;
  logic clk = 1'b0;
  always #1 clk = ~clk;
  logic rst_n = 1'b0, start = 1'b0;
  logic [19:0] code = '0, dout;
  logic signed [63:0] min_q, max_q, offset_q;
  logic [0:0][2:0][47:0] weights;
  logic valid, clip_low, clip_high, acc_ovf, gain_err, adc2_ovf;
  string vdir;
  int checks = 0;

  recon_core #(
      .P_N_ACTIVE(1), .P_N_MAIN(2), .P_N_SUB(1), .P_N_SLICES(1),
      .P_ADC2_BITS(20), .P_STAGES(7)
  ) dut (
      .clk(clk), .rst_n(rst_n), .cfg_ready(1'b1), .start(start),
      .clr_ovf(1'b0), .sampling_mask_en(1'b0), .slice_id(5'd0),
      .main_on(2'd0), .sub_on(1'b0), .dither_rail('0), .adc2_code(code),
      .inj_q(64'sd0), .w_rom(weights), .offset_q(offset_q),
      .adc2_min_q(min_q), .adc2_max_q(max_q), .dout(dout),
      .dout_valid(valid), .clip_low(clip_low), .clip_high(clip_high),
      .acc_ovf(acc_ovf), .gain_err(gain_err), .adc2_ovf(adc2_ovf), .busy()
  );

  function automatic int open_data(input string name);
    int fd;
    string line, token;
    fd = $fopen({vdir, "/", name}, "r");
    if (fd == 0) $fatal(1, "cannot open %s", name);
    forever begin
      if ($fgets(line, fd) == 0) $fatal(1, "missing #DATA in %s", name);
      if ($sscanf(line, "%s", token) == 1 && token == "#DATA") break;
    end
    return fd;
  endfunction

  function automatic void rd(input int fd, output logic [63:0] value);
    if ($fscanf(fd, "%h", value) != 1) $fatal(1, "oracle vector short read");
  endfunction

  initial begin
    int fd, cycles;
    logic [63:0] w0, w1, w2, vmin, vmax, voff, bits, count, identity;
    if (!$value$plusargs("vdir=%s", vdir)) vdir = "sim/vectors";
    fd = open_data("p2_oracle_spec.hex");
    rd(fd, w0); rd(fd, w1); rd(fd, w2);
    $fclose(fd);
    weights[0][0] = w0[47:0];
    weights[0][1] = w1[47:0];
    weights[0][2] = w2[47:0];
    fd = open_data("p2_oracle_cfg.hex");
    rd(fd, vmin); rd(fd, vmax); rd(fd, voff);
    rd(fd, bits); rd(fd, count); rd(fd, identity);
    $fclose(fd);
    if (bits != 20 || count != 1048576 || identity != 1)
      $fatal(1, "oracle vector metadata mismatch");
    min_q = $signed(vmin);
    max_q = $signed(vmax);
    offset_q = $signed(voff);
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    for (int c = 0; c < 1048576; c++) begin
      @(negedge clk);
      code = 20'(c);
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;
      cycles = 0;
      forever begin
        @(negedge clk);
        cycles++;
        if (valid) break;
        if (cycles > 40) $fatal(1, "oracle timeout at code=%0d", c);
      end
      if (dout !== code || {clip_low, clip_high, acc_ovf, gain_err, adc2_ovf} !== 5'b0)
        $fatal(1, "oracle mismatch code=%0d dout=%0d flags=%b%b%b%b%b",
               c, dout, clip_low, clip_high, acc_ovf, gain_err, adc2_ovf);
      if (cycles != 11) $fatal(1, "oracle latency=%0d expected=11", cycles);
      checks++;
      if ((checks % 262144) == 0) begin
        $display("P2_ORACLE_PROGRESS codes=%0d", checks);
        $fflush();
      end
    end
    $display("P2_ORACLE_COMPLETE codes=%0d errors=0", checks);
    $finish;
  end
endmodule
