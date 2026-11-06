// 执行轨迹并导出每沿实际握手和结果，Python 在仿真外比较期望值。
`timescale 1ns/1ps
module sar20_lms_recon_tb;
  typedef struct packed {
    logic rst_n, clear, cfg_start, cfg_valid;
    logic [25:0] cfg_data;
    logic in_valid;
    logic [31:0] id;
    logic in_bad;
    logic [39:0] ids;
    logic [135:0] main;
    logic [39:0] conv_sub, sample_sub;
    logic [11:0] adc2;
    logic out_ready;
  } stimulus_t;

  logic clk;
  stimulus_t v;
  logic cfg_start_ready, cfg_ready, configured, cfg_error;
  logic in_ready, out_valid, out_bad;
  logic [31:0] out_id;
  logic signed [19:0] code;

  sar20_lms_recon dut (
      .clk_i(clk), .rst_ni(v.rst_n), .clear_i(v.clear),
      .cfg_start_i(v.cfg_start), .cfg_start_ready_o(cfg_start_ready),
      .cfg_valid_i(v.cfg_valid), .cfg_ready_o(cfg_ready), .cfg_data_i(v.cfg_data),
      .configured_o(configured), .cfg_error_o(cfg_error),
      .in_valid_i(v.in_valid), .in_ready_o(in_ready), .in_sample_id_i(v.id), .in_bad_i(v.in_bad),
      .in_slice_ids_i(v.ids), .in_main_i(v.main), .in_conv_sub_i(v.conv_sub),
      .in_sample_sub_i(v.sample_sub), .in_adc2_i(v.adc2), .out_valid_o(out_valid),
      .out_ready_i(v.out_ready), .out_sample_id_o(out_id), .out_code_o(code), .out_bad_o(out_bad)
  );

  task automatic tick;
    #1;
    clk = 1'b1;
    #1;
    clk = 1'b0;
  endtask

  task automatic configure_midpoint_checks;
    v = '0;
    tick();
    v.rst_n = 1'b1;
    v.cfg_start = 1'b1;
    #1;
    if (!cfg_start_ready) $fatal(1, "cannot start midpoint coefficient load");
    tick();
    v.cfg_start = 1'b0;
    v.cfg_valid = 1'b1;
    for (int word = 0; word < 396; word++) begin
      v.cfg_data = (word % 22 == 21) ? 26'd33554432 : 26'd0;
      #1;
      if (!cfg_ready) $fatal(1, "midpoint coefficient %0d was not accepted", word);
      tick();
    end
    v.cfg_valid = 1'b0;
    if (!configured || cfg_error) $fatal(1, "midpoint coefficient load failed");
    for (int slot = 0; slot < 8; slot++) v.ids[5*slot+:5] = 5'(slot);
  endtask

  task automatic check_midpoint(input logic [11:0] adc2, input logic signed [31:0] expected);
    v.adc2 = adc2;
    v.id = {20'd0, adc2};
    v.in_valid = 1'b1;
    #1;
    if (!in_ready) $fatal(1, "ADC2 midpoint code %0d was not accepted", adc2);
    tick();  // E0
    v.in_valid = 1'b0;
    repeat (3) tick();  // E1…E3：累加器已初始化，首槽尚未加进来。
    if (dut.accum_num_q !== expected)
      $fatal(1, "ADC2 midpoint code %0d expected %0d got %0d", adc2, expected, dut.accum_num_q);
    v.clear = 1'b1;
    tick();
    v.clear = 1'b0;
  endtask

  task automatic check_vectors(input string vectors, input string midpoint, input string observation);
    logic [$bits(stimulus_t)-1:0] record;
    logic [58:0] unused_expected;
    int stream, observed, read_status, cycles, midpoint_count;
    logic [43:0] mid;

    cycles = 0;
    midpoint_count = 0;
    configure_midpoint_checks();
    stream = $fopen(midpoint, "r");
    if (stream == 0) $fatal(1, "cannot open %s", midpoint);
    read_status = $fscanf(stream, "%h\n", mid);
    while (read_status == 1) begin
      if (midpoint_count >= 4096 || mid[43:32] != 12'(midpoint_count))
        $fatal(1, "unexpected midpoint vector at row %0d", midpoint_count);
      check_midpoint(mid[43:32], $signed(mid[31:0]));
      midpoint_count += 1;
      read_status = $fscanf(stream, "%h\n", mid);
    end
    if (!$feof(stream)) $fatal(1, "malformed midpoint vectors");
    $fclose(stream);

    stream = $fopen(vectors, "r");
    observed = $fopen(observation, "w");
    if (stream == 0 || observed == 0) $fatal(1, "cannot open vector or observation stream");
    read_status = $fscanf(stream, "%h %h\n", record, unused_expected);
    while (read_status == 2) begin
      v = stimulus_t'(record);
      #1;
      $fwrite(observed, "%h ", {cfg_start_ready && v.cfg_start, cfg_ready && v.cfg_valid,
                              in_ready && v.in_valid});
      clk = 1'b1;
      #1;
      $fwrite(observed, "%h\n", {configured, cfg_error, out_valid,
                                out_valid ? {out_id, code, out_bad} : 53'd0});
      cycles += 1;
      clk = 1'b0;
      read_status = $fscanf(stream, "%h %h\n", record, unused_expected);
    end
    if (!$feof(stream)) $fatal(1, "malformed vectors after cycle %0d", cycles);
    $fclose(stream);
    $fclose(observed);
    $display("RECON_COMPLETE cycles=%0d midpoint=%0d", cycles, midpoint_count);
  endtask

  initial begin
    string vectors, midpoint, observation;
    clk = 1'b0;
    v = '0;
    if (!$value$plusargs("midpoint=%s", midpoint)) $fatal(1, "missing +midpoint");
    if (!$value$plusargs("vectors=%s", vectors)) $fatal(1, "missing +vectors");
    if (!$value$plusargs("observation=%s", observation)) $fatal(1, "missing +observation");
    check_vectors(vectors, midpoint, observation);
    $finish;
  end
endmodule
