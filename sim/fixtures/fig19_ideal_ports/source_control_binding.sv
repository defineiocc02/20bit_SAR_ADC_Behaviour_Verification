// 模块：source_control_binding；Fig19理想端口实施例的已决码到RDAC转换mask。
// 职责：每个9位已决prefix执行k=c-(4*D6+D4)，合法时按显式63+8工程几何展开。
// 接口/单位：word[5:0]={D6..D1}是6位控制字，不是ADC1分辨率；common为有符号Q32。
// 时序：纯组合，无时钟/复位；current word/state/IDs须已按采集所有权冻结。
// 异常：每个prefix的k越出0..511均invalid/error13，不钳位；上层丢弃整笔异常样本。
// 校正域：common=-T与实际mask k配套；不得在其外再重复加减lower或upper。
// 来源：US10505561B2 PDF22图19、PDF33说明；负极性、63+8及Q32是显式工程绑定。
// 验证边界：只限定理想双节点与数字码路径；不是原芯片完整映射、真实注入电容或时序签核。
`timescale 1ns/1ps
`include "rtl_params.vh"
module source_control_binding (
    input wire [8:0] coarse_resolved,
    input wire [CONTROL_WORD_BITS-1:0] control_word,
    input wire [STATE_BITS-1:0] state,
    input wire [N_ACTIVE-1:0][4:0] slice_ids,
    output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on,
    output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0] sub_on,
    output wire signed [V_BITS-1:0] known_common_q,
    output wire valid,
    output wire [31:0] error_code
);
  initial begin
    if (N_UNIT_MAIN != 63 || N_UNIT_SUB != 8 || N_ACTIVE != 8)
      $fatal(1, "Fig19 canonical engineering instance requires explicit63+8/8 geometry");
  end
  wire signed [V_BITS-1:0] unused_sadc_shift_q;
  wire node_valid;
  wire [31:0] node_error;
  source_acquisition_binding u_nodes (
      .control_word(control_word), .state(state), .slice_ids(slice_ids),
      .sadc_input_shift_q(unused_sadc_shift_q), .rdac_input_shift_q(known_common_q),
      .valid(node_valid), .error_code(node_error)
  );
  wire [2:0] upper_code = (control_word[5] ? 3'd4 : 3'd0) +
      (control_word[3] ? 3'd1 : 3'd0);
  // The signed code is an observable of this supplied binding, not a final-code oracle.
  wire signed [10:0] rdac_code = $signed({2'b0, coarse_resolved}) -
      $signed({8'b0, upper_code});
  assign valid = node_valid && node_error == 0 && rdac_code >= 0 && rdac_code <= 511;
  assign error_code = valid ? 32'd0 : 32'd13;
  always_comb begin
    main_on = '0;
    sub_on = '0;
    if (valid) begin
      for (int a = 0; a < int'(N_ACTIVE); a++) begin
        for (int u = 0; u < int'(N_UNIT_MAIN); u++)
          main_on[a][u] = u < (int'(rdac_code) >>> 3);
        for (int u = 0; u < int'(N_UNIT_SUB); u++)
          sub_on[a][u] = u < (int'(rdac_code) & 7);
      end
    end
  end
endmodule
