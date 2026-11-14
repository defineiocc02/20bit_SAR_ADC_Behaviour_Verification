`timescale 1ns/1ps
// digital_top_w 记分板 TB
//
// 检查口径（不是只比总数）：
//   * 每笔事务带唯一 tag，随流水透传；
//   * 入口按 tag 记 sample_id / 片组 / dither / adc2 / Wm / G / inv；
//   * E1 比 tag_a, slice_a, adc2_a, dither_a（片 ID / dither 不串）
//   * E2 比 tag_b, wm_b, g_b, inv_reg（系数不串）
//   * 出口按 FIFO 顺序弹 tag，比 code / overflow / err（不丢、不重、不乱序）
//   * 恒等式 N_acc = N_ret + N_canc + N_pending，排空后 pending==0
//
// 期望值：
//   * 金标段用 tb/vec_topw_gold.txt（生产 Python run_transaction 算的）
//   * 随机段用本文件的 ref_model（与 Python 独立实现），并与金标段三方比对
//
// 场景：金标连发 / 随机气泡 / 随机 stall / 中途 reset / 零系数 err
module tb_digital_top_w;

    localparam int NVER   = 4;
    localparam int NSL    = 18;
    localparam int NU     = 20;
    localparam int NGOLD  = 1536;
    localparam int TAGMAX = 200000;

    // ---------------------------------------------------------------- clock
    logic clk = 1'b0;
    always #5 clk = ~clk;

    // ---------------------------------------------------------------- DUT
    logic        rstn = 1'b0;
    logic        valid_i = 1'b0, stall_i = 1'b0;
    logic [8:0]  c_i = '0;
    logic [11:0] adc2_i = '0;
    logic [5:0]  dither_i = '0;
    logic [3:0]  reserved_i = '0, h_phase_i = '0;
    logic [2:0]  v_phase_i = '0;
    logic [23:0] tag_i = '0;
    logic        in_ready, busy;
    logic [39:0] slice_ids;
    logic [19:0] code_o;
    logic        ovf_o, err_o, valid_o;
    logic [23:0] tag_o;
    logic        cfg_we = 1'b0, cfg_inv_we = 1'b0;
    logic [4:0]  cfg_slice = '0, cfg_unit = '0;
    logic [43:0] cfg_w = '0;
    logic [63:0] cfg_inv = '0;
    logic [3:0]  cfg_grp = '0;

    digital_top_w dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i),
        .reserved_i(reserved_i), .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
        .tag_i(tag_i), .in_ready_o(in_ready),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b0), .ev_start_i(1'b0), .ev_valid_i(1'b0), .ev_bit_i(1'b0),
        .sar_index_i(4'd0), .sar_qid_i(1'b0),
        .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(), .ev_idx_o(), .ev_k_o(), .ev_err_o(),
        .ev_mask_valid_o(), .ev_mask_o(), .ev_ids_o(), .ev_proto_err_o(),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    // ---------------------------------------------------------------- 参考系数
    longint W    [0:NVER-1][0:NSL-1][0:NU-1];
    longint INVM [0:NVER-1][0:8];

    // ---------------------------------------------------------------- 金标向量
    int     G_c   [0:NGOLD-1];
    int     G_a2  [0:NGOLD-1];
    int     G_d   [0:NGOLD-1];
    longint G_wm  [0:NGOLD-1];
    longint G_g   [0:NGOLD-1];
    longint G_inv [0:NGOLD-1];
    int     G_cd  [0:NGOLD-1];
    int     G_ov  [0:NGOLD-1];

    // ---------------------------------------------------------------- 逐 tag 期望
    longint exp_wm   [0:TAGMAX-1];
    longint exp_g    [0:TAGMAX-1];
    longint exp_inv  [0:TAGMAX-1];
    int     exp_code [0:TAGMAX-1];
    int     exp_adc2 [0:TAGMAX-1];
    int     exp_dith [0:TAGMAX-1];
    int     exp_base [0:TAGMAX-1];
    bit     exp_ovf  [0:TAGMAX-1];
    bit     exp_err  [0:TAGMAX-1];
    bit     exp_live [0:TAGMAX-1];
    bit     chk_a    [0:TAGMAX-1];
    bit     chk_b    [0:TAGMAX-1];
    longint acc_cyc  [0:TAGMAX-1];

    // ---------------------------------------------------------------- 账目
    int  cyc = 0;
    int  ref_cnt = 0;
    int  n_acc = 0, n_ret = 0, n_canc = 0;
    int  errors = 0;
    int  lat_hist [0:16];
    bit  accepted_last = 1'b0;
    int unsigned q [$];

    int  next_tag = 0;
    int  n_stall_hold = 0;
    bit  holding = 1'b0;
    int  cur_ver = 0;
    bit  golden_mode = 1'b0;
    longint max_num = 0;

    always @(posedge clk) cyc <= cyc + 1;

    // ---------------------------------------------------------------- 工具
    bit neg_mode = 1'b0;      // 负向用例：错误应被"抓到"，但不计入总数
    int neg_hits = 0;

    task automatic fail(input string msg);
        if (neg_mode) begin
            neg_hits = neg_hits + 1;
            return;
        end
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    // ---------------------------------------------------------------- 参考模型
    // 与 digital_chain.run_transaction 独立实现，逐字对应其定点公式
    function automatic void ref_model(
        input  int     sample_id,
        input  int     c,
        input  int     a2,
        input  int     dith,
        input  int     ver,
        output longint wm,
        output longint g,
        output longint iv,
        output int     code,
        output bit     ovf
    );
        int     k, H, M, L, qd, res, hp, vp;
        int     h [16];
        int     y [8];
        int     idx [16];
        int     sid, i, j, s, upper, tnum;
        longint num, dac, rhat, tq, x, ax, q96, cc;

        upper = 4 * ((dith >> 5) & 1) + ((dith >> 3) & 1);
        k     = c - upper;
        H     = (k >> 5) & 15;
        M     = (k >> 2) & 7;
        L     = k & 3;

        qd  = sample_id / 9;
        res = qd % 16;
        hp  = (qd / 128) % 15;
        vp  = (qd / 16) % 8;

        for (i = 0; i < 16; i++) h[i] = 0;
        for (i = 0; i < 8; i++)  y[i] = 0;
        j = 0;
        for (i = 0; i < 16; i++) begin
            if (i != res) begin
                idx[j] = i;
                j = j + 1;
            end
        end
        for (i = 0; i < H; i++) h[idx[(hp + i) % 15]] = 1;
        for (i = 0; i < M; i++) y[(vp + i) % 8] = 1;

        wm = 0;
        g  = 0;
        for (s = 0; s < 8; s++) begin
            sid = ((sample_id + 1) * 8 + s) % 18;
            for (j = 0; j < 16; j++) begin
                g = g + W[ver][sid][j];
                if ((j == res) ? y[s] : h[j]) wm = wm + W[ver][sid][j];
            end
            if (L[1]) wm = wm + W[ver][sid][18];
            if (L[0]) wm = wm + W[ver][sid][19];
        end

        iv   = INVM[ver][sample_id % 9];   // 组号 = 样本身份 % 9，与 RTL 一致
        num  = 6 * wm - 3 * g;
        dac  = (num * iv) >>> 36;                 // 与 RTL 一致：算术右移 floor
        rhat = ((2 * a2 + 1) * 144 + 2) / 5;
        tnum = upper * 8 + ((dith >> 4) & 1) * 16 + ((dith >> 2) & 1) * 4
             + ((dith >> 1) & 1) * 2 + (dith & 1);
        tq = tnum * 24576;
        x  = dac + rhat + tq;

        ax  = (x >= 0) ? x : -x;
        q96 = (ax * 44739243 + (64'sd1 << 31)) >>> 32;
        if (x < 0) q96 = -q96;
        cc  = q96 + (1 << 19);
        ovf = (cc > 1048575) || (cc < 0);
        if (cc > 1048575)      code = 1048575;
        else if (cc < 0)       code = 0;
        else                   code = int'(cc);

        if ((num > max_num) || (-num > max_num)) begin
            max_num = (num > 0) ? int'(num) : int'(-num);
        end
    endfunction

    // ---------------------------------------------------------------- 读文件
    task automatic load_files();
        int fd, r, typ, ver, sid, u, iv, i;
        longint w, wm, g, inv, cd, ov;
        fd = $fopen("vec_topw_coef.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_topw_coef.txt");
            $finish;
        end
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d %d\n", typ, ver, sid, u, w);
            if (r == 5) begin
                if (typ == 0)      W[ver][sid][u] = w;
                else if (typ == 1) INVM[ver][sid] = w;   // sid 复用为组号 0..8
            end
        end
        $fclose(fd);

        fd = $fopen("vec_topw_gold.txt", "r");
        if (fd == 0) begin
            $display("FATAL: cannot open vec_topw_gold.txt");
            $finish;
        end
        i = 0;
        while (!$feof(fd) && i < NGOLD) begin
            r = $fscanf(fd, "%d %d %d %d %d %d %d %d\n",
                        G_c[i], G_a2[i], G_d[i], wm, g, inv, cd, ov);
            if (r == 8) begin
                G_wm[i] = wm; G_g[i] = g; G_inv[i] = inv;
                G_cd[i] = cd; G_ov[i] = ov;
                i = i + 1;
            end
        end
        $fclose(fd);
        $display("loaded coef + %0d golden vectors", i);
    endtask

    // ---------------------------------------------------------------- 系数加载
    task automatic load_version(input int ver);
        int s, u;
        // 仅在 idle 时写
        while (busy !== 1'b0) @(negedge clk);
        for (s = 0; s < NSL; s++) begin
            for (u = 0; u < NU; u++) begin
                @(negedge clk);
                cfg_we   = 1'b1;
                cfg_slice = s[4:0];
                cfg_unit  = u[4:0];
                cfg_w     = W[ver][s][u][43:0];
            end
        end
        @(negedge clk);
        cfg_we = 1'b0;
        for (int g = 0; g < 9; g++) begin
            @(negedge clk);
            cfg_inv_we = 1'b1;
            cfg_grp    = g[3:0];
            cfg_inv    = INVM[ver][g];
        end
        @(negedge clk);
        cfg_inv_we = 1'b0;
        $display("[%0t] loaded coefficient version %0d (9 group inverses)", $time, ver);
    endtask

    // 负向加载：mode=0 正常，1=交换 0/1 组倒数，2=跳过第 3 组不写
    task automatic load_inv_custom(input int ver, input int mode);
        longint iv0, iv1;
        while (busy !== 1'b0) @(negedge clk);
        if (mode == 2) begin
            // 模拟"系数已更新"：写一次同一权重，令旧倒数全部失效
            @(negedge clk); cfg_we = 1'b1;
            cfg_slice = 5'd0; cfg_unit = 5'd0; cfg_w = W[ver][0][0][43:0];
            @(negedge clk); cfg_we = 1'b0;
        end
        iv0 = INVM[ver][0];
        iv1 = INVM[ver][1];
        for (int g = 0; g < 9; g++) begin
            if (mode == 2 && g == 3) continue;      // 故意漏载第 3 组
            @(negedge clk);
            cfg_inv_we = 1'b1;
            cfg_grp    = g[3:0];
            if (mode == 1 && g == 0)      cfg_inv = iv1;   // 交换
            else if (mode == 1 && g == 1) cfg_inv = iv0;
            else                          cfg_inv = INVM[ver][g];
        end
        @(negedge clk);
        cfg_inv_we = 1'b0;
    endtask

    // ---------------------------------------------------------------- 入口监控 / 出口记分
    always @(posedge clk) begin
        int unsigned ftag;
        int bt, at, s, lat;
        longint m_wm, m_g, m_iv;
        int m_code;
        bit m_ovf;

        if (!rstn) begin
            ref_cnt       <= 0;
            accepted_last <= 1'b0;
            if (q.size() != 0) begin
                n_canc = n_canc + q.size();
                q.delete();
            end
        end else begin
            accepted_last <= 1'b0;

            // ---------- 入口：接收 ----------
            if (valid_i && in_ready) begin
                ref_model(ref_cnt, int'(c_i), int'(adc2_i), int'(dither_i), cur_ver,
                          m_wm, m_g, m_iv, m_code, m_ovf);

                exp_wm[tag_i]   = m_wm;
                exp_g[tag_i]    = m_g;
                exp_inv[tag_i]  = m_iv;
                exp_code[tag_i] = m_code;
                exp_ovf[tag_i]  = m_ovf;
                exp_err[tag_i]  = (cur_ver == 3);
                exp_adc2[tag_i] = int'(adc2_i);
                exp_dith[tag_i] = int'(dither_i);
                exp_base[tag_i] = ((ref_cnt + 1) * 8) % 18;
                exp_live[tag_i] = 1'b1;
                chk_a[tag_i]    = 1'b0;
                chk_b[tag_i]    = 1'b0;
                acc_cyc[tag_i]  = cyc;
                q.push_back(tag_i);

                if (golden_mode) begin
                    if (tag_i != ref_cnt[23:0]) fail("golden: tag != sample_id");
                    if (m_wm  != G_wm[ref_cnt])  fail("golden: model Wm != python");
                    if (m_g   != G_g[ref_cnt])   fail("golden: model G  != python");
                    if (m_iv  != G_inv[ref_cnt]) fail("golden: model inv!= python");
                    if (m_code!= G_cd[ref_cnt])  fail("golden: model code != python");
                    if (m_ovf != G_ov[ref_cnt][0]) fail("golden: model ovf != python");
                end

                n_acc = n_acc + 1;
                ref_cnt <= ref_cnt + 1;
                accepted_last <= 1'b1;
            end

            // ---------- E1：片 ID / dither / adc2 ----------
            if (dut.valid_a) begin
                at = int'(dut.tag_a);
                if (!exp_live[at]) fail("E1: valid_a for non-live tag");
                if (chk_a[at])     fail("E1: duplicate check for tag");
                if (int'(dut.adc2_a)   != exp_adc2[at]) fail("E1: adc2 crossed");
                if (int'(dut.dither_a) != exp_dith[at]) fail("E1: dither crossed");
                for (s = 0; s < 8; s++) begin
                    if (int'(dut.slice_a[s]) != ((exp_base[at] + s) % 18))
                        fail("E1: slice id crossed");
                end
                chk_a[at] = 1'b1;
            end

            // ---------- E2：Wm / G / inv ----------
            if (dut.valid_b) begin
                bt = int'(dut.tag_b);
                if (!exp_live[bt]) fail("E2: valid_b for non-live tag");
                if (chk_b[bt])     fail("E2: duplicate check for tag");
                if (longint'(dut.wm_b) != exp_wm[bt])  fail("E2: Wm crossed");
                if (longint'(dut.g_b)  != exp_g[bt])   fail("E2: G crossed");
                // 9 组倒数：比对"该组实际选出"的那一项，同时校验组号未错位
                if (longint'(dut.inv_sel) != exp_inv[bt]) fail("E2: inv crossed");
                if (int'(dut.grp_b) > 8)                    fail("E2: group id out of range");
                chk_b[bt] = 1'b1;
            end

            // ---------- 出口：按 FIFO 退休 ----------
            if (valid_o) begin
                if (q.size() == 0) begin
                    fail("E4: output with empty scoreboard queue");
                end else begin
                    ftag = q.pop_front();
                    if (ftag != tag_o) fail("E4: out-of-order tag");
                    if (!exp_live[tag_o]) fail("E4: duplicate/phantom tag");
                    if (!chk_a[tag_o]) fail("E4: E1 check missing");
                    if (!chk_b[tag_o]) fail("E4: E2 check missing");
                    if (int'(code_o) != exp_code[tag_o]) begin
                        fail("E4: code mismatch");
                        if (errors <= 25)
                            $display("     tag=%0d got=%0d exp=%0d", tag_o, code_o, exp_code[tag_o]);
                    end
                    if (ovf_o !== exp_ovf[tag_o]) fail("E4: overflow mismatch");
                    if (err_o !== exp_err[tag_o]) fail("E4: err mismatch");
                    exp_live[tag_o] = 1'b0;
                    lat = cyc - int'(acc_cyc[tag_o]);
                    if (lat < 0) lat = 0;
                    if (lat > 16) lat = 16;
                    lat_hist[lat] = lat_hist[lat] + 1;
                    n_ret = n_ret + 1;
                end
            end
        end
    end

    // ---------------------------------------------------------------- 排空 / reset
    task automatic drain();
        int guard;
        guard = 0;
        while ((busy || q.size() != 0) && guard < 500) begin
            @(negedge clk);
            guard = guard + 1;
        end
        repeat (2) @(negedge clk);
        if (busy || q.size() != 0) fail("drain timeout: pending not empty");
    endtask

    task automatic do_reset();
        // 直接在中途拉低 reset：当拍已在出口的事务也按 cancelled 计
        // （下游同样处于复位，不采样该拍输出；口径见 docs/TOP_INTEGRATION.md）
        valid_i = 1'b0;
        stall_i = 1'b0;
        holding = 1'b0;
        @(negedge clk);
        $display("[%0t] mid-stream reset: pending=%0d", $time, q.size());
        rstn = 1'b0;
        repeat (2) @(negedge clk);
        rstn = 1'b1;
    endtask

    // ---------------------------------------------------------------- DEM 相位
    // 与 digital_chain.dem_phases 一致：q = sample_id/9
    //   reserved = q%16, h_phase = (q/128)%15, v_phase = (q/16)%8
    task automatic set_phases(input int sid);
        int qd, rv, hp, vp;
        qd = sid / 9;
        rv = qd % 16;
        hp = (qd / 128) % 15;
        vp = (qd / 16) % 8;
        reserved_i = rv[3:0];
        h_phase_i  = hp[3:0];
        v_phase_i  = vp[2:0];
    endtask

    // ---------------------------------------------------------------- 金标连发
    task automatic phase_golden();
        int i;
        golden_mode = 1'b1;
        cur_ver = 0;
        rstn = 1'b1; stall_i = 1'b0;
        for (i = 0; i < NGOLD; i++) begin
            @(negedge clk);
            if (i > 0 && !accepted_last) fail("golden: transaction not accepted");
            valid_i  = 1'b1;
            set_phases(i);
            c_i      = G_c[i][8:0];
            adc2_i   = G_a2[i][11:0];
            dither_i = G_d[i][5:0];
            tag_i    = i[23:0];
        end
        @(negedge clk);
        if (!accepted_last) fail("golden: last transaction not accepted");
        valid_i = 1'b0;
        drain();
        golden_mode = 1'b0;
    endtask

    // ---------------------------------------------------------------- 随机突发
    task automatic run_burst(input int n, input int ver,
                             input int valid_pct, input int stall_pct,
                             input int n_reset);
        int i, rs_left, next_rs, upper;
        i = 0;
        holding = 1'b0;
        valid_i = 1'b0;
        stall_i = 1'b0;
        cur_ver = ver;
        rs_left = n_reset;
        next_rs = (rs_left > 0) ? $urandom_range(10, 40) : -1;

        while (i < n) begin
            @(negedge clk);
            if (holding && accepted_last) begin
                holding = 1'b0;
                i = i + 1;
            end
            if (holding && !accepted_last && stall_i)
                n_stall_hold = n_stall_hold + 1;   // 被 stall 挡住的实际拍数

            if (rs_left > 0 && i >= next_rs) begin
                holding = 1'b0;
                valid_i = 1'b0;
                do_reset();
                rs_left = rs_left - 1;
                next_rs = i + $urandom_range(20, 60);
                continue;
            end

            stall_i = (($urandom_range(0, 99)) < stall_pct);

            if (holding) begin
                valid_i = 1'b1;                 // 被 stall 挡住，保持原事务
            end else if (($urandom_range(0, 99)) < valid_pct) begin
                int dw, cc, aa;
                holding  = 1'b1;
                valid_i  = 1'b1;
                dw       = $urandom_range(0, 63);
                upper    = 4 * ((dw >> 5) & 1) + ((dw >> 3) & 1);
                cc       = upper + $urandom_range(0, 511 - upper);
                aa       = $urandom_range(0, 4095);
                set_phases(ref_cnt);
                dither_i = dw[5:0];
                c_i      = cc[8:0];
                adc2_i   = aa[11:0];
                tag_i    = next_tag[23:0];
                next_tag = next_tag + 1;
            end else begin
                valid_i = 1'b0;                 // 气泡
            end
        end
        valid_i = 1'b0;
        stall_i = 1'b0;
        holding = 1'b0;
        drain();
    endtask

    task automatic check_phase(input string name, input int a0, input int r0, input int c0);
        int a, r, c;
        a = n_acc - a0;
        r = n_ret - r0;
        c = n_canc - c0;
        $display("PHASE %-10s  accepted=%0d  retired=%0d  cancelled=%0d  pending=%0d  %s",
                 name, a, r, c, q.size(), (a == r + c && q.size() == 0) ? "OK" : "BAD");
        if (a != r + c) fail($sformatf("%s: identity broken", name));
        if (q.size() != 0) fail($sformatf("%s: pending != 0", name));
    endtask

    // ---------------------------------------------------------------- 主流程
    int a0, r0, c0, k;
    initial begin
        $srandom(32'd20261114);          // 固定随机种子，日志可复现
        for (k = 0; k < 17; k++) lat_hist[k] = 0;
        valid_i = 0; stall_i = 0; rstn = 0;
        load_files();

        repeat (4) @(negedge clk);
        rstn = 1'b1;
        load_version(0);

        // 1) 金标连续每拍一笔记（无气泡），与 Python 期望逐位比
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        phase_golden();
        check_phase("golden", a0, r0, c0);

        // 2) 随机气泡（同一版系数，背靠背 + 空洞）
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        run_burst(2000, 0, 90, 0, 0);
        check_phase("bubbles", a0, r0, c0);

        // 3) 随机 stall（换 V1，加载时已排空）
        load_version(1);
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        run_burst(2000, 1, 100, 12, 0);
        check_phase("stall", a0, r0, c0);

        // 4) 中途 reset（换 V2）
        load_version(2);
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        run_burst(2000, 2, 85, 4, 3);
        check_phase("reset", a0, r0, c0);

        // 5) 零系数版：验证 err_o 通路（G=0 / inv=0）
        load_version(3);
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        run_burst(64, 3, 100, 0, 0);
        check_phase("zero-err", a0, r0, c0);

        // 恢复 V0，确认 reset 没有破坏校准（再跑 200 笔）
        load_version(0);
        a0 = n_acc; r0 = n_ret; c0 = n_canc;
        run_burst(200, 0, 100, 0, 1);
        check_phase("recal", a0, r0, c0);

        // 7) 负向：交换 0/1 两组的倒数 —— 记分板必须抓到
        neg_mode = 1'b1; neg_hits = 0;
        load_inv_custom(0, 1);
        run_burst(300, 0, 100, 0, 0);
        drain();
        $display("NEG swap   : hits=%0d  %s", neg_hits, (neg_hits > 0) ? "OK(caught)" : "BAD(not caught)");
        if (neg_hits == 0) begin neg_mode = 1'b0; fail("swap of group inverses NOT caught"); end
        neg_mode = 1'b0;

        // 8) 负向：漏载第 3 组倒数 —— 必须报 err，不能静默用旧值
        neg_mode = 1'b1; neg_hits = 0;
        load_inv_custom(0, 2);
        run_burst(300, 0, 100, 0, 0);
        drain();
        $display("NEG missing: hits=%0d  %s", neg_hits, (neg_hits > 0) ? "OK(caught)" : "BAD(not caught)");
        if (neg_hits == 0) begin neg_mode = 1'b0; fail("missing group inverse NOT caught"); end
        neg_mode = 1'b0;

        // 负向用例结束后恢复完整 9 组，确认可恢复
        load_version(0);
        neg_mode = 1'b0; neg_hits = 0;
        run_burst(120, 0, 100, 0, 0);
        drain();
        $display("RECOVER    : hits=%0d (应为 0)", neg_hits);
        if (neg_hits != 0) fail("recovery after negative cases failed");

        // ------------------------------------------------------------ 总结
        $display("---------------------------------------------------------------");
        $display("TOTAL accepted=%0d retired=%0d cancelled=%0d pending=%0d",
                 n_acc, n_ret, n_canc, q.size());
        $display("STALL: %0d cycle(s) held by stall_i (in_ready=0)", n_stall_hold);
        $display("LATENCY (accept->valid_o) histogram:");
        for (k = 0; k < 17; k++)
            if (lat_hist[k] != 0) $display("   %0d cycles : %0d", k, lat_hist[k]);
        if (n_acc != n_ret + n_canc) fail("final: N_acc != N_ret + N_canc");
        if (q.size() != 0)           fail("final: pending != 0");
        if (errors == 0) $display("RESULT: PASS (%0d transactions checked)", n_ret);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
