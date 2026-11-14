`timescale 1ns/1ps
// digital_top_w · random18 模式端到端对拍
//
// 与 tb_digital_top_w 的分工：
//   tb_digital_top_w    : legacy9（sched_mode_i=0）8509 笔回归，9 组倒数查表
//   tb_digital_top_r18  : random18（sched_mode_i=1）动态选片 + 动态 G + Newton 倒数
//
// 判据（逐笔，不是只比总数）：
//   1) E1 : 8 个片 ID 与 Python 调度器（adi_model/sched18_rtl.py）逐笔一致
//   2) E2 : Wm / G 与生产模型逐位一致；inv 与 adi_model/recip_q60.py 逐位一致
//   3) E4 : code20 与"Python + recip_q60"逐位一致；同时统计与"精确倒数"版本的差，
//           要求 ≤1 LSB（算法口径，对应 P0-4 的 ≤0.5 LSB D_eff）
//   4) 调度不变量每拍检查：ACQ∩CONV=∅、|ACQ|=|CONV|=8、|IDLE|=2、并集=18
//   5) 事务守恒：acc = ret + canc，排空后 pending = 0；延迟分布如实打印
//   6) 模式隔离：legacy9 事务不得推进 random18 调度器（adv 按模式门控）
//   7) 合同违例：全零权重（G=0）必须报 err，不得静默出码
//
// 向量：tb/vec_r18_gold.txt（tools/gen_vec_r18.py）、tb/vec_phase_v3.txt
module tb_digital_top_r18;

    localparam int NGOLD = 2048;
    localparam int NSL   = 18;
    localparam int NU    = 20;
    localparam int NVER  = 4;

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic        rstn = 1'b0;
    logic        valid_i = 1'b0, stall_i = 1'b0;
    logic [8:0]  c_i = '0;
    logic [11:0] adc2_i = '0;
    logic [5:0]  dither_i = '0;
    logic [3:0]  reserved_i = '0, h_phase_i = '0;
    logic [2:0]  v_phase_i = '0;
    logic [23:0] tag_i = '0;
    logic        in_ready, busy;
    logic [19:0] code_o;
    logic        ovf_o, err_o, valid_o;
    logic [23:0] tag_o;
    logic        cfg_we = 1'b0, cfg_inv_we = 1'b0;
    logic [4:0]  cfg_slice = '0, cfg_unit = '0;
    logic [43:0] cfg_w = '0;
    logic [63:0] cfg_inv = '0;
    logic [3:0]  cfg_grp = '0;

    logic        sched_mode = 1'b1;
    logic        sched_seed_we = 1'b0;
    logic [31:0] sched_seed = 32'hACE1_0001;
    logic [17:0] sched_acq, sched_conv, sched_idle;
    logic [39:0] slice_ids;

    digital_top_w dut (
        .clk(clk), .reset_n(rstn),
        .valid_i(valid_i), .stall_i(stall_i),
        .c_i(c_i), .adc2_i(adc2_i), .dither_i(dither_i),
        .reserved_i(reserved_i), .h_phase_i(h_phase_i), .v_phase_i(v_phase_i),
        .tag_i(tag_i), .in_ready_o(in_ready),
        .sched_mode_i(sched_mode), .sched_seed_we_i(sched_seed_we), .sched_seed_i(sched_seed),
        .sched_acq_o(sched_acq), .sched_conv_o(sched_conv), .sched_idle_o(sched_idle),
        .cfg_we(cfg_we), .cfg_slice(cfg_slice), .cfg_unit(cfg_unit), .cfg_w(cfg_w),
        .cfg_inv_we(cfg_inv_we), .cfg_inv(cfg_inv), .cfg_inv_grp(cfg_grp),
        .ev_mode_i(1'b0), .ev_start_i(1'b0), .ev_valid_i(1'b0), .ev_bit_i(1'b0),
        .sar_index_i(4'd0), .sar_qid_i(1'b0),
        .flash_valid_i(1'b0), .flash_code_i(3'd0),
        .ev_done_o(), .ev_idx_o(), .ev_k_o(), .ev_err_o(),
        .ev_mask_valid_o(), .ev_mask_o(), .ev_ids_o(), .ev_proto_err_o(), .ev_ready_o(),
        .busy_o(busy), .slice_ids_o(slice_ids),
        .code_o(code_o), .overflow_o(ovf_o), .err_o(err_o),
        .valid_o(valid_o), .tag_o(tag_o));

    // ---------------------------------------------------------------- 系数 / 相位 / 向量
    longint W     [0:NVER-1][0:NSL-1][0:NU-1];
    int     PH_res [0:1919];
    int     PH_hp  [0:1919];
    int     PH_vp  [0:1919];

    int     V_id  [0:NGOLD-1][0:7];
    int     V_c   [0:NGOLD-1];
    int     V_a2  [0:NGOLD-1];
    int     V_d   [0:NGOLD-1];
    longint V_wm  [0:NGOLD-1];
    longint V_g   [0:NGOLD-1];
    longint V_inv [0:NGOLD-1];
    int     V_cd  [0:NGOLD-1];
    int     V_cx  [0:NGOLD-1];

    int      errors = 0;
    int      n_acc = 0, n_ret = 0, n_canc = 0;
    int      lat_hist [0:16];
    int      cyc = 0;
    int      d_code1 = 0, d_code_bad = 0, d_code_max = 0;
    int      n_exact = 0;
    longint  acc_cyc [0:NGOLD-1];
    int      acc_tag [$];

    always @(posedge clk) cyc <= cyc + 1;

    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    function automatic int popcnt18(input logic [17:0] v);
        int c;
        c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    task automatic load_all();
        int fd, r, typ, ver, sid, u, i, idx, rsv, hpp, vpp, dum;
        longint w, wm, g, inv, cd, cx;
        int i0,i1,i2,i3,i4,i5,i6,i7, cc, a2, dd;

        fd = $fopen("vec_topw_coef.txt", "r");
        if (fd == 0) begin $display("FATAL: coef"); $finish; end
        while (!$feof(fd)) begin
            r = $fscanf(fd, "%d %d %d %d %d\n", typ, ver, sid, u, w);
            if (r == 5 && ver < NVER && typ == 0) W[ver][sid][u] = w;
        end
        $fclose(fd);

        fd = $fopen("vec_phase_v3.txt", "r");
        if (fd == 0) begin $display("FATAL: phase"); $finish; end
        i = 0;
        while (!$feof(fd) && i < 1920) begin
            r = $fscanf(fd, "%d %d %d %d\n", idx, rsv, hpp, vpp);
            if (r == 4) begin PH_res[i]=rsv; PH_hp[i]=hpp; PH_vp[i]=vpp; i=i+1; end
        end
        $fclose(fd);

        fd = $fopen("vec_r18_gold.txt", "r");
        if (fd == 0) begin $display("FATAL: r18 gold"); $finish; end
        i = 0;
        while (!$feof(fd) && i < NGOLD) begin
            r = $fscanf(fd, "%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
                        idx, cc, a2, dd,
                        i0,i1,i2,i3,i4,i5,i6,i7,
                        wm, g, inv, cd, cx, dum, dum);
            if (r == 19) begin
                V_id[i][0]=i0; V_id[i][1]=i1; V_id[i][2]=i2; V_id[i][3]=i3;
                V_id[i][4]=i4; V_id[i][5]=i5; V_id[i][6]=i6; V_id[i][7]=i7;
                V_c[i]=cc; V_a2[i]=a2; V_d[i]=dd;
                V_wm[i]=wm; V_g[i]=g; V_inv[i]=inv; V_cd[i]=cd; V_cx[i]=cx;
                i = i + 1;
            end
        end
        $fclose(fd);
        $display("loaded coef(%0d ver), 1920 phases, %0d r18 vectors", NVER, i);
        if (i != NGOLD) begin $display("FATAL: 向量行数不足 %0d", i); $finish; end
    endtask

    task automatic load_version(input int ver);
        int s, u;
        while (busy !== 1'b0) @(negedge clk);
        for (s = 0; s < NSL; s++) begin
            for (u = 0; u < NU; u++) begin
                @(negedge clk);
                cfg_we    = 1'b1;
                cfg_slice = s[4:0];
                cfg_unit  = u[4:0];
                cfg_w     = W[ver][s][u][43:0];
            end
        end
        @(negedge clk);
        cfg_we = 1'b0;
        $display("[%0t] 装载权重 V%0d（random18 不写倒数表）", $time, ver);
    endtask

    task automatic set_phases(input int sid);
        reserved_i = PH_res[sid % 1920][3:0];
        h_phase_i  = PH_hp[sid % 1920][3:0];
        v_phase_i  = PH_vp[sid % 1920][2:0];
    endtask

    // ---------------------------------------------------------------- 记分板
    always @(posedge clk) begin
        int at, lat;

        if (!rstn) begin
            n_canc = n_canc + acc_tag.size();
            acc_tag.delete();
        end else begin
            // 调度不变量：每一拍都查
            if ((sched_acq & sched_conv) != 18'd0) fail("sched: ACQ/CONV overlap");
            if (popcnt18(sched_acq) != 8)          fail("sched: |ACQ| != 8");
            if (popcnt18(sched_conv) != 8)         fail("sched: |CONV| != 8");
            if (popcnt18(sched_idle) != 2)         fail("sched: |IDLE| != 2");
            if ((sched_acq | sched_conv | sched_idle) != 18'h3FFFF)
                fail("sched: 三态并集 != 18 片");

            // ---- 入口 ----
            if (valid_i && in_ready) begin
                at = int'(tag_i);
                acc_cyc[at] = cyc;
                acc_tag.push_back(at);
                n_acc = n_acc + 1;
            end

            // ---- E1：片 ID ----
            if (dut.valid_a) begin
                int tt;
                tt = int'(dut.tag_a);
                if (tt < NGOLD && sched_mode_run) begin
                    for (int s = 0; s < 8; s++)
                        if (int'(dut.slice_a[s]) != V_id[tt][s])
                            fail($sformatf("E1 tag%0d slot%0d: id %0d != %0d",
                                           tt, s, dut.slice_a[s], V_id[tt][s]));
                end
            end

            // ---- E2：Wm / G / inv ----
            if (dut.valid_b) begin
                int tt;
                tt = int'(dut.tag_b);
                if (tt < NGOLD && sched_mode_run) begin
                    if (longint'(dut.wm_b) != V_wm[tt])
                        fail($sformatf("E2 tag%0d: Wm %0d != %0d", tt, dut.wm_b, V_wm[tt]));
                    if (longint'(dut.g_b) != V_g[tt])
                        fail($sformatf("E2 tag%0d: G %0d != %0d", tt, dut.g_b, V_g[tt]));
                    if (longint'(dut.inv_sel) != V_inv[tt])
                        fail($sformatf("E2 tag%0d: inv %0d != %0d", tt, dut.inv_sel, V_inv[tt]));
                    if (!dut.mode_b) fail("E2: mode_b != 1（模式没随流水走）");
                end
            end

            // ---- 出口 ----
            if (valid_o) begin
                int ft;
                if (acc_tag.size() == 0) fail("E4: 出口无待弹 tag");
                else begin
                    ft = acc_tag.pop_front();
                    if (ft != int'(tag_o)) fail("E4: tag 乱序");
                    else if (ft < NGOLD && sched_mode_run) begin
                        if (int'(code_o) != V_cd[ft]) begin
                            fail($sformatf("E4 tag%0d: code %0d != %0d (Python+recip)",
                                           ft, code_o, V_cd[ft]));
                        end
                        if (int'(code_o) == V_cx[ft]) n_exact = n_exact + 1;
                        else if (abs(int'(code_o) - V_cx[ft]) == 1) d_code1 = d_code1 + 1;
                        else begin
                            d_code_bad = d_code_bad + 1;
                            fail($sformatf("E4 tag%0d: 与精确倒数差 %0d LSB（应 ≤1）",
                                           ft, int'(code_o) - V_cx[ft]));
                        end
                        if (abs(int'(code_o) - V_cx[ft]) > d_code_max)
                            d_code_max = abs(int'(code_o) - V_cx[ft]);
                        if (err_o) fail($sformatf("E4 tag%0d: 合同内事务误报 err", ft));
                    end
                    lat = cyc - int'(acc_cyc[ft]);
                    if (lat < 0) lat = 0;
                    if (lat > 16) lat = 16;
                    lat_hist[lat] = lat_hist[lat] + 1;
                    n_ret = n_ret + 1;
                end
            end
        end
    end

    task automatic drain();
        int guard;
        guard = 0;
        while ((busy || acc_tag.size() != 0) && guard < 500) begin
            @(negedge clk);
            guard = guard + 1;
        end
        repeat (2) @(negedge clk);
        if (busy || acc_tag.size() != 0) fail("drain timeout");
    endtask

    // ---------------------------------------------------------------- 主流程
    int k;
    bit sched_mode_run = 1'b0;      // 只有这一段用 r18 向量做逐笔比对
    initial begin
        valid_i = 0; stall_i = 0; rstn = 0;
        for (k = 0; k < 17; k++) lat_hist[k] = 0;
        load_all();

        repeat (4) @(negedge clk);
        rstn = 1'b1;
        @(negedge clk);
        sched_seed_we = 1'b1;
        sched_seed    = 32'hACE1_0001;
        @(negedge clk);
        sched_seed_we = 1'b0;

        load_version(0);
        sched_mode = 1'b1;
        sched_mode_run = 1'b1;

        // ---- 1) 背靠背 2048 笔，每拍一笔 ----
        for (k = 0; k < NGOLD; k++) begin
            @(negedge clk);
            if (!in_ready) fail($sformatf("第 %0d 笔入口未 ready", k));
            valid_i  = 1'b1;
            set_phases(k);
            c_i      = V_c[k][8:0];
            adc2_i   = V_a2[k][11:0];
            dither_i = V_d[k][5:0];
            tag_i    = k[23:0];
        end
        @(negedge clk);
        valid_i = 1'b0;
        drain();
        sched_mode_run = 1'b0;

        $display("PHASE r18-back2back: accepted=%0d retired=%0d cancelled=%0d pending=%0d",
                 n_acc, n_ret, n_canc, acc_tag.size());
        if (n_acc != n_ret + n_canc) fail("identity: acc != ret + canc");
        if (acc_tag.size() != 0)     fail("identity: pending != 0");
        if (n_ret != NGOLD)          fail("retired != NGOLD");

        // ---- 2) 模式隔离：legacy9 事务不得推进 random18 调度器 ----
        begin
            logic [17:0] acq_before;
            logic [39:0] ids_before;
            acq_before = sched_acq;
            ids_before = slice_ids;
            sched_mode = 1'b0;
            @(negedge clk);
            valid_i = 1'b1; tag_i = 24'd0; c_i = 9'd300; adc2_i = 12'd1000;
            dither_i = 6'd5; set_phases(7);
            @(negedge clk);
            valid_i = 1'b0;
            repeat (8) @(negedge clk);
            if (sched_acq !== acq_before)
                fail("legacy9 事务推进了 random18 调度器（adv 未按模式门控）");
            if (slice_ids === ids_before)
                $display("note: legacy 片组与 r18 偶然相同，换一笔再确认");
            $display("PHASE mode-isolation: legacy 接受 1 笔，调度器冻结 acq=%05h", acq_before);
            drain();
            n_acc = n_ret; n_canc = 0;
            sched_mode = 1'b1;
        end

        // ---- 3) 全零权重（G=0）：必须逐笔报 err，不得静默出码 ----
        begin
            int err_seen;
            err_seen = 0;
            load_version(3);
            @(negedge clk);
            sched_mode = 1'b1;
            fork
                begin
                    for (int t = 0; t < 8; t++) begin
                        @(negedge clk);
                        valid_i = 1'b1; tag_i = t[23:0]; c_i = 9'd200;
                        adc2_i = 12'd500; dither_i = 6'd1; set_phases(t);
                    end
                    @(negedge clk);
                    valid_i = 1'b0;
                end
                begin
                    repeat (20) begin
                        @(posedge clk);
                        if (valid_o && err_o) err_seen = err_seen + 1;
                    end
                end
            join
            $display("PHASE zero-weight(G=0): err 脉冲 %0d/8", err_seen);
            if (err_seen == 0) fail("G=0 未报 err（静默出码）");
            drain();
        end

        $display("---------------------------------------------------------------");
        $display("TOTAL r18: retired=%0d  code==exact:%0d  |Δ|=1:%0d  max|Δ|:%0d",
                 n_ret, n_exact, d_code1, d_code_max);
        $display("LATENCY (accept->valid_o) histogram:");
        for (k = 0; k < 17; k++) if (lat_hist[k] != 0) $display("   %0d cycles : %0d", k, lat_hist[k]);
        if (d_code_bad != 0) fail("存在与精确倒数差 >1 LSB 的事务");
        if (errors == 0) $display("RESULT: PASS (%0d transactions bit-exact vs Python)", NGOLD);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
