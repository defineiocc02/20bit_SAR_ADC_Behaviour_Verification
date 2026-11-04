`timescale 1ns/1ps
module cnv_ctrl_contract_tb;
  logic clk=0;
  always #5 clk=~clk;
  logic rst_n=0,cnv_p=0,cnv_n=0,lvds_en=0,busy=0;
  wire cnv_pulse,cnv_err;
  cnv_ctrl dut(.*);
  int pulse_count=0,error_count=0,checks=0;
  logic previous_pulse=0;
  task automatic tick(input int count);
    repeat(count)begin @(negedge clk);#1;end
  endtask
  task automatic require(input bit condition,input string message);
    checks++;
    if(!condition)$fatal(1,"CNV contract: %s",message);
  endtask
  always @(posedge clk)begin
    #1;
    if(!rst_n)begin
      pulse_count=0;error_count=0;previous_pulse=0;
    end else begin
      require(!(previous_pulse && cnv_pulse),"pulse wider than one clk");
      if(cnv_pulse)pulse_count++;
      if(cnv_err)error_count++;
      previous_pulse=cnv_pulse;
    end
  end
  initial begin
    for(int mode=0;mode<2;mode++)begin
      rst_n=0;lvds_en=1'(mode);cnv_p=0;cnv_n=1'(mode);busy=0;
      tick(3);rst_n=1;tick(6);
      require(pulse_count==0,"idle reset release produced a CNV");
      for(int k=0;k<3;k++)begin
        cnv_p=1;cnv_n=0;tick(5);
        cnv_p=0;cnv_n=1'(mode);tick(5);
      end
      require(pulse_count==3,"three input rising edges must yield three pulses");
      require(error_count==0,"idle converter produced a busy error");
      busy=1;
      for(int k=0;k<2;k++)begin
        cnv_p=1;cnv_n=0;tick(5);
        cnv_p=0;cnv_n=1'(mode);tick(5);
      end
      require(pulse_count==5 && error_count==2,"busy error count mismatch");
    end
    $display("CNV_CTRL_CONTRACT_COMPLETE modes=2 input_edges=10 checks=%0d",checks);
    $display("CNV_CTRL_CONTRACT_TB PASS");$finish;
  end
  initial begin #20000;$fatal(1,"CNV watchdog");end
endmodule
