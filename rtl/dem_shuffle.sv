// Fig.19-21 横/纵置换：保留列为固定零位，横向按 h_phase 轮转选列，纵向按 v_phase 轮转选片。
// 与 digital_chain._horizontal_columns / vertical_bits 逐位一致。
// 注：写成纯表达式，避免 always_comb 内的 automatic 变量（iverilog 不支持）。
module dem_shuffle (
    input  logic [8:0] k_i,
    input  logic [3:0] reserved_i,
    input  logic [3:0] h_phase_i,
    input  logic [2:0] v_phase_i,
    output logic [15:0] h_o,
    output logic [7:0]  y_o
);
    logic [3:0] H;
    logic [2:0] M;

    assign H = k_i[8:5];
    assign M = k_i[4:2];

    genvar c, s;
    generate
        for (c = 0; c < 16; c++) begin : g_col
            assign h_o[c] = (c[3:0] != reserved_i) &&
                            (((((c > reserved_i) ? (c - 1) : c) + 15 - h_phase_i) % 15) < H);
        end
        for (s = 0; s < 8; s++) begin : g_slice
            assign y_o[s] = (((s + 8 - v_phase_i) % 8) < M);
        end
    endgenerate
endmodule
