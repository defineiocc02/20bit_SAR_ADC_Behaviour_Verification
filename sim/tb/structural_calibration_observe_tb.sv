`timescale 1ns/1fs
// Actual structural SAR core connected to a static, fixed-mismatch charge
// plant. The plant's unit weights never enter the observation packet or fit.
// Input references and the quantized backend comparator result are saved by ID.
// This is a functional behavioral test, not an analog or timing signoff.
module structural_calibration_observe_tb;
  logic clk = 0, rst_n = 0, cfg_wr = 0, cfg_validate = 0, cfg_clear = 0;
  always #0.78125 clk = ~clk; // 640 MHz / 16 = 40 MS/s, functional only
  logic [15:0] cfg_addr = 0;
  logic [63:0] cfg_wdata = 0;
  wire cfg_ready;
  wire [3:0] phase;
  wire [1:0][8:0] coarse_trial;
  wire [11:0] fine_trial;
  wire [3:0] acquisition_rails;
  wire [17:0] selected;
  wire [17:0][62:0] main_sw;
  wire [17:0][7:0] sub_sw;
  wire [17:0][3:0] dither_sw;
  wire sw_valid, obs_valid, dout_valid, dout_bad;
  wire [747:0] obs_data;
  wire [19:0] dout;
  wire [31:0] dout_id;
  wire [4:0] dout_flags;
  logic ra_fault = 0;
  integer coarse_target = 256, fine_target = 2048;
  wire [1:0] coarse_ge = {coarse_target >= int'(coarse_trial[1]),
                           coarse_target >= int'(coarse_trial[0])};
  wire fine_ge = fine_target >= int'(fine_trial);
  logic [6:0] flash_therm = 0;
  logic [47:0] load_weights [0:1277];
  logic signed [63:0] load_offset = 0;
  integer requested = 128, train_mode = 1, fault_id = -1, chip_seed = 42, check_clear = 0;
  integer obs_count = 0, out_count = 0, ref_count = 0;
  integer obs_fd, ref_fd, out_fd;
  integer next_fine = 2048;
  integer frame = -1;
  integer q;
  logic [31:0] stimulus_state = 32'hb7e15163;
  logic [31:0] noise_state = 32'h6a09e667;
  real x_now, fine_voltage, nominal_dac, desired_fine;
  real w, sampled, dac;
  real noise_rms_uv = 0.0;
  string outdir, weights_path, offset_path;

  sar20_digital_core #(.P_CAL_OBSERVE(1)) dut (
    .clk(clk), .rst_n(rst_n), .flash_therm(flash_therm), .flash_valid(1'b1),
    .coarse_cmp_valid(2'b11), .coarse_cmp_ge(coarse_ge),
    .fine_cmp_valid(1'b1), .fine_cmp_ge(fine_ge),
    .analog_phase(phase), .quiet_sample(), .tp_clock(), .ra_az(), .ra_amplify(),
    .ref_precharge(), .ref_accurate(), .acquiring_mask(), .converting_mask(),
    .aux_charge_enable(), .hold_low_enable(), .coarse_compare_enable(),
    .coarse_acquire_enable(), .flash_acquire_enable(), .fine_acquire_enable(),
    .coarse_trial(coarse_trial), .quantizer_dither(), .acquisition_dither_rails(acquisition_rails),
    .fine_trial(fine_trial), .fine_compare_enable(), .flash_sample(),
    .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata), .cfg_rdata(),
    .cfg_validate(cfg_validate), .cfg_clear_valid(cfg_clear), .cfg_ready(cfg_ready),
    .sadc_code(9'd0), .sadc_rdy(1'b0), .adc2_code(12'd0), .adc2_rdy(1'b0),
    .ra_sat(ra_fault), .rdac_ovf(1'b0), .adc2_over(1'b0), .inj_q(64'sd0),
    .slice_sel(selected), .main_sw(main_sw), .sub_sw(sub_sw), .dither_sw(dither_sw),
    .sw_valid(sw_valid), .dout(dout), .dout_valid(dout_valid), .clip_low(), .clip_high(),
    .analog_ovf(), .acc_ovf(), .status_word(), .dout_sample_id(dout_id),
    .dout_flags(dout_flags), .cal_obs_valid(obs_valid), .cal_obs_data(obs_data),
    .dout_analog_bad(dout_bad)
  );

  // A deterministic physical fixture. The distribution is deliberately known
  // only inside the plant; the digital fitting input contains no such values.
  function automatic real physical_weight(input int s, input int u);
    int unsigned h;
    real base, epsilon;
    h = 32'(s * 2654435761 + u * 2246822519 + chip_seed * 3266489917);
    h = (h ^ (h >> 16)) * 32'h7feb352d;
    h = (h ^ (h >> 15)) * 32'h846ca68b;
    h = h ^ (h >> 16);
    epsilon = (real'(h % 2001) - 1000.0) / 1000000.0;
    base = u < 63 ? 0.0625 : 0.0078125;
    return base * (1.0 + epsilon);
  endfunction

  task automatic write_cfg(input logic [15:0] addr, input logic [63:0] value);
    @(negedge clk); #0.01; cfg_addr = addr; cfg_wdata = value; cfg_wr = 1;
    @(negedge clk); #0.01; cfg_wr = 0;
  endtask

  // Values are recorded at actual reconstructor acceptance. The producer has
  // no backpressure; this testbench consumes each complete parallel packet.
  always @(posedge clk) begin
    if (rst_n && obs_valid && obs_count < requested) begin
      $fwrite(obs_fd, "%0187h\n", obs_data);
      obs_count++;
    end
  end

  always @(negedge clk) begin
    if (rst_n && cfg_ready && dout_valid && out_count < requested) begin
      $fwrite(out_fd, "%0d,%0d,%0d,%0d\n", dout_id, dout, dout_flags, dout_bad);
      out_count++;
    end
  end

  initial begin
    if (!$value$plusargs("outdir=%s", outdir)) outdir = ".";
    if (!$value$plusargs("samples=%d", requested)) requested = 128;
    if (!$value$plusargs("train=%d", train_mode)) train_mode = 1;
    if (!$value$plusargs("fault_id=%d", fault_id)) fault_id = -1;
    if (!$value$plusargs("chip_seed=%d", chip_seed)) chip_seed = 42;
    if (!$value$plusargs("check_clear=%d", check_clear)) check_clear = 0;
    if (!$value$plusargs("noise_rms_uv=%f", noise_rms_uv)) noise_rms_uv = 0.0;
    if (requested < 1 || requested > 20000) $fatal(1, "unsupported sample count");
    if (noise_rms_uv < 0.0) $fatal(1, "negative noise RMS");
    if (train_mode == 0) stimulus_state = 32'h243f6a88;
    noise_state = 32'h6a09e667 ^ 32'(chip_seed) ^ (train_mode == 0 ? 32'hbb67ae85 : 32'h3c6ef372);
    for (int s = 0; s < 18; s++)
      for (int u = 0; u < 71; u++)
        load_weights[s*71+u] = u < 63 ? 48'd67108864 : 48'd8388608;
    if ($value$plusargs("weights=%s", weights_path)) $readmemh(weights_path, load_weights);
    if ($value$plusargs("offset=%s", offset_path)) begin
      int offset_fd, parsed;
      offset_fd = $fopen(offset_path, "r");
      if (offset_fd == 0) $fatal(1, "cannot open offset coefficient");
      parsed = $fscanf(offset_fd, "%h", load_offset);
      if (parsed != 1) $fatal(1, "invalid offset coefficient");
      $fclose(offset_fd);
    end
    obs_fd = $fopen({outdir,"/observations.hex"}, "w");
    ref_fd = $fopen({outdir,"/reference.csv"}, "w");
    out_fd = $fopen({outdir,"/outputs.csv"}, "w");
    if (obs_fd == 0 || ref_fd == 0 || out_fd == 0) $fatal(1, "cannot open capture files");
    $fwrite(ref_fd, "sample_id,input_v\n");
    $fwrite(out_fd, "sample_id,code,flags,analog_bad\n");
    repeat (3) @(negedge clk); #0.01; rst_n = 1;
    for (int s = 0; s < 18; s++) begin
      write_cfg(16'(16'h2000 + s*256), 64'd0);
      for (int u = 0; u < 71; u++) write_cfg(16'(u*8), 64'(load_weights[s*71+u]));
    end
    write_cfg(16'h1000, 64'(load_offset));
    write_cfg(16'h1008, -64'sd53687091);
    write_cfg(16'h1010, 64'sd590558003);
    write_cfg(16'h1018, 64'd7); // DEM + bridge + sampling dither
    repeat (4) @(negedge clk); #0.01; cfg_validate = 1;
    @(negedge clk); #0.01; cfg_validate = 0;
    if (!cfg_ready) $fatal(1, "calibration test configuration failed");
    while (out_count < requested) begin
      if (phase == 0) begin
        frame++;
        fine_target = next_fine;
        ra_fault = (fault_id >= 0 && dut.u_structure.current_id == 32'(fault_id));
        stimulus_state = stimulus_state * 32'd1664525 + 32'd1013904223;
        coarse_target = 16 + int'(stimulus_state % 480);
        stimulus_state = stimulus_state * 32'd1664525 + 32'd1013904223;
        desired_fine = 0.03 + real'(stimulus_state % 3300) / 10000.0;
        nominal_dac = -3.0 + 6.0 * real'(coarse_target) / 512.0;
        // Sampling dither is added to the RDAC code and cancels its nominal
        // injected charge; the independent quantizer sees the attenuated input.
        x_now = (32.0 * nominal_dac + desired_fine) / 31.75;
        flash_therm = 7'((1 << (coarse_target >> 6)) - 1);
      end
      if (phase == 9 && dut.u_structure.conv_valid && sw_valid) begin
        fine_voltage = 0.0003;
        if ($countones(selected) != 8) $fatal(1, "wrong physical slice count");
        for (int s = 0; s < 18; s++) if (selected[s]) begin
          for (int u = 0; u < 71; u++) begin
            w = physical_weight(s, u);
            if (u < 63) dac = main_sw[s][u] ? 3.0 : -3.0;
            else dac = sub_sw[s][u-63] ? 3.0 : -3.0;
            sampled = u >= 67 ? (dither_sw[s][u-67] ? 3.0 : -3.0) : x_now;
            fine_voltage += w * (sampled - dac);
          end
        end
        // Independent uniform output-equivalent noise. Its specified RMS is
        // input-referred and scaled by the assumed gain of 32; this is a
        // stress fixture, not a measured analog-noise spectrum.
        noise_state = noise_state * 32'd1664525 + 32'd1013904223;
        fine_voltage += (2.0 * real'(noise_state) / 4294967295.0 - 1.0) *
            noise_rms_uv * 1.0e-6 * 32.0 * 1.7320508075688772;
        q = $rtoi($floor((fine_voltage + 0.0375) * 4096.0 / 0.45));
        if (q < 0 || q > 4095) $fatal(1, "plant ADC2 outside range id=%0d voltage=%g", dut.u_structure.current_id, fine_voltage);
        next_fine = q;
        if (ref_count < requested) begin
          $fwrite(ref_fd, "%0d,%.17g\n", dut.u_structure.current_id, x_now);
          ref_count++;
        end
      end
      @(negedge clk); #0.01;
    end
    $fclose(obs_fd); $fclose(ref_fd); $fclose(out_fd);
    if (obs_count != requested || out_count != requested || ref_count < requested)
      $fatal(1, "incomplete capture obs=%0d out=%0d ref=%0d", obs_count, out_count, ref_count);
    if (check_clear != 0) begin
      while (!(phase == 15 && obs_valid)) begin @(negedge clk); #0.01; end
      cfg_clear = 1; #0.01;
      if (obs_valid) $fatal(1, "observer accepted a same-edge clear");
      @(negedge clk); #0.01; cfg_clear = 0;
      repeat (24) begin
        @(negedge clk); #0.01;
        if (obs_valid || dout_valid) $fatal(1, "old epoch escaped after clear");
      end
      $display("CAL_OBSERVER_CLEAR_PASS");
    end
    $display("STRUCTURAL_CALIBRATION_OBSERVE_COMPLETE samples=%0d refs=%0d outputs=%0d",obs_count,ref_count,out_count);
    $finish;
  end
  initial begin #20000000; $fatal(1, "calibration observation watchdog"); end
endmodule
