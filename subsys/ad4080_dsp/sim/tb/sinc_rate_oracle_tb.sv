`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : sinc_rate_oracle_tb.sv
// 模块名     : sinc_rate_oracle_tb
// 版本       : v1.0
// 功能       : 独立有限窗卷积检查。SINC1 为一次 N 点求和，SINC5 为五次求和。
// 设计说明   :
//   * 参考使用五个环形有限窗，64 位容纳 signed20*N^5 的精确整和。
//   * 所有 16 个抽取码都按当前工程的有效范围及最大 N 映射验证。
//   * 分开记录数学完整窗口和第五组，便于与外层 CNV 启动相位对应。
// 变更记录   : 2026-11-20 核对 SINC5 的末组停样排空与选择时序。
// =============================================================================
module sinc_rate_oracle_tb;
  // ==========================================================================
  // 1. 输入和两个独立的逐拍参考队列
  // ==========================================================================
  logic clk = 0, rst_n = 0, filter_sync = 0, valid_in = 0; // 停样仍保留核心时钟
  logic [19:0] data_in = 0; // 与两个叶都共享有符号位串
  logic [3:0] dec_sel = 0; // 全部编码逐一驱动，各叶依自己的范围钳位
  wire [19:0] data1, data5;
  wire valid1, valid5;
  bit active = 0; // 配置切换时先关计分，再同步清旧记录
  int cycle = 0, rate1 = 2, rate5 = 2, accepted = 0; // 核心拍与接受样本各计数
  int cases = 0, checks = 0, outputs1 = 0, outputs5 = 0;
  int case_outputs1 = 0, case_outputs5 = 0, positive_outputs = 0, negative_outputs = 0;
  int first_input_clock = 0; // 相对边沿 1 为本轮第一笔实际接受
  int first_complete_sample5 = 0, first_complete_due5 = 0, first_complete_clock5 = 0; // 数学支持已全部覆盖
  int fifth_due5 = 0, fifth_clock5 = 0; // 第五组对应 p.69 的数字部分
  longint signed sum1 = 0, scale5 = 32; // scale5 为五阶整增益 D^5
  longint signed window_sum [0:4]; // 有界窗口整和，无 DUT 的积分模回绕
  longint signed history [0:4][0:255]; // 每阶按接受样本序号共享环地址
  longint signed expected1[$], expected5[$]; // 可同时排队多个已产生的数值
  int due1[$], due5[$]; // 到期日按核心时钟，防止气泡隐去延迟

  always #5 clk <= ~clk;
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
    // 用地板除处理负数，避免 SV 默认向零除法掩盖 ±0.5 的差异。
    return numerator >= 0 ? numerator / denominator :
        -((-numerator + denominator - 1) / denominator);
  endfunction

  function automatic longint signed clamp20(input longint signed value);
    if (value < -524288) return -524288;
    if (value > 524287) return 524287;
    return value;
  endfunction

  initial forever begin : independent_reference
    longint signed sample_value, stage_input, next_sum, expected;
    logic [7:0] cursor;
    int due;
    @(posedge clk);
    cycle++;
    if (!rst_n || filter_sync) begin
      accepted = 0;
      first_input_clock = 0;
      first_complete_sample5 = 0;
      first_complete_due5 = 0;
      first_complete_clock5 = 0;
      fifth_due5 = 0;
      fifth_clock5 = 0;
      case_outputs1 = 0;
      case_outputs5 = 0;
      sum1 = 0;
      expected1.delete(); due1.delete();
      expected5.delete(); due5.delete();
      for (int stage = 0; stage < 5; stage++) begin
        window_sum[stage] = 0;
        for (int address = 0; address < 256; address++) history[stage][address] = 0;
      end
    end else if (active && valid_in) begin
      accepted++;
      if (accepted == 1) first_input_clock = cycle;
      sample_value = longint'($signed(data_in));
      sum1 += sample_value;
      if (accepted % rate1 == 0) begin
        expected1.push_back(floor_div(sum1 + longint'(rate1) / 64'sd2, longint'(rate1)));
        due1.push_back(cycle + 1);
        sum1 = 0;
      end

      // 每级都是长为 N 的有限窗口，逐级使用本次完整窗口和，无 CIC 积分反馈。
      cursor = 8'((accepted - 1) % rate5);
      stage_input = sample_value;
      for (int stage = 0; stage < 5; stage++) begin
        next_sum = window_sum[stage] + stage_input - history[stage][cursor];
        history[stage][cursor] = stage_input;
        window_sum[stage] = next_sum;
        stage_input = next_sum;
      end
      if (accepted % rate5 == 0) begin
        // 抽取事件选当前完整整和。一笔结果的提交间隔按核心拍而非新输入数计。
        expected5.push_back(clamp20(floor_div(stage_input + scale5 / 2, scale5)));
        due5.push_back(cycle + 4);
        // 五次有限窗的支持长度为 5*(D-1)+1。另记录手册启动公式的第 5 组。
        if (first_complete_sample5 == 0 && accepted >= 5*(rate5-1)+1) begin
          first_complete_sample5 = accepted;
          first_complete_due5 = cycle + 4;
        end
        if (accepted == 5*rate5) fifth_due5 = cycle + 4;
      end
    end
    #1; // 等待本沿的非阻塞更新，参考已按接受状态生成应答
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
        // 只有收到了逐字正确的真实 valid，才记录首次完整和第五组的核心周期。
        if (cycle == first_complete_due5) first_complete_clock5 = cycle - first_input_clock + 1;
        if (cycle == fifth_due5) fifth_clock5 = cycle - first_input_clock + 1;
        outputs5++; case_outputs5++;
        if ($signed(data5) > 0) positive_outputs++;
        if ($signed(data5) < 0) negative_outputs++;
      end else if (due5.size() > 0) require(due5[0] > cycle, "missing SINC5 output");
    end
  end

  // ==========================================================================
  // 2. 选率、启停和输出计数
  // ==========================================================================
  task automatic rate_case(input int code);
    int samples;
    logic signed [19:0] value;
    @(negedge clk);
    active = 0; valid_in = 0; filter_sync = 1; dec_sel = 4'(code);
    rate1 = code > 9 ? 1024 : (1 << (code + 1));
    rate5 = code > 7 ? 256 : (1 << (code + 1));
    scale5 = 1;
    repeat (5) scale5 *= longint'(rate5);
    @(negedge clk);
    filter_sync = 0; active = 1;
    // 故意在最后一个完整抽取点停样。无需再提供任何样本即可排空当前结果。
    samples = 40 * rate1;
    for (int index = 0; index < samples; index++) begin
      // 单脉冲检查初始历史，余下以有正负的动态码遍历窗口。
      if (index == 0) value = -524288;
      else if (index < 8 * rate5) value = 0;
      else value = 20'((index * 7919) % 1048576 - 524288);
      @(negedge clk); valid_in = 1; data_in = value;
      // 先以连续输入定位建立边界，再加入气泡来核对动态卷积与核心拍期望。
      if (index >= 5*rate5 && index % 5 == 0) begin @(negedge clk); valid_in = 0; end
    end
    @(negedge clk); valid_in = 0;
    repeat (10) @(negedge clk);
    require(accepted == samples, "accepted input count");
    require(case_outputs1 == samples / rate1, "SINC1 selected/clamped rate");
    require(case_outputs5 == samples / rate5, "SINC5 drains final decimated word without more inputs");
    require(first_complete_sample5 > 0 && first_complete_clock5 == first_complete_sample5 + 4
        && fifth_clock5 == 5*rate5 + 4, "SINC5 first full or fifth core boundary");
    require(expected1.size() == 0 && expected5.size() == 0 &&
        due1.size() == 0 && due5.size() == 0, "undrained output pipeline");
    cases++;
    $display("SINC_RATE_CASE code=%0d N1=%0d N5=%0d inputs=%0d outputs1=%0d outputs5=%0d",
        code, rate1, rate5, accepted, case_outputs1, case_outputs5);
    $display("SINC5_TIMING D=%0d first_full_sample=%0d first_full_core_edge=%0d fifth_core_edge=%0d",
        rate5, first_complete_sample5, first_complete_clock5, fifth_clock5);
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
