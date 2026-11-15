`timescale 1ns/1ps
// 小规模功能测试使用独立队列/RAM 预期模型，只比较已知内容，不假定 RAM 初始化。
module result_fifo_tb #(parameter int DEPTH=32);
  localparam int W=20,AW=$clog2(DEPTH),AF=3,AE=2;
  logic clk=0,rst_n=0,clear=0,wr_en=0,overwrite_en=0,rd_en=0,snapshot_rd_en=0;
  logic [W-1:0]wr_data=0;logic[AW-1:0]snapshot_addr=0;
  wire[W-1:0]rd_data;wire rd_valid,full,empty,almost_full,almost_empty,overflow,underflow,access_reject;
  always #5 clk=~clk;
  result_fifo #(.DATA_W(W),.DEPTH(DEPTH),.P_AF_MARGIN(AF),.P_AE_LEVEL(AE)) dut(.*);
  logic[W-1:0]memory[DEPTH];bit known[DEPTH];
  int wp=0,rp=0,count=0,checks=0,reads=0,snapshots=0,collisions=0,writes=0;
  bit ov=0,un=0;
  task automatic require(input bit yes,input string why);
    checks++;if(!yes)$fatal(1,"FIFO: %s",why);
  endtask
  task automatic step(input bit w,input int data,input bit q,input bit s,input int sa,input bit ow,input bit cl);
    bit col,wfire,qfire,sfire,compare;logic[W-1:0]expected;
    @(negedge clk);
    wr_en=w;wr_data=W'(data);rd_en=q;snapshot_rd_en=s;snapshot_addr=AW'(sa);overwrite_en=ow;clear=cl;
    col=s&&(w||q);wfire=!cl&&!col&&w&&(count<DEPTH||ow);
    qfire=!cl&&!col&&q&&(count>0);sfire=!cl&&!col&&s;
    compare=(qfire&&known[rp])||(sfire&&known[sa]);expected=memory[sfire?sa:rp];
    if(cl)begin wp=0;rp=0;count=0;end
    else if(!col)begin
      if(w&&count==DEPTH&&!ow)ov=1;
      if(q&&count==0)un=1;
      if(qfire)begin rp=(rp+1)%DEPTH;count--;reads++;end
      else if(wfire&&count==DEPTH&&ow)begin rp=(rp+1)%DEPTH;count--;end
      if(wfire)begin memory[wp]=W'(data);known[wp]=1;wp=(wp+1)%DEPTH;count++;writes++;end
      if(sfire)snapshots++;
    end else collisions++;
    @(posedge clk);#1;
    require(rd_valid==(qfire||sfire),"read valid");
    if(compare)require(rd_data===expected,"ordered data or snapshot mismatch");
    require(full==(count==DEPTH)&&empty==(count==0),"occupancy flags");
    require(almost_full==(count>=DEPTH-AF)&&almost_empty==(count<=AE),"almost flags");
    require(overflow==ov&&underflow==un,"sticky error flags");
    require(access_reject==(!cl&&col),"snapshot/queue exclusivity");
    require(int'(dut.count)==count,"pointer count differs from independent occupancy");
  endtask
  initial begin
    for(int i=0;i<DEPTH;i++)known[i]=0;
    repeat(3)@(negedge clk);rst_n=1;
    step(0,0,0,0,0,0,0);
    for(int i=0;i<DEPTH;i++)step(1,i+100,0,0,0,0,0);
    step(1,9999,0,0,0,0,0);step(1,8888,1,0,0,0,0);
    while(count>0)step(0,0,1,0,0,0,0);
    step(0,0,1,0,0,0,0);
    for(int i=0;i<DEPTH+5;i++)step(1,200+i,0,0,0,1,0);
    step(1,999,1,1,0,1,0);step(0,0,0,1,3,0,0);
    while(count>0)step(0,0,1,0,0,0,0);
    step(0,0,0,0,0,0,1);
    // 逻辑清除后仍应保留先前写入的 RAM 内容，快照读取据此比较。
    for(int i=0;i<DEPTH;i++)step(0,0,0,1,i,0,0);
    for(int i=0;i<256;i++)step((i%3)!=0,i*37,(i%4)==0,(i%17)==0,i%DEPTH,(i%7)==0,0);
    require(reads>DEPTH&&snapshots>=DEPTH&&collisions>0&&writes>2*DEPTH,"nonzero oracle coverage");
    $display("RESULT_FIFO_CONTRACT_COMPLETE depth=%0d queue_reads=%0d snapshot_reads=%0d collisions=%0d writes=%0d checks=%0d",DEPTH,reads,snapshots,collisions,writes,checks);$finish;
  end
  initial begin #200000;$fatal(1,"FIFO watchdog");end
endmodule
