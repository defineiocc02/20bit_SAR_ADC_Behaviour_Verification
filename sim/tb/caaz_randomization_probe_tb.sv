`timescale 1ps/1fs
// Module: caaz_randomization_probe_tb; opt-in digital randomization diagnostic.
// Scope: current production core, CAAZ enabled, eight DEM/dither combinations.
// Clock: ideal 640 MHz (1.5625 ns); all stimulus uses the core clock domain.
// Data: synthetic nonuniform Q30 weights, signed normalized-voltage Q32 injection,
//       20-bit offset-binary raw codes and signed 21-bit pair differences.
// Trace: PRE-edge aperture/advance/capture/observation events; POST-NBA tick/raw/pair.
// Faults: missing coarse/fine valid, failed even AZ capture, clear with a pending even.
// Limits: ideal comparator fixtures; no analog CBIAS storage, H_AZ/K equality,
//         sample-switch safety, settling, PDK or static timing proof.
// Interface: +outdir is required and must name a caller-owned result directory. CSV fields and
// integer reference contract: tools/check_caaz_randomization_trace.py.
module caaz_randomization_probe_tb;
  logic clk = 1'b0;
  always #781.25 clk = ~clk;
  logic rst_n = 1'b0;
  logic cfg_wr = 1'b0;
  logic cfg_validate = 1'b0;
  logic cfg_clear_valid = 1'b0;
  logic [15:0] cfg_addr = '0;
  logic [63:0] cfg_wdata = '0;
  wire cfg_ready;
  wire [3:0] phase;
  wire quiet_sample;
  wire tp_clock;
  wire ra_az;
  wire caaz_input_chop_neg;
  wire caaz_az_capture_en;
  wire caaz_az_hold;
  wire [33:0] cal_obs_caaz_data;
  wire cal_obs_valid;
  wire [747:0] cal_obs_data;
  wire caaz_raw_valid;
  wire [91:0] caaz_raw_data;
  wire [19:0] dout;
  wire dout_valid;
  wire [31:0] dout_sample_id;
  wire [4:0] dout_flags;
  wire dout_analog_bad;
  wire signed [20:0] caaz_pair_difference;
  wire caaz_pair_difference_valid;
  wire [31:0] caaz_pair_first_sample_id;
  wire [31:0] caaz_pair_id;
  wire caaz_pair_fault;
  wire [3:0] caaz_pair_fault_code;
  wire [31:0] status_word;
  wire [1:0][8:0] coarse_trial;
  wire [11:0] fine_trial;
  wire [1:0] coarse_cmp_ge;
  wire [1:0] coarse_cmp_valid;
  wire fine_cmp_valid;
  wire caaz_az_capture_ok;
  wire [6:0] flash_therm;
  wire [17:0] acquiring_mask;
  wire [17:0] converting_mask;
  wire [1:0][7:0] quantizer_dither;
  wire [3:0] acquisition_dither_rails;
  wire signed [63:0] injection;
  int epoch = 0;
  int mode = 0;
  int dem = 0;
  int tick = 0;
  int coarse_target;
  int checks = 0;
  int raw_outputs = 0;
  int paired_outputs = 0;
  int event_file;
  int tick_file;
  bit tracing = 1'b0;
  string output_directory;

  always_comb begin
    coarse_target = dut.u_structure.current_chop_neg ? 192 : 320;
    if (mode == 2)
      coarse_target += int'($signed(dut.u_structure.current_dither));
  end
  // External known analog injection is in the ADC input domain, after chopping. There is no
  // analog-dither enable/generator in the production config API.
  assign injection = mode == 1 ?
      64'(int'((dut.u_structure.current_id + 1) % 5) - 2) * 64'sd100000 : 64'sd0;
  assign flash_therm = 7'((1 << (coarse_target >> 6)) - 1);
  assign coarse_cmp_ge[0] = coarse_target >= int'(coarse_trial[0]);
  assign coarse_cmp_ge[1] = coarse_target >= int'(coarse_trial[1]);
  assign coarse_cmp_valid = epoch == 8 && dut.u_structure.current_id == 16 ? 2'b00 : 2'b11;
  assign fine_cmp_valid = !(epoch == 8 && dut.structural_id == 21);
  assign caaz_az_capture_ok = !(epoch == 9 && dut.u_structure.current_id == 16);

  sar20_digital_core #(
      .P_CAAZ_ENABLE(1'b1),
      .P_CAL_OBSERVE(1'b1),
      .P_RECON_STAGES(7)
  ) dut (
      .clk(clk), .rst_n(rst_n),
      .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata),
      .cfg_rdata(), .cfg_validate(cfg_validate),
      .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready),
      .sadc_code(9'd0), .sadc_rdy(1'b0), .adc2_code(12'd0), .adc2_rdy(1'b0),
      .ra_sat(1'b0), .rdac_ovf(1'b0), .adc2_over(1'b0), .inj_q(injection),
      .flash_therm(flash_therm), .flash_valid(1'b1),
      .coarse_cmp_valid(coarse_cmp_valid), .coarse_cmp_ge(coarse_cmp_ge),
      .fine_cmp_valid(fine_cmp_valid), .fine_cmp_ge(2048 >= int'(fine_trial)),
      .analog_phase(phase), .quiet_sample(quiet_sample), .tp_clock(tp_clock),
      .ra_az(ra_az), .ra_amplify(), .ref_precharge(), .ref_accurate(),
      .acquiring_mask(acquiring_mask), .converting_mask(converting_mask),
      .aux_charge_enable(), .hold_low_enable(),
      .coarse_compare_enable(), .coarse_acquire_enable(),
      .flash_acquire_enable(), .fine_acquire_enable(), .coarse_trial(coarse_trial),
      .quantizer_dither(quantizer_dither),
      .acquisition_dither_rails(acquisition_dither_rails), .fine_trial(fine_trial),
      .fine_compare_enable(), .flash_sample(),
      .slice_sel(), .main_sw(), .sub_sw(), .dither_sw(), .sw_valid(),
      .dout(dout), .dout_valid(dout_valid), .clip_low(), .clip_high(),
      .analog_ovf(), .acc_ovf(), .status_word(status_word),
      .dout_sample_id(dout_sample_id), .dout_flags(dout_flags),
      .cal_obs_valid(cal_obs_valid), .cal_obs_data(cal_obs_data),
      .dout_analog_bad(dout_analog_bad), .caaz_az_capture_ok(caaz_az_capture_ok),
      .caaz_input_chop_neg(caaz_input_chop_neg), .caaz_az_capture_en(caaz_az_capture_en),
      .caaz_az_hold(caaz_az_hold), .cal_obs_caaz_data(cal_obs_caaz_data),
      .caaz_raw_valid(caaz_raw_valid), .caaz_raw_data(caaz_raw_data),
      .caaz_pair_difference(caaz_pair_difference),
      .caaz_pair_difference_valid(caaz_pair_difference_valid),
      .caaz_pair_first_sample_id(caaz_pair_first_sample_id), .caaz_pair_id(caaz_pair_id),
      .caaz_pair_fault(caaz_pair_fault), .caaz_pair_fault_code(caaz_pair_fault_code)
  );

  task automatic require_ok(input bit condition, input string message);
    checks++;
    if (!condition)
      $fatal(1, "%s epoch=%0d mode=%0d dem=%0d phase=%0d", message, epoch, mode, dem, phase);
  endtask

  task automatic write_configuration(input logic [15:0] address, input logic [63:0] value);
    @(negedge clk);
    cfg_addr = address;
    cfg_wdata = value;
    cfg_wr = 1'b1;
    @(negedge clk);
    cfg_wr = 1'b0;
  endtask

  task automatic configure();
    for (int slice = 0; slice < 18; slice++) begin
      write_configuration(16'(16'h2000 + slice * 256), 64'd0);
      for (int unit_id = 0; unit_id < 71; unit_id++)
        write_configuration(16'(unit_id * 8), unit_id < 63 ?
            64'(67108864 + slice * 262144 + unit_id * 127) :
            64'(8388608 + slice * 32768 + unit_id * 73));
    end
    write_configuration(16'h1000, 64'd0);
    // Preserve the paper-literal backend range, normalized by Vfs=3 V.
    write_configuration(16'h1008, -64'sd53687091);
    write_configuration(16'h1010, 64'sd590558003);
    write_configuration(16'h1018,
        64'((dem != 0 ? 3 : 0) | (mode == 2 ? 8 : (mode == 3 ? 4 : 0))));
    repeat (4) @(negedge clk);
    cfg_validate = 1'b1;
    @(negedge clk);
    cfg_validate = 1'b0;
    require_ok(cfg_ready, "configuration rejected");
  endtask

  // PRE-edge event records identify the exact accepted ownership. POST-edge ticks and completion
  // records show registered outputs after NBA settling.
  always @(posedge clk) begin
    if (tracing && rst_n && cfg_ready && !cfg_clear_valid) begin
      if (quiet_sample && dut.u_structure.acquisition_primed)
        $fwrite(event_file, "AP %0d %0d %0d %0d %0d %0d %h %0d %h %0d %h\n",
            epoch, mode, dem, tick, dut.u_structure.current_id + 1,
            caaz_input_chop_neg, acquiring_mask,
            $signed(dut.u_structure.acquiring_dither), acquisition_dither_rails,
            $signed(injection), dut.u_structure.acq_ids);
      if (dut.u_structure.advance)
        $fwrite(event_file, "ADV %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
            epoch, mode, dem, tick, dut.u_structure.current_id + 1,
            dut.u_structure.bank, dut.u_structure.sid_a, dut.u_structure.sid_b,
            $signed(dut.u_structure.random_dither));
      if (dut.u_structure.capture && dut.u_structure.conv_valid)
        $fwrite(event_file, "CAP %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %h %h %h %h %0d %0d\n",
            epoch, mode, dem, tick, dut.u_structure.current_id,
            dut.u_structure.coarse_ok, dut.u_structure.current_chop_neg,
            dut.u_structure.current_az_pair_id, dut.u_structure.current_az_valid,
            $signed(dut.u_structure.current_dither), dut.u_structure.conv_ids,
            dut.u_structure.main_on, dut.u_structure.sub_on, dut.u_structure.rails,
            $signed(dut.u_structure.current_injection), dut.u_structure.sid);
      if (cal_obs_valid)
        $fwrite(event_file, "OBS %0d %0d %0d %0d %h %h %0d %0d %0d\n",
            epoch, mode, dem, tick, cal_obs_data, cal_obs_caaz_data,
            dut.u_recon.sum_W, dut.u_recon.sum_Wa, $signed(dut.u_recon.rails));
    end
    #1;
    if (tracing) begin
      $fwrite(tick_file, "%0d,%0d,%0d,%0d,%.4f,%0d,%0d,%0d,%0d,%h,%h,%0d,%0d,%0d,%0d,%h,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
          epoch, mode, dem, tick, $realtime / 1000.0, phase,
          dut.u_structure.current_id, dut.u_structure.acquisition_primed,
          dut.u_structure.bank, acquiring_mask, converting_mask,
          caaz_input_chop_neg, dut.u_structure.current_chop_neg,
          $signed(dut.u_structure.acquiring_dither), $signed(dut.u_structure.current_dither),
          acquisition_dither_rails, int'($signed(quantizer_dither[0])), int'($signed(quantizer_dither[1])),
          dut.u_structure.sid, dut.u_structure.sid_a, dut.u_structure.sid_b,
          dut.u_structure.current_az_pair_id, dut.u_structure.current_az_valid,
          ra_az, caaz_az_capture_en, caaz_az_hold,
          dut.structural_id, dut.rc_busy, caaz_raw_valid, dout_valid, caaz_pair_fault, tp_clock);
      if (cfg_ready) begin
        require_ok($countones(acquiring_mask) == 8, "acquiring mask count");
        require_ok($countones(converting_mask) == 8 || converting_mask == 0, "converting mask count");
        require_ok((acquiring_mask & converting_mask) == 0, "acquire/convert overlap");
        require_ok(ra_az == (phase >= 1 && phase <= 8), "legacy reset gated");
      end
      if (caaz_raw_valid) begin
        $fwrite(event_file, "RAW %0d %0d %0d %0d %h\n", epoch, mode, dem, tick, caaz_raw_data);
        require_ok(caaz_raw_data[5:0] == 0, "fixture raw arithmetic/analog flags");
        raw_outputs++;
      end
      if (dout_valid) begin
        $fwrite(event_file, "PAIR %0d %0d %0d %0d %0d %0d %0d %0d %0d\n",
            epoch, mode, dem, tick, caaz_pair_first_sample_id, dout_sample_id,
            caaz_pair_id, dout, $signed(caaz_pair_difference));
        require_ok(dout_flags == 0 && !dout_analog_bad, "pair flags");
        paired_outputs++;
      end
      tick++;
    end
  end

  initial begin
    if (!$value$plusargs("outdir=%s", output_directory))
      $fatal(1, "outdir required");
    event_file = $fopen({output_directory, "/events.txt"}, "w");
    tick_file = $fopen({output_directory, "/ticks.csv"}, "w");
    if (event_file == 0 || tick_file == 0)
      $fatal(1, "unable to open caller-owned trace files");
    $fwrite(tick_file, "epoch,mode,dem,tick,time_ns,phase,current_id,primed,bank,acq_mask,conv_mask,acq_chop,current_chop,acq_dither,current_dither,acq_rails,qdither0,qdither1,sid,sid_a,sid_b,az_pair,az_valid,ra_az,az_capture,az_hold,fine_id,recon_busy,raw_valid,pair_valid,pair_fault,tp_clock\n");
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    for (epoch = 0; epoch < 11; epoch++) begin
      mode = epoch < 8 ? epoch % 4 : (epoch == 10 ? 2 : 3);
      dem = epoch < 8 ? epoch / 4 : 1;
      raw_outputs = 0;
      paired_outputs = 0;
      tick = 0;
      configure();
      tracing = 1'b1;
      for (int sample_tick = 0; sample_tick < 128 * 16; sample_tick++) begin
        @(negedge clk);
        if (epoch == 10 && raw_outputs == 1 && dut.u_caaz_pair.g_pair.waiting_for_odd)
          break;
      end
      if (epoch < 8) begin
        require_ok(raw_outputs >= 120 && paired_outputs >= 60, "insufficient healthy coverage");
        require_ok(!caaz_pair_fault, "healthy stream fault");
      end else if (epoch == 8) begin
        require_ok(caaz_pair_fault && caaz_pair_fault_code == 4, "missing-valid gap not stopped");
      end else if (epoch == 9) begin
        require_ok(caaz_pair_fault && caaz_pair_fault_code == 3, "AZ failure not stopped");
      end else begin
        require_ok(raw_outputs == 1 && paired_outputs == 0 &&
            dut.u_caaz_pair.g_pair.waiting_for_odd, "midpair clear setup");
      end
      $display("PROBE_EPOCH epoch=%0d mode=%0d dem=%0d raw=%0d pairs=%0d fault=%0d reason=%0d", epoch, mode, dem, raw_outputs, paired_outputs, caaz_pair_fault, caaz_pair_fault_code);
      cfg_clear_valid = 1'b1;
      @(negedge clk);
      cfg_clear_valid = 1'b0;
      repeat (6) begin
        @(negedge clk);
        require_ok(!dout_valid && !caaz_raw_valid && !cfg_ready && !caaz_pair_fault, "clear leaked context");
      end
      tracing = 1'b0;
    end
    $fclose(event_file);
    $fclose(tick_file);
    $display("CAAZ_RANDOMIZATION_PROBE_COMPLETE checks=%0d epochs=11 healthy_modes=8 missingvalid=1 az_failure=1 midpair_clear=1", checks);
    $finish;
  end
  initial begin
    #1000000000;
    $fatal(1, "diagnostic watchdog");
  end
endmodule
