`timescale 1ns/1ps
// 来源：独立审查 isscc9p8_source_crosscheck_20261116/analog_interface_v21/
//       tb_analog_interface_v21.sv（原样入库，仅改模块名）
// 它复现并验收两项控制错误：
//   ① 宏选通 acq_sel_o/conv_sel_o 必须等于本笔冻结片号
//   ② release/promote 同沿时 B 必须拿到 ADC2 arm


// =============================================================================
// 文件名     : tb_analog_interface_v21.sv
// 模块名     : tb_analog_interface_v21
// 版本       : v1.0
// 功能       : 在固定 Git 提交上核对物理片组端口和连续两笔 ADC2 捕获授权。
//
// 设计说明   :
//   * 片组由 DUT 内部 sched18 产生。激励送入已决码和采样资格，
//     因此测试只检查数字归属，不模拟比较器、电容或参考建立。
//   * 第一组覆盖 A 释放同沿晋升 B；第二组只增加 CONV 空闲拍。
//     两组都先回送并消费 A 的后级字，以排除 owner 槽占用导致的阻塞。
//   * 发现接口不一致时累积失败，保留两组观测后统一返回非零。
// -----------------------------------------------------------------------------
// 变更记录   : 初始版本。
// =============================================================================
module tb_analog_if;
    logic clk = 0;
    always #5 clk = ~clk;

    // =========================================================================
    // 1. 驱动与观测
    // =========================================================================
    // --- 事务输入 ---
    logic rst_n = 0, prepare_valid = 0, prepare_ready;
    logic [31:0] prepare_tag = 0;      // 32 位事务标识，与片组 ID 分开追踪。
    logic prepare_qid = 0;             // A 用量化器 0，B 用量化器 1。
    logic sample_event = 0, input_captured = 0, residue_captured = 0, hold_ack = 0;
    logic ev_valid = 0, ev_qid = 0;
    logic [3:0] ev_bit_idx = 0, ev_idx = 0;
    logic adc2_valid = 0, adc2_ready, adc2_arm, rsp_valid;
    logic [31:0] rsp_tag;
    logic [17:0] acq_sel, conv_sel, pre_wr;
    logic final_applied, apply_fire, err;
    logic [9:0] final_k;
    logic [31:0] event_tag;
    logic [39:0] w_main [0:17][0:15];  // Q30 真实告知权重，测试用均匀值。
    logic [39:0] w_sub [0:17][0:3];    // Q30 子阵列权重，与原定向夹具一致。
    int failures = 0, checks = 0;

    // 选内部调度。Flash=4、无 dither，后续显式给六个拒绝位。
    // 这样可完全独立地判断最终码 256，排除错误事件碰巧阻止 arm 的情形。
    adc_digital_ip_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(6'd0),
        .prepare_h(3'd0), .prepare_v(3'd0), .prepare_half(1'b0),
        .prepare_coin(1'b0), .prepare_U(3'd0), .prepare_flash(3'd4),
        .prepare_qid(prepare_qid), .stop_i(1'b0),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_start_i(1'b0), .ev_bit_valid_i(ev_valid), .ev_bit_i(1'b0),
        .ev_bit_index_i(ev_bit_idx), .ev_event_index_i(ev_idx), .ev_qid_i(ev_qid),
        .ev_flash_valid_i(1'b0), .ev_flash_code_i(3'd0),
        .sched_ext_sel(1'b0), .sched_acq(18'd0), .sched_conv(18'd0),
        .sched_idle(18'd0), .sched_ids(40'd0), .sched_seed_we(1'b0), .sched_seed(32'd0),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .pre_wr_o(pre_wr),
        .adc2_valid_i(adc2_valid), .adc2_ready_o(adc2_ready), .adc2_raw_i(13'd2560),
        .adc2_range_err_i(1'b0), .adc2_capture_arm_o(adc2_arm),
        .adc2_rsp_valid_o(rsp_valid), .adc2_rsp_tag_o(rsp_tag),
        // 权重表已改为 wsum 内部寄存器堆 + 窄配置口。
        // 本测试只验接口时序，不做权重扫描，所以配置口保持静默
        // （权重全 0，只影响 G/Wp/Wm 数值，不影响本测试检查的选通与 arm）。
        .w_cfg_we(1'b0), .w_cfg_sel(1'b0), .w_cfg_sid(5'd0),
        .w_cfg_idx(5'd0), .w_cfg_data(40'd0),
        .cfg_epoch(8'd1), .b_q24_cfg(27'sd0),
        .apply_fire_o(apply_fire), .final_applied_o(final_applied),
        .final_k_o(final_k), .err_o(err), .tag_o(event_tag));

    // =========================================================================
    // 2. 独立判据与正沿驱动
    // =========================================================================
    function automatic logic [17:0] ids_mask(input logic [39:0] ids);
        logic [17:0] mask;
        mask = '0;
        for (int i = 0; i < 8; i++) mask[ids[5*i+:5]] = 1'b1;
        return mask;
    endfunction

    task automatic require(input bit pass, input string label);
        checks++;
        if (pass) $display("PASS %s", label);
        else begin
            failures++;
            $display("FAIL %s", label);
        end
    endtask

    // 所有输入从负沿驱动。采集沿前后分别等待仿真稳定，
    // 以免将非阻塞寄存器更新误读成片组或握手异常。
    task automatic prepare(input logic [31:0] tag, input logic qid);
        @(negedge clk);
        prepare_valid = 1;
        prepare_tag = tag;
        prepare_qid = qid;
        #1;
        require(prepare_ready === 1'b1, $sformatf("prepare ready tag=%08h", tag));
        @(posedge clk); #1;
        @(negedge clk); prepare_valid = 0;
    endtask

    task automatic sample(input bit capture_residue);
        @(negedge clk);
        sample_event = 1;
        input_captured = 1;
        residue_captured = capture_residue;
        hold_ack = capture_residue;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 0;
        input_captured = 0;
        residue_captured = 0;
        hold_ack = 0;
    endtask

    task automatic events(input logic qid, input logic [31:0] tag);
        // 序号 1 提交预装 Flash，序号 2..7 各拒绝一个低位，均需应用。
        for (int i = 1; i <= 7; i++) begin
            @(negedge clk);
            ev_valid = 1;
            ev_qid = qid;
            ev_idx = 4'(i);
            ev_bit_idx = i == 1 ? 4'd0 : 4'(7-i);
            #1;
            require(apply_fire === 1'b1, $sformatf("apply tag=%08h evt=%0d", tag, i));
            @(posedge clk); #1;
        end
        @(negedge clk); ev_valid = 0;
        require(final_applied && final_k == 10'd256 && event_tag == tag && !err,
                $sformatf("known final code and tag=%08h", tag));
        repeat (3) @(negedge clk);
    endtask

    // =========================================================================
    // 3. 同沿交接反例与对照
    // =========================================================================
    task automatic run_case(input bit same_edge);
        @(negedge clk); rst_n = 0;
        repeat (2) @(negedge clk);
        rst_n = 1;
        prepare(32'hA1, 1'b0);

        // A 刚分配尚未采样，预置和真实采集使能必须指向同一批片。
        // 片号序来自冻结的描述符，不把调度器的未来计划当成预期值。
        if (same_edge) begin
            $display("ROLE_A acq=%05h pre_wr=%05h frozen_acq=%05h conv=%05h conv_valid=%0b",
                     acq_sel, pre_wr, ids_mask(dut.acq_ids), conv_sel, dut.conv_valid);
            require(pre_wr == ids_mask(dut.acq_ids) && pre_wr == 18'h000FF,
                    "A pre writes the initial frozen group");
            require(acq_sel == pre_wr, "A macro ACQ select matches pre group");
            require(conv_sel == 18'd0, "A no macro CONV select before the first capture");
        end

        sample(0);
        prepare(32'hB2, 1'b1);
        if (same_edge) begin
            $display("ROLE_AB acq=%05h pre_wr=%05h frozen_acq=%05h conv=%05h frozen_conv=%05h",
                     acq_sel, pre_wr, ids_mask(dut.acq_ids), conv_sel, ids_mask(dut.conv_ids));
            require(acq_sel == ids_mask(dut.acq_ids) && conv_sel == ids_mask(dut.conv_ids),
                    "A/B macro role selects match frozen descriptor groups");
        end

        events(1'b0, 32'hA1);
        require(adc2_arm === 1'b1, "A is armed before common capture");
        sample(1);

        // 一拍只返回一次 A 的 raw，随后等待 quiet 计满。
        // B 的 arm 观察时 owner 槽必为空，确认旧笔没有背压新笔。
        adc2_valid = 1;
        #1;
        require(adc2_ready && rsp_valid && rsp_tag == 32'hA1, "A owner response accepted once");
        @(posedge clk); #1;
        @(negedge clk); adc2_valid = 0;
        @(posedge clk); #1;
        @(negedge clk);
        require(dut.release_fire === 1'b1, "A captured/hold/quiet release eligible");
        if (same_edge) begin
            prepare_valid = 1;
            prepare_tag = 32'hC3;
            prepare_qid = 0;
            #1;
            require(dut.release_fire && dut.commit_fire && dut.conv_valid && prepare_ready,
                    "A release and B promotion share the same accepted edge");
            @(posedge clk); #1;
            require(dut.conv_valid && dut.conv_tag == 32'hB2, "B occupies CONV immediately");
            @(negedge clk); prepare_valid = 0;
        end else begin
            // 仅插入一次可见空闲，检验上一组失败是否来自 owner 代际。
            @(posedge clk); #1;
            require(!dut.conv_valid, "control CONV has an idle edge");
            @(posedge clk); #1;
            prepare(32'hC3, 1'b0);
        end

        events(1'b1, 32'hB2);
        $display("ARM_B same_edge=%0b tag=%08h conv_valid=%0b final=%0b wsum_valid=%0b owner_busy=%0b seen=%0b arm=%0b",
                 same_edge, dut.conv_tag, dut.conv_valid, final_applied, dut.wsum_valid,
                 dut.adc2_owner_busy, dut.adc2_arm_seen_q, adc2_arm);
        require(dut.conv_valid && final_applied && dut.wsum_valid && !dut.adc2_owner_busy,
                "B has a complete valid conversion and a free ADC2 owner");
        require(adc2_arm === 1'b1,
                $sformatf("B is armed before its first common capture same_edge=%0b", same_edge));
    endtask

    initial begin
        for (int s = 0; s < 18; s++) begin
            for (int j = 0; j < 16; j++) w_main[s][j] = 40'd268435456;
            w_sub[s][0] = 40'd33554432;
            w_sub[s][1] = 40'd16777216;
            w_sub[s][2] = 40'd8388608;
            w_sub[s][3] = 40'd8388608;
        end
        run_case(1);
        run_case(0);
        $display("SUMMARY checks=%0d failures=%0d", checks, failures);
        if (failures != 0) $fatal(1, "interface contract violated");
        $finish;
    end
endmodule
