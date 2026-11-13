// CDAC 主/子阵列解码：9 位码 -> 17 个物理开关命令 + 4 位子阵列。
// 主阵列 = 15 个 2C（温度计）+ B4(1C) + S4(1C)；子阵列 = 8/4/2/1。
// 与 src/adi_model/digital_chain.py 的 _mask_bits 逐位一致。
module cdac_decode (
    input  logic [8:0]  k_i,
    output logic [16:0] main_o,   // [14:0]=2C 单元, [15]=B4, [16]=S4
    output logic [3:0]  sub_o
);
    logic [4:0] main_code;
    logic [4:0] therm, ones;

    assign main_code = k_i[8:4];
    assign therm = (main_code / 5'd2 > 5'd15) ? 5'd15 : main_code[4:1];
    assign ones  = (main_code - {therm,1'b0} > 5'd2) ? 5'd2 : (main_code - {therm,1'b0});

    always_comb begin
        main_o = 17'd0;
        for (int i = 0; i < 15; i++)
            if (i < therm) main_o[i] = 1'b1;
        if (ones >= 1) main_o[15] = 1'b1;
        if (ones >= 2) main_o[16] = 1'b1;
        sub_o = k_i[3:0];
    end
endmodule
