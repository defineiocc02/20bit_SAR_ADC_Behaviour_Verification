//===========================================================================
// status_regs.sv -- 粘滞状态标志与错误码寄存器
//===========================================================================
// 职责一句话
//   把逐样本事件（溢出/剪裁/模拟饱和/结构性错误）按"粘滞 vs 逐样本"两种口径
//   汇总成一组状态位，并拼成一个 32 位 status_word 供上层回读。
//   本模块**不做**任何判定（判定在 recon_core / 顶层），只做寄存与粘滞。
//
// 来源
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §4.4（RTL 的行为约定）与 §4.6
//   （模拟溢出标志必须独立保留）；docs/rtl/P2_INTERFACE.md §8。
//   既有判据：tests/unit/test_fixed_point.py::test_output_saturation_is_separate_from_analog_saturation
//
// 单位契约
//   全为 0/1 状态位与 32 位整数错误码。无模拟量。
//
// 参数来源分级
//   本模块无模块参数（尺寸与标志个数都是接口契约的一部分，不是可调尺寸）。
//   错误码常量定义于 calib_regs.sv，本模块只**透传** err_code，不解释它。
//
// 契约与不变量 / 适用域
//   * **粘滞**（acc_ovf / gain_err / adc2_ovf / analog_ovf）：一旦置位，只由
//     `clr` 或 `rst_n` 解除。理由（契约 §4.4）：Python 侧对同一情形是 `raise`，
//     即"这次转换的结论不可信"；RTL 无法抛异常，粘滞标志是它在硬件上唯一诚实的
//     等价物 —— **不允许它自己消失**。
//   * **非粘滞**（clip_low_last / clip_high_last）：逐拍跟随 `ev_clip_*`。
//     做成粘滞会让"码到边"看起来像"芯片坏了"（P2 §8 明文）。
//     注意 recon_core 只在 `div_done` 那拍更新 clip_*，其余时间保持，故"逐拍跟随"
//     与"逐样本更新"等价。
//   * `clr` 只清 4 个粘滞标志，**不清** clip_*，也**不改** err_code。
//   * `status_word = {err_code[25:0], 6 个标志}` —— 位分配是本模块的**设计选择**
//     （P2 §8 只写了 `{err_code, 6 个标志}`，未给位序/截位口径）：
//         [5] analog_ovf_sticky  [4] clip_high_last  [3] clip_low_last
//         [2] adc2_ovf_sticky    [1] gain_err_sticky [0] acc_ovf_sticky
//          [31:6] = err_code[25:0]
//     选择 [5:0] 放标志是为了让"码到边/溢出"这类高频观察量落在低位。
//   * `status_clr_value` 定义为 6 个粘滞位的写-1-清掩码（32'h0000_003F）。
//     P2 §8 只给了端口名未给语义，这是设计选择；顶层用不上它，留给软件侧使用。
//===========================================================================
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

  localparam logic [31:0] CLR_MASK = {26'b0, 6'b111111};

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

  assign status_word = {err_code[25:0],
                        analog_ovf_sticky, clip_high_last, clip_low_last,
                        adc2_ovf_sticky, gain_err_sticky, acc_ovf_sticky};

  assign status_clr_value = CLR_MASK;

endmodule
