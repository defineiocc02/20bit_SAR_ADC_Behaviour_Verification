`timescale 1ns/1ps
// Exercise the deliverable assembly itself with ideal synchronous comparators.
module sar20_system_top_tb;
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;
  logic cfg_wr = 0, cfg_validate = 0, cfg_clear = 0;
  logic ra_sat_fault = 0, rdac_fault = 0, adc2_fault = 0, fault_test = 0, dsp_restart = 0;
  logic [15:0] cfg_addr = 0;
  logic [63:0] cfg_wdata = 0;
  wire cfg_ready;
  wire [1:0][8:0] coarse_trial;
  wire [11:0] fine_trial;
  int coarse_target = 256, fine_target = 2048;
  wire [1:0] coarse_ge = {coarse_target >= int'(coarse_trial[1]),
                          coarse_target >= int'(coarse_trial[0])};
  wire fine_ge = fine_target >= int'(fine_trial);
  wire [19:0] sar_dout, dsp_dout;
  wire sar_valid, dsp_valid, accepted, rejected, stream_fault, sar_analog_ovf;
  wire [31:0] sar_id, accepted_id;
  wire [4:0] sar_flags;

  sar20_system_top #(.DSP_FIFO_DEPTH(32)) dut (
    .clk(clk), .rst_n(rst_n), .flash_therm(7'd0), .flash_valid(1'b1),
    .coarse_cmp_valid(2'b11), .coarse_cmp_ge(coarse_ge),
    .fine_cmp_valid(1'b1), .fine_cmp_ge(fine_ge),
    .analog_phase(), .quiet_sample(), .tp_clock(), .ra_az(), .ra_amplify(),
    .ref_precharge(), .ref_accurate(), .acquiring_mask(), .converting_mask(),
    .aux_charge_enable(), .hold_low_enable(), .coarse_compare_enable(),
    .coarse_acquire_enable(), .flash_acquire_enable(), .fine_acquire_enable(),
    .coarse_trial(coarse_trial), .quantizer_dither(), .acquisition_dither_rails(),
    .fine_trial(fine_trial), .fine_compare_enable(), .flash_sample(),
    .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata), .cfg_rdata(),
    .cfg_validate(cfg_validate), .cfg_clear_valid(cfg_clear), .cfg_ready(cfg_ready),
    .sadc_code(9'd0), .sadc_rdy(1'b0), .adc2_code(12'd0), .adc2_rdy(1'b0),
    .ra_sat(ra_sat_fault), .rdac_ovf(rdac_fault), .adc2_over(adc2_fault), .inj_q(64'sd0),
    .slice_sel(), .main_sw(), .sub_sw(), .dither_sw(), .sw_valid(),
    .sar_dout(sar_dout), .sar_dout_valid(sar_valid), .sar_clip_low(), .sar_clip_high(),
    .sar_analog_ovf(sar_analog_ovf), .sar_acc_ovf(), .sar_status_word(), .sar_sample_id(sar_id),
    .sar_flags(sar_flags), .dsp_restart(dsp_restart), .sample_accepted(accepted),
    .accepted_sample_id(accepted_id), .sample_rejected(rejected),
    .rejected_sample_id(), .rejected_flags(), .rejected_after_fault(), .stream_fault(stream_fault),
    .cs_n(1'b1), .sclk(1'b1), .sdi(1'b0), .sdo(), .sdo_oe(),
    .cnv_p(1'b0), .cnv_n(1'b0), .cnv_pulse(), .filter_sync(1'b0),
    .ext_event(1'b0), .arm(1'b0), .rd_en(1'b0), .bit_en(1'b1),
    .ser_data(), .ser_frame(), .dco(), .gpio_i(4'd0), .gpio_o(), .gpio_oe(),
    .dsp_dout(dsp_dout), .dsp_dout_valid(dsp_valid), .dsp_status(),
    .correction_clip_low(), .correction_clip_high()
  );

  logic [19:0] expected[0:255];
  int wr = 0, rd = 0, analog_rejected = 0;
  always @(negedge clk) begin
    if (rst_n && !fault_test && accepted) begin
      if (!sar_valid || sar_flags != 0 || accepted_id != sar_id || wr >= 256)
        $fatal(1, "system top accepted malformed SAR retirement");
      expected[wr] = sar_dout;
      wr++;
    end
    if (rst_n && !fault_test && dsp_valid) begin
      if (rd >= wr || dsp_dout !== expected[rd])
        $fatal(1, "system top output mismatch n=%0d got=%h expected=%h", rd, dsp_dout, expected[rd]);
      rd++;
    end
    if (rst_n && !fault_test && (rejected || stream_fault))
      $fatal(1, "unexpected system stream fault");
    if (rst_n && fault_test && (sar_analog_ovf || stream_fault) && (accepted || dsp_valid))
      $fatal(1, "analog fault leaked through system boundary");
    if (rst_n && fault_test && sar_valid && sar_analog_ovf && rejected && sar_flags == 0)
      analog_rejected++;
  end
  task automatic write_cfg(input logic [15:0] addr, input logic [63:0] data);
    @(negedge clk); #1; cfg_addr = addr; cfg_wdata = data; cfg_wr = 1;
    @(negedge clk); #1; cfg_wr = 0;
  endtask
  task automatic configure_sar;
    for (int s = 0; s < 18; s++) begin
      write_cfg(16'(16'h2000 + s*256), 64'd0);
      for (int u = 0; u < 71; u++)
        write_cfg(16'(u*8), 64'((u < 63 ? 67108864 : 8388608) + s*873 + u*91));
    end
    write_cfg(16'h1000, 64'd0);
    write_cfg(16'h1008, -64'sd4294967296);
    write_cfg(16'h1010, 64'sd4294967296);
    write_cfg(16'h1018, 64'd0);
    repeat (4) @(negedge clk); #1; cfg_validate = 1;
    @(negedge clk); #1; cfg_validate = 0;
    if (!cfg_ready) $fatal(1, "system SAR configuration failed");
  endtask
  initial begin
    repeat (3) @(negedge clk); #1; rst_n = 1;
    configure_sar();
    for (int tick = 0; tick < 16*80; tick++) begin
      @(negedge clk); #1;
      coarse_target = 220 + ((tick / 16) % 72);
      fine_target = 1900 + ((tick / 16) % 240);
    end
    while (wr != rd) @(negedge clk);
    #1;
    if (rd < 50) $fatal(1, "too few completed system samples: %0d", rd);
    // Exercise each physical overflow input in a new SAR configuration epoch.
    fault_test = 1;
    for (int kind = 0; kind < 3; kind++) begin
      int rejected_before;
      rejected_before = analog_rejected;
      case (kind)
        0: ra_sat_fault = 1;
        1: rdac_fault = 1;
        2: adc2_fault = 1;
      endcase
      repeat (64) @(negedge clk); #1;
      ra_sat_fault = 0; rdac_fault = 0; adc2_fault = 0;
      if (!sar_analog_ovf || !stream_fault || accepted || dsp_valid ||
          analog_rejected == rejected_before)
        $fatal(1, "SAR analog overflow kind=%0d was not blocked", kind);
      // DSP restart cannot erase SAR's sticky analog fault.
      dsp_restart = 1; repeat (2) @(negedge clk); #1; dsp_restart = 0;
      repeat (2) @(negedge clk);
      if (!sar_analog_ovf || !stream_fault) $fatal(1, "uncleared analog fault disappeared");
      cfg_clear = 1; #1;
      if (dsp_valid) $fatal(1, "epoch clear did not suppress DSP output");
      repeat (2) @(negedge clk); #1; cfg_clear = 0;
      repeat (2) @(negedge clk);
      if (sar_analog_ovf || stream_fault) $fatal(1, "epoch clear did not clear analog fault");
      if (kind < 2) configure_sar();
    end
    $display("SAR20_SYSTEM_TOP_COMPLETE matched_samples=%0d analog_guard_cases=3 rejected=%0d", rd, analog_rejected);
    $finish;
  end
  initial begin #300000; $fatal(1, "system top timeout"); end
endmodule
