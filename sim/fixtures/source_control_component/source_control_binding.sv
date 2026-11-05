// 模块：source_control_binding；显式数学组件的转换开关绑定。
// 职责：按粗码、活动物理ID与冻结state生成布尔mask，返回已知公共Q输入项。
// 接口/单位：coarse为9位原码；mask为0/1；common为有符号V/Vfs Q(V_FRAC)。
// 时序：纯组合，无时钟或复位；调用方冻结同一笔word/state/IDs并在valid时接受。
// 异常：state7或未实现的状态请求返回invalid/error13；拒绝结果不得作正常零码。
// 使用边界：这是任意XOR数学夹具，非原芯片DEM、物理开关或扰动机制。
// 参数：几何/格式来自显式rtl_params.vh，不在本文件推断器件默认值。
`timescale 1ns/1ps
`include "rtl_params.vh"
module source_control_binding (
 input logic [8:0] coarse_resolved,
 input logic [CONTROL_WORD_BITS-1:0] control_word,
 input logic [STATE_BITS-1:0] state,
 input logic [N_ACTIVE-1:0][4:0] slice_ids,
 output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on,
 output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0] sub_on,
 output logic signed [V_BITS-1:0] known_common_q,
 output logic valid,
 output logic [31:0] error_code
);
 always_comb begin
  for (int a=0;a<int'(N_ACTIVE);a++) begin
   for (int u=0;u<int'(N_UNIT_MAIN);u++)
    main_on[a][u]=coarse_resolved[(u+int'(slice_ids[a]))%9] ^ state[a%int'(STATE_BITS)];
   for (int u=0;u<int'(N_UNIT_SUB);u++)
    sub_on[a][u]=coarse_resolved[(u+a+1)%9] ^ state[(a+1)%int'(STATE_BITS)];
  end
  known_common_q=$signed({{(V_BITS-CONTROL_WORD_BITS){1'b0}},control_word}) <<< 8;
  valid=(state != STATE_BITS'(7)) && (SOURCE_DEM_REQUEST != 0 || state == 0);
  error_code=valid ? 32'd0 : 32'd13;
 end
endmodule
