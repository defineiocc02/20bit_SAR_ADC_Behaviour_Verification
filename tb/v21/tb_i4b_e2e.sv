`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i4b_e2e.sv
// 模块名     : tb_i4b_e2e
// 版本       : v3.0
// 功能       : I4b 端到端（RTL 侧绑定层）—— D1–D6 全链：两笔采样 → 逐事件
//              开关 → ADC2 节点模型返回 → 20 位定点重构，逐位对拍
//              A=0xFFF00、B=0x80000。
// 设计说明   :
//   * **本文件现在只做绑定**：激励、断言、轨迹、计数全部在
//       tb/v21/tb_e2e_decl.svh + tb/v21/tb_e2e_body.svh
//     里，门级侧 tb/v21/tb_gate_auto.sv（由 tools/gen_gate_tb.py 生成）
//     include **同一份**。这样"两侧激励逐字一致"是结构保证，
//     不是靠人肉同步——旧版是两份 TB 各抄一遍，抄错就会得到
//     "轨迹一致但两侧都没走完整链"的假结论。
//
//   * 本文件保留的差异仅三处：
//       ① DUT 端口名（RTL 用 prepare_ready / acq_sel … 与网表同名或近名）
//       ② 权重表是**数组**（w_main_tbl[18][16] / w_sub_tbl[18][4]）
//          —— 门级侧是 11520/2880 位扁平向量
//       ③ 层级探针 probe_acq_sampled / probe_conv_tag 直接读 DUT 内部
//          —— 门级侧 DC 可能改名，由共享层的 E2E_PROBE_GATE 宏切换
//
//   * 端口连接说明：共享层的输出声明为 wire（门级网表输出只能由网表驱动），
//     RTL 的 output logic 端口连 wire 合法，两侧因此可以共用同一套声明。
//
// 变更记录   :
//   2026-11-15  v1.0 只测 DUT 内宏的 raw 数值。
//   2026-11-15  v2.0 宏移到仿真边界，补 arm/登记分离、真实握手、sched18 生产路径。
//   2026-11-16  v3.0 拆成"公共激励/断言（.svh）+ 绑定层"，与门级 TB 共用激励。
// =============================================================================
module tb_i4b_e2e;

    // ---- 公共声明 + 公共激励体（与门级共用同一份文件）----
    `include "tb/v21/tb_e2e_decl.svh"

    // ---------------------------------------------------------------- DUT（RTL）
    // 端口名与网表完全同名；只有权重表是数组形态。
    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_U(prepare_U), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid_i), .ev_bit_i(ev_bit_i),
        .ev_bit_index_i(ev_bit_index_i), .ev_event_index_i(ev_event_index_i),
        .ev_qid_i(ev_qid_i),
        .sched_ext_sel(sched_ext_sel),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(acq_sel_o), .conv_sel_o(conv_sel_o), .idle_sel_o(idle_sel_o),
        .rdac_pre_state_o(rdac_pre_state_o), .pre_wr_o(pre_wr_o),
        .rdac_post_state_o(rdac_post_state_o), .post_wr_o(post_wr_o),
        .adc2_valid_i(adc2_valid_i), .adc2_ready_o(adc2_ready_o),
        .adc2_raw_i(adc2_raw_i), .adc2_range_err_i(adc2_range_err_i),
        .adc2_capture_arm_o(adc2_capture_arm_o),
        .adc2_rsp_valid_o(adc2_rsp_valid_o), .adc2_rsp_tag_o(adc2_rsp_tag_o),
        .adc2_rsp_qid_o(adc2_rsp_qid_o), .adc2_rsp_raw_o(adc2_rsp_raw_o),
        .adc2_rsp_range_o(adc2_rsp_range_o), .adc2_late_err_o(adc2_late_err_o),
        .adc2_release_ok_o(adc2_release_ok_o),
        .node_post_o(node_post_o), .node_pre_o(node_pre_o), .node_ids_o(node_ids_o),
        .node_qid_o(node_qid_o), .node_tag_o(node_tag_o), .node_valid_o(node_valid_o),
        .node_h_o(node_h_o), .node_v_o(node_v_o),
        .w_main_tbl(w_main_tbl), .w_sub_tbl(w_sub_tbl),
        .cfg_epoch(cfg_epoch), .b_q24_cfg(b_q24_cfg),
        .out_valid(out_valid), .out_code(out_code), .out_sat(out_sat),
        .out_err(out_err), .out_tag(out_tag),
        .apply_fire_o(apply_fire_o), .k_o(k_o), .k_bad_o(k_bad_o),
        .final_applied_o(final_applied_o), .final_k_o(final_k_o),
        .err_o(err_o), .done_o(done_o), .tag_o(tag_o), .qid_o(qid_o),
        .post_slot_o(post_slot_o));

    // ---------------------------------------------------------------- 层级探针（RTL 直读）
    // acq_sampled / conv_tag 是 RTL 顶层内部信号（非端口）。
    // 门级网表不保证同名，故门级绑定层不用本分支。
    wire probe_acq_sampled = dut.acq_sampled;
    wire [31:0] probe_conv_tag = dut.conv_tag;

    // ---------------------------------------------------------------- 公共激励与断言
    `include "tb/v21/tb_e2e_body.svh"

endmodule
