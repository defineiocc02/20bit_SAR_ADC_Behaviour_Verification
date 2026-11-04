// Binary SAR sequencer with optional flash seed. Comparator convention:
// cmp_ge=1 accepts the current DAC trial (input >= trial). Resolved excludes
// the pending trial bit: only resolved_valid may update the separate RDAC.
// Binary radix and clocked compare handshake are engineering choices, not a
// disclosure of the chip's transistor-level/asynchronous SAR implementation.
//
// State is encoded by busy, not a separate FSM register. Rising-edge priority:
//   reset/disable > cancel > start while idle > valid comparison while busy.
// Disable resets the result; cancel retains trial/resolved codes and bit_index
// but clears busy and both event pulses. A busy start is ignored; a concurrent
// valid comparison still advances the conversion. Comparator data is consumed
// only when busy && cmp_valid, so settling can take multiple clock intervals.
module sar_trial_ctrl #(
    parameter int P_BITS = 9,
    parameter int P_SEED_BITS = 3
) (
    input wire clk, rst_n, enable, start, cancel,
    input wire [P_BITS - 1:0] seed_code,
    input wire cmp_valid, cmp_ge,
    output logic [P_BITS - 1:0] trial_code, resolved_code,
    output logic busy, done, resolved_valid,
    output wire compare_enable
);
  localparam int IW = (P_BITS > 1) ? $clog2(P_BITS) : 1;
  logic [IW - 1:0] bit_index;
  logic [P_BITS - 1:0] accepted;
  initial begin
    if (P_BITS < 1 || P_SEED_BITS < 0 || P_SEED_BITS >= P_BITS)
      $fatal(1, "sar_trial_ctrl: invalid seed/resolution");
  end

  assign compare_enable = enable && busy && !cancel;

  // The pending bit is applied to a temporary copy. resolved_code contains
  // only accepted decisions; trial_code additionally raises the next test bit.
  always_comb begin
    accepted = resolved_code;
    accepted[bit_index] = cmp_ge;
  end

  always_ff @(posedge clk) begin
    if (!rst_n || !enable) begin
      trial_code <= '0;
      resolved_code <= '0;
      bit_index <= '0;
      busy <= 0;
      done <= 0;
      resolved_valid <= 0;
    end else if (cancel) begin
      busy <= 0;
      done <= 0;
      resolved_valid <= 0;
    end else begin
      // Pulses refer to the update just committed by this rising edge. Other
      // always_ff blocks observe these pulses on the FOLLOWING rising edge.
      done <= 0;
      resolved_valid <= 0;
      if (start && !busy) begin
        // Keep only the flash seed MSBs; initialize the first unresolved bit.
        resolved_code <= (seed_code >> (P_BITS - P_SEED_BITS)) << (P_BITS - P_SEED_BITS);
        trial_code <= ((seed_code >> (P_BITS - P_SEED_BITS)) << (P_BITS - P_SEED_BITS)) |
            (P_BITS'(1) << (P_BITS - P_SEED_BITS - 1));
        bit_index <= IW'(P_BITS - P_SEED_BITS - 1);
        busy <= 1;
        resolved_valid <= 1;
      end else if (busy && cmp_valid) begin
        resolved_code <= accepted;
        resolved_valid <= 1;
        if (bit_index == 0) begin
          trial_code <= accepted;
          busy <= 0;
          done <= 1;
        end else begin
          trial_code <= accepted | (P_BITS'(1) << (bit_index - 1'b1));
          bit_index <= bit_index - 1'b1;
        end
      end
    end
  end
endmodule
