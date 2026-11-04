//=============================================================================
// @file    cnv_ctrl.sv
// @brief   CNV逻辑电平同步、上升沿与忙时错误代理
// @details 数字代理以cnv_p表示接收后的逻辑正相；cnv_n/lvds_en保留电气接口描述。
//          合法差分互补关系不能用XOR译成数据。真实差分接收器、共模和端接不在RTL中。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : cnv_p异步，经两级同步后做边沿检测；busy属于clk域
// 复位     : rst_n异步低有效；释放必须满足系统复位合同
// 延迟     : cnv_p同步后产生单clk上升沿脉冲；cnv_err比该脉冲再寄存1clk
// 吞吐     : 由同步器可观测稳定窗口和模拟转换周期约束，不保证任意窄脉冲捕获
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无
// 边界     : CMOS模式cnv_n可接低；LVDS数字测试应提供稳定互补输入。
//          busy时再次CNV只报代理错误；有效模拟建立时间需另外验证。
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

    // cnv_p is the decoded logical positive phase in both digital modes.
    // A physical differential receiver must be supplied outside this module.
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
