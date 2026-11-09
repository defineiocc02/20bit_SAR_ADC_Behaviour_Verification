// =============================================================================
// 文件名     : sar20_digital_top.sv
// 模块名     : sar20_digital_top
// 版本       : v1.0
// 功能       : 18 片调度、DEM、dither 与 CAAZ 的数字控制顶层
//
// 设计说明   :
//   * sar20_digital_control 用 command_committed_q 把一笔转换锁成只接受第一个合法码；逐前缀输入会被判错，与冻结 C1 冲突。
//   * 同沿优先级：复位 > 清空/禁用 > 种子重载 > 采集申请 > 命令受理。
//   * 端口仍含 command_code_o 与 bridge_correction_q32_o，冻结契约 C3/C4 要求删除。
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
// 本文件含两层：sar20_digital_top 只把端口原样接到 sar20_digital_control，
// 全部状态机、错误判据与输出寄存都在后者，改行为只需看 control 那一层。
// 端口按四组读：acq_* 采集侧（模拟前端据片 ID 与位图动作）、conv_* 转换侧、
// recon_* 外部重构回传、out_* 两样本配对输出。位序统一 slot 0 在最低位。
// 三个使能语义不同：advance_i 是单拍申请，clear_i/enable_i 是电平许可。
// -------------------------------------------------------------------------
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
      // 本顶层走外部重构退休（SplitRetirement=0），片上流水那组握手空置。
      // 接常数而不是从端口表里删掉，是为了和 control 的参数化端口保持一一对应，
      // 换模式时只改一个参数，不用同时改两处连线。
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

// =========================================================================
// 2. 组合输出与连续性
// =========================================================================
// 输出在无效时清零，外部不得把上一拍残留当成新一笔结果。
// 三级门控：active 只看配置许可（复位/清空/使能），fault_o 是粘滞故障，
// run 同时排除两者。除 out_*/acq_valid_o 外，所有 valid 都由 run 再与一次。
  assign active = rst_ni && !clear_i && enable_i;
  assign fault_o = alignment_fault_o || caaz_fault;
  assign run = active && !fault_o;
  assign fault_pulse_o = active && (caaz_fault_pulse || alignment_fault_pulse_q);

// ready 的四个否定项各挡一类在途事务：坏样本未排空、上一次抽取未被调度器接收、
// 当前转换组尚未退休、以及 run 本身。少写任一项都会造成同拍双受理。
  assign advance_ready_o = run && !bad_pending_q && !dispatch_pending_q && !conv_active_q;
  assign dispatch = advance_i && advance_ready_o;
  // 空闲同沿冲突交给模块二原子拒绝。已有转换时不能绕过冲突写种子。
  // 判据里保留 advance_i：同一拍既申请抽取又重载种子时，只有 ready 成立（说明空闲）
  // 才把种子转发下去，否则新种子会被紧随其后的抽取立刻覆盖，等于静默丢种子。
  assign forward_seed = run && !bad_pending_q && seed_load_i && (!advance_i || advance_ready_o);
  assign seed_error_o = active && (d8_seed_error || busy_seed_error_q);

  // 对齐错误有两条来源：CAAZ 回传的样本 ID 与本层当前采样 ID 不一致（跨样本串线），
  // 以及 SplitRetirement 模式下重构结果的绑定检查失败。前者两种模式都查，
  // 后者只在片上流水把「接收 ADC2」与「重构完成」拆成两段时才存在。
  assign alignment_error = (caaz_acq_valid && (caaz_acq_id != acq_sample_id_o)) ||
                           (SplitRetirement && result_binding_error_i);
  // acquisition_allowed 只挡坏样本，acq_valid_o 再额外挡对齐错误：
  // 前者用于「要不要继续采」，后者用于「这一拍能不能把控制字交给模拟侧」。
  assign acquisition_allowed = run && !bad_pending_q && !retired_bad;
  assign acq_valid_o = run && !bad_pending_q && caaz_acq_valid && !alignment_error;
  assign conv_ready_o = run && !bad_pending_q && conv_active_q && d8_conv_ready;
  // 外接重构随当前转换组退休。片上流水在接收 ADC2 结果时释放转换组，
  // 后续重构由独立所有权队列与 CAAZ 顺序共同验证。
  // 两种模式的差别只在「谁代表这一笔结束」：
  //   SplitRetirement=1：片上流水，transfer_* 握手成功即退休，重构通道恒 ready；
  //   SplitRetirement=0：外部重构，recon_* 握手成功才退休，所以 ready 必须同时
  //   核对 sample_id 与命令是否已落定——ID 不符的迟到结果不能被当成本笔的结论。
  // 两分支都保留 command_committed_q 或 bad 的或项：未落定命令的样本只允许以「坏」
  // 的身份退休，否则会把没发过命令的样本记成一次正常转换。
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
  // 命令只受理一次：conv_ready_o 说明模块二在等码，command_committed_q 挡住同一笔的
  // 后续（重复或逐前缀）命令，retired_bad 挡住已判坏的样本。三个条件缺一不可——
  // 少了第二个，逐前缀输入会被拼成一条更长的命令，与冻结契约 C1 冲突。
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

