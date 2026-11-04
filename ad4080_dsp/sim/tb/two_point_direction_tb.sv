`timescale 1ns/1ps
// Expiring diagnostic: a successful marker confirms a known forward-fit limit;
// it does not mean the two-point inverse calibration requirement is satisfied.
module two_point_direction_tb;
  logic clk=0,rst_n=0,start=0,valid_in=0;
  always #5 clk=~clk;
  logic [19:0] y1=0,y2=0,x=0;
  wire [9:0] gain;
  wire signed [11:0] offset_code;
  wire done,error,valid_out,clip_low,clip_high;
  wire [19:0] y;
  cal_two_point u_cal(.clk(clk),.rst_n(rst_n),.start(start),.y1(y1),.y2(y2),.gain_code(gain),.offset_code(offset_code),.done(done),.error(error));
  sys_error_corr u_apply(.clk(clk),.rst_n(rst_n),.valid_in(valid_in),.x(x),.offset_code(offset_code),.gain_code(gain),.y(y),.valid_out(valid_out),.clip_low(clip_low),.clip_high(clip_high));
  initial begin
    repeat(3) @(negedge clk);#1;rst_n=1;
    // Known physical inputs -419430/+419430; measured raw codes for 1.01*x+100.
    @(negedge clk);#1;y1=20'(-423524);y2=20'(423724);start=1;
    @(negedge clk);#1;start=0;
    while(!done) @(negedge clk);
    if(gain!=840 || offset_code!=100 || error) $fatal(1,"unexpected two-point forward coefficients");
    @(negedge clk);#1;x=20'(303100);valid_in=1;
    @(negedge clk);#1;valid_in=0;
    while(!valid_out) @(negedge clk);
    if($signed(y)!=306234 || clip_low || clip_high) $fatal(1,"unexpected forward application");
    $display("TWO_POINT_DIRECTION_COUNTEREXAMPLE gain=%0d offset=%0d true=300000 raw=303100 corrected=%0d",gain,offset_code,$signed(y));
    $finish;
  end
  initial begin #10000; $fatal(1,"watchdog"); end
endmodule
