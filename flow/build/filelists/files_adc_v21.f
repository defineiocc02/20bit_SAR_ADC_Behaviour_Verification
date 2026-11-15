// ISSCC 2024 §9.8 数字 IP 完整顶层（方案 v1.4 §11.1 建议路径）
// 叶子直接引用原路径，v21 层只放新的集成逻辑。
//
// 生产综合清单只含数字逻辑；行为宏与向量留在仿真环境（另建 *_sim.f）。

// ---- D3/D4 叶子（复用，已验证）----
rtl/dem_phase_rr.sv
rtl/dither_pre.sv
rtl/dem_mask_v3.sv

// ---- D1 片组调度（复用）----
rtl/sched18.sv

// ---- D2 follower（复用，I2 迁移协议）----
rtl/follower_rt.sv

// ---- v21 集成层 ----
rtl/v21/acq_conv_ctx.sv
