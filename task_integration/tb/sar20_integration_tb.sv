// 数字闭环夹具。外部重构按本笔 T8 逐笔去扰，ADC2、失配和 AZ 噪声不在此夹具内。
`timescale 1ns/1ps
module sar20_integration_tb;
  logic clk_i = 0;
  logic rst_ni, clear_i, enable_i, advance_i, advance_ready_o;
  logic [17:0] eligible_i;
  logic shuffle_i, dem_enable_i, bridge_enable_i, dither_enable_i, caaz_enable_i;
  logic seed_load_i, seed_error_o, slot_accepted_o, stall_o;
  logic [31:0] slice_seed_i, dem_seed_i;
  logic [30:0] dither_seed_i;
  logic acq_valid_o, acq_chop_neg_o, acq_az_capture_o, acq_az_hold_o;
  logic [31:0] acq_sample_id_o;
  logic [39:0] acq_slice_ids_o, acq_sample_sub_high_o;
  logic [5:0] acq_word_o;
  logic [4:0] acq_lower_eighths_o;
  logic [30:0] acq_az_pair_id_o;
  logic conv_ready_o;
  logic [31:0] conv_sample_id_o;
  logic [39:0] conv_slice_ids_o, conv_sample_sub_high_o;
  logic [5:0] conv_word_o, conv_total_eighths_o;
  logic code_valid_i, mask_valid_o, code_error_o;
  logic [8:0] code_i, command_code_o;
  logic [31:0] code_sample_id_i, bridge_correction_q32_o;
  logic [135:0] main_mask_o;
  logic [39:0] sub_mask_o;
  logic recon_valid_i, recon_ready_o, recon_bad_i, recon_chop_neg_i, recon_az_ok_i;
  logic [31:0] recon_sample_id_i;
  logic signed [19:0] recon_code_i, out_code_o;
  logic [30:0] recon_az_pair_id_i, out_az_pair_id_o;
  logic recon_error_o, out_valid_o, out_pair_o, fault_o, fault_pulse_o, alignment_fault_o;
  logic [31:0] out_sample_id_o, out_first_sample_id_o;
  logic signed [20:0] out_difference_o;
  logic [2:0] caaz_fault_code_o;

  int cycles, checks, steady, bypass, pairs, mismatched_dither_pairs, rejections;
  int late_returns, command_cases, headroom_failures, macro_faults;
  logic [30:0] rng_ref;

  sar20_digital_top dut (.*);

  task automatic step_clock();
    #1;
    clk_i = 1;
    #1;
    clk_i = 0;
    cycles++;
  endtask

  task automatic check(input bit condition, input string message);
    checks++;
    if (!condition) $fatal(1, "cycle=%0d: %s", cycles, message);
  endtask

  function automatic int upper(input logic [5:0] word);
    return 4 * int'(word[5]) + int'(word[3]);
  endfunction

  function automatic int total(input logic [5:0] word);
    return 8 * (upper(word) + 2 * int'(word[4])) + int'(word[2:0]);
  endfunction

  function automatic logic [39:0] sample_mask(input logic [5:0] word);
    logic [39:0] result;
    int level;
    result = '0;
    for (int slot = 0; slot < 8; slot++) begin
      level = total(word) / 8 + int'(slot < (total(word) % 8));
      for (int bit_idx = 0; bit_idx < 4; bit_idx++) begin
        result[5*slot + bit_idx] = 1'((level / (8 >> bit_idx)) % 2);
      end
    end
    return result;
  endfunction

  function automatic logic [17:0] physical_mask(input logic [39:0] ids);
    logic [17:0] result;
    result = '0;
    for (int slot = 0; slot < 8; slot++) result |= 18'd1 << ids[5*slot+:5];
    return result;
  endfunction

  task automatic next_word(input bit active, output logic [5:0] word);
    bit feedback;
    word = '0;
    if (active) begin
      for (int b = 5; b >= 0; b--) begin
        feedback = rng_ref[30] != rng_ref[27];
        word[b] = feedback;
        rng_ref = (rng_ref << 1) | 31'(feedback);
      end
    end
  endtask

  task automatic reset(input bit chopped);
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
    slice_seed_i = 32'hcabe_2333;
    dem_seed_i = 32'h44bb_abab;
    dither_seed_i = 31'h6aaa_7f10;
    code_valid_i = 0;
    code_i = '0;
    code_sample_id_i = '0;
    recon_valid_i = 0;
    recon_bad_i = 0;
    recon_sample_id_i = '0;
    recon_code_i = '0;
    recon_chop_neg_i = 0;
    recon_az_ok_i = 0;
    recon_az_pair_id_i = '0;
    rng_ref = 31'h5a17_09c3;
    step_clock();
    rst_ni = 1;
    #1;
    check(advance_ready_o && !fault_o && !acq_valid_o && !conv_ready_o && !out_valid_o,
          "reset suppresses stale valid");
  endtask

  // 请求 N、调度发布 N+1、转换上下文 N+2。N+3 采样标签由调用方核对。
  task automatic dispatch();
    #1;
    check(advance_ready_o && !fault_o, "request ready");
    advance_i = 1;
    step_clock();
    advance_i = 0;
    check(!advance_ready_o && !slot_accepted_o && !acq_valid_o, "N request in flight");
    step_clock();
    check(slot_accepted_o && !stall_o && !acq_valid_o, "N+1 schedule response");
    step_clock();
    check(!slot_accepted_o && !acq_valid_o, "N+2 acquisition control still withheld");
  endtask

  task automatic check_command(input int adjusted, input bit bridge);
    int main_total, sub_total, expected_main, expected_sub;
    int bridge_fraction;
    logic [31:0] expected_correction;
    main_total = 0;
    sub_total = 0;
    for (int slot = 0; slot < 8; slot++) begin
      for (int unit_idx = 0; unit_idx < 17; unit_idx++) begin
        main_total += (unit_idx < 15 ? 2 : 1) * int'(main_mask_o[17*slot + unit_idx]);
      end
      for (int unit_idx = 0; unit_idx < 4; unit_idx++) begin
        sub_total += (8 >> unit_idx) * int'(sub_mask_o[5*slot + unit_idx]);
      end
      check(!sub_mask_o[5*slot + 4], "dummy remains on low reference");
    end
    expected_main = 16 * (adjusted / 32) + (bridge ? 2 * ((adjusted % 32) / 4)
                                                 : 8 * ((adjusted % 32) / 16));
    expected_sub = 8 * (adjusted % (bridge ? 4 : 16));
    expected_correction = 0;
    if (bridge) begin
      // 正桥补偿由 ((k >> 2) & 3) × 2^32 / 1088 最近舍入。
      bridge_fraction = (adjusted / 4) % 4;
      expected_correction = 32'((64'(bridge_fraction) * 64'd4294967296 + 544) / 1088);
    end
    check(mask_valid_o && !code_error_o && int'(command_code_o) == adjusted,
          "registered final coarse command");
    check(main_total == expected_main && sub_total == expected_sub &&
          bridge_correction_q32_o == expected_correction, "same-ID DEM charge conservation");
    command_cases++;
  endtask

  task automatic steady_stream(input bit chopped, input int count);
    logic [5:0] word, prev_word, previous_even_word;
    logic [39:0] prev_ids;
    bit prev_bridge;
    int raw_id, c, x, measured, corrected, even_code, diff_ref, launch_cycle;
    reset(chopped);
    prev_word = '0;
    previous_even_word = '0;
    prev_ids = '0;
    prev_bridge = 0;
    even_code = 0;
    launch_cycle = cycles;
    for (int sample_idx = 0; sample_idx <= count; sample_idx++) begin
      check(cycles == launch_cycle + 16 * sample_idx, "10 MS/s cadence at 160 MHz");
      bridge_enable_i = (sample_idx % 7 != 2);
      dem_enable_i = (sample_idx % 5 != 3);
      dither_enable_i = (sample_idx % 13 != 4);
      next_word(dither_enable_i, word);
      dispatch();
      raw_id = sample_idx - 1;
      c = 17 + (sample_idx * 23) % 477;
      if (sample_idx > 0) begin
        check(conv_ready_o && conv_sample_id_o == 32'(raw_id) && conv_slice_ids_o == prev_ids,
              "previous eight slices and ID became conversion group");
        check(conv_word_o == prev_word && int'(conv_total_eighths_o) == total(prev_word) &&
              conv_sample_sub_high_o == sample_mask(prev_word), "same-ID conversion dither");
        code_valid_i = 1;
        code_sample_id_i = 32'(raw_id);
        code_i = 9'(c);
      end else begin
        check(!conv_ready_o, "first request primes acquisition only");
      end
      step_clock();
      code_valid_i = 0;
      check(acq_valid_o && acq_sample_id_o == 32'(sample_idx) && acq_word_o == word,
            "chopper tag and PRBS31 word aligned on N+3");
      check(int'(acq_lower_eighths_o) == (16 * int'(word[4]) + int'(word[2:0])) &&
            acq_sample_sub_high_o == sample_mask(word), "SADC and eight RDAC sample masks");
      check($countones(physical_mask(acq_slice_ids_o)) == 8, "exactly eight legal slices");
      check((physical_mask(acq_slice_ids_o) & physical_mask(prev_ids)) == 0 || sample_idx == 0,
            "acquisition and conversion slices disjoint");
      check(acq_chop_neg_o == (chopped && 1'(sample_idx)) &&
            acq_az_capture_o == (chopped && !1'(sample_idx)) &&
            acq_az_hold_o == (chopped && 1'(sample_idx)) &&
            acq_az_pair_id_o == (chopped ? 31'(sample_idx / 2) : 31'd0), "macro AZ control");
      if (sample_idx > 0) check_command(c + upper(prev_word), prev_bridge);
      repeat (11) begin
        step_clock();
        check(!out_valid_o && !acq_valid_o && !mask_valid_o, "valid pulses and idle gaps");
      end
      if (sample_idx > 0) begin
        check(!advance_ready_o && conv_ready_o && conv_sample_id_o == 32'(raw_id),
              "unfinished conversion is held through N+14");
        x = (raw_id * 883) % 800011 - 399991;
        // 已知独立注入在夹具中用 11 码/档，先逐笔去扰，再接入斩波坐标。
        measured = ((chopped && 1'(raw_id)) ? -x : x) + 321 + 11 * total(prev_word);
        corrected = measured - 11 * int'(conv_total_eighths_o);
        recon_valid_i = 1;
        recon_sample_id_i = 32'(raw_id);
        recon_code_i = 20'(corrected);
        recon_chop_neg_i = chopped && 1'(raw_id);
        recon_az_pair_id_i = 31'(raw_id / 2);
        recon_az_ok_i = chopped;
        #1;
        check(recon_ready_o, "N+15 same-ID recon can retire");
      end
      step_clock();
      recon_valid_i = 0;
      if (sample_idx > 0) begin
        check(advance_ready_o && !conv_ready_o && !recon_error_o && !fault_o,
              "N+16 request can follow result");
        if (!chopped) begin
          check(out_valid_o && !out_pair_o && int'(out_code_o) == corrected &&
                out_sample_id_o == 32'(raw_id) && out_first_sample_id_o == 32'(raw_id) &&
                out_az_pair_id_o == 0 && int'(out_difference_o) == 2 * corrected, "CAAZ bypass");
          bypass++;
        end else if (!1'(raw_id)) begin
          check(!out_valid_o, "even sample is retained");
          even_code = corrected;
          previous_even_word = prev_word;
        end else begin
          diff_ref = even_code - corrected;
          check(out_valid_o && out_pair_o && int'(out_code_o) == (diff_ref >>> 1) &&
                int'(out_difference_o) == diff_ref && out_sample_id_o == 32'(raw_id) &&
                out_first_sample_id_o == 32'(raw_id - 1) && out_az_pair_id_o == 31'(raw_id / 2),
                "odd sample forms exact signed half-difference after per-sample subtraction");
          pairs++;
          mismatched_dither_pairs += int'(prev_word != previous_even_word);
        end
        if (chopped) steady++;
      end
      prev_ids = acq_slice_ids_o;
      prev_word = word;
      prev_bridge = bridge_enable_i;
    end
  endtask

  task automatic reject_and_backpressure();
    reset(0);
    eligible_i = 18'h0007f;
    advance_i = 1;
    step_clock();
    advance_i = 0;
    step_clock();
    check(stall_o && !slot_accepted_o && !acq_valid_o, "fewer than eight eligible stalls");
    step_clock();
    check(advance_ready_o, "stall retires request");
    eligible_i = 18'h3ffff;
    seed_load_i = 1;
    advance_i = 1;
    step_clock();
    seed_load_i = 0;
    advance_i = 0;
    step_clock();
    check(seed_error_o && !slot_accepted_o && !acq_valid_o, "same-edge seed conflict");
    step_clock();
    dither_seed_i = 31'h4000_0000;
    seed_load_i = 1;
    step_clock();
    seed_load_i = 0;
    repeat (3) step_clock();
    dispatch();
    step_clock();
    check(acq_valid_o && acq_sample_id_o == 0 && upper(acq_word_o) > 0, "upper injection fixture");
    dispatch();
    check(conv_ready_o && conv_sample_id_o == 0, "ID0 conversion alive");
    recon_valid_i = 1;
    recon_sample_id_i = 32'd8;
    code_valid_i = 1;
    code_sample_id_i = 32'd9;
    code_i = 9'd123;
    step_clock();
    check(recon_error_o && code_error_o && !mask_valid_o && conv_ready_o, "wrong IDs rejected");
    rejections += 2;
    recon_sample_id_i = 0;
    code_sample_id_i = 0;
    code_i = 9'd511;
    step_clock();
    check(recon_error_o && code_error_o && !mask_valid_o && conv_ready_o,
          "early recon and coarse overflow rejected");
    rejections++;
    headroom_failures++;
    recon_valid_i = 0;
    code_i = 9'd123;
    step_clock();
    check_command(123 + upper(conv_word_o), 1);
    step_clock();
    check(code_error_o && !mask_valid_o, "second coarse command rejected");
    rejections++;
    code_valid_i = 0;
    advance_i = 1;
    repeat (20) begin
      step_clock();
      check(!advance_ready_o && !slot_accepted_o && conv_ready_o && conv_sample_id_o == 0,
            "late external recon holds group and refuses next request");
    end
    advance_i = 0;
    recon_valid_i = 1;
    recon_code_i = -20'sd137;
    step_clock();
    recon_valid_i = 0;
    check(out_valid_o && out_code_o == -20'sd137 && advance_ready_o && !conv_ready_o,
          "late result retires without ID loss");
    late_returns++;
    advance_i = 1;
    step_clock();
    advance_i = 0;
    clear_i = 1;
    step_clock();
    clear_i = 0;
    #1;
    check(advance_ready_o && !conv_ready_o && !acq_valid_o && !out_valid_o,
          "clear cancels a pending dispatch");
    dispatch();
    step_clock();
    check(acq_valid_o && acq_sample_id_o == 0, "clear starts a fresh epoch");
  endtask

  task automatic macro_failure(input bit bad);
    reset(1);
    dispatch();
    step_clock();
    check(acq_valid_o && acq_az_capture_o, "first AZ capture");
    dispatch();
    code_valid_i = 1;
    code_sample_id_i = 0;
    code_i = 9'd50;
    if (bad) begin
      recon_valid_i = 1;
      recon_bad_i = 1;
      recon_sample_id_i = 0;
    end
    step_clock();
    code_valid_i = 0;
    if (bad) begin
      check(code_error_o && !mask_valid_o && caaz_fault_code_o == 3'd4,
            "explicit bad sample retires before command and faults");
    end else begin
      check(mask_valid_o && acq_valid_o && acq_az_hold_o, "subsequent AZ hold command");
      recon_valid_i = 1;
      recon_sample_id_i = 0;
      recon_az_ok_i = 0;
      step_clock();
      check(caaz_fault_code_o == 3'd6, "macro capture failure faults");
    end
    recon_valid_i = 0;
    check(fault_o && fault_pulse_o && !out_valid_o && !advance_ready_o && !alignment_fault_o,
          "fault suppresses advancement and output");
    macro_faults++;
  endtask

  initial begin
    steady_stream(0, 64);
    steady_stream(1, 4096);
    reject_and_backpressure();
    macro_failure(0);
    macro_failure(1);
    $display("SAR20_INTEGRATION_COMPLETE cycles=%0d checks=%0d steady=%0d bypass=%0d pairs=%0d mismatched_dither_pairs=%0d rejections=%0d late_returns=%0d command_cases=%0d headroom_failures=%0d macro_faults=%0d",
             cycles, checks, steady, bypass, pairs, mismatched_dither_pairs, rejections,
             late_returns, command_cases, headroom_failures, macro_faults);
    $finish;
  end
endmodule
