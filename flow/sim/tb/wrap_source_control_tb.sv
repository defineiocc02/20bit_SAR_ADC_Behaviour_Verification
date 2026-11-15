// Wrapper: set the explicit 5 ns tick for the VCS run without relying on -P syntax.
`timescale 1ns/1fs
module wrap_source_control_tb;
  source_control_contract_tb #(.P_TICK_NS(5.0)) u();
endmodule
