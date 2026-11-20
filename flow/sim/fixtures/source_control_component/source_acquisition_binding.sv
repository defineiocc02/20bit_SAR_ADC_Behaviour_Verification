// 模块：source_acquisition_binding；显式数学组件的两个采集输入偏移。
// 接口/单位：SADC偏移为0、RDAC公共偏移为word<<8，均为有符号V/Vfs Q(V_FRAC)。
// 时序：纯组合，无时钟或复位；实际acquiring组由调用方冻结word/state/IDs。
// 资格：valid仅支持无源扰动请求；invalid值不能作为新采样或静默零值使用。
// 边界：这里只声明数学输入端口，不实现采样MOS、注入电容、保持或建立时间。
// 本组件不采用Fig19控制字；非零common项不能被误称为已复现dither。
`timescale 1ns/1ps
`include "rtl_params.vh"
module source_acquisition_binding (
 input logic [CONTROL_WORD_BITS-1:0] control_word,
 input logic [STATE_BITS-1:0] state,
 input logic [N_ACTIVE-1:0][4:0] slice_ids,
 output logic signed [V_BITS-1:0] sadc_input_shift_q,
 output logic signed [V_BITS-1:0] rdac_input_shift_q,
 output logic valid,
 output logic [31:0] error_code
);
 wire unused_component_indexes = ^{state, slice_ids};
 assign sadc_input_shift_q = '0;
 assign rdac_input_shift_q = $signed({{(V_BITS-CONTROL_WORD_BITS){1'b0}},control_word}) <<< 8;
 assign valid = SOURCE_DITHER_REQUEST == 0;
 assign error_code = valid ? 32'd0 : 32'd13;
endmodule
