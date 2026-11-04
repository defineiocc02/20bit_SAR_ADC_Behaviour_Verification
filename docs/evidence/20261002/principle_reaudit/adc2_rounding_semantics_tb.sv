module adc2_rounding_semantics_tb;
  logic [11:0] code=0;
  logic signed [63:0] mi=0, ma=1;
  wire signed [63:0] fine;
  wire ov;
  int cases=0, physical_tie_differences=0;
  adc2_dec dut(.adc2_code(code),.adc2_min_q(mi),.adc2_max_q(ma),.fine_q(fine),.ovf(ov));
  task automatic check(input logic signed [63:0] min_q, max_q,
      input logic [11:0] raw,input logic signed [63:0] contract_q, center_rte_q);
    mi=min_q;ma=max_q;code=raw;#1;
    if(ov || fine!==contract_q)$fatal(1,"RTL does not match its frozen increment-RTE contract");
    if(fine!=center_rte_q) physical_tie_differences++;
    cases++;
    $display("ROUNDING_SEMANTICS_CASE min=%0d max=%0d code=%0d RTL_INCREMENT_RTE=%0d PHYSICAL_CENTER_RTE=%0d difference=%0d",
      mi,ma,code,fine,center_rte_q,fine-center_rte_q);
  endtask
  initial begin
    check(64'sd1,64'sd4097,12'd0,64'sd1,64'sd2);
    check(64'sd1,64'sd4097,12'd1,64'sd3,64'sd2);
    check(64'sd1,64'sd4097,12'd4094,64'sd4095,64'sd4096);
    check(64'sd1,64'sd4097,12'd4095,64'sd4097,64'sd4096);
    check(64'sd0,64'sd4096,12'd0,64'sd0,64'sd0);
    check(64'sd0,64'sd4096,12'd1,64'sd2,64'sd2);
    check(-64'sd4097,-64'sd1,12'd0,-64'sd4097,-64'sd4096);
    check(-64'sd4097,-64'sd1,12'd1,-64'sd4095,-64'sd4096);
    check(-64'sd53687091,64'sd590558003,12'd341,64'sd26215,64'sd26215);
    if(cases!=9 || physical_tie_differences!=6)$fatal(1,"Unexpected case coverage");
    $display("ADC2_ROUNDING_SEMANTICS_REPRODUCED cases=9 differences=6 scope=ADC2_ONLY_CONTRACT_MATCHES_PHYSICAL_CENTER_RTE_DIFFERS");
    $finish;
  end
endmodule
