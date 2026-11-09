// =============================================================================
// 文件名     : caaz20_pair_tb.sv
// 模块名     : caaz20_pair_tb
// 版本       : v1.0
// 功能       : CAAZ 配对的逐沿对拍测试台
//
// 设计说明   :
//   * 覆盖连续配对、stall、整数端点、负奇差、bypass 与非重叠约束。
//   * 断言失败意味着 ID 生命周期或 AZ 代对齐被破坏，不是单纯数值偏差。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// ===========================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 端口按功能分组，slot 0 在最低位；总线位宽与定点格式见同目录 SPEC。
// -------------------------------------------------------------------------
module caaz20_pair_tb;
  typedef struct packed {
    logic               acq_valid;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
    logic        [31:0] acq_id;  // 采集描述符的样本号，首笔用于填充流水。
    logic               chop_neg;  // 实际返回的斩波极性，须与该样本冻结状态一致。
    logic               az_capture;  // 该笔 RA 时窗的捕获意图，模拟成功确认另行检查。
    logic               az_hold;  // 该笔 RA 时窗的保持意图，不以采集时钟位置猜测。
    logic        [30:0] az_id;  // 共享 AZ 代，数字相同不代表模拟侧确实完成保持。
    logic               out_valid;  // 真实结果有效事件；反压时数据与归属应同时保持。
    logic signed [19:0] out_code;  // 最终有符号码，只在对应有效事务中比较。
    logic        [31:0] out_id;  // 已退休结果的样本号，与对应数字码同时核对。
    logic        [31:0] out_first_id;  // 同步低有效复位，必须取消在途样本；不能只清最终 valid。
    logic        [30:0] out_az_id;  // 共享 AZ 代，数字相同不代表模拟侧确实完成保持。
    logic               out_pair;  // 区分单笔旁路与两笔配对输出，计数不可混合。
    logic signed [20:0] out_difference;  // 解调前的完整有符号差值，负奇数的除二方向须另验。
    logic               fault;  // 故障保持状态，与故障脉冲及正常输出分别核对。
    logic               fault_pulse;  // 新异常的一拍事件，用于与持续故障状态分开计数。
    logic         [2:0] fault_code;  // 异常分类观测，不能仅凭输出未出现推断错误原因。
  } result_t;

  typedef struct packed {
    logic               rst_n;  // 同步低有效复位，必须取消在途样本；不能只清最终 valid。
    logic               clear;  // 清空在途事务的测试刺激，优先于同沿采样受理。
    logic               enable;  // 禁用与重新使能的生命周期刺激，禁止旧描述符复活。
    logic               caaz_enable;  // 选择旁路或非重叠两笔配对，输出计数按模式分别检查。
    logic               acq_valid;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
    logic        [31:0] acq_id;  // 采集描述符的样本号，首笔用于填充流水。
    logic               raw_valid;  // 事务有效位，须与同笔 ID、极性及 AZ 代一起核对。
    logic        [31:0] raw_id;  // 原始码所属样本，配对不能按到达数组位置猜测。
    logic signed [19:0] raw_code;  // 逐笔去扰重构后的有符号码，配对前不能先混合两笔。
    logic               raw_bad;  // 坏样本应进入异常路径，禁止用零码冒充正常测量值。
    logic               raw_chop_neg;  // 实际返回的斩波极性，须与该样本冻结状态一致。
    logic        [30:0] raw_az_id;  // 共享 AZ 代，数字相同不代表模拟侧确实完成保持。
    logic               raw_az_ok;  // 模拟宏对捕获／保持有效性的确认，缺失不得当作成功。
    result_t            expected;
  } vector_t;

  logic clk;  // 单域测试时钟，所有受理与结果检查以其上升沿为界。
  vector_t vector;
  result_t actual;

  caaz20_pair dut (
      .clk_i(clk),
      .rst_ni(vector.rst_n),
      .clear_i(vector.clear),
      .enable_i(vector.enable),
      .caaz_enable_i(vector.caaz_enable),
      .acq_valid_i(vector.acq_valid),
      .acq_sample_id_i(vector.acq_id),
      .raw_valid_i(vector.raw_valid),
      .raw_sample_id_i(vector.raw_id),
      .raw_code_i(vector.raw_code),
      .raw_bad_i(vector.raw_bad),
      .raw_chop_neg_i(vector.raw_chop_neg),
      .raw_az_pair_id_i(vector.raw_az_id),
      .raw_az_ok_i(vector.raw_az_ok),
      .acq_ctrl_valid_o(actual.acq_valid),
      .acq_ctrl_sample_id_o(actual.acq_id),
      .acq_chop_neg_o(actual.chop_neg),
      .acq_az_capture_o(actual.az_capture),
      .acq_az_hold_o(actual.az_hold),
      .acq_az_pair_id_o(actual.az_id),
      .out_valid_o(actual.out_valid),
      .out_code_o(actual.out_code),
      .out_sample_id_o(actual.out_id),
      .out_first_sample_id_o(actual.out_first_id),
      .out_az_pair_id_o(actual.out_az_id),
      .out_pair_o(actual.out_pair),
      .out_difference_o(actual.out_difference),
      .fault_o(actual.fault),
      .fault_pulse_o(actual.fault_pulse),
      .fault_code_o(actual.fault_code)
  );

  task automatic replay(input string path);
    vector_t record;
    int stream;
    int read_status;
    int cycles;
    int controls;
    int pairs;
    int bypass;
    int faults;
    logic [31:0] boundary_id;

    cycles = 0;
    controls = 0;
    pairs = 0;
    bypass = 0;
    faults = 0;
    for (int bytes = 1; bytes <= 4; bytes++) begin
      boundary_id = 32'hffffffff >> (32 - 8 * bytes);
      assert (dut.next_id(boundary_id) == boundary_id + 32'd1)
      else $fatal(1, "incorrect ID rollover at %h", boundary_id);
    end
    stream = $fopen(path, "r");
    if (stream == 0) $fatal(1, "cannot open vector stream");
    read_status = $fscanf(stream, "%h\n", record);
    while (read_status == 1) begin
      vector = record;
      #1;
      clk = 1'b1;
      #1;
      if (actual !== vector.expected) begin
        $fatal(1, "cycle %0d expected %h got %h", cycles, vector.expected, actual);
      end
      assert (!(actual.az_capture && actual.az_hold)) else $fatal(1, "AZ controls overlap");
      assert (!(actual.fault && (actual.acq_valid || actual.out_valid)))
      else $fatal(1, "fault accepted a transaction");
      if (actual.out_valid && actual.out_pair) begin
        assert (actual.out_id[0] && actual.out_first_id + 32'd1 == actual.out_id &&
                actual.out_az_id == actual.out_id[31:1])
        else $fatal(1, "output is not a nonoverlapping pair");
      end
      cycles += 1;
      if (actual.acq_valid) controls += 1;
      if (actual.out_valid && actual.out_pair) pairs += 1;
      if (actual.out_valid && !actual.out_pair) bypass += 1;
      if (actual.fault_pulse) faults += 1;
      clk = 1'b0;
      read_status = $fscanf(stream, "%h\n", record);
    end
    if (!$feof(stream)) $fatal(1, "malformed vector after cycle %0d", cycles);
    $fclose(stream);
    $display("CAAZ20_COMPLETE cycles=%0d controls=%0d pairs=%0d bypass=%0d faults=%0d id_boundary_checks=4",
             cycles, controls, pairs, bypass, faults);
  endtask

  initial begin
    string path;
    clk = 1'b0;
    vector = '0;
    if (!$value$plusargs("vectors=%s", path)) $fatal(1, "missing +vectors");
    replay(path);
    $finish;
  end
endmodule
