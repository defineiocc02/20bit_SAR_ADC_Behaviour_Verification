//===========================================================================
// cal_output_stage.sv -- 每次转换的输出提交与粘滞异常（同步寄存器）
//===========================================================================
// 捕获：仅在 cfg_ready && complete 的上升沿提交候选结果、sample ID 和
// result_flags，并使 dout_valid 高一拍。此模块不重复计算校准算术。
// flags[4:0] = {clip_high, clip_low, adc2_ovf, gain_err, acc_ovf}；其中
// clip 位在候选 acc_ovf/gain_err 时屏蔽，ID 与标志仍标记这次完成的转换。
//
// 异常：acc_ovf/gain_err 时保留最后合法 dout 及 clip 状态，dout_valid 仍
// 产生完成事件。adc2_ovf 独立报告，不阻止字更新；下游必须结合 flags 判读。
// clr_ovf 清除三个粘滞错误；同沿有新的完成错误时，新错误优先重新置位。
// clr_ovf 不清除 clip 状态，clip 仅随合法字更新或配置取消复位。
//
// 复位/取消：rst_n 为同步低有效复位。cfg_ready == 0 的分支最后执行，
// 把输出字/ID/本次标志/valid/clip 清零，丢弃同沿完成；已积累的三个粘滞
// 错误只由 reset 或 clr_ovf 清除。无额外 FIFO，提交延迟为一个寄存器沿。
// 综合：只有寄存器和小量资格/屏蔽逻辑，不能通过删掉异常事件提高吞吐。
//===========================================================================
`include "rtl_params.vh"
module cal_output_stage #(
    parameter int P_OUT_BITS = int'(OUT_BITS)
) (
    input wire clk, rst_n, cfg_ready, clr_ovf, complete,
    input wire [P_OUT_BITS - 1:0] candidate,
    input wire [31:0] candidate_sample_id,
    input wire candidate_clip_low, candidate_clip_high,
    input wire candidate_acc_ovf, candidate_gain_err, candidate_adc2_ovf,
    output logic [P_OUT_BITS - 1:0] dout,
    output logic dout_valid, clip_low, clip_high,
    output logic acc_ovf, gain_err, adc2_ovf,
    output logic [31:0] result_sample_id,
    output logic [4:0] result_flags
);
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      dout <= '0;
      result_sample_id <= '0;
      result_flags <= '0;
      dout_valid <= 1'b0;
      clip_low <= 1'b0;
      clip_high <= 1'b0;
      acc_ovf <= 1'b0;
      gain_err <= 1'b0;
      adc2_ovf <= 1'b0;
    end else begin
      dout_valid <= 1'b0;
      if (clr_ovf) begin
        acc_ovf <= 1'b0;
        gain_err <= 1'b0;
        adc2_ovf <= 1'b0;
      end
      if (cfg_ready && complete) begin
        dout_valid <= 1'b1;
        result_sample_id <= candidate_sample_id;
        result_flags <= {
          !(candidate_acc_ovf || candidate_gain_err) && candidate_clip_high,
          !(candidate_acc_ovf || candidate_gain_err) && candidate_clip_low,
          candidate_adc2_ovf, candidate_gain_err, candidate_acc_ovf
        };
        adc2_ovf <= (clr_ovf ? 1'b0 : adc2_ovf) | candidate_adc2_ovf;
        if (candidate_acc_ovf)
          acc_ovf <= 1'b1;
        if (candidate_gain_err)
          gain_err <= 1'b1;
        if (!(candidate_acc_ovf || candidate_gain_err)) begin
          dout <= candidate;
          clip_low <= candidate_clip_low;
          clip_high <= candidate_clip_high;
        end
      end
      if (!cfg_ready) begin
        result_sample_id <= '0;
        result_flags <= '0;
        dout <= '0;
        dout_valid <= 1'b0;
        clip_low <= 1'b0;
        clip_high <= 1'b0;
      end
    end
  end
endmodule
