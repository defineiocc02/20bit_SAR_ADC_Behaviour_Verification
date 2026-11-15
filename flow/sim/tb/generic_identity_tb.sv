`timescale 1ns/1ps
`include "rtl_params.vh"
// COMPONENT_SYNTHETIC_DIGITAL: three explicit positive Q30 terms and an exact affine identity.
// Bounded complete 12-bit input grid plus 26 20-bit boundaries. No old vectors, ADC profile,
// physical geometry or million-code default sweep.
module generic_identity_case #(parameter int ADC_BITS=20)(output logic done=0);
  logic clk=0,rst_n=0,start=0;
  always #1 clk=~clk;
  logic [ADC_BITS-1:0] code=0;
  logic [19:0] dout;
  logic [0:0][2:0][47:0] weights;
  wire valid,clip_low,clip_high,acc_ovf,gain_err,adc2_ovf;
  wire [31:0] result_id;
  logic [31:0] sample_id=0;
  int checks=0;
  recon_core #(.P_N_ACTIVE(1),.P_N_MAIN(2),.P_N_SUB(1),.P_N_SLICES(1),
    .P_ADC2_BITS(ADC_BITS),.P_STAGES(7),.P_USE_ROW_TOTALS(0)) dut(
    .clk(clk),.rst_n(rst_n),.cfg_ready(1'b1),.start(start),.sample_id(sample_id),.clr_ovf(1'b0),
    .slice_id(5'd0),.main_on(2'd0),.sub_on(1'b0),.adc2_code(code),.inj_q(64'sd0),
    .w_rom(weights),.row_total('0),.offset_q(64'sd0),.adc2_min_q(64'sd0),.adc2_max_q(64'sd8589934592),
    .dout(dout),.dout_valid(valid),.clip_low(clip_low),.clip_high(clip_high),.acc_ovf(acc_ovf),
    .gain_err(gain_err),.adc2_ovf(adc2_ovf),.busy(),.result_sample_id(result_id),.result_flags());
  task automatic check_code(input int value);
    int cycles;
    logic [19:0] expected;
    @(negedge clk);code=ADC_BITS'(value);sample_id=32'(checks+1);start=1;
    expected=(ADC_BITS==20)?20'(value):20'((2*value+1) << (19-ADC_BITS));
    @(negedge clk);start=0;cycles=0;code='1;sample_id='1;
    while(!valid)begin @(negedge clk);cycles++;if(cycles>12)$fatal(1,"identity timeout");end
    if(cycles!=11 || dout!==expected || result_id!=32'(checks+1) ||
       {clip_low,clip_high,acc_ovf,gain_err,adc2_ovf}!==5'd0)
      $fatal(1,"generic identity ADC_BITS=%0d value=%0d got=%0d want=%0d",ADC_BITS,value,dout,expected);
    checks++;
  endtask
  initial begin
    int boundary[26]='{0,1,2,3,15,16,31,32,63,64,127,128,255,256,511,512,
                           1023,1024,4095,4096,65535,65536,524287,524288,1048574,1048575};
    if(W_BITS!=48 || W_FRAC!=30 || V_BITS!=64 || V_FRAC!=32 || ACC_BITS!=96 || OUT_BITS!=20)
      $fatal(1,"explicit engineering_protocol numerical format required");
    weights[0][0]=48'd536870912;weights[0][1]=48'd268435456;weights[0][2]=48'd268435456;
    repeat(2)@(negedge clk);rst_n=1;
    if(ADC_BITS==12)for(int value=0;value<4096;value++)check_code(value);
    else if(ADC_BITS==20)foreach(boundary[n])check_code(boundary[n]);
    else $fatal(1,"unsupported bounded identity case");
    if(checks!=(ADC_BITS==12?4096:26))$fatal(1,"identity coverage changed");
    $display("GENERIC_IDENTITY_CASE_PASS adc_bits=%0d checks=%0d",ADC_BITS,checks);done=1;
  end
endmodule
module generic_identity_tb;
  wire[1:0] done;
  generic_identity_case #(.ADC_BITS(12)) full_small(.done(done[0]));
  generic_identity_case #(.ADC_BITS(20)) boundary_large(.done(done[1]));
  initial begin wait(&done);$display("GENERIC_IDENTITY_COMPLETE cases=2 checks=4122 full_12bit=4096 boundary_20bit=26");$finish;end
  initial begin #200000;$fatal(1,"generic identity watchdog");end
endmodule
