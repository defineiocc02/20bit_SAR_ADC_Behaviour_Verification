`timescale 1ns/1ps
// Public-port full-top functional witness, derived from structural_adc_tb.sv.
// The independent 256-bit oracle retains its own coefficient fixture and uses
// physical RDAC pins, not DUT arithmetic or optimized internal net names.
// P_RECON_STAGES labels the separately synthesized netlist; it is deliberately
// NOT passed to DUT. Supply a matching P5/P7 netlist. For local RTL checks only,
// STRUCTURAL_MAPPED_DUT can name an external, parameterized RTL wrapper.
// No reconstruction-start latency is checked: that event has no public port.
// The 2 ns clock is a functional stimulus, not an FPGA timing claim. Synchronous
// reset stays low for 220 ns, including glbl's first 100 ns of GSR activity.
`ifndef STRUCTURAL_MAPPED_DUT
`define STRUCTURAL_MAPPED_DUT sar20_digital_core
`endif
module structural_mapped_tb #(parameter int P_RECON_STAGES=7);
  logic clk=0,rst_n=0,cfg_wr=0,cfg_validate=0,cfg_clear_valid=0;
  always #1 clk=~clk;
  logic [15:0] cfg_addr=0;
  logic [63:0] cfg_wdata=0;
  wire [63:0] cfg_rdata;
  wire cfg_ready;
  logic [6:0] flash_therm=0;
  logic flash_valid=1;
  logic [1:0] coarse_valid=3;
  wire [1:0] coarse_ge;
  logic fine_valid=1;
  wire fine_ge;
  wire [3:0] phase;
  wire quiet,tp,az,amplify,precharge,accurate;
  wire [17:0] acq,conv,aux,hold_low,selected;
  wire [1:0] compare_en;
  wire [1:0][8:0] trials;
  wire [1:0][7:0] qdither;
  wire [3:0] acq_rails;
  wire [11:0] fine_trial;
  wire fine_en,flash_sample,sw_valid;
  wire [17:0][62:0] main_sw;
  wire [17:0][7:0] sub_sw;
  wire [17:0][3:0] dith_sw;
  wire [19:0] dout;
  wire dout_valid,clip_low,clip_high,analog_ovf,acc_ovf;
  wire [31:0] status,id;
  wire [4:0] flags;
  logic signed [63:0] injection=0;
  int coarse_target=256,fine_target=2048;
  assign coarse_ge[0]=(coarse_target+int'($signed(qdither[0])))>=int'(trials[0]);
  assign coarse_ge[1]=(coarse_target+int'($signed(qdither[1])))>=int'(trials[1]);
  assign fine_ge=fine_target>=int'(fine_trial);
  `STRUCTURAL_MAPPED_DUT dut(
    .clk(clk),.rst_n(rst_n),.cfg_wr(cfg_wr),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),
    .cfg_rdata(cfg_rdata),.cfg_validate(cfg_validate),.cfg_clear_valid(cfg_clear_valid),.cfg_ready(cfg_ready),
    .sadc_code(9'd0),.sadc_rdy(1'b0),.adc2_code(12'd0),.adc2_rdy(1'b0),
    .ra_sat(1'b0),.rdac_ovf(1'b0),.adc2_over(1'b0),.inj_q(injection),
    .flash_therm(flash_therm),.flash_valid(flash_valid),
    .coarse_cmp_valid(coarse_valid),.coarse_cmp_ge(coarse_ge),.fine_cmp_valid(fine_valid),.fine_cmp_ge(fine_ge),
    .analog_phase(phase),.quiet_sample(quiet),.tp_clock(tp),.ra_az(az),.ra_amplify(amplify),
    .ref_precharge(precharge),.ref_accurate(accurate),.acquiring_mask(acq),.converting_mask(conv),
    .aux_charge_enable(aux),.hold_low_enable(hold_low),.coarse_compare_enable(compare_en),
    .coarse_trial(trials),.quantizer_dither(qdither),.acquisition_dither_rails(acq_rails),
    .fine_trial(fine_trial),.fine_compare_enable(fine_en),.coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),.flash_sample(flash_sample),
    .slice_sel(selected),.main_sw(main_sw),.sub_sw(sub_sw),.dither_sw(dith_sw),.sw_valid(sw_valid),
    .dout(dout),.dout_valid(dout_valid),.clip_low(clip_low),.clip_high(clip_high),
    .analog_ovf(analog_ovf),.acc_ovf(acc_ovf),.status_word(status),.dout_sample_id(id),.dout_flags(flags));
  logic [47:0] weights[18][71];
  logic [19:0] expected[600];
  logic [4:0] expected_flags[600];
  bit pending[600];
  logic signed [255:0] residue_total,residue_gain,residue_rails,residue_inj;
  bit residue_valid;
  int residue_id,outputs=0,expected_count=0,frame=-1,checks=0;
  logic [17:0] acquired_at_edge,used='0;
  int mode=0,cancelled=0;
  int clock_count=0,cfg_reads=0,trace_fd=0;
  string trace_path;
  always @(posedge clk) clock_count++;
  task automatic read_cfg(input logic [15:0] a,input logic [63:0] want);
    // Called with cfg_wr already deasserted. Read through the actual public
    // bus, allowing a complete cycle for the asynchronous read mux to settle.
    cfg_addr=a;@(negedge clk);
    if(cfg_rdata!==want)
      $fatal(1,"config readback mode=%0d address=%h got=%h expected=%h",mode,a,cfg_rdata,want);
    cfg_reads++;
  endtask
  task automatic write_cfg(input logic [15:0] a,input logic [63:0] d);
    @(negedge clk);cfg_addr=a;cfg_wdata=d;cfg_wr=1;
    @(negedge clk);cfg_wr=0;
  endtask
  task automatic configure(input int controls);
    for(int s=0;s<18;s++) begin
      write_cfg(16'(16'h2000+s*256),0);
      read_cfg(16'(16'h2000+s*256),64'(s));
      for(int u=0;u<71;u++) begin
        write_cfg(16'(u*8),64'(weights[s][u]));
        read_cfg(16'(u*8),64'(weights[s][u]));
      end
    end
    write_cfg(16'h1000,64'd0);read_cfg(16'h1000,64'd0);
    write_cfg(16'h1008,-64'sd4294967296);read_cfg(16'h1008,-64'sd4294967296);
    write_cfg(16'h1010,64'sd4294967296);read_cfg(16'h1010,64'sd4294967296);
    write_cfg(16'h1018,64'(controls));repeat(4) @(negedge clk);
    read_cfg(16'h1018,64'(controls));
    cfg_validate=1;@(negedge clk);cfg_validate=0;
    if(cfg_ready!==1'b1) $fatal(1,"structural configuration rejected");
  endtask
  task automatic queue_expected();
    logic signed [255:0] f,n,d,q;
    if(residue_valid && fine_valid) begin
      if(residue_id<0 || residue_id>=600) $fatal(1,"oracle ID outside scoreboard");
      if(pending[residue_id]) $fatal(1,"duplicate oracle ID");
      if(residue_gain<=0) $fatal(1,"oracle requires positive gain");
      f=(2*256'(fine_target)+1)*256'sd1048576-256'sd4294967296;
      n=(((f<<<30)-(residue_rails<<<32)-residue_total*residue_inj+(residue_gain<<<32))<<<20);
      d=residue_gain<<<33;q=n/d;
      if(n<0 && n%d!=0) q--;
      expected_flags[residue_id]=0;
      if(q<0) begin expected[residue_id]=0;expected_flags[residue_id][3]=1;end
      else if(q>=1048576) begin expected[residue_id]='1;expected_flags[residue_id][4]=1;end
      else expected[residue_id]=q[19:0];
      pending[residue_id]=1;expected_count++;
    end
  endtask
  task automatic check_resolved_follower();
    int resolved_prefix,equivalent_sum,expected_command;
    resolved_prefix=coarse_target;
    if(mode==2) resolved_prefix+=int'($signed(qdither[(frame+1)%2]));
    resolved_prefix=(resolved_prefix >> (9-int'(phase))) << (9-int'(phase));
    expected_command=resolved_prefix;
    if(mode==2) expected_command-=int'($signed(qdither[(frame+1)%2]));
    if(expected_command<0) expected_command=0;
    if(expected_command>511) expected_command=511;
    equivalent_sum=0;
    for(int s=0;s<18;s++) if(selected[s])
      equivalent_sum+=$countones(main_sw[s])*8+$countones(sub_sw[s]);
    if(sw_valid!==1'b1 || equivalent_sum!=8*expected_command)
      $fatal(1,"RDAC included unresolved trial phase=%0d command=%0d expected=%0d",phase,equivalent_sum,8*expected_command);
  endtask
  task automatic save_residue();
    logic signed [255:0] w;
    bit on_bit;
    int equivalent;
    residue_valid=(frame>0 && flash_valid && coarse_valid==3);
    residue_id=frame+1;residue_inj=256'(injection);
    residue_total=0;residue_gain=0;residue_rails=0;
    if(residue_valid) begin
      if(selected!==conv) $fatal(1,"RDAC physical selection did not follow acquired bank");
      used|=selected;
      for(int s=0;s<18;s++) if(selected[s]) begin
        equivalent=$countones(main_sw[s])*8+$countones(sub_sw[s]);
        // Bridge exchanges one sub-unit, with 4 positive/4 negative slices.
        if(equivalent<coarse_target-3 || equivalent>coarse_target+3)
          $fatal(1,"RDAC failed to follow resolved SAR result frame=%0d code=%0d equiv=%0d",frame,coarse_target,equivalent);
        for(int u=0;u<71;u++) begin
          w=256'(weights[s][u]);on_bit=(u<63)?main_sw[s][u]:sub_sw[s][u-63];
          residue_total+=w;residue_rails+=on_bit?-w:w;
          if(mode==1 && u>=67) residue_rails+=dith_sw[s][u-67]?w:-w;
          else residue_gain+=w;
        end
      end
    end
  endtask
  initial begin
    if(P_RECON_STAGES!=5 && P_RECON_STAGES!=7)
      $fatal(1,"mapped witness supports a matching P5 or P7 netlist only");
    if($value$plusargs("mapped_trace=%s",trace_path)) begin
      trace_fd=$fopen(trace_path,"w");
      if(trace_fd==0) $fatal(1,"cannot open mapped trace CSV");
      $fdisplay(trace_fd,"mode,frame,clock,sample_id,code,expected_code,flags,expected_flags,status");
    end
    $display("MAPPED_SCOPE functional_only public_ports_only recon_stages=%0d latency_checked=0 reset_release_ns=220",P_RECON_STAGES);
    residue_valid=0;
    for(int s=0;s<18;s++) for(int u=0;u<71;u++)
      weights[s][u]=(u<63?48'd67108864:48'd8388608)+48'(s*873+u*91);
    repeat(110) @(negedge clk);rst_n=1;
    for(mode=0;mode<3;mode++) begin
      configure(mode==1?7:mode==2?11:3);
      frame=-1;residue_valid=0;
      for(int n=0;n<600;n++) pending[n]=0;
      // First iteration observes phase0 immediately after configuration commit.
      for(int tick=0;tick<16*160;tick++) begin
        if($isunknown({phase,acq,conv,selected,quiet,tp,az,amplify,precharge,
                       accurate,flash_sample,aux,hold_low,sw_valid,dout_valid,
                       main_sw,sub_sw,dith_sw,qdither,trials,fine_trial}))
          $fatal(1,"unknown public control/pin mode=%0d tick=%0d",mode,tick);
        if(phase==0) begin
          frame++;acquired_at_edge=acq;
          coarse_target=16+(frame*37)%480;
          fine_target=512+(frame*43)%3072;
          injection=64'(frame*9173);
          flash_valid=(frame%37!=11);coarse_valid=(frame%37==12)?0:3;
          fine_valid=(frame%41!=13);
          flash_therm=7'((1<<((coarse_target+int'($signed(qdither[(frame+1)%2])))>>6))-1);
          queue_expected();
        end
        if(phase==2 && conv!==acquired_at_edge)
          $fatal(1,"converted a bank that did not acquire the preceding interval");
        if((acq&conv)!=0 || $countones(acq)!=8 || (frame>0 && $countones(conv)!=8))
          $fatal(1,"slice pool cardinality/disjointness");
        if(precharge && accurate || az && amplify) $fatal(1,"analog phase overlap");
        if((frame>0 && tp===quiet) || flash_sample!==quiet || aux!==acq || hold_low!==~acq)
          $fatal(1,"sampling/auxiliary boundary mismatch");
        if(phase>=3 && phase<=9 && frame>0 && flash_valid && coarse_valid==3)
          check_resolved_follower();
        if(phase==9) save_residue();
        if(dout_valid===1'b1) begin
          if($isunknown({id,dout,flags,clip_low,clip_high,analog_ovf,acc_ovf}) || id>=600)
            $fatal(1,"unknown result or ID outside scoreboard");
          if({clip_high,clip_low}!==flags[4:3] || analog_ovf!==1'b0 || acc_ovf!==1'b0)
            $fatal(1,"public clipping flags or unexpected sticky overflow");
          $display("MAPPED_ROW,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",mode,frame,clock_count,id,dout,expected[id],flags,expected_flags[id],status);
          if(trace_fd!=0) begin
            $fdisplay(trace_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",mode,frame,clock_count,id,dout,expected[id],flags,expected_flags[id],status);
            $fflush(trace_fd);
          end
          if(!pending[id] || dout!==expected[id] || flags!==expected_flags[id])
            $fatal(1,"calibrated context mode=%0d frame=%0d id=%0d got=%0d expected=%0d flags=%b expflags=%b pending=%b",mode,frame,id,dout,expected[id],flags,expected_flags[id],pending[id]);
          pending[id]=0;outputs++;
        end
        checks++;@(negedge clk);
      end
      // Explicit epoch cancellation drops any queued residue/result.
      for(int n=0;n<600;n++) if(pending[n]) begin
        $display("MAPPED_CANCEL,mode=%0d,sample_id=%0d,frame=%0d",mode,n,frame);
        cancelled++;
      end
      if(outputs+cancelled!=expected_count) $fatal(1,"missing/duplicate calibrated result");
      cfg_clear_valid=1;@(negedge clk);cfg_clear_valid=0;
      repeat(16) begin @(negedge clk);if(dout_valid!==1'b0 || cfg_ready!==1'b0) $fatal(1,"epoch leaked");end
    end
    if(used!='1 || outputs!=438 || checks!=7680 || cfg_reads!=3900)
      $fatal(1,"coverage mismatch slices=%h outputs=%0d checks=%0d config_reads=%0d",used,outputs,checks,cfg_reads);
    if(trace_fd!=0) $fclose(trace_fd);
    $display("STRUCTURAL_MAPPED_COMPLETE modes=3 outputs=%0d checks=%0d physical_slices=18 recon_stages=%0d config_reads=%0d cancelled=%0d expected=%0d latency_checked=0",outputs,checks,P_RECON_STAGES,cfg_reads,cancelled,expected_count);$finish;
  end
  initial begin #200000; $fatal(1,"mapped functional watchdog");end
endmodule
