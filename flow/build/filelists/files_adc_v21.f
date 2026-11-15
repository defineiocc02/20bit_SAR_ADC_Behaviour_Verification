// ISSCC 2024 §9.8 数字 IP 完整生产顶层（方案 v1.4 §11.1）
//
// 只含可综合数字逻辑。按方案目标 I3 第 2 条，"节点宏和模拟真值 Vin
// 放在仿真边界" —— adc2_node_macro.sv 不在本清单里（见 *_sim.f）。

// ---- D3/D4 叶子（复用，已验证）----
rtl/dem_phase_rr.sv
rtl/dither_pre.sv
rtl/dem_mask_v3.sv

// ---- D1 片组调度（复用）----
rtl/sched18.sv

// ---- D2 follower：旧协议版（保留，旧链 8509 笔依赖它）----
rtl/follower_rt.sv

// ---- v21 集成层 ----
rtl/v21/acq_conv_ctx.sv
rtl/v21/sar_evt_v21.sv
rtl/v21/adc2_capture.sv
rtl/v21/wsum_v21.sv
rtl/v21/recon_v21.sv
rtl/v21/adc_digital_ip_v21.sv
