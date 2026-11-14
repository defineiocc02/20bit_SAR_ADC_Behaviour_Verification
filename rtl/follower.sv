// D2 Follower：7 个事件，第 i 个事件只使用已决前缀 (c >> (6-i)) << (6-i)。
// k_i = prefix_i - U。输出打包为 [62:0]，每 9 位一个事件（iverilog 不支持
// always_comb 驱动非打包数组输出端口）。
module follower (
    input  logic [8:0]  c_i,
    input  logic [2:0]  upper_i,
    output logic [62:0] k_ev_o
);
    genvar i;
    generate
        for (i = 0; i < 7; i++) begin : g_ev
            assign k_ev_o[i*9 +: 9] = ((c_i >> (6 - i)) << (6 - i)) - {6'b0, upper_i};
        end
    endgenerate
endmodule
