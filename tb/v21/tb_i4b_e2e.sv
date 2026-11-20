`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i4b_e2e.sv
// 模块名     : tb_i4b_e2e
// 版本       : v3.0
// 功能       : RTL 端到端测试顶层。两笔采样 → 逐事件
//              开关 → ADC2 节点模型返回 → 20 位定点重构，逐位对拍
//              A=0xFFF00、B=0x80000。
// 设计说明   :
//   * 引入 tb_e2e_decl.svh 的信号、节点模型和辅助任务，再用
//     tb_e2e_body.svh 发起交易、验证输出和统计交易次数。
//   * 复位后通过 w_cfg_* 写入并提交权重，配置生效后才能受理采样请求。
//   * ADC2 节点模型根据 DUT 输出的快照计算 13 位原始码，通过握手返回。
//   * 采样沿的断言读取 DUT 内部的 acq_sampled，区分已采样和等待捕获。
//
// 变更记录   :
//   2026-11-15  v1.0 只测 DUT 内宏的 raw 数值。
//   2026-11-15  v2.0 宏移到仿真边界，补 arm/登记分离、真实握手、sched18 生产路径。
//   2026-11-16  v3.0 拆成"公共激励/断言（.svh）+ 绑定层"，与门级 TB 共用激励。
// =============================================================================
module tb_i4b_e2e;

    // ---- 测试信号与 ADC2 节点模型 ----
    `include "tb/v21/tb_e2e_decl.svh"

    // ---------------------------------------------------------------- DUT 与权重配置
    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .dither_force_i(1'b1), .sadc_pre_state_o(), .sadc_pre_wr_o(),
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
        .w_cfg_we(w_cfg_we), .w_cfg_sel(w_cfg_sel),
        .w_cfg_sid(w_cfg_sid), .w_cfg_idx(w_cfg_idx), .w_cfg_data(w_cfg_data),
        .w_cfg_commit(w_cfg_commit), .w_cfg_reload(w_cfg_reload),
        .w_cfg_valid(w_cfg_valid), .w_cfg_busy(w_cfg_busy),
        .w_cfg_range_err(w_cfg_range_err),
        .cfg_epoch(cfg_epoch), .b_q24_cfg(b_q24_cfg),
        .out_valid(out_valid), .out_code(out_code), .out_sat(out_sat),
        .out_err(out_err), .out_tag(out_tag),
        .apply_fire_o(apply_fire_o), .k_o(k_o), .k_bad_o(k_bad_o),
        .final_applied_o(final_applied_o), .final_k_o(final_k_o),
        .err_o(err_o), .done_o(done_o), .tag_o(tag_o), .qid_o(qid_o),
        .post_slot_o(post_slot_o));

    // ---------------------------------------------------------------- 层级探针（RTL 直读）
    // acq_sampled / conv_tag 是 RTL 顶层内部信号（非端口）。
    wire probe_acq_sampled = dut.acq_sampled;
    wire [31:0] probe_conv_tag = dut.conv_tag;

    // ---------------------------------------------------------------- 公共激励与断言
    `include "tb/v21/tb_e2e_body.svh"

endmodule
