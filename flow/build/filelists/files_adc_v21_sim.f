// v21 仿真清单（含测试台、行为宏与金标；不用于生产综合）
-f flow/build/filelists/files_adc_v21.f

// 节点模型：模拟宏的替身，只在仿真侧
rtl/v21/adc2_node_macro.sv

tb/v21/tb_i1_two_sample.sv
tb/v21/tb_i2_events.sv
tb/v21/tb_i12_full.sv
tb/v21/tb_i3_full.sv
tb/v21/tb_i4b_e2e.sv
tb/v21/tb_i4_recon.sv
