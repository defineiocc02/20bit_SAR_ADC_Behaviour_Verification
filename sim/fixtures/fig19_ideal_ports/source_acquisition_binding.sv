// 模块：source_acquisition_binding；Fig19理想lower/total两个采集节点的显式译码。
// 职责：word[5:0]={D6,D5,D4,D3,D2,D1}；U=4*D6+D4，T=L+U*Delta。
// 接口/单位：输出分别为加到SADC采样输入的-L、加到全部RDAC采样单元的-T；V/Vfs Q32。
// 精度：Delta/Vfs定标为2^24；D3/D2/D1的整数权重为9586981/4793490/2396745。
// 时序：纯组合，无时钟/复位；两个输出须由同一acquiring word和valid资格控制。
// 异常：仅支持state0、无DEM；未使能dither时仅word0合法，不提供隐式活动模式回退。
// 来源：US10505561B2 PDF22图19、PDF33；七slice是说明例，不用于宣称目标芯片几何。
// 工程边界：负等效极性、理想电压节点与Q32是ASSUMED绑定；不实现物理电容/噪声/建立。
`timescale 1ns/1ps
`include "rtl_params.vh"
module source_acquisition_binding (
    input wire [CONTROL_WORD_BITS-1:0] control_word,
    input wire [STATE_BITS-1:0] state,
    input wire [N_ACTIVE-1:0][4:0] slice_ids,
    output wire signed [V_BITS-1:0] sadc_input_shift_q,
    output wire signed [V_BITS-1:0] rdac_input_shift_q,
    output wire valid,
    output wire [31:0] error_code
);
  initial begin
    if (CONTROL_WORD_BITS != 6 || STATE_BITS != 1 || V_BITS != 64 || V_FRAC != 32)
      $fatal(1, "Fig19 binding: explicit6bit word,1bit state,Q32/64 required");
  end
  wire unused_slice_ids = ^slice_ids;
  wire signed [63:0] lower_q =
      (control_word[4] ? 64'sd33554432 : 64'sd0) +
      (control_word[2] ? 64'sd9586981 : 64'sd0) +
      (control_word[1] ? 64'sd4793490 : 64'sd0) +
      (control_word[0] ? 64'sd2396745 : 64'sd0);
  wire signed [63:0] upper_q =
      (control_word[5] ? 64'sd67108864 : 64'sd0) +
      (control_word[3] ? 64'sd16777216 : 64'sd0);
  assign valid = state == 0 && SOURCE_DEM_REQUEST == 0 &&
      (SOURCE_DITHER_REQUEST != 0 || control_word == 0);
  assign sadc_input_shift_q = -lower_q;
  assign rdac_input_shift_q = -(lower_q + upper_q);
  assign error_code = valid ? 32'd0 : 32'd13;
endmodule
