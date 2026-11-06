// 六位 dither 字发生器，PRBS31 延迟多项式为 1+x^28+x^31。
// 同步低有效复位和 clear_i 恢复默认种子。合法种子载入占用独立时钟沿，
// 零种子或与 sample_accept_i 同沿的载入会置 seed_error_o，当沿不发字。
// 数字采样事务受理后发布六位字和样本 ID，先生成的位为 D6。模拟注入
// 需在后续采样孔径前留出建立时间，下游按样本 ID 保存字至同笔转换结束。
// 关闭 dither 仍发布同笔零字，随机状态保持。没有接受时保持字和 ID，
// word_valid_o 与 seed_error_o 每沿重新判定。采样与读取共用一个时钟域。
`timescale 1ns/1ps
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

  assign next_rng_state = advance_six(rng_state_o);

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
