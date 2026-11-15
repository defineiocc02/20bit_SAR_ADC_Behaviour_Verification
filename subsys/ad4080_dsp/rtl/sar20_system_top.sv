//=============================================================================
// @file    sar20_system_top.sv
// @brief   真实 SAR 数字核与并行 DSP 的集成顶层。
// @details 将 SAR 实际退役码、样本 ID、结果标志和模拟粘滞故障连接到 DSP 适配器。
//          数字核的模拟宏调度须遵循其单独确认的接口规则。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: [设计约定] sar20_digital_core 与 DSP_G1_ACTIVE_2；产品手册不证明内部结构。
// 时钟域: clk；模拟宏观测和外部控制须满足数字核同步要求。
// 复位: rst_n 低有效，由外部异步置位、同步释放；SAR 须观察到其规定的复位时钟沿。
// 延迟: 由数字核与 DSP 流水决定，不额外假定固定模拟延迟。
// 吞吐: 遵循数字核结果退役速率与所选 DSP 抽取率。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: sar20_digital_core、sar20_ad4080_top。
// 边界: 不提供 PHY/CNV 启动替代实现；缺少完整外部捕获/配置代次绑定时不可启用 CAAZ。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
`include "rtl_params.vh"
module sar20_system_top #(
    parameter int P_RECON_STAGES=int'(RECON_UNROLL),
    parameter bit P_CAL_OBSERVE=1'b0,
    parameter int DSP_FIFO_DEPTH=16384,
    parameter bit P_GAIN_MAP_VALID=1'b0,
    parameter int K_GAIN=-1,
    parameter int SHIFT=-1,
    parameter bit P_GAIN_CODE0_SUPPORTED=1'b0,
    parameter bit P_COMP_COEFF_VALID=1'b0,
    parameter int C0=32'h40000000,
    parameter int C1=32'h40000000,
    parameter int C2=32'h40000000,
    parameter int P_MULTIBYTE_ORDER=-1,
    parameter int P_OFFSET_LSB_ADDR=-1,
    parameter int P_PRODUCT_ID_L=-1,
    parameter int P_SHORT_ADDR_BITS=-1,
    parameter int P_WRITE_SDO_DRIVE=-1
) (
    input logic clk,rst_n,
    input logic source_control_valid,
    input logic [CONTROL_WORD_BITS-1:0] source_control_word,
    input logic [STATE_BITS-1:0] source_control_state,
    output logic source_control_ready,acquisition_control_valid,
    output logic [CONTROL_WORD_BITS-1:0] acquisition_control_word,
    output logic [STATE_BITS-1:0] acquisition_control_state,
    // 同一实际采集组的归一化加性偏移 Q(V_FRAC)，仅由 acquisition_control_valid 资格化。
    output wire signed [V_BITS-1:0] acquisition_sadc_shift_q,acquisition_rdac_shift_q,
    input logic [6:0] flash_therm,
    input logic flash_valid,
    input logic [1:0] coarse_cmp_valid,coarse_cmp_ge,
    input logic fine_cmp_valid,fine_cmp_ge,
    output logic [3:0] analog_phase,
    output logic quiet_sample,tp_clock,ra_az,ra_amplify,ref_precharge,ref_accurate,
    output logic [N_SLICES-1:0] acquiring_mask,converting_mask,aux_charge_enable,hold_low_enable,
    output logic [1:0] coarse_compare_enable,coarse_acquire_enable,
    output logic flash_acquire_enable,fine_acquire_enable,
    output logic [1:0][8:0] coarse_trial,
    output logic [ADC2_BITS-1:0] fine_trial,
    output logic fine_compare_enable,flash_sample,
    input logic cfg_wr,
    input logic [15:0] cfg_addr,
    input logic [63:0] cfg_wdata,
    output logic [63:0] cfg_rdata,
    input logic cfg_validate,cfg_clear_valid,
    output logic cfg_ready,
    input logic ra_sat,rdac_ovf,adc2_over,
    output logic [N_SLICES-1:0] slice_sel,
    output logic [N_SLICES-1:0][N_UNIT_MAIN-1:0] main_sw,
    output logic [N_SLICES-1:0][N_UNIT_SUB-1:0] sub_sw,
    output logic sw_valid,
    output logic [OUT_BITS-1:0] sar_dout,
    output logic sar_dout_valid,sar_clip_low,sar_clip_high,sar_analog_ovf,sar_acc_ovf,
    output logic [31:0] sar_status_word,sar_sample_id,
    output logic [4:0] sar_flags,
    output logic sar_result_analog_bad,sar_conversion_fault,cal_obs_valid,
    output logic [CAL_OBS_BITS-1:0] cal_obs_data,
    input logic dsp_restart,
    output logic sample_accepted,
    output logic [31:0] accepted_sample_id,
    output logic sample_rejected,
    output logic [31:0] rejected_sample_id,
    output logic [4:0] rejected_flags,
    output logic rejected_after_fault,stream_fault,
    input logic cs_n,sclk,sdi,
    output logic sdo,sdo_oe,
    input logic adc_idle,interface_ready,
    input logic filter_sync,ext_event,arm,rd_en,
    input logic [3:0] gpio_i,
    output logic [3:0] gpio_o,gpio_oe,
    output logic [19:0] dsp_dout,
    output logic dsp_dout_valid,
    output logic [7:0] dsp_status,
    output logic correction_clip_low,correction_clip_high,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason,
    output logic processing_reject
);
    // 此顶层未提供完整 CAAZ 绑定，明确禁用该路径并接收禁用核的未使用输出。
    logic unused_caaz_input_chop_neg,unused_caaz_az_capture_en,unused_caaz_az_hold;
    logic [33:0] unused_cal_obs_caaz_data;
    logic unused_caaz_raw_valid,unused_caaz_pair_difference_valid,unused_caaz_pair_fault;
    logic [91:0] unused_caaz_raw_data;
    logic signed [20:0] unused_caaz_pair_difference;
    logic [31:0] unused_caaz_pair_first_sample_id,unused_caaz_pair_id;
    logic [3:0] unused_caaz_pair_fault_code;
    sar20_digital_core #(.P_RECON_STAGES(P_RECON_STAGES),.P_CAL_OBSERVE(P_CAL_OBSERVE),
                        .P_CAAZ_ENABLE(1'b0)) u_sar (
        .clk(clk),.rst_n(rst_n),.source_control_valid(source_control_valid),
        .source_control_word(source_control_word),.source_control_state(source_control_state),
        .source_control_ready(source_control_ready),.acquisition_control_valid(acquisition_control_valid),
        .acquisition_control_word(acquisition_control_word),.acquisition_control_state(acquisition_control_state),
        .acquisition_sadc_shift_q(acquisition_sadc_shift_q),.acquisition_rdac_shift_q(acquisition_rdac_shift_q),
        .flash_therm(flash_therm),.flash_valid(flash_valid),.coarse_cmp_valid(coarse_cmp_valid),
        .coarse_cmp_ge(coarse_cmp_ge),.fine_cmp_valid(fine_cmp_valid),.fine_cmp_ge(fine_cmp_ge),
        .analog_phase(analog_phase),.quiet_sample(quiet_sample),.tp_clock(tp_clock),
        .ra_az(ra_az),.ra_amplify(ra_amplify),.ref_precharge(ref_precharge),.ref_accurate(ref_accurate),
        .acquiring_mask(acquiring_mask),.converting_mask(converting_mask),
        .aux_charge_enable(aux_charge_enable),.hold_low_enable(hold_low_enable),
        .coarse_compare_enable(coarse_compare_enable),.coarse_acquire_enable(coarse_acquire_enable),
        .flash_acquire_enable(flash_acquire_enable),.fine_acquire_enable(fine_acquire_enable),
        .coarse_trial(coarse_trial),.fine_trial(fine_trial),.fine_compare_enable(fine_compare_enable),
        .flash_sample(flash_sample),.cfg_wr(cfg_wr),.cfg_addr(cfg_addr),.cfg_wdata(cfg_wdata),
        .cfg_rdata(cfg_rdata),.cfg_validate(cfg_validate),.cfg_clear_valid(cfg_clear_valid),.cfg_ready(cfg_ready),
        .ra_sat(ra_sat),.rdac_ovf(rdac_ovf),.adc2_over(adc2_over),.slice_sel(slice_sel),
        .main_sw(main_sw),.sub_sw(sub_sw),.sw_valid(sw_valid),.dout(sar_dout),.dout_valid(sar_dout_valid),
        .clip_low(sar_clip_low),.clip_high(sar_clip_high),.analog_ovf(sar_analog_ovf),.acc_ovf(sar_acc_ovf),
        .status_word(sar_status_word),.dout_sample_id(sar_sample_id),.dout_flags(sar_flags),
        .cal_obs_valid(cal_obs_valid),.cal_obs_data(cal_obs_data),.dout_analog_bad(sar_result_analog_bad),
        .conversion_fault(sar_conversion_fault),
        // 不提供肯定的 CAAZ 捕获确认；调用方尚未建立完整捕获/配置代次接口。
        .caaz_az_capture_ok(1'b0),.caaz_input_chop_neg(unused_caaz_input_chop_neg),
        .caaz_az_capture_en(unused_caaz_az_capture_en),.caaz_az_hold(unused_caaz_az_hold),
        .cal_obs_caaz_data(unused_cal_obs_caaz_data),.caaz_raw_valid(unused_caaz_raw_valid),
        .caaz_raw_data(unused_caaz_raw_data),.caaz_pair_difference(unused_caaz_pair_difference),
        .caaz_pair_difference_valid(unused_caaz_pair_difference_valid),
        .caaz_pair_first_sample_id(unused_caaz_pair_first_sample_id),.caaz_pair_id(unused_caaz_pair_id),
        .caaz_pair_fault(unused_caaz_pair_fault),.caaz_pair_fault_code(unused_caaz_pair_fault_code)
    );
    sar20_ad4080_top #(.FIFO_DEPTH(DSP_FIFO_DEPTH),.P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),
        .K_GAIN(K_GAIN),.SHIFT(SHIFT),.P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),
        .P_COMP_COEFF_VALID(P_COMP_COEFF_VALID),.C0(C0),.C1(C1),.C2(C2),
        .P_MULTIBYTE_ORDER(P_MULTIBYTE_ORDER),.P_OFFSET_LSB_ADDR(P_OFFSET_LSB_ADDR),
        .P_PRODUCT_ID_L(P_PRODUCT_ID_L),.P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),
        .P_WRITE_SDO_DRIVE(P_WRITE_SDO_DRIVE)) u_dsp (
        .clk(clk),.rst_n(rst_n),.sar_epoch_clear(cfg_clear_valid),.dsp_restart(dsp_restart),
        .sar_dout(sar_dout),.sar_dout_valid(sar_dout_valid),.sar_sample_id(sar_sample_id),
        .sar_flags(sar_flags),.sar_analog_ovf(sar_analog_ovf),
        .sar_conversion_fault(sar_conversion_fault),
        .sample_accepted(sample_accepted),.accepted_sample_id(accepted_sample_id),
        .sample_rejected(sample_rejected),.rejected_sample_id(rejected_sample_id),
        .rejected_flags(rejected_flags),.rejected_after_fault(rejected_after_fault),.stream_fault(stream_fault),
        .cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),
        .adc_idle(adc_idle),.interface_ready(interface_ready),.filter_sync(filter_sync),
        .ext_event(ext_event),.arm(arm),.rd_en(rd_en),.gpio_i(gpio_i),.gpio_o(gpio_o),.gpio_oe(gpio_oe),
        .dout(dsp_dout),.dout_valid(dsp_dout_valid),.status(dsp_status),
        .correction_clip_low(correction_clip_low),.correction_clip_high(correction_clip_high),
        .cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason),.processing_reject(processing_reject)
    );
endmodule
`default_nettype wire
