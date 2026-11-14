`timescale 1ns/1ps
// DEM mask + Dither 预置 对拍（逐 k / 逐 word，与 Python 合同实现比）
module tb_dem_dither;
    logic [9:0] k; logic [2:0] h, v; logic coin; logic [3:0] acq;
    logic [19:0] mask [0:7];
    logic [5:0] word; logic [3:0] pre [0:7]; logic [2:0] U; logic [3:0] f;
    integer errs = 0;

    dem_mask_v3 u_dem(.k_i(k), .h_phase_i(h), .v_phase_i(v), .coin_i(coin),
                      .acq_seq_i(acq), .mask_o(mask));
    dither_pre u_pd(.word_i(word), .v_phase_i(v), .pre_o(pre), .U_o(U), .f_o(f));

    initial begin
        for (int kk = 0; kk <= 520; kk++)
            for (int hp = 0; hp < 8; hp++)
                for (int vp = 0; vp < 8; vp++)
                    for (int co = 0; co < 2; co++) begin
                        k = 10'(kk); h = 3'(hp); v = 3'(vp); coin = co[0]; acq = 4'd0;
                        #1;
                        $display("M %0d %0d %0d %0d %h %h %h %h %h %h %h %h",
                                 kk, hp, vp, co,
                                 mask[0],mask[1],mask[2],mask[3],
                                 mask[4],mask[5],mask[6],mask[7]);
                    end
        for (int w = 0; w < 64; w++)
            for (int vp = 0; vp < 8; vp++) begin
                word = 6'(w); v = 3'(vp); #1;
                $display("P %0d %0d %0d %0d %h %h %h %h %h %h %h %h",
                         w, vp, U, f,
                         pre[0],pre[1],pre[2],pre[3],
                         pre[4],pre[5],pre[6],pre[7]);
            end
        $finish;
    end
endmodule
