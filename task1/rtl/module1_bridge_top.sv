// =============================================================================
// 文件名     : module1_bridge_top.sv
// 模块名     : module1_bridge_top
// 版本       : v1.0
// 功能       : 模块一 Fig.21 桥接候选顶层，复用 module1_top 的时序与端口表
//
// 设计说明   :
//   * bridge 的 request/publish 二级冻结把配置对齐到 hold 晋升沿。
//   * conv_v_phase 给保留列、conv_h_phase[2:0] 给纵向起点；bridge_mask_valid 同时门控掩码与补偿。
//   * 错 ID、复位与非法相位一律回落 Fig.11，避免候选路径产生非法掩码。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module module1_bridge_top (
    // 复位、清空、禁用三者同沿生效且优先于种子与采集请求；端口与 module1_top 一一对应。
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic         clear_i,
    input  logic         enable_i,
    // advance_i 与 eligible_i 在 N 沿判定，N+1 由内部 module1_top 发布接受／stall 事件。
    input  logic         advance_i,
    // eligible_i 的 bit 序即物理片序，18 位对应 18 片；由上游保证已释放并可重新采样。
    input  logic [ 17:0] eligible_i,
    input  logic         shuffle_i,
    // dem_enable_i 随采集请求冻结；bridge_enable_i 同样随请求冻结，但走本层独立的二级流水。
    input  logic         dem_enable_i,
    input  logic         bridge_enable_i,
    // 两个种子须同沿非零且不与分配冲突，违例由 module1_top 报 seed_error_o，本层不重复判定。
    input  logic         seed_load_i,
    input  logic [ 31:0] slice_seed_i,
    input  logic [ 31:0] dem_seed_i,
    // 已决前缀带同笔 ID 进入；错 ID 或非法相位由 module1_top 置 code_error_o，并连带使本层补偿清零。
    input  logic         code_valid_i,
    input  logic [  8:0] code_i,
    input  logic [ 31:0] code_sample_id_i,
    // 采集／转换 ID、位图、valid 与样本号直接透传 module1_top，不在本层寄存，避免两处快照不同步。
    output logic [ 39:0] acq_ids_o,
    output logic [ 39:0] conv_ids_o,
    output logic [ 17:0] acq_mask_o,
    output logic [ 17:0] conv_mask_o,
    output logic         acq_valid_o,
    output logic         conv_valid_o,
    output logic [ 31:0] acq_sample_id_o,
    output logic [ 31:0] conv_sample_id_o,
    // slot_accepted_o 是 N+1 的单拍事件；本层的桥接保持位也在这一沿随组晋升。
    output logic         slot_accepted_o,
    output logic         stall_o,
    output logic         seed_error_o,
    // 两个随机状态端口只作观测，本层不读取、不推进，相位仍由 module1_top 独立产生。
    output logic [ 31:0] slice_lfsr_state_o,
    output logic [ 31:0] dem_rng_state_o,
    // DEM 使能透传供上层观察冻结配置；桥接另加 acq/conv_bridge_enable_o 两位，不并入 top_result_t。
    output logic         acq_dem_enable_o,
    output logic         conv_dem_enable_o,
    output logic         acq_bridge_enable_o,
    output logic         conv_bridge_enable_o,
    // h/v 相位按组冻结后透传；桥接例化只用 conv_* 一组，采集组相位留给上层。
    output logic [  3:0] acq_h_phase_o,
    output logic [  3:0] acq_v_phase_o,
    output logic [  3:0] conv_h_phase_o,
    output logic [  3:0] conv_v_phase_o,
    // 主／子掩码在桥接命中时换成 fig21_bridge 的结果，否则仍是 module1_top 的 Fig.11 掩码。
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    // Q32 正补偿仅在按 Fig.11 名义码本重构时加一次；按迁移后的实际位权重构时使用零补偿。掩码无效时所有掩码及补偿清零。
    output logic [ 31:0] correction_q32_o,
    output logic         mask_valid_o,
    output logic         code_error_o
);
  // 桥接使能单独走 request/publish 两级，与采集组晋升对齐；hold 位保存上一笔已冻结的桥接选择。
  logic bridge_request_q, bridge_publish_q;
  logic acq_bridge_hold_q, conv_bridge_hold_q;
  logic bridge_mask_valid;
  // base_* 是 Fig.11 结果，bridge_* 是 Fig.21 结果，两者并行算出后由 bridge_mask_valid 统一选择。
  logic [135:0] base_main_mask, bridge_main_mask;
  logic [39:0] base_sub_mask, bridge_sub_mask;
  logic [31:0] bridge_correction;

  // 复用 module1_top 的全部时序，本层不碰时钟、复位和调度；mask_valid_o/code_error_o 原样带出。
  // module1_top 没有 bridge 端口，桥接选择只能在组合输出端旁路，不能改它的例化端口表。
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

  // 单时钟域：复位分支把请求、发布与两个 hold 一起清，否则禁用后第一笔会沿用过期的桥接选择。
