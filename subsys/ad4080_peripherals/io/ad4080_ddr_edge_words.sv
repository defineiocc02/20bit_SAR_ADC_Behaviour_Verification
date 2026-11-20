`timescale 1ns/1ps

// =============================================================================
// 文件名     : ad4080_ddr_edge_words.sv
// 模块名     : ad4080_ddr_edge_words
// 版本       : v1.0
// 功能       : 把连续 LVDS 的并行 20 位结果展开成 DA/DB 的 DDR 边沿字。
// 设计说明   :
//   * 槽 0 对应首发下降沿及紧随的上升沿，位序依据 Rev. B 图 74/75。
//   * 组合边界无时钟、复位和在途状态。接收端同时捕获模式、边沿字及帧身份。
//   * [优先级] 固定检查字优先于样本。模式和检查位由外围在帧间提交。
//   * [边界] 填零用于确定未使用的向量槽。Pad OE、DCO、CDC 和复位隔离
//     依照物理宏合同另行接入，不能把逻辑 0 当成无驱动。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_ddr_edge_words (
    // =========================================================================
    // 1. 接口
    // =========================================================================
    // --- 已提交结果和模式 ---
    input  logic [19:0] sample_i,      // 二补码作为原样位串处理
    input  logic        dual_lane_i,   // 置位启用 A/B 双 lane
    input  logic        intf_check_i,  // 置位时覆盖样本，留给上层做空闲切换

    // --- DDR 宏前的装载数据 ---
    output logic [9:0]  da_fall_o,     // [0] 是本帧的首个下降沿位
    output logic [9:0]  da_rise_o,     // 同索引发生在对应下降沿之后
    output logic [9:0]  db_fall_o,     // 仅双 lane 时的前五槽有效
    output logic [9:0]  db_rise_o,     // 单 lane 的零值不控制 Pad 高阻
    output logic [3:0]  slot_count_o   // 有效边沿对数量，满帧物理发射由宏负责
);

    // =========================================================================
    // 2. 选择本帧的逻辑数据
    // =========================================================================
    localparam logic [19:0] CHECK_PATTERN = 20'hAC5D6;  // Rev. B 表 48 固定检查字
    logic [19:0] selected_word;                        // 全部 lane 从同一笔字取位

    // -------------------------------------------------------------------------
    // [测试] 先选择整字再分配 lane，使测试与实际结果经过相同映射。
    // 清除检查位后没有残留帧。本块未锁存模式，外部须提供帧边界保证。
    // -------------------------------------------------------------------------
    assign selected_word = intf_check_i ? CHECK_PATTERN : sample_i;
    assign slot_count_o = dual_lane_i ? 4'd5 : 4'd10;

    // =========================================================================
    // 3. 同一槽内先下降、后上升
    // =========================================================================
    // [位序] 双 lane 一槽消耗四位。DA 承担奇数位，DB 承担偶数位。
    // 单 lane 一槽消耗两位，DA 下降沿发奇数位，上升沿发偶数位。
    // 较高五槽仅服务单 lane。静态分支保证综合时所有位选落在 [19:0] 内。
    for (genvar slot = 0; slot < 10; slot++) begin : gen_slot
        if (slot < 5) begin : gen_shared_slot
            assign da_fall_o[slot] = dual_lane_i ? selected_word[19-4*slot]
                                                 : selected_word[19-2*slot];
            assign da_rise_o[slot] = dual_lane_i ? selected_word[17-4*slot]
                                                 : selected_word[18-2*slot];
            assign db_fall_o[slot] = dual_lane_i & selected_word[18-4*slot];
            assign db_rise_o[slot] = dual_lane_i & selected_word[16-4*slot];
        end else begin : gen_single_slot
            assign da_fall_o[slot] = !dual_lane_i & selected_word[19-2*slot];
            assign da_rise_o[slot] = !dual_lane_i & selected_word[18-2*slot];
            assign db_fall_o[slot] = 1'b0;
            assign db_rise_o[slot] = 1'b0;
        end
    end
endmodule
