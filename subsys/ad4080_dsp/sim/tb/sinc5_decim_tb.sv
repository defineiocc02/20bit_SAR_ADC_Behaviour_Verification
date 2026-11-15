`timescale 1ns/1ps
// 检查有符号滤波数值核；数学稳态预热不等于器件 FILTER_RDY。
module sinc5_decim_tb;
  logic clk=0,rst_n=0,filter_sync=0,valid_in=0;
  logic[19:0]data_in=0;logic[3:0]dec_sel=1;
  wire[19:0]data_out,out1;wire valid_out,v1;
  int checks=0,outputs1=0,outputs5=0,steady_compares=0,phase5=0,expected=0;
  bit active=0;
  always #5 clk=~clk;
  sinc5_decim dut(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(data_out),.valid_out(valid_out));
  sinc1_decim first(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(out1),.valid_out(v1));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"SINC: %s",why);endtask
  always @(posedge clk)begin
    #1;
    if(rst_n&&active&&!filter_sync)begin
      if(v1)begin outputs1++;require(int'($signed(out1))==expected,"SINC1 signed block average");end
      if(valid_out)begin
        outputs5++;phase5++;
        if(phase5>20)begin require(int'($signed(data_out))==expected,"SINC5 steady signed DC unity");steady_compares++;end
      end
    end
  end
  task automatic dc_case(input int value);
    int b1,b5;
    @(negedge clk);active=0;valid_in=0;filter_sync=1;
    @(negedge clk);#1;require(!valid_out&&!v1,"sync did not cancel valid pipeline");filter_sync=0;
    expected=value;phase5=0;b1=outputs1;b5=outputs5;active=1;
    for(int i=0;i<256;i++)begin
      @(negedge clk);valid_in=1;data_in=20'(value);
      @(negedge clk);valid_in=0;
      if(i%3==0)@(negedge clk);
    end
    repeat(8)@(negedge clk);
    require(outputs1-b1==64,"SINC1 accepted-sample rate");
    require(outputs5-b5==62,"SINC5 valid-domain pipeline count with gaps");
    active=0;
  endtask
  initial begin
    repeat(3)@(negedge clk);rst_n=1;
    dc_case(12345);dc_case(-23456);dc_case(-524288);
    require(steady_compares>100&&outputs1==192&&outputs5==186,"nonzero signed CIC comparisons");
    $display("SINC_KERNEL_CONTRACT_COMPLETE cases=3 sinc1_outputs=%0d sinc5_outputs=%0d steady_compares=%0d checks=%0d",outputs1,outputs5,steady_compares,checks);$finish;
  end
  initial begin #100000;$fatal(1,"SINC watchdog");end
endmodule
