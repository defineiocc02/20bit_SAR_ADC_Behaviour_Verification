// =============================================================================
// 文件名     : sar20_digital_lms_top.sv
// 模块名     : sar20_digital_lms_top
// 版本       : v1.0
// 功能       : 集成片上冻结 LMS 系数重构的数字顶层
//
// 设计说明   :
//   * 配置引擎顺序装载 396 个 26 位字，配置完成才允许调度；调度开始后拒绝重新装载。
//   * Q28 定点权重与基四恢复除法在 ADC2 握手后按同笔位图完成去扰与重构。
//   * 允许两笔已交接未退休样本并行，recon_inflight_o[1:0] 指示在途数量。
//   * 单时钟域 clk_i；复位、清空、禁用优先于受理，异常路径不得静默接受。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// ===========================================================================

`timescale 1ns/1ps
// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
// -------------------------------------------------------------------------
// 数据流分两段：sar20_digital_control 负责抽取与命令（SplitRetirement=1，
// 收到 ADC2 码即退休），sar20_lms_recon 负责按同笔位图去扰并做 Q28 重构。
// 两段之间靠一个两深度标签队列绑定 sample_id：ADC2 码进来时把模拟侧确认
// （chop 极性、AZ 配对 ID、AZ 是否有效）一起存进 tag_q，重构结果出来时再取出来
// 交给 CAAZ。标签对不上就报绑定错误，而不是把结果当成这一笔的。
// 端口按四组读：cfg_* 配置装载、adc2_* 外部 ADC2 回传、acq_*/conv_* 控制侧握手、
// out_* 配对输出。位序统一 slot 0 在最低位。
// -------------------------------------------------------------------------
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

// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
// 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
  assign active = rst_ni && !clear_i && enable_i;
  assign allow_dispatch = active && configured_o && !cfg_start_i;
  assign advance_ready_o = allow_dispatch && control_advance_ready;
  // 配置窗口：epoch 未锁定且引擎自身可接收时才给 ready。装载期间 allow_dispatch 为低，
  // 抽取被整体挡住——权重正在被改写时开始一笔转换，会用到半新半旧的系数。
  assign cfg_start_ready_o = active && !epoch_locked_q && engine_cfg_start_ready;
  assign cfg_error_o = active && (engine_cfg_error || blocked_cfg_q);

  // 三处 ready 串联成一条背压链：控制侧退休口（transfer_ready）、重构引擎入口
  // （input_ready）、以及在途深度未满（recon_inflight_o != 2）。三者都成立才收码，
  // 少任意一项都会把码塞进没有容量的位置，表现为静默丢样本而不是报错。
  assign adc2_ready_o = transfer_ready && input_ready && (recon_inflight_o != 2'd2);
  assign take_adc2 = adc2_valid_i && adc2_ready_o;
  assign {head_id, head_chop_neg, head_az_id, head_az_ok} = tag_q[tag_read_q];
  // 绑定检查：队列非空且队首 ID 与引擎输出 ID 相等才算同一笔。ID 不等时不给 ready，
  // 引擎继续保持 out_valid，形成「结果卡住 + result_binding_error 上报」的组合，
  // 这比让结果穿过去更安全——穿过去会把上一笔的模拟确认配到这一笔的数字码上。
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

  // 引擎的清空比控制层多一个故障项：fault_o 一高就把除法与被除数寄存一起清掉，
  // 否则故障期间算到一半的商会在故障消失后被当成合法结果送出去。
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

// =========================================================================
// 3. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
  always_ff @(posedge clk_i) begin
    if (!active) begin
      epoch_locked_q <= 1'b0;
      blocked_cfg_q <= 1'b0;
      adc2_error_o <= 1'b0;
    end else begin
      // epoch_locked_q 一旦置位就不再清零（除复位/清空）：配置窗口只在第一次抽取前开放，
      // 之后重新装载会让在途样本用的权重与后续样本不一致，所以按错误处理而不是忽略。
      if (advance_i && advance_ready_o) epoch_locked_q <= 1'b1;
      blocked_cfg_q <= cfg_start_i && epoch_locked_q;
      // ADC2 码到来但控制侧退休口没收：说明当前没有匹配的转换组，或 Sample ID 不符。
      adc2_error_o <= configured_o && !fault_o && adc2_valid_i && !transfer_ready;
    end
    // 故障与清空同权：fault_o 一高就把在途计数与指针清掉，避免故障恢复后
    // 队列里残留的旧标签被当成本轮样本。代价是故障会丢弃在途结果，这是有意的。
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
      // 深度固定为 2：控制侧只允许一笔已交接未退休的样本，重构引擎再压一笔，
      // 因此「已交接、未重构」最多两笔。写成 case 而不是两个独立 if，
      // 是为了让同拍一进一出（2'b11）落到 default 不变，计数不会被抵消成 0。
      case ({take_adc2, take_recon})
        2'b10: recon_inflight_o <= recon_inflight_o + 2'd1;
        2'b01: recon_inflight_o <= recon_inflight_o - 2'd1;
        default: ;
      endcase
    end
  end
endmodule
