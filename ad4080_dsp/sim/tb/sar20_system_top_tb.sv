`timescale 1ns/1ps
`include "rtl_params.vh"
// 检查真实数字核与 DSP，必须由外部提供参数包、系数和控制流。时钟仅用于
// 功能仿真，不证明模拟宏时序或物理签核。
module sar20_system_top_tb;
  `include "rtl_error_codes.vh"
  logic clk=0,rst_n=0,source_control_valid=0;
  wire source_control_ready,acquisition_control_valid;
  wire[CONTROL_WORD_BITS-1:0]acquisition_control_word;
  wire[STATE_BITS-1:0]acquisition_control_state;
  wire signed[V_BITS-1:0]acquisition_sadc_shift_q,acquisition_rdac_shift_q;
  logic[CONTROL_WORD_BITS-1:0]source_control_word;
  logic[STATE_BITS-1:0]source_control_state;
  wire[1:0][8:0]coarse_trial;wire[ADC2_BITS-1:0]fine_trial;
  logic[6:0]flash_therm;logic[1:0]coarse_cmp_ge;logic fine_cmp_ge;
  logic[1:0]coarse_cmp_valid=2'b11;logic fine_cmp_valid=1;
  wire[3:0]analog_phase;wire[31:0]sar_status_word;wire sar_conversion_fault;
  logic cfg_wr=0,cfg_validate=0,cfg_clear_valid=0;logic[15:0]cfg_addr=0;logic[63:0]cfg_wdata=0;wire[63:0]cfg_rdata;wire cfg_ready;
  logic dsp_restart=0,cs_n=1,sclk=1,sdi=0,adc_idle=0,interface_ready=1;
  wire sdo,sdo_oe;wire[OUT_BITS-1:0]sar_dout;wire sar_dout_valid,sar_analog_ovf;
  wire[31:0]sar_sample_id,accepted_sample_id,rejected_sample_id;wire[4:0]sar_flags,rejected_flags;
  wire sample_accepted,sample_rejected,rejected_after_fault,stream_fault;
  wire[19:0]dsp_dout;wire dsp_dout_valid;wire[7:0]dsp_status;
  wire correction_clip_low,correction_clip_high,cfg_reject,processing_reject;wire[3:0]cfg_reject_reason;
  int coarse_target,fine_target,checks=0,accepted=0,comparisons=0,source_handshakes=0,busy_rejects=0,interface_rejects=0,csr_compares=0;
  int control_index=0;logic[CONTROL_WORD_BITS+STATE_BITS-1:0]controls[$];
  int qualified_shift_cycles=0,nonzero_rdac_cycles=0;
  bit permit_fault=0;
  int fault_cases=0,good_raw_rejected=0,dsp_restart_blocks=0,epoch_recoveries=0;
  logic[W_BITS-1:0]weights[0:N_SLICES*N_UNIT_TOTAL-1];logic signed[V_BITS-1:0]scalars[0:2];
  logic[19:0]expected[$];logic[31:0]expected_ids[$];
  always #5 clk=~clk;
  always_comb begin
    flash_therm=7'((1<<(coarse_target>>6))-1);
    coarse_cmp_ge[0]=coarse_target>=int'(coarse_trial[0]);
    coarse_cmp_ge[1]=coarse_target>=int'(coarse_trial[1]);
    fine_cmp_ge=fine_target>=int'(fine_trial);
    source_control_word='0;source_control_state='0;
    if(controls.size()>0)begin source_control_word=controls[control_index][CONTROL_WORD_BITS-1:0];source_control_state=controls[control_index][CONTROL_WORD_BITS+:STATE_BITS];end
  end
  sar20_system_top #(.DSP_FIFO_DEPTH(32),.P_WRITE_SDO_DRIVE(0)) dut(
    .clk(clk),.rst_n(rst_n),.source_control_valid(source_control_valid),.source_control_word(source_control_word),.source_control_state(source_control_state),
    .source_control_ready(source_control_ready),.acquisition_control_valid(acquisition_control_valid),.acquisition_control_word(acquisition_control_word),.acquisition_control_state(acquisition_control_state),
    .acquisition_sadc_shift_q(acquisition_sadc_shift_q),.acquisition_rdac_shift_q(acquisition_rdac_shift_q),
    .flash_therm(flash_therm),.flash_valid(1'b1),.coarse_cmp_valid(coarse_cmp_valid),.coarse_cmp_ge(coarse_cmp_ge),.fine_cmp_valid(fine_cmp_valid),.fine_cmp_ge(fine_cmp_ge),
    .analog_phase(analog_phase),.quiet_sample(),.tp_clock(),.ra_az(),.ra_amplify(),.ref_precharge(),.ref_accurate(),.acquiring_mask(),.converting_mask(),.aux_charge_enable(),.hold_low_enable(),
    .coarse_compare_enable(),.coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),.coarse_trial(coarse_trial),.fine_trial(fine_trial),.fine_compare_enable(),.flash_sample(),
    .cfg_wr(cfg_wr),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),.cfg_rdata(cfg_rdata),.cfg_validate(cfg_validate),.cfg_clear_valid(cfg_clear_valid),.cfg_ready(cfg_ready),
    .ra_sat(1'b0),.rdac_ovf(1'b0),.adc2_over(1'b0),.slice_sel(),.main_sw(),.sub_sw(),.sw_valid(),
    .sar_dout(sar_dout),.sar_dout_valid(sar_dout_valid),.sar_clip_low(),.sar_clip_high(),.sar_analog_ovf(sar_analog_ovf),.sar_acc_ovf(),.sar_status_word(sar_status_word),.sar_sample_id(sar_sample_id),.sar_flags(sar_flags),.sar_result_analog_bad(),.sar_conversion_fault(sar_conversion_fault),.cal_obs_valid(),.cal_obs_data(),
    .dsp_restart(dsp_restart),.sample_accepted(sample_accepted),.accepted_sample_id(accepted_sample_id),.sample_rejected(sample_rejected),.rejected_sample_id(rejected_sample_id),.rejected_flags(rejected_flags),.rejected_after_fault(rejected_after_fault),.stream_fault(stream_fault),
    .cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),.adc_idle(adc_idle),.interface_ready(interface_ready),
    .filter_sync(1'b0),.ext_event(1'b0),.arm(1'b0),.rd_en(1'b0),.gpio_i(4'd0),.gpio_o(),.gpio_oe(),.dsp_dout(dsp_dout),.dsp_dout_valid(dsp_dout_valid),.dsp_status(dsp_status),
    .correction_clip_low(correction_clip_low),.correction_clip_high(correction_clip_high),.cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason),.processing_reject(processing_reject));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"REAL_SYSTEM: %s",why);endtask
  always @(posedge clk)begin
    logic[19:0]word_expected;logic[31:0]id_expected;
    if(rst_n)begin
      if(cfg_clear_valid||sar_conversion_fault||stream_fault)begin expected.delete();expected_ids.delete();end
      if(acquisition_control_valid)begin
        require(!$isunknown({acquisition_sadc_shift_q,acquisition_rdac_shift_q}),"qualified acquisition shifts unknown");
        qualified_shift_cycles++;
        if(acquisition_rdac_shift_q!=0)nonzero_rdac_cycles++;
      end
      if(source_control_ready&&source_control_valid)begin source_handshakes++;control_index<=(control_index+1)%controls.size();end
      if(sample_accepted)begin
        require(accepted_sample_id==sar_sample_id&&sar_flags==0&&!sar_analog_ovf,"real SAR accepted identity/flags");
        expected.push_back(sar_dout);expected_ids.push_back(sar_sample_id);accepted++;
      end
      require(permit_fault||(!sample_rejected&&!stream_fault&&!sar_conversion_fault),"normal component SAR stream fault/reject");
      if(permit_fault&&sar_conversion_fault)begin
        require(!sample_accepted,"conversion fault accepted a raw result");
        if(sar_dout_valid)begin
          require(sar_flags==0&&!sar_analog_ovf,"fault counterexample requires later good raw metadata");
          require(sample_rejected&&rejected_sample_id==sar_sample_id&&rejected_flags==0,
              "later good raw rejection lost real ID or invented flags");
          if(stream_fault)require(rejected_after_fault,"later good raw not identified as postfault rejection");
          good_raw_rejected++;
        end else require(!sample_rejected,"no-result conversion fault invented a rejected ID");
      end
    end
    #1;
    if(rst_n)begin
      require(!processing_reject,"unity bypass rejected data");
      if(cfg_reject)begin require(cfg_reject_reason==8,"unexpected configuration reject");if(interface_ready)busy_rejects++;else interface_rejects++;end
      if(sar_conversion_fault||stream_fault)begin
        require(!dsp_dout_valid&&!sample_accepted,"fault leaked accepted input or in-flight DSP output");
        expected.delete();expected_ids.delete();
      end else if(dsp_dout_valid)begin
        require(expected.size()>0,"unmatched real-core DSP output");word_expected=expected.pop_front();id_expected=expected_ids.pop_front();
        require(dsp_dout===word_expected,$sformatf("full20 codec/order for accepted ID%0d",id_expected));comparisons++;
        require(!correction_clip_low&&!correction_clip_high,"bypass DSP clipped");
      end
    end
  end
  task automatic cfg_write(input logic[15:0]address,input logic[63:0]value);
    @(negedge clk);cfg_addr=address;cfg_wdata=value;cfg_wr=1;@(negedge clk);cfg_wr=0;
  endtask
  task automatic program_coefficients;
    for(int s=0;s<N_SLICES;s++)begin
      cfg_write(16'(16'h2000+s*256),64'd0); // 选择权重行，此写值不是权重。
      for(int u=0;u<N_UNIT_TOTAL;u++)cfg_write(16'(u*8),64'(weights[s*N_UNIT_TOTAL+u]));
    end
    cfg_write(16'h1000,64'($signed(scalars[0])));cfg_write(16'h1008,64'($signed(scalars[1])));cfg_write(16'h1010,64'($signed(scalars[2])));
    source_control_valid=1;@(negedge clk);cfg_validate=1;@(negedge clk);cfg_validate=0;
    repeat(8)@(negedge clk);require(cfg_ready,"explicit complete coefficient config not ready");
  endtask
  task automatic conversion_failure_case(input int kind);
    int timeout,before_good,before_comparisons;
    logic[31:0]expected_error;
    permit_fault=1;
    while(analog_phase!=0)@(negedge clk);
    // 只驱动真实公开输入，不force内部error或伪造带错误flag的输出。
    if(kind==0)begin
      source_control_valid=0;
      while(analog_phase!=2)@(negedge clk);
      source_control_valid=1;expected_error=ERR_SOURCE_BINDING;
    end else if(kind==1)begin
      coarse_cmp_valid=0;
      while(analog_phase!=9)@(negedge clk);
      coarse_cmp_valid=2'b11;expected_error=ERR_SADC_NOT_READY;
    end else begin
      fine_cmp_valid=0;
      while(analog_phase!=15)@(negedge clk);
      fine_cmp_valid=1;expected_error=ERR_ADC2_NOT_READY;
    end
    timeout=0;
    while(!sar_conversion_fault&&timeout<80)begin @(negedge clk);timeout++;end
    require(sar_conversion_fault,"real core failed to report deliberately lost conversion");
    repeat(2)@(negedge clk);
    require(stream_fault&&cfg_ready&&sar_status_word[31:6]==26'(expected_error),
        "actual controller cause or fail-stop qualification missing");
    before_good=good_raw_rejected;timeout=0;
    while(good_raw_rejected<before_good+4&&timeout<256)begin @(negedge clk);timeout++;end
    require(good_raw_rejected>=before_good+4,"counterexample did not reach subsequent good raw results");
    @(negedge clk);dsp_restart=1;@(negedge clk);dsp_restart=0;
    repeat(3)@(negedge clk);
    require(sar_conversion_fault&&stream_fault&&!sample_accepted&&!dsp_dout_valid&&cfg_ready,
        "DSP restart silently bypassed persistent core conversion fault");
    dsp_restart_blocks++;
    // 显式epoch取消会清FIFO/滤波和系数资格；不声称无损恢复或模拟宏已idle。
    @(negedge clk);cfg_clear_valid=1;source_control_valid=0;
    @(negedge clk);cfg_clear_valid=0;
    require(!cfg_ready&&!sar_conversion_fault&&!stream_fault,"epoch clear did not cancel fault/config state");
    program_coefficients();
    require(!sar_conversion_fault&&!stream_fault,"reloaded epoch retained previous fault");
    permit_fault=0;before_comparisons=comparisons;timeout=0;
    while(comparisons<before_comparisons+8&&timeout<512)begin @(negedge clk);timeout++;end
    require(comparisons>=before_comparisons+8,"full epoch reload did not restore real comparisons");
    epoch_recoveries++;fault_cases++;
    $display("SAR20_SYSTEM_FAULT_CASE kind=%0d error=%0d cumulative_good_raw_rejected=%0d",
        kind,expected_error,good_raw_rejected);
  endtask
  task automatic spi_byte(input logic[7:0]tx,output logic[7:0]rx,input int expected_oe);
    for(int i=7;i>=0;i--)begin sdi=tx[i];#80;sclk=0;#80;sclk=1;rx[i]=sdo;if(expected_oe>=0)require(sdo_oe==1'(expected_oe),"SPI SDO ownership");#80;end
  endtask
  task automatic spi_frame_start;@(negedge clk);cs_n=0;sclk=1;repeat(8)@(negedge clk);endtask
  task automatic spi_frame_end;cs_n=1;sclk=1;repeat(8)@(negedge clk);endtask
  task automatic spi_write(input logic[14:0]address,input logic[7:0]value,input int payload_oe);
    logic[7:0]r;spi_frame_start();spi_byte({1'b0,address[14:8]},r,-1);spi_byte(address[7:0],r,-1);spi_byte(value,r,payload_oe);spi_frame_end();
  endtask
  task automatic scratch_read(input logic[7:0]value);
    logic[7:0]r;spi_frame_start();spi_byte(8'h80,r,0);spi_byte(8'h0a,r,0);spi_byte(0,r,1);require(r==value,"independent scratch value");csr_compares++;spi_frame_end();
  endtask
  initial begin
    string weight_path,scalar_path,control_path;int fd,scan,old_comparisons;logic[63:0]extra;logic[CONTROL_WORD_BITS+STATE_BITS-1:0]control;
    require($value$plusargs("WEIGHTS=%s",weight_path)&&$value$plusargs("SCALARS=%s",scalar_path)&&$value$plusargs("CONTROLS=%s",control_path)&&$value$plusargs("COARSE_TARGET=%d",coarse_target)&&$value$plusargs("FINE_TARGET=%d",fine_target),"required explicit component fixture paths/targets missing");
    require(OUT_BITS==20&&W_BITS<=64&&V_BITS==64&&CONTROL_WORD_BITS+STATE_BITS<64&&coarse_target>=0&&coarse_target<512&&fine_target>=0&&fine_target<(1<<ADC2_BITS),"fixture dimensions/targets");
    fd=$fopen(weight_path,"r");require(fd!=0,"weights file missing");
    for(int i=0;i<N_SLICES*N_UNIT_TOTAL;i++)begin
      scan=$fscanf(fd,"%h",extra);require(scan==1&&extra>0&&extra<(64'd1<<(W_BITS-1)),"complete positive in-range coefficient image required");weights[i]=W_BITS'(extra);
    end
    scan=$fscanf(fd,"%h",extra);require(scan==-1,"extra or malformed weight words");$fclose(fd);
    fd=$fopen(scalar_path,"r");require(fd!=0,"scalars missing");
    for(int i=0;i<3;i++)begin scan=$fscanf(fd,"%h",scalars[i]);require(scan==1,"three explicit scalar words required");end
    scan=$fscanf(fd,"%h",extra);require(scan==-1,"extra or malformed scalar words");$fclose(fd);
    fd=$fopen(control_path,"r");require(fd!=0,"control vector missing");
    scan=$fscanf(fd,"%h",extra);
    while(scan==1)begin require(extra<(64'd1<<(CONTROL_WORD_BITS+STATE_BITS)),"control word exceeds explicit widths");control=(CONTROL_WORD_BITS+STATE_BITS)'(extra);controls.push_back(control);scan=$fscanf(fd,"%h",extra);end
    require(scan==-1,"malformed control stream");$fclose(fd);require(controls.size()>0,"nonempty explicitly bound control stream");
    repeat(4)@(negedge clk);rst_n=1;
    require(!cfg_ready&&interface_ready,"interface authority must be independent of core cfg-ready");
    spi_write(15'h000a,8'h5a,0);scratch_read(8'h5a);require(!cfg_ready,"SPI scratch write invented core readiness");
    program_coefficients();
    while(comparisons<12)@(negedge clk);
    old_comparisons=comparisons;spi_write(15'h0000,8'h81,0);require(busy_rejects>0&&cfg_ready,"continuous core idle0 reset not rejected");scratch_read(8'h5a);
    interface_ready=0;spi_write(15'h000a,8'ha5,-1);require(interface_rejects>0&&cfg_ready,"interface-ready conflated with core configuration");interface_ready=1;scratch_read(8'h5a);
    require(comparisons>old_comparisons+8&&accepted>=comparisons&&source_handshakes>20&&csr_compares==3&&adc_idle==0,"nonzero continuous stream/codecs/independent authorities");
    require(qualified_shift_cycles>0&&nonzero_rdac_cycles>0,"fixture must exercise actual qualified nonzero RDAC shift output");
    for(int kind=0;kind<3;kind++)conversion_failure_case(kind);
    require(fault_cases==3&&good_raw_rejected>=12&&dsp_restart_blocks==3&&epoch_recoveries==3&&adc_idle==0,
        "nonvacuous actual-core conversion loss/restart/recovery cases");
    $display("SAR20_SYSTEM_CONTRACT_COMPLETE accepted=%0d comparisons=%0d source_handshakes=%0d busy_reset_rejects=%0d interface_rejects=%0d csr_compares=%0d pending=%0d checks=%0d",accepted,comparisons,source_handshakes,busy_rejects,interface_rejects,csr_compares,expected.size(),checks);
    $display("SAR20_SYSTEM_ACQUISITION_COMPLETE qualified_shift_cycles=%0d nonzero_rdac_cycles=%0d",qualified_shift_cycles,nonzero_rdac_cycles);
    $display("SAR20_SYSTEM_FAULT_COMPLETE fault_cases=%0d good_raw_rejected=%0d dsp_restart_blocks=%0d epoch_recoveries=%0d",
        fault_cases,good_raw_rejected,dsp_restart_blocks,epoch_recoveries);
    $display("SAR20_SYSTEM_IDLE_RESET_PRESERVATION_OPEN no_normal_stop_drain_macro_idle_ack");$finish;
  end
  initial begin #2000000;$fatal(1,"system watchdog");end
endmodule
