// 模块：sar_structural_ctrl；真实slice所有权、seeded9bit SAR与前代fine上下文。
// source_control_binding由调用者显式提供；本模块不选择物理DEM/dither/bridge映射。
// source_control_valid/ready在advance沿为新acquiring组冻结word/state；quiet沿读取旧acquiring元数据
// 为current。无valid只创建invalid acquisition，不重用旧word为good。
// actual conversion masks/commonQ在每次已解析SAR更新的RDAC接受沿寄存，再随residue保存。
// 采集连接在绑定中逐unit明确；未来未知SAR conversion mask不能在采集开始时捏造。
// 相位是显式支持的16tick工程schedule；valid不足按截止取消，不声称模拟settle/STA。
// clk上升沿同步低有效reset；disable取消所有资格。普通RA AZ的逐笔状态由外部AZ模型持有。
`timescale 1ns/1ps
`include "rtl_params.vh"
module sar_structural_ctrl (
    input wire clk, rst_n, enable,
    input wire source_control_valid,
    input wire [CONTROL_WORD_BITS-1:0] source_control_word,
    input wire [STATE_BITS-1:0] source_control_state,
    output wire source_control_ready,
    output wire acquisition_control_valid,
    output wire [CONTROL_WORD_BITS-1:0] acquisition_control_word,
    output wire [STATE_BITS-1:0] acquisition_control_state,
    output wire signed [V_BITS-1:0] acquisition_sadc_shift_q,
    output wire signed [V_BITS-1:0] acquisition_rdac_shift_q,
    input wire [6:0] flash_therm,
    input wire flash_valid,
    input wire [1:0] coarse_cmp_valid, coarse_cmp_ge,
    input wire fine_cmp_valid, fine_cmp_ge,
    input wire analog_bad, recon_busy,
    output wire [3:0] phase,
    output wire quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate,
    output wire [17:0] acquiring_mask, converting_mask,
    output wire [17:0] aux_charge_enable, hold_low_enable,
    output wire [1:0] coarse_compare_enable,
    output logic [1:0] coarse_acquire_enable,
    output logic flash_acquire_enable, fine_acquire_enable,
    output wire [1:0][8:0] coarse_trial,
    output wire [ADC2_BITS-1:0] fine_trial,
    output wire fine_compare_enable, flash_sample,
    output wire [17:0] slice_sel,
    output wire [N_SLICES-1:0][N_UNIT_MAIN-1:0] main_sw,
    output wire [N_SLICES-1:0][N_UNIT_SUB-1:0] sub_sw,
    output logic sw_valid,
    output wire recon_start,
    output wire [31:0] context_id,
    output wire [8:0] context_coarse,
    output wire [STATE_BITS-1:0] context_state,
    output wire [CONTROL_WORD_BITS-1:0] context_control_word,
    output wire [N_ACTIVE-1:0][4:0] context_slices,
    output wire [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] context_main,
    output wire [N_ACTIVE-1:0][N_UNIT_SUB-1:0] context_sub,
    output wire context_analog_bad,
    output wire signed [V_BITS-1:0] context_known_common_q,
    output logic [ADC2_BITS-1:0] fine_code,
    output logic [31:0] error_code
);
  `include "rtl_error_codes.vh"
  wire advance, capture;
  analog_phase_ctrl #(.P_REF_ON(10), .P_CAPTURE(9)) u_phase (
      .clk(clk), .rst_n(rst_n), .enable(enable), .phase(phase),
      .quiet_sample(quiet_sample), .bank_advance(advance), .residue_capture(capture),
      .tp_clock(tp_clock), .ra_az(ra_az), .ra_amplify(ra_amplify),
      .ref_precharge(ref_precharge), .ref_accurate(ref_accurate)
  );
  wire [N_ACTIVE-1:0][4:0] acquiring_ids, conv_ids;
  wire conv_valid;
  wire acquisition_primed;
  wire [31:0] current_id;
  slice_pool_ctrl #(.P_SEED(32'(POOL_SHUFFLE_SEED))) u_pool (
      .clk(clk), .rst_n(rst_n), .enable(enable), .advance(advance),
      .shuffle(1'(POOL_SHUFFLE_ENABLE)), .acquiring_ids(acquiring_ids),
      .converting_ids(conv_ids), .acquiring_mask(acquiring_mask),
      .converting_mask(converting_mask), .conversion_valid(conv_valid),
      .sample_id(current_id), .acquisition_primed(acquisition_primed)
  );
  logic [CONTROL_WORD_BITS-1:0] acquiring_word, current_word;
  logic [STATE_BITS-1:0] acquiring_state, current_state;
  logic acquiring_word_valid, current_word_valid;
  logic signed [V_BITS-1:0] current_rdac_shift_q;
  wire acquisition_binding_valid, acquisition_shift_in_range;
  wire [31:0] acquisition_binding_error;
  // Both macro shifts belong to the registered acquiring group, not the
  // current conversion. The caller supplies the actual source implementation.
  source_acquisition_binding u_acquisition_binding (
      .control_word(acquiring_word), .state(acquiring_state), .slice_ids(acquiring_ids),
      .sadc_input_shift_q(acquisition_sadc_shift_q),
      .rdac_input_shift_q(acquisition_rdac_shift_q),
      .valid(acquisition_binding_valid), .error_code(acquisition_binding_error)
  );
  assign source_control_ready = rst_n && enable && advance;
  assign acquisition_control_valid = rst_n && enable && acquiring_word_valid &&
      acquisition_binding_valid && acquisition_binding_error == 0 && acquisition_shift_in_range;
  assign acquisition_control_word = acquiring_word;
  assign acquisition_control_state = acquiring_state;
  // These remain digital macro proxies. Physical TP/mask/chop closure and independent CBIAS hold
  // require the explicitly bound analog interface.
  assign aux_charge_enable = acquisition_control_valid ? acquiring_mask : '0;
  assign hold_low_enable = acquisition_control_valid ? ~acquiring_mask : '1;
  assign flash_sample = quiet_sample;

  logic coarse_cancel, fine_cancel, bank;
  wire [2:0] flash_code;
  sadc_enc #(.P_B1(3)) u_flash (.cmp_raw(flash_therm), .sadc_code(flash_code));
  wire [1:0] unused_coarse_busy, coarse_done, coarse_update;
  wire [1:0][8:0] resolved;
  for (genvar b = 0; b < 2; b++) begin : g_coarse
    sar_trial_ctrl #(.P_BITS(9), .P_SEED_BITS(3)) u_sar (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .start(advance && flash_valid && current_word_valid && bank == 1'(b)),
        .cancel(coarse_cancel), .seed_code({flash_code, 6'b0}),
        .cmp_valid(coarse_cmp_valid[b]), .cmp_ge(coarse_cmp_ge[b]),
        .trial_code(coarse_trial[b]), .resolved_code(resolved[b]),
        .busy(unused_coarse_busy[b]), .done(coarse_done[b]),
        .resolved_valid(coarse_update[b]), .compare_enable(coarse_compare_enable[b])
    );
  end
  wire unused_fine_busy, fine_done, unused_fine_update, context_valid;
  wire [ADC2_BITS-1:0] fine_resolved;
  sar_trial_ctrl #(.P_BITS(int'(ADC2_BITS)), .P_SEED_BITS(0)) u_fine (
      .clk(clk), .rst_n(rst_n), .enable(enable), .start(advance && context_valid),
      .cancel(fine_cancel), .seed_code({ADC2_BITS{1'b0}}),
      .cmp_valid(fine_cmp_valid), .cmp_ge(fine_cmp_ge), .trial_code(fine_trial),
      .resolved_code(fine_resolved), .busy(unused_fine_busy), .done(fine_done),
      .resolved_valid(unused_fine_update), .compare_enable(fine_compare_enable)
  );
  wire [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] bound_main;
  wire [N_ACTIVE-1:0][N_UNIT_SUB-1:0] bound_sub;
  wire signed [V_BITS-1:0] bound_common_q;
  wire bound_valid;
  wire [31:0] bound_error;
  source_control_binding u_source_binding (
      .coarse_resolved(resolved[bank]), .control_word(current_word),
      .state(current_state), .slice_ids(conv_ids), .main_on(bound_main),
      .sub_on(bound_sub), .known_common_q(bound_common_q),
      .valid(bound_valid), .error_code(bound_error)
  );
  // Every quantized per-unit term must fit before the fold is evaluated. The conservative interval
  // supports either conversion rail for every unit.
  localparam logic signed [V_BITS:0] V_ONE = {{V_BITS{1'b0}}, 1'b1};
  localparam logic signed [V_BITS:0] V_SCALE = V_ONE <<< V_FRAC;
  localparam logic signed [V_BITS:0] COMMON_MIN = -(V_ONE <<< (V_BITS-1)) + V_SCALE;
  localparam logic signed [V_BITS:0] COMMON_MAX = (V_ONE <<< (V_BITS-1)) - V_ONE - V_SCALE;
  wire signed [V_BITS:0] common_wide = {bound_common_q[V_BITS-1], bound_common_q};
  wire common_in_range = common_wide >= COMMON_MIN && common_wide <= COMMON_MAX;
  wire signed [V_BITS:0] acquisition_shift_wide =
      {acquisition_rdac_shift_q[V_BITS-1], acquisition_rdac_shift_q};
  assign acquisition_shift_in_range = acquisition_shift_wide >= COMMON_MIN &&
      acquisition_shift_wide <= COMMON_MAX;
  wire query_update = enable && conv_valid && current_word_valid &&
      coarse_update[bank] && phase >= 2 && phase <= 8;
  wire accepted_update = query_update && bound_valid && common_in_range && bound_error == 0 &&
      bound_common_q == current_rdac_shift_q;
  logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] accepted_main;
  logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0] accepted_sub;
  logic signed [V_BITS-1:0] accepted_common_q;
  logic accepted_mask_valid, binding_bad;
  rdac_drv u_rdac (
      .clk(clk), .rst_n(rst_n && enable), .load(accepted_update),
      .slice_id(conv_ids), .main_on(bound_main), .sub_on(bound_sub),
      .slice_sel(slice_sel), .main_sw(main_sw), .sub_sw(sub_sw)
  );
  logic coarse_ok, fine_ok, fine_done_seen;
  cal_sample_context u_context (
      .clk(clk), .rst_n(rst_n), .enable(enable), .residue_capture(capture),
      .quiet_sample(quiet_sample),
      .current_valid(conv_valid && current_word_valid && coarse_ok &&
                     accepted_mask_valid && !binding_bad),
      .current_id(current_id), .current_coarse(resolved[bank]),
      .current_state(current_state), .current_control_word(current_word),
      .current_chop_neg(1'b0), .current_az_pair_id(32'd0), .current_az_valid(1'b0),
      .current_slices(conv_ids),
      .current_main(accepted_main), .current_sub(accepted_sub),
      .current_known_common_q(accepted_common_q), .analog_bad(analog_bad),
      .current_bad(binding_bad), .fine_valid(context_valid), .fine_id(context_id),
      .fine_coarse(context_coarse), .fine_state(context_state),
      .fine_control_word(context_control_word),
      .fine_slices(context_slices), .fine_main(context_main), .fine_sub(context_sub),
      .fine_analog_bad(context_analog_bad), .fine_known_common_q(context_known_common_q)
  );
  assign recon_start = enable && phase == 15 && context_valid && fine_ok && !recon_busy;

  always_ff @(posedge clk) begin
    if (!rst_n || !enable) begin
      coarse_cancel <= 1'b0;
      fine_cancel <= 1'b0;
      bank <= 1'b0;
      coarse_acquire_enable <= '0;
      flash_acquire_enable <= 1'b0;
      fine_acquire_enable <= 1'b0;
      acquiring_word <= '0;
      acquiring_state <= '0;
      acquiring_word_valid <= 1'b0;
      current_word <= '0;
      current_state <= '0;
      current_word_valid <= 1'b0;
      current_rdac_shift_q <= '0;
      accepted_main <= '0;
      accepted_sub <= '0;
      accepted_common_q <= '0;
      accepted_mask_valid <= 1'b0;
      binding_bad <= 1'b0;
      coarse_ok <= 1'b0;
      fine_ok <= 1'b0;
      fine_done_seen <= 1'b0;
      fine_code <= '0;
      sw_valid <= 1'b0;
      error_code <= ERR_NONE;
    end else begin
      flash_acquire_enable <= phase != 0;
      fine_acquire_enable <= phase >= 13;
      coarse_cancel <= phase == 7 || phase == 8;
      fine_cancel <= phase == 13 || phase == 14;
      sw_valid <= accepted_update;
      if (quiet_sample) begin
        bank <= !bank;
        current_word <= acquiring_word;
        current_state <= acquiring_state;
        current_word_valid <= acquisition_control_valid;
        current_rdac_shift_q <= acquisition_rdac_shift_q;
        coarse_ok <= 1'b0;
        fine_ok <= 1'b0;
        fine_done_seen <= 1'b0;
        accepted_mask_valid <= 1'b0;
        binding_bad <= 1'b0;
        if (acquiring_word_valid && !acquisition_control_valid)
          error_code <= ERR_SOURCE_BINDING;
      end else if (fine_done) begin
        // Shorter explicitly bound backends can complete before phase14. Retain that event until
        // the fixed deadline instead of losing a pulse.
        fine_done_seen <= 1'b1;
      end
      if (advance) begin
        coarse_acquire_enable <= bank ? 2'b01 : 2'b10;
        acquiring_word_valid <= source_control_valid;
        if (source_control_valid) begin
          acquiring_word <= source_control_word;
          acquiring_state <= source_control_state;
        end else begin
          error_code <= ERR_SOURCE_BINDING;
        end
        if (!flash_valid && current_word_valid)
          error_code <= ERR_SADC_NOT_READY;
      end
      if (query_update) begin
        if (accepted_update) begin
          accepted_main <= bound_main;
          accepted_sub <= bound_sub;
          accepted_common_q <= bound_common_q;
          accepted_mask_valid <= 1'b1;
        end else begin
          binding_bad <= 1'b1;
          // status_word has a finite project error field; do not silently wrap an arbitrary caller
          // error value into that public field.
          error_code <= bound_error != 0 && bound_error < 32'h04000000 ?
              bound_error : ERR_SOURCE_BINDING;
        end
      end
      if (phase == 8) begin
        coarse_ok <= current_word_valid && coarse_done[bank];
        if (conv_valid && current_word_valid && !coarse_done[bank])
          error_code <= ERR_SADC_NOT_READY;
      end
      if (phase == 14) begin
        fine_ok <= fine_done_seen || fine_done;
        fine_code <= fine_resolved;
        if (context_valid && !(fine_done_seen || fine_done))
          error_code <= ERR_ADC2_NOT_READY;
      end
      // Later same-edge fault assignments take priority. Reset/disable remain above all events. A
      // good later conversion does not erase sticky cause.
      if (capture && binding_bad)
        error_code <= ERR_SOURCE_BINDING;
      if (phase == 15 && context_valid && fine_ok && recon_busy)
        error_code <= ERR_RECON_BUSY;
    end
  end
endmodule
