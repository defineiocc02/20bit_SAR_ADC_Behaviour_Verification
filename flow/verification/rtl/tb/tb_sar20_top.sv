// V3 top-level bring-up bench for sar20_digital_core.
//
// Scope, stated honestly: this bench proves the *digital top elaborates with the
// generated source bindings and reaches its documented idle state after reset*.
// It does NOT yet drive comparator decisions, config writes or samples, so it is
// **not** a V3 pass. The campaign report marks it ELABORATED_ONLY.
//
// Why it exists: before this, the claim "V3 is NOT_RUN" had no evidence at all —
// nobody had proved the top even builds. That is a different, weaker statement
// than "the top behaves correctly", and the two must not be conflated.
`timescale 1ns/1ps
`include "rtl_params.vh"

module tb_sar20_top;
  logic clk = 1'b0;
  logic rst_n = 1'b0;

  logic        source_control_valid;
  logic [5:0]  source_control_word;
  logic [0:0]  source_control_state;
  logic        source_control_ready;
  logic        acquisition_control_valid;
  logic [5:0]  acquisition_control_word;
  logic [0:0]  acquisition_control_state;
  logic signed [63:0] acquisition_sadc_shift_q;
  logic signed [63:0] acquisition_rdac_shift_q;

  logic [6:0]  flash_therm;
  logic        flash_valid;
  logic [1:0]  coarse_cmp_valid, coarse_cmp_ge;
  logic        fine_cmp_valid, fine_cmp_ge;

  logic [3:0]  analog_phase;
  logic        quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate;
  logic [17:0] acquiring_mask, converting_mask, aux_charge_enable, hold_low_enable;
  logic [1:0]  coarse_compare_enable, coarse_acquire_enable;
  logic        flash_acquire_enable, fine_acquire_enable;
  logic [1:0][8:0] coarse_trial;
  logic [11:0] fine_trial;
  logic        fine_compare_enable, flash_sample;

  logic        cfg_wr, cfg_validate, cfg_clear_valid;
  logic [15:0] cfg_addr;
  logic [63:0] cfg_wdata, cfg_rdata;
  logic        cfg_ready;

  logic        ra_sat, rdac_ovf, adc2_over;
  logic [17:0] slice_sel;
  logic [17:0][62:0] main_sw;
  logic [17:0][7:0]  sub_sw;
  logic        sw_valid;
  logic [19:0] dout;
  logic        dout_valid, clip_low, clip_high, analog_ovf, acc_ovf, conversion_fault;
  logic [31:0] status_word, dout_sample_id;
  logic [4:0]  dout_flags;
  logic        cal_obs_valid;
  logic [int'(CAL_OBS_BITS)-1:0] cal_obs_data;
  logic        dout_analog_bad;

  integer fd, cycle;

  always #5 clk = ~clk;

  sar20_digital_core #(
      .P_RECON_STAGES(int'(RECON_UNROLL)),
      .P_CAL_OBSERVE(1'b0)
  ) dut (
      .clk(clk), .rst_n(rst_n),
      .source_control_valid(source_control_valid),
      .source_control_word(source_control_word),
      .source_control_state(source_control_state),
      .source_control_ready(source_control_ready),
      .acquisition_control_valid(acquisition_control_valid),
      .acquisition_control_word(acquisition_control_word),
      .acquisition_control_state(acquisition_control_state),
      .acquisition_sadc_shift_q(acquisition_sadc_shift_q),
      .acquisition_rdac_shift_q(acquisition_rdac_shift_q),
      .flash_therm(flash_therm), .flash_valid(flash_valid),
      .coarse_cmp_valid(coarse_cmp_valid), .coarse_cmp_ge(coarse_cmp_ge),
      .fine_cmp_valid(fine_cmp_valid), .fine_cmp_ge(fine_cmp_ge),
      .analog_phase(analog_phase), .quiet_sample(quiet_sample), .tp_clock(tp_clock),
      .ra_az(ra_az), .ra_amplify(ra_amplify),
      .ref_precharge(ref_precharge), .ref_accurate(ref_accurate),
      .acquiring_mask(acquiring_mask), .converting_mask(converting_mask),
      .aux_charge_enable(aux_charge_enable), .hold_low_enable(hold_low_enable),
      .coarse_compare_enable(coarse_compare_enable),
      .coarse_acquire_enable(coarse_acquire_enable),
      .flash_acquire_enable(flash_acquire_enable), .fine_acquire_enable(fine_acquire_enable),
      .coarse_trial(coarse_trial), .fine_trial(fine_trial),
      .fine_compare_enable(fine_compare_enable), .flash_sample(flash_sample),
      .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata), .cfg_rdata(cfg_rdata),
      .cfg_validate(cfg_validate), .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready),
      .ra_sat(ra_sat), .rdac_ovf(rdac_ovf), .adc2_over(adc2_over),
      .slice_sel(slice_sel), .main_sw(main_sw), .sub_sw(sub_sw), .sw_valid(sw_valid),
      .dout(dout), .dout_valid(dout_valid),
      .clip_low(clip_low), .clip_high(clip_high),
      .analog_ovf(analog_ovf), .acc_ovf(acc_ovf),
      .conversion_fault(conversion_fault),
      .status_word(status_word), .dout_sample_id(dout_sample_id), .dout_flags(dout_flags),
      .cal_obs_valid(cal_obs_valid), .cal_obs_data(cal_obs_data),
      .dout_analog_bad(dout_analog_bad)
  );

  initial begin
    source_control_valid = 1'b0;
    source_control_word  = '0;
    source_control_state = '0;
    flash_therm = '0;
    flash_valid = 1'b0;
    coarse_cmp_valid = '0;
    coarse_cmp_ge = '0;
    fine_cmp_valid = 1'b0;
    fine_cmp_ge = 1'b0;
    cfg_wr = 1'b0;
    cfg_addr = '0;
    cfg_wdata = '0;
    cfg_validate = 1'b0;
    cfg_clear_valid = 1'b0;
    ra_sat = 1'b0;
    rdac_ovf = 1'b0;
    adc2_over = 1'b0;

    fd = $fopen("sar20_top.out", "w");
    if (fd == 0) $fatal(1, "cannot open sar20_top.out");

    repeat (4) @(negedge clk);
    rst_n = 1'b1;
    for (cycle = 0; cycle < 32; cycle = cycle + 1) begin
      @(negedge clk);
      $fdisplay(fd, "%0d %0b %0b %0b %0b %0h %0h %0h",
                cycle, cfg_ready, source_control_ready,
                acquisition_control_valid, dout_valid,
                status_word, acquiring_mask, converting_mask);
    end
    $fclose(fd);
    $display("SAR20_TOP_IDLE cfg_ready=%0b ctrl_ready=%0b acq_valid=%0b dout_valid=%0b status=%0h",
             cfg_ready, source_control_ready, acquisition_control_valid,
             dout_valid, status_word);
    $finish;
  end
endmodule
