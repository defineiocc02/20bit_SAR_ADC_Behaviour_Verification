// 模块：sar20_digital_core；显式source binding、物理权重校正与样本上下文。
// 必须先提供Config/format/geometry/control binding生成rtl_params.vh和外部 source_control_binding；
// 没有可默认编译的几何、ADC2窗口、Q格式或物理mapper。
// 此首版仅支持逐unit证明的AllSIGNAL/明确±Vfs/已知common项严格fold子集。
// lower/upper dither未实现；source word不是给旧scalar dither改名后的替身。
// 时钟：所有core输入属于clk域；rst_n同步低有效，外部宏须满足建立/保持与有效期限。
// 配置：组合读；posedge接受写；reset/clear优先，ready后锁定，三个标量和所有权重在同epoch写全后
// validate。0x1018旧模式控制地址已退休，不提供别名。
// 清除取消在途包/结果/CAAZ配对并使装载bitmap失效，但保留权重数值和row缓存。
// observer v2位序由显式packet_schema.json定义；只记录实际mask/word/state/common项，不携带a/term数组
// 或模拟真值；接收方须用相同hash binding恢复已知连接。
`timescale 1ns/1ps
`include "rtl_params.vh"
module sar20_digital_core #(
    parameter int P_RECON_STAGES = int'(RECON_UNROLL),
    parameter bit P_CAL_OBSERVE = 1'b0,
    parameter bit P_CAAZ_ENABLE = 1'b0
) (
    input logic clk, rst_n,
    input wire source_control_valid,
    input wire [CONTROL_WORD_BITS-1:0] source_control_word,
    input wire [STATE_BITS-1:0] source_control_state,
    output wire source_control_ready, acquisition_control_valid,
    output wire [CONTROL_WORD_BITS-1:0] acquisition_control_word,
    output wire [STATE_BITS-1:0] acquisition_control_state,
    output wire signed [V_BITS-1:0] acquisition_sadc_shift_q,
    output wire signed [V_BITS-1:0] acquisition_rdac_shift_q,
    input wire [6:0] flash_therm,
    input wire flash_valid,
    input wire [1:0] coarse_cmp_valid, coarse_cmp_ge,
    input wire fine_cmp_valid, fine_cmp_ge,
    output wire [3:0] analog_phase,
    output wire quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate,
    output wire [17:0] acquiring_mask, converting_mask, aux_charge_enable, hold_low_enable,
    output wire [1:0] coarse_compare_enable, coarse_acquire_enable,
    output wire flash_acquire_enable, fine_acquire_enable,
    output wire [1:0][8:0] coarse_trial,
    output wire [ADC2_BITS-1:0] fine_trial,
    output wire fine_compare_enable, flash_sample,
    input logic cfg_wr,
    input logic [15:0] cfg_addr,
    input logic [63:0] cfg_wdata,
    output logic [63:0] cfg_rdata,
    input logic cfg_validate, cfg_clear_valid,
    output wire cfg_ready,
    input logic ra_sat, rdac_ovf, adc2_over,
    output wire [N_SLICES-1:0] slice_sel,
    output wire [N_SLICES-1:0][N_UNIT_MAIN-1:0] main_sw,
    output wire [N_SLICES-1:0][N_UNIT_SUB-1:0] sub_sw,
    output wire sw_valid,
    output wire [OUT_BITS-1:0] dout,
    output wire dout_valid, clip_low, clip_high, analog_ovf, acc_ovf,
    // 控制层抑制结果也必须可见；该位不靠valid间隙或ID推测，直到epoch撤销。
    output wire conversion_fault,
    output wire [31:0] status_word, dout_sample_id,
    output wire [4:0] dout_flags,
    output wire cal_obs_valid,
    output wire [CAL_OBS_BITS-1:0] cal_obs_data,
    output wire dout_analog_bad,
    input wire caaz_az_capture_ok,
    output wire caaz_input_chop_neg, caaz_az_capture_en, caaz_az_hold,
    output wire [33:0] cal_obs_caaz_data,
    output wire caaz_raw_valid,
    output wire [91:0] caaz_raw_data,
    output wire signed [20:0] caaz_pair_difference,
    output wire caaz_pair_difference_valid,
    output wire [31:0] caaz_pair_first_sample_id, caaz_pair_id,
    output wire caaz_pair_fault,
    output wire [3:0] caaz_pair_fault_code
);
  `include "rtl_error_codes.vh"
  localparam int W_A = int'(ACC_BITS) - int'(V_FRAC) - 1;
  localparam logic [W_BITS-1:0] W_MAX = W_BITS'(1) << (W_BITS-1);
  initial begin
    if (N_SLICES != 18 || N_ACTIVE != 8 || B1 != 9 || OUT_BITS != 20 ||
        PHASES != 16 || ADC2_BITS < 1 || ADC2_BITS > 12 ||
        N_UNIT_MAIN < 1 || N_UNIT_SUB < 1 || N_UNIT_TOTAL > 128 ||
        N_UNIT_TOTAL != N_UNIT_MAIN + N_UNIT_SUB ||
        W_BITS != 48 || W_FRAC != 30 || V_BITS != 64 || V_FRAC != 32 || ACC_BITS != 96 ||
        CONTROL_WORD_BITS < 1 || STATE_BITS < 1 || P_RECON_STAGES < 1 ||
        ((W_A + P_RECON_STAGES - 1) / P_RECON_STAGES + 2) > int'(PHASES))
      $fatal(1, "sar20_digital_core: unsupported explicit numerical/control binding");
  end

  // Scalar/window addresses retain the existing transport ABI. The removed mode-control register is
  // not accepted or silently reinterpreted.
  wire weight_region = cfg_addr[15:12] == 4'h0;
  wire window_region = cfg_addr[15:13] == 3'b001;
  wire is_off = cfg_addr == 16'h1000;
  wire is_min = cfg_addr == 16'h1008;
  wire is_max = cfg_addr == 16'h1010;
  wire is_status = cfg_addr == 16'h1020;
  wire [6:0] unit_address = cfg_addr[9:3];
  wire [4:0] window_slice = cfg_addr[12:8];
  wire weight_address_valid = weight_region && cfg_addr[11:10] == 0 &&
      cfg_addr[2:0] == 0 && int'(unit_address) < int'(N_UNIT_TOTAL);
  wire window_address_valid = window_region && cfg_addr[7:0] == 0 &&
      int'(window_slice) < int'(N_SLICES);
  wire address_valid = weight_address_valid || window_address_valid || is_off || is_min || is_max;
  wire bad_weight_width = weight_region && ((|cfg_wdata[63:W_BITS]) ||
      cfg_wdata[W_BITS-1:0] == 0 || cfg_wdata[W_BITS-1:0] >= W_MAX);
  wire rc_busy;
  wire epoch_rst_n = rst_n && !cfg_clear_valid;
  wire cfg_bus_ok = rst_n && cfg_wr && address_valid && !bad_weight_width &&
      !cfg_ready && !cfg_validate && !cfg_clear_valid;
  wire cfg_bus_reject = cfg_wr && !cfg_bus_ok;
  logic [4:0] current_slice;
  logic [31:0] bus_error;
  wire weight_error, weights_ready;
  always_ff @(posedge clk) begin
    if (!rst_n || cfg_clear_valid) begin
      current_slice <= '0;
      bus_error <= ERR_NONE;
    end else begin
      if (cfg_bus_ok && window_address_valid)
        current_slice <= window_slice;
      if (cfg_bus_reject)
        bus_error <= bad_weight_width ? ERR_W_RANGE : ERR_CFG_WRITE;
      else if (weight_error)
        bus_error <= ERR_W_SUM;
      else if (cfg_validate && !cfg_wr && !rc_busy)
        bus_error <= ERR_NONE;
    end
  end
  wire [N_SLICES-1:0][N_UNIT_TOTAL-1:0][W_BITS-1:0] weights;
  wire [N_SLICES-1:0][63:0] row_total;
  wire [W_BITS-1:0] selected_weight;
  weight_store #(.P_N_SLICES(int'(N_SLICES)), .P_N_UNITS(int'(N_UNIT_TOTAL))) u_wstore (
      .clk(clk), .rst_n(rst_n), .cfg_ready(cfg_ready), .clear_load(cfg_clear_valid),
      .load_complete(weights_ready), .wr_en(cfg_bus_ok && weight_address_valid),
      .wr_slice(current_slice), .wr_unit(unit_address), .wr_data(cfg_wdata[W_BITS-1:0]),
      .err_write(weight_error), .w_q(weights), .row_total(row_total),
      .selected_weight(selected_weight)
  );
  wire [31:0] calibration_error;
  wire signed [V_BITS-1:0] offset_q, adc2_min_q, adc2_max_q;
  wire [1:0] scalar_select = is_min ? 2'd1 : is_max ? 2'd2 : 2'd0;
  calib_regs u_calib (
      .clk(clk), .rst_n(rst_n), .wr_en(cfg_bus_ok && (is_off || is_min || is_max)),
      .sel(scalar_select), .data_v(cfg_wdata[V_BITS-1:0]), .weights_ready(weights_ready),
      .config_busy(cfg_wr || rc_busy), .validate(cfg_validate), .clear_valid(cfg_clear_valid),
      .cfg_ready(cfg_ready), .err_code(calibration_error), .offset_q(offset_q),
      .adc2_min_q(adc2_min_q), .adc2_max_q(adc2_max_q)
  );

  wire launch_recon;
  wire [31:0] context_id, controller_error;
  wire [8:0] context_coarse;
  wire [STATE_BITS-1:0] context_state;
  wire [CONTROL_WORD_BITS-1:0] context_word;
  wire [N_ACTIVE-1:0][4:0] context_slices;
  wire [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] context_main;
  wire [N_ACTIVE-1:0][N_UNIT_SUB-1:0] context_sub;
  wire signed [V_BITS-1:0] context_common_q;
  wire context_bad, context_chop_neg, context_az_valid;
  wire [31:0] context_az_pair_id;
  wire [ADC2_BITS-1:0] fine_code;
  sar_structural_ctrl #(.P_CAAZ_ENABLE(P_CAAZ_ENABLE)) u_structure (
      .clk(clk), .rst_n(epoch_rst_n), .enable(cfg_ready),
      .source_control_valid(source_control_valid), .source_control_word(source_control_word),
      .source_control_state(source_control_state), .source_control_ready(source_control_ready),
      .acquisition_control_valid(acquisition_control_valid),
      .acquisition_control_word(acquisition_control_word),
      .acquisition_control_state(acquisition_control_state),
      .acquisition_sadc_shift_q(acquisition_sadc_shift_q),
      .acquisition_rdac_shift_q(acquisition_rdac_shift_q),
      .flash_therm(flash_therm), .flash_valid(flash_valid),
      .coarse_cmp_valid(coarse_cmp_valid), .coarse_cmp_ge(coarse_cmp_ge),
      .fine_cmp_valid(fine_cmp_valid), .fine_cmp_ge(fine_cmp_ge),
      .analog_bad(ra_sat || rdac_ovf || adc2_over), .recon_busy(rc_busy),
      .caaz_az_capture_ok(caaz_az_capture_ok), .caaz_input_chop_neg(caaz_input_chop_neg),
      .caaz_az_capture_en(caaz_az_capture_en), .caaz_az_hold(caaz_az_hold),
      .phase(analog_phase), .quiet_sample(quiet_sample), .tp_clock(tp_clock),
      .ra_az(ra_az), .ra_amplify(ra_amplify), .ref_precharge(ref_precharge),
      .ref_accurate(ref_accurate), .acquiring_mask(acquiring_mask), .converting_mask(converting_mask),
      .aux_charge_enable(aux_charge_enable), .hold_low_enable(hold_low_enable),
      .coarse_compare_enable(coarse_compare_enable), .coarse_acquire_enable(coarse_acquire_enable),
      .flash_acquire_enable(flash_acquire_enable), .fine_acquire_enable(fine_acquire_enable),
      .coarse_trial(coarse_trial), .fine_trial(fine_trial), .fine_compare_enable(fine_compare_enable),
      .flash_sample(flash_sample), .slice_sel(slice_sel), .main_sw(main_sw), .sub_sw(sub_sw),
      .sw_valid(sw_valid), .recon_start(launch_recon), .context_id(context_id),
      .context_coarse(context_coarse), .context_state(context_state),
      .context_control_word(context_word), .context_chop_neg(context_chop_neg),
      .context_az_pair_id(context_az_pair_id), .context_az_valid(context_az_valid),
      .context_slices(context_slices), .context_main(context_main), .context_sub(context_sub),
      .context_analog_bad(context_bad), .context_known_common_q(context_common_q),
      .fine_code(fine_code), .error_code(controller_error)
  );
  assign cal_obs_valid = P_CAL_OBSERVE && rst_n && !cfg_clear_valid && cfg_ready && launch_recon;
  assign cal_obs_data = P_CAL_OBSERVE ? {
      context_id, context_coarse, context_state, context_word, fine_code,
      context_common_q, context_slices, context_main, context_sub, context_bad
  } : '0;
  assign cal_obs_caaz_data = P_CAL_OBSERVE ?
      {context_chop_neg, context_az_pair_id, context_az_valid} : '0;

  wire [OUT_BITS-1:0] rc_dout;
  wire rc_valid, rc_clip_low, rc_clip_high, rc_acc_ovf, rc_gain_err, rc_adc2_ovf;
  wire [31:0] raw_sample_id;
  wire [4:0] raw_flags;
  recon_core #(
      .P_N_ACTIVE(int'(N_ACTIVE)), .P_N_MAIN(int'(N_UNIT_MAIN)), .P_N_SUB(int'(N_UNIT_SUB)),
      .P_N_SLICES(int'(N_SLICES)), .P_ADC2_BITS(int'(ADC2_BITS)), .P_STAGES(P_RECON_STAGES),
      .P_USE_ROW_TOTALS(1'b1)
  ) u_recon (
      .clk(clk), .rst_n(epoch_rst_n), .cfg_ready(cfg_ready), .start(launch_recon),
      .sample_id(context_id), .clr_ovf(cfg_clear_valid), .slice_id(context_slices),
      .main_on(context_main), .sub_on(context_sub), .adc2_code(fine_code),
      .inj_q(context_common_q), .w_rom(weights), .row_total(row_total),
      .offset_q(offset_q), .adc2_min_q(adc2_min_q), .adc2_max_q(adc2_max_q),
      .dout(rc_dout), .dout_valid(rc_valid), .clip_low(rc_clip_low), .clip_high(rc_clip_high),
      .acc_ovf(rc_acc_ovf), .gain_err(rc_gain_err), .adc2_ovf(rc_adc2_ovf), .busy(rc_busy),
      .result_sample_id(raw_sample_id), .result_flags(raw_flags)
  );
  logic pending_bad, pending_chop, pending_az_valid;
  logic [31:0] pending_az_id;
  always_ff @(posedge clk) begin
    if (!rst_n || cfg_clear_valid || !cfg_ready) begin
      pending_bad <= 1'b0;
      pending_chop <= 1'b0;
      pending_az_valid <= 1'b0;
      pending_az_id <= '0;
    end else if (launch_recon) begin
      pending_bad <= context_bad;
      pending_chop <= context_chop_neg;
      pending_az_valid <= context_az_valid;
      pending_az_id <= context_az_pair_id;
    end
  end
  wire raw_bad = rc_valid && pending_bad;
  wire unused_pair_fault_pulse, unused_pair_pending;
  assign caaz_raw_valid = P_CAAZ_ENABLE && rc_valid;
  assign caaz_raw_data = P_CAAZ_ENABLE ?
      {raw_sample_id, rc_dout, pending_chop, pending_az_id, pending_az_valid, raw_flags, raw_bad} : '0;
  caaz_pair_processor #(.P_CAAZ_ENABLE(P_CAAZ_ENABLE)) u_caaz_pair (
      .clk(clk), .rst_n(rst_n), .epoch_clear(cfg_clear_valid || !cfg_ready),
      .in_valid(rc_valid), .in_code(rc_dout), .in_sample_id(raw_sample_id),
      .in_chop_neg(pending_chop), .in_az_pair_id(pending_az_id), .in_az_valid(pending_az_valid),
      .in_flags(raw_flags), .in_analog_bad(raw_bad), .out_valid(dout_valid), .out_code(dout),
      .out_sample_id(dout_sample_id), .out_first_sample_id(caaz_pair_first_sample_id),
      .out_az_pair_id(caaz_pair_id), .out_flags(dout_flags), .out_analog_bad(dout_analog_bad),
      .pair_difference(caaz_pair_difference), .pair_difference_valid(caaz_pair_difference_valid),
      .fault(caaz_pair_fault), .fault_pulse(unused_pair_fault_pulse),
      .pair_pending(unused_pair_pending), .fault_code(caaz_pair_fault_code)
  );
  assign clip_low = rc_clip_low;
  assign clip_high = rc_clip_high;
  wire [31:0] merged_error = bus_error != ERR_NONE ? bus_error :
      calibration_error != ERR_NONE ? calibration_error :
      weight_error ? ERR_W_SUM : caaz_pair_fault ? ERR_CAAZ_PAIR : controller_error;
  // 直接取控制诊断，避免配置总线错误优先级掩盖已丢失转换。
  // core仍可观察后续结果；系统消费者须据此保守锁停，不能把好flags视为故障已恢复。
  assign conversion_fault = rst_n && !cfg_clear_valid && cfg_ready &&
      (controller_error != ERR_NONE || caaz_pair_fault);
  wire unused_gain_sticky, unused_adc2_sticky, unused_clip_low, unused_clip_high;
  wire [31:0] unused_status_clear_value;
  status_regs u_status (
      .clk(clk), .rst_n(rst_n), .clr(cfg_clear_valid), .ev_acc_ovf(rc_acc_ovf),
      .ev_gain_err(rc_gain_err), .ev_adc2_ovf(rc_adc2_ovf), .ev_clip_low(rc_clip_low),
      .ev_clip_high(rc_clip_high), .ev_analog_ovf((launch_recon && context_bad) || rc_adc2_ovf),
      .err_code(merged_error), .acc_ovf_sticky(acc_ovf), .gain_err_sticky(unused_gain_sticky),
      .adc2_ovf_sticky(unused_adc2_sticky), .clip_low_last(unused_clip_low),
      .clip_high_last(unused_clip_high), .analog_ovf_sticky(analog_ovf),
      .status_word(status_word), .status_clr_value(unused_status_clear_value)
  );
  always_comb begin
    cfg_rdata = '0;
    if (weight_address_valid)
      cfg_rdata = {{(64-W_BITS){1'b0}}, selected_weight};
    else if (is_off)
      cfg_rdata = offset_q;
    else if (is_min)
      cfg_rdata = adc2_min_q;
    else if (is_max)
      cfg_rdata = adc2_max_q;
    else if (is_status)
      cfg_rdata = {32'd0, status_word};
    else if (window_address_valid)
      cfg_rdata = {59'd0, current_slice};
  end
endmodule
