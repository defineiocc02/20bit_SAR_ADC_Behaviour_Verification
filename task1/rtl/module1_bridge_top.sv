// 模块一的 Fig.21 桥接候选。调度、种子和样本 ID 资格沿用 module1_top。
// bridge_enable_i 随采集请求冻结，并在该采集组晋升时选择跨 B4～B6 的映射。
// 保留列 R 使用本笔 v 相位，纵向起始 slot 使用本笔 h 相位低三位。
// correction_q32_o 是输入归一化正补偿。用 Fig.11 名义码本重构时加一次，
// 按迁移后的实际位权重构时使用零补偿。掩码无效时所有掩码及补偿清零。
`timescale 1ns/1ps
module module1_bridge_top (
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic         clear_i,
    input  logic         enable_i,
    input  logic         advance_i,
    input  logic [ 17:0] eligible_i,
    input  logic         shuffle_i,
    input  logic         dem_enable_i,
    input  logic         bridge_enable_i,
    input  logic         seed_load_i,
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,
    input  logic [ 31:0] code_sample_id_i,
    output logic [ 39:0] acq_ids_o,
    output logic [ 39:0] conv_ids_o,
    output logic [ 17:0] acq_mask_o,
    output logic [ 17:0] conv_mask_o,
    output logic         acq_valid_o,
    output logic         conv_valid_o,
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 31:0] conv_sample_id_o,
    output logic         slot_accepted_o,
    output logic         stall_o,
    output logic         seed_error_o,
    output logic [ 31:0] slice_lfsr_state_o,
    output logic [ 31:0] dem_rng_state_o,
    output logic         acq_dem_enable_o,
    output logic         conv_dem_enable_o,
    output logic         acq_bridge_enable_o,
    output logic         conv_bridge_enable_o,
    output logic [  3:0] acq_h_phase_o,
    output logic [  3:0] acq_v_phase_o,
    output logic [  3:0] conv_h_phase_o,
    output logic [  3:0] conv_v_phase_o,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic [ 31:0] correction_q32_o,
    output logic         mask_valid_o,
    output logic         code_error_o
);
  logic bridge_request_q, bridge_publish_q;
  logic acq_bridge_hold_q, conv_bridge_hold_q;
  logic bridge_mask_valid;
  logic [135:0] base_main_mask, bridge_main_mask;
  logic [39:0] base_sub_mask, bridge_sub_mask;
  logic [31:0] bridge_correction;

  module1_top u_module1 (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .clear_i(clear_i),
      .enable_i(enable_i),
      .advance_i(advance_i),
      .eligible_i(eligible_i),
      .shuffle_i(shuffle_i),
      .dem_enable_i(dem_enable_i),
      .seed_load_i(seed_load_i),
      .slice_seed_i(slice_seed_i),
      .dem_seed_i(dem_seed_i),
      .code_valid_i(code_valid_i),
      .code_i(code_i),
      .code_sample_id_i(code_sample_id_i),
      .acq_ids_o(acq_ids_o),
      .conv_ids_o(conv_ids_o),
      .acq_mask_o(acq_mask_o),
      .conv_mask_o(conv_mask_o),
      .acq_valid_o(acq_valid_o),
      .conv_valid_o(conv_valid_o),
      .acq_sample_id_o(acq_sample_id_o),
      .conv_sample_id_o(conv_sample_id_o),
      .slot_accepted_o(slot_accepted_o),
      .stall_o(stall_o),
      .seed_error_o(seed_error_o),
      .slice_lfsr_state_o(slice_lfsr_state_o),
      .dem_rng_state_o(dem_rng_state_o),
      .acq_dem_enable_o(acq_dem_enable_o),
      .conv_dem_enable_o(conv_dem_enable_o),
      .acq_h_phase_o(acq_h_phase_o),
      .acq_v_phase_o(acq_v_phase_o),
      .conv_h_phase_o(conv_h_phase_o),
      .conv_v_phase_o(conv_v_phase_o),
      .main_mask_o(base_main_mask),
      .sub_mask_o(base_sub_mask),
      .mask_valid_o(mask_valid_o),
      .code_error_o(code_error_o)
  );

  always_ff @(posedge clk_i) begin
    if (!rst_ni || clear_i || !enable_i) begin
      bridge_request_q <= 1'b0;
      bridge_publish_q <= 1'b0;
      acq_bridge_hold_q <= 1'b0;
      conv_bridge_hold_q <= 1'b0;
    end else begin
      // N 保存请求位。N+1 的接受事件和该请求位同时发布。
      bridge_request_q <= bridge_enable_i;
      bridge_publish_q <= bridge_request_q;
      if (slot_accepted_o) begin
        conv_bridge_hold_q <= acq_bridge_hold_q;
        acq_bridge_hold_q <= bridge_publish_q;
      end
    end
  end

  // 发布沿使用新快照，下一沿写入保持位。连续接受仍逐笔晋升旧采集快照。
  assign acq_bridge_enable_o = slot_accepted_o ? bridge_publish_q : acq_bridge_hold_q;
  assign conv_bridge_enable_o = slot_accepted_o ? acq_bridge_hold_q : conv_bridge_hold_q;

  fig21_bridge u_fig21_bridge (
      .code_i(code_i),
      .reserved_column_i(conv_v_phase_o),
      .vertical_slot_i(conv_h_phase_o[2:0]),
      .main_mask_o(bridge_main_mask),
      .sub_mask_o(bridge_sub_mask),
      .correction_q32_o(bridge_correction)
  );

  assign bridge_mask_valid = mask_valid_o && conv_bridge_enable_o;
  assign main_mask_o = bridge_mask_valid ? bridge_main_mask : base_main_mask;
  assign sub_mask_o = bridge_mask_valid ? bridge_sub_mask : base_sub_mask;
  assign correction_q32_o = bridge_mask_valid ? bridge_correction : '0;
endmodule
