// Explicit synthetic comparator/binding component integration, not an analog ADC. Required
// generated inputs and coefficient files are selected by the caller. The trace separates
// acquisition handshakes, actual physical RDAC loads, launch observations and completed words; an
// independent integer checker reads it.
`timescale 1ns/1fs
`include "rtl_params.vh"
module source_control_contract_tb #(
    parameter realtime P_TICK_NS = 0.0
);
  logic clk = 1'b0;
  initial begin
    if (P_TICK_NS > 0) begin
      forever #(P_TICK_NS/2) clk = ~clk;
    end
  end
  logic rst_n = 1'b0;
  logic cfg_wr = 1'b0, cfg_validate = 1'b0, cfg_clear_valid = 1'b0;
  logic [15:0] cfg_addr = '0;
  logic [63:0] cfg_wdata = '0;
  wire [63:0] cfg_rdata;
  wire cfg_ready;
  logic source_control_valid = 1'b0;
  logic [CONTROL_WORD_BITS-1:0] source_control_word = '0;
  logic [STATE_BITS-1:0] source_control_state = '0;
  wire source_control_ready, acquisition_control_valid;
  wire [CONTROL_WORD_BITS-1:0] acquisition_control_word;
  wire [STATE_BITS-1:0] acquisition_control_state;
  wire signed [V_BITS-1:0] acquisition_sadc_shift_q, acquisition_rdac_shift_q;
  wire [3:0] phase;
  wire [17:0] acquiring, converting, selected;
  wire [N_SLICES-1:0][N_UNIT_MAIN-1:0] main_sw;
  wire [N_SLICES-1:0][N_UNIT_SUB-1:0] sub_sw;
  wire [1:0][8:0] coarse_trial;
  wire [ADC2_BITS-1:0] fine_trial;
  wire [1:0] coarse_ge = {9'd173 >= coarse_trial[1], 9'd173 >= coarse_trial[0]};
  wire fine_ge = ADC2_BITS'(32) >= fine_trial;
  logic adc2_over = 1'b0;
  wire sw_valid, dout_valid, analog_ovf, dout_bad;
  wire unused_conversion_fault;
  wire [19:0] dout;
  wire [31:0] result_id, status_word;
  wire [4:0] flags;
  wire obs_valid;
  wire [CAL_OBS_BITS-1:0] obs_data;
  wire unused_quiet, unused_tp, unused_az, unused_amp, unused_pre, unused_accurate;
  wire [17:0] unused_aux, unused_hold;
  wire [1:0] unused_coarse_compare, unused_coarse_acquire;
  wire unused_flash_acquire, unused_fine_acquire, unused_fine_compare, unused_flash_sample;
  wire unused_clip_low, unused_clip_high, unused_acc;
  sar20_digital_core #(.P_CAL_OBSERVE(1'b1)) dut (
      .clk(clk), .rst_n(rst_n), .source_control_valid(source_control_valid),
      .source_control_word(source_control_word), .source_control_state(source_control_state),
      .source_control_ready(source_control_ready), .acquisition_control_valid(acquisition_control_valid),
      .acquisition_control_word(acquisition_control_word), .acquisition_control_state(acquisition_control_state),
      .acquisition_sadc_shift_q(acquisition_sadc_shift_q),
      .acquisition_rdac_shift_q(acquisition_rdac_shift_q),
      .flash_therm(7'b0000011), .flash_valid(1'b1), .coarse_cmp_valid(2'b11),
      .coarse_cmp_ge(coarse_ge), .fine_cmp_valid(1'b1), .fine_cmp_ge(fine_ge),
      .analog_phase(phase), .quiet_sample(unused_quiet), .tp_clock(unused_tp),
      .ra_az(unused_az), .ra_amplify(unused_amp), .ref_precharge(unused_pre),
      .ref_accurate(unused_accurate), .acquiring_mask(acquiring), .converting_mask(converting),
      .aux_charge_enable(unused_aux), .hold_low_enable(unused_hold),
      .coarse_compare_enable(unused_coarse_compare), .coarse_acquire_enable(unused_coarse_acquire),
      .flash_acquire_enable(unused_flash_acquire), .fine_acquire_enable(unused_fine_acquire),
      .coarse_trial(coarse_trial), .fine_trial(fine_trial), .fine_compare_enable(unused_fine_compare),
      .flash_sample(unused_flash_sample), .cfg_wr(cfg_wr), .cfg_addr(cfg_addr),
      .cfg_wdata(cfg_wdata), .cfg_rdata(cfg_rdata), .cfg_validate(cfg_validate),
      .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready), .ra_sat(1'b0),
      .rdac_ovf(1'b0), .adc2_over(adc2_over), .slice_sel(selected), .main_sw(main_sw),
      .sub_sw(sub_sw), .sw_valid(sw_valid), .dout(dout), .dout_valid(dout_valid),
      .clip_low(unused_clip_low), .clip_high(unused_clip_high), .analog_ovf(analog_ovf),
      .acc_ovf(unused_acc), .status_word(status_word), .dout_sample_id(result_id),
      .conversion_fault(unused_conversion_fault),
      .dout_flags(flags), .cal_obs_valid(obs_valid), .cal_obs_data(obs_data),
      .dout_analog_bad(dout_bad)
  );
  logic [W_BITS-1:0] coefficient [N_SLICES][N_UNIT_TOTAL];
  logic signed [V_BITS-1:0] scalar [3];
  int epoch = 0, controls = 0, checks = 0, observations = 0, raw_words = 0, output_words = 0;
  int controls_file, residues_file, events_file;
  int frames;
  string trace_dir, weights_path, scalars_path;
  task automatic require_ok(input bit condition, input string message);
    checks++;
    if (!condition)
      $fatal(1, "%s epoch=%0d control_count=%0d", message, epoch, controls);
  endtask
  task automatic write_cfg(input logic [15:0] address, input logic [63:0] value);
    @(negedge clk);
    cfg_addr = address;
    cfg_wdata = value;
    cfg_wr = 1'b1;
    @(negedge clk);
    cfg_wr = 1'b0;
  endtask
  task automatic configure;
    for (int s = 0; s < int'(N_SLICES); s++) begin
      write_cfg(16'(16'h2000 + s*256), 64'd0);
      for (int u = 0; u < int'(N_UNIT_TOTAL); u++) begin
        write_cfg(16'(u*8), 64'(coefficient[s][u]));
        #0.001;
        require_ok(cfg_rdata == 64'(coefficient[s][u]), "weight readback mismatch");
      end
    end
    for (int i = 0; i < 3; i++)
      write_cfg(16'(16'h1000 + i*8), 64'(scalar[i]));
    // The old scalar-dither control address is rejected even with zero data.
    write_cfg(16'h1018, 64'd0);
    cfg_addr = 16'h1020;
    #0.001;
    require_ok(cfg_rdata[31:6] == 26'd5, "retired mode write was not rejected");
    @(negedge clk);
    cfg_validate = 1'b1;
    @(negedge clk);
    cfg_validate = 1'b0;
    #0.001;
    require_ok(cfg_ready && status_word[31:6] == 0, "explicit complete configuration rejected");
  endtask
  initial forever begin
    @(negedge clk);
    if (rst_n && cfg_ready && !cfg_clear_valid) begin
      if (phase == 1) begin
        source_control_valid = controls + 2 != 8;
        source_control_word = CONTROL_WORD_BITS'((controls + 2) % 4);
        source_control_state = STATE_BITS'((controls + 2 == 12) ? 7 : (controls + 2) % 7);
      end
      // External fault injection belongs to residue6 at its quiet transfer.
      adc2_over = phase == 0 && controls == 6;
    end else begin
      source_control_valid = 1'b0;
      adc2_over = 1'b0;
    end
  end
  initial forever begin : record_edge
    bit ready_before;
    bit valid_before;
    int owner;
    logic [CONTROL_WORD_BITS-1:0] word_before;
    logic [STATE_BITS-1:0] state_before;
    @(posedge clk);
    if (!rst_n || !cfg_ready || cfg_clear_valid) begin
      controls = 0;
    end else begin
      ready_before = source_control_ready;
      valid_before = source_control_valid;
      owner = controls + 2;
      word_before = source_control_word;
      state_before = source_control_state;
      if (phase == 9)
        $fdisplay(residues_file, "%0d,%0d,%h,%h,%h,%h,%0d", epoch, controls,
                  converting, selected, main_sw, sub_sw, sw_valid);
      if (obs_valid) begin
        $fdisplay(events_file, "%0d,OBS,,,,,%h,%h,%h,%0d", epoch, obs_data, 34'd0, status_word, analog_ovf);
        observations++;
      end
      if (dout_valid) begin
        $fdisplay(events_file, "%0d,OUT,%0d,%0d,%0d,%0d,,,%h,%0d", epoch,
                  result_id, dout, flags, dout_bad, status_word, analog_ovf);
        output_words++;
      end
      if (ready_before) begin
        controls++;
        #0.001;
        $fdisplay(controls_file, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h", epoch,
                  owner, valid_before, word_before, state_before, acquisition_control_valid,
                  acquisition_control_word, acquisition_control_state, acquiring, converting);
        require_ok(acquisition_control_valid == valid_before, "acquisition valid ownership mismatch");
        if (valid_before)
          require_ok(acquisition_control_word == word_before && acquisition_control_state == state_before,
                     "acquisition handshake did not freeze actual word/state");
        if (valid_before)
          require_ok(acquisition_sadc_shift_q == 0 &&
                     acquisition_rdac_shift_q == (V_BITS'(word_before) <<< 8),
                     "explicit acquiring-node shift lost control ownership");
      end
    end
  end
  initial begin : main
    int file_id, read_count;
    logic [W_BITS-1:0] unused_extra_weight;
    logic [63:0] unused_extra_scalar;
    require_ok(P_TICK_NS > 0, "explicit tick period is required");
    require_ok(N_SLICES == 18 && N_ACTIVE == 8 && N_UNIT_MAIN == 2 && N_UNIT_SUB == 1 &&
               ADC2_BITS == 6 && CONTROL_WORD_BITS == 2 && STATE_BITS == 3,
               "this is the explicitly declared source_control_component fixture only");
    if (!$value$plusargs("weights=%s", weights_path) || !$value$plusargs("scalars=%s", scalars_path) ||
        !$value$plusargs("trace_dir=%s", trace_dir) || !$value$plusargs("frames=%d", frames))
      $fatal(1, "explicit coefficient paths/trace directory/frame count required");
    require_ok(frames >= 24 && frames <= 1000, "bounded nonzero frame count required");
    file_id = $fopen(weights_path, "r");
    require_ok(file_id != 0, "weights missing");
    for (int s = 0; s < int'(N_SLICES); s++)
      for (int u = 0; u < int'(N_UNIT_TOTAL); u++) begin
        read_count = $fscanf(file_id, "%h", coefficient[s][u]);
        require_ok(read_count == 1 && coefficient[s][u] > 0 && coefficient[s][u] < 48'h80000000,
                   "missing/out-of-fixture coefficient");
      end
    require_ok($fscanf(file_id, "%h", unused_extra_weight) != 1, "extra coefficient record");
    $fclose(file_id);
    file_id = $fopen(scalars_path, "r");
    require_ok(file_id != 0, "scalars missing");
    read_count = $fscanf(file_id, "%h %h %h", scalar[0], scalar[1], scalar[2]);
    require_ok(read_count == 3 && scalar[1] < scalar[2], "missing/invalid explicit scalar record");
    require_ok($fscanf(file_id, "%h", unused_extra_scalar) != 1, "extra scalar record");
    $fclose(file_id);
    controls_file = $fopen({trace_dir, "/controls.csv"}, "w");
    residues_file = $fopen({trace_dir, "/residues.csv"}, "w");
    events_file = $fopen({trace_dir, "/events.csv"}, "w");
    require_ok(controls_file != 0 && residues_file != 0 && events_file != 0, "trace open failed");
    $fdisplay(controls_file, "epoch,owner_sample_id,input_valid,input_word,input_state,actual_valid,actual_word,actual_state,acquiring_mask,converting_mask");
    $fdisplay(residues_file, "epoch,sample_id,converting_mask,selected_mask,main_physical,sub_physical,sw_valid");
    $fdisplay(events_file, "epoch,kind,sample_id,code,flags,analog_bad,packet_hex,caaz_hex,status_word,analog_sticky");
    repeat (3) @(negedge clk);
    rst_n = 1'b1;
    for (epoch = 0; epoch < 2; epoch++) begin
      configure();
      repeat (frames * 16) @(negedge clk);
      cfg_clear_valid = 1'b1;
      repeat (2) @(negedge clk);
      cfg_clear_valid = 1'b0;
      repeat (2) @(negedge clk);
      require_ok(!cfg_ready && !dout_valid && !acquisition_control_valid,
                 "clear did not cancel the configuration epoch");
      $fdisplay(events_file, "%0d,END,,,,,,,%h,%0d", epoch, status_word, analog_ovf);
      cfg_validate = 1'b1;
      @(negedge clk);
      cfg_validate = 1'b0;
      #0.001;
      require_ok(!cfg_ready && status_word[31:6] == 26'd6,
                 "clear did not require complete coefficient reload");
    end
    require_ok(observations > 0 && output_words > 0, "empty integration comparison domain");
    $fclose(controls_file);
    $fclose(residues_file);
    $fclose(events_file);
    $display("SOURCE_CONTROL_COMPONENT_COMPLETE observations=%0d outputs=%0d checks=%0d epochs=2 scope=COMPONENT_SYNTHETIC_DIGITAL", observations, output_words, checks);
    $display("SOURCE_CONTROL_ANALOG_MAPPING_TIMING_CAAZ_NOISE_NOT_VERIFIED");
    $finish;
  end
  initial begin
    if (P_TICK_NS > 0) begin
      #(P_TICK_NS * 40000);
      $fatal(1, "bounded source-control component watchdog");
    end
  end
endmodule
