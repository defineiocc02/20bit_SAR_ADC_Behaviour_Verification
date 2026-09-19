`timescale 1ns/1ps
module review_recon_protocol_tb;
  logic clk=0,rst_n=0,ready=0,start=0,clr=0;
  always #1 clk=~clk;
  logic [4:0] sid=0;
  logic [19:0] code=0,dout;
  logic valid,busy,gerr;
  logic [31:0] sample_id=0, result_id;
  logic [4:0] result_flags;
  int sequence_id=0;
  logic [0:0][2:0][47:0] weights;
  recon_core #(.P_N_ACTIVE(1),.P_N_MAIN(2),.P_N_SUB(1),.P_N_SLICES(1),.P_ADC2_BITS(20)) dut(
    .clk(clk),.rst_n(rst_n),.cfg_ready(ready),.start(start),.clr_ovf(clr),
    .sampling_mask_en(1'b0),.slice_id(sid),.main_on(2'd0),.sub_on(1'b0),.dither_rail(4'd0),
    .adc2_code(code),.inj_q(64'sd0),.w_rom(weights),.offset_q(64'sd0),
    .adc2_min_q(64'sd0),.adc2_max_q(64'sd8589934592),.dout(dout),.dout_valid(valid),
    .clip_low(),.clip_high(),.acc_ovf(),.gain_err(gerr),.adc2_ovf(),.busy(busy),
      .sample_id(sample_id), .result_sample_id(result_id), .result_flags(result_flags)
  );
  task automatic request(input int value);
    @(negedge clk); code=20'(value); start=1;
    sequence_id++; sample_id=32'(sequence_id);
    @(negedge clk); start=0; sample_id='1;
  endtask
  task automatic result(input int value,input bit gain_error);
    int n=0;
    while(!valid) begin @(negedge clk); n++; if(n>15) $fatal(1,"recon timeout"); end
    if(result_id!==32'(sequence_id)) $fatal(1,"sample ID not aligned with calibrated result");
    if(result_flags[2:0]!=={1'b0,gain_error,1'b0}) $fatal(1,"per-sample calibration error flags");
    if(dout!==20'(value) || gerr!==gain_error) $fatal(1,"recon result/flag mismatch");
    @(negedge clk);
  endtask
  initial begin
    weights[0][0]=48'd536870912; weights[0][1]=48'd268435456; weights[0][2]=48'd268435456;
    repeat(2) @(negedge clk); rst_n=1;
    request(123); if(busy) $fatal(1,"unconfigured start accepted");
    ready=1;
    repeat(16) begin @(negedge clk); if(valid) $fatal(1,"unconfigured request leaked after enable"); end
    request(73); result(73,0);
    request(99); repeat(3) @(negedge clk); ready=0;
    @(negedge clk); ready=1;
    repeat(16) begin @(negedge clk); if(valid) $fatal(1,"cancelled transaction leaked"); end
    request(101); result(101,0);
    sid=5'd31; request(500); result(101,1); // invalid physical index holds prior output
    sid=0; clr=1; @(negedge clk); clr=0;
    request(203); result(203,0);
    // Cancellation on the completion edge must also discard error events.
    weights='0; request(0);
    while(!dut.div_done) @(negedge clk);
    ready=0; @(negedge clk);
    if(valid || gerr) $fatal(1,"cancelled completion leaked an error event");
    ready=1;
    weights[0][0]=48'd536870912; weights[0][1]=48'd268435456; weights[0][2]=48'd268435456;
    request(304); result(304,0);
    $display("REVIEW_RECON_PROTOCOL_COMPLETE"); $finish;
  end
  initial begin #1000; $fatal(1,"recon protocol watchdog"); end
endmodule
