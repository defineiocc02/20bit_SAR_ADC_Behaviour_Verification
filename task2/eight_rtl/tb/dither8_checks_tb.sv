// 按独立 Python 事务向量核对完整八片数字顶层。
`timescale 1ns/1ps
module dither8_checks_tb;
  typedef struct packed {
    logic accepted, stall, seed_error;
    logic acq_valid;
    logic [31:0] acq_id;
    logic [39:0] acq_ids;
    logic [5:0] acq_word;
    logic [4:0] acq_lower;
    logic [39:0] acq_sub;
    logic conv_ready;
    logic [31:0] conv_id;
    logic [39:0] conv_ids;
    logic [5:0] conv_word;
    logic [5:0] conv_total;
    logic [39:0] conv_sample_sub;
    logic [8:0] command;
    logic [135:0] main;
    logic [39:0] sub;
    logic [31:0] bridge_correction;
    logic mask_valid, code_error;
  } result_t;

  typedef struct packed {
    logic rst_n, clear, enable, advance;
    logic [17:0] eligible;
    logic shuffle, dem_enable, bridge_enable, dither_enable, seed_load;
    logic [31:0] slice_seed, dem_seed;
    logic [30:0] dither_seed;
    logic code_valid;
    logic [8:0] code;
    logic [31:0] code_sample_id;
    result_t expected;
  } vector_t;

  logic clk;
  vector_t vector;
  result_t actual;

  dither8_top dut (
      .clk_i(clk), .rst_ni(vector.rst_n), .clear_i(vector.clear), .enable_i(vector.enable),
      .advance_i(vector.advance), .eligible_i(vector.eligible), .shuffle_i(vector.shuffle),
      .dem_enable_i(vector.dem_enable), .bridge_enable_i(vector.bridge_enable),
      .dither_enable_i(vector.dither_enable), .seed_load_i(vector.seed_load),
      .slice_seed_i(vector.slice_seed), .dem_seed_i(vector.dem_seed),
      .dither_seed_i(vector.dither_seed), .code_valid_i(vector.code_valid), .code_i(vector.code),
      .code_sample_id_i(vector.code_sample_id), .slot_accepted_o(actual.accepted),
      .stall_o(actual.stall), .seed_error_o(actual.seed_error), .acq_dither_valid_o(actual.acq_valid),
      .acq_sample_id_o(actual.acq_id), .acq_slice_ids_o(actual.acq_ids),
      .acq_word_o(actual.acq_word), .acq_lower_eighths_o(actual.acq_lower),
      .acq_sample_sub_high_o(actual.acq_sub), .conv_ready_o(actual.conv_ready),
      .conv_sample_id_o(actual.conv_id), .conv_slice_ids_o(actual.conv_ids),
      .conv_word_o(actual.conv_word), .conv_total_eighths_o(actual.conv_total),
      .conv_sample_sub_high_o(actual.conv_sample_sub), .command_code_o(actual.command),
      .main_mask_o(actual.main), .sub_mask_o(actual.sub),
      .bridge_correction_q32_o(actual.bridge_correction), .mask_valid_o(actual.mask_valid),
      .code_error_o(actual.code_error)
  );

  initial begin
    string path;
    int stream, read_status;
    int cycles, acq_words, valid_commands, code_errors, stalls, seed_errors;
    vector_t record;

    clk = 1'b0;
    vector = '0;
    cycles = 0;
    acq_words = 0;
    valid_commands = 0;
    code_errors = 0;
    stalls = 0;
    seed_errors = 0;
    if (!$value$plusargs("vectors=%s", path)) $fatal(1, "missing +vectors");
    stream = $fopen(path, "r");
    if (stream == 0) $fatal(1, "cannot open %s", path);

    read_status = $fscanf(stream, "%h\n", record);
    while (read_status == 1) begin
      vector = record;
      #1;
      clk = 1'b1;
      #1;
      assert (actual === vector.expected)
      else $fatal(1, "cycle %0d expected %h got %h", cycles, vector.expected, actual);
      assert (!actual.mask_valid || (actual.conv_ready && actual.conv_id == vector.code_sample_id))
      else $fatal(1, "command lost sample binding at cycle %0d", cycles);
      assert (actual.mask_valid || {actual.command, actual.main, actual.sub,
                                   actual.bridge_correction} == '0)
      else $fatal(1, "invalid command drove switches at cycle %0d", cycles);
      cycles += 1;
      acq_words += int'(actual.acq_valid);
      valid_commands += int'(actual.mask_valid);
      code_errors += int'(actual.code_error);
      stalls += int'(actual.stall);
      seed_errors += int'(actual.seed_error);
      clk = 1'b0;
      read_status = $fscanf(stream, "%h\n", record);
    end
    if (!$feof(stream)) $fatal(1, "malformed vector after cycle %0d", cycles);
    $fclose(stream);
    $display("TASK2_DITHER8_COMPLETE cycles=%0d acq_words=%0d valid_commands=%0d code_errors=%0d stalls=%0d seed_errors=%0d",
             cycles, acq_words, valid_commands, code_errors, stalls, seed_errors);
    $finish;
  end
endmodule
