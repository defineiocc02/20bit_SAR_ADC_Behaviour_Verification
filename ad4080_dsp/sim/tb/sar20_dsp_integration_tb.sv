`timescale 1ns/1ps
// 检查真实退役适配器与 DSP 的集成；不包含 SAR 算术验证或模拟宏模型。
module sar20_dsp_integration_tb;
  logic clk=0,rst_n=0,sar_epoch_clear=0,dsp_restart=0;
  logic[19:0]sar_dout=0;logic sar_dout_valid=0,sar_analog_ovf=0,sar_conversion_fault=0;
  logic[31:0]sar_sample_id=0;logic[4:0]sar_flags=0;
  wire sample_accepted,sample_rejected,rejected_after_fault,stream_fault;
  wire[31:0]accepted_sample_id,rejected_sample_id;wire[4:0]rejected_flags;
  logic cs_n=1,sclk=1,sdi=0,adc_idle=0,interface_ready=1;
  logic filter_sync=0,ext_event=0,arm=0,rd_en=0;logic[3:0]gpio_i=0;
  wire sdo,sdo_oe;wire[3:0]gpio_o,gpio_oe,cfg_reject_reason;
  wire[19:0]dout;wire dout_valid,correction_clip_low,correction_clip_high,cfg_reject,processing_reject;
  wire[7:0]status;
  int checks=0,accepted=0,rejected=0,compares=0,canceled=0,after_fault=0,flag_cases=0,sticky_cases=0,restarts=0;
  int conversion_cases=0,held_fault_restarts=0;
  logic[19:0]expected[$];logic[31:0]expected_ids[$];
  always #5 clk=~clk;
  sar20_ad4080_top #(.FIFO_DEPTH(32)) dut(.*);
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"SAR_DSP_INGRESS: %s",why);endtask
  always @(posedge clk)begin
    logic[19:0]word_expected;logic[31:0]id_expected;
    if(!rst_n||dsp_restart||sar_epoch_clear)begin canceled+=expected.size();expected.delete();expected_ids.delete();end
    else begin
      if((sar_dout_valid&&sar_flags!=0)||sar_analog_ovf||sar_conversion_fault)begin
        canceled+=expected.size();expected.delete();expected_ids.delete();
      end
      if(sample_accepted)begin
        require(accepted_sample_id==sar_sample_id&&sar_flags==0&&!sar_analog_ovf&&
            !sar_conversion_fault&&!stream_fault,"accept ID/qualification");
        expected.push_back(sar_dout);expected_ids.push_back(sar_sample_id);accepted++;
      end
      if(sample_rejected)begin
        require(rejected_sample_id==sar_sample_id&&rejected_flags==sar_flags,"reject ID/flags");rejected++;
        if(rejected_after_fault)after_fault++;
      end
      require(!(sample_accepted&&sample_rejected),"accept/reject overlap");
    end
    #1;
    if(rst_n&&!dsp_restart&&!sar_epoch_clear)begin
      require(!cfg_reject&&!processing_reject,"inactive config or unity processing unexpectedly rejected");
      if(dout_valid)begin
        require(expected.size()>0&&expected_ids.size()>0,"unmatched or fault-leaked output");
        word_expected=expected.pop_front();id_expected=expected_ids.pop_front();
        require(dout===word_expected,$sformatf("all20 codec/order mismatch for acceptedID%0d",id_expected));
        require(!correction_clip_low&&!correction_clip_high,"unity offset-binary codec clipped");compares++;
      end
      if(stream_fault||sar_conversion_fault)require(!dout_valid&&!sample_accepted,"persistent fault leaked output");
      if(sar_conversion_fault&&!sar_dout_valid)require(!sample_rejected,"no-result fault invented rejected ID");
    end
  end
  task automatic issue(input logic[19:0]word,input logic[4:0]flags);
    @(negedge clk);sar_dout=word;sar_flags=flags;sar_dout_valid=1;sar_sample_id++;
  endtask
  task automatic stop_and_drain;
    @(negedge clk);sar_dout_valid=0;sar_flags=0;repeat(12)@(negedge clk);
  endtask
  task automatic restart;
    @(negedge clk);sar_dout_valid=0;sar_flags=0;sar_analog_ovf=0;sar_conversion_fault=0;dsp_restart=1;
    @(negedge clk);dsp_restart=0;repeat(3)@(negedge clk);require(!stream_fault,"explicit restart did not clear fault");restarts++;
  endtask
  initial begin
    repeat(4)@(negedge clk);rst_n=1;
    issue(20'h00000,0);issue(20'h7ffff,0);issue(20'h80000,0);issue(20'hfffff,0);
    for(int i=0;i<64;i++)issue(20'(i*13457+123),0);
    stop_and_drain();require(expected.size()==0&&compares==68,"bypass retirement count/order");
    for(int f=0;f<5;f++)begin
      issue(20'h12345,0);issue(20'habcde,0);issue(20'h54321,5'(1<<f));
      @(negedge clk);sar_dout_valid=0;require(stream_fault,"flag did not latch persistent fault");flag_cases++;
      for(int i=0;i<4;i++)issue(20'(i+100),0);
      stop_and_drain();require(stream_fault&&expected.size()==0,"postfault stream resumed implicitly");restart();
    end
    // 即使没有有效退役结果，模拟粘滞输入仍须触发持续故障锁存。
    @(negedge clk);sar_dout_valid=0;sar_analog_ovf=1;@(negedge clk);sar_analog_ovf=0;
    require(stream_fault,"analog sticky without valid ignored");sticky_cases++;
    issue(20'h33333,0);stop_and_drain();restart();
    // 上游转换丢失无 raw word：先建立非空在途队列，再断言独立故障资格。
    issue(20'h11111,0);issue(20'heeeee,0);
    @(negedge clk);sar_dout_valid=0;sar_conversion_fault=1;
    require(expected.size()>0,"conversion fault pending fixture is empty");
    @(negedge clk);require(stream_fault&&!dout_valid,"no-result conversion fault did not fence pending output");
    conversion_cases++;
    issue(20'h12345,0);
    @(negedge clk);sar_dout_valid=0;dsp_restart=1;
    @(negedge clk);dsp_restart=0;
    repeat(3)@(negedge clk);
    require(sar_conversion_fault&&stream_fault&&!sample_accepted&&!dout_valid,"restart bypassed held upstream fault");
    held_fault_restarts++;
    sar_conversion_fault=0;issue(20'h54321,0);stop_and_drain();
    require(stream_fault,"live fault deassertion silently resumed stream");restart();
    issue(20'h0a55a,0);issue(20'hf1234,0);
    @(negedge clk);sar_dout_valid=0;sar_epoch_clear=1;
    @(negedge clk);sar_epoch_clear=0;repeat(3)@(negedge clk);
    issue(20'h23456,0);issue(20'hfedcb,0);stop_and_drain();
    require(expected.size()==0&&accepted>75&&rejected>=28&&compares>=70&&canceled>=12&&
        after_fault>=22&&flag_cases==5&&sticky_cases==1&&conversion_cases==1&&held_fault_restarts==1,
        "nonzero ingress/isolation coverage");
    require(adc_idle==0,"adapter test must not fabricate macro idle");
    $display("SAR20_DSP_INGRESS_CONTRACT_COMPLETE accepted=%0d rejected=%0d comparisons=%0d canceled=%0d after_fault=%0d flag_cases=%0d sticky_cases=%0d restarts=%0d conversion_cases=%0d held_fault_restarts=%0d checks=%0d",accepted,rejected,compares,canceled,after_fault,flag_cases,sticky_cases,restarts,conversion_cases,held_fault_restarts,checks);$finish;
  end
  initial begin #100000;$fatal(1,"SAR DSP ingress watchdog");end
endmodule
