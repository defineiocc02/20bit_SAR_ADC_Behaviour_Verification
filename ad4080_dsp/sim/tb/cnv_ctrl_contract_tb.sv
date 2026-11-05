`timescale 1ns/1ps
// 检查已译码输入边沿的单拍事件、busy 错误计数和复位释放，不验证电气 CNV 接收。
module cnv_ctrl_contract_tb;
  logic clk=0,rst_n=0,cnv_p=0,busy=0;
  wire cnv_pulse,cnv_err;
  always #5 clk=~clk;
  cnv_ctrl dut(.clk(clk),.rst_n(rst_n),.cnv_p(cnv_p),.busy(busy),.cnv_pulse(cnv_pulse),.cnv_err(cnv_err));
  int pulses=0,errors=0,checks=0;
  bit previous_pulse=0;
  task automatic tick(input int n); repeat(n) begin @(negedge clk); #1; end endtask
  task automatic require(input bit yes,input string why);
    checks++; if(!yes)$fatal(1,"CNV: %s",why);
  endtask
  always @(posedge clk) begin
    #1;
    if(rst_n)begin
      require(!(previous_pulse&&cnv_pulse),"decoded event pulse wider than one clock");
      if(cnv_pulse)pulses++;
      if(cnv_err)errors++;
      previous_pulse=cnv_pulse;
    end else previous_pulse=0;
  end
  initial begin
    tick(3);rst_n=1;tick(6);
    require(pulses==0&&errors==0,"idle release produced event");
    for(int n=0;n<5;n++)begin
      busy=(n>=3);cnv_p=1;tick(5);cnv_p=0;tick(5);
    end
    require(pulses==5&&errors==2,"decoded event/busy counts");
    rst_n=0;tick(3);rst_n=1;busy=0;tick(5);
    require(pulses==5&&errors==2,"reset release produced spurious event");
    require(checks>40,"vacuous checks");
    $display("CNV_CTRL_CONTRACT_COMPLETE input_edges=5 busy_errors=%0d pulses=%0d checks=%0d",errors,pulses,checks);$finish;
  end
  initial begin #20000;$fatal(1,"CNV watchdog");end
endmodule
