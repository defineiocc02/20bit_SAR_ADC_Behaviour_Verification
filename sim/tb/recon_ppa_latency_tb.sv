`timescale 1ns/1ps
// Functional latency/initiation-interval contract for three synthesis profiles.
// This zero-delay RTL test does not measure area, power, or achieved frequency.
module recon_ppa_latency_tb;
  logic clk=0,rst_n=0,ready=0,start=0;
  always #1 clk=~clk;
  logic [19:0] code;
  logic [31:0] id;
  logic [0:0][2:0][47:0] weights;
  wire [2:0][19:0] dout;
  wire [2:0] valid,busy;
  wire [2:0][31:0] rid;
  wire [2:0][4:0] flags;
  int count[3];
  for(genvar g=0;g<3;g++) begin : profile
    recon_core #(.P_N_ACTIVE(1),.P_N_MAIN(2),.P_N_SUB(1),.P_N_SLICES(1),
      .P_ADC2_BITS(20),.P_STAGES(g+5),.P_USE_ROW_TOTALS(1)) d(
      .clk(clk),.rst_n(rst_n),.cfg_ready(ready),.start(start),.sample_id(id),.clr_ovf(1'b0),
      .sampling_mask_en(1'b0),.slice_id(5'd0),.main_on(2'd0),.sub_on(1'b0),.dither_rail(4'd0),
      .adc2_code(code),.inj_q(64'sd0),.w_rom(weights),.offset_q(64'sd0),
      .adc2_min_q(64'sd0),.adc2_max_q(64'sd8589934592),.dout(dout[g]),.dout_valid(valid[g]),
      .clip_low(),.clip_high(),.acc_ovf(),.gain_err(),.adc2_ovf(),.busy(busy[g]),
      .result_sample_id(rid[g]),.result_flags(flags[g]), .row_total(64'd1073741824));
  end
  initial begin
    weights[0][0]=48'd536870912;weights[0][1]=48'd268435456;weights[0][2]=48'd268435456;
    foreach(count[g]) count[g]=0;
    repeat(2) @(negedge clk);rst_n=1;ready=1;
    for(int tick=0;tick<16*32+16;tick++) begin
      start=tick%16==0 && tick<16*32;
      code=20'((tick/16)*7919);id=32'(tick/16+1);
      if(!start) begin code=20'(tick*1327);id='1;end
      if(start && busy) $fatal(1,"16-tick request rejected tick=%0d busy=%b",tick,busy);
      @(posedge clk);#0.001;
      for(int g=0;g<3;g++) begin
        automatic int n=(63+(g+5)-1)/(g+5);
        automatic int age=tick%16;
        automatic bit active_frame=tick<16*32;
        if(busy[g] !== (active_frame && age<n+1))
          $fatal(1,"busy release profile=%0d tick=%0d busy=%b",g+5,tick,busy[g]);
        if(valid[g] !== (active_frame && age==n+2))
          $fatal(1,"latency profile=%0d tick=%0d valid=%b expected_age=%0d",g+5,tick,valid[g],n+2);
        if(valid[g]) begin
          if(rid[g]!=tick/16+1 || dout[g]!=20'((tick/16)*7919) || flags[g]!=0)
            $fatal(1,"sample capture or result mismatch profile=%0d tick=%0d",g+5,tick);
          count[g]++;
        end
      end
      @(negedge clk);
    end
    for(int g=0;g<3;g++) begin
      if(count[g]!=32) $fatal(1,"sample count mismatch");
      $display("RECON_PPA_PROFILE_PASS stages=%0d samples=%0d busy_release=%0d latency=%0d initiation_interval=16",g+5,count[g],(63+g+4)/(g+5)+1,(63+g+4)/(g+5)+2);
    end
    $finish;
  end
endmodule
