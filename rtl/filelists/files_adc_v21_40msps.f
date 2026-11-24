// =============================================================================
// 文件名     : files_adc_v21_40msps.f
// 功能       : 双时钟域 40 MSPS（40 MHz 主帧 + 自定时事件域）生产 RTL 综合清单。
// 设计说明   : 复用全部已验证叶子模块，顶层替换为 adc_digital_ip_v21_40msps。
// =============================================================================

// ---- 复用已验证核心叶子 ----
rtl/sar_core/dither_pre.sv
rtl/sar_core/dem_mask_v3.sv
rtl/sar_core/sched18.sv
rtl/sar_core/sar_evt_v21.sv
rtl/sar_core/adc2_capture.sv
rtl/sar_core/wsum_v21.sv
rtl/sar_core/recip_q60.sv
rtl/sar_core/recip_rnd_v21.sv
rtl/sar_core/recon_v21.sv

// ---- 双域 40 MSPS 顶层 ----
rtl/sar_core/adc_digital_ip_v21_40msps.sv
