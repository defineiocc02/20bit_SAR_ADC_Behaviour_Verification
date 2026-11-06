// 对真实集成顶层核验两代样本归属、原生子阵列位序和粗码上溢门控。
module dither8_contract (
    input logic clk_i, rst_ni, clear_i, enable_i, advance_i,
    input logic [17:0] eligible_i,
    input logic shuffle_i, dem_enable_i, bridge_enable_i, dither_enable_i, seed_load_i,
    input logic [31:0] slice_seed_i, dem_seed_i,
    input logic [30:0] dither_seed_i,
    input logic code_valid_i,
    input logic [8:0] code_i,
    input logic [31:0] code_sample_id_i
);
  logic accepted, acq_valid, conv_ready, mask_valid, code_error;
  logic [31:0] acq_id, conv_id, correction;
  logic [5:0] acq_word, conv_word, conv_total;
  logic [4:0] acq_lower;
  logic [39:0] acq_sub, conv_sub, converted_sub;
  logic [135:0] converted_main;
  logic [8:0] command;
  logic active;
  logic previous_accept, last_valid, older_valid;
  logic [31:0] last_id, older_id;
  logic [5:0] last_word, older_word;
  logic [9:0] ref_command;
  logic ref_valid;

  dither8_top dut (
      .clk_i(clk_i), .rst_ni(rst_ni), .clear_i(clear_i), .enable_i(enable_i),
      .advance_i(advance_i), .eligible_i(eligible_i), .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i), .bridge_enable_i(bridge_enable_i),
      .dither_enable_i(dither_enable_i), .seed_load_i(seed_load_i),
      .slice_seed_i(slice_seed_i), .dem_seed_i(dem_seed_i), .dither_seed_i(dither_seed_i),
      .code_valid_i(code_valid_i), .code_i(code_i), .code_sample_id_i(code_sample_id_i),
      .slot_accepted_o(accepted), .stall_o(), .seed_error_o(),
      .acq_dither_valid_o(acq_valid), .acq_sample_id_o(acq_id), .acq_slice_ids_o(),
      .acq_word_o(acq_word), .acq_lower_eighths_o(acq_lower),
      .acq_sample_sub_high_o(acq_sub), .conv_ready_o(conv_ready),
      .conv_sample_id_o(conv_id), .conv_slice_ids_o(), .conv_word_o(conv_word),
      .conv_total_eighths_o(conv_total), .conv_sample_sub_high_o(conv_sub),
      .command_code_o(command), .main_mask_o(converted_main), .sub_mask_o(converted_sub),
      .bridge_correction_q32_o(correction), .mask_valid_o(mask_valid), .code_error_o(code_error)
  );

  assign active = rst_ni && !clear_i && enable_i;
  always_ff @(posedge clk_i) previous_accept <= active && accepted;

  // 监视顶层公开描述符，按外部观察时序保存历史。
  always_ff @(posedge clk_i) begin
    if (!active) begin
      last_valid <= 1'b0;
      older_valid <= 1'b0;
      last_id <= '0;
      older_id <= '0;
      last_word <= '0;
      older_word <= '0;
    end else if (acq_valid) begin
      older_valid <= last_valid;
      older_id <= last_id;
      older_word <= last_word;
      last_valid <= 1'b1;
      last_id <= acq_id;
      last_word <= acq_word;
    end
  end

  // 从转换端观察到的上路直接重算粗码。先比较上溢，再比较有效命令。
  assign ref_command = 10'(code_i) + (conv_word[5] ? 10'd4 : 10'd0)
                       + (conv_word[3] ? 10'd1 : 10'd0);
  assign ref_valid = active && code_valid_i && conv_ready && code_sample_id_i == conv_id
                     && ref_command < 10'd512;

  always_comb begin
    assert (acq_valid == (active && previous_accept));
    assert (mask_valid == ref_valid);
    assert (code_error == (active && code_valid_i && !ref_valid));
    if (ref_valid) begin
      assert (command == ref_command[8:0]);
    end else begin
      assert ({command, converted_main, converted_sub, correction} == '0);
    end
    if (acq_valid) begin
      assert (acq_lower == (acq_word[4] ? 5'd16 : 5'd0) + 5'(acq_word[2:0]));
    end
    if (conv_ready) begin
      assert (conv_total == conv_word);
      if (acq_valid) begin
        assert ((conv_id == acq_id && conv_word == acq_word)
                || (last_valid && conv_id == last_id && conv_word == last_word));
      end else begin
        assert ((last_valid && conv_id == last_id && conv_word == last_word)
                || (older_valid && conv_id == older_id && conv_word == older_word));
      end
    end
  end

  // 用物理子电容位权解码，而非复写位翻转表达式。
  for (genvar slot = 0; slot < 8; slot++) begin : gen_slot
    localparam logic [2:0] SlotIndex = 3'(slot);
    wire [4:0] a = acq_sub[5*slot+:5];
    wire [4:0] b = conv_sub[5*slot+:5];
    wire [3:0] a_level = 4'(8*a[0] + 4*a[1] + 2*a[2] + a[3]);
    wire [3:0] b_level = 4'(8*b[0] + 4*b[1] + 2*b[2] + b[3]);
    always_comb begin
      if (acq_valid) begin
        assert (!a[4]);
        assert (a_level == 4'(acq_word[5:3]) + (acq_word[2:0] > SlotIndex ? 4'd1 : 4'd0));
      end
      if (conv_ready) begin
        assert (!b[4]);
        assert (b_level == 4'(conv_word[5:3]) + (conv_word[2:0] > SlotIndex ? 4'd1 : 4'd0));
      end
    end
  end
endmodule
