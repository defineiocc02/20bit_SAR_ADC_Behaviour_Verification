`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_continuous_lvds.sv
// 模块名     : ad4080_continuous_lvds
// 版本       : v1.0
// 功能       : 把同笔 CNV 事件和已备妥的 20 位结果按表 20 送入 DDR 输出宏。
// 设计说明   :
//   * CLK+ 上升沿接收已对齐事件。每拍装载的 pair 面向随后的 FALL/RISE。
//     CNV 的管脚对齐、整字 CDC、宏和 Pad 不在本叶，不能用两级同步后的
//     任意 CNV 脉冲代替已对齐事件。输入字和 tag 必须同时且来自同一笔。
//   * 旁路按 CNV 标签取结果，过期即留空。滤波直读依 Rev. B 图 97 持有
//     已发布的最近一字，之后的 CNV 槽允许重读，并同时给出本槽与源字标签。
//   * [优先级] 复位、换代 > 会话失效 > 固定字训练 > 预约/收字与本槽退休。
//     截止沿抵达的有效字仍可用于本槽。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [图97/表49] 增加滤波输出保持和双 lane Manchester 码元。
// =============================================================================

module ad4080_continuous_lvds #(
    parameter int unsigned TAG_WIDTH = 16  // 在途事务的身份，至少 2 位
) (
    // --- 同一 CLK+ 域的模式和时钟边界 ---
    input  logic                 clk_dclk_i,          // 已恢复的正向 LVDS CLK
    input  logic                 rst_ni,              // 会话共代清除，低有效
    input  logic                 enable_i,            // 高期间两字节保持，低须跨一个上升沿
    input  logic [7:0]           config_a_i,          // 0x15，独占连续 LVDS 模式
    input  logic [7:0]           config_b_i,          // 0x16，CNV pad 选择由上层执行
    input  logic                 filter_mode_i,       // 配置会话内同步稳定，置位启用图97保持
    input  logic                 clear_i,             // 保持到 CLK+ 采到；同代已作废的 CDC 字须冲刷
    output logic                 config_ok_o,         // 支持且当前会话内一致
    output logic                 idle_o,              // 无待发转换，也无在发 pair

    // --- 原子身份和结果 ---
    input  logic                 cnv_event_i,         // 已对齐的 CNV 上升事件，每次提交一拍
    input  logic [TAG_WIDTH-1:0] cnv_tag_i,           // 与该次结果共用的转换 tag
    input  logic                 result_valid_i,      // 每个置位的上升沿提交一个完整字
    input  logic [TAG_WIDTH-1:0] result_tag_i,        // raw 同笔 CNV，滤波为本次真实发布的 ID
    input  logic [19:0]          result_i,            // 已发布的 20 位值，不重做校正/滤波
    output logic                 cnv_rejected_o,      // 已采到 CNV 但未预约，脉冲
    output logic                 result_rejected_o,   // 未预约、重复、过期或模式无效，脉冲
    output logic                 frame_missing_o,     // 预约到期但缺失或不可装载，脉冲
    output logic [TAG_WIDTH-1:0] frame_missing_tag_o, // 脉冲所在拍关联的预约身份

    // --- 到物理边界的 FALL → RISE 负载 ---
    output logic                 pair_valid_o,        // 上升沿后装妥，随后 FALL 和 RISE 有效
    output logic                 pair_first_o,        // 本 pair 的 FALL 发送 D19
    output logic                 pair_last_o,         // 本 pair 的 RISE 发送最后位
    output logic [TAG_WIDTH-1:0] pair_tag_o,          // 占用该时间槽的 CNV 身份，训练为 0
    output logic [TAG_WIDTH-1:0] pair_source_tag_o,   // 本字原身份，滤波重读保留同一源 ID
    output logic                 pair_repeat_o,       // 本字已在旧时隙交付，仅滤波可置位
    output logic                 da_fall_o,           // 本 CLK 下降沿 DA
    output logic                 da_rise_o,           // 紧随的上升沿 DA
    output logic                 db_fall_o,           // 双 lane 本 CLK 下降沿 DB
    output logic                 db_rise_o,           // 双 lane 紧随的上升沿 DB
    output logic                 dco_clk_o,           // 连续转发时钟源，由专用时钟路径实现
    output logic                 dco_oe_o,            // self 模式关闭驱动，不能用常量替代
    output logic                 da_oe_o,             // 空槽没有有效帧，仍属于连续口会话
    output logic                 db_oe_o              // 单 lane 关闭 DB 驱动
);
    // =========================================================================
    // 1. 表 20 配置与会话
    // =========================================================================
    logic [7:0] config_a_q, config_b_q;                // 启动时的原子模式快照
    logic session_q, profile_fault_q, filter_mode_q; // 更换模式须先结束旧会话
    logic dual_lane, training, manchester, profile_valid, drive_allowed, run;
    logic [3:0] map_slots, slots_per_cnv;             // 原 DDR 位对数、实际占用 CLK 数
    logic [4:0] biased_count, decoded_count, delay_cycles;

`ifndef SYNTHESIS
    if (TAG_WIDTH < 2) begin : g_bad_tag_width
        initial $fatal(1, "TAG_WIDTH must be at least 2");
    end
