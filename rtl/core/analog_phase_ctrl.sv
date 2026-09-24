// Clock-domain control boundaries for the analog macros. These are enables,
// not physical non-overlap/level-shifter cells. Implement the shared TP edge
// and local series-enable/hold-low FETs in the analog macro (paper Fig9.8.2).
// Default 16 ticks/sample is the project's budget, not the published 7ns SAR.
module analog_phase_ctrl #(
    parameter int P_PHASES=16,
    parameter int P_REF_ON=10,
    parameter int P_CAPTURE=9
) (
    input wire clk,rst_n,enable,
    output logic [$clog2(P_PHASES)-1:0] phase,
    output wire quiet_sample, bank_advance, residue_capture,
    output logic tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate
);
  initial begin
    if(P_PHASES<16 || P_CAPTURE<9 || P_REF_ON<=P_CAPTURE || P_REF_ON>=P_PHASES)
      $fatal(1,"analog_phase_ctrl: invalid schedule");
  end
  localparam int PW=$clog2(P_PHASES);
  wire [PW-1:0] next_phase=(int'(phase)==P_PHASES-1)?'0:phase+1'b1;
  // Never drive an analog switch/clock from a multi-bit counter decoder:
  // counter carry skew could otherwise create extra TP edges. Registered
  // controls retain a full dead tick between precharge and accurate reference.
  always_ff @(posedge clk) begin
    if(!rst_n || !enable) begin
      phase<='0;tp_clock<=0;ra_az<=0;ra_amplify<=0;ref_precharge<=0;ref_accurate<=0;
    end else begin
      phase<=next_phase;
      tp_clock<=next_phase!=0;
      ra_az<=next_phase>=1 && int'(next_phase)<P_CAPTURE;
      ra_amplify<=int'(next_phase)>=P_REF_ON || next_phase==0;
      ref_precharge<=next_phase>=2 && int'(next_phase)<P_CAPTURE;
      ref_accurate<=int'(next_phase)>=P_REF_ON || next_phase==0;
    end
  end
  assign quiet_sample=rst_n && enable && phase==0;
  assign bank_advance=rst_n && enable && phase==1;
  assign residue_capture=rst_n && enable && int'(phase)==P_CAPTURE;
endmodule
