`timescale 1ns/1ps
// =============================================================================
// 文件名     : sinc5_decim_tb.sv
// 模块名     : sinc5_decim_tb
// 版本       : v1.0
// 功能       : 检查有符号滤波数值、停样排空及清同步/复位后的输出。
// 设计说明   :
//   * D=4 的五重有限窗长度为 16 个接受样本。第 4 个抽取结果应已经达到
//     常量输入的精确直流值，设备 FILTER_RDY 由外层 CNV 合同决定。
//   * 同沿 FILTER_SYNC 或异步复位都须撤销未交付的抽取组。
// 变更记录   : 2026-11-20 对照真实首次完整窗和停止输入后的末组。
// =============================================================================
module sinc5_decim_tb;
  logic clk=0,rst_n=0,filter_sync=0,valid_in=0;
  logic[19:0]data_in=0;logic[3:0]dec_sel=1;
  wire[19:0]data_out,out1;wire valid_out,v1;
  int checks=0,outputs1=0,outputs5=0,steady_compares=0,phase5=0,expected=0;
  bit active=0;
  always #5 clk<=~clk;
  sinc5_decim dut(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(data_out),.valid_out(valid_out));
  sinc1_decim first(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(out1),.valid_out(v1));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"SINC: %s",why);endtask
  initial forever begin
    @(posedge clk);
    #1;
    if(rst_n&&active&&!filter_sync)begin
      if(v1)begin outputs1++;require(int'($signed(out1))==expected,"SINC1 signed block average");end
      if(valid_out)begin
        outputs5++;phase5++;
        // 第 4 个抽取点覆盖完整有限窗，之后每笔均须保持同一有符号直流值。
        if(phase5>=4)begin require(int'($signed(data_out))==expected,"SINC5 full-window signed DC unity");steady_compares++;end
      end
    end
  end
  task automatic dc_case(input int value);
    int b1,b5;
    @(negedge clk);active=0;valid_in=0;filter_sync=1;
    @(negedge clk);#1;require(!valid_out&&!v1,"sync did not cancel valid pipeline");filter_sync=0;
    expected=value;phase5=0;b1=outputs1;b5=outputs5;active=1;
    // 在完整抽取组后停止，不补任何虚拟有效样本。
    for(int i=0;i<256;i++)begin
      @(negedge clk);valid_in=1;data_in=20'(value);
      @(negedge clk);valid_in=0;
      if(i%3==0)@(negedge clk);
    end
    repeat(8)@(negedge clk);
    require(outputs1-b1==64,"SINC1 accepted-sample rate");
    require(outputs5-b5==64,"SINC5 final group drains with input stopped");
    active=0;
  endtask

  // [清空] 刚接受第四笔而尚未到达输出，注入一次复位或同沿有效的 FILTER_SYNC。
  // 继续时钟、不再给样本，观察被撤销的抽取令牌是否泄漏。
  task automatic cancel_pending(input bit hard_reset);
    @(negedge clk);
    valid_in = 0;
    filter_sync = 1;
    @(negedge clk);
    filter_sync = 0;
    valid_in = 1;
    data_in = 20'd4000;
    repeat (4) @(negedge clk);
    valid_in = 0;
    if (hard_reset) begin
      #1;
      rst_n = 0;
    end else begin
      filter_sync = 1;
      valid_in = 1; // 同沿的样本应被清同步丢弃
    end
    @(negedge clk);
    rst_n = 1;
    filter_sync = 0;
    valid_in = 0;
    repeat (8) begin
      @(posedge clk);
      #1;
      require(!valid_out, "clear/reset leaked a pending SINC5 group");
    end
  endtask

  initial begin
    repeat(3)@(negedge clk);rst_n=1;
    dc_case(12345);dc_case(-23456);dc_case(-524288);
    cancel_pending(1'b0);
    cancel_pending(1'b1);
    require(steady_compares==183&&outputs1==192&&outputs5==192,"full-window CIC comparisons");
    $display("SINC_KERNEL_CONTRACT_COMPLETE cases=3 sinc1_outputs=%0d sinc5_outputs=%0d steady_compares=%0d checks=%0d",outputs1,outputs5,steady_compares,checks);$finish;
  end
  initial begin #100000;$fatal(1,"SINC watchdog");end
endmodule
