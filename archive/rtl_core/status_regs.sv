// 模块：status_regs；粘滞事件、逐拍clip状态及错误码组合回读。
// 端口/单位：输入为离散事件/状态；输出status_word[5:0]为六标志，[31:6]为err_code低26位。
// 时钟/复位：clk上升沿、同步低有效rst_n；clr对四个sticky位优先于同沿事件。
// 标志：sticky={analog_ovf,adc2_ovf,gain_err,acc_ovf}，只由reset/clr清除；clip_last逐拍跟随。
// 同沿规则：clip不受clr门控；本模块无valid资格，调用方须提供所需最近合法结果的clip电平。
// 回读：err_code直接组合透传，不在此保存；其来源、优先级与清除由顶层定义。
// ABI：flags[5:0]={analog,clip_high,clip_low,adc2,gain,acc}；status_clr_value=0x27不含clip。
// 来源：RTL_ARITHMETIC_CONTRACT.md §4与P2_INTERFACE.md §8；不重新判定数值范围。
`timescale 1ns/1ps
`include "rtl_params.vh"

module status_regs (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clr,             // 显式清除（清粘滞标志，不影响错误码）
    // 事件输入（各与 dout 同拍或独立）
    input  logic        ev_acc_ovf,
    input  logic        ev_gain_err,
    input  logic        ev_adc2_ovf,
    input  logic        ev_clip_low,
    input  logic        ev_clip_high,
    input  logic        ev_analog_ovf,   // rdac_ovf | adc2_ovf | ra_sat
    input  logic [31:0] err_code,        // 来自 calib_regs / weight_store
    output logic        acc_ovf_sticky,  // 粘滞，只由 clr / rst_n 解除
    output logic        gain_err_sticky,
    output logic        adc2_ovf_sticky,
    output logic        clip_low_last,
    output logic        clip_high_last,
    output logic        analog_ovf_sticky,
    output logic [31:0] status_word,     // {err_code, 6 个标志}
    output logic [31:0] status_clr_value
);

  localparam logic [31:0] CLR_MASK = {26'b0, 6'b100111};

  // 时序边界：仅六个状态位进入 FF，不在这里重新检测数值范围或增设流水级。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      acc_ovf_sticky    <= 1'b0;
      gain_err_sticky   <= 1'b0;
      adc2_ovf_sticky   <= 1'b0;
      analog_ovf_sticky <= 1'b0;
      clip_low_last     <= 1'b0;
      clip_high_last    <= 1'b0;
    end else begin
      if (clr) begin
        // 显式清除：4 个粘滞位一起归零。clip_* 不在其中（非粘滞，见模块头）。
        acc_ovf_sticky    <= 1'b0;
        gain_err_sticky   <= 1'b0;
        adc2_ovf_sticky   <= 1'b0;
        analog_ovf_sticky <= 1'b0;
      end else begin
        acc_ovf_sticky    <= acc_ovf_sticky    | ev_acc_ovf;
        gain_err_sticky   <= gain_err_sticky   | ev_gain_err;
        adc2_ovf_sticky   <= adc2_ovf_sticky   | ev_adc2_ovf;
        analog_ovf_sticky <= analog_ovf_sticky | ev_analog_ovf;
      end
      // 非粘滞：逐拍跟随（与"逐样本更新"等价，理由见模块头）
      clip_low_last  <= ev_clip_low;
      clip_high_last <= ev_clip_high;
    end
  end

  // 组合回读：截取 err_code 低 26 位，再按固定 ABI 顺序拼接六个标志。
  // The upper six project-code bits are intentionally outside this status ABI.
  wire unused_upper_error_bits = |err_code[31:26];
  assign status_word = {
    err_code[25:0],
    analog_ovf_sticky, clip_high_last, clip_low_last,
    adc2_ovf_sticky, gain_err_sticky, acc_ovf_sticky
  };

  assign status_clr_value = CLR_MASK;

endmodule
