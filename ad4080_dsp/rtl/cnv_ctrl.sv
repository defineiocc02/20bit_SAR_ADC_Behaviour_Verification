//=============================================================================
// @file    cnv_ctrl.sv
// @brief   CNV 逻辑电平同步、上升沿与忙时错误代理。
// @details 两种数字模式均以 cnv_p 作为接收后逻辑正相；cnv_n/lvds_en 保留电气
//          接口描述。合法互补输入不能用 XOR 译码；真实差分接收、共模和端接由外部提供。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: cnv_p 异步，经两级同步后检测边沿；busy 属于 clk 域。
// 复位: rst_n 异步低有效，释放须满足系统复位约定。
// 延迟: 同步后产生单 clk 上升沿脉冲；cnv_err 再经过一个寄存沿。
// 吞吐: 受同步器可观测稳定窗口和模拟转换周期约束，不保证捕获任意窄脉冲。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: CMOS 模式 cnv_n 可接低；LVDS 数字测试须提供稳定互补输入。
//     busy 时再次 CNV 只报告代理错误，模拟建立时间需另行验证。
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

    // 两种数字模式都将 cnv_p 视为译码后的逻辑正相；物理差分接收器须由外部提供。
    assign raw = cnv_p;

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
