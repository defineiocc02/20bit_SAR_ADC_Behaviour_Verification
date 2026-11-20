// =============================================================================
// 文件名     : continuous_pin_tasks.svh
// 模块名     : tb_ad4080_peripheral_product 内的连续 LVDS 验收
// 版本       : v1.0
// 功能       : 通过实际配置 SPI、原数值流水和产品 DDR 装载口逐沿验收。
// 设计说明   :
//   * CNV 向上游的两个已译码入口使用同一事件。DCLK+ 对齐沿为时间零点，
//     核心走对齐沿之后的第一上升。下一 CNV 提交前次真实转换的原始码。
//   * 只驱动产品的公开输入。层级观察用于记录两拍校正和跨域交接的时刻。
//   * 理想 DDR 宏在 FALL 锁住两个半拍，随后 RISE 保持同一 pair 的归属。
//     这里的 DCO 是数字源，封装、差分幅度和传播时间由物理试验给出。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [图97/表49] 在同一产品入口验收滤波保持和 Manchester 双 lane。
// =============================================================================

    // =========================================================================
    // 1. 理想 DDR 加载夹具及逐边沿检查
    // =========================================================================
    logic [1:0] cont_fall_q, cont_rise_q;             // {DA, DB} 按同一 FALL 装载
    bit cont_pins_valid_q;                           // 验证空槽时驱动数据无伪交付
    localparam logic [19:0] CONT_RAW_WORDS[2] = '{20'hABCDE, 20'h25AD9};
    localparam logic [19:0] CONT_CORRECTED[2] = '{20'hABC9E, 20'h25A99};
    bit cont_observe;
    logic [31:0] cont_base_tag;
    longint cont_ready_ps[2], cont_push_ps[2];         // 原数值发布与 CDC 受理相隔一核心拍

    // [宏] 运算和位展开使用唯一产品 RTL。夹具同时锁存本 FALL 和下一
    // RISE，避免在下一上升沿读到新槽，掩盖末位或连续帧交界错误。
    always @(negedge continuous_dco_clk_o) begin
        cont_pins_valid_q <= continuous_pair_valid_o;
        cont_fall_q <= {continuous_da_fall_o, continuous_db_fall_o};
        cont_rise_q <= {continuous_da_rise_o, continuous_db_rise_o};
    end

    // [原数值] 真实原码用有符号 OFFSET=-64、GAIN=0x200。正负两笔都须
    // 校正。观察数值发布、桥实际受理和每笔同伴 tag，测试端不写中间节点。
    always @(posedge clk_core_i) begin : observe_core_continuous
        int seq_index;
        longint core_edge_ps;
        // 观测发布在时钟沿后的 NBA。先记录真实核心沿，避免把仿真
        // 观察延迟和不同 timescale 精度的舍入加进核心流水。
        core_edge_ps = longint'($realtime * 1000.0);
        if (cont_observe && dut.stream_word_valid_core) begin
            seq_index = int'(dut.stream_tag_core - cont_base_tag);
            if (seq_index < 0 || seq_index > 1 || !dut.u_stream_cdc.ready_core_o)
                $fatal(1, "continuous CDC could not accept the real DSP result tag=%h", dut.stream_tag_core);
            if (dut.stream_word_core != CONT_CORRECTED[seq_index] || cont_push_ps[seq_index] != -1)
                $fatal(1, "continuous DSP value/tag at the transport entrance");
            cont_push_ps[seq_index] = core_edge_ps;
        end
        #1ps;
        if (cont_observe && continuous_result_drop_core_o)
            $fatal(1, "continuous CDC unexpectedly dropped a DSP transaction");
        if (cont_observe && dut.stream_word_valid_core) begin
            seq_index = int'(dut.stream_tag_core - cont_base_tag);
            if (seq_index < 0 || seq_index > 1 || cont_ready_ps[seq_index] != -1)
                $fatal(1, "continuous DSP published an unknown or repeated tag");
            cont_ready_ps[seq_index] = core_edge_ps;
        end
    end

    // [位序] 独立从真实时钟沿序号取原字。双 lane 每沿 DA 先于 DB。
    // 只有 FALL 新装载时检查 first/last，随后 RISE 已保持上一组输出。
    task automatic check_continuous_bit(
        input string case_name, input bit rising, expected_valid,
        input int lanes, slot, input logic [31:0] tag, input logic [19:0] word,
        input longint timestamp_ps, input bit encoded = 1'b0,
        input bit filtered = 1'b0, repeated = 1'b0, input logic [31:0] source_tag = '0
    );
        logic [1:0] observed, expected;
        int bit_index, frame_clocks;
        observed = rising ? cont_rise_q : cont_fall_q;
        expected = 2'b00;
        if (expected_valid) begin
            // [编码] 双线原始 MSB 对先到。第一个物理半码取反，第二个
            // 半码保持原值，因此每字所占 CLK 与单 lane 原始数据等长。
            frame_clocks = encoded ? 10 : 10/lanes;
            bit_index = encoded ? 19 - 2*slot : 19 - lanes * (2*slot + int'(rising));
            expected[1] = word[bit_index];
            if (lanes == 2) expected[0] = word[bit_index-1];
            if (encoded && !rising) expected = ~expected;
            if (!rising && (!continuous_pair_valid_o ||
                continuous_pair_first_o !== (slot == 0) ||
                continuous_pair_last_o !== (slot == frame_clocks - 1) ||
                continuous_pair_tag_o != tag ||
                continuous_pair_source_tag_o != (filtered ? source_tag : tag) ||
                continuous_pair_repeat_o != repeated))
                $fatal(1, "%s pair metadata, tag=%h slot=%0d", case_name, tag, slot);
            $display("CONT_EDGE,%s,%0d,%08h,%s,%0d,%0b,%0b",
                     case_name, timestamp_ps, tag, rising ? "RISE" : "FALL",
                     slot, observed[1], observed[0]);
        end
        if (cont_pins_valid_q !== expected_valid || observed !== expected ||
            continuous_dco_clk_o !== rising)
            $fatal(1, "%s launched bits, time=%0d ps tag=%h rise=%b slot=%0d observed=%b expected=%b",
                   case_name, timestamp_ps, tag, rising, slot, observed, expected);
    endtask

    // [训练] 保留上层的接口签名。训练起发相位为工程自定义，可找完整字
    // 的首 FALL，找到后按过零沿逐位取。此入口用于有参考记录的冷启动试验。
    task automatic collect_continuous_word(input logic [19:0] word, input logic [31:0] tag,
                                           input string case_name);
        longint edge_ps;
        do @(negedge continuous_dco_clk_o);
        while (!(continuous_pair_valid_o && continuous_pair_first_o));
        for (int slot = 0; slot < 10; slot++) begin
            edge_ps = longint'($realtime * 1000.0);
            #1ps;
            if (!continuous_dco_oe_o || !continuous_da_oe_o || continuous_db_oe_o)
                $fatal(1, "%s did not select one-lane echo", case_name);
            check_continuous_bit(case_name, 1'b0, 1'b1, 1, slot, tag, word, edge_ps);
            @(posedge continuous_dco_clk_o);
            edge_ps = longint'($realtime * 1000.0);
            #1ps;
            check_continuous_bit(case_name, 1'b1, 1'b1, 1, slot, tag, word, edge_ps);
            if (slot != 9) @(negedge continuous_dco_clk_o);
        end
    endtask

    // =========================================================================
    // 2. 实际 CSR 受理与转换时轴
    // =========================================================================
    task automatic continuous_read_profile(input string case_name, input logic [7:0] a, b);
        logic [15:0] read_a, read_b;
        // 真正通过 SDO 读两次，配置 CDC 的就绪另查；CSR 镜像不能代替生效。
        spi_access(1, 15'h0015, 1, 0, read_a);
        spi_access(1, 15'h0016, 1, 0, read_b);
        if (read_a !== {8'b0, a} || read_b !== {8'b0, b})
            $fatal(1, "%s data config readback failed 0x15=%h 0x16=%h", case_name, read_a, read_b);
        $display("CONT_CSR,%s,%02h,%02h", case_name, read_a[7:0], read_b[7:0]);
    endtask

    // [一笔实验] 每个 CLK 观测一次，包含首发前和两个有意停供的尾部
    // 预约；输出位在实际 FALL、RISE 比。20 位的标签与漏字判据独立，
    // 避免等待 DUT 首帧的写法把错一个 DCLK 的实现判成成功。
    task automatic continuous_profile(
        input string case_name, input int lanes, count_number,
        input logic [31:0] tag_base, input bit expect_late,
        input longint core_period_ps, io_period_ps, input bit encoded = 1'b0
    );
        int slots, delay_cycles, conversion, slot, active_frame, expect_frame;
        longint edge_ps, origin_ps, aligned_ps, deadline_ps, expected_delivery_ps;
        longint raw_ps[2], dst_ps[2], first_ps[2], last_ps[2];
        bit cnv_now, have_pair, previous_valid, expect_missing, expect_reject;
        logic [31:0] expected_tag, previous_tag;
        int previous_slot, result_index, missing_count;
        logic [19:0] expected_word, previous_word;

        slots = encoded ? 10 : 10 / lanes;
        delay_cycles = slots + (encoded ? 2*count_number : count_number);
        origin_ps = 0;
        previous_valid = 1'b0;
        previous_slot = -1;
        previous_tag = '0;
        previous_word = '0;
        missing_count = 0;
        cont_base_tag = tag_base;
        raw_ps = '{-1, -1};
        dst_ps = '{-1, -1};
        first_ps = '{-1, -1};
        last_ps = '{-1, -1};
        cont_ready_ps = '{-1, -1};
        cont_push_ps = '{-1, -1};

        // 关闭 FIFO 后真实复位旧批状态；上述 0x15/16 的生效从 I/O
        // 的许可确认。夹具把核心事件量化到 DCO 同源事件后的第一核心沿。
        write_reg(15'h001C, 1, 16'h0000);
        wait (continuous_config_ready_core_o && continuous_cnv_allowed_io_o);
        @(negedge continuous_dco_clk_o);
        cont_observe = 1'b1;
        sample_source_idle_core_i = 1'b0;

        for (int tick = 0; tick <= 4*slots + delay_cycles; tick++) begin
            conversion = tick/slots;
            cnv_now = tick % slots == 0 && conversion < 4;
            cnv_tick_io_i = cnv_now;
            cnv_tag_io_i = tag_base + 32'(conversion);
            @(posedge continuous_dco_clk_o);
            edge_ps = longint'($realtime * 1000.0);
            if (tick == 0) origin_ps = edge_ps;
            if (edge_ps != origin_ps + longint'(tick)*io_period_ps)
                $fatal(1, "%s nonuniform DCLK/CNV reference geometry", case_name);

            // [收字] 在 DCLK 上升沿前的 valid 才是这笔真实到达时刻。
            // 判断所携实际标签和校正字，避免把 leaf 的输出反推成来源。
            expect_reject = 1'b0;
            if (dut.stream_valid_io) begin
                result_index = int'(dut.stream_tag_io - tag_base);
                if (result_index < 0 || result_index > 1 || dst_ps[result_index] != -1 ||
                    dut.stream_word_io != CONT_CORRECTED[result_index])
                    $fatal(1, "%s invalid I/O-side result identity", case_name);
                dst_ps[result_index] = edge_ps;
                expect_reject = expect_late;
            end

            #1ps;
            check_continuous_bit(case_name, 1'b1, previous_valid, lanes, previous_slot,
                                 previous_tag, previous_word, edge_ps, encoded);
            if (previous_valid && previous_slot == slots - 1)
                last_ps[int'(previous_tag - tag_base)] = edge_ps;

            expect_frame = (tick - delay_cycles) / slots;
            slot = (tick - delay_cycles) % slots;
            active_frame = tick >= delay_cycles && expect_frame < 4 ? expect_frame : -1;
            have_pair = active_frame >= 0 && active_frame < 2 && !expect_late;
            expect_missing = active_frame >= 0 && slot == 0 && (expect_late || active_frame >= 2);
            expected_tag = active_frame >= 0 ? tag_base + 32'(active_frame) : '0;
            expected_word = have_pair ? CONT_CORRECTED[active_frame] : '0;
            if (continuous_cnv_rejected_io_o || continuous_result_rejected_io_o !== expect_reject ||
                continuous_frame_missing_io_o !== expect_missing ||
                (expect_missing && continuous_frame_missing_tag_o !== expected_tag))
                $fatal(1, "%s diagnostics at DCLK tick=%0d saw missing/tag/reject %b/%h/%b",
                       case_name, tick, continuous_frame_missing_io_o,
                       continuous_frame_missing_tag_o, continuous_result_rejected_io_o);
            if (expect_missing) begin
                missing_count++;
                $display("CONT_MISSING,%s,%0d,%08h", case_name, edge_ps, expected_tag);
            end

            if (cnv_now) begin
                // [供码] E1 只建立预定轨迹。E2/E3 提交前次源转换，E4
                // 结束输入但让原数值已完成字退休。tag 从激励转换给定。
                cnv_tick_core_i = 1'b1;
                sample_valid_core_i = conversion == 1 || conversion == 2;
                if (sample_valid_core_i) begin
                    sample_tag_core_i = tag_base + 32'(conversion-1);
                    sample_signed_core_i = CONT_RAW_WORDS[conversion-1];
                end
                @(posedge clk_core_i);
                if (sample_valid_core_i) begin
                    if (!dut.u_dsp.chain_input_valid)
                        $fatal(1, "%s raw code did not reach the original DSP", case_name);
                    raw_ps[conversion-1] = longint'($realtime * 1000.0);
                end
                #1ps;
                cnv_tick_core_i = 1'b0;
                sample_valid_core_i = 1'b0;
                if (cnv_data_timing_error_core_o || conversion_timing_error_core_o)
                    $fatal(1, "%s invalid decoded CNV/source phase", case_name);
            end

            @(negedge continuous_dco_clk_o);
            edge_ps = longint'($realtime * 1000.0);
            #1ps;
            // 这里比较真实 FALL。DCO 和 DA 始终属当前 echo 档，单线
            // DB 必须保持 OE 低，即便固定字状态曾接管过该输出组。
            if (edge_ps != origin_ps + longint'(tick)*io_period_ps + io_period_ps/2 ||
                !continuous_da_oe_o || !continuous_dco_oe_o ||
                continuous_db_oe_o !== (lanes == 2))
                $fatal(1, "%s wrong falling clock or pad ownership", case_name);
            check_continuous_bit(case_name, 1'b0, have_pair, lanes, slot,
                                 expected_tag, expected_word, edge_ps, encoded);
            if (have_pair && slot == 0) first_ps[active_frame] = edge_ps;
            previous_valid = have_pair;
            previous_slot = slot;
            previous_tag = expected_tag;
            previous_word = expected_word;
        end

        sample_source_idle_core_i = 1'b1;
        cont_observe = 1'b0;
        // [期限] 完成时间来自实际源端与目的端。两级 Gray 加目的消费
        // 共走三个 DCLK 上升。相同源速度下，短档应该恰晚一拍。
        for (int sample = 0; sample < 2; sample++) begin
            aligned_ps = origin_ps + longint'(sample*slots)*io_period_ps;
            deadline_ps = aligned_ps + longint'(delay_cycles)*io_period_ps;
            expected_delivery_ps = origin_ps + (((cont_push_ps[sample] - origin_ps) /
                                   io_period_ps) + 3)*io_period_ps;
            if (raw_ps[sample] <= aligned_ps + longint'(slots)*io_period_ps ||
                raw_ps[sample] > aligned_ps + longint'(slots)*io_period_ps + core_period_ps ||
                cont_ready_ps[sample] != raw_ps[sample] + 2*core_period_ps ||
                cont_push_ps[sample] != cont_ready_ps[sample] + core_period_ps ||
                dst_ps[sample] != expected_delivery_ps)
                $fatal(1, "%s measured DSP/CDC ages invalid raw=%0d ready=%0d push=%0d dst=%0d",
                       case_name, raw_ps[sample], cont_ready_ps[sample], cont_push_ps[sample], dst_ps[sample]);
            if (expect_late) begin
                if (dst_ps[sample] != deadline_ps + io_period_ps ||
                    first_ps[sample] != -1 || last_ps[sample] != -1)
                    $fatal(1, "%s expected a single-cycle miss with no substitute bits", case_name);
            end else if (dst_ps[sample] > deadline_ps || first_ps[sample] != deadline_ps + io_period_ps/2 ||
                        last_ps[sample] != deadline_ps + longint'(slots)*io_period_ps) begin
                $fatal(1, "%s first/last at real edge or delivery deadline mismatch", case_name);
            end
            $display("CONT_LATENCY,%s,%08h,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                     case_name, tag_base + 32'(sample), aligned_ps, raw_ps[sample],
                     cont_ready_ps[sample], cont_push_ps[sample], dst_ps[sample],
                     deadline_ps, first_ps[sample], last_ps[sample]);
        end
        if (missing_count != (expect_late ? 4 : 2))
            $fatal(1, "%s missing counts", case_name);
    endtask

    // =========================================================================
    // 3. 配置、单/双 lane 以及晚一拍反例
    // =========================================================================
    // [图97] SINC1 D=4，不启用 FIFO。两种原件滤波复位源共用本接收轴。
    // 第一个资格 CNV 作为 E1 的相位是工程同步约定。E2 空，E3…E6
    // 和 E7…E10 两组各四原码，相邻滤波结果由实际 DSP 在 E7/E11 发布。
    // 原件没有细到 DCLK 的滤波发布与 serializer 关系，表20时隙仍与
    // 原码通路共用；每个候选整字按到达的目的时钟决定在第几个帧更新。
    task automatic exercise_lvds_filter(
        input string case_name, input longint io_ps,
        input logic [31:0] base, input bit with_gpio_sync, input int delta
    );
        int sample_number, frame_slot, previous_slot, delivery_count;
        longint edge_ps, reference_ps;
        bit has_cnv, assigned_valid, assigned_repeat, expects_missing;
        bit previous_valid, previous_repeat, expects_delivery;
        logic [31:0] cnv_for_pair, source_for_pair, previous_cnv, previous_source;
        logic [19:0] word_for_pair, previous_word, first_data, second_data;
        localparam int FRAME_CLKS = 10;          // 单 lane 的 20 个 DDR 位
        localparam int WAIT_CLKS = 20;           // 10 个转换 CLK + 表20 count 10

        // [换代] 只在清数据域后提交首 CNV。用不同输入重复测试时，即使
        // 源序号再次从零开始，仍能用校正后的数值与 CNV 身份分辨旧/新。
        first_data = 20'(186 + delta);
        second_data = 20'(1086 + delta);
        wait (continuous_config_ready_core_o && continuous_cnv_allowed_io_o);
        @(negedge clk_core_i);
        sample_source_idle_core_i = 1'b0;
        if (with_gpio_sync) begin
            gpio_i[1] = 1'b0;
            repeat (5) @(negedge clk_core_i);
        end
        // GPIO 仍低时 E1 只进入原 DSP 清除/对齐；从 CSR 清除已恢复
        // 的 E1 还应获一次真实 LVDS 预约，截止时由于未出码应为空。
        @(negedge continuous_dco_clk_o);
        cnv_tag_io_i = base - 32'd1;
        cnv_tick_io_i = 1'b1;
        @(posedge continuous_dco_clk_o);
        #1ps;
        if (continuous_cnv_rejected_io_o !== with_gpio_sync)
            $fatal(1, "incorrect CNV admission at filter epoch start");
        cnv_tick_core_i = 1'b1;
        @(posedge clk_core_i);
        #1ps;
        cnv_tick_core_i = 1'b0;
        @(negedge continuous_dco_clk_o);
        cnv_tick_io_i = 1'b0;
        if (with_gpio_sync) gpio_i[1] = 1'b1;
        repeat (FRAME_CLKS - 1) @(negedge continuous_dco_clk_o);
        if (!continuous_cnv_allowed_io_o)
            $fatal(1, "filter SYNC did not rearm continuous I/O before E2");

        previous_valid = 1'b0;
        previous_repeat = 1'b0;
        previous_slot = -1;
        previous_cnv = '0;
        previous_source = '0;
        previous_word = '0;
        delivery_count = 0;
        reference_ps = 0;
        for (int tick = 0; tick <= 150; tick++) begin
            sample_number = tick/FRAME_CLKS;
            has_cnv = tick % FRAME_CLKS == 0 && sample_number < 13;
            cnv_tick_io_i = has_cnv;
            cnv_tag_io_i = base + 32'(sample_number);
            @(posedge continuous_dco_clk_o);
            edge_ps = longint'($realtime * 1000.0);
            if (tick == 0) reference_ps = edge_ps;
            // [可用窗] 原 SINC1 在 E7/E11 真实公布。原 core 对齐后
            // 第一个沿与 CDC 写沿至多两核心周期；三拍 CDC 后在 53/93 读取。
            // 因此最早完整起发的预约是第 60/100 个 CLK 上升。
            expects_delivery = tick == 53 || tick == 93;
            if (dut.stream_valid_io !== expects_delivery)
                $fatal(1, "filtered result did not arrive at expected I/O edge %0d", tick);
            if (expects_delivery) begin
                if (dut.stream_tag_io != 32'(delivery_count) ||
                    dut.stream_word_io != (delivery_count == 0 ? first_data : second_data))
                    $fatal(1, "filter CDC wrong actual value or publication identity");
                $display("CONT_FILTER_UPDATE,%s,%0d,%08h,%05h",
                         case_name, edge_ps, dut.stream_tag_io, dut.stream_word_io);
                delivery_count++;
            end

            #1ps;
            check_continuous_bit(case_name, 1'b1, previous_valid, 1, previous_slot,
                                 previous_cnv, previous_word, edge_ps, 0, 1,
                                 previous_repeat, previous_source);
            // 启动未知在每个已接收 CNV 到期时均禁止输出。两条复位路
            // 径只因清除期 E1 能否预约，相差一个未知启动槽。
            expects_missing = tick >= (with_gpio_sync ? 20 : 10) && tick < 60 &&
                              tick % FRAME_CLKS == 0;
            if (continuous_cnv_rejected_io_o || continuous_result_rejected_io_o ||
                continuous_frame_missing_io_o !== expects_missing ||
                (expects_missing && continuous_frame_missing_tag_o != base + 32'(sample_number-2)))
                $fatal(1, "filter warmup or slot retirement mismatch at tick=%0d", tick);
            if (expects_missing)
                $display("CONT_FILTER_WARMUP,%s,%0d,%08h", case_name, edge_ps,
                         continuous_frame_missing_tag_o);

            if (has_cnv) begin
                // E2 无原码，E3 首原码。后面继续给真实 CNV 让最后一笔
                // 正常发布及重复；输入原始完成码与回读前端使用同一身份。
                cnv_tick_core_i = 1'b1;
                sample_valid_core_i = sample_number >= 1 && sample_number <= 8;
                sample_tag_core_i = base + 32'(sample_number - 1);
                case (sample_number)
                    1, 2, 3, 4: sample_signed_core_i = 20'(100*sample_number + delta);
                    5, 6, 7, 8: sample_signed_core_i = 20'(1000 + 100*(sample_number - 5) + delta);
                    default: sample_signed_core_i = '0;
                endcase
                @(posedge clk_core_i);
                if (sample_valid_core_i && !dut.u_dsp.chain_input_valid)
                    $fatal(1, "SINC1 source code was not accepted");
                #1ps;
                cnv_tick_core_i = 1'b0;
                sample_valid_core_i = 1'b0;
                if (cnv_data_timing_error_core_o || conversion_timing_error_core_o ||
                    continuous_result_drop_core_o)
                    $fatal(1, "filter CNV/transport fixture had an invalid transaction");
                // RDY 活动低窗来自公开 GPIO2，串行在后面的跨域槽更新。
                if ((sample_number == 5 || sample_number == 9) &&
                    (!gpio_oe[2] || gpio_o[2]))
                    $fatal(1, "previous filter publication did not drive GPIO2 low");
            end

            assigned_valid = tick >= 60 && tick < 150;
            assigned_repeat = assigned_valid && !((tick >= 60 && tick < 70) ||
                                                   (tick >= 100 && tick < 110));
            frame_slot = tick % FRAME_CLKS;
            cnv_for_pair = base + 32'((tick - WAIT_CLKS)/FRAME_CLKS);
            source_for_pair = tick < 100 ? 32'd0 : 32'd1;
            word_for_pair = tick < 100 ? first_data : second_data;
            @(negedge continuous_dco_clk_o);
            edge_ps = longint'($realtime * 1000.0);
            #1ps;
            if (edge_ps != reference_ps + longint'(tick)*io_ps + io_ps/2 ||
                !continuous_dco_oe_o || !continuous_da_oe_o || continuous_db_oe_o)
                $fatal(1, "filter LVDS clock or pin ownership changed between CNV slots");
            check_continuous_bit(case_name, 1'b0, assigned_valid, 1, frame_slot,
                                 cnv_for_pair, word_for_pair, edge_ps, 0, 1,
                                 assigned_repeat, source_for_pair);
            if (assigned_valid && frame_slot == 0)
                $display("CONT_FILTER_FRAME,%s,%0d,%08h,%08h,%0b,%05h",
                         case_name, edge_ps, continuous_pair_tag_o, continuous_pair_source_tag_o,
                         continuous_pair_repeat_o, word_for_pair);
            previous_valid = assigned_valid;
            previous_repeat = assigned_repeat;
            previous_slot = frame_slot;
            previous_cnv = cnv_for_pair;
            previous_source = source_for_pair;
            previous_word = word_for_pair;
        end
        if (delivery_count != 2) $fatal(1, "missing real SINC1 result");
        sample_source_idle_core_i = 1'b1;
    endtask

    // [p67] 直接 LVDS 支持 GPIO FILTER_SYNC 和实际 0x29 写入两个清
    // 除条件。不等同于 FIFO 需要 GPIO 开批；第二试验严格保持 GPIO 高。
    task automatic audit_lvds_filtered(input longint io_ps);
        logic [15:0] filter_csr;
        wait (continuous_config_ready_core_o);
        write_reg(15'h0015, 1, 16'h0040);       // 切为单 lane，维持 10 CLK 每字
        wait (continuous_config_ready_core_o);
        write_reg(15'h0016, 1, 16'h0070);       // 表20 count 10 后需要 20 个完整 CLK
        write_reg(15'h0019, 1, 16'h000D);       // GPIO1 输入与 GPIO2 输出
        write_reg(15'h001A, 1, 16'h0080);       // GPIO1 接 FILTER_SYNC
        write_reg(15'h001B, 1, 16'h0003);       // GPIO2 接低有效 FILTER_RESULT_RDY
        write_reg(15'h0029, 1, 16'h0009);       // 配置真正 SINC1 D=4
        spi_access(1, 15'h0029, 1, 0, filter_csr);
        if (filter_csr !== 16'h0009) $fatal(1, "SINC1 D4 SPI readback failed");
        continuous_read_profile("LVDS_FILTER", 8'h40, 8'h70);
        exercise_lvds_filter("LVDS_FILTER_GPIO", io_ps, 32'h5500, 1'b1, 0);

        // 同值写也是重置事件。旧首字之前的所有时隙必须空白；换不同
        // 刺激才允许同一个滤波序号重新出现，防止遗留保持数据假通过。
        wait (continuous_config_ready_core_o);
        write_reg(15'h0029, 1, 16'h0009);
        exercise_lvds_filter("LVDS_FILTER_CSR", io_ps, 32'h5600, 1'b0, 700);
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h001A, 1, 16'h0000);
        write_reg(15'h001B, 1, 16'h0000);
        write_reg(15'h0019, 1, 16'h0001);
        wait (continuous_config_ready_core_o);
        $display("PASS product Fig97 SINC1 D4 words 186/1086, then 886/1786 after same-value 0x29 reset");
    endtask

    task automatic run_continuous_pin_check();
        longint core_ps, io_ps, edge_ps;
        int rejects_before;
        // 实测两个时钟周期。当前夹具把核心资格设置为 100 MHz，持续
        // 周期是 22ns×10/5。40MS/s 的同位点由更快时钟的定向叶试验覆盖。
        @(posedge clk_core_i);
        edge_ps = longint'($realtime * 1000.0);
        @(posedge clk_core_i);
        core_ps = longint'($realtime * 1000.0) - edge_ps;
        @(posedge continuous_dco_clk_o);
        edge_ps = longint'($realtime * 1000.0);
        @(posedge continuous_dco_clk_o);
        io_ps = longint'($realtime * 1000.0) - edge_ps;
        if (core_ps != 10000 || io_ps != 22000)
            $fatal(1, "continuous product fixture clock profile changed core=%0d io=%0d", core_ps, io_ps);
        $display("CONT_CLOCKS,%0d,%0d,%0d,%0d", core_ps, io_ps, 10*io_ps, 5*io_ps);

        // 通过真实 SPI 配置 OFFSET、GAIN 与滤波旁路，数值检查可区分
        // 原码直连与实际校正。第一/第三档使用表20最长的 10/5。
        write_reg(15'h0026, 2, 16'h0FC0);
        write_reg(15'h0028, 2, 16'h0200);
        write_reg(15'h0029, 1, 16'h0000);
        write_reg(15'h0015, 1, 16'h0040);
        write_reg(15'h0016, 1, 16'h0070);
        continuous_read_profile("LVDS_SINGLE", 8'h40, 8'h70);
        continuous_profile("LVDS_SINGLE", 1, 10, 32'h5100, 0, core_ps, io_ps);

        // 只更改表20相位码，实际来源和跨域时钟照旧。结果完成却错过
        // 调度截止的先例用来闭合真实 CDC 晚一个 DCLK 的拒绝路径。
        wait (continuous_config_ready_core_o);
        write_reg(15'h0016, 1, 16'h0000);
        continuous_read_profile("LVDS_LATE", 8'h40, 8'h00);
        continuous_profile("LVDS_LATE", 1, 3, 32'h5200, 1, core_ps, io_ps);

        wait (continuous_config_ready_core_o);
        write_reg(15'h0015, 1, 16'h0044);
        wait (continuous_config_ready_core_o);
        write_reg(15'h0016, 1, 16'h0020);
        continuous_read_profile("LVDS_DUAL", 8'h44, 8'h20);
        continuous_profile("LVDS_DUAL", 2, 5, 32'h5300, 0, core_ps, io_ps);

        // [产品编码] 同一 DSP/CDC 给出真实字，物理边沿现在承载半码。实际
        // SPI 必须拒绝单线和滤波组合，原快照继续有效。
        wait (continuous_config_ready_core_o);
        write_reg(15'h0016, 1, 16'h0024);
        continuous_read_profile("LVDS_MANCHESTER", 8'h44, 8'h24);
        continuous_profile("LVDS_MANCHESTER", 2, 5, 32'h5400, 0, core_ps, io_ps, 1'b1);
        wait (continuous_config_ready_core_o);
        rejects_before = config_rejects;
        write_reg(15'h0015, 1, 16'h0040);
        write_reg(15'h0029, 1, 16'h0009);
        if (config_rejects != rejects_before + 2)
            $fatal(1, "mixed Manchester single lane or filter configuration was not rejected");
        continuous_read_profile("LVDS_MANCHESTER_REJECT", 8'h44, 8'h24);
        write_reg(15'h0016, 1, 16'h0020);
        wait (continuous_config_ready_core_o);
        audit_lvds_filtered(io_ps);

        // [收尾] 恢复初始连续选择和单位系数，后续 Fig83/99 的原配置
        // 序列独立。新 clk 域配置确认后才把控制权交回主台架。
        wait (continuous_config_ready_core_o);
        write_reg(15'h0015, 1, 16'h0040);
        wait (continuous_config_ready_core_o);
        write_reg(15'h0016, 1, 16'h0000);
        write_reg(15'h0026, 2, 16'h0000);
        write_reg(15'h001C, 1, 16'h0000);
        wait (continuous_config_ready_core_o);
        $display("PASS product continuous SPI, DSP/tag, one-DCLK miss, filter replay and Manchester");
    endtask
