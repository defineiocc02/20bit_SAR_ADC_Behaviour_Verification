`timescale 1ns/1ps
module sar_trial_tb;
  logic clk=0,rst_n=0,start=0,cancel=0,cmp_valid=0,enable=1;
  always #1 clk=~clk;
  logic [8:0] seed;
  logic [11:0] target;
  wire [8:0] trial9,code9;
  wire [11:0] trial12,code12;
  wire busy9,done9,update9,en9,busy12,done12,update12,en12;
  sar_trial_ctrl u9(.clk(clk),.rst_n(rst_n),.enable(enable),.start(start),.cancel(cancel),
    .seed_code(seed),.cmp_valid(cmp_valid),.cmp_ge(target[8:0]>=trial9),
    .trial_code(trial9),.resolved_code(code9),.busy(busy9),.done(done9),
    .resolved_valid(update9),.compare_enable(en9));
  sar_trial_ctrl #(.P_BITS(12),.P_SEED_BITS(0)) u12(
    .clk(clk),.rst_n(rst_n),.enable(enable),.start(start),.cancel(cancel),
    .seed_code(12'd0),.cmp_valid(cmp_valid),.cmp_ge(target>=trial12),
    .trial_code(trial12),.resolved_code(code12),.busy(busy12),.done(done12),
    .resolved_valid(update12),.compare_enable(en12));
  initial begin
    bit seen9,seen12;
    int ticks;
    logic [8:0] saved9;
    logic [11:0] saved12;
    repeat(2) @(negedge clk);rst_n=1;
    for(int n=0;n<4096;n++) begin
      target=12'(n);seed={target[8:6],6'b0};start=1;cmp_valid=0;
      @(negedge clk);start=0;seen9=0;seen12=0;ticks=0;
      while(!seen9 || !seen12) begin
        saved9=trial9;saved12=trial12;
        cmp_valid=(ticks%3!=1);ticks++;
        @(negedge clk);
        if(!cmp_valid && (saved9!==trial9 || saved12!==trial12)) $fatal(1,"SAR trial changed before comparator valid");
        if(done9) begin
          if(seen9 || code9!==target[8:0]) $fatal(1,"coarse binary SAR mismatch");seen9=1;
        end
        if(done12) begin
          if(seen12 || code12!==target) $fatal(1,"fine binary SAR mismatch");seen12=1;
        end
        if(ticks>24) $fatal(1,"SAR liveness");
      end
      if(busy9 || busy12 || en9 || en12) $fatal(1,"SAR did not release comparator");
    end
    start=1;@(negedge clk);start=0;
    repeat(3) @(negedge clk);cancel=1;@(negedge clk);cancel=0;
    repeat(20) begin @(negedge clk);if(busy9 || busy12 || done9 || done12) $fatal(1,"cancelled SAR leaked");end
    $display("SAR_TRIAL_COMPLETE fine_codes=4096 coarse_codes=512 stalls_and_cancel=PASS");$finish;
  end
  initial begin #200000; $fatal(1,"SAR watchdog");end
endmodule
