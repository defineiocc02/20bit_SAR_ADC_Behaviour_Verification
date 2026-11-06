// 重放八片失配模型的开关和 ADC2 码，独立整数参考逐笔验收片上重构与 CAAZ。
`timescale 1ns/1ps
module sar20_lms_integration_tb;
  logic                clk_i;
  logic                rst_ni;
  logic                clear_i;
  logic                enable_i;
  logic                advance_i;
  logic                advance_ready_o;
  logic         [17:0] eligible_i;
  logic                shuffle_i;
  logic                dem_enable_i;
  logic                bridge_enable_i;
  logic                dither_enable_i;
  logic                caaz_enable_i;
  logic                seed_load_i;
  logic         [31:0] slice_seed_i;
  logic         [31:0] dem_seed_i;
  logic         [30:0] dither_seed_i;
  logic                seed_error_o;
  logic                slot_accepted_o;
  logic                stall_o;
  logic                acq_valid_o;
  logic         [31:0] acq_sample_id_o;
  logic         [39:0] acq_slice_ids_o;
  logic          [5:0] acq_word_o;
  logic          [4:0] acq_lower_eighths_o;
  logic         [39:0] acq_sample_sub_high_o;
  logic                acq_chop_neg_o;
  logic                acq_az_capture_o;
  logic                acq_az_hold_o;
  logic         [30:0] acq_az_pair_id_o;
  logic                conv_ready_o;
  logic         [31:0] conv_sample_id_o;
  logic         [39:0] conv_slice_ids_o;
  logic          [5:0] conv_word_o;
  logic          [5:0] conv_total_eighths_o;
  logic         [39:0] conv_sample_sub_high_o;
  logic                code_valid_i;
  logic          [8:0] code_i;
  logic         [31:0] code_sample_id_i;
  logic                mask_valid_o;
  logic                code_error_o;
  logic          [8:0] command_code_o;
  logic        [135:0] main_mask_o;
  logic         [39:0] sub_mask_o;
  logic         [31:0] bridge_correction_q32_o;
  logic                out_valid_o;
  logic signed  [19:0] out_code_o;
  logic         [31:0] out_sample_id_o;
  logic         [31:0] out_first_sample_id_o;
  logic         [30:0] out_az_pair_id_o;
  logic                out_pair_o;
  logic signed  [20:0] out_difference_o;
  logic                fault_o;
  logic                fault_pulse_o;
  logic          [2:0] caaz_fault_code_o;
  logic                alignment_fault_o;
  logic                cfg_start_i;
  logic                cfg_start_ready_o;
  logic                cfg_valid_i;
  logic                cfg_ready_o;
  logic         [25:0] cfg_data_i;
  logic                configured_o;
  logic                cfg_error_o;
  logic                adc2_valid_i;
  logic                adc2_ready_o;
  logic         [31:0] adc2_sample_id_i;
  logic         [11:0] adc2_code_i;
  logic                adc2_bad_i;
  logic                adc2_chop_neg_i;
  logic         [30:0] adc2_az_pair_id_i;
  logic                adc2_az_ok_i;
  logic                adc2_error_o;
  logic          [1:0] recon_inflight_o;

  localparam int Samples = 4096;
  typedef struct packed {
    logic [5:0] word;
    logic [39:0] ids;
    logic [8:0] coarse;
    logic [8:0] command_code;
    logic [135:0] main;
    logic [39:0] sub;
    logic [39:0] sampled;
    logic [11:0] adc2;
    logic signed [19:0] golden_code;
  } sample_t;

  sample_t current, previous;
  int expected_code [Samples];
  logic [5:0] expected_word [Samples];
  int accept_cycle [Samples];
  int cycles, checks, adc2_captures, bypass, pairs, peak_inflight;
  int signed_negative_pairs, mixed_dither_pairs, rejects, macro_faults;
  int expected_output_id;
  bit scoreboarding, chopped;

  sar20_digital_lms_top dut (.*);

  task automatic check(input bit condition, input string message);
    checks++;
    if (!condition) $fatal(1, "cycle=%0d: %s", cycles, message);
  endtask

  task automatic step_clock();
    int id, diff_ref;
    #1;
    clk_i = 1;
    #1;
    clk_i = 0;
    cycles++;
    peak_inflight = (int'(recon_inflight_o) > peak_inflight) ? int'(recon_inflight_o) : peak_inflight;
    if (scoreboarding && out_valid_o) begin
      id = int'(out_sample_id_o);
      check(id == expected_output_id && id < Samples && !fault_o, "exact output order");
      check(cycles - accept_cycle[id] == 23, "ADC2 handoff to CAAZ publication is 23 cycles");
      if (chopped) begin
        diff_ref = expected_code[id - 1] - expected_code[id];
        check(out_pair_o && int'(out_code_o) == (diff_ref >>> 1) &&
              int'(out_difference_o) == diff_ref && out_first_sample_id_o == 32'(id - 1) &&
              out_az_pair_id_o == 31'(id / 2), "pair matches independent calibrated code reference");
        signed_negative_pairs += int'(diff_ref < 0);
        mixed_dither_pairs += int'(expected_word[id] != expected_word[id - 1]);
        pairs++;
        expected_output_id += 2;
      end else begin
        check(!out_pair_o && int'(out_code_o) == expected_code[id] &&
              int'(out_difference_o) == 2 * expected_code[id] && out_az_pair_id_o == 0 &&
              out_first_sample_id_o == 32'(id), "bypass matches independent calibrated code");
        bypass++;
        expected_output_id++;
      end
    end
  endtask

  task automatic reset();
    clk_i = 0;
    rst_ni = 0;
    clear_i = 0;
    enable_i = 1;
    advance_i = 0;
    eligible_i = 18'h3ffff;
    shuffle_i = 1;
    dem_enable_i = 1;
    bridge_enable_i = 1;
    dither_enable_i = 1;
    caaz_enable_i = chopped;
    seed_load_i = 0;
    slice_seed_i = 32'h243f_6a88;
    dem_seed_i = 32'h9e37_79b9;
    dither_seed_i = 31'h5a17_09c3;
    code_valid_i = 0;
    code_i = '0;
    code_sample_id_i = '0;
    cfg_start_i = 0;
    cfg_valid_i = 0;
    cfg_data_i = '0;
    adc2_valid_i = 0;
    adc2_sample_id_i = '0;
    adc2_code_i = '0;
    adc2_bad_i = 0;
    adc2_chop_neg_i = 0;
    adc2_az_pair_id_i = '0;
    adc2_az_ok_i = 0;
    step_clock();
    rst_ni = 1;
    #1;
    check(!configured_o && !advance_ready_o, "unconfigured core cannot acquire");
  endtask

  task automatic load_coeff(input string path);
    int file_handle, rc;
    file_handle = $fopen(path, "r");
    check(file_handle != 0 && cfg_start_ready_o, "read coefficient fixture");
    cfg_start_i = 1;
    step_clock();
    cfg_start_i = 0;
    cfg_valid_i = 1;
    for (int i = 0; i < 396; i++) begin
      rc = $fscanf(file_handle, "%h", cfg_data_i);
      check(rc == 1 && cfg_ready_o, "configuration word available");
      step_clock();
      check(!cfg_error_o, "configuration word accepted");
    end
    cfg_valid_i = 0;
    $fclose(file_handle);
    check(configured_o && !cfg_ready_o && advance_ready_o, "complete coefficient bank");
    seed_load_i = 1;
    step_clock();
    seed_load_i = 0;
    repeat (3) step_clock();
    check(!seed_error_o, "reloaded capture seed");
  endtask

  task automatic begin_slot();
    #1;
    check(advance_ready_o && !fault_o, "16-cycle dispatch admission");
    advance_i = 1;
    step_clock();
    advance_i = 0;
    step_clock();
    check(slot_accepted_o && !stall_o, "schedule response");
    step_clock();
  endtask

  task automatic replay(input string cfg_path, input string data_path, input bit pair_mode);
    int data_handle, rc, start_cycle, raw_id;
    scoreboarding = 0;
    chopped = pair_mode;
    reset();
    load_coeff(cfg_path);
    data_handle = $fopen(data_path, "r");
    check(data_handle != 0, "read sample fixture");
    scoreboarding = 1;
    expected_output_id = chopped ? 1 : 0;
    start_cycle = cycles;
    for (int s = 0; s <= Samples; s++) begin
      check(cycles == start_cycle + s * 16, "one request each 16 ticks");
      if (s < Samples) begin
        rc = $fscanf(data_handle, "%h", current);
        check(rc == 1, "sample fixture record");
        expected_code[s] = int'(current.golden_code);
        expected_word[s] = current.word;
      end
      raw_id = s - 1;
      begin_slot();
      if (s > 0) begin
        check(conv_ready_o && conv_sample_id_o == 32'(raw_id) && conv_slice_ids_o == previous.ids &&
              conv_word_o == previous.word && conv_total_eighths_o == previous.word &&
              conv_sample_sub_high_o == previous.sampled, "conversion descriptor matches fixture");
        code_valid_i = 1;
        code_sample_id_i = 32'(raw_id);
        code_i = previous.coarse;
      end
      step_clock();
      code_valid_i = 0;
      check(acq_valid_o && acq_sample_id_o == 32'(s) &&
            acq_chop_neg_o == (chopped && 1'(s)) && acq_az_hold_o == (chopped && 1'(s)) &&
            acq_az_capture_o == (chopped && !1'(s)) &&
            acq_az_pair_id_o == (chopped ? 31'(s / 2) : 31'd0), "aligned acquisition controls");
      if (s < Samples) begin
        check(acq_slice_ids_o == current.ids && acq_word_o == current.word &&
              acq_sample_sub_high_o == current.sampled &&
              acq_lower_eighths_o == {current.word[4], 1'b0, current.word[2:0]},
              "actual sampled bottom plates and SADC command match fixture");
      end
      if (s > 0) begin
        check(mask_valid_o && !code_error_o && command_code_o == previous.command_code &&
              main_mask_o == previous.main && sub_mask_o == previous.sub,
              "coarse and both conversion masks match actual fixture");
        check(bridge_correction_q32_o ==
              32'((64'(command_code_o[3:2]) * 64'd4294967296 + 64'd544) / 64'd1088),
              "bridge metadata provided while actual masks drive the weights");
      end
      if (s == 2) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'hdeadbeef;
        cfg_start_i = 1;
        #1;
        check(!adc2_ready_o && !cfg_start_ready_o, "reject wrong ID and runtime reconfiguration");
        step_clock();
        check(adc2_error_o && cfg_error_o && configured_o, "reported without disturbing bank");
        adc2_valid_i = 0;
        cfg_start_i = 0;
        rejects += 2;
        repeat (10) step_clock();
      end else begin
        repeat (11) step_clock();
      end
      if (s > 0) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'(raw_id);
        adc2_code_i = previous.adc2;
        adc2_chop_neg_i = chopped && 1'(raw_id);
        adc2_az_pair_id_i = 31'(raw_id / 2);
        adc2_az_ok_i = chopped;
        #1;
        check(adc2_ready_o && !advance_ready_o, "N+15 ADC2 handshake before next slot");
        accept_cycle[raw_id] = cycles + 1;
      end
      step_clock();
      adc2_valid_i = 0;
      if (s > 0) begin
        check(advance_ready_o && !conv_ready_o && !adc2_error_o && !alignment_fault_o,
              "conversion released independently of pending reconstruction");
        adc2_captures++;
      end
      previous = current;
    end
    repeat (25) step_clock();
    check(recon_inflight_o == 0 && !fault_o && !alignment_fault_o &&
          expected_output_id == (chopped ? Samples + 1 : Samples), "all results drained");
    scoreboarding = 0;
    $fclose(data_handle);
  endtask

  task automatic bad_macro_result(input bit late);
    clear_i = 1;
    step_clock();
    clear_i = 0;
    #1;
    check(configured_o && !fault_o && recon_inflight_o == 0, "clear preserves complete coefficients");
    begin_slot();
    step_clock();
    begin_slot();
    check(conv_ready_o && conv_sample_id_o == 0, "bad conversion fixture ID0");
    if (late) begin
      step_clock();
      check(acq_valid_o && acq_sample_id_o == 1, "acquisition published before late failure");
      repeat (4) step_clock();
    end
    adc2_valid_i = 1;
    adc2_bad_i = 1;
    adc2_sample_id_i = 0;
    advance_i = 1;
    #1;
    check(adc2_ready_o && !advance_ready_o, "same-ID macro failure can finish before coarse");
    step_clock();
    adc2_valid_i = 0;
    check(!acq_valid_o && !advance_ready_o && !slot_accepted_o && !fault_o,
          "bad ADC2 handoff suppresses new acquisition before the ordered fault returns");
    repeat (25) begin
      step_clock();
      check(!out_valid_o && !advance_ready_o && !slot_accepted_o && !acq_valid_o,
            "pending macro error freezes further admissions and never publishes a code");
    end
    check(fault_o && caaz_fault_code_o == 3'd4 && !alignment_fault_o &&
          !advance_ready_o && !fault_pulse_o && recon_inflight_o == 0, "ADC2 bad status propagated");
    macro_faults++;
    advance_i = 0;
  endtask

  string bypass_coeff, bypass_data, pair_coeff, pair_data;
  initial begin
    if (!$value$plusargs("bypass_coeff=%s", bypass_coeff) ||
        !$value$plusargs("bypass_data=%s", bypass_data) ||
        !$value$plusargs("pair_coeff=%s", pair_coeff) || !$value$plusargs("pair_data=%s", pair_data))
      $fatal(1, "capture and coefficient paths are required");
    replay(bypass_coeff, bypass_data, 0);
    replay(pair_coeff, pair_data, 1);
    bad_macro_result(0);
    bad_macro_result(1);
    $display("SAR20_LMS_INTEGRATION_COMPLETE cycles=%0d checks=%0d adc2_captures=%0d bypass=%0d pairs=%0d peak_inflight=%0d signed_negative_pairs=%0d mixed_dither_pairs=%0d rejects=%0d macro_faults=%0d",
             cycles, checks, adc2_captures, bypass, pairs, peak_inflight,
             signed_negative_pairs, mixed_dither_pairs, rejects, macro_faults);
    $finish;
  end
endmodule
