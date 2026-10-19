`timescale 1ns/1ps

// Exhaustive reduced-width checks complement the independent 63/64-bit Python
// oracle. Exercise numerator/divisor width relations and padded unroll groups.
module divider_borrow_case #(
    parameter int WA = 6, WD = 8, STAGES = 7
) (
    output logic complete = 0
);
  logic clk = 0, rst_n = 0, start = 0;
  always #1 clk = ~clk;
  logic signed [WA-1:0] a, q;
  logic [WD-1:0] d;
  wire err, busy, done;
  int checks = 0;
  localparam int LAT = (WA + STAGES - 1) / STAGES;
  div_floor #(.P_W_A(WA), .P_W_D(WD), .P_STAGES(STAGES)) dut (
      .clk(clk), .rst_n(rst_n), .start(start), .a(a), .d(d),
      .q(q), .err(err), .busy(busy), .done(done)
  );

  initial begin
    int expected, lat;
    logic signed [WA-1:0] held_q;
    logic held_err;
    a = 0;
    d = 0;
    repeat (2) @(negedge clk);
    rst_n = 1;
    for (int numerator = -(1 << (WA-1)); numerator < (1 << (WA-1)); numerator++) begin
      for (int denominator = 0; denominator < (1 << WD); denominator++) begin
        expected = (denominator == 0) ? 0 : numerator / denominator;
        if (denominator != 0 && numerator < 0 && numerator % denominator != 0) expected--;
        held_q = q;
        held_err = err;
        a = WA'(numerator);
        d = WD'(denominator);
        start = 1;
        @(negedge clk);
        start = 0;
        lat = 0;
        if (!busy || done || q !== held_q || err !== held_err)
          $fatal(1, "acceptance must assert busy and hold the preceding result");
        while (!done) begin
          @(negedge clk);
          lat++;
          // Only inject rejected requests while busy, including changed high d bits.
          start = busy && (lat == 1);
          if (start) begin
            a = WA'(1);
            d = '1;
          end
          if (!done && (!busy || q !== held_q || err !== held_err))
            $fatal(1, "busy result changed before completion");
          if (lat > LAT)
            $fatal(1, "divider liveness width=%0d/%0d stages=%0d", WA, WD, STAGES);
        end
        start = 0;
        if (int'($signed(q)) != expected || err != (denominator == 0) || lat != LAT || busy)
          $fatal(1, "floor mismatch width=%0d/%0d stages=%0d a=%0d d=%0d q=%0d expected=%0d err=%b latency=%0d",
                 WA, WD, STAGES, numerator, denominator, $signed(q), expected, err, lat);
        if (denominator != 0 && !(int'($signed(q)) * denominator <= numerator &&
                                  numerator < (int'($signed(q)) + 1) * denominator))
          $fatal(1, "division defining inequality failed");
        checks++;
        @(negedge clk);
        if (done || busy || int'($signed(q)) != expected || err != (denominator == 0))
          $fatal(1, "idle result must hold and done must be a one-cycle pulse");
      end
    end
    if (checks != (1 << WA) * (1 << WD)) $fatal(1, "wrong exhaustive coverage");
    $display("DIVIDER_BORROW_CASE_PASS numerator_bits=%0d denominator_bits=%0d stages=%0d checks=%0d",
             WA, WD, STAGES, checks);
    complete = 1;
  end
endmodule

module divider_borrow_tb;
  wire [5:0] complete;
  divider_borrow_case #(.WA(6), .WD(8), .STAGES(1)) d1(.complete(complete[0]));
  divider_borrow_case #(.WA(6), .WD(8), .STAGES(2)) d2(.complete(complete[1]));
  divider_borrow_case #(.WA(6), .WD(8), .STAGES(3)) d3(.complete(complete[2]));
  divider_borrow_case #(.WA(6), .WD(8), .STAGES(7)) d7(.complete(complete[3]));
  divider_borrow_case #(.WA(6), .WD(5), .STAGES(5)) narrow(.complete(complete[4]));
  divider_borrow_case #(.WA(2), .WD(1), .STAGES(1)) minimal(.complete(complete[5]));
  initial begin
    wait (&complete);
    $display("DIVIDER_BORROW_COMPLETE exhaustive_checks=67592");
    $finish;
  end
  initial begin
    #400000;
    $fatal(1, "divider exhaustive watchdog");
  end
endmodule
