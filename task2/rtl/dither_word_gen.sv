// =============================================================================
// 文件名     : dither_word_gen.sv
// 模块名     : dither_word_gen
// 版本       : v1.0
// 功能       : PRBS31 六位扰动字发生器
//
// 设计说明   :
//   * 连续六次反馈只读取旧状态中尚未移出的位，保证一次沿推进得到的字与串行参考一致。
//   * dither 关闭时字保持为零且 PRBS31 状态冻结；复位回到默认非零种子。
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
module dither_word_gen (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        clear_i,
    input  logic        dither_enable_i,
    input  logic        seed_load_i,
    input  logic [30:0] seed_i,
    input  logic        sample_accept_i,
    input  logic [31:0] sample_id_i,
    output logic [ 5:0] word_o,
    output logic        word_valid_o,
    output logic [31:0] word_sample_id_o,
    output logic [30:0] rng_state_o,
    output logic        seed_error_o
);
  localparam logic [30:0] DefaultSeed = 31'h5a17_09c3;
  logic [30:0] next_rng_state;

  function automatic logic [30:0] advance_six(input logic [30:0] state);
    logic [30:0] shifted;
    shifted = state;
    for (int bit_index = 0; bit_index < 6; bit_index++) begin
      shifted = {shifted[29:0], shifted[30] ^ shifted[27]};
    end
    advance_six = shifted;
  endfunction

// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
// 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
  assign next_rng_state = advance_six(rng_state_o);

// =========================================================================
// 3. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
  always_ff @(posedge clk_i) begin
    if (!rst_ni || clear_i) begin
      rng_state_o      <= DefaultSeed;
      word_o           <= '0;
      word_sample_id_o <= '0;
      word_valid_o     <= 1'b0;
      seed_error_o     <= 1'b0;
    end else begin
      word_valid_o <= 1'b0;
      seed_error_o <= 1'b0;
      if (seed_load_i) begin
        if (sample_accept_i || seed_i == 31'd0) begin
          seed_error_o <= 1'b1;
        end else begin
          rng_state_o <= seed_i;
        end
      end else if (sample_accept_i) begin
        word_valid_o     <= 1'b1;
        word_sample_id_o <= sample_id_i;
        word_o           <= dither_enable_i ? next_rng_state[5:0] : 6'd0;
        if (dither_enable_i) begin
          rng_state_o <= next_rng_state;
        end
      end
    end
  end
endmodule
