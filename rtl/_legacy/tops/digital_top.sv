// D6 数字顶层：c -> (dither U) -> k -> CDAC 解码 + DEM 置换 -> mask/输出码
// 依赖：dither_split, cdac_decode, dem_shuffle, recon_d5
module digital_top (
    input  logic [8:0]  c_i,          // 量化器粗码
    input  logic [11:0] adc2_i,
    input  logic [5:0]  word_i,       // D6..D1
    input  logic [3:0]  reserved_i,
    input  logic [3:0]  h_phase_i,
    input  logic [2:0]  v_phase_i,
    output logic [8:0]  k_o,
    output logic [16:0] main_o,
    output logic [3:0]  sub_o,
    output logic [15:0] h_o,
    output logic [7:0]  y_o,
    output logic [19:0] code_o,
    output logic        overflow_o
);
    logic [2:0] upper;
    logic [4:0] lower_q3;
    logic [5:0] total_q3;

    dither_split u_dither(
        .word_i(word_i), .upper_o(upper),
        .lower_q3_o(lower_q3), .total_q3_o(total_q3));

    assign k_o = c_i - {6'b0, upper};

    cdac_decode u_cdac(.k_i(k_o), .main_o(main_o), .sub_o(sub_o));
    dem_shuffle u_dem(.k_i(k_o), .reserved_i(reserved_i),
                      .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
                      .h_o(h_o), .y_o(y_o));
    recon_d5 u_recon(.k_i(k_o), .adc2_i(adc2_i), .dither_i(word_i),
                     .code_o(code_o), .overflow_o(overflow_o));
endmodule
