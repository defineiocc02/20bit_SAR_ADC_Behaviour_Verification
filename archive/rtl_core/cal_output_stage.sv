// 模块：cal_output_stage；转换结果、sample ID 与异常标志的同步提交。
// 端口/单位：candidate/dout 为无符号输出码；ID 为32位样本号；flags为离散状态位。
// 时钟/复位：clk 上升沿、同步低有效 rst_n；cfg_ready=0 取消同沿提交并清字/ID/valid/clip。
// 接受：cfg_ready&&complete 时 dout_valid 置1一拍，ID与本次 flags 原子更新。
// flags[4:0]={clip_high,clip_low,adc2_ovf,gain_err,acc_ovf}，与粘滞状态分开。
// 异常：acc_ovf/gain_err 仍提交ID/flags，但保持最后合法字和clip；单独adc2_ovf不阻止字更新。
// 因此valid是“本次计算完成”，不是“本字数值可信”；错误完成的ID/flags属于本次，
// 保留的dout却可能来自前次。下游必须按flags拒收，不能把旧字配给当前样本。
// 同沿优先级：reset最高；clr_ovf先清粘滞位，同沿新完成错误重新置位；配置取消最后覆盖。
// 来源：RTL_ARITHMETIC_CONTRACT.md §4；本模块不重新执行校准运算。
`timescale 1ns/1ps
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
        // clr_ovf只清历史粘滞状态，不取消当前完成；本沿新异常会在后文重新置位。
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
