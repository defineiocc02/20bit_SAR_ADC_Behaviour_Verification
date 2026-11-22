`timescale 1ns/1ps
// 检查早事件的完整深度快照、事件前后边界及拒绝重新 arm 后的数据顺序。
module fifo_capture_contract_tb #(parameter int DEPTH=32);
  logic clk=0,rst_n=0,clear_capture=0,arm=0,event_in=0,rd_en=0,valid_in=0;
  logic[1:0]mode=0;logic[14:0]watermark=4;logic[19:0]data_in=0;
  wire[19:0]rd_data;wire rd_valid,full,empty,overflow,underflow,read_done,captured,full_flag,wm_wb_valid,capture_active,capture_incomplete,cfg_reject;
  wire[14:0]wm_wb_data;wire[3:0]cfg_reject_reason;
  int checks=0,early_reads=0,known_compares=0,post_compares=0,rearm_rejects=0,ordered_compares=0;
  always #5 clk=~clk;
  fifo_capture #(.DEPTH(DEPTH)) dut(.*);
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"CAPTURE: %s",why);endtask
  task automatic step(input bit a,input bit e,input bit v,input int data,input bit r,input bit cl);
    @(negedge clk);arm=a;event_in=e;valid_in=v;data_in=20'(data);rd_en=r;clear_capture=cl;
    @(posedge clk);#1;
  endtask
  initial begin
    repeat(3)@(negedge clk);rst_n=1;
    mode=3;step(1,0,0,0,0,0);
    // 与事件同沿的输入是最后一个事件前样本，不能计入事件后水位。
    step(0,1,1,111,0,0);
    for(int i=0;i<4;i++)step(0,0,1,201+i,0,0);
    require(captured&&full_flag&&!capture_incomplete&&wm_wb_valid&&wm_wb_data==1,"early event completion/address");
    for(int i=0;i<DEPTH;i++)begin
      step(0,0,0,0,1,0);require(rd_valid,"full-depth snapshot lost a read");early_reads++;
      if(i==DEPTH-5)begin require(rd_data==111,"event-coincident input placement");known_compares++;end
      if(i>=DEPTH-4)begin require(rd_data==20'(201+i-(DEPTH-4)),"post-event WM window order");known_compares++;post_compares++;end
      if(i==0)require(!full_flag,"first snapshot read did not clear record FULL");
      require(read_done==(i==DEPTH-1),"snapshot done timing");
      // 未写前缀保持未定义，不与假定的零值比较。
    end
    step(0,0,0,0,0,1);require(!captured&&!read_done,"explicit clear");
    mode=1;step(1,0,0,0,0,0);step(0,0,1,300,0,0);
    step(1,0,1,301,0,0);require(cfg_reject&&cfg_reject_reason==8,"active rearm did not reject");rearm_rejects++;
    step(0,0,1,302,0,0);step(0,0,1,303,0,0);require(captured,"rearm rejection dropped old accepted input");
    for(int i=0;i<4;i++)begin step(0,0,0,0,1,0);require(rd_valid&&rd_data==20'(300+i),"old session lost order on rejected rearm");ordered_compares++;end
    require(read_done&&early_reads==DEPTH&&known_compares==5&&post_compares==4&&ordered_compares==4,"nonzero data/coverage");
    $display("FIFO_CAPTURE_CONTRACT_COMPLETE depth=%0d early_reads=%0d known_history_compares=%0d post_event_compares=%0d rearm_rejects=%0d ordered_compares=%0d checks=%0d",DEPTH,early_reads,known_compares,post_compares,rearm_rejects,ordered_compares,checks);$finish;
  end
  initial begin #1000000;$fatal(1,"capture watchdog");end
endmodule
