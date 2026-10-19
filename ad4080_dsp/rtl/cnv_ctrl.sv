//=============================================================================
// @file    cnv_ctrl.sv
// @brief   CNV 输入选择、同步与 tCONV 违反检测
// @details CMOS/LVDS 由 0x16[0] 选择；2FF 同步后取上升沿；
//          忙时再次 CNV 记为 ADC_CNV_ERR（数字侧代理指标）。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 2.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.33, p.45, p.83
// 时钟域   : cnv_p/cnv_n 异步，2FF 同步
// 复位     : rst_n 异步低有效
// 延迟     : 同步 2 clk + 边沿检测 1 clk
// 吞吐     : 1 pulse/clk
// 可综合性 : 是
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版
//   2.0.0 2026-10-19 增加 busy 检测与 cnv_err
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.33 / p.45 / p.83
//     - CNV+ / CNV- 可配置为 CMOS（CNV- 接地）或 LVDS，由 0x16 bit0
//       LVDS_CNV_EN 选择。
//     - ADC_CNV_ERR（DEVICE_STATUS bit2）：用户违反最小 tCONV 时置位，
//       该次转换结果无效。
//
//   本模块只做数字选择 + 2FF 同步 + 上升沿检测 + "忙时又来 CNV" 检测；
//   LVDS 差分接收器/端接/时钟占空比不在数字范围内。
//
//   NOTE(choice): 错误判据 —— 上一次转换的 valid_in 尚未返回（busy=1）时又出现
//   CNV 上升沿，即视为违反 tCONV。这是数字侧可观测的代理指标，不等价于
//   模拟侧的建立时间测量。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module cnv_ctrl (
    input  logic clk,                                     // 系统时钟
    input  logic rst_n,                                   // 异步复位，低有效
    input  logic cnv_p,                                   // CNV 正端
    input  logic cnv_n,                                   // CNV 负端（LVDS 模式）
    input  logic lvds_en,                                 // 0 = CMOS / 1 = LVDS
    input  logic busy,        // 转换进行中（由上层维护）
    output logic cnv_pulse,                               // 同步后的上升沿，单拍
    output logic cnv_err                                  // 忙时又来 CNV 产生的单拍错误标志
);

    logic raw, s0, s1, s2;
    logic err_r;

    assign raw = lvds_en ? (cnv_p ^ cnv_n) : cnv_p;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s0 <= 1'b0; s1 <= 1'b0; s2 <= 1'b0; err_r <= 1'b0;
        end else begin
            s0 <= raw;
            s1 <= s0;
            s2 <= s1;
            err_r <= (s1 & ~s2) & busy;
        end
    end

    assign cnv_pulse = s1 & ~s2;
    assign cnv_err   = err_r;

endmodule

`default_nettype wire
