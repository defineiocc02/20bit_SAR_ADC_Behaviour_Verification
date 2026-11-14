// D4 Split Dither：6 位分路 -> 8 片物理采样预置开关位（合同 v2.1 §7）
//
// U  = 4*D6 + D4
// Ld = 2*D5 + (4*D3 + 2*D2 + D1)/8
// J  = U + 2*D5,  f = 4*D3 + 2*D2 + D1
// pre_value[s] = J + ((s - v_phase) % 8 < f)
// 展开成 4 位 [4,2,1,1bal]；值 8 用 1111
module dither_pre (
    input  logic [5:0]  word_i,       // D1..D6
    input  logic [2:0]  v_phase_i,
    output logic [3:0]  pre_o [0:7],  // 每片 4 个物理开关位
    output logic [2:0]  U_o,
    output logic [3:0]  f_o
);
    logic [2:0] d5;
    logic [2:0] J;
    logic [3:0] val;

    assign U_o = {1'b0, word_i[5]} * 3'd4 + {2'b0, word_i[3]};
    assign d5  = {2'b0, word_i[4]};
    assign f_o = {1'b0, word_i[2], word_i[1], word_i[0]};
    assign J   = U_o + {d5, 1'b0};            // U + 2*D5

    always_comb begin
        for (int s = 0; s < 8; s++) begin
            val = {1'b0, J} + (((s - v_phase_i) & 3'd7) < f_o ? 4'd1 : 4'd0);
            pre_o[s] = (val >= 4'd8) ? 4'b1111 : {val[2:0], 1'b0};  // [b2,b1,b0,bal]
        end
    end
endmodule
