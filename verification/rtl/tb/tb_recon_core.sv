// Verification testbench for recon_core — the full fixed-point reconstruction.
//
// Contract: verification/contracts/digital_interfaces.yaml :: recon_core
//           verification/contracts/fixed_point.yaml :: reconstruction
// Output:   recon_core.out, one line per vector:
//             "<dout> <clip_low> <clip_high> <acc_ovf> <gain_err> <adc2_ovf>
//              <result_sample_id> <latency_cycles>"
//
// The weight ROM comes from vectors/w_rom.mem, flattened as
// [slice][unit] in the same order the RTL expects, so the bench and the
// reference read one source of truth for W.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_recon_core;
  localparam int MAXV  = 256;
  localparam int N_ACT = int'(N_ACTIVE);
  localparam int N_MAIN = int'(N_UNIT_MAIN);
  localparam int N_SUB = int'(N_UNIT_SUB);
  localparam int N_SLICE = int'(N_SLICES);
  localparam int ADC2_W = int'(ADC2_BITS);
  localparam int V_W = int'(V_BITS);
  localparam int W_W = int'(W_BITS);
  localparam int OUT_W = int'(OUT_BITS);
  localparam int N_UNITS = N_MAIN + N_SUB;

  logic [N_ACT*5-1:0]        id_mem   [0:MAXV-1];
  logic [N_ACT*N_MAIN-1:0]   main_mem [0:MAXV-1];
  logic [N_ACT*N_SUB-1:0]    sub_mem  [0:MAXV-1];
  logic [ADC2_W-1:0]         code_mem [0:MAXV-1];
  logic [V_W-1:0]            inj_mem  [0:MAXV-1];
  logic [V_W-1:0]            off_mem  [0:MAXV-1];
  logic [V_W-1:0]            min_mem  [0:MAXV-1];
  logic [V_W-1:0]            max_mem  [0:MAXV-1];
  logic [31:0]               sid_mem  [0:MAXV-1];
  logic [W_W-1:0]            rom_flat [0:N_SLICE*N_UNITS-1];

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic cfg_ready = 1'b0;
  logic start = 1'b0;
  logic [31:0] sample_id;
  logic clr_ovf = 1'b0;
  logic [N_ACT-1:0][4:0] slice_id;
  logic [N_ACT-1:0][N_MAIN-1:0] main_on;
  logic [N_ACT-1:0][N_SUB-1:0] sub_on;
  logic [ADC2_W-1:0] adc2_code;
  logic signed [V_W-1:0] inj_q, offset_q, adc2_min_q, adc2_max_q;
  logic [N_SLICE-1:0][N_UNITS-1:0][W_W-1:0] w_rom;
  logic [N_SLICE-1:0][63:0] row_total;

  logic [OUT_W-1:0] dout;
  logic dout_valid, clip_low, clip_high, acc_ovf, gain_err, adc2_ovf, busy;
  logic [31:0] result_sample_id;
  logic [4:0] result_flags;

  integer n_vectors, fd, i, s, u;
  integer latency, guard;

  always #5 clk = ~clk;

  recon_core #(
      .P_N_ACTIVE(N_ACT), .P_N_MAIN(N_MAIN), .P_N_SUB(N_SUB),
      .P_N_SLICES(N_SLICE), .P_ADC2_BITS(ADC2_W),
      .P_STAGES(7), .P_USE_ROW_TOTALS(0)
  ) dut (
      .clk(clk), .rst_n(rst_n), .cfg_ready(cfg_ready), .start(start),
      .sample_id(sample_id), .clr_ovf(clr_ovf),
      .slice_id(slice_id), .main_on(main_on), .sub_on(sub_on),
      .adc2_code(adc2_code), .inj_q(inj_q),
      .w_rom(w_rom), .row_total(row_total),
      .offset_q(offset_q), .adc2_min_q(adc2_min_q), .adc2_max_q(adc2_max_q),
      .dout(dout), .dout_valid(dout_valid),
      .clip_low(clip_low), .clip_high(clip_high),
      .acc_ovf(acc_ovf), .gain_err(gain_err), .adc2_ovf(adc2_ovf),
      .busy(busy), .result_sample_id(result_sample_id), .result_flags(result_flags)
  );

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/slice_id.mem", id_mem);
    $readmemh("vectors/main_on.mem", main_mem);
    $readmemh("vectors/sub_on.mem", sub_mem);
    $readmemh("vectors/adc2_code.mem", code_mem);
    $readmemh("vectors/inj_q.mem", inj_mem);
    $readmemh("vectors/offset_q.mem", off_mem);
    $readmemh("vectors/adc2_min_q.mem", min_mem);
    $readmemh("vectors/adc2_max_q.mem", max_mem);
    $readmemh("vectors/sample_id.mem", sid_mem);
    $readmemh("vectors/w_rom.mem", rom_flat);

    for (s = 0; s < N_SLICE; s++)
      for (u = 0; u < N_UNITS; u++)
        w_rom[s][u] = rom_flat[s*N_UNITS + u];
    row_total = '0;

    fd = $fopen("recon_core.out", "w");
    if (fd == 0) $fatal(1, "cannot open recon_core.out");

    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    cfg_ready = 1'b1;
    @(negedge clk);

    for (i = 0; i < n_vectors; i = i + 1) begin
      // Wait for the core to be idle before presenting the next request.
      guard = 0;
      while (busy === 1'b1 && guard < 200) begin
        @(negedge clk);
        guard = guard + 1;
      end
      @(negedge clk);
      slice_id = id_mem[i];
      main_on = main_mem[i];
      sub_on = sub_mem[i];
      adc2_code = code_mem[i];
      inj_q = inj_mem[i];
      offset_q = off_mem[i];
      adc2_min_q = min_mem[i];
      adc2_max_q = max_mem[i];
      sample_id = sid_mem[i];
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;
      latency = 0;
      guard = 0;
      while (dout_valid !== 1'b1 && guard < 200) begin
        @(negedge clk);
        latency = latency + 1;
        guard = guard + 1;
      end
      if (guard >= 200) $fatal(1, "recon_core never raised dout_valid");
      $fdisplay(fd, "%0h %0b %0b %0b %0b %0b %0d %0d",
                dout, clip_low, clip_high, acc_ovf, gain_err, adc2_ovf,
                result_sample_id, latency);
      @(negedge clk);
    end
    $fclose(fd);
    $display("RECON_CORE_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
