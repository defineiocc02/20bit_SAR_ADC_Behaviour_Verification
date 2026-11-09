// =============================================================================
// 文件名     : dither_word_gen_tb.sv
// 模块名     : dither_word_gen_tb
// 版本       : v1.0
// 功能       : 扰动字发生器的逐沿对拍测试台
//
// 设计说明   :
//   * 用独立的串行反馈实现做 oracle，避免复用向量化发生器造成自我验证。
//   * 覆盖全字空间、种子重载、丢弃在途事务与连续接受。
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
module dither_word_gen_tb;
  typedef struct packed {
    logic [5:0] word;
    logic word_valid;
    logic [31:0] word_sample_id;
    logic [30:0] rng_state;
    logic seed_error;
  } word_result_t;

  typedef struct packed {
    logic rst_n;
    logic clear;
    logic dither_enable;
    logic seed_load;
    logic [30:0] seed;
    logic sample_accept;
    logic [31:0] sample_id;
    word_result_t expected;
  } vector_t;

  logic clk;
  vector_t vector;
  word_result_t actual;

  dither_word_gen dut (
      .clk_i(clk),
      .rst_ni(vector.rst_n),
      .clear_i(vector.clear),
      .dither_enable_i(vector.dither_enable),
      .seed_load_i(vector.seed_load),
      .seed_i(vector.seed),
      .sample_accept_i(vector.sample_accept),
      .sample_id_i(vector.sample_id),
      .word_o(actual.word),
      .word_valid_o(actual.word_valid),
      .word_sample_id_o(actual.word_sample_id),
      .rng_state_o(actual.rng_state),
      .seed_error_o(actual.seed_error)
  );

  task automatic check_vectors(input string vector_path);
    vector_t record;
    int stream;
    int read_status;
    int cycles;
    int words;
    int seed_errors;

    cycles = 0;
    words = 0;
    seed_errors = 0;
    stream = $fopen(vector_path, "r");
    if (stream == 0) $fatal(1, "cannot open %s", vector_path);

    read_status = $fscanf(stream, "%h\n", record);
    while (read_status == 1) begin
      vector = record;
      #1;
      clk = 1'b1;
      #1;
      if (actual !== vector.expected) begin
        $fatal(1, "cycle %0d sample %0d expected %h got %h",
               cycles, vector.sample_id, vector.expected, actual);
      end
      cycles += 1;
      if (actual.word_valid) words += 1;
      if (actual.seed_error) seed_errors += 1;
      clk = 1'b0;
      read_status = $fscanf(stream, "%h\n", record);
    end
    if (!$feof(stream)) $fatal(1, "malformed vector after cycle %0d", cycles);
    $fclose(stream);
    $display("TASK2_DITHER_WORD_COMPLETE cycles=%0d words=%0d seed_errors=%0d",
             cycles, words, seed_errors);
  endtask

  initial begin
    string vector_path;

    clk = 1'b0;
    vector = '0;
    if (!$value$plusargs("vectors=%s", vector_path)) $fatal(1, "missing +vectors");
    check_vectors(vector_path);
    $finish;
  end
endmodule
