module calibration_domain_tb;
  logic signed [63:0] f=0, o=0, i=0;
  logic [63:0] g=0, t=0;
  logic signed [65:0] rails=0;
  wire [62:0] a;
  wire ov;
  cal_residue_mac dut(.fine_r(f),.off_r(o),.inj_r(i),.gain_s(g),
    .total_s(t),.rails_s(rails),.a1(a),.any_ovf(ov));
  task automatic check(input string name,input logic [63:0] gain,
      input logic signed [63:0] fine,input logic signed [63:0] off,input logic expected_ov);
    logic signed [131:0] expected;
    g=gain;t=gain;f=fine;o=off;#1;
    if(ov!==expected_ov)$fatal(1,"%s ov=%b expected=%b",name,ov,expected_ov);
    expected = ($signed(132'(fine))-$signed(132'(off)))*132'sd1073741824+
      $signed(132'(gain))*132'sd4294967296;
    expected = (expected*132'sd1048576)>>>33;
    if(!ov && a!==expected[62:0])
      $fatal(1,"%s A1 mismatch",name);
    $display("DOMAIN_CASE name=%s gain=%0d fine=%0d offset=%0d ov=%b a1=%0d",name,g,f,o,ov,a);
  endtask
  initial begin
    // Coefficient-only scale changes can be legal to load yet fail the declared accumulator range.
    check("nominal_midscale",64'd34359738368,64'sd26215,64'sd26215,0);
    check("legal_coefficients_scaled_2pow20",64'd36028797018963968,64'sd26215,64'sd26215,1);
    check("midscale_last_safe_gain",64'd8796093022207,64'sd0,64'sd0,0);
    check("midscale_first_overflow_gain",64'd8796093022208,64'sd0,64'sd0,1);
    check("positive_fullscale_last_safe_gain",64'd4398046511103,64'sd17592186044412,64'sd0,0);
    check("positive_fullscale_first_overflow_gain",64'd4398046511104,64'sd17592186044416,64'sd0,1);
    $display("CALIBRATION_DOMAIN_PASS cases=6 scope=MAC_ONLY_NOT_FULL_TOP");
    $finish;
  end
endmodule
