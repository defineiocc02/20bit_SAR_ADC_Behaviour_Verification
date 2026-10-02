`timescale 1ns/1ps
module structural_protocol_tb;
  logic clk=0, rst_n=0, enable=0;
  always #1 clk=~clk;
  logic flash_valid=1;
  logic [1:0] cv=3;
  logic fv=1;
  wire [1:0] cg;
  wire fg;
  wire [3:0] phase;
  wire [1:0][8:0] trial;
  wire [11:0] ft,fc;
  wire launch;
  wire [31:0] id,err;
  wire [7:0][4:0] ids;
  wire [7:0][62:0] cm;
  wire [7:0][7:0] cs;
  wire [3:0] cr;
  wire csm,cab;
  wire signed [63:0] ci;
  wire [17:0] acq,conv,sel;
  wire [17:0][62:0] ms;
  wire [17:0][7:0] ss;
  wire [17:0][3:0] ds;
  wire sv;
  wire flash_acq,fine_acq,fine_cmp_en;
  wire [1:0] coarse_cmp_en;
  realtime last_clk_edge=0;
  bit monitor_outputs=0;
  int perturbations=0;
  int target=224, fine_target=2048, frame=-1, drop_c=-1,drop_f=-1;
  logic signed [63:0] injection;
  bit expect_prev=0,expect_curr=0;
  int launches=0,drops=0,checks=0;
  assign cg[0]=target>=int'(trial[0]);
  assign cg[1]=target>=int'(trial[1]);
  assign fg=fine_target>=int'(ft);
  sar_structural_ctrl d(.clk(clk),.rst_n(rst_n),.enable(enable),
    .dem_en(1'b1),.bridge_en(1'b1),.sampling_en(1'b0),.quantizer_en(1'b0),
    .flash_therm(7'b0000111),.flash_valid(flash_valid),.coarse_cmp_valid(cv),.coarse_cmp_ge(cg),
    .fine_cmp_valid(fv),.fine_cmp_ge(fg),.injection_q(injection),.analog_bad(1'b0),.recon_busy(1'b0),
    .phase(phase),.quiet_sample(),.tp_clock(),.ra_az(),.ra_amplify(),.ref_precharge(),.ref_accurate(),
    .acquiring_mask(acq),.converting_mask(conv),.aux_charge_enable(),.hold_low_enable(),
    .coarse_compare_enable(coarse_cmp_en),.coarse_acquire_enable(),.flash_acquire_enable(flash_acq),.fine_acquire_enable(fine_acq),
    .coarse_trial(trial),.quantizer_dither(),.acquisition_dither_rails(),.fine_trial(ft),.fine_compare_enable(fine_cmp_en),
    .flash_sample(),.slice_sel(sel),.main_sw(ms),.sub_sw(ss),.dither_sw(ds),.sw_valid(sv),
    .recon_start(launch),.context_id(id),.context_slices(ids),.context_main(cm),.context_sub(cs),
    .context_rails(cr),.context_sampling(csm),.context_analog_bad(cab),.context_injection(ci),
    .fine_code(fc),.error_code(err));
  // A zero-delay RTL simulation cannot establish physical glitch freedom.
  // Deliberately perturb only the phase bus between clocks to test isolation
  // of analog-facing enables from transient decoder values. This is a
  // structural fault injection, not a gate-delay or silicon timing model.
  always @(posedge clk) begin
    last_clk_edge=$realtime;
    monitor_outputs=rst_n && enable;
  end
  always @(flash_acq or fine_acq or coarse_cmp_en or fine_cmp_en) begin
    if(monitor_outputs && rst_n && enable && $realtime!=last_clk_edge)
      $fatal(1,"analog enable changed between clock edges at %0t",$realtime);
  end
  initial begin
    repeat(2) @(negedge clk);rst_n=1;enable=1;
    for(int tick=0;tick<16*120;tick++) begin
      if(phase==0) begin
        frame++;
        expect_prev=expect_curr;
        target=192+(frame*17)%64;
        fine_target=(frame*173)%4096;
        injection=frame*9173;
        // Every isolated dropped decision is followed by a healthy frame.
        // Also lose a Flash seed, then exercise late comparator activity.
        drop_c=(frame%4==0)?2+((frame/4)%6):-1;
        drop_f=(frame%4==2)?2+((frame/4)%12):-1;
        flash_valid=frame%29!=5;
        expect_curr=frame>0 && drop_c<0 && flash_valid;
      end
      if(tick>0 && (flash_acq!==(phase>=2 || phase==0) || fine_acq!==(phase>=14 || phase==0)))
        $fatal(1,"registered acquire enable schedule mismatch phase=%0d",phase);
      cv=(int'(phase)==drop_c)?0:3;
      fv=int'(phase)!=drop_f;
      if(phase==15) begin
        if(launch !== (expect_prev && drop_f<0))
          $fatal(1,"launch mismatch frame=%0d prior=%0d drop_f=%0d launch=%0d",frame,expect_prev,drop_f,launch);
        if(launch) begin
          if(id!=frame || fc!=fine_target || ci!=(frame-1)*9173)
            $fatal(1,"context mismatch frame=%0d id=%0d fc=%0d expected=%0d inj=%0d",frame,id,fc,fine_target,ci);
          launches++;
        end else drops++;
      end
      // These values can occur transiently during a multi-bit counter carry.
      // Restore the phase before any active clock edge, so the functional
      // transaction oracle remains independent of this isolated perturbation.
      if(frame==7 && phase==3) begin
        force d.u_phase.phase=4'd1; #0.1;
        force d.u_phase.phase=4'd3; #0.1;
        release d.u_phase.phase;perturbations++;
      end
      if(frame==7 && phase==7) begin
        force d.u_phase.phase=4'd14; #0.1;
        force d.u_phase.phase=4'd7; #0.1;
        release d.u_phase.phase;perturbations++;
      end
      checks++;
      @(negedge clk);
    end
    enable=0;repeat(2) @(negedge clk);
    if(launch || sv || flash_acq || fine_acq || coarse_cmp_en || fine_cmp_en)
      $fatal(1,"disabled transaction/analog enable leak");
    rst_n=0;repeat(2) @(negedge clk);
    if(flash_acq || fine_acq || coarse_cmp_en || fine_cmp_en)
      $fatal(1,"reset analog enable/cancel leak");
    if(perturbations!=2) $fatal(1,"missing phase-decode negative-control coverage");
    $display("STRUCTURAL_PROTOCOL_COMPLETE frames=%0d launches=%0d expected_drops=%0d checks=%0d decoder_perturbations=%0d",frame+1,launches,drops,checks,perturbations);
    $finish;
  end
  initial begin #10000; $fatal(1,"watchdog");end
endmodule
