`timescale 1ns/1ps
// 来源：独立审查 isscc9p8_source_crosscheck_20261116/analog_interface_v21/
//       tb_analog_interface_v21.sv；另覆盖 issue 10 的沿前、沿后与排空。
// 它复现并验收两项控制错误：
//   ① 宏选通 acq_sel_o/conv_sel_o 必须等于本笔冻结片号
//   ② release/promote 同沿时 B 必须拿到 ADC2 arm


// =============================================================================
// 文件名     : tb_analog_if.sv
// 模块名     : tb_analog_if
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

    // ---------------- 权重配置口（CONFIG_CONTRACT v1.0）----------------
    logic        t_cfg_we = 1'b0, t_cfg_sel = 1'b0;
    logic [4:0]  t_cfg_sid = 5'd0, t_cfg_idx = 5'd0;
    logic [39:0] t_cfg_data = 40'd0;
    logic        t_cfg_commit = 1'b0, t_cfg_reload = 1'b0;
    wire         t_cfg_valid, t_cfg_busy, t_cfg_range_err;

    task automatic t_wt_write(input logic sel, input logic [4:0] sid,
                              input logic [4:0] idx, input logic [39:0] d);
        begin
            @(negedge clk);
            t_cfg_we = 1'b1; t_cfg_sel = sel; t_cfg_sid = sid;
            t_cfg_idx = idx; t_cfg_data = d;
            @(posedge clk); @(negedge clk);
            t_cfg_we = 1'b0;
        end
    endtask

    task automatic t_wt_install();
        begin
            @(negedge clk); t_cfg_reload = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_reload = 1'b0;
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++)
                    t_wt_write(1'b0, 5'(s), 5'(j), 40'd268435456);
                t_wt_write(1'b1, 5'(s), 5'd0, 40'd33554432);
                t_wt_write(1'b1, 5'(s), 5'd1, 40'd16777216);
                t_wt_write(1'b1, 5'(s), 5'd2, 40'd8388608);
                t_wt_write(1'b1, 5'(s), 5'd3, 40'd8388608);
            end
            @(negedge clk); t_cfg_commit = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_commit = 1'b0;
            @(posedge clk); @(negedge clk);
        end
    endtask

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
    logic stop_i = 0;
    logic [5:0] prepare_word = 0;
    logic [17:0] acq_sel, conv_sel, idle_sel, pre_wr;
    logic [71:0] pre_state;
    // 默认片池前三笔的物理片号，A 与 C 重合七片。
    localparam logic [17:0] GROUP_A = 18'h000FF, GROUP_B = 18'h3BD00, GROUP_C = 18'h040FE;
    logic final_applied, apply_fire, err;
    logic [9:0] final_k;
    logic [31:0] event_tag;
    int failures = 0, checks = 0;

    // 选内部调度。三笔预置不同，Flash=4，所有低位判决均拒绝。
    // A、B 的高段为零，C 的高段为四，对应最终命令 256、256、260。
    adc_digital_ip_v21 dut (
        .alpha_q30_cfg(32'd1073741824), // 名义增益与冻结向量一致。
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .dither_force_i(1'b1), .sadc_pre_state_o(), .sadc_pre_wr_o(),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(3'd0), .prepare_v(3'd0), .prepare_half(1'b0),
        .prepare_coin(1'b0), .prepare_U({prepare_word[5], 1'b0, prepare_word[3]}), .prepare_flash(3'd4),
        .prepare_qid(prepare_qid), .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_start_i(1'b0), .ev_bit_valid_i(ev_valid), .ev_bit_i(1'b0),
        .ev_bit_index_i(ev_bit_idx), .ev_event_index_i(ev_idx), .ev_qid_i(ev_qid),
        .ev_flash_valid_i(1'b0), .ev_flash_code_i(3'd0),
        .sched_ext_sel(1'b0), .sched_acq(18'd0), .sched_conv(18'd0),
        .sched_idle(18'd0), .sched_ids(40'd0), .sched_seed_we(1'b0), .sched_seed(32'd0),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .pre_wr_o(pre_wr), .rdac_pre_state_o(pre_state),
        .adc2_valid_i(adc2_valid), .adc2_ready_o(adc2_ready), .adc2_raw_i(13'd2560),
        .adc2_range_err_i(1'b0), .adc2_capture_arm_o(adc2_arm),
        .adc2_rsp_valid_o(rsp_valid), .adc2_rsp_tag_o(rsp_tag),
        // 权重表已改为 wsum 内部寄存器堆 + 窄配置口。
        // 每次复位后装入名义表并提交，准备请求才能通过配置有效门。
        .w_cfg_we(t_cfg_we), .w_cfg_sel(t_cfg_sel),
        .w_cfg_sid(t_cfg_sid), .w_cfg_idx(t_cfg_idx), .w_cfg_data(t_cfg_data),
        .w_cfg_commit(t_cfg_commit), .w_cfg_reload(t_cfg_reload),
        .w_cfg_valid(t_cfg_valid), .w_cfg_busy(t_cfg_busy),
        .w_cfg_range_err(t_cfg_range_err),
        .cfg_epoch(8'd1), .b_q24_cfg(27'sd0),
        // 其余顶层观测口在本用例中明确闲置。
        .rdac_post_state_o(), .post_wr_o(), .node_post_o(), .node_pre_o(),
        .node_ids_o(), .node_qid_o(), .node_tag_o(), .node_valid_o(),
        .node_h_o(), .node_v_o(), .adc2_rsp_qid_o(), .adc2_rsp_raw_o(),
        .adc2_rsp_range_o(), .adc2_late_err_o(), .adc2_release_ok_o(), .out_valid(),
        .out_code(), .out_sat(), .out_err(), .out_tag(),
        .k_o(), .k_bad_o(), .done_o(), .qid_o(),
        .post_slot_o(),
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
    task automatic check_route(input string label, input logic [17:0] want_acq,
                               input logic [17:0] want_conv, input logic [17:0] want_wr,
                               input logic [3:0] nibble);
        logic [71:0] want_data;
        want_data = '0;
        for (int sid = 0; sid < 18; sid++)
            if (want_wr[sid]) want_data[4*sid +: 4] = nibble;
        $display("ROUTE %s acq=%05h conv=%05h pre_wr=%05h overlap=%05h",
                 label, acq_sel, conv_sel, pre_wr, acq_sel & conv_sel);
        require(acq_sel === want_acq && conv_sel === want_conv, {label, " ownership"});
        require((acq_sel & conv_sel) === 18'd0 && idle_sel === ~(want_acq | want_conv),
                {label, " disjoint partition"});
        require(pre_wr === want_wr && (pre_wr & conv_sel) === 18'd0, {label, " write gate"});
        require(pre_state === want_data, {label, " physical preset data"});
    endtask

    task automatic prepare(input logic [31:0] tag, input logic qid, input logic [5:0] word_in,
                           input logic [17:0] group_mask, input logic [3:0] nibble);
        @(negedge clk);
        prepare_valid = 1;
        prepare_tag = tag;
        prepare_qid = qid;
        prepare_word = word_in;
        #1;
        // 此任务只在 CONV 空闲时调用，沿前允许给新采集组提前预置。
        check_route("early_preset", group_mask, '0, group_mask, nibble);
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

    task automatic events(input logic qid, input logic [31:0] tag, input logic [9:0] expected_k);
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
        require(final_applied && final_k == expected_k && event_tag == tag && !err,
                $sformatf("known final code and tag=%08h", tag));
        repeat (3) @(negedge clk);
    endtask

    // =========================================================================
    // 3. 同沿交接反例与对照
    // =========================================================================
    task automatic run_case(input bit same_edge);
        @(negedge clk); rst_n = 0; stop_i = 0; prepare_valid = 0;
        repeat (2) @(negedge clk);
        rst_n = 1;
        t_wt_install();   // 装表 + commit，否则 cfg_valid=0 门禁挡住交易
        check_route("cold", GROUP_A, '0, '0, '0);
        prepare(32'hA1, 1'b0, 6'd0, GROUP_A, 4'h0);

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
        prepare(32'hB2, 1'b1, 6'd16, GROUP_B, 4'h4);
        if (same_edge) begin
            $display("ROLE_AB acq=%05h pre_wr=%05h frozen_acq=%05h conv=%05h frozen_conv=%05h",
                     acq_sel, pre_wr, ids_mask(dut.acq_ids), conv_sel, ids_mask(dut.conv_ids));
            require(acq_sel == ids_mask(dut.acq_ids) && conv_sel == ids_mask(dut.conv_ids),
                    "A/B macro role selects match frozen descriptor groups");
        end

        // A 仍占 CONV，B 尚未采样。候选 C 的字和 qid 不能影响物理引脚。
        @(negedge clk); prepare_valid = 1; prepare_word = 6'd48; prepare_qid = 0;
        #1;
        require(!prepare_ready, "blocked C before B samples");
        check_route("blocked", GROUP_B, GROUP_A, GROUP_B, 4'h4);
        @(negedge clk); prepare_valid = 0;
        events(1'b0, 32'hA1, 10'd256);
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
            prepare_word = 6'd48;
            prepare_qid = 0;
            #1;
            require(dut.release_fire && dut.commit_fire && dut.conv_valid && prepare_ready,
                    "A release and B promotion share the same accepted edge");
            check_route("handover_before", GROUP_B, GROUP_A, GROUP_B, 4'h4);
            @(posedge clk); #1;
            check_route("handover_after", GROUP_C, GROUP_B, GROUP_C, 4'hC);
            require(dut.conv_valid && dut.conv_tag == 32'hB2, "B occupies CONV immediately");
            @(negedge clk); prepare_valid = 0;
        end else begin
            // 仅插入一次可见空闲，检验上一组失败是否来自 owner 代际。
            @(posedge clk); #1;
            require(!dut.conv_valid, "control CONV has an idle edge");
            @(posedge clk); #1;
            prepare(32'hC3, 1'b0, 6'd48, GROUP_C, 4'hC);
        end

        events(1'b1, 32'hB2, 10'd256);
        $display("ARM_B same_edge=%0b tag=%08h conv_valid=%0b final=%0b wsum_valid=%0b owner_busy=%0b seen=%0b arm=%0b",
                 same_edge, dut.conv_tag, dut.conv_valid, final_applied, dut.wsum_valid,
                 dut.adc2_owner_busy, dut.adc2_arm_seen_q, adc2_arm);
        require(dut.conv_valid && final_applied && dut.wsum_valid && !dut.adc2_owner_busy,
                "B has a complete valid conversion and a free ADC2 owner");
        require(adc2_arm === 1'b1,
                $sformatf("B is armed before its first common capture same_edge=%0b", same_edge));
        if (same_edge) begin
            // C 晋升后 ACQ 无归属。排空后撤销 stop，也须等待真实请求才重新选通。
            @(negedge clk); stop_i = 1;
            sample(1);
            adc2_valid = 1; #1;
            require(adc2_ready && rsp_valid && rsp_tag == 32'hB2, "drain returns B");
            @(posedge clk); #1; @(negedge clk); adc2_valid = 0;
            repeat (3) @(posedge clk); #1;
            require(dut.conv_valid && dut.conv_tag == 32'hC3 && !dut.acq_valid,
                    "drain promotes C without a new acquisition");
            check_route("only_conv", '0, GROUP_C, '0, '0);
            events(1'b0, 32'hC3, 10'd260);
            require(adc2_arm, "drained C is armed");
            sample(1);
            adc2_valid = 1; #1;
            require(adc2_ready && rsp_valid && rsp_tag == 32'hC3, "drain returns C");
            @(posedge clk); #1; @(negedge clk); adc2_valid = 0;
            repeat (3) @(posedge clk); #1;
            check_route("drained", '0, '0, '0, '0);
            @(negedge clk); stop_i = 0; #1;
            check_route("resume_idle", '0, '0, '0, '0);
        end
    endtask

    initial begin
        run_case(1);
        run_case(0);
        $display("SUMMARY checks=%0d failures=%0d", checks, failures);
        if (failures != 0) $fatal(1, "interface contract violated");
        $finish;
    end
endmodule
