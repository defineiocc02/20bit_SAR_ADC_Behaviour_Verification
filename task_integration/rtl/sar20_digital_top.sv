// SAR20 数字顶层。调度、扰动和斩波控制按样本 ID 绑定，重构由外部路径提供。
// 全部接口同域。合法粗码输出一拍最终开关命令，转换组在重构退休前禁止覆盖。
`timescale 1ns/1ps
module sar20_digital_top (
    input  logic                clk_i,
    input  logic                rst_ni,
    input  logic                clear_i,
    input  logic                enable_i,
    input  logic                advance_i,
    output logic                advance_ready_o,
    input  logic         [17:0] eligible_i,
    input  logic                shuffle_i,
    input  logic                dem_enable_i,
    input  logic                bridge_enable_i,
    input  logic                dither_enable_i,
    input  logic                caaz_enable_i,
    input  logic                seed_load_i,
    input  logic         [31:0] slice_seed_i,
    input  logic         [31:0] dem_seed_i,
    input  logic         [30:0] dither_seed_i,
    output logic                seed_error_o,
    output logic                slot_accepted_o,
    output logic                stall_o,
    output logic                acq_valid_o,
    output logic         [31:0] acq_sample_id_o,
    output logic         [39:0] acq_slice_ids_o,
    output logic          [5:0] acq_word_o,
    output logic          [4:0] acq_lower_eighths_o,
    output logic         [39:0] acq_sample_sub_high_o,
    output logic                acq_chop_neg_o,
    output logic                acq_az_capture_o,
    output logic                acq_az_hold_o,
    output logic         [30:0] acq_az_pair_id_o,
    output logic                conv_ready_o,
    output logic         [31:0] conv_sample_id_o,
    output logic         [39:0] conv_slice_ids_o,
    output logic          [5:0] conv_word_o,
    output logic          [5:0] conv_total_eighths_o,
    output logic         [39:0] conv_sample_sub_high_o,
    input  logic                code_valid_i,
    input  logic          [8:0] code_i,
    input  logic         [31:0] code_sample_id_i,
    output logic                mask_valid_o,
    output logic                code_error_o,
    output logic          [8:0] command_code_o,
    output logic        [135:0] main_mask_o,
    output logic         [39:0] sub_mask_o,
    output logic         [31:0] bridge_correction_q32_o,
    input  logic                recon_valid_i,
    output logic                recon_ready_o,
    input  logic         [31:0] recon_sample_id_i,
    input  logic signed  [19:0] recon_code_i,
    input  logic                recon_bad_i,
    input  logic                recon_chop_neg_i,
    input  logic         [30:0] recon_az_pair_id_i,
    input  logic                recon_az_ok_i,
    output logic                recon_error_o,
    output logic                out_valid_o,
    output logic signed  [19:0] out_code_o,
    output logic         [31:0] out_sample_id_o,
    output logic         [31:0] out_first_sample_id_o,
    output logic         [30:0] out_az_pair_id_o,
    output logic                out_pair_o,
    output logic signed  [20:0] out_difference_o,
    output logic                fault_o,
    output logic                fault_pulse_o,
    output logic          [2:0] caaz_fault_code_o,
    output logic                alignment_fault_o
);
  sar20_digital_control u_control (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
      .advance_i(advance_i),
      .advance_ready_o(advance_ready_o),
      .eligible_i(eligible_i),
      .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i),
      .bridge_enable_i(bridge_enable_i),
      .dither_enable_i(dither_enable_i),
      .caaz_enable_i(caaz_enable_i),
      .seed_load_i(seed_load_i),
      .slice_seed_i(slice_seed_i),
      .dem_seed_i(dem_seed_i),
      .dither_seed_i(dither_seed_i),
      .seed_error_o(seed_error_o),
      .slot_accepted_o(slot_accepted_o),
      .stall_o(stall_o),
      .acq_valid_o(acq_valid_o),
      .acq_sample_id_o(acq_sample_id_o),
      .acq_slice_ids_o(acq_slice_ids_o),
      .acq_word_o(acq_word_o),
      .acq_lower_eighths_o(acq_lower_eighths_o),
      .acq_sample_sub_high_o(acq_sample_sub_high_o),
      .acq_chop_neg_o(acq_chop_neg_o),
      .acq_az_capture_o(acq_az_capture_o),
      .acq_az_hold_o(acq_az_hold_o),
      .acq_az_pair_id_o(acq_az_pair_id_o),
      .conv_ready_o(conv_ready_o),
      .conv_sample_id_o(conv_sample_id_o),
      .conv_slice_ids_o(conv_slice_ids_o),
      .conv_word_o(conv_word_o),
      .conv_total_eighths_o(conv_total_eighths_o),
      .conv_sample_sub_high_o(conv_sample_sub_high_o),
      .code_valid_i(code_valid_i),
      .code_i(code_i),
      .code_sample_id_i(code_sample_id_i),
      .mask_valid_o(mask_valid_o),
      .code_error_o(code_error_o),
      .command_code_o(command_code_o),
      .main_mask_o(main_mask_o),
      .sub_mask_o(sub_mask_o),
      .bridge_correction_q32_o(bridge_correction_q32_o),
      .recon_valid_i(recon_valid_i),
      .recon_ready_o(recon_ready_o),
      .recon_sample_id_i(recon_sample_id_i),
      .recon_code_i(recon_code_i),
      .recon_bad_i(recon_bad_i),
      .recon_chop_neg_i(recon_chop_neg_i),
      .recon_az_pair_id_i(recon_az_pair_id_i),
      .recon_az_ok_i(recon_az_ok_i),
      .recon_error_o(recon_error_o),
      .out_valid_o(out_valid_o),
      .out_code_o(out_code_o),
      .out_sample_id_o(out_sample_id_o),
      .out_first_sample_id_o(out_first_sample_id_o),
      .out_az_pair_id_o(out_az_pair_id_o),
      .out_pair_o(out_pair_o),
      .out_difference_o(out_difference_o),
      .fault_o(fault_o),
      .fault_pulse_o(fault_pulse_o),
      .caaz_fault_code_o(caaz_fault_code_o),
      .alignment_fault_o(alignment_fault_o),
      .transfer_valid_i(1'b0),
      .transfer_sample_id_i(32'd0),
      .transfer_bad_i(1'b0),
      .transfer_ready_o(),
      .result_binding_error_i(1'b0)
  );
endmodule

module sar20_digital_control #(
    parameter bit SplitRetirement = 1'b0
) (
    input  logic                clk_i,
    input  logic                rst_ni,
    input  logic                clear_i,
    input  logic                enable_i,
    input  logic                advance_i,
    output logic                advance_ready_o,
    input  logic         [17:0] eligible_i,
    input  logic                shuffle_i,
    input  logic                dem_enable_i,
    input  logic                bridge_enable_i,
    input  logic                dither_enable_i,
    input  logic                caaz_enable_i,
    input  logic                seed_load_i,
    input  logic         [31:0] slice_seed_i,
    input  logic         [31:0] dem_seed_i,
    input  logic         [30:0] dither_seed_i,
    output logic                seed_error_o,
    output logic                slot_accepted_o,
    output logic                stall_o,
    output logic                acq_valid_o,
    output logic         [31:0] acq_sample_id_o,
    output logic         [39:0] acq_slice_ids_o,
    output logic          [5:0] acq_word_o,
    output logic          [4:0] acq_lower_eighths_o,
    output logic         [39:0] acq_sample_sub_high_o,
    output logic                acq_chop_neg_o,
    output logic                acq_az_capture_o,
    output logic                acq_az_hold_o,
    output logic         [30:0] acq_az_pair_id_o,
    output logic                conv_ready_o,
    output logic         [31:0] conv_sample_id_o,
    output logic         [39:0] conv_slice_ids_o,
    output logic          [5:0] conv_word_o,
    output logic          [5:0] conv_total_eighths_o,
    output logic         [39:0] conv_sample_sub_high_o,
    input  logic                code_valid_i,
    input  logic          [8:0] code_i,
    input  logic         [31:0] code_sample_id_i,
    output logic                mask_valid_o,
    output logic                code_error_o,
    output logic          [8:0] command_code_o,
    output logic        [135:0] main_mask_o,
    output logic         [39:0] sub_mask_o,
    output logic         [31:0] bridge_correction_q32_o,
    input  logic                recon_valid_i,
    output logic                recon_ready_o,
    input  logic         [31:0] recon_sample_id_i,
    input  logic signed  [19:0] recon_code_i,
    input  logic                recon_bad_i,
    input  logic                recon_chop_neg_i,
    input  logic         [30:0] recon_az_pair_id_i,
    input  logic                recon_az_ok_i,
    output logic                recon_error_o,
    output logic                out_valid_o,
    output logic signed  [19:0] out_code_o,
    output logic         [31:0] out_sample_id_o,
    output logic         [31:0] out_first_sample_id_o,
    output logic         [30:0] out_az_pair_id_o,
    output logic                out_pair_o,
    output logic signed  [20:0] out_difference_o,
    output logic                fault_o,
    output logic                fault_pulse_o,
    output logic          [2:0] caaz_fault_code_o,
    output logic                alignment_fault_o,
    input  logic                transfer_valid_i,
    input  logic         [31:0] transfer_sample_id_i,
    input  logic                transfer_bad_i,
    output logic                transfer_ready_o,
    input  logic                result_binding_error_i
);
  logic active, run;
  logic dispatch, dispatch_pending_q;
  logic conv_active_q, command_committed_q;
  logic forward_seed, d8_seed_error, busy_seed_error_q;
  logic d8_acq_valid, d8_conv_ready;
  logic code_request, d8_code_error, d8_mask_valid;
  logic [8:0] d8_command_code;
  logic [135:0] d8_main_mask;
  logic [39:0] d8_sub_mask;
  logic [31:0] d8_bridge_correction;
  logic recon_fire, conversion_retired, retired_bad;
  logic caaz_acq_valid, caaz_out_valid, caaz_fault, caaz_fault_pulse;
  logic [31:0] caaz_acq_id;
  logic alignment_error, alignment_fault_pulse_q;
  logic mask_valid_q, bad_pending_q, acquisition_allowed;

  assign active = rst_ni && !clear_i && enable_i;
  assign fault_o = alignment_fault_o || caaz_fault;
  assign run = active && !fault_o;
  assign fault_pulse_o = active && (caaz_fault_pulse || alignment_fault_pulse_q);

  assign advance_ready_o = run && !bad_pending_q && !dispatch_pending_q && !conv_active_q;
  assign dispatch = advance_i && advance_ready_o;
  // 空闲同沿冲突交给模块二原子拒绝。已有转换时不能绕过冲突写种子。
  assign forward_seed = run && !bad_pending_q && seed_load_i && (!advance_i || advance_ready_o);
  assign seed_error_o = active && (d8_seed_error || busy_seed_error_q);

  assign alignment_error = (caaz_acq_valid && (caaz_acq_id != acq_sample_id_o)) ||
                           (SplitRetirement && result_binding_error_i);
  assign acquisition_allowed = run && !bad_pending_q && !retired_bad;
  assign acq_valid_o = run && !bad_pending_q && caaz_acq_valid && !alignment_error;
  assign conv_ready_o = run && !bad_pending_q && conv_active_q && d8_conv_ready;
  // 外接重构随当前转换组退休。片上流水在接收 ADC2 结果时释放转换组，
  // 后续重构由独立所有权队列与 CAAZ 顺序共同验证。
  if (SplitRetirement) begin : gen_split_retirement
    assign transfer_ready_o = conv_ready_o && (transfer_sample_id_i == conv_sample_id_o) &&
                              (command_committed_q || transfer_bad_i);
    assign conversion_retired = transfer_valid_i && transfer_ready_o;
    assign retired_bad = conversion_retired && transfer_bad_i;
    assign recon_ready_o = run;
  end else begin : gen_external_retirement
    assign transfer_ready_o = 1'b0;
    assign conversion_retired = recon_fire;
    assign retired_bad = recon_fire && recon_bad_i;
    assign recon_ready_o = conv_ready_o && (recon_sample_id_i == conv_sample_id_o) &&
                           (command_committed_q || recon_bad_i);
  end
  assign recon_fire = recon_valid_i && recon_ready_o;
  assign code_request = code_valid_i && conv_ready_o && !command_committed_q && !retired_bad;
  assign mask_valid_o = run && mask_valid_q;
  assign out_valid_o = run && caaz_out_valid;

  dither8_top u_dither8 (
      .clk_i(clk_i), .rst_ni(rst_ni), .clear_i(clear_i), .enable_i(enable_i),
      .advance_i(dispatch), .eligible_i(eligible_i), .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i), .bridge_enable_i(bridge_enable_i),
      .dither_enable_i(dither_enable_i), .seed_load_i(forward_seed),
      .slice_seed_i(slice_seed_i), .dem_seed_i(dem_seed_i), .dither_seed_i(dither_seed_i),
      .slot_accepted_o(slot_accepted_o), .stall_o(stall_o), .seed_error_o(d8_seed_error),
      .acq_dither_valid_o(d8_acq_valid), .acq_sample_id_o(acq_sample_id_o),
      .acq_slice_ids_o(acq_slice_ids_o), .acq_word_o(acq_word_o),
      .acq_lower_eighths_o(acq_lower_eighths_o), .acq_sample_sub_high_o(acq_sample_sub_high_o),
      .conv_ready_o(d8_conv_ready), .conv_sample_id_o(conv_sample_id_o),
      .conv_slice_ids_o(conv_slice_ids_o), .conv_word_o(conv_word_o),
      .conv_total_eighths_o(conv_total_eighths_o), .conv_sample_sub_high_o(conv_sample_sub_high_o),
      .code_valid_i(code_request), .code_i(code_i), .code_sample_id_i(code_sample_id_i),
      .command_code_o(d8_command_code), .main_mask_o(d8_main_mask), .sub_mask_o(d8_sub_mask),
      .bridge_correction_q32_o(d8_bridge_correction),
      .mask_valid_o(d8_mask_valid), .code_error_o(d8_code_error)
  );

  caaz20_pair u_caaz (
      .clk_i(clk_i), .rst_ni(rst_ni), .clear_i(clear_i), .enable_i(enable_i),
      .caaz_enable_i(caaz_enable_i), .acq_valid_i(d8_acq_valid && acquisition_allowed),
      .acq_sample_id_i(acq_sample_id_o), .raw_valid_i(recon_fire),
      .raw_sample_id_i(recon_sample_id_i), .raw_code_i(recon_code_i), .raw_bad_i(recon_bad_i),
      .raw_chop_neg_i(recon_chop_neg_i), .raw_az_pair_id_i(recon_az_pair_id_i),
      .raw_az_ok_i(recon_az_ok_i), .acq_ctrl_valid_o(caaz_acq_valid),
      .acq_ctrl_sample_id_o(caaz_acq_id), .acq_chop_neg_o(acq_chop_neg_o),
      .acq_az_capture_o(acq_az_capture_o), .acq_az_hold_o(acq_az_hold_o),
      .acq_az_pair_id_o(acq_az_pair_id_o), .out_valid_o(caaz_out_valid),
      .out_code_o(out_code_o), .out_sample_id_o(out_sample_id_o),
      .out_first_sample_id_o(out_first_sample_id_o), .out_az_pair_id_o(out_az_pair_id_o),
      .out_pair_o(out_pair_o), .out_difference_o(out_difference_o),
      .fault_o(caaz_fault), .fault_pulse_o(caaz_fault_pulse), .fault_code_o(caaz_fault_code_o)
  );

  always_ff @(posedge clk_i) begin
    if (!active) begin
      dispatch_pending_q <= 1'b0;
      conv_active_q <= 1'b0;
      command_committed_q <= 1'b0;
      busy_seed_error_q <= 1'b0;
      alignment_fault_o <= 1'b0;
      alignment_fault_pulse_q <= 1'b0;
      bad_pending_q <= 1'b0;
      mask_valid_q <= 1'b0;
      code_error_o <= 1'b0;
      recon_error_o <= 1'b0;
      command_code_o <= '0;
      main_mask_o <= '0;
      sub_mask_o <= '0;
      bridge_correction_q32_o <= '0;
    end else begin
      busy_seed_error_q <= run && seed_load_i && advance_i && !advance_ready_o;
      alignment_fault_pulse_q <= alignment_error && !alignment_fault_o;
      if (alignment_error) alignment_fault_o <= 1'b1;
      // 显式坏样本已进入有序重构。停止新的模拟采样，同时让旧结果和坏样本排空。
      if (retired_bad) bad_pending_q <= 1'b1;
      code_error_o <= run && (d8_code_error || (code_valid_i && !code_request));
      recon_error_o <= run && recon_valid_i && !recon_ready_o;
      mask_valid_q <= d8_mask_valid;

      if (dispatch) dispatch_pending_q <= 1'b1;
      else if (d8_acq_valid || stall_o || d8_seed_error) dispatch_pending_q <= 1'b0;

      if (slot_accepted_o && d8_conv_ready) begin
        conv_active_q <= 1'b1;
        command_committed_q <= 1'b0;
      end else if (conversion_retired) begin
        conv_active_q <= 1'b0;
        command_committed_q <= 1'b0;
      end else if (d8_mask_valid) begin
        command_committed_q <= 1'b1;
      end
      if (d8_mask_valid) begin
        command_code_o <= d8_command_code;
        main_mask_o <= d8_main_mask;
        sub_mask_o <= d8_sub_mask;
        bridge_correction_q32_o <= d8_bridge_correction;
      end
    end
  end
endmodule
