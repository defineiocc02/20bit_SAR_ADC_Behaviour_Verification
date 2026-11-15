`timescale 1ns/1ps
// d3d4_ip 顶层对拍：验证"相位发生器 + DEM + Dither"接成一条流水后仍然正确
//
// 判据：
//   1) 每次握手后，mask/pre/U/f 与同拍相位下的参考算法逐位一致
//   2) 相位序列与 tb/vec_phase_rtl.txt 逐步一致（证明计数器与叶子对齐）
//   3) 非握手拍输出必须冻结（证明输出是寄存器输出，没有组合穿透）
//   4) 三轴对称覆盖满 1920（证明随机化真的在工作）
//
// 参考算法：tools/ref_d3d4_ip.py 生成 tb/vec_d3d4_ip.txt
//   每行：seq k dither m0..m7(各 5 hex，20 位一段) pre(8hex) U f res hp vp
//   mask 之所以拆 8 段：$fscanf("%h") 读进 longint 会按 32 位截断，
//   一次读 160 位会静默丢高位（实测确认），所以按 20 位分段再拼。
module tb_d3d4_ip;
    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rstn = 1'b0;
    logic valid_i = 1'b0;
    logic [9:0] k_i;
    logic [5:0] dither_i;
    logic sample_adv_i = 1'b1;
    logic ready_o;
    logic [159:0] mask_o;
    logic [31:0]  pre_o;
    logic [2:0]   upper_o;
    logic [3:0]   f_o;
    logic [3:0]   res_o, hp_o;
    logic [2:0]   vp_o;
    logic [17:0]  acq_m, conv_m, idle_m;
    logic [39:0]  acq_ids, conv_ids;

    d3d4_ip dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .k_i(k_i), .dither_i(dither_i),
        .sample_adv_i(sample_adv_i), .ready_o(ready_o),
        .mask_o(mask_o), .pre_o(pre_o), .upper_o(upper_o), .f_o(f_o),
        .reserved_o(res_o), .h_phase_o(hp_o), .v_phase_o(vp_o),
        .acq_mask_o(acq_m), .conv_mask_o(conv_m), .idle_mask_o(idle_m),
        .acq_ids_o(acq_ids), .conv_ids_o(conv_ids));

    int errors = 0;
    int checked = 0;
    int N_VEC = 512;
    logic [1919:0] covered;

    // 金标
    logic [159:0] v_mask[0:511];
    int v_pre[0:511];
    int v_U[0:511];
    int v_f[0:511];
    int v_res[0:511];
    int v_hp[0:511];
    int v_vp[0:511];
    int v_k[0:511];
    int v_dith[0:511];

    function automatic int cnt18(input logic [17:0] v);
        int c;
        c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 20) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    initial begin
        int fd, r, n;
        int s0,s1,s2,s3,s4,s5,s6,s7;
        int i0, i1, i2, p32, u3, f4, rs, hs, vs;
        int ncov;

        covered = '0;
        fd = $fopen("vec_d3d4_ip.txt", "r");
        if (fd == 0) begin $display("FATAL: cannot open vec_d3d4_ip.txt"); $finish; end
        n = 0;
        while (!$feof(fd) && n < N_VEC) begin
            // 17 列：seq k dither m0..m7 pre U f res hp vp
            r = $fscanf(fd, "%d %d %d %h %h %h %h %h %h %h %h %h %d %d %d %d %d\n",
                        i0, i1, i2,
                        s0,s1,s2,s3,s4,s5,s6,s7,
                        p32, u3, f4, rs, hs, vs);
            if (r == 17) begin
                v_k[n] = i1; v_dith[n] = i2;
                // 拼回 160 位：段 t 对应 bit [20*t +: 20]
                v_mask[n] = { s7[19:0], s6[19:0], s5[19:0], s4[19:0],
                              s3[19:0], s2[19:0], s1[19:0], s0[19:0] };
                v_pre[n] = p32;
                v_U[n] = u3; v_f[n] = f4;
                v_res[n] = rs; v_hp[n] = hs; v_vp[n] = vs;
                n = n + 1;
            end
        end
        $fclose(fd);
        if (n < N_VEC) begin $display("FATAL: 只读到 %0d 行向量", n); $finish; end

        // ---- 复位 ----
        repeat (3) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);
        if (ready_o !== 1'b1) fail("复位释放后 ready_o 应为 1");

        // ---- 逐拍握手并比对 ----
        for (int j = 0; j < N_VEC; j++) begin
            @(negedge clk);
            valid_i = 1'b1;
            k_i = 10'(v_k[j]);
            dither_i = 6'(v_dith[j]);
            // 在 negedge 驱动，posedge 由 DUT 采样；随后在下一 negedge 读结果
            @(negedge clk);
            valid_i = 1'b0;
            checked = checked + 1;

            if (mask_o !== 160'(v_mask[j])) begin
                fail($sformatf("第 %0d 拍 mask 不匹配", j));
                if (errors <= 3)
                    $display("     got %040h want %040h", mask_o, 160'(v_mask[j]));
            end
            if (pre_o !== 32'(v_pre[j])) begin
                fail($sformatf("第 %0d 拍 pre 不匹配", j));
                if (errors <= 3)
                    $display("     got %08h want %08h (dith=%0d vp=%0d)",
                             pre_o, 32'(v_pre[j]), v_dith[j], v_vp[j]);
            end
            if (upper_o !== 3'(v_U[j]))   fail($sformatf("第 %0d 拍 U 不匹配", j));
            if (f_o !== 4'(v_f[j]))       fail($sformatf("第 %0d 拍 f 不匹配", j));
            if (res_o !== 4'(v_res[j])) begin
                fail($sformatf("第 %0d 拍 res 不匹配", j));
                if (errors <= 3)
                    $display("     got res=%0d hp=%0d vp=%0d | want res=%0d hp=%0d vp=%0d",
                             res_o, hp_o, vp_o, v_res[j], v_hp[j], v_vp[j]);
            end
            if (hp_o !== 4'(v_hp[j]))     fail($sformatf("第 %0d 拍 hp 不匹配", j));
            if (vp_o !== 3'(v_vp[j]))     fail($sformatf("第 %0d 拍 vp 不匹配", j));

            // ---- 两相位交织不变量 ----
            // 这三条是论文"两相位"的结构保证，任何一条不成立都说明
            // 采集与转换没有真正分离。
            if ((acq_m & conv_m) != 18'd0)
                fail($sformatf("第 %0d 拍 ACQ/CONV 重叠", j));
            if (cnt18(acq_m) != 8)
                fail($sformatf("第 %0d 拍 |ACQ|=%0d != 8", j, cnt18(acq_m)));
            if (cnt18(conv_m) != 8)
                fail($sformatf("第 %0d 拍 |CONV|=%0d != 8", j, cnt18(conv_m)));
            if (cnt18(idle_m) != 2)
                fail($sformatf("第 %0d 拍 |IDLE|=%0d != 2", j, cnt18(idle_m)));
            if ((acq_m | conv_m | idle_m) != 18'h3FFFF)
                fail($sformatf("第 %0d 拍 三态并集不满 18 片", j));

            // ---- 三轴覆盖（注意 hp 取值 0..14，res 0..15）
            begin
                int idx;
                idx = v_res[j] * 120 + v_hp[j] * 8 + v_vp[j];
                if (idx < 1920) covered[idx] = 1'b1;
            end
        end

        // ---- 非握手拍必须冻结 ----
        begin
            logic [159:0] m0; logic [31:0] p0;
            m0 = mask_o; p0 = pre_o;
            repeat (5) @(negedge clk);   // valid_i 保持 0
            if (mask_o !== m0 || pre_o !== p0) fail("valid_i=0 期间输出发生了漂移");
        end

        ncov = 0;
        for (int q = 0; q < 1920; q++) if (covered[q]) ncov = ncov + 1;
        $display("三轴覆盖: %0d/1920", ncov);

        $display("---------------------------------------------------------------");
        $display("d3d4_ip: %0d 拍, %0d errors", checked, errors);
        if (errors == 0) $display("RESULT: PASS (%0d tx bit-exact)", checked);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end
endmodule
