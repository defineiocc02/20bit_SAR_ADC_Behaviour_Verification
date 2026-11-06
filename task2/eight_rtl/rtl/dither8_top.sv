// 八片采样扰动控制。采集描述符在模块一发布后一个上升沿有效，
// 提供 SADC 低路标签和 RDAC 子阵列采样掩码。后级按转换样本号
// 取回同笔扰动快照。掩码驱动是数字合同，模拟宏另行确认参考极性。
`timescale 1ns/1ps
module dither8_top (
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic         clear_i,
    input  logic         enable_i,
    input  logic         advance_i,
    input  logic [ 17:0] eligible_i,
    input  logic         shuffle_i,
    input  logic         dem_enable_i,
    input  logic         bridge_enable_i,
    input  logic         dither_enable_i,
    input  logic         seed_load_i,
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    input  logic [ 30:0] dither_seed_i,
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,
    input  logic [ 31:0] code_sample_id_i,
    output logic         slot_accepted_o,
    output logic         stall_o,
    output logic         seed_error_o,
    output logic         acq_dither_valid_o,
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 39:0] acq_slice_ids_o,
    output logic [  5:0] acq_word_o,
    output logic [  4:0] acq_lower_eighths_o,
    output logic [ 39:0] acq_sample_sub_high_o,
    output logic         conv_ready_o,
    output logic [ 31:0] conv_sample_id_o,
    output logic [ 39:0] conv_slice_ids_o,
    output logic [  5:0] conv_word_o,
    output logic [  5:0] conv_total_eighths_o,
    output logic [ 39:0] conv_sample_sub_high_o,
    output logic [  8:0] command_code_o,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic [ 31:0] bridge_correction_q32_o,
    output logic         mask_valid_o,
    output logic         code_error_o
);
  logic active, seed_accepted;
  logic dither_request_q, dither_publish_q;
  logic seed_request_q, seed_publish_q, seed_rejected_q;
  logic [30:0] dither_seed_request_q, dither_seed_publish_q;
  logic [39:0] scheduled_acq_ids;
  logic [31:0] scheduled_acq_sample_id;
  logic conv_group_valid, latest_word_pulse;
  logic latest_word_valid_q, previous_word_valid_q;
  logic [31:0] previous_sample_id_q;
  logic [5:0] previous_word_q;
  logic latest_match, previous_match;
  logic [5:0] selected_word;
  logic [2:0] upper_code;
  logic [9:0] adjusted_code;
  logic command_eligible;

  // 八片依有序 slot 分档。四个二进制档位位与物理电容的打包顺序相反。
  // 档 8 只把 8C 接高参考，末尾 dummy 始终接低参考。
  function automatic logic [39:0] sample_sub_high(input logic [5:0] word);
    logic [3:0] level;
    for (int slot = 0; slot < 8; slot++) begin
      level = {1'b0, word[5:3]} + {3'b0, (3'(slot) < word[2:0])};
      sample_sub_high[5*slot+:5] = {1'b0, level[0], level[1], level[2], level[3]};
    end
  endfunction

  assign active = rst_ni && !clear_i && enable_i;
  assign seed_accepted = seed_load_i && !advance_i && (slice_seed_i != '0)
                         && (dem_seed_i != '0) && (dither_seed_i != '0);

  module1_bridge_top u_module1 (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
      .advance_i(advance_i && !seed_load_i),
      .eligible_i(eligible_i),
      .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i),
      .bridge_enable_i(bridge_enable_i),
      .seed_load_i(seed_accepted),
      .slice_seed_i(slice_seed_i),
      .dem_seed_i(dem_seed_i),
      .code_valid_i(command_eligible),
      .code_i(adjusted_code[8:0]),
      .code_sample_id_i(code_sample_id_i),
      .acq_ids_o(scheduled_acq_ids),
      .conv_ids_o(conv_slice_ids_o),
      .acq_mask_o(),
      .conv_mask_o(),
      .acq_valid_o(),
      .conv_valid_o(conv_group_valid),
      .acq_sample_id_o(scheduled_acq_sample_id),
      .conv_sample_id_o(conv_sample_id_o),
      .slot_accepted_o(slot_accepted_o),
      .stall_o(stall_o),
      .seed_error_o(),
      .slice_lfsr_state_o(),
      .dem_rng_state_o(),
      .acq_dem_enable_o(),
      .conv_dem_enable_o(),
      .acq_bridge_enable_o(),
      .conv_bridge_enable_o(),
      .acq_h_phase_o(),
      .acq_v_phase_o(),
      .conv_h_phase_o(),
      .conv_v_phase_o(),
      .main_mask_o(main_mask_o),
      .sub_mask_o(sub_mask_o),
      .correction_q32_o(bridge_correction_q32_o),
      .mask_valid_o(mask_valid_o),
      .code_error_o()
  );

  dither_word_gen u_word (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i || !enable_i),
      .dither_enable_i(dither_publish_q),
      .seed_load_i(seed_publish_q),
      .seed_i(dither_seed_publish_q),
      .sample_accept_i(slot_accepted_o),
      .sample_id_i(scheduled_acq_sample_id),
      .word_o(acq_word_o),
      .word_valid_o(latest_word_pulse),
      .word_sample_id_o(acq_sample_id_o),
      .rng_state_o(),
      .seed_error_o()
  );

  always_ff @(posedge clk_i) begin
    if (!active) begin
      dither_request_q <= 1'b0;
      dither_publish_q <= 1'b0;
      seed_request_q <= 1'b0;
      seed_publish_q <= 1'b0;
      seed_rejected_q <= 1'b0;
      seed_error_o <= 1'b0;
      dither_seed_request_q <= '0;
      dither_seed_publish_q <= '0;
      latest_word_valid_q <= 1'b0;
      previous_word_valid_q <= 1'b0;
      previous_sample_id_q <= '0;
      previous_word_q <= '0;
      acq_slice_ids_o <= '0;
    end else begin
      // 请求与发布相隔一拍，种子和扰动使能沿同一流水传递。
      dither_request_q <= dither_enable_i;
      dither_publish_q <= dither_request_q;
      seed_request_q <= seed_accepted;
      seed_publish_q <= seed_request_q;
      seed_rejected_q <= seed_load_i && !seed_accepted;
      seed_error_o <= seed_rejected_q;
      if (seed_accepted) dither_seed_request_q <= dither_seed_i;
      if (seed_request_q) dither_seed_publish_q <= dither_seed_request_q;

      // 只有新描述符发布才移位。空拍不能用最新字覆盖在转换的上一笔。
      if (slot_accepted_o) begin
        previous_word_valid_q <= latest_word_valid_q;
        previous_sample_id_q <= acq_sample_id_o;
        previous_word_q <= acq_word_o;
        latest_word_valid_q <= 1'b1;
        acq_slice_ids_o <= scheduled_acq_ids;
      end
    end
  end

  assign acq_dither_valid_o = active && latest_word_pulse;
  assign acq_lower_eighths_o = {acq_word_o[4], 1'b0, acq_word_o[2:0]};
  assign acq_sample_sub_high_o = sample_sub_high(acq_word_o);

  assign latest_match = latest_word_valid_q && (acq_sample_id_o == conv_sample_id_o);
  assign previous_match = previous_word_valid_q && (previous_sample_id_q == conv_sample_id_o);
  assign conv_ready_o = active && conv_group_valid && (latest_match || previous_match);
  // 发布沿将旧采集组晋升为转换组，对应扰动字此时在最新槽。
  // 下一沿描述符移位后，该字位于上一槽，直到下一次晋升。
  assign selected_word = slot_accepted_o ? acq_word_o : previous_word_q;
  assign conv_word_o = conv_ready_o ? selected_word : 6'd0;
  assign conv_total_eighths_o = conv_word_o;
  assign conv_sample_sub_high_o = sample_sub_high(conv_word_o);

  // 加法和样本号校验并行。原生子阵列采用正注入，进位用于拒绝越界。
  assign upper_code = {selected_word[5], 1'b0, selected_word[3]};
  assign adjusted_code = {1'b0, code_i} + {7'b0, upper_code};
  assign command_eligible = code_valid_i && conv_ready_o && !adjusted_code[9];
  assign command_code_o = mask_valid_o ? adjusted_code[8:0] : 9'd0;
  assign code_error_o = active && code_valid_i && !mask_valid_o;
endmodule
