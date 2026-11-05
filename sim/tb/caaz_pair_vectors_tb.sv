// @file caaz_pair_vectors_tb.sv
// @brief Explicit digital-event vectors for the CAAZ pair arithmetic component.
// Boundary: no ADC model, analog noise, physical CBIAS or acquisition assumption. Inputs change on
// falling edges; registered outputs are checked 1 ns after rising edges. Parse into staging storage
// before driving connected DUT inputs. Required plusargs select the vector/trace paths and all
// nonzero record counts.
`timescale 1ns/1ps
`default_nettype none

module caaz_pair_vectors_tb;
  logic clk = 1'b0;
  bit clock_running = 1'b0;
  initial forever begin
    #5;
    if (clock_running)
      clk = ~clk;
  end
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
  int trace_file;
  int fields_read;
  int case_id;
  int cycle_id;
  int previous_case = -1;
  int previous_cycle = -1;
  int observed_cycles = 0;
  int observed_cases = 0;
  int observed_outputs = 0;
  int assertions_checked = 0;
  int expected_cycles;
  int expected_cases;
  int expected_valid_outputs;
  bit counts_ready = 1'b0;
  string vector_path;
  string trace_path;
  string line;
  longint signed parsed [0:22];

  task automatic require_ok(input bit condition, input string message);
    assertions_checked++;
    if (!condition)
      $fatal(1, "%s case=%0d cycle=%0d", message, case_id, cycle_id);
  endtask

  task automatic require_boolean(input logic [4:0] field_index);
    require_ok(parsed[field_index] == 0 || parsed[field_index] == 1,
               "nonbinary Boolean field");
  endtask

  function automatic int column_count(input string text_line);
    bit in_token;
    int count;
    in_token = 1'b0;
    count = 0;
    for (int index = 0; index < text_line.len(); index++) begin
      if (text_line.getc(index) inside {8'h09, 8'h0a, 8'h0d, 8'h20}) begin
        in_token = 1'b0;
      end else if (!in_token) begin
        count++;
        in_token = 1'b1;
      end
    end
    return count;
  endfunction

  initial begin
    if (!$value$plusargs("vectors=%s", vector_path) ||
        !$value$plusargs("trace_csv=%s", trace_path) ||
        !$value$plusargs("expected_cycles=%d", expected_cycles) ||
        !$value$plusargs("expected_cases=%d", expected_cases) ||
        !$value$plusargs("expected_valid_outputs=%d", expected_valid_outputs))
      $fatal(1, "explicit vector/trace paths and completion counts are required");
    require_ok(expected_cycles > 0 && expected_cycles <= 100000 &&
               expected_cases > 0 && expected_cases <= expected_cycles &&
               expected_valid_outputs > 0 && expected_valid_outputs <= expected_cycles,
               "invalid or unsupported bounded component-vector counts");
    fixture_file = $fopen(vector_path, "r");
    trace_file = $fopen(trace_path, "w");
    require_ok(fixture_file != 0 && trace_file != 0, "cannot open component artifact");
    $fdisplay(trace_file, "case_id,cycle,rst_n,epoch_clear,in_valid,in_code,in_sample_id,in_chop_neg,in_az_pair_id,in_az_valid,in_flags,in_analog_bad,out_valid,out_code,out_sample_id,out_first_sample_id,out_az_pair_id,out_flags,out_analog_bad,pair_difference,pair_difference_valid,fault,fault_pulse,pair_pending,fault_code");
    counts_ready = 1'b1;
    while ($fgets(line, fixture_file) != 0) begin
      if (line.len() > 0 && line.getc(0) != 8'h23) begin
        require_ok(column_count(line) == 25, "component row requires exactly 25 columns");
        if (observed_cycles != 0)
          @(negedge clk);
        fields_read = $sscanf(line,
            "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d",
            case_id, cycle_id, parsed[0], parsed[1], parsed[2], parsed[3],
            parsed[4], parsed[5], parsed[6], parsed[7], parsed[8], parsed[9],
            parsed[10], parsed[11], parsed[12], parsed[13], parsed[14],
            parsed[15], parsed[16], parsed[17], parsed[18], parsed[19],
            parsed[20], parsed[21], parsed[22]);
        require_ok(fields_read == 25,
                   $sformatf("malformed component-vector row fields=%0d", fields_read));
        if (case_id != previous_case) begin
          require_ok(case_id == previous_case + 1 && cycle_id == 0 && parsed[0] == 0,
                     "each sequential case must start with an explicit reset row");
          observed_cases++;
        end else begin
          require_ok(cycle_id == previous_cycle + 1, "nonconsecutive component cycle");
        end
        previous_case = case_id;
        previous_cycle = cycle_id;
        foreach (parsed[field_index]) begin
          case (field_index)
            3, 11: require_ok(parsed[field_index] >= 0 && parsed[field_index] < 1048576,
                              "20-bit word is out of range");
            4, 6, 12, 13, 14:
              require_ok(parsed[field_index] >= 64'sd0 && parsed[field_index] <= 64'sd4294967295,
                         $sformatf("32-bit identity is out of range field=%0d value=%0d",
                                   field_index, parsed[field_index]));
            8, 15: require_ok(parsed[field_index] >= 0 && parsed[field_index] < 32,
                              "five-bit flags are out of range");
            17: require_ok(parsed[field_index] >= -1048576 && parsed[field_index] < 1048576,
                           "signed pair difference is out of range");
            22: require_ok(parsed[field_index] >= 0 && parsed[field_index] < 16,
                           "fault code is out of range");
            default: require_boolean(5'(field_index));
          endcase
        end
        // Explicit assignments preserve older Verilator sensitivity tracking.
        rst_n = 1'(parsed[0]);
        epoch_clear = 1'(parsed[1]);
        in_valid = 1'(parsed[2]);
        in_code = 20'(parsed[3]);
        in_sample_id = 32'(parsed[4]);
        in_chop_neg = 1'(parsed[5]);
        in_az_pair_id = 32'(parsed[6]);
        in_az_valid = 1'(parsed[7]);
        in_flags = 5'(parsed[8]);
        in_analog_bad = 1'(parsed[9]);
        clock_running = 1'b1;
        @(posedge clk);
        #1;
        require_ok(out_valid == 1'(parsed[10]), "component output-valid mismatch");
        require_ok(out_code == 20'(parsed[11]), "component floor-word mismatch");
        require_ok(out_sample_id == 32'(parsed[12]), "component last-ID mismatch");
        require_ok(out_first_sample_id == 32'(parsed[13]), "component first-ID mismatch");
        require_ok(out_az_pair_id == 32'(parsed[14]), "component AZ-ID mismatch");
        require_ok(out_flags == 5'(parsed[15]), "component flags mismatch");
        require_ok(out_analog_bad == 1'(parsed[16]), "component analog-flag mismatch");
        require_ok(longint'($signed(pair_difference)) == parsed[17],
                   "component half-LSB difference mismatch");
        require_ok(pair_difference_valid == 1'(parsed[18]), "component difference-valid mismatch");
        require_ok(fault == 1'(parsed[19]), "component sticky-fault mismatch");
        require_ok(fault_pulse == 1'(parsed[20]), "component fault-pulse mismatch");
        require_ok(pair_pending == 1'(parsed[21]), "component pending-state mismatch");
        require_ok(fault_code == 4'(parsed[22]), "component fault-code mismatch");
        $fdisplay(trace_file, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
            case_id, cycle_id, rst_n, epoch_clear, in_valid, in_code,
            in_sample_id, in_chop_neg, in_az_pair_id, in_az_valid, in_flags,
            in_analog_bad, out_valid, out_code, out_sample_id, out_first_sample_id,
            out_az_pair_id, out_flags, out_analog_bad, $signed(pair_difference),
            pair_difference_valid, fault, fault_pulse, pair_pending, fault_code);
        observed_cycles++;
        require_ok(observed_cycles <= expected_cycles, "extra component-vector records");
        if (out_valid)
          observed_outputs++;
      end
    end
    $fclose(fixture_file);
    $fclose(trace_file);
    require_ok(observed_cycles == expected_cycles && observed_cases == expected_cases &&
               observed_outputs == expected_valid_outputs, "incomplete component execution");
    $display("CAAZ_PAIR_VECTORS_COMPLETE cycles=%0d outputs=%0d checks=%0d cases=%0d scope=COMPONENT_SYNTHETIC_DIGITAL",
             observed_cycles, observed_outputs, assertions_checked, observed_cases);
    $finish;
  end

  initial begin
    wait (counts_ready);
    repeat (expected_cycles + 100) @(posedge clk);
    $fatal(1, "bounded component-vector watchdog");
  end
endmodule

`default_nettype wire
