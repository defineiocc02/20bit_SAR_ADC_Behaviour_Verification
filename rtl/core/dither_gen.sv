// Integer sampling dither: same PMF as round(uniform(-D,D)) in sampler.py.
// Endpoints have half the probability of an interior integer. This is not a
// bit-exact replacement for PCG64; the PRNG sequence is an RTL design choice.
//
// Right-shift Galois LFSR, period 2^32-1 for every nonzero seed. The reduction
// polynomial is verified independently in tests/unit/test_rtl_review_regressions.py.
// IMPORTANT: shift in ZERO before XOR. Rotating bit0 into bit31 cancels the
// feedback MSB and makes raw[31:24] collapse to zero (review R1, 2026-09-19).
//
// Advance eight LFSR bits per draw to avoid overlapping 8-bit windows.
// gcd(8, 2^32-1)=1, so decimation preserves the full state period.
// en=0 holds state. Rejected draws have valid=0 and must not be consumed.
// D=0 produces zero. Supported D is 0..63; a zero seed is prohibited.
`include "rtl_params.vh"
module dither_gen #(
    parameter int D = DITHER_UNITS_RANGE,
    parameter logic [31:0] SEED = 32'h1357_9BDF
) (
    input logic clk,
    input logic rst_n,
    input logic en,
    output logic signed [7:0] dither_code,
    output logic valid
);
  localparam int SPAN = (D == 0) ? 1 : 4 * D;
  localparam int LIMIT = (256 / SPAN) * SPAN;
  localparam logic [31:0] TAPS = 32'h8020_0003;
  logic [31:0] lfsr, next_lfsr;
  logic [7:0] raw, reduced;
  logic [8:0] rounded;
  logic signed [9:0] centered;

  initial begin
    if (D < 0 || D > 63) $fatal(1, "dither_gen: D must be in 0..63");
    if (SEED == 0) $fatal(1, "dither_gen: SEED must be nonzero");
  end
  assign raw = lfsr[31:24];
  assign reduced = raw % 8'(SPAN);
  assign valid = (9'(raw) < 9'(LIMIT));
  // 4D equiprobable cells -> 1 endpoint cell, 2 per interior, 1 endpoint.
  assign rounded = ({1'b0, reduced} + 9'd1) >> 1;
  assign centered = $signed({1'b0, rounded}) - 10'(D);
  assign dither_code = (D == 0) ? 8'sd0 : centered[7:0];
  always_comb begin
    next_lfsr = lfsr;
    for (int k = 0; k < 8; k++)
      next_lfsr = {1'b0, next_lfsr[31:1]} ^ (next_lfsr[0] ? TAPS : 32'h0);
  end
  always_ff @(posedge clk) begin
    if (!rst_n) lfsr <= SEED;
    else if (en) lfsr <= next_lfsr;
  end
endmodule
