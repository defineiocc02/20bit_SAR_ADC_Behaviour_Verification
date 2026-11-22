`timescale 1ns/1ps
module caaz_pair_processor_tb;
  bit clk=0;
  always #5 clk=~clk;
  bit rst_n=0,epoch_clear=0,in_valid=0,in_chop_neg=0,in_az_valid=1,in_analog_bad=0;
  logic [19:0] in_code=0;
  logic [31:0] in_sample_id=0,in_az_pair_id=0;
  logic [4:0] in_flags=0;
  wire out_valid, out_analog_bad,pair_difference_valid,fault,fault_pulse,pair_pending;
  wire [19:0] out_code;
  wire [31:0] out_sample_id,out_first_sample_id,out_az_pair_id;
  wire [4:0] out_flags;
  wire signed [20:0] pair_difference;
  wire [3:0] fault_code;
  caaz_pair_processor #(.P_CAAZ_ENABLE(1'b1)) dut(.*);
  wire off_valid,off_bad,off_diff_valid,off_fault,off_pulse,off_pending;
  wire [19:0] off_code;
  wire [31:0] off_id,off_first,off_pair;
  wire [4:0] off_flags;
  wire signed [20:0] off_diff;
  wire [3:0] off_error;
  caaz_pair_processor bypass(.clk(clk),.rst_n(rst_n),.epoch_clear(epoch_clear),
  .in_valid(in_valid),.in_code(in_code),.in_sample_id(in_sample_id),.in_chop_neg(in_chop_neg),
  .in_az_pair_id(in_az_pair_id),.in_az_valid(in_az_valid),.in_flags(in_flags),.in_analog_bad(in_analog_bad),
  .out_valid(off_valid),.out_code(off_code),.out_sample_id(off_id),.out_first_sample_id(off_first),
  .out_az_pair_id(off_pair),.out_flags(off_flags),.out_analog_bad(off_bad),.pair_difference(off_diff),
  .pair_difference_valid(off_diff_valid),.fault(off_fault),.fault_pulse(off_pulse),.fault_code(off_error),.pair_pending(off_pending));
  int checks=0,pairs=0,bypass_checks=0;
  int even_code_reference,even_sample_id_reference;
  int unsigned random_state=32'h8a619b07;
  int unsigned stimulus_sample_id,stimulus_az_pair_id;
  task automatic require_ok(input bit ok,input string message);
    checks++;
    if(!ok)$fatal(1,"%s",message);
  endtask
  task automatic check_bypass();
    bypass_checks++;
    require_ok(off_valid===in_valid && off_code===in_code && off_id===in_sample_id && off_flags===in_flags && off_bad===in_analog_bad,"default-off changed old fields/latency");
    require_ok(!off_diff_valid&&!off_fault&&!off_pulse&&!off_pending&&off_diff==0&&off_error==0,"default-off sideband");
  endtask
  task automatic clear_case();
    @(negedge clk);
    epoch_clear=1;
    in_valid=0;
    #1;
    check_bypass();
    @(posedge clk);
    #1;
    require_ok(!out_valid&&!fault&&!pair_pending,"epoch clear did not cancel");
    @(negedge clk);
    epoch_clear=0;
    #1;
  endtask
  task automatic send_sample(input int code,input logic[31:0] id,input bit neg,input logic[31:0] pair_id,
    input logic[4:0] flags,input bit bad,input bit az_ok,input bit emit_pair,input int expected_error,input bit pulse);
    real ve,vo,vpair;
    int expected_code,expected_diff;
    @(negedge clk);
    in_valid=1;
    in_code=20'(code);
    in_sample_id=id;
    in_chop_neg=neg;
    in_az_pair_id=pair_id;
    in_flags=flags;
    in_analog_bad=bad;
    in_az_valid=az_ok;
    #1;
    check_bypass();
    @(posedge clk);
    #1;
    require_ok(out_valid===emit_pair && pair_difference_valid===emit_pair,"pair valid mismatch");
    require_ok(fault===(expected_error!=0) && fault_code==4'(expected_error) && fault_pulse===pulse,"fault state/pulse mismatch");
    if(emit_pair)
    begin
      // Independent reference starts with the public CodeStream voltage centers.
      ve=(real'(even_code_reference)+0.5)-524288.0;
      vo=(real'(code)+0.5)-524288.0;
      vpair=(ve-vo)/2.0;
      expected_code=$rtoi($floor(vpair+524288.0));
      expected_diff=even_code_reference-code;
      require_ok(out_code==20'(expected_code),"bin-center/uniform-floor disagreement");
      require_ok(int'($signed(pair_difference))==expected_diff,"21bit half-LSB difference mismatch");
      require_ok(out_first_sample_id==32'(even_sample_id_reference)&&out_sample_id==id&&out_az_pair_id==pair_id,"pair metadata mismatch");
      require_ok(out_flags==0&&!out_analog_bad,"valid pair flag");
      pairs++;
    end
    else if(!neg && expected_error==0)
    begin
      even_code_reference=code;
      even_sample_id_reference=int'(id);
    end
  endtask
  task automatic even_sample(input int code,input logic[31:0] id,input logic[31:0] pair_id);
    send_sample(code,id,0,pair_id,0,0,1,0,0,0);
  endtask
  task automatic odd_sample(input int code,input logic[31:0] id,input logic[31:0] pair_id);
    send_sample(code,id,1,pair_id,0,0,1,1,0,0);
  endtask
  initial
  begin
    repeat(2)@(negedge clk);
    rst_n=1;
    clear_case();
    // ID parity deliberately disagrees with physical chop phase.
    even_sample(0,123,77);
    odd_sample(1048575,124,77);
    even_sample(1048575,125,78);
    odd_sample(0,126,78);
    even_sample(0,127,79);
    odd_sample(0,128,79);
    even_sample(1048575,129,80);
    odd_sample(1048575,130,80);
    even_sample(524288,131,81);
    odd_sample(524288,132,81);
    even_sample(524289,133,82);
    odd_sample(524288,134,82);
    // Deterministic different inputs, including an idle clk gap, never validation tuning.
    stimulus_sample_id=135;
    stimulus_az_pair_id=83;
    for(int n=0;n<512;n++)
    begin
      random_state=1664525*random_state+1013904223;
      even_sample(int'(random_state&32'hfffff),stimulus_sample_id,stimulus_az_pair_id);
      @(negedge clk);
      in_valid=0;
      #1;
      check_bypass();
      @(posedge clk);
      #1;
      require_ok(!out_valid,"idle fabricated pair");
      random_state=1664525*random_state+1013904223;
      odd_sample(int'(random_state&32'hfffff),stimulus_sample_id+1,stimulus_az_pair_id);
      stimulus_sample_id+=2;
      stimulus_az_pair_id++;
    end
    // Common AZ integer sample cancels in each aligned pair.
    even_sample(700000+11,stimulus_sample_id,stimulus_az_pair_id);
    odd_sample(1048575-650000+11,stimulus_sample_id+1,stimulus_az_pair_id);
    stimulus_sample_id+=2;
    stimulus_az_pair_id++;
    even_sample(700000-17,stimulus_sample_id,stimulus_az_pair_id);
    odd_sample(1048575-650000-17,stimulus_sample_id+1,stimulus_az_pair_id);
    clear_case();
    even_sample(10,10,5);
    send_sample(20,12,1,5,0,0,1,0,4,1);
    send_sample(20,13,1,5,0,0,1,0,4,0);
    // sticky quarantine, no silent resync
    clear_case();
    send_sample(20,11,1,5,0,0,1,0,5,1);
    clear_case();
    even_sample(10,10,5);
    send_sample(20,11,0,5,0,0,1,0,5,1);
    clear_case();
    even_sample(10,10,5);
    send_sample(20,11,1,6,0,0,1,0,6,1);
    for(int f=0;f<5;f++)
    begin
      clear_case();
      even_sample(0,10,5);
      send_sample(1048575,11,1,5,5'(1<<f),0,1,0,1,1);
    end
    clear_case();
    even_sample(1,10,5);
    send_sample(2,11,1,5,0,1,1,0,2,1);
    clear_case();
    send_sample(1,10,0,5,0,0,0,0,3,1);
    clear_case();
    even_sample(1,10,5);
    odd_sample(2,11,5);
    send_sample(3,14,0,6,0,0,1,0,4,1);
    clear_case();
    even_sample(1,10,5);
    odd_sample(2,11,5);
    send_sample(3,12,0,7,0,0,1,0,6,1);
    clear_case();
    even_sample(1,10,5);
    @(negedge clk);
    epoch_clear=1;
    in_valid=1;
    in_code=2;
    in_sample_id=11;
    in_chop_neg=1;
    #1;
    check_bypass();
    @(posedge clk);
    #1;
    require_ok(!out_valid&&!fault&&!pair_pending,"clear+odd committed a stale pair");
    @(negedge clk);
    epoch_clear=0;
    in_valid=0;
    even_sample(0,32'hfffffffe,32'hffffffff);
    odd_sample(1048575,32'hffffffff,32'hffffffff);
    even_sample(1048575,0,0);
    odd_sample(0,1,0);
    clear_case();
    even_sample(1,10,5);
    @(negedge clk);
    rst_n=0;
    in_valid=0;
    @(posedge clk);
    #1;
    require_ok(!out_valid&&!fault&&!pair_pending,"reset retained pending");
    @(negedge clk);
    rst_n=1;
    send_sample(2,11,1,5,0,0,1,0,5,1);
    $display("CAAZ_PAIR_PROCESSOR_COMPLETE checks=%0d pairs=%0d default_off_checks=%0d source=BIN_CENTER_UNIFORM_FLOOR_ONLY",checks,pairs,bypass_checks);
    $finish;
  end
  initial
  begin
    #100000;
    $fatal(1,"watchdog");
  end
endmodule
