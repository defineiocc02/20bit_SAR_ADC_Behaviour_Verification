`timescale 1ns/1ps
// dem_phase_gen 严格对拍：复位态 + 逐拍推进，全部与金标比对
//
// 判据（任一不满足即 FAIL）：
//   1) 复位释放后的输出 == 金标第 0 行（= 种子 SEED 对应的相位，确定起点）
//   2) 每次 sample_adv 后的输出 == 金标第 n+1 行
//   3) sample_adv=0 时输出不得变化（证明"只在事务接受时推进"）
//   4) 三轴对称覆盖 1920 组三元组（证明随机性满覆盖）
//
// 金标：tb/vec_phase_rtl.txt（由 tools/gen_vec_phase.py 用 dem_phases_rtl 生成）
// 注意：旧版 TB 把"posedge 后 #1 的读数"记成 n=0，等效把整条序列偏移一拍；
//       本版显式区分"复位态"与"第 n 次推进后"，不再依赖采样时刻的巧合。
module tb_dem_phase_gen;
    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rstn = 1'b0, adv = 1'b0;
    logic [3:0] res, hp, nres, nhp;
    logic [2:0] vp, nvp;
    logic [31:0] rng, nrng;

    dem_phase_gen dut(
        .clk(clk), .reset_n(rstn), .sample_adv(adv),
        .reserved_o(res), .h_phase_o(hp), .v_phase_o(vp), .rng_o(rng),
        .nxt_reserved_o(nres), .nxt_h_phase_o(nhp),
        .nxt_v_phase_o(nvp), .nxt_rng_o(nrng));

    int errors = 0;
    int checked = 0;
    int N_VEC = 17280;

    int v_res[0:17279];
    int v_hp[0:17279];
    int v_vp[0:17279];

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 20) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    // 三轴对称覆盖：用一张 1920 位的位图记录出现过哪些三元组
    logic [1919:0] covered;
    task automatic mark_cover();
        int idx;
        idx = res * 120 + hp * 8 + vp;
        if (idx < 1920) covered[idx] = 1'b1;
    endtask

    initial begin
        int fd, r, n, i0, i1, i2, i3;
        int ncov;
        covered = '0;

        fd = $fopen("vec_phase_rtl.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_phase_rtl.txt");
            $finish;
        end
        n = 0;
        while (!$feof(fd) && n < N_VEC) begin
            r = $fscanf(fd, "%d %d %d %d\n", i0, i1, i2, i3);
            if (r == 4) begin
                v_res[n] = i1;   // seq 由 i0 给出，这里按到达顺序存，不依赖 seq 值
                v_hp[n]  = i2;
                v_vp[n]  = i3;
                n = n + 1;
            end
        end
        $fclose(fd);

        // ---- 复位态（第 0 行）----
        repeat (3) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);
        if (res !== 4'(v_res[0]) || hp !== 4'(v_hp[0]) || vp !== 3'(v_vp[0]))
            fail($sformatf("复位态不匹配: got %0d %0d %0d, want %0d %0d %0d",
                           res, hp, vp, v_res[0], v_hp[0], v_vp[0]));
        mark_cover();
        checked = 1;

        // ---- 逐拍推进 ----
        for (int k = 1; k < N_VEC; k++) begin
            logic [3:0] pr; logic [3:0] ph; logic [2:0] pv;
            pr = nres; ph = nhp; pv = nvp;   // 推进前记录的"下一态"
            adv = 1'b1;
            @(negedge clk);
            adv = 1'b0;
            @(negedge clk);
            checked = checked + 1;
            // nxt_* 必须与本拍实际输出一致，否则 d3d4_ip 取用 nxt 会错拍
            if (res !== pr || hp !== ph || vp !== pv)
                fail($sformatf("第 %0d 拍 nxt 口与实际输出不一致", k));
            mark_cover();
            if (res !== 4'(v_res[k]) || hp !== 4'(v_hp[k]) || vp !== 3'(v_vp[k])) begin
                fail($sformatf("第 %0d 拍不匹配: got %0d %0d %0d, want %0d %0d %0d",
                               k, res, hp, vp, v_res[k], v_hp[k], v_vp[k]));
            end
        end

        // ---- sample_adv=0 时输出必须冻结 ----
        begin
            logic [3:0] r0; logic [3:0] h0; logic [2:0] v0;
            r0 = res; h0 = hp; v0 = vp;
            repeat (5) @(negedge clk);
            if (res !== r0 || hp !== h0 || vp !== v0)
                fail("sample_adv=0 期间相位发生了漂移");
        end

        // ---- 三轴覆盖 ----
        ncov = 0;
        for (int q = 0; q < 1920; q++) if (covered[q]) ncov = ncov + 1;
        $display("三轴对称覆盖: %0d/1920", ncov);
        if (ncov != 1920) fail($sformatf("三轴对称覆盖不足 %0d/1920", ncov));

        $display("---------------------------------------------------------------");
        $display("dem_phase_gen: %0d 拍检查, %0d errors", checked, errors);
        if (errors == 0) $display("RESULT: PASS (%0d phases bit-exact)", checked);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end
endmodule
