// =============================================================================
// 文件名     : sar20_lms_recon_tb.sv
// 模块名     : sar20_lms_recon_tb
// 版本       : v1.0
// 功能       : 片上重构的逐沿对拍测试台
//
// 设计说明   :
//   * repeat (3) tick() 对应 E1~E3：累加器已初始化，首槽尚未加进来。
//   * 配置未完成即禁止调度；畸形配置字与 ready 反压必须分别验。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 端口按功能分组，slot 0 在最低位；总线位宽与定点格式见同目录 SPEC。
// -------------------------------------------------------------------------
module sar20_lms_recon_tb;
  // 把一个上升沿要驱动的全部激励打成一整包；向量文件按 $bits 宽度读成一整行十六进制。
  typedef struct packed {
    logic rst_n, clear, cfg_start, cfg_valid;
    logic [25:0] cfg_data;
    logic in_valid;
    logic [31:0] id;
    logic in_bad;
    logic [39:0] ids;
    logic [135:0] main;
    logic [39:0] conv_sub, sample_sub;
    logic [11:0] adc2;
    logic out_ready;
  } stimulus_t;

  // clk 单独驱动，其余激励统一放在 v 里逐拍覆盖；观测信号单独引出便于 $fwrite。
  logic clk;
  stimulus_t v;
  logic cfg_start_ready, cfg_ready, configured, cfg_error;
  logic in_ready, out_valid, out_bad;
  logic [31:0] out_id;
  logic signed [19:0] code;

  // 端口逐一名写而非 .*：DUT 名与夹具名不同（rst_ni/rst_n、id/in_sample_id_i 等），
  // 显式映射才能让向量包 v 的每一位落点可核对。
  sar20_lms_recon dut (
      .clk_i(clk), .rst_ni(v.rst_n), .clear_i(v.clear),
      .cfg_start_i(v.cfg_start), .cfg_start_ready_o(cfg_start_ready),
      .cfg_valid_i(v.cfg_valid), .cfg_ready_o(cfg_ready), .cfg_data_i(v.cfg_data),
      .configured_o(configured), .cfg_error_o(cfg_error),
      .in_valid_i(v.in_valid), .in_ready_o(in_ready), .in_sample_id_i(v.id), .in_bad_i(v.in_bad),
      .in_slice_ids_i(v.ids), .in_main_i(v.main), .in_conv_sub_i(v.conv_sub),
      .in_sample_sub_i(v.sample_sub), .in_adc2_i(v.adc2), .out_valid_o(out_valid),
      .out_ready_i(v.out_ready), .out_sample_id_o(out_id), .out_code_o(code), .out_bad_o(out_bad)
  );

  // 两次 #1 各半拍，clk 在中间拉高、末尾拉低；一次 tick 恰为一个上升沿，
  // 组合握手信号必须在两次 #1 之前先稳定，否则 DUT 采到的是旧值。
  task automatic tick;
    #1;
    clk = 1'b1;
    #1;
    clk = 1'b0;
  endtask

  // 先把 396 个配置字装成「零权重 + 合法增益」，使 MAC 累加器只含 ADC2 midpoint，
  // 从而 check_midpoint 能单独验证 midpoint 解码，不受训练权重干扰。
  task automatic configure_midpoint_checks;
    v = '0;
    tick();
    v.rst_n = 1'b1;
    v.cfg_start = 1'b1;
    #1;
    if (!cfg_start_ready) $fatal(1, "cannot start midpoint coefficient load");
    tick();
    v.cfg_start = 1'b0;
    v.cfg_valid = 1'b1;
    // 396 = 18 片 ×（21 个权重 + 1 个增益）；每片第 22 字（word%22==21）是增益字。
    for (int word = 0; word < 396; word++) begin
      // 增益填 2·2^24，落在 [2^24,3·2^24] 合法区间；权重字全 0。
      v.cfg_data = (word % 22 == 21) ? 26'd33554432 : 26'd0;
      #1;
      if (!cfg_ready) $fatal(1, "midpoint coefficient %0d was not accepted", word);
      tick();
    end
    v.cfg_valid = 1'b0;
    // 失败意味着装载被中途拒字或 396 字未凑齐，后续 midpoint 检查的系数前提就不成立。
    if (!configured || cfg_error) $fatal(1, "midpoint coefficient load failed");
    // 八个 slot 的片号设成 0..7，保证每拍读到的都是合法且互不重复的物理片。
    for (int slot = 0; slot < 8; slot++) v.ids[5*slot+:5] = 5'(slot);
  endtask

  // 送一个 ADC2 码进核，在首槽累加前读内部累加器，核对 midpoint 的 Q28 整数值。
  task automatic check_midpoint(input logic [11:0] adc2, input logic signed [31:0] expected);
    v.adc2 = adc2;
    // 直接拿 adc2 当样本 ID，保证 4096 个向量各有唯一 ID，便于排错定位。
    v.id = {20'd0, adc2};
    v.in_valid = 1'b1;
    #1;
    if (!in_ready) $fatal(1, "ADC2 midpoint code %0d was not accepted", adc2);
    // E0 握手当沿把输入快照进寄存器，累加器此时还未写入初值。
    tick();  // E0
    v.in_valid = 1'b0;
    // E1 查 midpoint 三段、E2 汇总、E3 把 midpoint 写进 accum_num_q。
    // 再走 3 拍后累加器已初始化、首槽特征尚未加进来，正是读 accum_num_q 的窗口。
    repeat (3) tick();  // E1…E3：累加器已初始化，首槽尚未加进来。
    // 用层次引用直读 DUT 内部累加器；!== 连 X/Z 也判不等，expected 是 Python 给的 32 位有符号 Q28 值。
    if (dut.accum_num_q !== expected)
      $fatal(1, "ADC2 midpoint code %0d expected %0d got %0d", adc2, expected, dut.accum_num_q);
    // 每个向量后用 clear 冲掉在途流水，避免上一笔 MAC/除法状态污染下一笔。
    v.clear = 1'b1;
    tick();
    v.clear = 1'b0;
  endtask

  // 先跑 4096 个 midpoint 向量，再把归档激励逐拍灌入并写出观察文件给 Python 比对。
  task automatic check_vectors(input string vectors, input string midpoint, input string observation);
    // record 宽度 = $bits(stimulus_t)；第二个字段 59 位 = 3 位握手 + 56 位输出记录之和，
    // 是片外预存的期望观察值，这里只 fscanf 占位读入后丢弃，比较在仿真外完成。
    logic [$bits(stimulus_t)-1:0] record;
    logic [58:0] unused_expected;
    int stream, observed, read_status, cycles, midpoint_count;
    logic [43:0] mid;

    cycles = 0;
    midpoint_count = 0;
    configure_midpoint_checks();
    stream = $fopen(midpoint, "r");
    if (stream == 0) $fatal(1, "cannot open %s", midpoint);
    // mid[43:32] 是 12 位 ADC2 码，mid[31:0] 是该码期望的 32 位有符号 midpoint；
    // 行号必须等于 ADC2 码，正好覆盖 2^12 = 4096 个中点，顺序错或缺失都报错。
    read_status = $fscanf(stream, "%h\n", mid);
    while (read_status == 1) begin
      if (midpoint_count >= 4096 || mid[43:32] != 12'(midpoint_count))
        $fatal(1, "unexpected midpoint vector at row %0d", midpoint_count);
      check_midpoint(mid[43:32], $signed(mid[31:0]));
      midpoint_count += 1;
      read_status = $fscanf(stream, "%h\n", mid);
    end
    // 循环结束必须是自然 EOF，读到半行坏数据说明向量文件被截断。
    if (!$feof(stream)) $fatal(1, "malformed midpoint vectors");
    $fclose(stream);

    stream = $fopen(vectors, "r");
    observed = $fopen(observation, "w");
    if (stream == 0 || observed == 0) $fatal(1, "cannot open vector or observation stream");
    // 每行两个十六进制字段：激励包 record 与被丢弃的期望值；read_status==2 才是一行完整记录。
    read_status = $fscanf(stream, "%h %h\n", record, unused_expected);
    while (read_status == 2) begin
      v = stimulus_t'(record);
      #1;
      // 上升沿前记录本沿真正生效的三组握手（各 ready && valid），供 Python 核对受理时刻。
      $fwrite(observed, "%h ", {cfg_start_ready && v.cfg_start, cfg_ready && v.cfg_valid,
                              in_ready && v.in_valid});
      clk = 1'b1;
      #1;
      // 上升沿后记录输出；无 out_valid 时用 53'd0 补齐，使每行位宽恒定便于片外解析。
      $fwrite(observed, "%h\n", {configured, cfg_error, out_valid,
                                out_valid ? {out_id, code, out_bad} : 53'd0});
      cycles += 1;
      clk = 1'b0;
      read_status = $fscanf(stream, "%h %h\n", record, unused_expected);
    end
    if (!$feof(stream)) $fatal(1, "malformed vectors after cycle %0d", cycles);
    $fclose(stream);
    $fclose(observed);
    $display("RECON_COMPLETE cycles=%0d midpoint=%0d", cycles, midpoint_count);
  endtask

  // 三个路径都由 +plusargs 注入，任一缺失即停：没有向量或观察路径就无法完成对拍。
  initial begin
    string vectors, midpoint, observation;
    clk = 1'b0;
    v = '0;
    if (!$value$plusargs("midpoint=%s", midpoint)) $fatal(1, "missing +midpoint");
    if (!$value$plusargs("vectors=%s", vectors)) $fatal(1, "missing +vectors");
    if (!$value$plusargs("observation=%s", observation)) $fatal(1, "missing +observation");
    check_vectors(vectors, midpoint, observation);
    $finish;
  end
endmodule
