// Split Dither 分路（US10505561B2 Fig.19，八片工程模式分母 8）：
//   U = 4*D6 + D4
//   L = 2*D5 + (4*D3 + 2*D2 + D1) / 8
//   T = U + L
// 全部量以 Q3（1/8 粗 LSB）整数表示，避免浮点。
module dither_split (
    input  logic [5:0] word_i,     // D6..D1，D6 为 MSB
    output logic [2:0] upper_o,    // U，单位粗 LSB
    output logic [4:0] lower_q3_o, // L*8
    output logic [5:0] total_q3_o  // T*8
);
    logic d6,d5,d4,d3,d2,d1;   // 各 1 位：此前误声明为 [5:0]，拼接变 36 位
    assign {d6,d5,d4,d3,d2,d1} = word_i;

    // U = 4*D6 + D4  ->  bit2 = D6, bit1 = 0, bit0 = D4
    assign upper_o    = {d6, 1'b0, d4};
    // L*8 = 16*D5 + 4*D3 + 2*D2 + D1  ->  bit4..bit0 = D5,0,D3,D2,D1
    assign lower_q3_o = {d5, 1'b0, d3, d2, d1};
    assign total_q3_o = {upper_o, 3'b0} + lower_q3_o;           // 8U + L*8
endmodule
