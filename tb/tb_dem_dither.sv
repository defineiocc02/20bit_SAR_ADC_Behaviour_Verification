`timescale 1ns/1ps
// DEM mask + Dither 预置 对拍（逐 k / 逐 word，与 Python 合同实现比）
//
// 端口约定（2026-11-15 起）：两个叶子模块的输出改为打包向量
//   dem_mask_v3.mask_o : bit = 20*s + j   （s=片 0..7，j=单元 0..19）
//   dither_pre.pre_o   : bit =  4*s + b   （s=片 0..7，b=开关位 0..3）
// 本 TB 在内部拆回二维数组再按原格式打印，因此
// tools/cmp_dem_dither.py 的比对口径**不需要改动**。
module tb_dem_dither;
    logic [9:0] k; logic [2:0] h, v; logic coin; logic [3:0] acq;
    logic [159:0] mask_flat; logic [31:0] pre_flat;
    logic [19:0] mask [0:7]; logic [3:0] pre [0:7];
    logic [5:0] word; logic [2:0] U; logic [3:0] f;
    integer errs = 0;

    dem_mask_v3 u_dem(.k_i(k), .h_phase_i(h), .v_phase_i(v), .coin_i(coin),
                      .acq_seq_i(acq), .mask_o(mask_flat));
    logic [6:0] sadc_pre;
    dither_pre u_pd(.word_i(word), .v_phase_i(v), .pre_o(pre_flat), .U_o(U), .f_o(f),
                    .sadc_pre_o(sadc_pre));

    // 拆包：位序与 RTL 源码里的唯一展开处一致
    genvar s;
    generate
        for (s = 0; s < 8; s++) begin : g_unpack
            assign mask[s] = mask_flat[20*s +: 20];
            assign pre[s]  = pre_flat[4*s +: 4];
        end
    endgenerate

    initial begin
        for (int kk = 0; kk <= 520; kk++)
            for (int hp = 0; hp < 8; hp++)
                for (int vp = 0; vp < 8; vp++)
                    for (int co = 0; co < 2; co++)
                    for (int hf = 0; hf < 2; hf++) begin
                        k = 10'(kk); h = 3'(hp); v = 3'(vp); coin = co[0];
                        acq = 4'(hf << 3);
                        #1;
                        $display("M %0d %0d %0d %0d %h %h %h %h %h %h %h %h",
                                 kk, hp, vp, co*2+hf,
                                 mask[0],mask[1],mask[2],mask[3],
                                 mask[4],mask[5],mask[6],mask[7]);
                    end
        for (int w = 0; w < 64; w++)
            for (int vp = 0; vp < 8; vp++) begin
                word = 6'(w); v = 3'(vp); #1;
                // 七个真实开关折算到 1/8 粗码单位，必须与该字的低段一致。
                // 高段 D6/D4 的变化不应进入前级量化器的采样控制。
                if (sadc_pre !== {1'b0, word[4], 1'b0, word[2:0], 1'b0})
                    $fatal(1, "SADC pre mismatch word=%0d phase=%0d", w, vp);
                $display("P %0d %0d %0d %0d %h %h %h %h %h %h %h %h",
                         w, vp, U, f,
                         pre[0],pre[1],pre[2],pre[3],
                         pre[4],pre[5],pre[6],pre[7]);
            end
        $finish;
    end
endmodule
