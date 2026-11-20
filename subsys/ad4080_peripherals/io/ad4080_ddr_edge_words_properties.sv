`timescale 1ns/1ps

// =============================================================================
// 文件名     : ad4080_ddr_edge_words_properties.sv
// 模块名     : ad4080_ddr_edge_words_properties
// 版本       : v1.0
// 功能       : Yosys SAT 对通用综合网表检查所有二态字和模式。
// 设计说明   : 显式按线时序连接输出位，独立于 DUT 的 genvar 位选规律。
//              形式结果仅涵盖逻辑映射，不含发射宏或跨域握手。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_ddr_edge_words_properties (
    // =========================================================================
    // 1. SAT 自由变量和所证性质
    // =========================================================================
    // --- 自由二态变量 ---
    input  logic [19:0] sample_i,      // 不约束样本数值和符号
    input  logic        dual_lane_i,   // 两个 lane 模式都参与求证
    input  logic        intf_check_i,  // 固定覆盖检查和正常透传都参与求证

    // --- 全合取性质 ---
    output logic        correct_o     // 若为 0，模型给出次序或闲置槽反例
);
    logic [9:0]  da_fall, da_rise; // 物理边沿的并行镜像
    logic [9:0]  db_fall, db_rise; // 单 lane 时这些位应全部可证明为零
    logic [3:0]  slots;            // 覆盖宏下一次重装载应发生的边界
    logic [19:0] single_rx;        // DA 每沿一位得到的字
    logic [19:0] dual_rx;          // 每沿按 DA、DB 合并得到的字
    logic [19:0] expected;         // 直接由接口需求选择结果或测试常数
    logic        single_format;   // 单 lane 字长和未用数据输出
    logic        dual_format;     // 双 lane 字长和未用装载槽

    ad4080_ddr_edge_words dut (
        .sample_i(sample_i),
        .dual_lane_i(dual_lane_i),
        .intf_check_i(intf_check_i),
        .da_fall_o(da_fall),
        .da_rise_o(da_rise),
        .db_fall_o(db_fall),
        .db_rise_o(db_rise),
        .slot_count_o(slots)
    );

    // =========================================================================
    // 2. 按图 74/75 反解全部输出
    // =========================================================================
    // 顺序写出首下降到末上升的全部槽。模式未生效的向量由格式性质另行检查。
    assign single_rx = {da_fall[0], da_rise[0], da_fall[1], da_rise[1],
                        da_fall[2], da_rise[2], da_fall[3], da_rise[3],
                        da_fall[4], da_rise[4], da_fall[5], da_rise[5],
                        da_fall[6], da_rise[6], da_fall[7], da_rise[7],
                        da_fall[8], da_rise[8], da_fall[9], da_rise[9]};
    assign dual_rx = {da_fall[0], db_fall[0], da_rise[0], db_rise[0],
                      da_fall[1], db_fall[1], da_rise[1], db_rise[1],
                      da_fall[2], db_fall[2], da_rise[2], db_rise[2],
                      da_fall[3], db_fall[3], da_rise[3], db_rise[3],
                      da_fall[4], db_fall[4], da_rise[4], db_rise[4]};

    // [判据] 同时检查向量内容和有效长度，错误填充不能由成功重组掩盖。
    assign expected = intf_check_i ? 20'hAC5D6 : sample_i;
    assign single_format = (slots == 4'd10) && ({db_fall, db_rise} == 20'b0);
    assign dual_format = (slots == 4'd5) &&
                         ({da_fall[9:5], da_rise[9:5], db_fall[9:5], db_rise[9:5]} == 20'b0);
    assign correct_o = dual_lane_i ? (dual_format && dual_rx == expected)
                                    : (single_format && single_rx == expected);
endmodule
