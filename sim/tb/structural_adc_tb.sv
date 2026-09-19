`timescale 1ns/1ps
// Pin-driven structural integration. Ideal comparator models respond to the
// actual SAR trial pins. Oracle uses physical RDAC pins and independently
// retained coefficient fixtures; it cannot see reconstruction internal sums.
module structural_adc_tb;
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
  assign coarse_ge[0]=coarse_target>=int'(trials[0]);
  assign coarse_ge[1]=coarse_target>=int'(trials[1]);
  assign fine_ge=fine_target>=int'(fine_trial);
  sar20_digital_core dut(
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
  task automatic write_cfg(input logic [15:0] a,input logic [63:0] d);
    @(negedge clk);cfg_addr=a;cfg_wdata=d;cfg_wr=1;
    @(negedge clk);cfg_wr=0;
  endtask
  task automatic configure(input int controls);
    for(int s=0;s<18;s++) begin
      write_cfg(16'(16'h2000+s*256),0);
      for(int u=0;u<71;u++) write_cfg(16'(u*8),64'(weights[s][u]));
    end
    write_cfg(16'h1000,64'd0);
    write_cfg(16'h1008,-64'sd4294967296);write_cfg(16'h1010,64'sd4294967296);
    write_cfg(16'h1018,64'(controls));repeat(4) @(negedge clk);
    cfg_validate=1;@(negedge clk);cfg_validate=0;
    if(!cfg_ready) $fatal(1,"structural configuration rejected");
  endtask
  task automatic queue_expected();
    logic signed [255:0] f,n,d,q;
    if(residue_valid && fine_valid) begin
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
    resolved_prefix=(coarse_target >> (9-int'(phase))) << (9-int'(phase));
    expected_command=resolved_prefix;
    if(mode==2) expected_command-=int'($signed(qdither[(frame+1)%2]));
    if(expected_command<0) expected_command=0;
    if(expected_command>511) expected_command=511;
    equivalent_sum=0;
    for(int s=0;s<18;s++) if(selected[s])
      equivalent_sum+=$countones(main_sw[s])*8+$countones(sub_sw[s]);
    if(!sw_valid || equivalent_sum!=8*expected_command)
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
    residue_valid=0;
    for(int s=0;s<18;s++) for(int u=0;u<71;u++)
      weights[s][u]=(u<63?48'd67108864:48'd8388608)+48'(s*873+u*91);
    repeat(2) @(negedge clk);rst_n=1;
    for(mode=0;mode<3;mode++) begin
      configure(mode==1?7:mode==2?11:3);
      frame=-1;residue_valid=0;
      for(int n=0;n<600;n++) pending[n]=0;
      // First iteration observes phase0 immediately after configuration commit.
      for(int tick=0;tick<16*160;tick++) begin
        if(phase==0) begin
          frame++;acquired_at_edge=acq;
          coarse_target=16+(frame*37)%480;
          fine_target=512+(frame*43)%3072;
          injection=64'(frame*9173);
          flash_valid=(frame%37!=11);coarse_valid=(frame%37==12)?0:3;
          fine_valid=(frame%41!=13);
          flash_therm=7'((1<<(coarse_target>>6))-1);
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
        if(dout_valid) begin
          if(!pending[id] || dout!==expected[id] || flags!==expected_flags[id])
            $fatal(1,"calibrated context mode=%0d frame=%0d id=%0d got=%0d expected=%0d flags=%b expflags=%b pending=%b",mode,frame,id,dout,expected[id],flags,expected_flags[id],pending[id]);
          pending[id]=0;outputs++;
        end
        checks++;@(negedge clk);
      end
      // Explicit epoch cancellation drops any queued residue/result.
      for(int n=0;n<600;n++) if(pending[n]) cancelled++;
      if(outputs+cancelled!=expected_count) $fatal(1,"missing/duplicate calibrated result");
      cfg_clear_valid=1;@(negedge clk);cfg_clear_valid=0;
      repeat(16) begin @(negedge clk);if(dout_valid || cfg_ready) $fatal(1,"epoch leaked");end
    end
    if(used!='1 || outputs<400) $fatal(1,"insufficient physical slice/result coverage");
    $display("STRUCTURAL_ADC_COMPLETE modes=3 outputs=%0d checks=%0d physical_slices=18",outputs,checks);$finish;
  end
  initial begin #100000; $fatal(1,"structural watchdog");end
endmodule
