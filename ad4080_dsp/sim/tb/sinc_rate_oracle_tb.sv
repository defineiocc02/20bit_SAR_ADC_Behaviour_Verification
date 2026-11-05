`timescale 1ns/1ps
`default_nettype none
// 独立有限窗卷积检查：SINC1 为一次 N 点求和，SINC5 为五次 N 点求和。
// 不复用 DUT 的积分器、梳状差分或有限位宽状态。64 bit 容纳 signed20*N^5。
// 全部 16 个抽取码均检查；大于有效码的映射按已声明的最大 N 处理。
// 5 个初始抽取事件及 3 个 clk 输出流水属于本工程数值合同，不是器件时序披露。
module sinc_rate_oracle_tb;
  logic clk = 0, rst_n = 0, filter_sync = 0, valid_in = 0;
  logic [19:0] data_in = 0;
  logic [3:0] dec_sel = 0;
  wire [19:0] data1, data5;
  wire valid1, valid5;
  bit active = 0;
  int cycle = 0, rate1 = 2, rate5 = 2, accepted = 0;
  int cases = 0, checks = 0, outputs1 = 0, outputs5 = 0;
  int case_outputs1 = 0, case_outputs5 = 0, positive_outputs = 0, negative_outputs = 0;
  longint signed sum1 = 0, scale5 = 32;
  longint signed window_sum [0:4];
  longint signed history [0:4][0:255];
  longint signed mathematical5[$], expected1[$], expected5[$];
  int due1[$], due5[$];

  always #5 clk = ~clk;
  sinc1_decim u_sinc1 (
      .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync), .valid_in(valid_in),
      .data_in(data_in), .dec_sel(dec_sel), .data_out(data1), .valid_out(valid1)
  );
  sinc5_decim u_sinc5 (
      .clk(clk), .rst_n(rst_n), .filter_sync(filter_sync), .valid_in(valid_in),
      .data_in(data_in), .dec_sel(dec_sel), .data_out(data5), .valid_out(valid5)
  );

  task automatic require(input bit condition, input string reason);
    checks++;
    if (!condition) $fatal(1, "SINC_RATE_ORACLE: %s", reason);
  endtask

  function automatic longint signed floor_div(
      input longint signed numerator, input longint signed denominator
  );
    return numerator >= 0 ? numerator / denominator :
        -((-numerator + denominator - 1) / denominator);
  endfunction

  function automatic longint signed clamp20(input longint signed value);
    if (value < -524288) return -524288;
    if (value > 524287) return 524287;
    return value;
  endfunction

  always @(posedge clk) begin : independent_reference
    longint signed sample_value, stage_input, next_sum, expected;
    int cursor, due;
    cycle++;
    if (!rst_n || filter_sync) begin
      accepted = 0;
      case_outputs1 = 0;
      case_outputs5 = 0;
      sum1 = 0;
      expected1.delete(); due1.delete();
      expected5.delete(); due5.delete();
      mathematical5.delete();
      repeat (5) mathematical5.push_back(0);
      for (int stage = 0; stage < 5; stage++) begin
        window_sum[stage] = 0;
        for (int address = 0; address < 256; address++) history[stage][address] = 0;
      end
    end else if (active && valid_in) begin
      accepted++;
      sample_value = longint'($signed(data_in));
      sum1 += sample_value;
      if (accepted % rate1 == 0) begin
        expected1.push_back(floor_div(sum1 + longint'(rate1) / 64'sd2, longint'(rate1)));
        due1.push_back(cycle + 1);
        sum1 = 0;
      end

      // 每级都是长为 N 的有限窗口，逐级使用本次完整窗口和，无 CIC 积分反馈。
      cursor = (accepted - 1) % rate5;
      stage_input = sample_value;
      for (int stage = 0; stage < 5; stage++) begin
        next_sum = window_sum[stage] + stage_input - history[stage][cursor];
        history[stage][cursor] = stage_input;
        window_sum[stage] = next_sum;
        stage_input = next_sum;
      end
      if (accepted % rate5 == 0)
        mathematical5.push_back(clamp20(floor_div(stage_input + scale5 / 2, scale5)));
      if (accepted > 5 && (accepted - 5) % rate5 == 0) begin
        require(mathematical5.size() > 0, "empty mathematical convolution queue");
        expected5.push_back(mathematical5.pop_front());
        due5.push_back(cycle + 3);
      end
    end
    #1;
    if (rst_n && active && !filter_sync) begin
      if (valid1) begin
        require(expected1.size() > 0 && due1.size() > 0, "unexpected SINC1 output");
        expected = expected1.pop_front(); due = due1.pop_front();
        require(due == cycle, "SINC1 output latency");
        require(longint'($signed(data1)) == expected, "SINC1 independent block average");
        outputs1++; case_outputs1++;
        if ($signed(data1) > 0) positive_outputs++;
        if ($signed(data1) < 0) negative_outputs++;
      end else if (due1.size() > 0) require(due1[0] > cycle, "missing SINC1 output");
      if (valid5) begin
        require(expected5.size() > 0 && due5.size() > 0, "unexpected SINC5 output");
        expected = expected5.pop_front(); due = due5.pop_front();
        require(due == cycle, "SINC5 output latency");
        require(longint'($signed(data5)) == expected, "SINC5 independent finite-window convolution");
        outputs5++; case_outputs5++;
        if ($signed(data5) > 0) positive_outputs++;
        if ($signed(data5) < 0) negative_outputs++;
      end else if (due5.size() > 0) require(due5[0] > cycle, "missing SINC5 output");
    end
  end

  task automatic rate_case(input int code);
    int samples, value;
    @(negedge clk);
    active = 0; valid_in = 0; filter_sync = 1; dec_sel = 4'(code);
    rate1 = code > 9 ? 1024 : (1 << (code + 1));
    rate5 = code > 7 ? 256 : (1 << (code + 1));
    scale5 = 1;
    repeat (5) scale5 *= longint'(rate5);
    @(negedge clk);
    filter_sync = 0; active = 1;
    samples = 40 * rate1 + 5;
    for (int index = 0; index < samples; index++) begin
      if (index == 0) value = -524288;
      else if (index < 8 * rate5) value = 0;
      else value = (index * 7919) % 1048576 - 524288;
      @(negedge clk); valid_in = 1; data_in = 20'(value);
      if (index % 5 == 0) begin @(negedge clk); valid_in = 0; end
    end
    @(negedge clk); valid_in = 0;
    repeat (10) @(negedge clk);
    require(accepted == samples, "accepted input count");
    require(case_outputs1 == samples / rate1, "SINC1 selected/clamped rate");
    require(case_outputs5 == (samples - 5) / rate5, "SINC5 selected/clamped rate");
    require(expected1.size() == 0 && expected5.size() == 0 &&
        due1.size() == 0 && due5.size() == 0, "undrained output pipeline");
    cases++;
    $display("SINC_RATE_CASE code=%0d N1=%0d N5=%0d inputs=%0d outputs1=%0d outputs5=%0d",
        code, rate1, rate5, accepted, case_outputs1, case_outputs5);
    active = 0;
  endtask

  initial begin
    repeat (3) @(negedge clk);
    rst_n = 1;
    for (int code = 0; code < 16; code++) rate_case(code);
    require(cases == 16 && outputs1 > 600 && outputs5 > 1000 &&
        positive_outputs > 50 && negative_outputs > 50, "nonvacuous all-code signed coverage");
    $display("SINC_RATE_ORACLE_COMPLETE cases=%0d outputs1=%0d outputs5=%0d positive=%0d negative=%0d checks=%0d",
        cases, outputs1, outputs5, positive_outputs, negative_outputs, checks);
    $finish;
  end
  initial begin #10000000; $fatal(1, "SINC rate oracle watchdog"); end
endmodule
`default_nettype wire
