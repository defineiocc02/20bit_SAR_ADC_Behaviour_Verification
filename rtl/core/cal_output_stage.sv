// Registered calibrated output and per-conversion validity. Overflow/gain errors
// preserve the last good word; sticky flags cannot silently turn a bad result into
// a good one. A configuration cancellation discards both the word and its events.
`include "rtl_params.vh"
module cal_output_stage #(
    parameter int P_OUT_BITS = int'(OUT_BITS)
) (
    input wire clk, rst_n, cfg_ready, clr_ovf, complete,
    input wire [P_OUT_BITS-1:0] candidate,
    input wire [31:0] candidate_sample_id,
    input wire candidate_clip_low, candidate_clip_high,
    input wire candidate_acc_ovf, candidate_gain_err, candidate_adc2_ovf,
    output logic [P_OUT_BITS-1:0] dout,
    output logic dout_valid, clip_low, clip_high,
    output logic acc_ovf, gain_err, adc2_ovf,
    output logic [31:0] result_sample_id,
    output logic [4:0] result_flags
);
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      dout <= '0;
      result_sample_id <= '0;
      result_flags <= '0;
      dout_valid <= 1'b0;
      clip_low <= 1'b0;
      clip_high <= 1'b0;
      acc_ovf <= 1'b0;
      gain_err <= 1'b0;
      adc2_ovf <= 1'b0;
    end else begin
      dout_valid <= 1'b0;
      if (clr_ovf) begin
        acc_ovf <= 1'b0;
        gain_err <= 1'b0;
        adc2_ovf <= 1'b0;
      end
      if (cfg_ready && complete) begin
        dout_valid <= 1'b1;
        result_sample_id <= candidate_sample_id;
        result_flags <= {
          !(candidate_acc_ovf || candidate_gain_err) && candidate_clip_high,
          !(candidate_acc_ovf || candidate_gain_err) && candidate_clip_low,
          candidate_adc2_ovf, candidate_gain_err, candidate_acc_ovf};
        adc2_ovf <= (clr_ovf ? 1'b0 : adc2_ovf) | candidate_adc2_ovf;
        if (candidate_acc_ovf) acc_ovf <= 1'b1;
        if (candidate_gain_err) gain_err <= 1'b1;
        if (!(candidate_acc_ovf || candidate_gain_err)) begin
          dout <= candidate;
          clip_low <= candidate_clip_low;
          clip_high <= candidate_clip_high;
        end
      end
      if (!cfg_ready) begin
        result_sample_id <= '0;
        result_flags <= '0;
        dout <= '0;
        dout_valid <= 1'b0;
        clip_low <= 1'b0;
        clip_high <= 1'b0;
      end
    end
  end
endmodule
