`timescale 1ns/1ps
module analog_status_boundary_tb;
  logic clk=0; always #5 clk=~clk;
  logic rst_n=0, enable=0, capture=0, quiet=0, current_valid=0;
  logic analog_bad=0, current_bad=0, launch=0, clr=0;
  logic [31:0] current_id=0;
  wire fine_valid, fine_bad, analog_sticky;
  wire [31:0] fine_id, status_word;
  wire event_analog = launch && fine_bad;
  cal_sample_context ctx(
    .clk(clk),.rst_n(rst_n),.enable(enable),.residue_capture(capture),.quiet_sample(quiet),
    .current_valid(current_valid),.current_id(current_id),.current_slices(40'd0),
    .current_main(504'd0),.current_sub(64'd0),.current_rails(4'd0),.current_sampling(1'b0),
    .current_injection(64'sd0),.analog_bad(analog_bad),.current_bad(current_bad),
    .fine_valid(fine_valid),.fine_id(fine_id),.fine_slices(),.fine_main(),.fine_sub(),
    .fine_rails(),.fine_sampling(),.fine_analog_bad(fine_bad),.fine_injection());
  status_regs sts(.clk(clk),.rst_n(rst_n),.clr(clr),.ev_acc_ovf(1'b0),.ev_gain_err(1'b0),
    .ev_adc2_ovf(1'b0),.ev_clip_low(1'b0),.ev_clip_high(1'b0),.ev_analog_ovf(event_analog),
    .err_code(32'd0),.acc_ovf_sticky(),.gain_err_sticky(),.adc2_ovf_sticky(),
    .clip_low_last(),.clip_high_last(),.analog_ovf_sticky(analog_sticky),
    .status_word(status_word),.status_clr_value());
  integer fd, checks=0, edge_count=0; string label;
  task automatic step(input string name,input bit exp_valid,input int exp_id,input bit exp_bad,input bit exp_sticky);
    @(posedge clk); #0.001; edge_count++;
    if (fine_valid!==exp_valid || fine_id!==32'(exp_id) || fine_bad!==exp_bad || analog_sticky!==exp_sticky)
      $fatal(1,"boundary check %s mismatch: valid=%0d id=%0d bad=%0d sticky=%0d",name,fine_valid,fine_id,fine_bad,analog_sticky);
    checks++;
    $fdisplay(fd,"%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",edge_count,name,capture,quiet,current_id,current_bad,analog_bad,launch,clr,fine_valid,fine_id,fine_bad,event_analog,analog_sticky);
    @(negedge clk);
  endtask
  initial begin
    fd=$fopen("analog_status_boundary_trace.csv","w");
    if(fd==0) $fatal(1,"cannot open trace");
    $fdisplay(fd,"edge,scenario,capture,quiet,current_id,current_bad,analog_bad,launch,clear,fine_valid,fine_id,fine_analog_bad,top_equivalent_event,analog_sticky");
    step("reset",0,0,0,0);
    rst_n=1; enable=1; current_valid=1; current_id=101; capture=1; current_bad=1;
    step("rdac_bad_frozen_at_capture",0,0,0,0);
    capture=0; quiet=1; current_bad=0;
    step("rdac_bad_transfer_to_fine",1,101,1,0);
    quiet=0; launch=1;
    step("launch_latches_sticky",1,101,1,1);
    launch=0; clr=1;
    step("clear_sticky",1,101,1,0);
    clr=0; capture=1; current_id=202;
    step("next_residue_capture",1,101,1,0);
    capture=0; quiet=1; analog_bad=1;
    step("analog_bad_sampled_at_quiet",1,202,1,0);
    quiet=0; analog_bad=0;
    step("no_launch_bad_retained_sticky_zero_1",1,202,1,0);
    step("no_launch_bad_retained_sticky_zero_2",1,202,1,0);
    capture=1; current_id=203;
    step("replacement_residue_capture",1,202,1,0);
    capture=0; quiet=1;
    step("cancelled_bad_context_replaced",1,203,0,0);
    quiet=0; launch=1;
    step("replacement_good_launch_cannot_report_old_bad",1,203,0,0);
    launch=0; capture=1; current_id=303; analog_bad=1;
    step("pulse_before_quiet_not_latched",1,203,0,0);
    capture=0; analog_bad=0; quiet=1;
    step("pulse_absent_at_quiet_not_reported",1,303,0,0);
    quiet=0; launch=1;
    step("launch_after_missing_hold",1,303,0,0);
    launch=0; enable=0;
    step("disable_cancels_context",0,0,0,0);
    $fclose(fd);
    $display("ANALOG_STATUS_BOUNDARY_PASS checks=%0d edges=%0d",checks,edge_count);
    $finish;
  end
endmodule
