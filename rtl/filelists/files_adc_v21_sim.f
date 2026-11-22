// v21 仿真清单（含测试台、行为宏与金标；不用于生产综合）
-f rtl/filelists/files_adc_v21.f

// 节点模型：模拟宏的替身，只在仿真侧
rtl/sar_core/adc2_node_macro.sv

// 三项审计反例的正式回归（对应 isscc9p8_audit_20261115）
tb/v21/tb_audit3.sv

tb/v21/tb_i1_two_sample.sv
tb/v21/tb_i2_events.sv
tb/v21/tb_i12_full.sv
tb/v21/tb_i3_full.sv
tb/v21/tb_i4b_e2e.sv
tb/v21/tb_i4_recon.sv
