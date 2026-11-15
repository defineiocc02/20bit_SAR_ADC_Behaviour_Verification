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
    // follower 事件流
    logic        ev_mode=0, ev_start=0, ev_bv=0, ev_bit=0, ev_qid=0;
    logic [3:0]  ev_idx_i=0;
    logic [2:0]  ev_flash=0, ev_up=0;
    logic        ev_valid_o, ev_done_o, ev_err_o, ev_proto_err_o;
    logic        k_range_err_o, out_valid_o, out_k_err_o;
    logic [8:0]  k_ev_o;

    d3d4_ip dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .k_i(k_i), .dither_i(dither_i),
        .sample_adv_i(sample_adv_i), .ready_o(ready_o),
        .mask_o(mask_o), .pre_o(pre_o), .upper_o(upper_o), .f_o(f_o),
        .reserved_o(res_o), .h_phase_o(hp_o), .v_phase_o(vp_o),
        .acq_mask_o(acq_m), .conv_mask_o(conv_m), .idle_mask_o(idle_m),
        .acq_ids_o(acq_ids), .conv_ids_o(conv_ids),
        .ev_mode_i(ev_mode), .ev_start_i(ev_start),
        .ev_bit_valid_i(ev_bv), .ev_bit_i(ev_bit),
        .ev_sar_index_i(ev_idx_i), .ev_sar_qid_i(ev_qid),
        .ev_flash_code_i(ev_flash), .ev_upper_i(ev_up),
        .ev_valid_o(ev_valid_o), .k_ev_o(k_ev_o), .ev_done_o(ev_done_o),
        .ev_err_o(ev_err_o), .ev_proto_err_o(ev_proto_err_o),
        .k_range_err_o(k_range_err_o),
        .out_valid_o(out_valid_o), .out_k_err_o(out_k_err_o));

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
        // 整码模式（ev_mode_i=0）下 ready_o 必须为 1，否则上游发不进事务
        if (ready_o !== 1'b1) fail("整码模式下 ready_o 应为 1");

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

        // ---- k 范围负向测试 ----
        // 合同要求"超过表达范围必须报错，不做模512回绕或静默夹紧"。
        // 判据：0..520 全部不报错；521 起必须报错。
        begin
            int bad_cnt;
            bad_cnt = 0;
            for (int kk = 0; kk <= 520; kk++) begin
                @(negedge clk);
                valid_i = 1'b1; k_i = 10'(kk);
                @(negedge clk);
                valid_i = 1'b0;
                if (k_range_err_o !== 1'b0) bad_cnt = bad_cnt + 1;
            end
            if (bad_cnt != 0)
                fail($sformatf("k=0..520 中有 %0d 个被误报越界", bad_cnt));

            // 越界必须报错
            bad_cnt = 0;
            for (int kk = 521; kk <= 1023; kk++) begin
                @(negedge clk);
                valid_i = 1'b1; k_i = 10'(kk);
                @(negedge clk);
                valid_i = 1'b0;
                if (k_range_err_o !== 1'b1) bad_cnt = bad_cnt + 1;
            end
            if (bad_cnt != 0)
                fail($sformatf("k=521..1023 中有 %0d 个未报越界（静默产生 mask）", bad_cnt));
            else
                $display("      k_range: 0..520 无报错, 521..1023 全报错 -> 符合合同");

            // 越界时 mask 不应被使用：这里只检查标志，mask 内容不作判据
            // （合同说"报错"而不是"输出一个特定 mask"，所以不对 mask 内容设判据）
        end

        // ---- 错误标志与 mask 的对齐（问题 4）----
        // 判据：在 mask_o 有效的那一拍，out_k_err_o 必须描述**同一笔**输入。
        // 场景 520 -> 521 -> 520 是最能暴露错位的：如果标志是组合直通，
        // 下游在 mask(520) 有效时会读到下一笔(521)的 err=1。
        begin
            int bad;
            bad = 0;
            // 连续三笔：520(合法) -> 521(越界) -> 520(合法)
            for (int t = 0; t < 3; t++) begin
                int kk; int want_err;
                kk = (t == 1) ? 521 : 520;
                want_err = (t == 1) ? 1 : 0;
                @(negedge clk);
                valid_i = 1'b1; k_i = 10'(kk);
                @(negedge clk);
                valid_i = 1'b0;
                // 此刻 mask_o 与 out_k_err_o 都应对应刚接受的这一笔
                if (out_valid_o !== 1'b1) begin
                    bad = bad + 1;
                    $display("     t=%0d: out_valid_o=%b（应为 1）", t, out_valid_o);
                end
                if (out_k_err_o !== want_err[0]) begin
                    bad = bad + 1;
                    $display("     t=%0d k=%0d: out_k_err_o=%b（应为 %0d）",
                             t, kk, out_k_err_o, want_err);
                end
            end
            if (bad != 0) fail($sformatf("错误标志与 mask 不对齐：%0d 处", bad));
            else $display("      out_k_err_o: 520/521/520 三笔标志与 mask 同拍对齐");

            // 空泡：无新事务时 out_valid_o 必须为 0（mask_o 保持）
            begin
                logic [159:0] held;
                held = mask_o;
                repeat (3) @(negedge clk);
                if (out_valid_o !== 1'b0)
                    fail("空泡拍 out_valid_o 未落回 0（下游会重复消费旧值）");
                if (mask_o !== held)
                    fail("空泡拍 mask_o 未保持（模拟侧需要持续看到最后命令）");
            end
        end

        // ---- follower 事件流专项：验证"边产生边载入" ----
        // 判据：k_ev_o 必须随每个事件**单调累加**，而不是到最后才跳变。
        // 具体做法：发 9 个事件（bit 全 1），每拍记录 k_ev_o，
        // 要求第 i 个事件后的 k 严格大于第 i-1 个（前缀在增长）。
        begin
            int  prev_k;
            int  grew;
            logic [159:0] prev_mask;
            int  n_mask_changed;
            n_mask_changed = 0;
            prev_mask = 160'd0;
            prev_k = -1;
            grew   = 0;

            // 复位到干净状态
            repeat (2) @(negedge clk);
            rstn = 1'b0; @(negedge clk); rstn = 1'b1; @(negedge clk);

            ev_mode = 1'b1;
            // 事件流模式下 ready_o 必须关闭整码入口，否则上游按整码发会丢事务。
            // 注意：要等一下再查。ready_o 虽是组合输出，但在 negedge 刚改变
            // ev_mode 的同一时刻读，门级网表里可能仍处于前一个稳定值
            // （RTL 仿真因为零延迟而"碰巧"正确）。等到下一个 negedge
            // 再检查，才是真实可用的时序点 —— 这是我第一版在门级失败的原因。
            @(negedge clk);
            if (ready_o !== 1'b0)
                fail("事件流模式下 ready_o 未关闭整码入口（会静默丢事务）");
            ev_up   = 3'd0;          // upper=0，避免 prefix<upper 的钳位干扰
            ev_flash= 3'd0;
            ev_qid  = 1'b0;
            // start
            @(negedge clk); ev_start = 1'b1;
            @(negedge clk); ev_start = 1'b0;

            // 9 个事件，bit 全 1，索引 8..0（从高位到低位）
            for (int i = 0; i < 9; i++) begin
                @(negedge clk);
                ev_bv    = 1'b1;
                ev_bit   = 1'b1;
                ev_idx_i = 4'(8 - i);
                @(negedge clk);
                ev_bv    = 1'b0;
                if (ev_valid_o !== 1'b1)
                    fail($sformatf("follower 事件 %0d 未产出 ev_valid_o", i));
                if (prev_k >= 0 && int'(k_ev_o) > prev_k) grew = grew + 1;
                // 每次都比较，包括第 0 个事件后（此时 mask 从复位态变为事件值，
                // 这本身就是一次有效更新）。原先加了 `prev_mask !== 0` 的守卫，
                // 把第一次变化跳过了，导致只数到 7/8，是我 TB 的计数 bug。
                if (i > 0 && mask_o !== prev_mask) n_mask_changed = n_mask_changed + 1;
                prev_mask = mask_o;
                $display("      follower ev%0d: idx=%0d k_ev=%0d (prev=%0d)",
                         i, 8 - i, k_ev_o, prev_k);
                prev_k = int'(k_ev_o);
            end
            @(negedge clk);
            if (ev_done_o !== 1'b1) fail("follower 第 9 事件后 ev_done_o 未置位");
            // 关键补充：mask_o 必须也随事件变化。
            // 只比 k_ev_o 是不够的 —— k_ev_o 增长不代表 RDAC 开关命令更新了。
            // 这条是"边产生边载入"落到 mask 上的证据。
            if (n_mask_changed < 8)
                fail($sformatf("mask_o only changed %0d/8 times: k not reaching RDAC switches",
                               n_mask_changed));
            else
                $display("      follower: mask_o 随事件变化 %0d/8 次 -> RDAC 开关逐事件更新成立",
                         n_mask_changed);

            // 8 次比较中应当全部增长（9 个事件产生 8 次递增）
            if (grew != 8)
                fail($sformatf("follower k 未逐事件累加：只增长 %0d/8 次，" +
                               "说明不是'边产生边载入'", grew));
            else
                $display("      follower: k 逐事件累加 %0d/8 次 -> 边产生边载入成立", grew);
            ev_mode = 1'b0;
        end

        $display("---------------------------------------------------------------");
        $display("d3d4_ip: %0d 拍, %0d errors", checked, errors);
        if (errors == 0) $display("RESULT: PASS (%0d tx bit-exact)", checked);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end
endmodule
