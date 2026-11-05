//=============================================================================
// @file    cnv_ctrl.sv
// @brief   已译码输入事件的边沿监视与外部忙状态检测。
// @details cnv_p 必须已由外部接收器译码。本模块只同步并监视逻辑事件，
//          不启动 SAR 转换，也不实现电气输入模式。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: [设计约定] 逻辑事件监视；AD4080 Rev.B p.33 仅提供外部 CNV 背景。
// 时钟域: cnv_p 经过两级同步寄存器；busy 必须属于 clk 域。
// 复位: rst_n 低有效、异步置位，由外部同步释放。
// 延迟: 输入经过两级同步；忙时事件错误再经过一个寄存沿。
// 吞吐: 调用方须保证脉宽可被同步采样；不支持任意窄脉冲。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: 不实现差分接收器、最小 tCONV 测量或转换启动集成。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none
module cnv_ctrl (
    input logic clk, rst_n, cnv_p, busy,
    output logic cnv_pulse, cnv_err
);
    (* ASYNC_REG="TRUE" *) logic input_meta, input_sync;
    logic previous, error_r;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin input_meta<=1'b0; input_sync<=1'b0; previous<=1'b0; error_r<=1'b0; end
        else begin
            input_meta<=cnv_p;
            input_sync<=input_meta;
            previous<=input_sync;
            error_r<=(input_sync && !previous) && busy;
        end
    end
    assign cnv_pulse=input_sync && !previous;
    assign cnv_err=error_r;
endmodule
`default_nettype wire
