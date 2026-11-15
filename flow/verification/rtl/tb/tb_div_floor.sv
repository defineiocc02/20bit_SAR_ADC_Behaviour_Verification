// Verification testbench for div_floor — fixed-latency signed floor division.
//
// Contract: verification/contracts/digital_interfaces.yaml :: div_floor
//           verification/contracts/timing.yaml :: div_floor
// Output:   div_floor.out, one line per vector: "<q hex> <err> <cycles>"
//
// The bench measures the latency itself instead of assuming it, so a contract
// drift shows up as a cycle-count mismatch rather than as a silent retime.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_div_floor;
  localparam int MAXV = 1024;
  localparam int W_A  = int'(ACC_BITS) - (int'(V_FRAC) + 1);  // 63
  localparam int W_D  = 60;

  logic signed [W_A-1:0] a_vec [0:MAXV-1];
  logic        [W_D-1:0] d_vec [0:MAXV-1];

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic start = 1'b0;
  logic signed [W_A-1:0] a;
  logic [W_D-1:0] d;
  logic signed [W_A-1:0] q;
  logic err, busy, done;

  integer n_vectors, fd, i;
  integer cycles;
  logic signed [W_A-1:0] q_capture;
  logic err_capture;
  integer guard;

  always #5 clk = ~clk;

  div_floor #(.P_W_A(W_A), .P_W_D(W_D), .P_STAGES(7)) dut (
      .clk(clk), .rst_n(rst_n), .start(start),
      .a(a), .d(d), .q(q), .err(err), .busy(busy), .done(done)
  );

  // One transaction: hold inputs, pulse start, wait for done, return latency.
  task automatic run_one(
      input  logic signed [W_A-1:0] a_in,
      input  logic        [W_D-1:0] d_in,
      output logic signed [W_A-1:0] q_out,
      output logic                  err_out,
      output integer                cyc_out
  );
    begin
      // Wait until the unit can accept a new request.
      guard = 0;
      while (busy === 1'b1 && guard < 200) begin
        @(negedge clk);
        guard = guard + 1;
      end
      @(negedge clk);
      a = a_in;
      d = d_in;
      start = 1'b1;
      @(negedge clk);
      start = 1'b0;
      cyc_out = 0;
      guard = 0;
      while (done !== 1'b1 && guard < 200) begin
        @(negedge clk);
        cyc_out = cyc_out + 1;
        guard = guard + 1;
      end
      if (guard >= 200) $fatal(1, "div_floor never raised done");
      q_out = q;
      err_out = err;
      // Let the done pulse retire before the next transaction.
      @(negedge clk);
    end
  endtask

  initial begin
    if (!$value$plusargs("N=%d", n_vectors)) n_vectors = 1;
    $readmemh("vectors/a.mem", a_vec);
    $readmemh("vectors/d.mem", d_vec);
    fd = $fopen("div_floor.out", "w");
    if (fd == 0) $fatal(1, "cannot open div_floor.out");

    rst_n = 1'b0;
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    @(negedge clk);

    for (i = 0; i < n_vectors; i = i + 1) begin
      run_one(a_vec[i], d_vec[i], q_capture, err_capture, cycles);
      $fdisplay(fd, "%0h %0b %0d", q_capture, err_capture, cycles);
    end
    $fclose(fd);
    $display("DIV_FLOOR_DONE n=%0d", n_vectors);
    $finish;
  end
endmodule
