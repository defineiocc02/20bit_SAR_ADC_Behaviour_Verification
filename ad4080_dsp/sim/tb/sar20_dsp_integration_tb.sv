`timescale 1ns/1ps
// Connect the real SAR structural top to the DSP wrapper and exercise its
// ingress contract with separately injected error retirements.
module sar20_dsp_integration_tb;
  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  logic cfg_wr = 0, cfg_validate = 0, cfg_clear = 0;
  logic [15:0] cfg_addr = 0;
  logic [63:0] cfg_wdata = 0;
  wire cfg_ready;
  logic [6:0] flash_therm = 0;
  wire [1:0] coarse_ge;
  wire [1:0][8:0] coarse_trial;
  wire [11:0] fine_trial;
  int coarse_target = 256, fine_target = 2048;
  assign coarse_ge[0] = coarse_target >= int'(coarse_trial[0]);
  assign coarse_ge[1] = coarse_target >= int'(coarse_trial[1]);
  wire fine_ge = fine_target >= int'(fine_trial);
  wire [19:0] sar_dout;
  wire sar_valid, sar_analog_ovf;
  wire [31:0] sar_id;
  wire [4:0] sar_flags;

  sar20_digital_core u_sar (
    .clk(clk), .rst_n(rst_n),
    .flash_therm(flash_therm), .flash_valid(1'b1),
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
    .ra_sat(1'b0), .rdac_ovf(1'b0), .adc2_over(1'b0), .inj_q(64'sd0),
    .slice_sel(), .main_sw(), .sub_sw(), .dither_sw(), .sw_valid(),
    .dout(sar_dout), .dout_valid(sar_valid), .clip_low(), .clip_high(),
    .analog_ovf(sar_analog_ovf), .acc_ovf(), .status_word(), .dout_sample_id(sar_id),
    .dout_flags(sar_flags), .cal_obs_valid(), .cal_obs_data(), .dout_analog_bad()
  );

  logic inject_mode = 1, inject_valid = 0, inject_analog_ovf = 0, restart = 0;
  logic [19:0] inject_data = 0;
  logic [31:0] inject_id = 0;
  logic [4:0] inject_flags = 0;
  wire [19:0] in_data = inject_mode ? inject_data : sar_dout;
  wire in_valid = inject_mode ? inject_valid : sar_valid;
  wire [31:0] in_id = inject_mode ? inject_id : sar_id;
  wire [4:0] in_flags = inject_mode ? inject_flags : sar_flags;
  wire in_analog_ovf = inject_mode ? inject_analog_ovf : sar_analog_ovf;
  wire accepted, rejected, rejected_after_fault, fault;
  wire [31:0] accepted_id, rejected_id;
  wire [4:0] rejected_flags;
  wire [19:0] dout;
  wire dout_valid;
  wire [1:0] ser_data, ser_frame;
  wire dco;

  sar20_ad4080_top #(.FIFO_DEPTH(32)) dut (
    .clk(clk), .rst_n(rst_n), .sar_epoch_clear(cfg_clear), .dsp_restart(restart),
    .sar_dout(in_data), .sar_dout_valid(in_valid), .sar_sample_id(in_id),
    .sar_flags(in_flags), .sar_analog_ovf(in_analog_ovf),
    .sample_accepted(accepted), .accepted_sample_id(accepted_id),
    .sample_rejected(rejected), .rejected_sample_id(rejected_id),
    .rejected_flags(rejected_flags), .rejected_after_fault(rejected_after_fault),
    .stream_fault(fault), .cs_n(1'b1), .sclk(1'b1), .sdi(1'b0), .sdo(), .sdo_oe(),
    .cnv_p(1'b0), .cnv_n(1'b0), .cnv_pulse(), .filter_sync(1'b0),
    .ext_event(1'b0), .arm(1'b0), .rd_en(1'b0), .bit_en(1'b1),
    .ser_data(ser_data), .ser_frame(ser_frame), .dco(dco),
    .gpio_i(4'd0), .gpio_o(), .gpio_oe(), .dout(dout), .dout_valid(dout_valid),
    .status(), .correction_clip_low(), .correction_clip_high()
  );

  logic [19:0] expected [0:1023];
  int wr = 0, rd = 0, real_samples = 0, rejects = 0;
  bit scoreboard_enabled = 1;
  // At the reset defaults (gain=1, offset=0, bypass, FIFO off), the two
  // independent code conversions must preserve the complete 20-bit word.
  always @(negedge clk) begin
    if (rst_n && scoreboard_enabled && accepted) begin
      if (wr >= 1024) $fatal(1, "scoreboard overflow");
      expected[wr] = in_data;
      wr++;
      if (accepted_id !== in_id) $fatal(1, "input ID lost");
      if (!inject_mode) real_samples++;
    end
    if (rst_n && scoreboard_enabled && dout_valid) begin
      if (rd >= wr || dout !== expected[rd])
        $fatal(1, "DSP mismatch index=%0d got=%h expected=%h", rd, dout, expected[rd]);
      rd++;
    end
    if (rst_n && fault && (dout_valid || ser_data != 0 || ser_frame != 0 || dco))
      $fatal(1, "fault must suppress output validity and serial pins");
  end

  task automatic inject(input logic [19:0] word, input logic [4:0] flags,
                        input int sample_id);
    @(negedge clk); #1;
    inject_data = word; inject_valid = 1; inject_flags = flags; inject_id = 32'(sample_id);
  endtask
  task automatic end_inject;
    @(negedge clk); #1; inject_valid = 0; inject_flags = 0;
  endtask
  task automatic restart_dsp;
    @(negedge clk); #1; restart = 1; inject_valid = 0; inject_flags = 0;
    repeat (2) @(negedge clk);
    #1; restart = 0;
    repeat (2) @(negedge clk);
    if (fault) $fatal(1, "fault did not clear on restart");
  endtask
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
    repeat (4) @(negedge clk);
    #1; cfg_validate = 1;
    @(negedge clk); #1; cfg_validate = 0;
    if (!cfg_ready) $fatal(1, "SAR configuration failed");
  endtask

  initial begin
    repeat (3) @(negedge clk); #1; rst_n = 1;
    repeat (3) @(negedge clk);
    // Consecutive transfers cover both rails, just below/at midscale and zero.
    inject(20'h00000, 0, 1);
    inject(20'h7ffff, 0, 2);
    inject(20'h80000, 0, 3);
    inject(20'hfffff, 0, 4);
    end_inject();
    repeat (8) @(negedge clk);
    if (wr != 4 || rd != 4) $fatal(1, "boundary transfer count wr=%0d rd=%0d", wr, rd);

    // A flagged retirement immediately after an accepted input must suppress
    // that earlier input if it is still in flight through the DSP pipeline.
    scoreboard_enabled = 0;
    inject(20'h81234, 0, 50);
    inject(20'h80000, 5'b00001, 51);
    @(posedge clk); #1;
    if (!fault || dout_valid) $fatal(1, "in-flight fault did not latch");
    end_inject();
    repeat (8) @(negedge clk);
    restart_dsp();
    scoreboard_enabled = 1;

    for (int bit_index = 0; bit_index < 5; bit_index++) begin
      inject(20'h91234, 5'(1 << bit_index), 100 + bit_index);
      #1;
      if (!rejected || accepted || rejected_id != 32'(100 + bit_index) ||
          rejected_flags != 5'(1 << bit_index) || rejected_after_fault)
        $fatal(1, "first fault event is not reported");
      @(posedge clk); #1;
      if (!fault || dout_valid) $fatal(1, "fault did not latch");
      inject(20'h80000, 0, 200 + bit_index);
      #1;
      if (!rejected || !rejected_after_fault || accepted)
        $fatal(1, "post-fault sample not rejected");
      rejects += 2;
      end_inject();
      restart_dsp();
    end
    // The SAR's sticky analog overflow is separate from the five result flags.
    inject(20'h81234, 0, 250);
    #1; inject_analog_ovf = 1;
    #1;
    if (!rejected || accepted || dout_valid) $fatal(1, "analog overflow did not block input");
    @(posedge clk); #1;
    if (!fault) $fatal(1, "analog overflow did not latch fault");
    end_inject();
    @(negedge clk); #1; restart = 1;
    repeat (2) @(negedge clk); #1;
    if (fault) $fatal(1, "DSP reset did not clear local fault");
    restart = 0;
    @(posedge clk); #1;
    if (!fault) $fatal(1, "uncleared SAR analog status did not restore fault");
    @(negedge clk); #1; inject_analog_ovf = 0; cfg_clear = 1;
    repeat (2) @(negedge clk); #1; cfg_clear = 0;
    repeat (2) @(negedge clk);
    if (fault) $fatal(1, "epoch clear did not recover analog fault");
    inject(20'habcde, 0, 300); end_inject();
    repeat (8) @(negedge clk);
    if (wr != 5 || rd != 5) $fatal(1, "restart did not restore data stream");

    configure_sar();
    @(negedge clk); #1; inject_mode = 0;
    for (int tick = 0; tick < 16*80; tick++) begin
      @(negedge clk); #1;
      coarse_target = 220 + ((tick / 16) % 72);
      fine_target = 1900 + ((tick / 16) % 240);
    end
    // Switch away only after the last real conversion has drained.
    repeat (30) @(negedge clk);
    #1; inject_mode = 1; inject_valid = 0;
    repeat (8) @(negedge clk);
    if (fault || real_samples < 50 || wr != rd)
      $fatal(1, "real SAR stream failed samples=%0d wr=%0d rd=%0d fault=%b", real_samples, wr, rd, fault);
    cfg_clear = 1; #1;
    if (dout_valid || fault) $fatal(1, "epoch clear did not reset DSP");
    @(negedge clk); #1; cfg_clear = 0;
    $display("SAR20_DSP_INTEGRATION_COMPLETE boundaries=4 flag_cases=5 analog_guard=PASS rejected=%0d real_sar_samples=%0d", rejects, real_samples);
    $finish;
  end
  initial begin #300000; $fatal(1, "integration timeout"); end
endmodule
