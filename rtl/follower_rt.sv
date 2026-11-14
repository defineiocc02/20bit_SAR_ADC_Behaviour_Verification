// D2 Follower（实时版）：由比较器逐位判决驱动，不再从最终码反推。
//
// 事件序列：第 i 个事件接收一位已决码，累加进前缀并立刻输出该事件的 RDAC 码。
//   ev 0: 收到 bit8 -> k = {bit8, 7'b0}
//   ev 1: 收到 bit7 -> k = {bit8, bit7, 6'b0}
//   ...
//   ev 7: 收到 bit1 -> 8 位温度计码齐
//   ev 8: 子阵列 bit0（L 的最低位），k 的低位补齐
//
// 与 follower.sv 的区别：本模块**不看最终码**，只看每拍送进来的 bit_i。
module follower_rt (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        start_i,      // 一次转换开始，清空前缀
    input  logic        bit_valid_i,  // 该拍有一位已决码
    input  logic        bit_i,        // 已决码位
    input  logic [2:0]  upper_i,      // dither 的 U，用于码修正
    output logic        ev_valid_o,
    output logic [2:0]  ev_idx_o,     // 0..8
    output logic [8:0]  k_ev_o,       // 该事件驱动 RDAC 的码
    output logic        done_o
);

    logic [8:0] prefix;
    logic [3:0] idx;
    logic       done;

    // 第 idx 个事件接到的是原码的第 (8-idx) 位：ev0->bit8 ... ev8->bit0
    // 因此直接把该位放到它原来的位置，而不是移位寄存器。
    wire [8:0] bit_pos = 9'd1 << (4'd8 - idx);
    wire [8:0] acc     = prefix | (bit_i ? bit_pos : 9'd0);
    wire [8:0] k_ev = acc - {6'b0, upper_i};

    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            prefix <= 9'd0; idx <= 4'd0; done <= 1'b0;
        end else if (start_i) begin
            prefix <= 9'd0; idx <= 4'd0; done <= 1'b0;
        end else if (bit_valid_i && !done) begin
            prefix <= acc;
            idx    <= idx + 4'd1;
            if (idx == 4'd8) done <= 1'b1;
        end
    end

    assign ev_valid_o = bit_valid_i && !done;
    assign ev_idx_o   = idx[2:0];
    assign k_ev_o     = k_ev;
    assign done_o     = done;

endmodule
