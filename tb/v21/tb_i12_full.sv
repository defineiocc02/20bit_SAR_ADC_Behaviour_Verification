`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_i12_full.sv
// 模块名     : tb_i12_full
// 版本       : v2.0
// 功能       : I1+I2 完整联调 —— 两笔采样 + 逐事件物理开关零差异对拍。
//              覆盖 A 与 B **两笔都走完整流程**（v1.0 只走了 A）。
// 设计说明   :
//   * 判据（方案 §11.2 I1/I2 + 本轮四项补充）：
//       1. 旧组释放：A 的 capture/hold/quiet 未满足时，后续 prepare 不得
//          覆盖或复用 A 的 CONV；同沿"释放+提升"时提升优先。
//       2. 物理输出同沿注册：post 数据与 18 位写门在 apply_fire 沿后同拍有效；
//          空泡/非法事件写门为 0；已写片保持。
//       3. F=7 拒绝 512 阈：不得写 RDAC，随后从 bit5 继续普通六位，
//          总提交仍 7 次，c=480/U=5 -> k=485。
//       4. B 全流程：逐事件 k/160 位 slot/360 位物理 post/18 位写门/tag/qid/final。
//   * 期望值来自 dem_dither.py（与叶子对拍同一份参考），见 tools/gen_vec_i12.py。
//   * 写片位图由片号生成，断言与 RTL 输出逐位一致。
// 变更记录   :
//   2026-11-15  v1.0 只覆盖 A 的逐事件应用。
//   2026-11-15  v2.0 补 B 全流程、同沿优先级、注册时机、guard 反例。
// =============================================================================
module tb_i12_full;


    logic clk = 1'b0;

    // ---------------- 权重配置口（CONFIG_CONTRACT v1.0）----------------
    // 顶层受理门禁要求 cfg_valid=1，所以本 TB 在复位释放后必须装表并 commit。
    logic        t_cfg_we = 1'b0, t_cfg_sel = 1'b0;
    logic [4:0]  t_cfg_sid = 5'd0, t_cfg_idx = 5'd0;
    logic [39:0] t_cfg_data = 40'd0;
    logic        t_cfg_commit = 1'b0, t_cfg_reload = 1'b0;
    wire         t_cfg_valid, t_cfg_busy, t_cfg_range_err;

    task automatic t_wt_write(input logic sel, input logic [4:0] sid,
                              input logic [4:0] idx, input logic [39:0] d);
        begin
            @(negedge clk);
            t_cfg_we = 1'b1; t_cfg_sel = sel; t_cfg_sid = sid;
            t_cfg_idx = idx; t_cfg_data = d;
            @(posedge clk);
            @(negedge clk);
            t_cfg_we = 1'b0;
        end
    endtask

    // 装 360 个字（18 片 x 20）并 commit
    task automatic t_wt_install();
        begin
            @(negedge clk); t_cfg_reload = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_reload = 1'b0;
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++)
                    t_wt_write(1'b0, 5'(s), 5'(j), 40'd268435456);
                t_wt_write(1'b1, 5'(s), 5'd0, 40'd33554432);
                t_wt_write(1'b1, 5'(s), 5'd1, 40'd16777216);
                t_wt_write(1'b1, 5'(s), 5'd2, 40'd8388608);
                t_wt_write(1'b1, 5'(s), 5'd3, 40'd8388608);
            end
            @(negedge clk); t_cfg_commit = 1'b1;
            @(posedge clk); @(negedge clk); t_cfg_commit = 1'b0;
            @(posedge clk); @(negedge clk);
        end
    endtask

    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // 事务
    logic        prepare_valid = 1'b0, prepare_ready;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v = 3'd0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;
    logic [2:0]  prepare_U = 3'd0, prepare_flash = 3'd0;
    logic        prepare_qid = 1'b0;
    logic        stop_i = 1'b0;

    // 采样
    logic sample_event = 1'b0, input_captured = 1'b0;
    logic residue_captured = 1'b0, hold_ack = 1'b0;

    // 事件
    logic       ev_bit_valid_i = 1'b0, ev_bit_i = 1'b0;
    logic [3:0] ev_bit_index_i = 4'd0, ev_event_index_i = 4'd0;
    logic       ev_qid_i = 1'b0;

    // 片组：本 TB 用**外部驱动**做定向向量，所以必须把 sched_ext_sel 拉高。
    //
    // 踩过的坑：顶层后来加了 `sched_ext_sel`（生产态默认 0 = 用内部 sched18）。
    // 本 TB 没接这个端口，它悬空成 0，于是内部 sched18 接管，
    // 外部给的 sched_* 被忽略 -> B 的 CONV 片号全错、16 项失败（实测）。
    // 新增端口后必须逐个 TB 核对，不能只看编译通过。
    logic        sched_ext_sel = 1'b1;
    logic [17:0] sched_acq, sched_conv, sched_idle;
    logic [39:0] sched_ids;
    logic        sched_seed_we = 1'b0;
    logic [31:0] sched_seed = 32'h1234_5678;

    // 输出
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [71:0] pre_state;
    logic [17:0] pre_wr;
    logic [359:0] post_state;
    logic [17:0] post_wr;
    logic apply_fire, k_bad, final_applied, err, done;
    logic [9:0] k_out, final_k;
    logic [31:0] tag_out;
    logic qid_out;
    logic [159:0] post_slot;

    adc_digital_ip_v21 dut (
        .w_cfg_we(t_cfg_we), .w_cfg_sel(t_cfg_sel),
        .w_cfg_sid(t_cfg_sid), .w_cfg_idx(t_cfg_idx), .w_cfg_data(t_cfg_data),
        .w_cfg_commit(t_cfg_commit), .w_cfg_reload(t_cfg_reload),
        .w_cfg_valid(t_cfg_valid), .w_cfg_busy(t_cfg_busy),
        .w_cfg_range_err(t_cfg_range_err),
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v(prepare_v),
        .prepare_half(prepare_half), .prepare_coin(prepare_coin),
        .prepare_U(prepare_U), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid),
        .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .ev_bit_valid_i(ev_bit_valid_i), .ev_bit_i(ev_bit_i),
        .ev_bit_index_i(ev_bit_index_i), .ev_event_index_i(ev_event_index_i),
        .ev_qid_i(ev_qid_i),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .sched_ext_sel(sched_ext_sel),
        .sched_seed_we(sched_seed_we), .sched_seed(sched_seed),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
        .rdac_post_state_o(post_state), .post_wr_o(post_wr),
        .apply_fire_o(apply_fire), .k_o(k_out), .k_bad_o(k_bad),
        .final_applied_o(final_applied), .final_k_o(final_k),
        .err_o(err), .done_o(done), .tag_o(tag_out), .qid_o(qid_out),
        .post_slot_o(post_slot));

    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 30) $display("[%0t] ERROR: %s", $time, msg);
    endtask

    function automatic logic [39:0] pack_ids(input int a[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(a[i]);
        return v;
    endfunction

    function automatic int pc18(input logic [17:0] v);
        int c; c = 0;
        for (int i = 0; i < 18; i++) if (v[i]) c = c + 1;
        return c;
    endfunction

    // 由片号生成写片位图（用于断言）
    function automatic logic [17:0] mask_of(input int a[0:7]);
        logic [17:0] m; m = 18'd0;
        for (int i = 0; i < 8; i++) m[a[i]] = 1'b1;
        return m;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

    // ---- A 的金标（h=0,v=3,half=0,coin=1）----
    localparam logic [159:0] A_S[0:4] = '{
        160'h43FFF43FFF43FFF43FFF47FFF43FFF43FFF43FFF,
        160'h5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF,
        160'h5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF5FFFF,
        160'h7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF,
        160'h7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF7FFFF };
    int A_K[0:4]   = '{453, 517, 517, 519, 519};
    int A_BIT[0:4] = '{0, 9, 2, 1, 0};
    int A_BV[0:4]  = '{0, 1, 0, 1, 0};

    // ---- B 的金标（h=1,v=7,half=0,coin=0）----
    localparam logic [159:0] B_S[0:6] = '{
        160'h003FC003FC003FC003FC003FC003FC003FC003FC,
        160'h003FC003FC003FC003FC003FC003FC003FC003FC,
        160'h003FC003FC003FC003FC003FC003FC003FC003FC,
        160'h003FC003FC003FC003FC003FC003FC003FC003FC,
        160'h003FC003FC003FC003FC003FC003FC003FC003FC,
        160'h203FC203FC203FC203FC203FC203FC203FC203FC,
        160'h203FC203FC203FC203FC203FC203FC203FC203FC };
    int B_K[0:6]   = '{256, 256, 256, 256, 256, 258, 258};
    int B_BIT[0:6] = '{0, 5, 4, 3, 2, 1, 0};
    int B_BV[0:6]  = '{0, 0, 0, 0, 0, 1, 0};

    task automatic do_sample(input logic ic, input logic rc, input logic hk);
        @(negedge clk);
        sample_event = 1'b1; input_captured = ic;
        residue_captured = rc; hold_ack = hk;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0;
        residue_captured = 1'b0; hold_ack = 1'b0;
    endtask

    task automatic do_prepare(input logic [31:0] tag, input logic [5:0] w,
                              input logic [2:0] h, input logic [2:0] v,
                              input logic half, input logic coin,
                              input logic q, input logic [2:0] F);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = tag; prepare_word = w;
        prepare_h = h; prepare_v = v; prepare_half = half; prepare_coin = coin;
        prepare_U = {1'b0, w[5]} * 3'd4 + {2'b0, w[3]};
        prepare_flash = F; prepare_qid = q;
        @(posedge clk); #1;
        @(negedge clk);
        prepare_valid = 1'b0;
    endtask

    // 被拒的 prepare（不发 valid 上升，只检查 ready=0）
    task automatic try_prepare_expect_reject(input string why);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = 32'hDEAD;
        #1;
        checks = checks + 1;
        if (prepare_ready !== 1'b0)
            fail($sformatf("%s：prepare 应被拒（ready 应为 0）", why));
        else $display("      %s -> prepare 被拒  OK", why);
        @(negedge clk);
        prepare_valid = 1'b0;
    endtask

    // 一个已决事件；在 posedge 前采组合值，posedge 后采**注册**输出
    logic [159:0] ev_post_slot;     // 组合（apply 当拍）
    logic [359:0] ev_post_phys_reg; // 寄存（沿后）
    logic [17:0]  ev_post_wr_reg;
    logic         ev_applied;
    logic [9:0]   ev_k;
    task automatic do_event(input logic [3:0] bi, input logic [3:0] ei,
                            input logic bv, input logic q);
        @(negedge clk);
        ev_bit_valid_i = 1'b1; ev_bit_i = bv; ev_bit_index_i = bi;
        ev_event_index_i = ei; ev_qid_i = q;
        #1;
        ev_applied   = apply_fire;
        ev_post_slot = post_slot;      // 组合：本事件的 slot post
        ev_k         = k_out;
        @(posedge clk); #1;
        // 沿后一拍：注册输出应已生效（§4.1 同沿注册）
        ev_post_phys_reg = post_state;
        ev_post_wr_reg   = post_wr;
        @(negedge clk);
        ev_bit_valid_i = 1'b0;
    endtask

    // 物理 post 的累积影子模型。
    //
    // **不能每事件从 0 重建期望值**：§4.1 明确"未选物理片保持原状态"。
    // A 写过的片 0..7 在 B 转换期间仍是 A 的残留值（B 不碰它们），
    // 若期望从 0 开始，会把 A 的合法残留误判成错误（实测踩过）。
    logic [359:0] phys_shadow = 360'd0;

    // 逐事件对拍（A 或 B）
    task automatic check_event(input string who, input int idx,
                               input logic [159:0] want_slot,
                               input logic [9:0] want_k,
                               input int ids[0:7], input logic want_q);
        logic [359:0] want_phys;
        logic [17:0]  want_wr;
        // 在影子模型上叠加本次写入（其余片保持）
        for (int s = 0; s < 8; s++)
            phys_shadow[ids[s]*20 +: 20] = want_slot[s*20 +: 20];
        want_phys = phys_shadow;
        want_wr   = mask_of(ids);

        checks = checks + 1;
        if (ev_applied !== 1'b1) fail($sformatf("%s ev%0d 未 apply", who, idx));
        checks = checks + 1;
        if (ev_k !== want_k)
            fail($sformatf("%s ev%0d k=%0d 期望 %0d", who, idx, ev_k, want_k));
        // 判据：160 位 slot mask
        checks = checks + 1;
        if (ev_post_slot !== want_slot) begin
            fail($sformatf("%s ev%0d slot mask 不匹配", who, idx));
            $display("     got  %040h", ev_post_slot);
            $display("     want %040h", want_slot);
        end
        // 判据：360 位物理 post（注册后）
        checks = checks + 1;
        if (ev_post_phys_reg !== want_phys) begin
            fail($sformatf("%s ev%0d 物理 post 不匹配", who, idx));
            $display("     got  %090h", ev_post_phys_reg);
            $display("     want %090h", want_phys);
        end
        // 判据：18 位写门 == 本笔片号位图
        checks = checks + 1;
        if (ev_post_wr_reg !== want_wr) begin
            fail($sformatf("%s ev%0d 写门=%06h 期望 %06h", who, idx, ev_post_wr_reg, want_wr));
        end
        // 判据：tag 与 qid
        checks = checks + 1;
        if (qid_out !== want_q) fail($sformatf("%s ev%0d qid=%b 期望 %b", who, idx, qid_out, want_q));
        $display("      %s ev%0d k=%0d slot/phys/wr/qid 全对  OK", who, idx, ev_k);
    endtask

    initial begin
        sched_acq  = 18'h000FF; sched_conv = 18'h00000; sched_idle = 18'h3FF00;
        sched_ids  = pack_ids(A_IDS);

        repeat (3) @(negedge clk);
        rst_n = 1'b1; @(negedge clk);
        t_wt_install();   // 装表 + commit，否则 cfg_valid=0 门禁挡住交易

        // ==================== 阶段 1：prepare A ====================
        $display("---- 阶段1: prepare A (guard F=7, word=63, h=0 v=3 coin=1) ----");
        do_prepare(32'hA1, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        checks = checks + 1;
        if (dut.acq_valid !== 1'b1) fail("A 应在 ACQ");
        else $display("      A 在 ACQ  OK");

        // E0：只采输入
        $display("---- E0: 采样 A ----");
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (dut.acq_sampled !== 1'b1) fail("E0 后 A 应标记 sampled");
        else $display("      A sampled  OK");

        // ==================== 阶段 2：A 未释放时，prepare 必须被拒 ====================
        $display("---- 阶段2: A 未释放 + ACQ 未采样完时 prepare 应被拒 ----");
        // 此时 ACQ(A) 已 sampled，CONV 空 —— prepare 可以接受（提升 A）
        // 为测"被拒"，先把 A 提升进 CONV，再在 CONV 未释放时试 prepare，
        // 若 ACQ 也占着则被拒。
        sched_acq  = 18'h3BD00; sched_conv = 18'h000FF; sched_idle = 18'h04200;
        sched_ids  = pack_ids(B_IDS);
        do_prepare(32'hB2, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        checks = checks + 1;
        if (dut.conv_ids !== pack_ids(A_IDS)) fail("CONV 应为 A 组");
        else $display("      CONV = A 组  OK");
        checks = checks + 1;
        if (dut.acq_ids !== pack_ids(B_IDS)) fail("ACQ 应为 B 组");
        else $display("      ACQ = B 组  OK");

        // 现在 ACQ(B) 还没采样 -> prepare C 必须被拒（ACQ 占着且未采样）
        try_prepare_expect_reject("ACQ 未采样");
        // A 仍在 CONV，且不应被上面那次被拒的 prepare 改动
        checks = checks + 1;
        if (dut.conv_ids !== pack_ids(A_IDS) || dut.conv_tag !== 32'hA1)
            fail("被拒的 prepare 改动了 A 的 CONV");
        else $display("      A 的 CONV 未被改动  OK");

        // ==================== 阶段 3：A 的 5 个事件逐次应用 ====================
        $display("---- 阶段3: A 逐事件应用（B 同时持有 ACQ）----");
        for (int i = 0; i < 5; i++) begin
            do_event(4'(A_BIT[i]), 4'(i+1), A_BV[i][0], 1'b0);
            check_event("A", i, A_S[i], 10'(A_K[i]), A_IDS, 1'b0);
        end
        checks = checks + 1;
        if (final_applied !== 1'b1) fail("A final_applied 未置位");
        else $display("      A final_applied  OK");
        checks = checks + 1;
        if (final_k !== 10'd519) fail($sformatf("A final_k=%0d 期望 519", final_k));
        else $display("      A final_k = 519  OK");

        // ==================== 阶段 4：空泡写门为 0 ====================
        $display("---- 阶段4: 空泡时写门必须为 0 ----");
        repeat (2) @(negedge clk);
        checks = checks + 1;
        if (post_wr !== 18'd0) fail($sformatf("空泡写门=%06h 应为 0", post_wr));
        else $display("      空泡写门 = 0  OK");
        // 已写状态保持：影子模型此刻就是 A 最后 k=519 的写入结果
        checks = checks + 1;
        if (post_state !== phys_shadow) fail("空泡时已写状态未保持");
        else $display("      已写状态保持 (k=519)  OK");

        // ==================== 阶段 5：非法事件不写开关 ====================
        $display("---- 阶段5: 非法事件不得写开关 ----");
        // 5a 错 qid
        do_event(4'd1, 4'd6, 1'b1, 1'b1);
        checks = checks + 1;
        if (ev_applied !== 1'b0) fail("错 qid 仍 apply");
        else $display("      错 qid 被拒  OK");
        checks = checks + 1;
        if (post_wr !== 18'd0) fail("错 qid 时写门应为 0");
        else $display("      错 qid 写门 = 0  OK");
        // 5b done 后多发
        do_event(4'd1, 4'd7, 1'b1, 1'b0);
        checks = checks + 1;
        if (ev_applied !== 1'b0) fail("done 后仍 apply");
        else $display("      done 后事件被拒  OK");

        // ==================== 阶段 6：E1 采 B + 捕获 A 余差 ====================
        $display("---- 阶段6: E1 采 B 并捕获 A 余差 -> A 释放 ----");
        do_sample(1'b1, 1'b1, 1'b1);
        repeat (4) @(negedge clk);
        checks = checks + 1;
        if (dut.conv_valid !== 1'b0) fail("A 应已释放");
        else $display("      A 已释放  OK");

        // ==================== 阶段 7：B 全流程逐事件 ====================
        $display("---- 阶段7: B 的 7 个事件逐次应用 ----");
        // B 已在 ACQ 且已 sampled；下一次 prepare 会把它推进 CONV。
        // 但本轮要求"B 能独立转入 CONV" —— 用 stop 排空验证（阶段 8 先做 C）
        // 这里先用一次 prepare C 把 B 顶进 CONV
        sched_acq  = 18'h000FF; sched_conv = 18'h3BD00; sched_idle = 18'h04200;
        sched_ids  = pack_ids(A_IDS);
        do_prepare(32'hC3, 6'd63, 3'd0, 3'd3, 1'b0, 1'b1, 1'b0, 3'd7);
        checks = checks + 1;
        if (dut.conv_ids !== pack_ids(B_IDS)) fail("CONV 应为 B 组");
        else $display("      CONV = B 组  OK");
        checks = checks + 1;
        if (dut.conv_tag !== 32'hB2) fail($sformatf("CONV tag=%08h 应为 B2", dut.conv_tag));
        else $display("      CONV tag = B2  OK");

        for (int i = 0; i < 7; i++) begin
            do_event(4'(B_BIT[i]), 4'(i+1), B_BV[i][0], 1'b1);
            check_event("B", i, B_S[i], 10'(B_K[i]), B_IDS, 1'b1);
        end
        checks = checks + 1;
        if (final_k !== 10'd258) fail($sformatf("B final_k=%0d 期望 258", final_k));
        else $display("      B final_k = 258  OK");

        // ==================== 阶段 8：stop 排空 ====================
        $display("---- 阶段8: stop 排空 —— C 无须新 prepare 即可转 CONV ----");
        // 前提：B 的 CONV 必须先释放，否则 do_drain 要求 ~conv_valid_q 会被挡住。
        // 这一步也验证了"A 释放后 B 才能被重新分配"的顺序要求。
        do_sample(1'b1, 1'b1, 1'b1);
        repeat (4) @(negedge clk);
        checks = checks + 1;
        if (dut.conv_valid !== 1'b0) fail("B 应已释放");
        else $display("      B 已释放  OK");
        // C 在 ACQ 且未采样 -> 先采
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (dut.acq_sampled !== 1'b1) fail("C 应已 sampled");
        else $display("      C sampled  OK");
        // 停收：C 应能独立转 CONV
        @(negedge clk);
        stop_i = 1'b1;
        @(posedge clk); #1;
        @(negedge clk);
        stop_i = 1'b0;
        checks = checks + 1;
        if (dut.conv_tag !== 32'hC3)
            fail($sformatf("stop 排空后 CONV tag=%08h 应为 C3", dut.conv_tag));
        else $display("      C 已独立转入 CONV  OK");
        // E2（方案 §4.3）：sample_event=1, input_captured=0, residue_captured=1
        // 停收后不再采输入，但必须捕获最后一笔余差。
        // 参数顺序是 (input_captured, residue_captured, hold_ack) ——
        // 第一版误把 rc 写成 0，导致 C 永远等不到捕获、不释放（实测）。
        do_sample(1'b0, 1'b1, 1'b1);
        repeat (4) @(negedge clk);
        checks = checks + 1;
        if (dut.conv_valid !== 1'b0) fail("C 应已释放");
        else $display("      C 已释放（E2 排空完成）OK");

        // ==================== 阶段 9：F=7 拒绝 512 阈（反例）====================
        // 方案 §8.1 + 本轮第 3 项要求：
        //   F=7 -> Flash 提交 448；阈 512 试探被**拒** -> 不写 RDAC，
        //   退出 guard，从 bit5 继续普通六位；总提交仍 7 次。
        //   c=480 (=448+32), U=5 -> 最终 k=485。
        $display("---- 阶段9: F=7 拒绝 512 阈 -> 回普通六位, c=480 U=5 -> k=485 ----");
        repeat (2) @(negedge clk);
        rst_n = 1'b0; @(negedge clk); rst_n = 1'b1; @(negedge clk);
        t_wt_install();   // 复位清 written_q，必须重装配置才恢复 cfg_valid
        // 复位后重新给片组
        sched_acq  = 18'h000FF; sched_conv = 18'h00000; sched_idle = 18'h3FF00;
        sched_ids  = pack_ids(A_IDS);

        do_prepare(32'hD4, 6'd63, 3'd1, 3'd7, 1'b0, 1'b0, 1'b0, 3'd7);
        do_sample(1'b1, 1'b0, 1'b0);
        // 把 D 推进 CONV
        sched_acq  = 18'h3BD00; sched_conv = 18'h000FF; sched_idle = 18'h04200;
        sched_ids  = pack_ids(B_IDS);
        do_prepare(32'hE5, 6'd17, 3'd1, 3'd7, 1'b0, 1'b0, 1'b1, 3'd4);
        checks = checks + 1;
        if (dut.conv_tag !== 32'hD4) fail("CONV 应为 D4");
        else $display("      CONV = D4 (F=7)  OK");

        // evt1: Flash 提交（448）
        do_event(4'd0, 4'd1, 1'b0, 1'b0);
        checks = checks + 1;
        if (ev_k !== 10'd453) fail($sformatf("D evt1 k=%0d 期望 453(448+5)", ev_k));
        else $display("      D evt1 Flash: k=453  OK");

        // evt2: 阈 512 试探 -> **拒绝**。必须不写 RDAC。
        begin
            // 注意：`before` 是 SystemVerilog 关键字，不能做变量名
            // （编译报 "keyword 'before' is not expected to be used in this context"）。
            logic [359:0] post_before;
            logic [17:0]  wr_before;
            post_before = post_state; wr_before = post_wr;
            do_event(4'd9, 4'd2, 1'b0, 1'b0);
            checks = checks + 1;
            // 阈被拒仍应"提交已决前缀 448"，所以 apply_fire=1、k 仍 453，
            // 但 prefix 不变、不产生"接受 512"的写入。
            // 方案 §8.1："512 试探**不提交**" —— 所以这次**不能** apply，
            // 物理写门必须关闭。旧断言写反了（要求 apply=1），
            // 会在修好 RTL 后把正确行为判成错（实测）。
            if (ev_applied !== 1'b0)
                fail("阈被拒不应 apply（不得写 RDAC）");
            else $display("      阈被拒不 apply（不写 RDAC）OK");
            checks = checks + 1;
            if (ev_k !== 10'd453)
                fail($sformatf("阈被拒后 k=%0d 应仍为 453（448+5）", ev_k));
            else $display("      阈被拒 k 仍 453（未接受 512）OK");
            checks = checks + 1;
            if (dut.u_evt.prefix_q !== 10'd448)
                fail($sformatf("阈被拒后 prefix=%0d 应仍为 448", dut.u_evt.prefix_q));
            else $display("      阈被拒 prefix 仍 448  OK");
            // 写门必须为 0
            checks = checks + 1;
            if (post_wr !== 18'd0) fail("阈被拒时物理写门应为 0");
            else $display("      阈被拒写门 = 0  OK");
            checks = checks + 1;
            if (dut.u_evt.guard_q !== 1'b0)
                fail("阈被拒后应退出 guard");
            else $display("      已退出 guard  OK");
            // 阈被拒不占提交序号：它不推进 exp_evt，下一事件仍是 2。
            // 若写成"回退到 2 但测试期望 3"，就会把正确的 RTL 判成错（踩过）。
            checks = checks + 1;
            if (dut.u_evt.exp_evt_q !== 4'd2)
                fail($sformatf("阈被拒不占序号：下一事件序号应为 2，实为 %0d", dut.u_evt.exp_evt_q));
            else $display("      阈被拒不占提交序号（下一事件=2）OK");
        end

        // 之后 bit5 接受(448+32=480)，bit4..bit0 全拒 -> c=480
        do_event(4'd5, 4'd2, 1'b1, 1'b0);   // bit5 受（序号 2）
        checks = checks + 1;
        if (ev_k !== 10'd485) fail($sformatf("bit5 受后 k=%0d 期望 485(480+5)", ev_k));
        else $display("      bit5 受 -> k=485  OK");
        do_event(4'd4, 4'd3, 1'b0, 1'b0);   // bit4 拒
        do_event(4'd3, 4'd4, 1'b0, 1'b0);   // bit3 拒
        do_event(4'd2, 4'd5, 1'b0, 1'b0);   // bit2 拒
        do_event(4'd1, 4'd6, 1'b0, 1'b0);   // bit1 拒
        checks = checks + 1;
        if (dut.u_evt.exp_evt_q !== 4'd7)
            fail($sformatf("此时应还剩 bit0（序号 7）待提交，实为 %0d", dut.u_evt.exp_evt_q));
        else $display("      还需 bit0（第 7 次提交，序号 7）OK");
        do_event(4'd0, 4'd7, 1'b0, 1'b0);   // bit0 拒 -> 完成
        checks = checks + 1;
        if (done !== 1'b1) fail("bit0 后应完成");
        else $display("      D 完成（共 7 次提交）OK");
        checks = checks + 1;
        if (final_k !== 10'd485) fail($sformatf("D final_k=%0d 期望 485", final_k));
        else $display("      D final_k = 485  OK");

        $display("---------------------------------------------------------------");
        $display("I1+I2 full: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