`endif

    // [表 20] 原件的 count 序列从 3 起，越过 lane 帧长后回绕到 1。
    // 原始双线图 80 延迟箭头比 D19/D18 多画一拍，以后者为参考。
    // [工程相位] 原件没有 Manchester 首位轴，故每原 DDR 位对的时隙
    // 扩成两个物理 CLK，保持表20双 lane 码范围。400 MHz 时 count=5
    // 的首半码在 CNV+51.25ns；此值须由器件波形确认。
    assign dual_lane = config_a_i[2];
    assign training = config_a_i[4];
    assign manchester = config_b_i[2];
    assign slots_per_cnv = manchester ? map_slots << 1 : map_slots;
    assign biased_count = {1'b0, config_b_i[7:4]} + 5'd3;
    assign decoded_count = biased_count > {1'b0, map_slots}
        ? biased_count - {1'b0, map_slots} : biased_count;
    assign delay_cycles = {1'b0, slots_per_cnv} + (manchester ? decoded_count << 1 : decoded_count);

    // [边界] 表49 只允许旁路滤波、双 lane 的 Manchester。self 与
    // echo 数据等价，区别在 DCO OE。滤波换代清数值，同时保持外部时基。
    assign profile_valid = config_a_i[6] && !(|(config_a_i & 8'hAB)) &&
                           !config_b_i[1] && config_b_i[7:4] < map_slots &&
                           (!manchester || (dual_lane && !filter_mode_i));
    assign config_ok_o = profile_valid && !profile_fault_q &&
                         (!session_q || {config_a_i, config_b_i, filter_mode_i} ==
                                        {config_a_q, config_b_q, filter_mode_q});
    assign drive_allowed = rst_ni && enable_i && config_ok_o;
    assign run = drive_allowed && !clear_i;
    assign dco_clk_o = clk_dclk_i;
    assign dco_oe_o = drive_allowed && !config_b_i[3];
    assign da_oe_o  = drive_allowed;
    assign db_oe_o  = drive_allowed && dual_lane;

    // [失效] 在途改档立即封住装载/OE，时钟沿记住失败，避免更改后再恢复
    // 原寄存器值时偷偷续发。pad 的物理启动/隔离由外层处理。
    always_ff @(posedge clk_dclk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            config_a_q      <= '0;
            config_b_q      <= '0;
            filter_mode_q   <= 1'b0;
            session_q       <= 1'b0;
            profile_fault_q <= 1'b0;
        end else if (!enable_i || clear_i) begin
            session_q       <= 1'b0;
            profile_fault_q <= 1'b0;
        end else if (!session_q) begin
            config_a_q      <= config_a_i;
            config_b_q      <= config_b_i;
            filter_mode_q   <= filter_mode_i;
            session_q       <= 1'b1;
            profile_fault_q <= !profile_valid;
        end else if (!config_ok_o) begin
            profile_fault_q <= 1'b1;
        end
    end

    // =========================================================================
    // 2. 两个待发 CNV 预约及完整结果配对
    // =========================================================================
    logic [1:0] pending_q, have_word_q, due, reusable; // 每个预约独立等待本字和本时隙
    logic [1:0] same_tag, result_match;
    logic [4:0] remaining_q [2];                      // 0 意味着本上升沿必须决出结果
    logic [TAG_WIDTH-1:0] pending_tag_q [2];
    logic [19:0] pending_word_q [2];                  // 仅两笔在途，独立于采后 FIFO
    logic [3:0] cnv_holdoff_q;                        // 上次受理后保持帧长，禁相叠
    logic cnv_accept, result_for_new;
    logic free_index, due_index, due_present, due_has_word;
    logic frame_active_q, frame_complete, launch;
    logic [TAG_WIDTH-1:0] frame_tag_q, frame_source_tag_q;
    logic [19:0] frame_word_q;
    logic frame_repeat_q;
    logic filter_update, filter_valid_q, filter_unused_q;
    logic [TAG_WIDTH-1:0] filter_tag_q;
    logic [19:0] filter_word_q;                        // 图97 当前最新真实发布字

    // [重叠] 每 10/5 个 CLK 最快接受一次，最长预约 20/10 CLK，因此两槽
    // 足够。到期与再次受理可以同沿。tag 仍在发或尚未退休时不可重复用。
    assign reusable = ~pending_q | due;
    assign free_index = !reusable[0];
    assign cnv_accept = run && !training && cnv_event_i && cnv_holdoff_q == 0 &&
                        (|reusable) && !(|same_tag) &&
                        !(frame_active_q && !frame_complete && frame_tag_q == cnv_tag_i);
    assign result_for_new = cnv_accept && !filter_mode_i && result_valid_i && result_tag_i == cnv_tag_i;
    assign filter_update = run && filter_mode_i && !training && result_valid_i &&
                           (!filter_valid_q || result_tag_i != filter_tag_q);
    assign due_index = due[1];
    assign due_present = |due;
    assign due_has_word = filter_mode_i ? (filter_valid_q || filter_update) :
                          (have_word_q[due_index] || result_match[due_index]);
    assign launch = run && !training && due_present && due_has_word &&
                    (!frame_active_q || frame_complete);
    assign idle_o = pending_q == 0 && !frame_active_q;

    for (genvar slot = 0; slot < 2; slot++) begin : g_reservation
        assign due[slot] = pending_q[slot] && remaining_q[slot] == 0;
        assign same_tag[slot] = pending_q[slot] && pending_tag_q[slot] == cnv_tag_i;
        assign result_match[slot] = !filter_mode_i && pending_q[slot] && !have_word_q[slot] &&
                                    result_valid_i && pending_tag_q[slot] == result_tag_i;

        // [同沿] 已到期槽优先供当前帧取走，随后允许在同位置登记新 CNV。
        // 收字不改等待计数，保证快字与最后一拍才到的字使用同一个首位沿。
        // 过期数据不能写向新槽。复位清元数据，旧值没有机会成为新会话结果。
        always_ff @(posedge clk_dclk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                pending_q[slot]      <= 1'b0;
                have_word_q[slot]    <= 1'b0;
                remaining_q[slot]    <= '0;
                pending_tag_q[slot]  <= '0;
                pending_word_q[slot] <= '0;
            end else if (!run || training) begin
                pending_q[slot]   <= 1'b0;
                have_word_q[slot] <= 1'b0;
            end else if (cnv_accept && free_index == 1'(slot)) begin
                pending_q[slot]     <= 1'b1;
                have_word_q[slot]   <= result_for_new;
                remaining_q[slot]   <= delay_cycles - 5'd1;
                pending_tag_q[slot] <= cnv_tag_i;
                if (result_for_new) pending_word_q[slot] <= result_i;
            end else if (due[slot]) begin
                pending_q[slot]   <= 1'b0;
                have_word_q[slot] <= 1'b0;
            end else if (pending_q[slot]) begin
                remaining_q[slot] <= remaining_q[slot] - 5'd1;
                if (result_match[slot]) begin
                    have_word_q[slot]    <= 1'b1;
                    pending_word_q[slot] <= result_i;
                end
            end
        end
    end

    // [图97] 滤波字是复合结果，来源标签独立于 CNV。同标签的重复
    // 提交不会重写已持有的真字。新结果与时隙到期同沿时本帧优先使用新值。
    // 开始新代前同时作废 CDC 在途字；原值只有在当前代发布后才可重读。
    always_ff @(posedge clk_dclk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            filter_valid_q  <= 1'b0;
            filter_unused_q <= 1'b0;
            filter_tag_q    <= '0;
            filter_word_q   <= '0;
        end else if (!run || !filter_mode_i || training) begin
            filter_valid_q  <= 1'b0;
            filter_unused_q <= 1'b0;
        end else if (filter_update) begin
            filter_valid_q  <= 1'b1;
            filter_unused_q <= !launch;
            filter_tag_q    <= result_tag_i;
            filter_word_q   <= result_i;
        end else if (launch) begin
            filter_unused_q <= 1'b0;
        end
    end

    // [诊断] 截止时缺字可见。raw 迟字不参加任何其他帧，滤波尚无
    // 首有效字时也留空；禁用期间的
    // 提交也有可见拒绝；跨域输入须先同步再进入本逻辑。
    always_ff @(posedge clk_dclk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            cnv_holdoff_q       <= '0;
            cnv_rejected_o      <= 1'b0;
            result_rejected_o   <= 1'b0;
            frame_missing_o     <= 1'b0;
            frame_missing_tag_o <= '0;
        end else begin
            if (!run || training) cnv_holdoff_q <= '0;
            else if (cnv_accept) cnv_holdoff_q <= slots_per_cnv - 4'd1;
            else if (cnv_holdoff_q != 0) cnv_holdoff_q <= cnv_holdoff_q - 4'd1;

            cnv_rejected_o <= cnv_event_i && !cnv_accept;
            result_rejected_o <= result_valid_i && !(run && !training &&
                                 ((|result_match) || result_for_new || filter_update));
            frame_missing_o <= run && !training && due_present && !launch;
            if (run && !training && due_present && !launch)
                frame_missing_tag_o <= pending_tag_q[due_index];
        end
    end

    // =========================================================================
    // 3. 固定字和正常字经同一个 DDR 位边沿映射器
    // =========================================================================
    logic [3:0] slot_q, map_index;                     // 启用编码时每个原槽分成两个符号
    logic da_symbol, db_symbol;
    logic [9:0] da_fall_slots, da_rise_slots, db_fall_slots, db_rise_slots;

    // [顺序] 末 pair 的 RISE 在更新本寄存器的同一 CLK 上升沿之前已有宏
    // 装载。截止沿来的字直接装入 frame_q，保持下一个 FALL 可取槽 0。
    // 训练从首个使能采样沿起按整字重复，首相位是工程约定，见 Rev. B p50。
    assign frame_complete = frame_active_q && slot_q == slots_per_cnv - 4'd1;
    always_ff @(posedge clk_dclk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            frame_active_q <= 1'b0;
            frame_tag_q    <= '0;
            frame_source_tag_q <= '0;
            frame_repeat_q <= 1'b0;
            frame_word_q   <= '0;
            slot_q         <= '0;
        end else if (!run) begin
            frame_active_q <= 1'b0;
            slot_q         <= '0;
        end else if (training) begin
            frame_active_q <= 1'b1;
            slot_q         <= (!frame_active_q || frame_complete) ? 4'd0 : slot_q + 4'd1;
        end else if (launch) begin
            frame_active_q <= 1'b1;
            frame_tag_q    <= pending_tag_q[due_index];
            frame_repeat_q <= filter_mode_i && !filter_update && !filter_unused_q;
            if (filter_mode_i) begin
                frame_source_tag_q <= filter_update ? result_tag_i : filter_tag_q;
                frame_word_q <= filter_update ? result_i : filter_word_q;
            end else begin
                frame_source_tag_q <= pending_tag_q[due_index];
                frame_word_q <= result_match[due_index] ? result_i : pending_word_q[due_index];
            end
            slot_q         <= '0;
        end else if (frame_complete) begin
            frame_active_q <= 1'b0;
            slot_q         <= '0;
        end else if (frame_active_q) begin
            slot_q <= slot_q + 4'd1;
        end
    end

    ad4080_ddr_edge_words u_edge_words (
        .sample_i(frame_word_q), .dual_lane_i(dual_lane), .intf_check_i(training),
        .da_fall_o(da_fall_slots), .da_rise_o(da_rise_slots),
        .db_fall_o(db_fall_slots), .db_rise_o(db_rise_slots), .slot_count_o(map_slots)
    );

    // [空槽] 宏收到有效槽才解释边沿字；逻辑空槽值为零，独立 OE 才控制
    // 物理驱动。DDR 宏应锁住同一 pair，不能在下一上升沿重新取 RISE 位。
    assign pair_valid_o = run && frame_active_q;
    assign pair_first_o = pair_valid_o && slot_q == 0;
    assign pair_last_o = pair_valid_o && frame_complete;
    assign pair_tag_o = pair_valid_o && !training ? frame_tag_q : '0;
    assign pair_source_tag_o = pair_valid_o && !training ? frame_source_tag_q : '0;
    assign pair_repeat_o = pair_valid_o && !training && frame_repeat_q;
    // [IEEE 802.3 §7.3.1.1] 一个码元的前半取反、后半原值。每条 lane
    // 逐位发送原 DDR mapper 的 FALL、RISE 位，原 lane 位序无第二套。
    // 对等半周期每个码元各一高一低，数据按 10 个 CLK/字传输。
    assign map_index = manchester ? slot_q >> 1 : slot_q;
    assign da_symbol = (manchester && slot_q[0]) ? da_rise_slots[map_index] : da_fall_slots[map_index];
    assign db_symbol = (manchester && slot_q[0]) ? db_rise_slots[map_index] : db_fall_slots[map_index];
    assign da_fall_o = pair_valid_o && (manchester ? !da_symbol : da_symbol);
    assign da_rise_o = pair_valid_o && (manchester ? da_symbol : da_rise_slots[map_index]);
    assign db_fall_o = pair_valid_o && (manchester ? !db_symbol : db_symbol);
    assign db_rise_o = pair_valid_o && (manchester ? db_symbol : db_rise_slots[map_index]);
endmodule

`default_nettype wire
