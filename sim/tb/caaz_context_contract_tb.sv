`timescale 1ps/1fs
// Real structural core, ideal640MHz clock. This verifies digital ownership, not analog chopping,
// CBIAS noise storage, aperture settling, or target STA.
module caaz_context_contract_tb;
  logic clk = 1'b0;
  always #781.25 clk = ~clk;
  logic rst_n = 1'b0;
  logic cfg_wr = 1'b0;
  logic cfg_validate = 1'b0;
  logic cfg_clear_valid = 1'b0;
  logic [15:0] cfg_addr = '0;
  logic [63:0] cfg_wdata = '0;
  wire [63:0] cfg_rdata;
  wire cfg_ready;
  wire [3:0] phase;
  wire quiet_sample;
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
  int scenario = 0;
  int coarse_target;
  int checks = 0;
  int raw_outputs = 0;
  int paired_outputs = 0;
  int acquired_samples = 0;
  int trace_file;
  string output_directory;
  bit expected_present[64];
  bit expected_chop[64];
  bit expected_az_valid[64];
  int expected_pair[64];
  bit seen_raw[64];
  int raw_code_by_id[64];

  always_comb begin
    // Ideal comparator input follows the physically captured CURRENT chop, not the chopper already
    // prepared for the next acquiring bank.
    coarse_target = dut.u_structure.current_chop_neg ? 192 : 320;
  end
  assign flash_therm = 7'((1 << (coarse_target >> 6)) - 1);
  assign coarse_cmp_ge[0] = coarse_target >= int'(coarse_trial[0]);
  assign coarse_cmp_ge[1] = coarse_target >= int'(coarse_trial[1]);
  assign coarse_cmp_valid = scenario == 0 && dut.u_structure.current_id == 4 ? 2'b00 : 2'b11;
  assign fine_cmp_valid = !(scenario == 0 && dut.structural_id == 7);
  assign caaz_az_capture_ok = !(scenario == 1 && dut.u_structure.current_id == 4);

  sar20_digital_core #(
      .P_CAAZ_ENABLE(1'b1),
      .P_CAL_OBSERVE(1'b1),
      .P_RECON_STAGES(5)
  ) dut (
      .clk(clk), .rst_n(rst_n),
      .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata),
      .cfg_rdata(cfg_rdata), .cfg_validate(cfg_validate),
      .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready),
      .sadc_code(9'd0), .sadc_rdy(1'b0), .adc2_code(12'd0), .adc2_rdy(1'b0),
      .ra_sat(1'b0), .rdac_ovf(1'b0), .adc2_over(1'b0), .inj_q(64'sd0),
      .flash_therm(flash_therm), .flash_valid(1'b1),
      .coarse_cmp_valid(coarse_cmp_valid), .coarse_cmp_ge(coarse_cmp_ge),
      .fine_cmp_valid(fine_cmp_valid), .fine_cmp_ge(2048 >= int'(fine_trial)),
      .analog_phase(phase), .quiet_sample(quiet_sample), .tp_clock(),
      .ra_az(ra_az), .ra_amplify(), .ref_precharge(), .ref_accurate(),
      .acquiring_mask(), .converting_mask(), .aux_charge_enable(), .hold_low_enable(),
      .coarse_compare_enable(), .coarse_acquire_enable(),
      .flash_acquire_enable(), .fine_acquire_enable(), .coarse_trial(coarse_trial),
      .quantizer_dither(), .acquisition_dither_rails(), .fine_trial(fine_trial),
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
      $fatal(1, "%s scenario=%0d phase=%0d", message, scenario, phase);
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
        write_configuration(16'(unit_id * 8), unit_id < 63 ? 64'd67108864 : 64'd8388608);
    end
    write_configuration(16'h1000, 64'd0);
    // Unchanged paper-literal backend range, not a widened test range.
    write_configuration(16'h1008, -64'sd53687091);
    write_configuration(16'h1010, 64'sd590558003);
    write_configuration(16'h1018, 64'd0);
    repeat (4) @(negedge clk);
    cfg_validate = 1'b1;
    @(negedge clk);
    cfg_validate = 1'b0;
    require_ok(cfg_ready, "configuration rejected");
  endtask

  // Independent reference starts at the actual input aperture: the physical chop wire is sampled
  // BEFORE the DUT's NBA polarity update. The upcoming ID comes from the real pool, not an assumed
  // ID parity relation.
  always @(posedge clk) begin
    int expected_id;
    if (rst_n && cfg_ready && !cfg_clear_valid && quiet_sample &&
        dut.u_structure.acquisition_primed) begin
      expected_id = int'(dut.u_structure.current_id) + 1;
      require_ok(expected_id < 64, "reference storage exceeded");
      require_ok(caaz_input_chop_neg == 1'(acquired_samples % 2), "input chop sequence/priming");
      if (acquired_samples == 0)
        require_ok(expected_id == 2 && !caaz_input_chop_neg, "first valid ID2 must be positive");
      if (acquired_samples == 1)
        require_ok(expected_id == 3 && caaz_input_chop_neg, "second valid ID3 must be negative");
      expected_present[expected_id] = 1'b1;
      expected_chop[expected_id] = caaz_input_chop_neg;
      expected_pair[expected_id] = acquired_samples / 2;
      expected_az_valid[expected_id] = !(scenario == 1 && (expected_id == 4 || expected_id == 5));
      acquired_samples++;
    end
  end

  task automatic observe();
    int owner_id;
    int raw_id;
    logic [19:0] raw_code;
    bit raw_chop;
    logic [31:0] raw_pair;
    bit raw_az_valid;
    logic [4:0] raw_flags;
    bit raw_bad;
    logic [31:0] packed_raw_id;
    int expected_difference;
    int expected_word;
    real even_voltage_lsb;
    real odd_voltage_lsb;
    // The current RA owner is being installed at phase1, before pool advance updates current_id on
    // its following edge.
    owner_id = int'(dut.u_structure.current_id) + (phase == 1 ? 1 : 0);
    require_ok(ra_az == (phase >= 1 && phase <= 8), "legacy per-cycle RA reset was gated");
    if (owner_id >= 2 && owner_id < 64 && expected_present[owner_id]) begin
      require_ok(dut.u_structure.current_chop_neg == expected_chop[owner_id], "current RA borrowed acquiring/fine chop");
      require_ok(dut.u_structure.current_az_pair_id == 32'(expected_pair[owner_id]), "current AZ generation");
      require_ok(caaz_az_hold == expected_chop[owner_id], "odd CBIAS hold ownership");
      require_ok(caaz_az_capture_en ==
          ((phase >= 1 && phase <= 8) && !expected_chop[owner_id]), "even-only AZ capture ownership");
    end
    if (dut.u_structure.context_valid) begin
      require_ok(dut.structural_chop_neg == expected_chop[dut.structural_id], "residue-to-fine chop mismatch");
      require_ok(dut.structural_az_pair_id == 32'(expected_pair[dut.structural_id]), "residue-to-fine AZ generation");
      require_ok(dut.structural_az_valid == expected_az_valid[dut.structural_id], "residue-to-fine AZ validity");
    end
    if (cal_obs_valid)
      require_ok(cal_obs_caaz_data == {expected_chop[dut.structural_id],
          32'(expected_pair[dut.structural_id]), expected_az_valid[dut.structural_id]}, "calibration packet sideband mismatch");
    if (caaz_raw_valid) begin
      {packed_raw_id, raw_code, raw_chop, raw_pair, raw_az_valid, raw_flags, raw_bad} = caaz_raw_data;
      raw_id = int'(packed_raw_id);
      require_ok(expected_present[raw_id], "raw result lacks an acquisition record");
      require_ok(!seen_raw[raw_id], "duplicate raw result");
      require_ok(raw_chop == expected_chop[raw_id], "reconstruction result used a live chop bit");
      require_ok(raw_pair == 32'(expected_pair[raw_id]), "raw result AZ generation");
      require_ok(raw_az_valid == expected_az_valid[raw_id], "raw result AZ validity");
      require_ok(raw_flags == 0 && !raw_bad, "unexpected arithmetic/analog fixture flags");
      seen_raw[raw_id] = 1'b1;
      raw_code_by_id[raw_id] = int'(raw_code);
      raw_outputs++;
    end
    require_ok(caaz_pair_difference_valid == dout_valid, "pair word/difference valid mismatch");
    if (dout_valid) begin
      require_ok(seen_raw[caaz_pair_first_sample_id] && seen_raw[dout_sample_id], "pair lacks raw records");
      even_voltage_lsb = real'(raw_code_by_id[caaz_pair_first_sample_id]) + 0.5 - 524288.0;
      odd_voltage_lsb = real'(raw_code_by_id[dout_sample_id]) + 0.5 - 524288.0;
      expected_word = $rtoi($floor((even_voltage_lsb - odd_voltage_lsb) / 2.0 + 524288.0));
      expected_difference = raw_code_by_id[caaz_pair_first_sample_id] - raw_code_by_id[dout_sample_id];
      require_ok(dout == 20'(expected_word), "full-code pair floor mismatch");
      require_ok(int'($signed(caaz_pair_difference)) == expected_difference, "full-code pair difference mismatch");
      require_ok(dout_flags == 0 && !dout_analog_bad, "valid pair flags");
      require_ok(caaz_pair_id == 32'(expected_pair[dout_sample_id]), "pair generation output");
      paired_outputs++;
    end
    $fwrite(trace_file, "%0d,%.4f,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d\n",
        scenario, $realtime/1000.0, phase, dut.u_structure.current_id,
        caaz_input_chop_neg, dut.u_structure.current_chop_neg, ra_az,
        caaz_az_capture_en, dut.structural_id, dut.structural_chop_neg,
        caaz_raw_valid, caaz_raw_valid ? caaz_raw_data[91:60] : 32'd0,
        dout_valid, dout_sample_id, caaz_pair_fault);
  endtask

  initial begin
    if (!$value$plusargs("outdir=%s", output_directory))
      output_directory = ".";
    trace_file = $fopen({output_directory, "/caaz_context_trace.csv"}, "w");
    $fwrite(trace_file, "scenario,time_ns,phase,current_id,acquiring_chop,current_chop,legacy_ra_az,az_capture,fine_id,fine_chop,raw_valid,raw_id,pair_valid,pair_last_id,pair_fault\n");
    repeat (2) @(negedge clk);
    rst_n = 1'b1;
    for (scenario = 0; scenario < 3; scenario++) begin
      acquired_samples = 0;
      raw_outputs = 0;
      paired_outputs = 0;
      for (int sample = 0; sample < 64; sample++) begin
        expected_present[sample] = 1'b0;
        seen_raw[sample] = 1'b0;
      end
      configure();
      for (int tick = 0; tick < 16 * 16; tick++) begin
        observe();
        @(negedge clk);
      end
      require_ok(seen_raw[2] && seen_raw[3], "ID2/3 were not actually reconstructed");
      require_ok(raw_outputs >= 9, "insufficient real structural outputs");
      if (scenario == 0) begin
        require_ok(!seen_raw[4] && !seen_raw[7], "missing comparator-valid was not exercised");
        require_ok(caaz_pair_fault && caaz_pair_fault_code == 4, "missing raw ID did not stop pairing");
        require_ok(paired_outputs == 1, "faulted stream continued pairing");
      end else if (scenario == 1) begin
        require_ok(caaz_pair_fault && caaz_pair_fault_code == 3, "failed AZ capture did not stop pairing");
        require_ok(paired_outputs == 1, "invalid AZ state was averaged");
      end else begin
        require_ok(!caaz_pair_fault && paired_outputs >= 5, "healthy CAAZ stream did not pair");
      end
      $display("CAAZ_CONTEXT_EPOCH scenario=%0d raw=%0d pairs=%0d fault=%0d cause=%0d", scenario, raw_outputs, paired_outputs, caaz_pair_fault, caaz_pair_fault_code);
      cfg_clear_valid = 1'b1;
      @(negedge clk);
      cfg_clear_valid = 1'b0;
      repeat (16) begin
        @(negedge clk);
        require_ok(!dout_valid && !caaz_raw_valid && !cfg_ready && !caaz_pair_fault, "clear leaked old context/pair");
      end
    end
    $fclose(trace_file);
    $display("CAAZ_CONTEXT_CONTRACT_COMPLETE checks=%0d epochs=3 first_ids=2,3 missingvalid=PASS az_failure=PASS legacy_reset=PASS", checks);
    $finish;
  end
  initial begin
    #100000000;
    $fatal(1, "CAAZ context watchdog");
  end
endmodule