// =========================================================================
// 2. 时序逻辑与逐沿行为
// =========================================================================
  always_ff @(posedge clk_i) begin
    if (!rst_ni || clear_i || !enable_i) begin
      bridge_request_q <= 1'b0;
      bridge_publish_q <= 1'b0;
      acq_bridge_hold_q <= 1'b0;
      conv_bridge_hold_q <= 1'b0;
    end else begin
      // request/publish 相隔一拍，publish 与 N+1 的 slot_accepted_o 同沿到达，冻结的正是请求当时那一笔的 bridge_enable。
      // N 保存请求位。N+1 的接受事件和该请求位同时发布。
      bridge_request_q <= bridge_enable_i;
      bridge_publish_q <= bridge_request_q;
      // 连续接受时逐笔把旧采集 hold 晋升为转换 hold；空拍或 stall 时保持，不因 bridge_enable_i 变化而改写。
      if (slot_accepted_o) begin
        conv_bridge_hold_q <= acq_bridge_hold_q;
        acq_bridge_hold_q <= bridge_publish_q;
      end
    end
  end

  // 发布沿使用新快照，下一沿写入保持位。连续接受仍逐笔晋升旧采集快照。
  // 接受沿用刚发布的 bridge_publish_q 作为新采集组的选择，其余沿显示已冻结的 hold 值。
  assign acq_bridge_enable_o = slot_accepted_o ? bridge_publish_q : acq_bridge_hold_q;
  assign conv_bridge_enable_o = slot_accepted_o ? acq_bridge_hold_q : conv_bridge_hold_q;

  // 保留列取本笔 v 相位（4 位直接当 0～15 列号），纵向起点取 h 相位低三位（0～7）；高一位不参与。
  // 相位来自 conv_*，即已晋升的转换组，保证同笔全部已决前缀看到同一组列与起点。
  fig21_bridge u_fig21_bridge (
      .code_i(code_i),
      .reserved_column_i(conv_v_phase_o),
      .vertical_slot_i(conv_h_phase_o[2:0]),
      .main_mask_o(bridge_main_mask),
      .sub_mask_o(bridge_sub_mask),
      .correction_q32_o(bridge_correction)
  );

  // 只有 mask_valid_o 与转换组桥接冻结同时成立才切换输出；错 ID、非法相位或复位时仍走 Fig.11 分支。
  assign bridge_mask_valid = mask_valid_o && conv_bridge_enable_o;
  // 两路掩码共用一个选择信号，避免主掩码走桥接、子掩码却留在 Fig.11 的错配组合。
  assign main_mask_o = bridge_mask_valid ? bridge_main_mask : base_main_mask;
  assign sub_mask_o = bridge_mask_valid ? bridge_sub_mask : base_sub_mask;
  // 补偿与桥接掩码同门控：非桥接前缀不补，复位或无效码也不补，防止重复加或漏加。
  assign correction_q32_o = bridge_mask_valid ? bridge_correction : '0;
endmodule
