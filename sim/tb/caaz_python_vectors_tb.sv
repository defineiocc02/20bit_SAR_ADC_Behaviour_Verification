`timescale 1ns/1ps
// Independent Python model/oracle fixture, sampled after each clock edge. The fixture includes
// full-model calibrated raw words and protocol faults.
module caaz_python_vectors_tb;
  logic clk = 1'b0;
  always #5 clk = ~clk;
  logic rst_n = 1'b0;
  logic epoch_clear = 1'b0;
  logic in_valid = 1'b0;
  logic [19:0] in_code = '0;
  logic [31:0] in_sample_id = '0;
  logic in_chop_neg = 1'b0;
  logic [31:0] in_az_pair_id = '0;
  logic in_az_valid = 1'b0;
  logic [4:0] in_flags = '0;
  logic in_analog_bad = 1'b0;
  wire out_valid;
  wire [19:0] out_code;
  wire [31:0] out_sample_id;
  wire [31:0] out_first_sample_id;
  wire [31:0] out_az_pair_id;
  wire [4:0] out_flags;
  wire out_analog_bad;
  wire signed [20:0] pair_difference;
  wire pair_difference_valid;
  wire fault;
  wire fault_pulse;
  wire pair_pending;
  wire [3:0] fault_code;
  caaz_pair_processor #(.P_CAAZ_ENABLE(1'b1)) dut (.*);

  int fixture_file;
  int fields_read;
  int case_id;
  int cycle_id;
  int observed_cycles = 0;
  int observed_outputs = 0;
  int assertions_checked = 0;
  string vector_directory;
  string line;
  int expected_valid;
  logic [19:0] expected_code;
  logic [31:0] expected_last_id;
  logic [31:0] expected_first_id;
  logic [31:0] expected_pair_id;
  logic [4:0] expected_flags;
  int expected_analog_bad;
  int expected_difference;
  int expected_difference_valid;
  int expected_fault;
  int expected_fault_pulse;
  int expected_pending;
  int expected_fault_code;

  // Parse into independent storage, then explicitly publish the transaction.
  // Older Verilator releases may not reschedule DUT combinational sensitivity
  // when $sscanf writes a connected input directly. Clock timing is unchanged.
  int parsed_rst_n;
  int parsed_epoch_clear;
  int parsed_valid;
  int parsed_code;
  int unsigned parsed_sample_id;
  int parsed_chop_neg;
  int unsigned parsed_az_pair_id;
  int parsed_az_valid;
  int parsed_flags;
  int parsed_analog_bad;

  task automatic require_ok(input bit condition, input string message);
    assertions_checked++;
    if (!condition)
      $fatal(1, "%s case=%0d cycle=%0d", message, case_id, cycle_id);
  endtask

  initial begin
    if (!$value$plusargs("vdir=%s", vector_directory))
      vector_directory = "sim/vectors";
    fixture_file = $fopen({vector_directory, "/caaz_pair_contract.txt"}, "r");
    if (!fixture_file)
      $fatal(1, "missing caaz_pair_contract.txt");
    while ($fgets(line, fixture_file)) begin
      if (line.len() > 0 && line.getc(0) != 8'h23) begin
        @(negedge clk);
        fields_read = $sscanf(line,
            "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
            case_id, cycle_id, parsed_rst_n, parsed_epoch_clear, parsed_valid,
            parsed_code, parsed_sample_id, parsed_chop_neg, parsed_az_pair_id,
            parsed_az_valid, parsed_flags, parsed_analog_bad,
            expected_valid, expected_code, expected_last_id, expected_first_id,
            expected_pair_id, expected_flags, expected_analog_bad,
            expected_difference, expected_difference_valid, expected_fault,
            expected_fault_pulse, expected_pending, expected_fault_code);
        require_ok(fields_read == 25, "malformed fixture row");
        rst_n = 1'(parsed_rst_n);
        epoch_clear = 1'(parsed_epoch_clear);
        in_valid = 1'(parsed_valid);
        in_code = 20'(parsed_code);
        in_sample_id = parsed_sample_id;
        in_chop_neg = 1'(parsed_chop_neg);
        in_az_pair_id = parsed_az_pair_id;
        in_az_valid = 1'(parsed_az_valid);
        in_flags = 5'(parsed_flags);
        in_analog_bad = 1'(parsed_analog_bad);
        @(posedge clk);
        #1;
        require_ok(out_valid == 1'(expected_valid), "valid differs from Python");
        require_ok(out_code == expected_code, "floor word differs from Python");
        require_ok(out_sample_id == expected_last_id, "last ID differs from Python");
        require_ok(out_first_sample_id == expected_first_id, "first ID differs from Python");
        require_ok(out_az_pair_id == expected_pair_id, "AZ pair ID differs from Python");
        require_ok(out_flags == expected_flags, "flags differ from Python");
        require_ok(out_analog_bad == 1'(expected_analog_bad), "analog flag differs from Python");
        require_ok(int'($signed(pair_difference)) == expected_difference, "half-LSB difference differs from Python");
        require_ok(pair_difference_valid == 1'(expected_difference_valid), "difference-valid differs from Python");
        require_ok(fault == 1'(expected_fault), "sticky fault differs from Python");
        require_ok(fault_pulse == 1'(expected_fault_pulse), "fault event differs from Python");
        require_ok(pair_pending == 1'(expected_pending), "pending state differs from Python");
        require_ok(fault_code == 4'(expected_fault_code), "fault cause differs from Python");
        observed_cycles++;
        if (out_valid)
          observed_outputs++;
      end
    end
    $fclose(fixture_file);
    require_ok(observed_cycles == 468 && observed_outputs == 206, "incomplete fixture execution");
    $display("CAAZ_PYTHON_VECTORS_COMPLETE cycles=%0d outputs=%0d checks=%0d cases=15", observed_cycles, observed_outputs, assertions_checked);
    $finish;
  end
  initial begin
    #100000;
    $fatal(1, "Python vector watchdog");
  end
endmodule
