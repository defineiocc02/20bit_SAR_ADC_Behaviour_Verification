// 按独立队列模型生成的向量核对每拍的控制、码和故障。
`timescale 1ns/1ps
module caaz20_pair_tb;
  typedef struct packed {
    logic               acq_valid;
    logic        [31:0] acq_id;
    logic               chop_neg;
    logic               az_capture;
    logic               az_hold;
    logic        [30:0] az_id;
    logic               out_valid;
    logic signed [19:0] out_code;
    logic        [31:0] out_id;
    logic        [31:0] out_first_id;
    logic        [30:0] out_az_id;
    logic               out_pair;
    logic signed [20:0] out_difference;
    logic               fault;
    logic               fault_pulse;
    logic         [2:0] fault_code;
  } result_t;

  typedef struct packed {
    logic               rst_n;
    logic               clear;
    logic               enable;
    logic               caaz_enable;
    logic               acq_valid;
    logic        [31:0] acq_id;
    logic               raw_valid;
    logic        [31:0] raw_id;
    logic signed [19:0] raw_code;
    logic               raw_bad;
    logic               raw_chop_neg;
    logic        [30:0] raw_az_id;
    logic               raw_az_ok;
    result_t            expected;
  } vector_t;

  logic clk;
  vector_t vector;
  result_t actual;

  caaz20_pair dut (
      .clk_i(clk),
      .rst_ni(vector.rst_n),
      .clear_i(vector.clear),
      .enable_i(vector.enable),
      .caaz_enable_i(vector.caaz_enable),
      .acq_valid_i(vector.acq_valid),
      .acq_sample_id_i(vector.acq_id),
      .raw_valid_i(vector.raw_valid),
      .raw_sample_id_i(vector.raw_id),
      .raw_code_i(vector.raw_code),
      .raw_bad_i(vector.raw_bad),
      .raw_chop_neg_i(vector.raw_chop_neg),
      .raw_az_pair_id_i(vector.raw_az_id),
      .raw_az_ok_i(vector.raw_az_ok),
      .acq_ctrl_valid_o(actual.acq_valid),
      .acq_ctrl_sample_id_o(actual.acq_id),
      .acq_chop_neg_o(actual.chop_neg),
      .acq_az_capture_o(actual.az_capture),
      .acq_az_hold_o(actual.az_hold),
      .acq_az_pair_id_o(actual.az_id),
      .out_valid_o(actual.out_valid),
      .out_code_o(actual.out_code),
      .out_sample_id_o(actual.out_id),
      .out_first_sample_id_o(actual.out_first_id),
      .out_az_pair_id_o(actual.out_az_id),
      .out_pair_o(actual.out_pair),
      .out_difference_o(actual.out_difference),
      .fault_o(actual.fault),
      .fault_pulse_o(actual.fault_pulse),
      .fault_code_o(actual.fault_code)
  );

  task automatic replay(input string path);
    vector_t record;
    int stream;
    int read_status;
    int cycles;
    int controls;
    int pairs;
    int bypass;
    int faults;
    logic [31:0] boundary_id;

    cycles = 0;
    controls = 0;
    pairs = 0;
    bypass = 0;
    faults = 0;
    for (int bytes = 1; bytes <= 4; bytes++) begin
      boundary_id = 32'hffffffff >> (32 - 8 * bytes);
      assert (dut.next_id(boundary_id) == boundary_id + 32'd1)
      else $fatal(1, "incorrect ID rollover at %h", boundary_id);
    end
    stream = $fopen(path, "r");
    if (stream == 0) $fatal(1, "cannot open vector stream");
    read_status = $fscanf(stream, "%h\n", record);
    while (read_status == 1) begin
      vector = record;
      #1;
      clk = 1'b1;
      #1;
      if (actual !== vector.expected) begin
        $fatal(1, "cycle %0d expected %h got %h", cycles, vector.expected, actual);
      end
      assert (!(actual.az_capture && actual.az_hold)) else $fatal(1, "AZ controls overlap");
      assert (!(actual.fault && (actual.acq_valid || actual.out_valid)))
      else $fatal(1, "fault accepted a transaction");
      if (actual.out_valid && actual.out_pair) begin
        assert (actual.out_id[0] && actual.out_first_id + 32'd1 == actual.out_id &&
                actual.out_az_id == actual.out_id[31:1])
        else $fatal(1, "output is not a nonoverlapping pair");
      end
      cycles += 1;
      if (actual.acq_valid) controls += 1;
      if (actual.out_valid && actual.out_pair) pairs += 1;
      if (actual.out_valid && !actual.out_pair) bypass += 1;
      if (actual.fault_pulse) faults += 1;
      clk = 1'b0;
      read_status = $fscanf(stream, "%h\n", record);
    end
    if (!$feof(stream)) $fatal(1, "malformed vector after cycle %0d", cycles);
    $fclose(stream);
    $display("CAAZ20_COMPLETE cycles=%0d controls=%0d pairs=%0d bypass=%0d faults=%0d id_boundary_checks=4",
             cycles, controls, pairs, bypass, faults);
  endtask

  initial begin
    string path;
    clk = 1'b0;
    vector = '0;
    if (!$value$plusargs("vectors=%s", path)) $fatal(1, "missing +vectors");
    replay(path);
    $finish;
  end
endmodule