// =========================================================================
// 3. 同步时序与状态转移
// =========================================================================
// 复位优先于受理；在途事务在清空/禁用同沿丢弃，ID 生命周期按样本绑定。
// 这里的分支是同步清空而不是异步复位：!active（复位/清空/禁用任一）成立时所有
// 状态寄存器一起归零，不产生错误脉冲、不保留在途转换组。下游若依赖「清空后
// 还能看到最后一个错误」会失效——错误只在 active 期间寄存。
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
      // 种子上写与申请同拍但 ready 不成立时记一次错，只报一拍，不粘滞：
      // 这一拍没写进去，下一拍可以重新申请，不属于需要停机的故障。
      busy_seed_error_q <= run && seed_load_i && advance_i && !advance_ready_o;
      // 对齐错误同时给出粘滞位与单拍脉冲。粘滞位负责停机，脉冲负责让上游
      // 只计数一次；脉冲用 !alignment_fault_o 限定，避免故障期间每拍重复上报。
      alignment_fault_pulse_q <= alignment_error && !alignment_fault_o;
      if (alignment_error) alignment_fault_o <= 1'b1;
      // 显式坏样本已进入有序重构。停止新的模拟采样，同时让旧结果和坏样本排空。
      if (retired_bad) bad_pending_q <= 1'b1;
      // 两条来源要分开看：d8_code_error 是模块二自己判的非法码，后一项是本层拒收
      // （命令来了但当前没有可受理的转换组）。合并成一个引脚是因为对外只有一条错误线，
      // 但调试时应当知道二者含义不同：前者是码的问题，后者是时序的问题。
      code_error_o <= run && (d8_code_error || (code_valid_i && !code_request));
      // 结果到了、ready 却是低：说明重构结果与本层当前转换组配不上（ID 不符或命令未落定）。
      recon_error_o <= run && recon_valid_i && !recon_ready_o;
      mask_valid_q <= d8_mask_valid;

      // dispatch_pending_q 的生命周期：受理那一拍置位，等到模块二给出 acq_valid、
      // 报 stall 或报种子错误三者之一才清。它存在的意义是覆盖「已申请但下游还没接」的
      // 中间拍，这段时间 advance_ready_o 必须为低，否则会发出第二笔申请。
      if (dispatch) dispatch_pending_q <= 1'b1;
      else if (d8_acq_valid || stall_o || d8_seed_error) dispatch_pending_q <= 1'b0;

      // 三选一，按优先级从上到下：新抽取被接收 > 本笔退休 > 命令落定。
      // 新受理必须压过退休同沿的旧值（同一拍既退休又受理是合法的背靠背流水），
      // 因此不能写成互不相关的 if。command_committed_q 在受理时清零、掩码出现时置位，
      // 这就是「一笔转换只认第一个合法码」的落地位置。
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
        // 命令码与两张位图同拍寄存：三者属于同一笔转换，分开打拍会让下游看到
        // 「新命令 + 旧掩码」这种不存在的组合。
        command_code_o <= d8_command_code;
        main_mask_o <= d8_main_mask;
        sub_mask_o <= d8_sub_mask;
        bridge_correction_q32_o <= d8_bridge_correction;
      end
    end
  end
endmodule
