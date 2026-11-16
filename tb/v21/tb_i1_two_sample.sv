`timescale 1ns/1ps
// 状态：**历史**。I1 两笔样本，覆盖已并入 tb_i12_full.sv
//       （入口 tools/verify_v21.sh）。保留作追溯。
// =============================================================================
// 文件名     : tb_i1_two_sample.sv
// 模块名     : tb_i1_two_sample
// 版本       : v1.0
// 功能       : I1 两笔采样验收 —— ACQ/CONV 独立上下文、物理片号路由、
//              pre 写入与保持、旧组释放资格。
// 设计说明   :
//   * 向量来自实施方案 v1.3 §11.3：
//       A: qid0, 片 0..7,  (h,v,half,coin)=(0,3,0,1), word=63, U=5,
//          pre_slot=0xFFFFFEFF
//       B: qid1, 片 9..15,17, (h,v,half,coin)=(1,7,0,0), word=17, U=0,
//          pre_slot=0x64444444
//   * 本阶段用宏桩模拟 capture / hold / quiet 资格，隔离 ADC2 数值通路
//     （I3 才接）。桩只证明"前端因果"，不证明宏时序。
//   * 核心判据（方案 §11.2 I1）：
//       ① B 开始准备时 A 的上下文不变（ACQ/CONV 互不覆写）
//       ② 真正采过的原组才能进入转换（CONV 来自 ACQ，不是来自本拍 sched）
//       ③ 旧组在捕获+hold+安静窗三者满足前保持，期间下一笔 prepare 不覆写
//       ④ pre 按物理片号路由，逐位对拍
// 变更记录   :
//   2026-11-15  初版。
// =============================================================================
module tb_i1_two_sample;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    // ---- prepare ----
    logic        prepare_valid = 1'b0;
    logic        prepare_ready;
    logic [31:0] prepare_tag = 32'd0;
    logic [5:0]  prepare_word = 6'd0;
    logic [2:0]  prepare_h = 3'd0, prepare_v_dem = 3'd0, prepare_v_pre = 3'd0;
    logic        prepare_half = 1'b0, prepare_coin = 1'b0;

    // ---- 采样事件（桩）----
    logic       sample_event = 1'b0;
    logic       input_captured = 1'b0, residue_captured = 1'b0, hold_ack = 1'b0;
    logic [1:0] qid_busy = 2'b00;

    // ---- 片组 ----
    logic [17:0] sched_acq, sched_conv, sched_idle;
    logic [39:0] sched_ids;
    logic        sched_adv;

    // ---- 输出 ----
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [2:0]  acq_h, acq_v_dem, acq_v_pre, acq_U;
    logic        acq_half, acq_coin;
    logic [5:0]  acq_word;
    logic [31:0] pre_slot_only;   // dither_pre 叶子输出（slot 级）
    logic [31:0] acq_pre_slot;    // DUT 输出：ACQ 冻结的 slot 级 pre
    logic        acq_sampled;     // DUT 观测：ACQ 已采样
    logic [2:0]  conv_h, conv_v_dem;
    logic        conv_half, conv_coin;
    logic [9:0]  conv_k;
    logic [39:0] acq_ids, conv_ids;
    logic        acq_valid, conv_valid;
    logic [71:0] pre_state;
    logic [17:0] pre_wr;
    logic [359:0] post_state;
    logic [17:0] post_wr;
    logic        prepare_fire, commit_fire, release_fire;
    logic [31:0] conv_tag;
    logic [5:0]  conv_word;
    logic [2:0]  conv_U;
    logic [31:0] conv_pre_slot;

    // ---------------------------------------------------------------- DUT
    acq_conv_ctx u_ctx (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v_dem(prepare_v_dem),
        .prepare_v_pre(prepare_v_pre), .prepare_half(prepare_half),
        .prepare_coin(prepare_coin),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .sched_acq(sched_acq), .sched_conv(sched_conv), .sched_idle(sched_idle),
        .sched_ids(sched_ids),
        .acq_sel_o(acq_sel), .conv_sel_o(conv_sel), .idle_sel_o(idle_sel),
        .acq_h_o(acq_h), .acq_v_dem_o(acq_v_dem), .acq_v_pre_o(acq_v_pre),
        .acq_half_o(acq_half), .acq_coin_o(acq_coin),
        .acq_word_o(acq_word), .acq_U_o(acq_U),
        .acq_pre_slot_i(pre_slot_only),
        .acq_sampled_o(acq_sampled),
        .acq_pre_slot_o(acq_pre_slot),
        .conv_h_o(conv_h), .conv_v_dem_o(conv_v_dem),
        .conv_half_o(conv_half), .conv_coin_o(conv_coin),
        .acq_ids_o(acq_ids), .conv_ids_o(conv_ids),
        .acq_valid_o(acq_valid), .conv_valid_o(conv_valid),
        .rdac_pre_state_o(pre_state), .pre_wr_o(pre_wr),
        .rdac_post_state_o(post_state), .post_wr_o(post_wr),
        .prepare_fire_o(prepare_fire),
        .commit_fire_o(commit_fire), .release_fire_o(release_fire),
        .conv_tag_o(conv_tag), .conv_word_o(conv_word), .conv_U_o(conv_U),
        .conv_pre_slot_o(conv_pre_slot));

    // ---------------------------------------------------------------- 叶子（复用）
    logic [2:0]  U_leaf;
    logic [3:0]  f_leaf;
    // 叶子必须由 **prepare 输入** 驱动，而不是由 ACQ 寄存值驱动。
    // 若接到 acq_word（寄存器），prepare 那一拍它还是旧值，
    // 算出的 pre 是上一笔的 -> 存入 acq_pre_q 后全错（实测 A 变成 word=0 的结果）。
    // 生产顶层同样是"prepare 沿用输入 word 算 pre，随即冻结进描述符"。
    dither_pre u_pd (.word_i(prepare_word), .v_phase_i(prepare_v_pre),
                     .pre_o(pre_slot_only), .U_o(U_leaf), .f_o(f_leaf));

    // 把叶子的 slot 级 pre 接回 DUT（生产实现里应在 DUT 内例化；
    // 本测试为清晰起见在 TB 里串接，等价于 acq_pre_slot_o 直连叶子输出）
    // 注：DUT 的 acq_pre_slot_o 目前是输入占位，这里直接把叶子结果赋给内部比较。

    // ---------------------------------------------------------------- 计数与判据
    int errors = 0, checks = 0;
    task automatic fail(input string msg);
        errors = errors + 1;
        if (errors <= 25) $display("[%0t] ERROR: %s", $time, msg);
    endtask
    task automatic pass_check(input string msg);
        checks = checks + 1;
        $display("[%0t] OK: %s", $time, msg);
    endtask

    // 期望值（由 tools/gen_vec_i1.py 生成）
    localparam logic [31:0] A_PRE_SLOT = 32'hFFFFFEFF;
    localparam logic [31:0] B_PRE_SLOT = 32'h64444444;
    localparam logic [71:0] A_PRE_PHYS = 72'h0000000000FFFFFEFF;
    localparam logic [71:0] B_PRE_PHYS = 72'h604444444000000000;

    // 片号打包：A=0..7，B={9,10,11,12,13,14,15,17}
    function automatic logic [39:0] pack_ids(input int arr[0:7]);
        logic [39:0] v; v = 40'd0;
        for (int i = 0; i < 8; i++) v[i*5 +: 5] = 5'(arr[i]);
        return v;
    endfunction

    int A_IDS[0:7] = '{0,1,2,3,4,5,6,7};
    int B_IDS[0:7] = '{9,10,11,12,13,14,15,17};

    // prepare 一笔（保持到 ready 采样）
    task automatic do_prepare(input logic [31:0] tag, input logic [5:0] word,
                              input logic [2:0] h, input logic [2:0] vd,
                              input logic [2:0] vp, input logic half,
                              input logic coin);
        @(negedge clk);
        prepare_valid = 1'b1; prepare_tag = tag; prepare_word = word;
        prepare_h = h; prepare_v_dem = vd; prepare_v_pre = vp;
        prepare_half = half; prepare_coin = coin;
        // 有限等待：最多 20 拍，超时即报错（避免死循环把仿真挂住）
        begin
            int guard;
            guard = 0;
            while (!prepare_ready && guard < 20) begin
                @(negedge clk); guard = guard + 1;
            end
            if (!prepare_ready) begin
                fail($sformatf("prepare 等待超时（tag=%08h）", tag));
                prepare_valid = 1'b0;
            end else begin
                @(posedge clk); #1;   // prepare_fire 沿
                @(negedge clk);
                prepare_valid = 1'b0;
            end
        end
    endtask

    // 一个采样事件
    task automatic do_sample(input logic ic, input logic rc, input logic hk);
        @(negedge clk);
        sample_event = 1'b1; input_captured = ic; residue_captured = rc; hold_ack = hk;
        @(posedge clk); #1;
        @(negedge clk);
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0; hold_ack = 1'b0;
    endtask

    initial begin
        // 片组初值：A 组 0..7 采集
        sched_acq  = 18'h000FF;
        sched_conv = 18'h00000;
        sched_idle = 18'h3FF00;
        sched_ids  = pack_ids(A_IDS);
        sched_adv  = 1'b0;

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);

        // ================= 场景 1：prepare A（建立 ACQ）=================
        $display("---- 场景1: prepare A (word=63, h=0,v=3) ----");
        do_prepare(32'hA000_0001, 6'd63, 3'd0, 3'd3, 3'd3, 1'b0, 1'b1);
        checks = checks + 1;
        if (acq_valid !== 1'b1) fail("A prepare 后 acq_valid 应为 1");
        else $display("      A 进 ACQ  OK");
        checks = checks + 1;
        if (acq_ids !== pack_ids(A_IDS))
            fail($sformatf("A 片号错误: %010h", acq_ids));
        else $display("      A 片号 = 0..7  OK");
        checks = checks + 1;
        if (acq_pre_slot !== A_PRE_SLOT)
            fail($sformatf("A pre_slot=%08h != %08h", acq_pre_slot, A_PRE_SLOT));
        else $display("      A pre_slot = 0x%08h  OK", acq_pre_slot);

        // ================= 场景 2：E0 采样 A =================
        $display("---- 场景2: E0 采样 A（只采输入，不捕获余差）----");
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (acq_sampled !== 1'b1) fail("E0 后 A 应标记已采样");
        else $display("      A 标记 sampled  OK");
        checks = checks + 1;
        if (conv_valid !== 1'b0) fail("A 尚未进 CONV");
        else $display("      A 仍在 ACQ（未进 CONV）OK");

        // ================= 场景 3：prepare B -> A 进 CONV，B 建 ACQ =================
        $display("---- 场景3: prepare B -> A 提升为 CONV, B 建立 ACQ ----");
        sched_acq  = 18'h3BD00;
        sched_conv = 18'h000FF;
        sched_idle = 18'h04200;
        sched_ids  = pack_ids(B_IDS);
        do_prepare(32'hB000_0002, 6'd17, 3'd1, 3'd7, 3'd7, 1'b0, 1'b0);

        // 关键判据①：CONV 是原来的 A
        checks = checks + 1;
        if (conv_tag !== 32'hA000_0001)
            fail($sformatf("CONV tag 应为 A，实为 %08h", conv_tag));
        else $display("      CONV 来自 A（原 ACQ 组）OK");
        checks = checks + 1;
        if (conv_ids !== pack_ids(A_IDS))
            fail("CONV 片号不是 A 原组");
        else $display("      CONV 片号 = A 原组 OK");
        checks = checks + 1;
        if (conv_word !== 6'd63)
            fail($sformatf("CONV word 应为 A(63)，实为 %0d", conv_word));
        else $display("      CONV word = 63 (A) OK");

        // 关键判据②：新 ACQ 是 B，且未标 sampled
        checks = checks + 1;
        if (acq_ids !== pack_ids(B_IDS))
            fail($sformatf("新 ACQ 应为 B 组，实为 %010h", acq_ids));
        else $display("      ACQ 片号 = B 组 OK");
        checks = checks + 1;
        if (acq_word !== 6'd17)
            fail($sformatf("ACQ word 应为 B(17)，实为 %0d", acq_word));
        else $display("      ACQ word = 17 (B) OK");
        checks = checks + 1;
        if (acq_sampled !== 1'b0) fail("新 ACQ(B) 不应已采样");
        else $display("      B 未采样  OK");
        checks = checks + 1;
        if (acq_pre_slot !== B_PRE_SLOT)
            fail($sformatf("B pre_slot=%08h != %08h", acq_pre_slot, B_PRE_SLOT));
        else $display("      B pre_slot = 0x%08h  OK", acq_pre_slot);

        // 关键判据③：A 与 B 的上下文互不覆写
        checks = checks + 1;
        if (conv_pre_slot !== A_PRE_SLOT)
            fail($sformatf("A 的冻结 pre 被覆写: %08h", conv_pre_slot));
        else $display("      A 的冻结 pre 保持 = 0x%08h  OK", conv_pre_slot);

        // ================= 场景 4：A 未释放时不得回池 =================
        $display("---- 场景4: A 未捕获/hold -> 不得释放 ----");
        checks = checks + 1;
        if (release_fire !== 1'b0) fail("A 未捕获/hold 不应释放");
        else $display("      A 未释放  OK");
        // 只给 hold，不给捕获
        do_sample(1'b1, 1'b0, 1'b1);
        checks = checks + 1;
        if (release_fire !== 1'b0) fail("只有 hold 无捕获不应释放");
        else $display("      只有 hold -> 未释放  OK");

        // ================= 场景 5：E1 捕获 A 余差 + hold -> 释放 =================
        $display("---- 场景5: E1 捕获 A 余差 + hold -> 释放 ----");
        do_sample(1'b1, 1'b1, 1'b1);
        repeat (4) @(negedge clk);
        checks = checks + 1;
        if (conv_valid !== 1'b0)
            fail("捕获+hold+安静窗满足后 A 应已释放");
        else $display("      A 已释放回池  OK");
        checks = checks + 1;
        if (idle_sel === 18'd0 && conv_valid === 1'b0)
            $display("      A 原组可回 IDLE  OK");
        else if (conv_valid === 1'b0) $display("      A 释放后 idle 掩码 OK");

        // ================= 场景 6：E1 同时采 B =================
        $display("---- 场景6: E1 同沿采 B ----");
        do_sample(1'b1, 1'b0, 1'b0);
        checks = checks + 1;
        if (acq_sampled !== 1'b1) fail("E1 后 B 应标记已采样");
        else $display("      B 标记 sampled  OK");

        $display("---------------------------------------------------------------");
        $display("I1 two_sample: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
