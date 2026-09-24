`timescale 1ns/1ps
// End-to-end mismatch recovery: synthesize analog residue from the true cell
// weights and a known input code, quantize it independently, and require the
// corrected RTL output to stay within the backend quantization uncertainty.
module calibration_recovery_tb;
  logic clk=0,rst_n=0,start=0;
  always #1 clk=~clk;
  logic [7:0][4:0] ids, next_ids;
  logic [7:0][6:0] main_on, next_main;
  logic [7:0][0:0] sub_on, next_sub;
  logic [17:0][7:0][47:0] weights,nominal;
  logic [11:0] fine;
  logic [19:0] out_cal,out_raw;
  wire valid_cal,valid_raw;
  wire [4:0] flags_cal,flags_raw;
  // Eight equal cells per slice; nominal total = 32*2^30 (effective C/Cf, RA gain32). Main/sub here are
  // physical coefficient test fixtures, not the paper's capacitor geometry.
  recon_core #(.P_N_ACTIVE(8),.P_N_MAIN(7),.P_N_SUB(1),.P_N_SLICES(18),.P_DIT_N(1),.P_DIT_END(8)) cal(
    .clk(clk),.rst_n(rst_n),.cfg_ready(1'b1),.start(start),.sample_id(32'd0),.clr_ovf(1'b0),
    .sampling_mask_en(1'b0),.slice_id(ids),.main_on(main_on),.sub_on(sub_on),.dither_rail(1'b0),
    .adc2_code(fine),.inj_q(64'sd0),.w_rom(weights),.offset_q(64'sd0),
    .adc2_min_q(-64'sd4294967296),.adc2_max_q(64'sd4294967296),
    .dout(out_cal),.dout_valid(valid_cal),.clip_low(),.clip_high(),.acc_ovf(),.gain_err(),.adc2_ovf(),
    .busy(),.result_sample_id(),.result_flags(flags_cal));
  recon_core #(.P_N_ACTIVE(8),.P_N_MAIN(7),.P_N_SUB(1),.P_N_SLICES(18),.P_DIT_N(1),.P_DIT_END(8)) raw(
    .clk(clk),.rst_n(rst_n),.cfg_ready(1'b1),.start(start),.sample_id(32'd0),.clr_ovf(1'b0),
    .sampling_mask_en(1'b0),.slice_id(ids),.main_on(main_on),.sub_on(sub_on),.dither_rail(1'b0),
    .adc2_code(fine),.inj_q(64'sd0),.w_rom(nominal),.offset_q(64'sd0),
    .adc2_min_q(-64'sd4294967296),.adc2_max_q(64'sd4294967296),
    .dout(out_raw),.dout_valid(valid_raw),.clip_low(),.clip_high(),.acc_ovf(),.gain_err(),.adc2_ovf(),
    .busy(),.result_sample_id(),.result_flags(flags_raw));
  initial begin
    logic signed [255:0] total,rail_sum,w,fexact,c,v;
    int target,coarse,ticks,cal_error,raw_error,max_cal=0,max_raw=0;
    for(int s=0;s<18;s++) for(int u=0;u<8;u++) begin
      nominal[s][u]=48'd536870912;
      weights[s][u]=48'(536870912 + (((s*17+u*13)%31)-15)*256000);
    end
    repeat(2) @(negedge clk);rst_n=1;
    for(int n=1;n<=2048;n++) begin
      coarse=8+n%49;target=coarse*16384+int'((n*117)%1024)-512;
      total=0;rail_sum=0;
      for(int a=0;a<8;a++) begin
        next_ids[a]=5'((n+7*a)%18);
        for(int u=0;u<8;u++) begin
          // Rotate the unit selection independently of physical slice choice.
          if(u<7) next_main[a][u]=((a*8+u+n)%64)<coarse;
          else next_sub[a][0]=((a*8+u+n)%64)<coarse;
          w=256'(weights[next_ids[a]][u]);total+=w;
          rail_sum+=(((a*8+u+n)%64)<coarse)?-w:w;
        end
      end
      // Solve the analog transfer equation for the midpoint of target code.
      // v is normalized input in Q32; fexact is the ADC2 voltage after RA gain32.
      v=(2*256'(target)+1)*256'sd4096-256'sd4294967296;
      fexact=(total*v+(rail_sum<<<32)) / 256'sd1073741824;
      c=(fexact+256'sd4294967296)/256'sd2097152;
      if(c<0 || c>4095) $fatal(1,"residue fixture exceeded ADC2 range");
      // Publish packed transaction fields atomically. Older simulator versions
      // can fail to propagate coroutine-local element writes across input ports.
      ids=next_ids;main_on=next_main;sub_on=next_sub;
      fine=c[11:0];start=1;@(negedge clk);start=0;ticks=0;
      while(!valid_cal) begin @(negedge clk);ticks++;if(ticks>12) $fatal(1,"calibration recovery timeout");end
      if(!valid_raw || flags_cal!=0 || flags_raw!=0)
        $fatal(1,"calibration recovery flag/latency n=%0d raw_valid=%b cal_flags=%b raw_flags=%b cal_gain=%0d raw_gain=%0d fine=%0d cal_word=%0d raw_word=%0d ids=%h port_ids=%h invalid=%b",
          n,valid_raw,flags_cal,flags_raw,cal.gain_s,raw.gain_s,fine,out_cal,out_raw,ids,cal.slice_id,cal.invalid_slice);
      cal_error=int'(out_cal)-target;if(cal_error<0) cal_error=-cal_error;
      raw_error=int'(out_raw)-target;if(raw_error<0) raw_error=-raw_error;
      if(cal_error>max_cal) max_cal=cal_error;if(raw_error>max_raw) max_raw=raw_error;
      // ±4 counts from ideal ADC2 quantization + 1 count integer boundary margin.
      if(cal_error>5) $fatal(1,"mismatch correction failed input=%0d corrected=%0d raw=%0d",target,out_cal,out_raw);
      @(negedge clk);
    end
    if(max_raw<100 || max_cal*20>=max_raw) $fatal(1,"test did not discriminate calibrated/nominal coefficients");
    $display("CALIBRATION_RECOVERY_COMPLETE samples=2048 max_calibrated_error=%0d max_uncalibrated_error=%0d",max_cal,max_raw);$finish;
  end
  initial begin #100000; $fatal(1,"recovery watchdog");end
endmodule
