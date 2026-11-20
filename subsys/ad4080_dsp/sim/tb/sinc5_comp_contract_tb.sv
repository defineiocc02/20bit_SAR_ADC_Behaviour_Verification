`timescale 1ns/1ps
// =============================================================================
// 文件名     : sinc5_comp_contract_tb.sv
// 模块名     : sinc5_comp_contract_tb
// 版本       : v1.0
// 功能       : 用独立整数参考核对显式绑定的工程补偿 FIR。
// 设计说明   :
//   * 用真实 SINC5 输出作为本级输入，再用 64 位独立整式求 FIR 值。
//     选定系数用于分辨正、负及非单位乘法，厂商响应另行绑定。
//   * 第三个 SINC5 输出是首个拥有三点历史的偶抽取点。组末停样后
//     仍会收到最后一个 SINC5 结果，计数须包含此尾组。
//   * 在有本级待提交结果时施加 FILTER_SYNC，检查取消及重新填充。
// 变更记录   : 2026-11-20 核对重定时后的真实前级启动及末组排空数。
// =============================================================================
module sinc5_comp_contract_tb;
  logic clk=0,rst_n=0,filter_sync=0,valid_in=0;logic[19:0]data_in=0;logic[3:0]dec_sel=1;
  wire[19:0]data_out,reference_s5,missing_out;wire valid_out,s5_valid,missing_valid,processing_reject,missing_reject;
  localparam int C0=24576,C1=16384,C2=-8192;
  int checks=0,compares=0,positives=0,negatives=0,changed=0,rejects=0,sync_cancels=0,cycle=0,index=0;
  int s5_total=0,previous=0;longint signed h1=0,h2=0;
  longint signed expected_queue[$];int due_queue[$];
  always #5 clk=~clk;
  sinc5_comp #(.P_COMP_COEFF_VALID(1),.C0(C0),.C1(C1),.C2(C2)) dut(.*);
  sinc5_comp missing(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(missing_out),.valid_out(missing_valid),.processing_reject(missing_reject));
  sinc5_decim reference(.clk(clk),.rst_n(rst_n),.filter_sync(filter_sync),.valid_in(valid_in),.data_in(data_in),.dec_sel(dec_sel),.data_out(reference_s5),.valid_out(s5_valid));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"COMP: %s",why);endtask
  function automatic longint signed floor_div(input longint signed n,input longint signed d);return n>=0?n/d:-((-n+d-1)/d);endfunction
  always @(posedge clk)begin
    longint signed current,acc,expected,got_expected;int due;
    cycle++;
    if(!rst_n||filter_sync)begin expected_queue.delete();due_queue.delete();index=0;h1=0;h2=0;end
    else if(s5_valid)begin
      current=longint'($signed(reference_s5));s5_total++;
      if(s5_total>1&&current!=longint'(previous))changed++;
      previous=int'(current);
      if(index>=2&&index%2==0)begin
        acc=current*C0+h1*C1+h2*C2;
        expected=floor_div(acc+16384,32768);
        if(expected>524287)expected=524287;
        if(expected< -524288)expected=-524288;
        expected_queue.push_back(expected);due_queue.push_back(cycle+1);
      end
      h2=h1;h1=current;index++;
    end
    #1;
    if(rst_n)begin
      require(!processing_reject,"explicit coefficients rejected");
      require(missing_reject==(valid_in&&!filter_sync),"missing binding gate did not reject request");
      if(missing_reject)rejects++;
      require(!missing_valid,"unbound coefficient instance produced result");
      if(filter_sync)require(!valid_out,"sync did not cancel pending FIR output");
      else if(valid_out)begin
        require(expected_queue.size()>0&&due_queue.size()>0,"output before FIR priming or no oracle");
        got_expected=expected_queue.pop_front();due=due_queue.pop_front();
        require(due==cycle,"registered FIR latency");
        require(longint'($signed(data_out))==got_expected,"independent64bit signed FIR oracle");
        compares++;if($signed(data_out)>0)positives++;if($signed(data_out)<0)negatives++;
      end else if(due_queue.size()>0)require(due_queue[0]>cycle,"missing expected FIR output");
    end
  end
  task automatic feed(input int n);
    for(int i=0;i<n;i++)begin
      @(negedge clk);valid_in=1;data_in=20'((i%128<64)?(150000-(i%64)*3000):(-150000+(i%64)*3000));
      if(i%5==0)begin @(negedge clk);valid_in=0;end
    end
    @(negedge clk);valid_in=0;repeat(12)@(negedge clk);
  endtask
  initial begin
    int old_compare,old_s5,pending,waited;
    repeat(3)@(negedge clk);rst_n=1;
    @(negedge clk);filter_sync=1;@(negedge clk);filter_sync=0;
    old_compare=compares;old_s5=s5_total;feed(512);
    // 512 笔经 D=4 得到 128 组。零基索引 2、4、…、126 提交 63 个 FIR 结果。
    require(s5_total-old_s5==128&&compares-old_compare==63,"2N output rate/priming");
    // 先确认输出已排队，再测试取消，避免只在空流水上检查同步复位。
    waited=0;
    while(expected_queue.size()==0&&waited<200)begin @(negedge clk);valid_in=1;data_in=20'd40000;waited++;end
    pending=expected_queue.size();require(pending>0,"sync cancellation fixture is empty");filter_sync=1;
    @(negedge clk);filter_sync=0;valid_in=0;sync_cancels++;repeat(6)@(negedge clk);
    require(expected_queue.size()==0,"sync left oracle pending");
    old_compare=compares;old_s5=s5_total;feed(128);
    // 清同步后 128 笔的 32 个前级结果只允许在索引 2、4、…、30 提交。
    require(s5_total-old_s5==32&&compares-old_compare==15,"post-sync priming and 2N rate");
    require(compares==78&&positives>10&&negatives>10&&changed>50&&rejects>=640&&sync_cancels==1,"nonzero changing signed FIR checks");
    $display("SINC5_COMP_CONTRACT_COMPLETE fir_compares=%0d positive=%0d negative=%0d changed_s5=%0d unbound_rejects=%0d sync_cancels=%0d checks=%0d",compares,positives,negatives,changed,rejects,sync_cancels,checks);$finish;
  end
  initial begin #100000;$fatal(1,"COMP watchdog");end
endmodule
