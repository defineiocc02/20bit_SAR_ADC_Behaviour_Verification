// 带噪 ADC2 归档回放。实际 ADC2 交接、片上重构握手和最终码分别输出观察记录。
`timescale 1ns/1ps
module sar20_joint_noise_tb;
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
    logic signed [19:0] reference_raw;
  } sample_t;

  sample_t current, previous;
  logic previous_chop_neg;
  logic [30:0] previous_az_pair;
  bit observe;
  int trace_file, cycles, checks, adc2_captures, reconstructed, outputs, peak_inflight;
  string coeff_path, vector_path, trace_path;

  sar20_digital_lms_top dut (.*);

  task automatic check(input bit condition, input string message);
    checks++;
    if (!condition) $fatal(1, "cycle=%0d: %s", cycles, message);
  endtask

  task automatic step_clock();
    #1;
    cycles++;
    // 记录沿上真正接收的信号。组合探针只读，不进入被测逻辑。
    if (observe && adc2_valid_i && adc2_ready_o) begin
      $fdisplay(trace_file, "A %0d %0d %0d", cycles, adc2_sample_id_i, adc2_code_i);
      adc2_captures++;
    end
    if (observe && dut.engine_out_valid && dut.engine_out_ready) begin
      $fdisplay(trace_file, "R %0d %0d %0d %0d", cycles, dut.engine_out_id,
                $signed(dut.engine_out_code), dut.engine_out_bad);
      reconstructed++;
    end
    clk_i = 1;
    #1;
    if (observe) begin
      check(!fault_o && !fault_pulse_o && !alignment_fault_o && caaz_fault_code_o == 0 &&
            !adc2_error_o && !cfg_error_o && !seed_error_o, "no unexpected faults during replay");
      if (int'(recon_inflight_o) > peak_inflight) peak_inflight = int'(recon_inflight_o);
      if (out_valid_o) begin
        $fdisplay(trace_file, "O %0d %0d %0d %0d %0d %0d %0d", cycles, out_sample_id_o,
                  out_first_sample_id_o, out_az_pair_id_o, out_pair_o, $signed(out_code_o),
                  $signed(out_difference_o));
        outputs++;
      end
    end
    clk_i = 0;
  endtask

  task automatic initialize();
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
    seed_load_i = 0;
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
    observe = 0;
    step_clock();
    rst_ni = 1;
    #1;
    check(!advance_ready_o && !configured_o, "configuration required before acquisition");
  endtask

  task automatic load_configuration();
    int coeff_file, rc;
    coeff_file = $fopen(coeff_path, "r");
    check(coeff_file != 0 && cfg_start_ready_o, "open frozen coefficient bank");
    cfg_start_i = 1;
    step_clock();
    cfg_start_i = 0;
    cfg_valid_i = 1;
    for (int idx = 0; idx < 396; idx++) begin
      rc = $fscanf(coeff_file, "%h", cfg_data_i);
      check(rc == 1 && cfg_ready_o, "coefficient ready");
      step_clock();
      check(!cfg_error_o, "coefficient accepted");
    end
    cfg_valid_i = 0;
    $fclose(coeff_file);
    check(configured_o && !cfg_ready_o, "complete coefficient bank");
    seed_load_i = 1;
    step_clock();
    seed_load_i = 0;
    repeat (3) step_clock();
    check(!seed_error_o && advance_ready_o, "frozen replay seeds loaded");
  endtask

  task automatic replay();
    int data_file, rc, start_cycle, sample_id;
    data_file = $fopen(vector_path, "r");
    trace_file = $fopen(trace_path, "w");
    check(data_file != 0 && trace_file != 0, "open replay and observation files");
    start_cycle = cycles;
    observe = 1;
    for (int s = 0; s <= Samples; s++) begin
      check(cycles == start_cycle + s * 16 && advance_ready_o, "16-tick dispatch cadence");
      if (s < Samples) begin
        rc = $fscanf(data_file, "%h", current);
        check(rc == 1, "read complete archived sample word");
      end
      advance_i = 1;
      step_clock();
      advance_i = 0;
      step_clock();
      check(slot_accepted_o && !stall_o, "eligible slices selected");
      step_clock();
      sample_id = s - 1;
      if (s > 0) begin
        check(conv_ready_o && conv_sample_id_o == 32'(sample_id) &&
              conv_slice_ids_o == previous.ids && conv_word_o == previous.word &&
              conv_total_eighths_o == previous.word && conv_sample_sub_high_o == previous.sampled,
              "same-ID archived conversion descriptor");
        code_valid_i = 1;
        code_sample_id_i = 32'(sample_id);
        code_i = previous.coarse;
      end else begin
        check(!conv_ready_o, "first acquisition only primes scheduler");
      end
      step_clock();
      code_valid_i = 0;
      check(acq_valid_o && acq_sample_id_o == 32'(s) &&
            acq_chop_neg_o == (caaz_enable_i && 1'(s)) &&
            acq_az_capture_o == (caaz_enable_i && !1'(s)) &&
            acq_az_hold_o == (caaz_enable_i && 1'(s)) &&
            acq_az_pair_id_o == (caaz_enable_i ? 31'(s / 2) : 31'd0), "CAAZ intent follows sample ID");
      if (s < Samples) begin
        check(acq_slice_ids_o == current.ids && acq_word_o == current.word &&
              acq_sample_sub_high_o == current.sampled &&
              acq_lower_eighths_o == {current.word[4], 1'b0, current.word[2:0]},
              "ordered slice IDs and native dither switch masks match archived sample");
      end
      if (s > 0) begin
        check(mask_valid_o && !code_error_o && command_code_o == previous.command_code &&
              main_mask_o == previous.main && sub_mask_o == previous.sub,
              "coarse result and conversion masks match archived sample");
        check(bridge_correction_q32_o ==
              32'((64'(previous.command_code[3:2]) * 64'd4294967296 + 64'd544) / 64'd1088),
              "bridge metadata follows the command");
      end
      repeat (11) step_clock();
      if (s > 0) begin
        adc2_valid_i = 1;
        adc2_sample_id_i = 32'(sample_id);
        adc2_code_i = previous.adc2;
        // 共享组由宏代理按上一笔实际发布的意图回送。独立 AZ 两组用旁路。
        adc2_chop_neg_i = previous_chop_neg;
        adc2_az_pair_id_i = previous_az_pair;
        adc2_az_ok_i = caaz_enable_i;
        #1;
        check(adc2_ready_o && !advance_ready_o, "archived ADC2 accepted on the fixed slot");
      end
      step_clock();
      adc2_valid_i = 0;
      check(advance_ready_o && !conv_ready_o, "ADC2 handoff releases only this conversion");
      previous = current;
      previous_chop_neg = acq_chop_neg_o;
      previous_az_pair = acq_az_pair_id_o;
    end
    repeat (25) step_clock();
    check(recon_inflight_o == 0, "all reconstruction tags retired");
    check(adc2_captures == Samples && reconstructed == Samples &&
          outputs == (caaz_enable_i ? Samples / 2 : Samples), "complete replay counts");
    $fclose(data_file);
    $fclose(trace_file);
  endtask

  initial begin
    if (!$value$plusargs("coeff=%s", coeff_path) || !$value$plusargs("vectors=%s", vector_path) ||
        !$value$plusargs("trace=%s", trace_path) || !$value$plusargs("caaz=%d", caaz_enable_i) ||
        !$value$plusargs("slice_seed=%h", slice_seed_i) || !$value$plusargs("dem_seed=%h", dem_seed_i) ||
        !$value$plusargs("dither_seed=%h", dither_seed_i))
      $fatal(1, "frozen coefficient, seed and replay paths are required");
    initialize();
    load_configuration();
    replay();
    $display("SAR20_JOINT_NOISE_COMPLETE cycles=%0d checks=%0d adc2_captures=%0d reconstructed=%0d outputs=%0d peak_inflight=%0d",
             cycles, checks, adc2_captures, reconstructed, outputs, peak_inflight);
    $finish;
  end
endmodule
