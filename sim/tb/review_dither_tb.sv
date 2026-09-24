`timescale 1ns/1ps
// Statistical regression, after startup. PMF matches round(uniform(-D,D)).
module review_dither_check #(parameter int D=2, parameter logic [31:0] SEED=32'h13579bdf)
  (output logic done=0);
  logic clk=0, rst_n=0, en=0, valid;
  logic signed [7:0] code;
  int hist[-D:D];
  int count, v, prev, pairs;
  logic [31:0] held;
  real total, square, cross_sum, expected, delta, mean, variance, corr;
  always #5 clk=~clk;
  dither_gen #(.D(D), .SEED(SEED)) dut(.*,.dither_code(code));
  initial begin
    count=0; total=0; square=0; cross_sum=0; pairs=0; prev=0;
    for(int k=-D;k<=D;k++) hist[k]=0;
    repeat(2) @(negedge clk);
    rst_n=1;
    held=dut.lfsr;
    repeat(3) @(negedge clk);
    if(dut.lfsr !== held) $fatal(1,"disabled generator moved");
    en=1;
    repeat(64) @(negedge clk);
    for(int n=0;n<200000;n++) begin
      @(negedge clk);
      if(valid) begin
        v=int'(code);
        if(v < -D || v > D) $fatal(1,"out of support D=%0d",D);
        hist[v]++; total+=v; square+=real'(v)*v;
        if(count>0) begin cross_sum+=real'(prev)*v; pairs++; end
        prev=v; count++;
      end
    end
    if(count<100000) $fatal(1,"too few accepted draws");
    mean=total/count; variance=square/count-mean*mean;
    if(D>0) begin
      if(mean < -0.025*D || mean > 0.025*D) $fatal(1,"mean D=%0d: %f",D,mean);
      for(int k=-D;k<=D;k++) begin
        expected=real'(count)*((k==-D || k==D)?1.0:2.0)/(4.0*D);
        delta=hist[k]-expected;
        if(delta*delta>49.0*expected) $fatal(1,"PMF D=%0d bin=%0d count=%0d expected=%f",D,k,hist[k],expected);
      end
      corr=(cross_sum/pairs-mean*mean)/variance;
      if(corr < -0.03 || corr > 0.03) $fatal(1,"lag1 correlation D=%0d: %f",D,corr);
    end else if(hist[0]!=count) $fatal(1,"D=0 nonzero draw");
    en=0; held=dut.lfsr;
    repeat(3) @(negedge clk);
    if(dut.lfsr !== held) $fatal(1,"hold after running failed");
    rst_n=0; @(negedge clk);
    if(dut.lfsr !== SEED) $fatal(1,"reset did not restore seed");
    $display("DITHER PASS D=%0d seed=%h accepted=%0d mean=%f corr=%f",D,SEED,count,mean,corr);
    done=1;
  end
endmodule
module review_dither_tb;
  wire [6:0] done;
  review_dither_check #(.D(0)) c0(done[0]);
  review_dither_check #(.D(1)) c1(done[1]);
  review_dither_check #(.D(2)) c2(done[2]);
  review_dither_check #(.D(3)) c3(done[3]);
  review_dither_check #(.D(63)) c63(done[4]);
  review_dither_check #(.D(2),.SEED(32'h1)) ca(done[5]);
  review_dither_check #(.D(2),.SEED(32'hffffffff)) cb(done[6]);
  initial begin wait(&done); $display("REVIEW_DITHER_COMPLETE"); $finish; end
  initial begin #2100000; $fatal(1,"dither timeout"); end
endmodule
