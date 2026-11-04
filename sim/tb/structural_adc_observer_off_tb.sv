`timescale 1ns/1ps
// Reuse the physical-output scoreboard with the optional observer disabled.
module structural_adc_observer_off_tb;
  structural_adc_tb #(.P_OBSERVER(0)) u_test();
endmodule
