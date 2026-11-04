`timescale 1ns/1ps
// Small-geometry functional FSM witness. This is zero-delay RTL simulation,
// not a timing/analog/PPA measurement. Inputs change only on falling edges;
// every assertion/CSV post-state observes 1 ps after the rising edge (NBA done).
// Optional +trace_dir=<existing fresh directory> writes three real edge traces.
module engineering_fsm_tb;
  logic clk = 0;
  always #5 clk = ~clk;

  logic rst_n = 0;
  logic phase_enable = 0;
  wire [3:0] phase;
  wire quiet_sample, bank_advance, residue_capture;
  wire tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate;
  analog_phase_ctrl u_phase (
      .clk(clk), .rst_n(rst_n), .enable(phase_enable), .phase(phase),
      .quiet_sample(quiet_sample), .bank_advance(bank_advance),
      .residue_capture(residue_capture), .tp_clock(tp_clock), .ra_az(ra_az),
      .ra_amplify(ra_amplify), .ref_precharge(ref_precharge),
      .ref_accurate(ref_accurate)
  );

  logic sar_enable = 0, sar_start = 0, sar_cancel = 0, cmp_valid = 0;
  logic [8:0] seed_code = 0, sar_target = 0;
  wire [8:0] trial_code, resolved_code;
  wire sar_busy, sar_done, resolved_valid, compare_enable;
  wire cmp_ge = sar_target >= trial_code;
  sar_trial_ctrl u_sar (
      .clk(clk), .rst_n(rst_n), .enable(sar_enable), .start(sar_start),
      .cancel(sar_cancel), .seed_code(seed_code), .cmp_valid(cmp_valid),
      .cmp_ge(cmp_ge), .trial_code(trial_code), .resolved_code(resolved_code),
      .busy(sar_busy), .done(sar_done), .resolved_valid(resolved_valid),
      .compare_enable(compare_enable)
  );

  logic cfg_ready = 0, recon_start = 0, clr_ovf = 0;
  logic [4:0] slice_id = 0;
  logic [19:0] adc2_code = 0;
  logic [31:0] sample_id = 0;
  logic [0:0][2:0][47:0] weights;
  wire [19:0] dout;
  wire dout_valid, recon_busy, clip_low, clip_high, acc_ovf, gain_err, adc2_ovf;
  wire [31:0] result_id;
  wire [4:0] result_flags;
  recon_core #(
      .P_N_ACTIVE(1), .P_N_MAIN(2), .P_N_SUB(1), .P_N_SLICES(1),
      .P_ADC2_BITS(20), .P_STAGES(7)
  ) u_recon (
      .clk(clk), .rst_n(rst_n), .cfg_ready(cfg_ready), .start(recon_start),
      .sample_id(sample_id), .clr_ovf(clr_ovf), .sampling_mask_en(1'b0),
      .slice_id(slice_id), .main_on(2'd0), .sub_on(1'b0), .dither_rail(4'd0),
      .adc2_code(adc2_code), .inj_q(64'sd0), .w_rom(weights), .row_total('0),
      .offset_q(64'sd0), .adc2_min_q(64'sd0), .adc2_max_q(64'sd8589934592),
      .dout(dout), .dout_valid(dout_valid), .clip_low(clip_low),
      .clip_high(clip_high), .acc_ovf(acc_ovf), .gain_err(gain_err),
      .adc2_ovf(adc2_ovf), .busy(recon_busy), .result_sample_id(result_id),
      .result_flags(result_flags)
  );

  string trace_dir;
  string sar_scenario = "reset", recon_scenario = "reset", phase_scenario = "reset";
  integer sar_fd = 0, recon_fd = 0, phase_fd = 0;
  int edge_count = 0;
  int sar_accepts = 0, sar_completions = 0, sar_cancels = 0, sar_stalls = 0;
  int sar_busy_starts = 0, phase_startups = 0, phase_wraps = 0;
  int recon_accepts = 0, recon_results = 0, recon_errors = 0, recon_cancels = 0;
  int recon_overlaps = 0, rejected_requests = 0;
  bit pending = 0, pending_error = 0, expected_sticky_gain = 0;
  int due_edge = 0;
  logic [31:0] pending_id = 0;
  logic [19:0] pending_word = 0, last_good_word = 0;

  initial begin
    if ($value$plusargs("trace_dir=%s", trace_dir)) begin
      sar_fd = $fopen({trace_dir, "/sar_trace.csv"}, "w");
      recon_fd = $fopen({trace_dir, "/recon_trace.csv"}, "w");
      phase_fd = $fopen({trace_dir, "/phase_trace.csv"}, "w");
      if (sar_fd == 0 || recon_fd == 0 || phase_fd == 0)
        $fatal(1, "trace_dir must be an existing writable fresh directory");
      $fdisplay(sar_fd, "edge,time_ns,scenario,rst_n,enable,start,cancel,cmp_valid,cmp_ge,pre_bit_index,pre_busy,pre_trial,pre_resolved,post_bit_index,post_busy,post_done,post_resolved_valid,post_compare_enable,post_trial,post_resolved");
      $fdisplay(recon_fd, "edge,time_ns,scenario,rst_n,cfg_ready,start,clear,code,id,slice_id,pre_busy,pre_stage_b,pre_div_busy,pre_div_done,pre_div_cnt,post_busy,post_stage_b,post_div_busy,post_div_done,post_div_cnt,post_valid,post_word,post_id,post_flags,post_gain_sticky,post_fine,post_gain,post_total,post_rails,post_a1,post_div_q");
      $fdisplay(phase_fd, "edge,time_ns,scenario,rst_n,enable,pre_phase,pre_quiet,pre_advance,pre_capture,post_phase,post_quiet,post_advance,post_capture,post_tp,post_az,post_amplify,post_precharge,post_accurate");
    end
  end

  // This observer captures all pre-edge inputs/state before any NBA update,
  // then uses actual post-edge DUT state. The recon scoreboard checks external
  // latency directly; it does not use div_done to decide when a result is due.
  initial forever begin : observe_edge
    bit pre_rst, pre_pe, pre_se, pre_ss, pre_sc, pre_cv, pre_ge;
    bit pre_ready, pre_rs, pre_clr, pre_rb, pre_sb, pre_db, pre_dd;
    bit pre_sbusy, pre_quiet, pre_advance, pre_capture, accept_recon;
    int pre_phase, pre_bit, pre_cnt, expected_phase;
    logic [8:0] pre_trial, pre_resolved, expected_accepted;
    logic [19:0] pre_code;
    logic [31:0] pre_id;
    logic [4:0] pre_sid;
    @(posedge clk);
    edge_count++;
    pre_rst = rst_n;
    pre_pe = phase_enable;
    pre_phase = int'(phase);
    pre_quiet = quiet_sample;
    pre_advance = bank_advance;
    pre_capture = residue_capture;
    pre_se = sar_enable;
    pre_ss = sar_start;
    pre_sc = sar_cancel;
    pre_cv = cmp_valid;
    pre_ge = cmp_ge;
    pre_sbusy = sar_busy;
    pre_bit = int'(u_sar.bit_index);
    pre_trial = trial_code;
    pre_resolved = resolved_code;
    pre_ready = cfg_ready;
    pre_rs = recon_start;
    pre_clr = clr_ovf;
    pre_rb = recon_busy;
    pre_sb = u_recon.stage_b;
    pre_db = u_recon.div_busy;
    pre_dd = u_recon.div_done;
    pre_cnt = int'(u_recon.u_div.cnt);
    pre_code = adc2_code;
    pre_id = sample_id;
    pre_sid = slice_id;
    accept_recon = pre_rst && pre_ready && pre_rs && !pre_rb;
    #0.001;

    if (!pre_rst || !pre_pe) begin
      if ({phase, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate} !== 9'd0)
        $fatal(1, "phase reset/disable failed");
    end else begin
      expected_phase = (pre_phase == 15) ? 0 : pre_phase + 1;
      if (int'(phase) != expected_phase ||
          tp_clock !== (expected_phase != 0) ||
          ra_az !== (expected_phase >= 1 && expected_phase < 9) ||
          ra_amplify !== (expected_phase >= 10 || expected_phase == 0) ||
          ref_precharge !== (expected_phase >= 2 && expected_phase < 9) ||
          ref_accurate !== (expected_phase >= 10 || expected_phase == 0))
        $fatal(1, "registered phase schedule mismatch edge=%0d", edge_count);
      if (pre_quiet !== (pre_phase == 0) || pre_advance !== (pre_phase == 1) ||
          pre_capture !== (pre_phase == 9))
        $fatal(1, "digital phase handshake must sample pre-edge phase");
      if (phase_startups == 0) begin
        if (pre_phase != 0 || phase != 1 || !pre_quiet)
          $fatal(1, "phase startup did not leave cleared phase zero");
        phase_startups++;
      end
      if (phase == 0) begin
        phase_wraps++;
        if (!ra_amplify || !ref_accurate)
          $fatal(1, "steady phase zero must differ from cleared startup zero");
      end
    end

    if (!pre_rst || !pre_se) begin
      if ({trial_code, resolved_code, sar_busy, sar_done, resolved_valid} !== 21'd0)
        $fatal(1, "SAR reset/disable failed");
    end else if (pre_sc) begin
      if (sar_busy || sar_done || resolved_valid || compare_enable ||
          trial_code !== pre_trial || resolved_code !== pre_resolved ||
          int'(u_sar.bit_index) != pre_bit)
        $fatal(1, "SAR cancel must retain data and suppress completion");
      if (pre_sbusy) sar_cancels++;
    end else if (pre_ss && !pre_sbusy) begin
      sar_accepts++;
      if (!sar_busy || sar_done || !resolved_valid || u_sar.bit_index != 5 ||
          resolved_code !== {seed_code[8:6], 6'b0} ||
          trial_code !== {seed_code[8:6], 1'b1, 5'b0})
        $fatal(1, "SAR accepted seed/pending trial mismatch");
    end else if (pre_sbusy && pre_cv) begin
      expected_accepted = pre_resolved;
      expected_accepted[pre_bit] = pre_ge;
      if (resolved_code !== expected_accepted || !resolved_valid)
        $fatal(1, "SAR comparison committed incorrect resolved code");
      if (pre_bit == 0) begin
        sar_completions++;
        if (sar_busy || !sar_done || trial_code !== expected_accepted ||
            resolved_code !== sar_target)
          $fatal(1, "SAR completed word mismatch");
      end else if (!sar_busy || sar_done || int'(u_sar.bit_index) != pre_bit - 1 ||
          trial_code !== (expected_accepted | (9'd1 << (pre_bit - 1))))
        $fatal(1, "SAR next trial/index mismatch");
    end else begin
      if (sar_busy !== pre_sbusy || sar_done || resolved_valid ||
          trial_code !== pre_trial || resolved_code !== pre_resolved ||
          int'(u_sar.bit_index) != pre_bit)
        $fatal(1, "SAR stalled/idle state changed");
      if (pre_sbusy && !pre_cv) sar_stalls++;
    end
    if (pre_rst && pre_se && pre_sbusy && pre_ss && !pre_sc) sar_busy_starts++;

    if (!pre_rst) begin
      pending = 0;
      last_good_word = 0;
      expected_sticky_gain = 0;
      if (dout_valid || dout != 0 || gain_err || result_id != 0 || result_flags != 0)
        $fatal(1, "recon reset failed");
    end else begin
      if (pre_clr) expected_sticky_gain = 0;
      if (!pre_ready) begin
        if (pending) recon_cancels++;
        pending = 0;
        last_good_word = 0;
        if (dout_valid || dout != 0 || result_id != 0 || result_flags != 0 || recon_busy)
          $fatal(1, "configuration cancellation leaked a word/event");
        if (pre_rs) rejected_requests++;
      end else begin
        if (pending && edge_count == due_edge) begin
          if (!dout_valid || result_id !== pending_id || dout !== pending_word ||
              result_flags !== (pending_error ? 5'b00010 : 5'b00000))
            $fatal(1, "recon word/ID/flags/latency mismatch edge=%0d", edge_count);
          recon_results++;
          if (pending_error) begin
            recon_errors++;
            expected_sticky_gain = 1;
          end else last_good_word = pending_word;
          pending = 0;
          if (accept_recon) recon_overlaps++;
        end else if (dout_valid)
          $fatal(1, "recon emitted an early/late/cancelled completion");
        if (accept_recon) begin
          if (pending) $fatal(1, "recon accepted an overlapping busy request");
          pending = 1;
          recon_accepts++;
          due_edge = edge_count + 11;
          pending_id = pre_id;
          pending_error = pre_sid != 0;
          // For these exact positive weights and ADC2 limits the independent
          // integer equation reduces to dout=adc2_code. Invalid slice holds
          // the previous legal word and reports gain_err for this sample.
          pending_word = pending_error ? last_good_word : pre_code;
        end
      end
      if (gain_err !== expected_sticky_gain || acc_ovf || adc2_ovf || clip_low || clip_high)
        $fatal(1, "recon sticky/clip flags mismatch");
    end

    if (sar_fd != 0)
      $fdisplay(sar_fd, "%0d,%0.3f,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
          edge_count, $realtime, sar_scenario, pre_rst, pre_se, pre_ss, pre_sc,
          pre_cv, pre_ge, pre_bit, pre_sbusy, pre_trial, pre_resolved,
          u_sar.bit_index, sar_busy, sar_done, resolved_valid, compare_enable,
          trial_code, resolved_code);
    if (recon_fd != 0)
      $fdisplay(recon_fd, "%0d,%0.3f,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
          edge_count, $realtime, recon_scenario, pre_rst, pre_ready, pre_rs, pre_clr,
          pre_code, pre_id, pre_sid, pre_rb, pre_sb, pre_db, pre_dd, pre_cnt,
          recon_busy, u_recon.stage_b, u_recon.div_busy, u_recon.div_done,
          u_recon.u_div.cnt, dout_valid, dout, result_id, result_flags, gain_err,
          u_recon.fine_r, u_recon.gain_s, u_recon.total_s, u_recon.rails_s,
          $signed(u_recon.a1), u_recon.div_q);
    if (phase_fd != 0)
      $fdisplay(phase_fd, "%0d,%0.3f,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
          edge_count, $realtime, phase_scenario, pre_rst, pre_pe, pre_phase,
          pre_quiet, pre_advance, pre_capture, phase, quiet_sample, bank_advance,
          residue_capture, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate);
  end

  task automatic tick;
    @(posedge clk);
    #0.002; // observer assertions/CSV run at +1 ps first
  endtask

  task automatic request_recon(input int id, input int code, input string scenario);
    @(negedge clk);
    recon_scenario = scenario;
    sample_id = 32'(id);
    adc2_code = 20'(code);
    recon_start = 1;
    tick();
    @(negedge clk);
    recon_start = 0;
    // Poison the live bus after capture to test that ID/data are snapshotted.
    sample_id = '1;
    adc2_code = 20'd987654;
  endtask

  task automatic await_recon(input int id);
    int waited = 0;
    while (!dout_valid || result_id != 32'(id)) begin
      tick();
      waited++;
      if (waited > 16) $fatal(1, "recon await timeout id=%0d", id);
    end
  endtask

  task automatic start_sar(input int target, input string scenario);
    @(negedge clk);
    sar_scenario = scenario;
    sar_target = 9'(target);
    seed_code = 9'(target);
    sar_start = 1;
    cmp_valid = 0;
    tick();
    @(negedge clk);
    sar_start = 0;
  endtask

  task automatic finish_sar;
    int steps = 0;
    while (sar_busy) begin
      @(negedge clk);
      cmp_valid = (steps % 3 != 1); // explicit comparator-settling stall
      sar_start = steps == 2; // ignored busy request must not replace context
      tick();
      steps++;
      if (steps > 12) $fatal(1, "SAR await timeout");
    end
    @(negedge clk);
    cmp_valid = 0;
    sar_start = 0;
  endtask

  initial begin : stimulus
    weights[0][0] = 48'd536870912;
    weights[0][1] = 48'd268435456;
    weights[0][2] = 48'd268435456;
    repeat (2) tick();
    @(negedge clk);
    rst_n = 1;
    phase_enable = 1;
    phase_scenario = "startup_then_steady";
    sar_enable = 1;

    start_sar(345, "normal_stall_complete");
    finish_sar();
    start_sar(123, "cancel_during_compare");
    @(negedge clk);
    cmp_valid = 1;
    tick();
    @(negedge clk);
    sar_cancel = 1;
    cmp_valid = 1; // cancellation has priority over this valid comparison
    tick();
    @(negedge clk);
    sar_cancel = 0;
    cmp_valid = 0;
    repeat (3) tick();
    start_sar(201, "recovery_stall_complete");
    finish_sar();

    request_recon(100, 123, "unconfigured_request");
    repeat (2) tick();
    @(negedge clk);
    cfg_ready = 1;
    request_recon(101, 73, "normal_capture");
    await_recon(101);

    request_recon(201, 111, "normal_before_overlap");
    while (!u_recon.div_done) begin
      tick();
    end
    // div_done is now high and busy is low; the NEXT edge commits 201 and
    // accepts 202. Both capture and observation conventions remain explicit.
    @(negedge clk);
    recon_scenario = "commit_and_next_start";
    sample_id = 32'd202;
    adc2_code = 20'd203;
    recon_start = 1;
    tick();
    if (!dout_valid || result_id != 201 || dout != 111)
      $fatal(1, "old output lost on simultaneous next capture");
    @(negedge clk);
    recon_start = 0;
    sample_id = '1;
    adc2_code = 20'd987654;
    recon_scenario = "next_after_overlap";
    await_recon(202);

    request_recon(301, 99, "cancel_during_division");
    repeat (3) tick();
    @(negedge clk);
    cfg_ready = 0;
    tick();
    @(negedge clk);
    cfg_ready = 1;
    recon_scenario = "cancel_drain_no_leak";
    repeat (16) tick();

    request_recon(302, 150, "recovery_after_cancel");
    await_recon(302);
    @(negedge clk);
    slice_id = 5'd31;
    request_recon(401, 500, "invalid_slice_error");
    await_recon(401);
    @(negedge clk);
    slice_id = 0;
    clr_ovf = 1;
    recon_scenario = "clear_sticky_error";
    tick();
    @(negedge clk);
    clr_ovf = 0;
    request_recon(501, 304, "recovery_after_error");
    await_recon(501);

    @(negedge clk);
    slice_id = 5'd31;
    request_recon(601, 500, "cancel_on_error_commit");
    while (!u_recon.div_done) tick();
    @(negedge clk);
    cfg_ready = 0;
    tick(); // same edge that would have committed the candidate error
    @(negedge clk);
    cfg_ready = 1;
    slice_id = 0;
    recon_scenario = "cancelled_error_drain";
    repeat (16) tick();
    request_recon(701, 409, "final_recovery");
    await_recon(701);
    @(negedge clk);
    phase_enable = 0;
    phase_scenario = "disable_cancels_schedule";
    tick();

    if (pending || recon_accepts != 9 || recon_results != 7 || recon_errors != 1 ||
        recon_cancels != 2 || recon_overlaps != 1 || rejected_requests != 1 ||
        sar_accepts != 3 || sar_completions != 2 || sar_cancels != 1 ||
        sar_stalls < 4 || sar_busy_starts != 2 || phase_startups != 1 || phase_wraps < 2)
      $fatal(1, "FSM scenario accounting failed");
    if (sar_fd != 0) $fclose(sar_fd);
    if (recon_fd != 0) $fclose(recon_fd);
    if (phase_fd != 0) $fclose(phase_fd);
    $display("ENGINEERING_FSM_COMPLETE recon_accepts=%0d recon_results=%0d recon_errors=%0d recon_cancels=%0d commit_overlaps=%0d rejected=%0d sar_accepts=%0d sar_done=%0d sar_cancel=%0d sar_stalls=%0d busy_starts=%0d phase_startups=%0d phase_wraps=%0d edges=%0d",
        recon_accepts, recon_results, recon_errors, recon_cancels, recon_overlaps,
        rejected_requests, sar_accepts, sar_completions, sar_cancels, sar_stalls,
        sar_busy_starts, phase_startups, phase_wraps, edge_count);
    $finish;
  end
  initial begin
    #10000;
    $fatal(1, "engineering FSM watchdog");
  end
endmodule
