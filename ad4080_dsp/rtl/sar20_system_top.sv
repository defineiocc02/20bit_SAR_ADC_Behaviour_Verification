// Optional project-level assembly: actual SAR digital core + standalone DSP.
// The analog macro still supplies synchronous comparator/overflow signals.
// Both instances share clk; reset release must be synchronized externally.
`timescale 1ns/1ps
`default_nettype none

module sar20_system_top #(
    parameter bit P_STRUCTURAL = 1,
    parameter int P_RECON_STAGES = 7,
    parameter int P_REF_ON = 10,
    parameter int P_RESIDUE_CAPTURE = 9,
    parameter logic [31:0] P_SHUFFLE_SEED = 32'h6d2b79f5,
    parameter int DSP_FIFO_DEPTH = 16384
) (
    input  logic clk, rst_n,
    // SAR analog/macro boundary (same fixed geometry as sar20_digital_core).
    input  logic [6:0] flash_therm,
    input  logic flash_valid,
    input  logic [1:0] coarse_cmp_valid, coarse_cmp_ge,
    input  logic fine_cmp_valid, fine_cmp_ge,
    output logic [3:0] analog_phase,
    output logic quiet_sample, tp_clock, ra_az, ra_amplify, ref_precharge, ref_accurate,
    output logic [17:0] acquiring_mask, converting_mask, aux_charge_enable, hold_low_enable,
    output logic [1:0] coarse_compare_enable, coarse_acquire_enable,
    output logic flash_acquire_enable, fine_acquire_enable,
    output logic [1:0][8:0] coarse_trial,
    output logic [1:0][7:0] quantizer_dither,
    output logic [3:0] acquisition_dither_rails,
    output logic [11:0] fine_trial,
    output logic fine_compare_enable, flash_sample,
    input  logic cfg_wr,
    input  logic [15:0] cfg_addr,
    input  logic [63:0] cfg_wdata,
    output logic [63:0] cfg_rdata,
    input  logic cfg_validate, cfg_clear_valid,
    output logic cfg_ready,
    input  logic [8:0] sadc_code,
    input  logic sadc_rdy,
    input  logic [11:0] adc2_code,
    input  logic adc2_rdy, ra_sat, rdac_ovf, adc2_over,
    input  logic signed [63:0] inj_q,
    output logic [17:0] slice_sel,
    output logic [17:0][62:0] main_sw,
    output logic [17:0][7:0] sub_sw,
    output logic [17:0][3:0] dither_sw,
    output logic sw_valid,
    // Raw SAR output remains separately observable for audit/debug.
    output logic [19:0] sar_dout,
    output logic sar_dout_valid, sar_clip_low, sar_clip_high, sar_analog_ovf, sar_acc_ovf,
    output logic [31:0] sar_status_word, sar_sample_id,
    output logic [4:0] sar_flags,
    // DSP input acceptance and rejection events; no output-side sample ID.
    input  logic dsp_restart,
    output logic sample_accepted,
    output logic [31:0] accepted_sample_id,
    output logic sample_rejected,
    output logic [31:0] rejected_sample_id,
    output logic [4:0] rejected_flags,
    output logic rejected_after_fault, stream_fault,
    input  logic cs_n, sclk, sdi,
    output logic sdo, sdo_oe,
    input  logic cnv_p, cnv_n,
    output logic cnv_pulse,
    input  logic filter_sync, ext_event, arm, rd_en, bit_en,
    output logic [1:0] ser_data, ser_frame,
    output logic dco,
    input  logic [3:0] gpio_i,
    output logic [3:0] gpio_o, gpio_oe,
    output logic [19:0] dsp_dout,
    output logic dsp_dout_valid,
    output logic [7:0] dsp_status,
    output logic correction_clip_low, correction_clip_high
);
    sar20_digital_core #(
        .P_STRUCTURAL(P_STRUCTURAL), .P_RECON_STAGES(P_RECON_STAGES),
        .P_REF_ON(P_REF_ON), .P_RESIDUE_CAPTURE(P_RESIDUE_CAPTURE),
        .P_SHUFFLE_SEED(P_SHUFFLE_SEED)
    ) u_sar (
        .clk(clk), .rst_n(rst_n), .flash_therm(flash_therm), .flash_valid(flash_valid),
        .coarse_cmp_valid(coarse_cmp_valid), .coarse_cmp_ge(coarse_cmp_ge),
        .fine_cmp_valid(fine_cmp_valid), .fine_cmp_ge(fine_cmp_ge),
        .analog_phase(analog_phase), .quiet_sample(quiet_sample), .tp_clock(tp_clock),
        .ra_az(ra_az), .ra_amplify(ra_amplify), .ref_precharge(ref_precharge),
        .ref_accurate(ref_accurate), .acquiring_mask(acquiring_mask),
        .converting_mask(converting_mask), .aux_charge_enable(aux_charge_enable),
        .hold_low_enable(hold_low_enable), .coarse_compare_enable(coarse_compare_enable),
        .coarse_acquire_enable(coarse_acquire_enable), .flash_acquire_enable(flash_acquire_enable),
        .fine_acquire_enable(fine_acquire_enable), .coarse_trial(coarse_trial),
        .quantizer_dither(quantizer_dither), .acquisition_dither_rails(acquisition_dither_rails),
        .fine_trial(fine_trial), .fine_compare_enable(fine_compare_enable), .flash_sample(flash_sample),
        .cfg_wr(cfg_wr), .cfg_addr(cfg_addr), .cfg_wdata(cfg_wdata), .cfg_rdata(cfg_rdata),
        .cfg_validate(cfg_validate), .cfg_clear_valid(cfg_clear_valid), .cfg_ready(cfg_ready),
        .sadc_code(sadc_code), .sadc_rdy(sadc_rdy), .adc2_code(adc2_code), .adc2_rdy(adc2_rdy),
        .ra_sat(ra_sat), .rdac_ovf(rdac_ovf), .adc2_over(adc2_over), .inj_q(inj_q),
        .slice_sel(slice_sel), .main_sw(main_sw), .sub_sw(sub_sw), .dither_sw(dither_sw),
        .sw_valid(sw_valid), .dout(sar_dout), .dout_valid(sar_dout_valid),
        .clip_low(sar_clip_low), .clip_high(sar_clip_high), .analog_ovf(sar_analog_ovf),
        .acc_ovf(sar_acc_ovf), .status_word(sar_status_word), .dout_sample_id(sar_sample_id),
        .dout_flags(sar_flags)
    );

    sar20_ad4080_top #(.FIFO_DEPTH(DSP_FIFO_DEPTH)) u_dsp (
        .clk(clk), .rst_n(rst_n), .sar_epoch_clear(cfg_clear_valid), .dsp_restart(dsp_restart),
        .sar_dout(sar_dout), .sar_dout_valid(sar_dout_valid), .sar_sample_id(sar_sample_id),
        .sar_flags(sar_flags), .sample_accepted(sample_accepted),
        .accepted_sample_id(accepted_sample_id), .sample_rejected(sample_rejected),
        .rejected_sample_id(rejected_sample_id), .rejected_flags(rejected_flags),
        .rejected_after_fault(rejected_after_fault), .stream_fault(stream_fault),
        .cs_n(cs_n), .sclk(sclk), .sdi(sdi), .sdo(sdo), .sdo_oe(sdo_oe),
        .cnv_p(cnv_p), .cnv_n(cnv_n), .cnv_pulse(cnv_pulse),
        .filter_sync(filter_sync), .ext_event(ext_event), .arm(arm), .rd_en(rd_en), .bit_en(bit_en),
        .ser_data(ser_data), .ser_frame(ser_frame), .dco(dco),
        .gpio_i(gpio_i), .gpio_o(gpio_o), .gpio_oe(gpio_oe),
        .dout(dsp_dout), .dout_valid(dsp_dout_valid), .status(dsp_status),
        .correction_clip_low(correction_clip_low), .correction_clip_high(correction_clip_high)
    );
endmodule

`default_nettype wire
