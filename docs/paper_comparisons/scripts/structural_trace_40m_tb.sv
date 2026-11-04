`timescale 1ns/1fs
// Real RTL timing at an ideal 640MHz clock. This is functional timing only.
module structural_trace_40m_tb;
 logic clk=0,rst_n=0,enable=0,sampling_en=0,quantizer_en=0;
 always #0.78125 clk=~clk;
 wire [3:0] phase;wire quiet,tp,az,amp,pre,accurate;
 wire [17:0] acq,conv,sel,aux,hold;
 wire [1:0] compare_en,acquire_en;
 wire [1:0][8:0] trials;wire [1:0][7:0] qdither;
 wire [11:0] fine_trial,fine_code;wire fine_en,swvalid,recon;
 wire [17:0][62:0] main_sw;wire [17:0][7:0] sub_sw;wire [17:0][3:0] dith_sw;
 wire [31:0] context_id,err;wire [1:0] coarse_ge;
 int target=173;
 assign coarse_ge[0]=(target+int'($signed(qdither[0])))>=int'(trials[0]);
 assign coarse_ge[1]=(target+int'($signed(qdither[1])))>=int'(trials[1]);
 sar_structural_ctrl dut(.clk(clk),.rst_n(rst_n),.enable(enable),.dem_en(1'b1),.bridge_en(1'b1),
  .sampling_en(sampling_en),.quantizer_en(quantizer_en),.flash_therm(7'd3),.flash_valid(1'b1),
  .coarse_cmp_valid(2'b11),.coarse_cmp_ge(coarse_ge),.fine_cmp_valid(1'b1),.fine_cmp_ge(2048>=int'(fine_trial)),
  .injection_q(64'sd0),.analog_bad(1'b0),.recon_busy(1'b0),.phase(phase),.quiet_sample(quiet),.tp_clock(tp),
  .ra_az(az),.ra_amplify(amp),.ref_precharge(pre),.ref_accurate(accurate),.acquiring_mask(acq),.converting_mask(conv),
  .aux_charge_enable(aux),.hold_low_enable(hold),.coarse_compare_enable(compare_en),.coarse_acquire_enable(acquire_en),
  .flash_acquire_enable(),.fine_acquire_enable(),.coarse_trial(trials),.quantizer_dither(qdither),.acquisition_dither_rails(),
  .fine_trial(fine_trial),.fine_compare_enable(fine_en),.flash_sample(),.slice_sel(sel),.main_sw(main_sw),.sub_sw(sub_sw),
  .dither_sw(dith_sw),.sw_valid(swvalid),.recon_start(recon),.context_id(context_id),.context_slices(),.context_main(),
  .context_sub(),.context_rails(),.context_sampling(),.context_analog_bad(),.context_injection(),
  .context_coarse(),.context_sid(),.context_dither(),.fine_code(fine_code),.error_code(err));
 integer fd,tick,mode,s,nominal,checks=0,reopen_ticks=0,recon_count=0,events=0;
 logic [17:0] sampled_mask;
 string outdir,vcdf;
 initial begin
  if(!$value$plusargs("outdir=%s",outdir))outdir=".";
  vcdf={outdir,"/structural_trace.vcd"};$dumpfile(vcdf);$dumpvars(0,structural_trace_40m_tb);
  fd=$fopen({outdir,"/structural_trace.csv"},"w");
  $fwrite(fd,"mode,tick,time_ns,phase,tp,quiet,acquiring_mask,converting_mask,current_id,conversion_valid,bank,coarse_trial,coarse_resolved,coarse_enable,rdac_valid,rdac_nominal_command,fine_trial,fine_enable,fine_code,context_id,context_valid,recon_start,az,amplify,precharge,accurate,coarse_acquire,flash_acquire,fine_acquire,error,direct_tp_and_en_reopen,sid,selected_mask,main_sw_hex,sub_sw_hex,dither_sw_hex\n");
  repeat(2)@(negedge clk);rst_n=1;
  for(mode=0;mode<3;mode++)begin
   enable=0;@(negedge clk);sampling_en=(mode==1);quantizer_en=(mode==2);enable=1;sampled_mask=0;
   for(tick=0;tick<16*8;tick++)begin
    @(negedge clk);
    nominal=0;for(s=0;s<18;s++)if(sel[s])nominal+=$countones(main_sw[s])*8+$countones(sub_sw[s]);
    if(nominal%8!=0)$fatal(1,"nominal bridge conservation");
    if(quiet)sampled_mask=acq;
    if(phase==1 && tp && |(sampled_mask&acq))reopen_ticks++;
    if((acq&conv)!=0 || $countones(acq)!=8 || (dut.conv_valid && $countones(conv)!=8))$fatal(1,"pool disjoint/cardinality");
    if(az && amp || pre && accurate)$fatal(1,"analog control overlap");
    if(recon)recon_count++;
    if(swvalid)events++;
    $fwrite(fd,"%0d,%0d,%.3f,%0d,%0d,%0d,%05h,%05h,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%05h,%h,%h,%h\n",mode,tick,$realtime,phase,tp,quiet,acq,conv,dut.current_id,dut.conv_valid,dut.bank,trials[dut.bank],dut.resolved[dut.bank],compare_en[dut.bank],swvalid,nominal/8,fine_trial,fine_en,fine_code,context_id,dut.context_valid,recon,az,amp,pre,accurate,acquire_en,dut.flash_acquire_enable,dut.fine_acquire_enable,err,(phase==1 && tp && |(sampled_mask&acq)),dut.sid,sel,main_sw,sub_sw,dith_sw);
    checks++;
   end
  end
  $fclose(fd);
  if(checks!=384 || recon_count<12 || events<100)$fatal(1,"insufficient trace coverage");
  $display("STRUCTURAL_TRACE_40M_COMPLETE checks=%0d recon_starts=%0d rdac_updates=%0d direct_gate_reopen_ticks=%0d",checks,recon_count,events,reopen_ticks);$finish;
 end
 initial begin #10000;$fatal(1,"trace watchdog");end
endmodule
