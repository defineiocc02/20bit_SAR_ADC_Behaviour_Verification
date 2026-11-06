// SAR20 数字顶层，含片上冻结系数重构。ADC2 交接释放当前转换组，
// 独立两项队列保存未完成重构的真实斩波与 AZ 确认，按 ID 送 CAAZ。
`timescale 1ns/1ps
module sar20_digital_lms_top (
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
    input  logic                cfg_start_i,
    output logic                cfg_start_ready_o,
    input  logic                cfg_valid_i,
    output logic                cfg_ready_o,
    input  logic         [25:0] cfg_data_i,
    output logic                configured_o,
    output logic                cfg_error_o,
    input  logic                adc2_valid_i,
    output logic                adc2_ready_o,
    input  logic         [31:0] adc2_sample_id_i,
    input  logic         [11:0] adc2_code_i,
    input  logic                adc2_bad_i,
    input  logic                adc2_chop_neg_i,
    input  logic         [30:0] adc2_az_pair_id_i,
    input  logic                adc2_az_ok_i,
    output logic                adc2_error_o,
    output logic          [1:0] recon_inflight_o
);
  logic active, allow_dispatch, control_advance_ready, epoch_locked_q;
  logic engine_cfg_start_ready, engine_cfg_error, blocked_cfg_q;
  logic transfer_ready, input_ready, take_adc2, take_recon;
  logic engine_out_valid, engine_out_ready, engine_out_bad;
  logic [31:0] engine_out_id;
  logic signed [19:0] engine_out_code;
  logic control_recon_ready, tag_match, result_binding_error;
  logic [64:0] tag_q [2];
  logic tag_read_q, tag_write_q;
  logic [31:0] head_id;
  logic head_chop_neg, head_az_ok;
  logic [30:0] head_az_id;

  assign active = rst_ni && !clear_i && enable_i;
  assign allow_dispatch = active && configured_o && !cfg_start_i;
  assign advance_ready_o = allow_dispatch && control_advance_ready;
  assign cfg_start_ready_o = active && !epoch_locked_q && engine_cfg_start_ready;
  assign cfg_error_o = active && (engine_cfg_error || blocked_cfg_q);

  assign adc2_ready_o = transfer_ready && input_ready && (recon_inflight_o != 2'd2);
  assign take_adc2 = adc2_valid_i && adc2_ready_o;
  assign {head_id, head_chop_neg, head_az_id, head_az_ok} = tag_q[tag_read_q];
  assign tag_match = (recon_inflight_o != 0) && (head_id == engine_out_id);
  assign engine_out_ready = control_recon_ready && tag_match;
  assign take_recon = engine_out_valid && engine_out_ready;
  assign result_binding_error = active && engine_out_valid && !tag_match;

  sar20_digital_control #(.SplitRetirement(1'b1)) u_control (
      .advance_i(advance_i && allow_dispatch),
      .advance_ready_o(control_advance_ready),
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
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
      .transfer_valid_i(adc2_valid_i && input_ready && (recon_inflight_o != 2'd2)),
      .transfer_sample_id_i(adc2_sample_id_i), .transfer_bad_i(adc2_bad_i),
      .transfer_ready_o(transfer_ready), .result_binding_error_i(result_binding_error),
      .recon_valid_i(engine_out_valid && tag_match), .recon_ready_o(control_recon_ready),
      .recon_sample_id_i(engine_out_id), .recon_code_i(engine_out_code),
      .recon_bad_i(engine_out_bad), .recon_chop_neg_i(head_chop_neg),
      .recon_az_pair_id_i(head_az_id), .recon_az_ok_i(head_az_ok), .recon_error_o()
  );

  sar20_lms_recon u_recon (
      .clk_i(clk_i), .rst_ni(rst_ni), .clear_i(clear_i || !enable_i || fault_o),
      .cfg_start_i(cfg_start_i && cfg_start_ready_o),
      .cfg_start_ready_o(engine_cfg_start_ready), .cfg_valid_i(cfg_valid_i),
      .cfg_ready_o(cfg_ready_o), .cfg_data_i(cfg_data_i), .configured_o(configured_o),
      .cfg_error_o(engine_cfg_error),
      .in_valid_i(adc2_valid_i && transfer_ready && (recon_inflight_o != 2'd2)),
      .in_ready_o(input_ready), .in_sample_id_i(adc2_sample_id_i), .in_bad_i(adc2_bad_i),
      .in_slice_ids_i(conv_slice_ids_o), .in_main_i(main_mask_o), .in_conv_sub_i(sub_mask_o),
      .in_sample_sub_i(conv_sample_sub_high_o), .in_adc2_i(adc2_code_i),
      .out_valid_o(engine_out_valid), .out_ready_i(engine_out_ready),
      .out_sample_id_o(engine_out_id), .out_code_o(engine_out_code), .out_bad_o(engine_out_bad)
  );

  always_ff @(posedge clk_i) begin
    if (!active) begin
      epoch_locked_q <= 1'b0;
      blocked_cfg_q <= 1'b0;
      adc2_error_o <= 1'b0;
    end else begin
      if (advance_i && advance_ready_o) epoch_locked_q <= 1'b1;
      blocked_cfg_q <= cfg_start_i && epoch_locked_q;
      adc2_error_o <= configured_o && !fault_o && adc2_valid_i && !transfer_ready;
    end
    if (!active || fault_o) begin
      recon_inflight_o <= '0;
      tag_read_q <= 1'b0;
      tag_write_q <= 1'b0;
    end else begin
      // 最多两笔已交接、未重构的原始码。只有一次合法交接才写入同笔模拟确认。
      if (take_adc2) begin
        tag_q[tag_write_q] <= {adc2_sample_id_i, adc2_chop_neg_i,
                               adc2_az_pair_id_i, adc2_az_ok_i};
        tag_write_q <= !tag_write_q;
      end
      if (take_recon) tag_read_q <= !tag_read_q;
      case ({take_adc2, take_recon})
        2'b10: recon_inflight_o <= recon_inflight_o + 2'd1;
        2'b01: recon_inflight_o <= recon_inflight_o - 2'd1;
        default: ;
      endcase
    end
  end
endmodule
