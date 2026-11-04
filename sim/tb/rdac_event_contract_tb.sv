`timescale 1ns/1fs
// Live structural RDAC pin events for every 9-bit code in off/sampling/
// quantizer-dither modes. An independent prefix oracle checks each event;
// the CSV is also consumed by Python's resolved-event contract checker.
module rdac_event_contract_tb;
  logic clk=0, rst_n=0, enable=0, sampling_en=0, quantizer_en=0;
  always #0.78125 clk=~clk; // 640 MHz, or 40 MS/s at 16 clocks/sample
  logic [6:0] flash_therm=0;
  wire [1:0][8:0] trial;
  wire [1:0][7:0] qdither;
  wire [3:0] phase, acq_rails;
  wire [11:0] fine_trial;
  wire [17:0] selected;
  wire [17:0][62:0] main_sw;
  wire [17:0][7:0] sub_sw;
  wire sw_valid;
  integer target=0, frame=-1, mode=0, current_dither=0, quantized_target=0;
  integer expected, shift, sum_code, events, fd, adjustment;
  realtime sample_time_ns;
  wire [1:0] coarse_ge = {
    target + int'($signed(qdither[1])) >= int'(trial[1]),
    target + int'($signed(qdither[0])) >= int'(trial[0])
  };
  string outdir;
  sar_structural_ctrl dut (
    .clk(clk),.rst_n(rst_n),.enable(enable),.dem_en(1'b1),.bridge_en(1'b1),
    .sampling_en(sampling_en),.quantizer_en(quantizer_en),
    .flash_therm(flash_therm),.flash_valid(1'b1),
    .coarse_cmp_valid(2'b11),.coarse_cmp_ge(coarse_ge),
    .fine_cmp_valid(1'b1),.fine_cmp_ge(2048>=int'(fine_trial)),
    .injection_q(64'sd0),.analog_bad(1'b0),.recon_busy(1'b0),
    .phase(phase),.quiet_sample(),.tp_clock(),.ra_az(),.ra_amplify(),
    .ref_precharge(),.ref_accurate(),.acquiring_mask(),.converting_mask(),
    .aux_charge_enable(),.hold_low_enable(),.coarse_compare_enable(),
    .coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),
    .coarse_trial(trial),.quantizer_dither(qdither),.acquisition_dither_rails(acq_rails),
    .fine_trial(fine_trial),.fine_compare_enable(),.flash_sample(),
    .slice_sel(selected),.main_sw(main_sw),.sub_sw(sub_sw),.dither_sw(),.sw_valid(sw_valid),
    .recon_start(),.context_id(),.context_coarse(),.context_sid(),.context_dither(),
    .context_slices(),.context_main(),.context_sub(),.context_rails(),.context_sampling(),
    .context_analog_bad(),.context_injection(),.fine_code(),.error_code()
  );
  initial begin
    if (!$value$plusargs("outdir=%s",outdir)) outdir=".";
    fd=$fopen({outdir,"/rdac_events.csv"},"w");
    if(fd==0) $fatal(1,"cannot open RDAC event capture");
    $fwrite(fd,"mode,sample_id,target,coarse,dither,phase,elapsed_ns,command,sid\n");
    repeat(3) @(negedge clk); rst_n=1;
    for(mode=0;mode<3;mode++) begin
      enable=0; repeat(2) @(negedge clk);
      sampling_en=(mode==1);quantizer_en=(mode==2);enable=1;frame=-1;events=0;
      for(int tick=0;tick<16*514;tick++) begin
        if(phase==0) begin
          frame++;
          sample_time_ns=$realtime;
          target=(frame+511)%512;
          if(mode==1) current_dither=$countones(acq_rails)-2;
          else if(mode==2) current_dither=int'($signed(qdither[!dut.bank]));
          else current_dither=0;
          quantized_target=target+(mode==2 ? current_dither : 0);
          if(quantized_target<0) quantized_target=0;
          if(quantized_target>511) quantized_target=511;
          flash_therm=7'((1 << (quantized_target>>6))-1);
        end
        if(sw_valid && frame>0 && frame<=512) begin
          if(phase<3 || phase>9) $fatal(1,"RDAC event outside phase contract");
          shift=9-int'(phase);
          expected=(quantized_target>>shift)<<shift;
          adjustment=mode==1 ? current_dither : (mode==2 ? -current_dither : 0);
          expected+=adjustment;
          if(expected<0)expected=0;
          if(expected>511)expected=511;
          sum_code=0;
          for(int s=0;s<18;s++) if(selected[s])
            sum_code+=$countones(main_sw[s])*8+$countones(sub_sw[s]);
          if($countones(selected)!=8 || sum_code!=8*expected)
            $fatal(1,"RDAC mismatch mode=%0d frame=%0d phase=%0d actual=%0d expected=%0d",mode,frame,phase,sum_code/8,expected);
          $fwrite(fd,"%0d,%0d,%0d,%0d,%0d,%0d,%.5f,%0d,%0d\n",mode,dut.current_id,target,quantized_target,current_dither,phase,$realtime-sample_time_ns,sum_code/8,dut.sid);
          events++;
        end
        @(negedge clk);
      end
      if(events!=512*7)$fatal(1,"event coverage mode=%0d events=%0d",mode,events);
    end
    $fclose(fd);
    $display("RDAC_EVENT_CONTRACT_COMPLETE modes=3 codes=512 events=%0d",3*512*7);
    $finish;
  end
  initial begin #100000; $fatal(1,"RDAC event watchdog"); end
endmodule
