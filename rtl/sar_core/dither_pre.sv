`timescale 1ns/1ps
// =============================================================================
// 文件名     : dither_pre.sv
// 模块名     : dither_pre
// 功能       : 将同一六位字拆为 SADC 低段及八个 RDAC 槽的采样预置。
// 设计依据   : 工程合同 B1、B6，RDAC 正向采样极性。
// =============================================================================
//
// U  = 4*D6 + D4
// Ld = 2*D5 + (4*D3 + 2*D2 + D1)/8
// J  = U + 2*D5,  f = 4*D3 + 2*D2 + D1
// pre_value[s] = J + ((s - v_phase) % 8 < f)
// 展开成 4 位 [4,2,1,1bal]；值 8 用 1111
//
// 端口打包约定（2026-11-15）
// -------------------------
// 原版 pre_o 是 `output logic [3:0] pre_o [0:7]`（非打包数组）。
// 与 dem_mask_v3 同理，DC 会把它展平且顺序与源码下标相反，门级易错位。
// 现固定为打包向量：
//
//     pre_o[4*s + b] = 第 s 片（0..7）的第 b 个开关位（b=0..3）
//
// 内部保留二维视图 pre_v[][]，位序只在最后展开处定义一次。
module dither_pre (
    input  logic [5:0]  word_i,       // D1..D6
    input  logic [2:0]  v_phase_i,
    output logic [31:0] pre_o,        // 8 片 × 4 位，bit = 4*s + b
    output logic [2:0]  U_o,
    output logic [3:0]  f_o,
    output logic [6:0]  sadc_pre_o    // SADC 七个子开关，bit6 至 bit0 按 B1 顺序
);
    logic [2:0] d5;
    logic [2:0] J;
    logic [3:0] val;

    assign U_o = {1'b0, word_i[5]} * 3'd4 + {2'b0, word_i[3]};
    assign d5  = {2'b0, word_i[4]};
    assign f_o = {1'b0, word_i[2], word_i[1], word_i[0]};
    assign J   = U_o + 3'({d5, 1'b0});        // U + 2*D5 最大为 7。
    // SADC 子支路从 bit6 到 bit0 折为 4、2、1、1/2、1/4、1/8、1/8。
    // 只接入 D5、D3、D2、D1，名义偏移为 2D5 + f/8；U 仅在 RDAC 两个阶段使用。
    assign sadc_pre_o = {1'b0, word_i[4], 1'b0, word_i[2:0], 1'b0};

    logic [3:0] pre_v [0:7];

    always_comb begin
        for (int s = 0; s < 8; s++) begin
            logic [2:0] relative_slot;
            relative_slot = 3'(s) - v_phase_i; // 三位模减法先回绕，再与四位 f 比较。
            val = {1'b0, J} + ({1'b0, relative_slot} < f_o ? 4'd1 : 4'd0);
            // 位序必须是 pre_v[s][3]=b2, [2]=b1, [1]=b0, [0]=bal（MSB-first），
            // 与参考实现 dem_dither.pre() 返回的 [b2,b1,b0,bal] 逐位对应。
            // 依据：tools/cmp_dem_dither.py 的 hb4() 把 hex 按 (3-i) 还原成 list，
            // 即它把打印出的 4 位 hex 的**最高位**当作 bits[0]=b2。
            // 原写法 {val[2:0], 1'b0} 在 SystemVerilog 里是
            //   [3]=val[2]=b2, [2]=val[1]=b1, [1]=val[0]=b0, [0]=0=bal
            // 即 [bal,b0,b1,b2]——整条位序被倒过来，导致 pre 与 mask 对不上
            // （实测 got=aaaaa888 want=55555111，正是每 4 位反转）。
            // 这里显式按位赋值，避免再被拼接顺序绕进去。
            pre_v[s][3] = val[2];      // b2（4Cu）   <- 最高位
            pre_v[s][2] = val[1];      // b1（2Cu）
            pre_v[s][1] = val[0];      // b0（1Cu）
            pre_v[s][0] = 1'b0;        // bal（平衡/guard），普通转换置零
            if (val >= 4'd8) pre_v[s] = 4'b1111;   // 值 8 用 4+2+1+1 全选
        end
    end

    // 唯一的位序定义处：bit = 4*s + b
    genvar gs, gb;
    generate
        for (gs = 0; gs < 8; gs++) begin : g_pack_s
            for (gb = 0; gb < 4; gb++) begin : g_pack_b
                assign pre_o[4*gs + gb] = pre_v[gs][gb];
            end
        end
    endgenerate

endmodule
