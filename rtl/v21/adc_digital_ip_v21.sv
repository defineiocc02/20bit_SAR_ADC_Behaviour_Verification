// =============================================================================
// 文件名     : adc_digital_ip_v21.sv
// 模块名     : adc_digital_ip_v21
// 版本       : v1.0
// 功能       : ISSCC 2024 §9.8 数字 IP 集成顶层（方案 v1.4 §11.1 建议路径）。
//              把 D1 片组 / D2 事件 / D3 DEM+bridge / D4 预置 串成一条可对拍的通路。
// 设计说明   :
//   * 层次：
//       acq_conv_ctx  —— 两笔上下文、物理片号路由（I1）
//       sar_evt_v21   —— v2.1 事件协议、k=prefix+U（I2）
//       dither_pre    —— 字 → 八片 pre（复用）
//       dem_mask_v3   —— k+相位+coin → 160 位 slot 级 post（复用）
//       sched18       —— 8/8/2 片组递推（复用）
//
//   * 两笔流水怎么走（方案 §4.3）：
//       E0: A 被采样 -> A 记 sampled
//       prepare B: A 从其 ACQ 提升为 CONV（带着它冻结的 k 流），B 建立 ACQ
//       A 的事件从 prepare A 起就持续应用（CONV 上下文里那份）
//       物理 post 写门只对 CONV 的片号有效 —— 这是本层的核心路由决定
//
//   * 为什么 post 必须打到 CONV 而不是 ACQ：
//     论文 p.182 "8 sDACs converting" —— RDAC 在转换期才需要开关命令。
//     ACQ 组此刻正在采样，它的选通由 pre 决定，不是 post。
//
//   * 与 leaf 的接口：dem_mask_v3 只认 slot（0..7），本层按**冻结片号**
//     把 slot 级 160 位展开成物理 360 位（18 片 × 20）。
//     展开用 generate 逐位做，避免变量下标（此前踩过 130 个未时钟寄存器的坑）。
//
//   * 本轮范围：I1+I2 联调。ADC2 捕获与 20 位重构在 I3/I4。
// 变更记录   :
//   2026-11-15  初版。I1+I2 联调：逐事件物理开关零差异对拍。
// =============================================================================
module adc_digital_ip_v21 (
    input  logic        clk,
    input  logic        rst_n,

    // ---------------------------------------------------------------- 事务
    input  logic        prepare_valid,
    output logic        prepare_ready,
    input  logic [31:0] prepare_tag,
    input  logic [5:0]  prepare_word,
    input  logic [2:0]  prepare_h,
    input  logic [2:0]  prepare_v,
    input  logic        prepare_half,
    input  logic        prepare_coin,
    input  logic [2:0]  prepare_U,       // 由 word 派生，本层只做一致性检查
    input  logic [2:0]  prepare_flash,   // 本笔 Flash 码（随描述符冻结）
    input  logic        prepare_qid,     // 本笔 qid（随描述符冻结）
    input  logic        stop_i,          // 停收新样本：已采过的 ACQ 仍要能转 CONV 排空

    // ---------------------------------------------------------------- 采样事件
    input  logic        sample_event,
    input  logic        input_captured,
    input  logic        residue_captured,
    input  logic        hold_ack,

    // ---------------------------------------------------------------- 事件入口
    input  logic        ev_start_i,      // 分配一笔事件上下文（= prepare 时同沿）
    input  logic        ev_bit_valid_i,
    input  logic        ev_bit_i,
    input  logic [3:0]  ev_bit_index_i,
    input  logic [3:0]  ev_event_index_i,
    input  logic        ev_qid_i,
    input  logic        ev_flash_valid_i,
    input  logic [2:0]  ev_flash_code_i,

    // ---------------------------------------------------------------- 片组
    // 生产态用内部 sched18；测试态可把 sched_ext_sel=1 用外部驱动（便于定向向量）
    input  logic        sched_ext_sel,
    input  logic [17:0] sched_acq,
    input  logic [17:0] sched_conv,
    input  logic [17:0] sched_idle,
    input  logic [39:0] sched_ids,
    input  logic        sched_seed_we,
    input  logic [31:0] sched_seed,

    output logic [17:0] acq_sel_o,
    output logic [17:0] conv_sel_o,
    output logic [17:0] idle_sel_o,

    // ---------------------------------------------------------------- 物理宏接口
    output logic [71:0]  rdac_pre_state_o,
    output logic [17:0]  pre_wr_o,
    output logic [359:0] rdac_post_state_o,
    output logic [17:0]  post_wr_o,

    // ---------------------------------------------------------------- I3：ADC2
    input  logic         adc2_valid_i,
    output logic         adc2_ready_o,
    input  logic [12:0]  adc2_raw_i,
    input  logic         adc2_range_err_i,
    output logic         adc2_capture_arm_o,

    // ---------------------------------------------------------------- 节点模型快照出口
    //
    // **方案目标 I3 第 2 条**："将节点宏和模拟真值 Vin 放在仿真边界"。
    // 所以 DUT 不再内部例化 adc2_node_macro；它只把"本笔的不可变快照"
    // 送出去，由仿真侧的节点模型（代表模拟宏）自行算 y 与 raw，
    // 再通过 adc2_valid/ready 把手握结果送回。
    //
    // 这样做有三个好处：
    //   ① 语义正确 —— 节点模型是模拟宏的替身，不该进数字综合
    //   ② 解决 compile_ultra 超时（96 位除法 + 8x19 权重表不再进网表）
    //   ③ 快照化后 DUT 与模型之间是可对拍的明确接口
    output logic [159:0] node_post_o,     // 本笔最后一次实际应用的 slot 级 post
    output logic [31:0]  node_pre_o,      // 本笔采样时保存的 slot 级 pre
    output logic [39:0]  node_ids_o,      // 本笔 8 个物理片号
    output logic         node_qid_o,
    output logic [31:0]  node_tag_o,
    output logic         node_valid_o,    // 快照有效（本笔已 final apply）
    output logic [2:0]   node_h_o,        // 本笔冻结的 DEM 相位（供追溯）
    output logic [2:0]   node_v_o,

    output logic         adc2_rsp_valid_o,
    output logic [31:0]  adc2_rsp_tag_o,
    output logic         adc2_rsp_qid_o,
    output logic [12:0]  adc2_rsp_raw_o,
    output logic         adc2_rsp_range_o,
    output logic         adc2_late_err_o,
    output logic         adc2_release_ok_o,   // hold 到即可释放（与 raw 解耦）

    // ---------------------------------------------------------------- D5 配置与输出
    // 权重表（18×20 Q30）。方案 §9.4：权重来自显式装载。
    input  logic [39:0]  w_main_tbl [0:17][0:15],
    input  logic [39:0]  w_sub_tbl  [0:17][0:3],
    input  logic [7:0]   cfg_epoch,
    input  logic signed [26:0] b_q24_cfg,

    // 20 位结果（每笔恰好退休一次）
    output logic         out_valid,
    output logic [19:0]  out_code,
    output logic         out_sat,
    output logic         out_err,
    output logic [31:0]  out_tag,

    // ---------------------------------------------------------------- 观测
    output logic        apply_fire_o,
    output logic [9:0]  k_o,
    output logic        k_bad_o,
    output logic        final_applied_o,
    output logic [9:0]  final_k_o,
    output logic        err_o,
    output logic        done_o,
    output logic [31:0] tag_o,
    output logic        qid_o,
    output logic [159:0] post_slot_o    // slot 级 160 位（便于与参考对拍）
);

    // ---------------------------------------------------------------- 两笔上下文
    logic [2:0]  acq_h, acq_v, acq_v_pre, acq_U;
    logic        acq_half, acq_coin;
    logic [5:0]  acq_word;
    logic [31:0] acq_pre_slot;
    logic [39:0] acq_ids, conv_ids;
    logic        acq_valid, acq_sampled, conv_valid;
    logic        prepare_fire, commit_fire, release_fire, sched_adv;
    logic [2:0]  conv_h, conv_v;
    logic        conv_half, conv_coin;
    logic [5:0]  conv_word;
    logic [2:0]  conv_U_w;
    logic [31:0] conv_tag, conv_pre_slot;
    logic [31:0] acq_tag;
    logic [2:0]  acq_flash, conv_flash;
    logic        acq_qid, conv_qid;

    // ACQ 的 v_pre 与 v_dem 在生产态相同；测试隔离时可分别给。
    logic [2:0]  prep_v_pre;
    assign prep_v_pre = prepare_v;

    // dither_pre 叶子：由 prepare 输入驱动（不是 ACQ 寄存值，
    // 否则 prepare 那拍读到的是上一笔的 word）
    logic [31:0] pre_slot_leaf;
    logic [2:0]  U_leaf;
    logic [3:0]  f_leaf;
    dither_pre u_pd (
        .word_i   (prepare_word),
        .v_phase_i(prep_v_pre),
        .pre_o    (pre_slot_leaf),
        .U_o      (U_leaf),
        .f_o      (f_leaf));

    // ---------------------------------------------------------------- D1 片组
    logic [17:0] s18_acq, s18_conv, s18_idle;
    logic [39:0] s18_ids;
    logic [31:0] s18_rng;

    sched18 u_sched (
        .clk(clk), .reset_n(rst_n),
        .seed_we(sched_seed_we), .seed_i(sched_seed),
        // 用 sched_adv_o（= 任何被接受的 prepare），不是 commit_fire。
        // 首笔时 commit_fire=0，sched18 不会推进（审计问题 1 的根因）。
        .adv(sched_adv),
        .acq_mask_o(s18_acq), .conv_mask_o(s18_conv), .idle_mask_o(s18_idle),
        .ids_o(s18_ids), .rng_o(s18_rng));

    // 片组来源选择：生产走内部 sched18，定向测试可外部覆盖
    wire [17:0] use_acq  = sched_ext_sel ? sched_acq  : s18_acq;
    wire [17:0] use_conv = sched_ext_sel ? sched_conv : s18_conv;
    wire [17:0] use_idle = sched_ext_sel ? sched_idle : s18_idle;
    wire [39:0] use_ids  = sched_ext_sel ? sched_ids  : s18_ids;

    acq_conv_ctx u_ctx (
        .clk(clk), .rst_n(rst_n),
        .prepare_valid(prepare_valid), .prepare_ready(prepare_ready),
        .prepare_tag(prepare_tag), .prepare_word(prepare_word),
        .prepare_h(prepare_h), .prepare_v_dem(prepare_v),
        .prepare_v_pre(prep_v_pre), .prepare_half(prepare_half),
        .prepare_coin(prepare_coin), .prepare_flash(prepare_flash),
        .prepare_qid(prepare_qid),
        .stop_i(stop_i),
        .sample_event(sample_event), .input_captured(input_captured),
        .residue_captured(residue_captured), .hold_ack(hold_ack),
        .sched_acq(use_acq), .sched_conv(use_conv), .sched_idle(use_idle),
        .sched_ids(use_ids),
        .acq_sel_o(acq_sel_o), .conv_sel_o(conv_sel_o), .idle_sel_o(idle_sel_o),
        .acq_h_o(acq_h), .acq_v_dem_o(acq_v), .acq_v_pre_o(acq_v_pre),
        .acq_half_o(acq_half), .acq_coin_o(acq_coin),
        .acq_word_o(acq_word), .acq_U_o(acq_U),
        .acq_pre_slot_i(pre_slot_leaf), .acq_pre_slot_o(acq_pre_slot),
        .acq_tag_o(acq_tag), .acq_flash_o(acq_flash), .conv_flash_o(conv_flash),
        .acq_qid_o(acq_qid), .conv_qid_o(conv_qid),
        .conv_h_o(conv_h), .conv_v_dem_o(conv_v),
        .conv_half_o(conv_half), .conv_coin_o(conv_coin),
        .conv_word_o(conv_word),
        .acq_ids_o(acq_ids), .conv_ids_o(conv_ids),
        .acq_valid_o(acq_valid), .acq_sampled_o(acq_sampled),
        .conv_valid_o(conv_valid),
        .rdac_pre_state_o(rdac_pre_state_o), .pre_wr_o(pre_wr_o),
        .rdac_post_state_o(), .post_wr_o(),
        .prepare_fire_o(prepare_fire), .commit_fire_o(commit_fire),
        .sched_adv_o(sched_adv),
        .release_fire_o(release_fire),
        .conv_tag_o(conv_tag), .conv_U_o(conv_U_w),
        .conv_pre_slot_o(conv_pre_slot));

    // ---------------------------------------------------------------- 事件协议
    logic apply_fire_w, last_evt_w;
    logic [9:0] k_w;
    logic k_bad_w, final_applied_w, err_w, done_w;
    logic [9:0] final_k_w;
    logic [31:0] tag_w;
    logic qid_w;

    // U 一致性：外部 prepare_U 必须与 word 派生值一致。
    // 方案 §4.2 要求"不接受互相矛盾的外部 U"。
    wire [2:0] U_from_word = {1'b0, prepare_word[5]} * 3'd4 + {2'b0, prepare_word[3]};
    wire       U_conflict   = (prepare_U != U_from_word);

    // ---------------------------------------------------------------- 事件上下文归属
    //
    // **关键决定**：事件层绑定到「正在转换的那一笔」（CONV），不是绑定到 prepare。
    //
    // 踩过的坑：最初把 ev_start_i 直接当 start_i，于是 prepare B 时会把事件层
    // 重置成 B 的 word/U —— 但此刻 A 刚进 CONV，还要用 **A 的** k 流逐事件应用。
    // 结果 A 的 5 个事件全部算成 B 的 k=256（实测）。
    //
    // 正确的因果：A 的 Flash/U 在 A 进 CONV 那一刻才成为事件层的输入。
    // 所以 start 由 commit_fire（CONV 建立沿）触发，U 用 CONV 冻结值。
    //
    // 方案 §4.2："不能因为 qid 已重用就覆盖旧 ADC2 的上下文"—— 同理，
    // 新 prepare 不得覆盖正在转换那一笔的事件上下文。
    // U 必须取 **ACQ 侧** 的 word：commit_fire 那一沿，ACQ 里还是"被提升那一笔"
    // （A），CONV 的寄存器要到该沿之后才更新。若取 conv_word，拿到的是上一笔的 U。
    //
    // 时间线（commit_fire 沿，非阻塞赋值生效前）：
    //   acq_word_q = A 的 63   <- 用它算 U，正确
    //   conv_word_q = 上一笔   <- 用它算 U，错
    wire [2:0] U_ev = {1'b0, acq_word[5]} * 3'd4 + {2'b0, acq_word[3]};

    // 事件层同样用 ACQ 的 tag（该沿 ACQ 仍是 A）。
    // Flash 码由适配层按本笔绑定（真实 Flash 在 I3 接），这里保留外部输入。
    logic ev_start_conv;
    assign ev_start_conv = commit_fire;

    sar_evt_v21 u_evt (
        .clk(clk), .rst_n(rst_n),
        .start_i(ev_start_conv), .abort_i(1'b0),
        .tag_i(acq_tag), .qid_i(acq_qid), .upper_i(U_ev),
        .flash_valid_i(ev_start_conv), .flash_code_i(acq_flash),
        .bit_valid_i(ev_bit_valid_i), .bit_i(ev_bit_i),
        .bit_index_i(ev_bit_index_i), .event_index_i(ev_event_index_i),
        .sar_qid_i(ev_qid_i), .sar_final_i(1'b0),
        .ev_valid_o(), .ev_event_index_o(), .ev_bit_index_o(),
        .k_o(k_w), .k_bad_o(k_bad_w), .apply_fire_o(apply_fire_w),
        .last_evt_o(last_evt_w),
        .final_applied_o(final_applied_w), .final_k_o(final_k_w),
        .tag_o(tag_w), .qid_o(qid_w), .err_o(err_w), .done_o(done_w));

    assign apply_fire_o   = apply_fire_w;
    assign k_o            = k_w;
    assign k_bad_o        = k_bad_w;
    assign final_applied_o = final_applied_w;
    assign final_k_o      = final_k_w;
    assign err_o          = err_w | U_conflict;   // U 冲突也算错误
    assign done_o         = done_w;
    assign tag_o          = tag_w;
    assign qid_o          = qid_w;

    // ---------------------------------------------------------------- DEM + bridge
    // 用 **CONV 的冻结相位**：转换期才需要 post。
    logic [159:0] post_slot_w;
    dem_mask_v3 u_dem (
        .k_i        (k_w),
        .h_phase_i  (conv_h),
        .v_phase_i  (conv_v),
        .coin_i     (conv_coin),
        .acq_seq_i  ({conv_half, 3'b0}),
        .mask_o     (post_slot_w));

    assign post_slot_o = post_slot_w;

    // ---------------------------------------------------------------- 物理 post 路由
    // slot 级 160 位 -> 物理 360 位（18 片 × 20）。
    // 只对 **CONV 的冻结片号** 写，且只在 apply_fire 那拍写门有效。
    // 逐位 generate 展开，避免变量下标被推断成锁存器。
    logic [359:0] post_phys_c;
    logic [17:0]  post_wr_c;
    genvar gp, gj;
    generate
        for (gp = 0; gp < 18; gp++) begin : g_post_p
            for (gj = 0; gj < 20; gj++) begin : g_post_j
                logic v;
                always_comb begin
                    v = 1'b0;
                    if (apply_fire_w && conv_valid) begin
                        for (int s = 0; s < 8; s++)
                            if (conv_ids[s*5 +: 5] == 5'(gp))
                                v = post_slot_w[s*20 + gj];
                    end
                end
                assign post_phys_c[gp*20 + gj] = v;
            end
        end
    endgenerate

    generate
        for (gp = 0; gp < 18; gp++) begin : g_post_wr
            logic w;
            always_comb begin
                w = 1'b0;
                if (apply_fire_w && conv_valid)
                    for (int s = 0; s < 8; s++)
                        if (conv_ids[s*5 +: 5] == 5'(gp)) w = 1'b1;
            end
            assign post_wr_c[gp] = w;
        end
    endgenerate

    // ---------------------------------------------------------------- 同沿注册
    //
    // 方案 §4.1："写入以数字边界的同沿注册为准。某沿 apply_fire 时，
    // 沿后 post_wr[sid] 与新 rdac_post_state[20*sid +: 20] 一起有效。"
    //
    // 所以这里必须**寄存**，不能把组合结果直接送出去：
    //   · 组合直通时，apply_fire 撤掉写门就撤，适配层若在沿后采样会漏掉这次写；
    //   · 寄存后，写门与数据在 apply_fire 沿后一整拍同时有效，
    //     适配层有完整一拍可以消费（连续两拍高代表两次写入）。
    //   · 空泡/非法事件时写门为 0，已写状态保持（数据寄存器不更新）。
    logic [359:0] post_state_q;
    logic [17:0]  post_wr_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            post_state_q <= 360'd0;
            post_wr_q    <= 18'd0;
        end else begin
            // 写门每拍重新产生：本拍无合法应用则落回 0（不保留上一拍的写门）
            post_wr_q <= post_wr_c;
            // 数据只在有效写时更新，其余保持 —— "未选物理片保持原状态"
            for (int gp = 0; gp < 18; gp++) begin
                if (post_wr_c[gp])
                    post_state_q[gp*20 +: 20] <= post_phys_c[gp*20 +: 20];
            end
        end
    end

    assign rdac_post_state_o = post_state_q;
    assign post_wr_o         = post_wr_q;

    // ---------------------------------------------------------------- I3：ADC2
    //
    // wsum 的输出与有效标志要**前置声明**：arm 的门槛用 wsum_valid，
    // 捕获登记用 wsum_G/Wp/Wm。声明若落在使用之后，Verilog 会隐式推断成
    // 1 位 net —— 于是 G 永远只有 bit0（=0），D5 报 bad_g（踩过多次）。
    logic        wsum_valid;
    logic [63:0] wsum_G, wsum_Wp, wsum_Wm;

    // final post 与本笔冻结相位的锁存寄存器。
    // **必须在任何使用它们的 assign 之前声明**：Verilog 会把未声明的名字
    // 隐式推断成 1 位 net，于是赋值报 "Illegal behavioral left hand side"，
    // 或者静默只保留最低位（G_q30_w 上踩过同类坑）。
    logic [159:0] final_post_q;
    logic [2:0]   final_h_q, final_v_q;
    logic         final_coin_q, final_half_q;

    // 捕获归属：在本笔 CONV 被公共沿捕获余差时登记 owner。
    // 方案 §4.3："E1 锁 B 的输入，同时捕获 A 的余差" —— 捕获发生在
    // 下一笔的采样沿上，因此 capture_req 由 sample_event & residue_captured
    // 驱动，而 owner 记录的是**当前 CONV**那一笔。
    // ---------------------------------------------------------------- 沿前授权
    //
    // 方案目标 I3 第 1 条："根据本笔最后一次实际开关应用、RA 和可用捕获资源
    // 提前产生 arm。sample_event 和 residue_captured 仅用于登记已发生的捕获。"
    //
    // 所以 arm 的条件**不含** sample_event：
    //   · conv_valid        本笔转换上下文存在
    //   · final_applied_w   最后一次合法 apply 已经发生（不是"事件发完了"）
    //   · ~adc2_arm_done    本笔的 arm 还没发过（一次捕获一次授权）
    //                        否则会在整个 CONV 期间持续拉高，宏可能重复捕获。
    // arm 的语义是"**待捕获**"，不是一个单拍脉冲：
    //   · 置位：本笔 final apply 之后（且本笔还没捕过）
    //   · 清零：本笔的捕获**已经登记**（capture_reg_req），或换笔/复位
    //
    // 早期版本用"arm_req 一拍就把 done 置 1"，arm 只持续一拍，
    // 测试与真实宏都可能错过（实测 arm 检查失败）。
    // 让它在整个"可以捕获"的窗口内保持高电平，宏就有充足时间采到。
    logic adc2_arm_seen_q;
    // arm 的门槛里必须包含 wsum_valid：arm 表示"余差可以捕获了"，
    // 而捕获时要把本笔的 G/Wp/Wm 快照进 owner。若 arm 早于 wsum 有效，
    // 捕获就会把 0 存进 owner（实测 owner_G_q=0 -> D5 报 bad_g）。
    //
    // 时序链：T0 最后 apply -> T1 wsum 采数 -> T2 wsum 输出有效 -> arm
    // 这是"应用已算完再授权"，符合方案目标"根据本笔最后一次实际开关应用提前产生 arm"。
    wire  adc2_arm_req = conv_valid & final_applied_w & wsum_valid & ~adc2_arm_seen_q;

    // 沿后登记：只在公共沿确实报了 residue_captured 时登记
    wire  adc2_reg_req = sample_event & residue_captured;

    always_ff @(posedge clk or negedge rst_n) begin
        // **必须按样本换代清，不能只看 conv_valid 是否出现空闲拍。**
        //
        // 反例（独立 VCS 已复现）：A 回送后同一沿 release A + promote B，
        // `conv_valid` 由 1 保持为 1（没有空拍），于是 B 继承了 A 的
        // "已捕获" 状态：
        //     B: conv_valid=1 final=1 wsum_valid=1 owner_busy=0
        //     seen=1  arm=0     <- B 永远拿不到 arm
        // 对照组只要插一个空闲拍就正常（seen=0 arm=1）。
        //
        // 正确语义：arm_seen 属于**某一笔**，换笔就要清。
        // 换笔的判据是 `commit_fire`（A 的 CONV 被新一笔顶替那一沿），
        // 它覆盖"有空闲拍"和"同沿交接"两种情形。
        if (!rst_n)                    adc2_arm_seen_q <= 1'b0;
        else if (commit_fire)          adc2_arm_seen_q <= 1'b0;   // 换笔先清
        else if (adc2_reg_req)         adc2_arm_seen_q <= 1'b1;   // 已捕获 -> 关门
        else if (!conv_valid)          adc2_arm_seen_q <= 1'b0;   // 保守兜底
    end

    // 节点宏也在**沿后登记那一拍**求值（此时 final_post/pre 都已稳定）
    wire adc2_capture_req = adc2_reg_req & conv_valid;

    logic        adc2_arm, adc2_ready_w, adc2_hold_done, adc2_late_err;
    logic        adc2_release_ok;
    logic [63:0] rsp_G, rsp_Wp, rsp_Wm;
    logic        adc2_owner_busy;
    logic        rsp_valid, rsp_qid, rsp_range;
    logic [31:0] rsp_tag;
    logic [12:0] rsp_raw;

    adc2_capture u_adc2 (
        .clk(clk), .rst_n(rst_n),
        .capture_arm_req(adc2_arm_req),
        .capture_reg_req(adc2_capture_req),
        .capture_tag(conv_tag),
        .capture_qid(conv_qid),
        .capture_phase_h(final_h_q),
        .capture_phase_v(final_v_q),
        .capture_G_q30(wsum_G),
        .capture_Wp_q30(wsum_Wp),
        .capture_Wm_q30(wsum_Wm),
        .adc2_capture_arm_o(adc2_arm),
        .adc2_hold_ack_i(hold_ack),
        .adc2_valid_i(adc2_valid_i),
        .adc2_ready_o(adc2_ready_w),
        .adc2_raw_i(adc2_raw_i),
        .adc2_range_err_i(adc2_range_err_i),
        .rsp_valid_o(rsp_valid), .rsp_tag_o(rsp_tag), .rsp_qid_o(rsp_qid),
        .rsp_raw_o(rsp_raw), .rsp_range_o(rsp_range),
        .owner_busy_o(adc2_owner_busy), .hold_done_o(adc2_hold_done),
        .late_err_o(adc2_late_err), .release_ok_o(adc2_release_ok),
        .rsp_G_q30_o(rsp_G), .rsp_Wp_q30_o(rsp_Wp), .rsp_Wm_q30_o(rsp_Wm));

    // 节点行为宏：用**实际应用的 post**（post_state_q）与**采样时保存的 pre**
    // （conv_pre_q 经 conv_ids 路由后的 slot 级等价物）算余差。
    //
    // 注意：这里用 conv 的 slot 级 post（post_slot_w）而不是物理 360 位，
    // 因为宏按 slot 组织权重表；物理路由的正确性已由 I1/I2 逐位对拍证明。
    logic        node_calc_valid;

    logic [12:0] node_raw;
    logic        node_range;

    // ---------------------------------------------------------------- final post 锁存
    //
    // **关键**：节点宏必须用**最后一次实际应用**的 post，不能用当拍的
    // post_slot_w。原因：post_slot_w 是 dem_mask_v3 对当前 k 的组合输出，
    // 而 k 在最后一个事件之后还会随输入变化；捕获发生在下一笔的采样沿，
    // 那时 k_w 早已不是本笔的 final_k。
    //
    // 实测症状：直接用 post_slot_w 时 A 的 y 算出 -190.9 V（Wm≈64.3，
    // 约为名义 32.4 的两倍），raw=0。
    //
    // 方案 §8.2 明确要求："final apply 置 final_applied，并保存 D5 的 final_post；
    // 禁止 D5 从后来相位重算"。
    // final_post_q / final_h_q / final_v_q / final_coin_q / final_half_q
    // 的声明必须在**使用之前**（见文件上方 I3 段）。

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            final_post_q <= 160'd0;
            final_h_q    <= 3'd0;
            final_v_q    <= 3'd0;
            final_coin_q <= 1'b0;
            final_half_q <= 1'b0;
        end else if (apply_fire_w & last_evt_w) begin
            // **必须用 apply_fire_w 而不是 final_applied_w**：
            // final_applied_o 是 sar_evt_v21 里的寄存器输出，比 apply 晚整拍；
            // 那一拍 k_w 已经不再是本笔的 final k，post_slot_w 也就不是
            // 最后一次真实应用的 mask（实测锁到全 0）。
            // 方案 §8.2："final apply 置 final_applied，并保存 D5 的 final_post"
            // —— 保存动作要发生在**应用那一拍**。
            final_post_q <= post_slot_w;
            final_h_q    <= conv_h;
            final_v_q    <= conv_v;
            final_coin_q <= conv_coin;
            final_half_q <= conv_half;
        end
    end

    // ---------------------------------------------------------------- 生产侧权重和
    //
    // 节点宏（仿真边界）内部也算 Wm/Wp，但那一路不能回喂数字侧 ——
    // 否则等于用模拟侧结果做数字重构。生产侧必须自己算一份同口径的。
    //
    // **两个关键决定**（都是踩坑后改的）：
    //   ① 输入用 final_post_q（锁存值），不用 post_slot_w（组合）。
    //      post_slot_w 依赖 combinational 的 k_w，在采样沿附近会抖，
    //      实测 G_q30 采到 0 而 sum_valid 却是 1。
    //   ② calc_valid 用**电平**（conv 存在且 final apply 已发生），
    //      不用单拍脉冲。电平触发下 wsum 持续用稳定输入重算，
    //      输出在 2 拍后稳定并保持 —— 捕获那一刻必然读得到正确值。
    // **wsum 的触发要晚一拍**。
    //
    // 踩过的坑：原来用 `apply_fire_w & last_evt_w` 当 calc_valid。
    // 但 post_slot_w 依赖 k_w，而 k_w = prefix_accept + U 是**组合**的；
    // 在 apply_fire 那个 posedge，wsum 的 always_comb 采到的仍是
    // combinational 稳定前的值 —— 实测 sum_valid 置了 1 但 G_q30 是 0。
    //
    // 修法：把最后一个 apply 打一拍再触发 wsum。那一拍 k_w/post_slot_w
    // 已经稳定（它们在整个 CONV 期间保持不变），G/Wm/Wp 都能正确算出。
    wire         wsum_calc = conv_valid & final_applied_w;

    wsum_v21 u_wsum (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(wsum_calc),
        .ids_i(conv_ids),
        .pre_mask_i(conv_pre_slot),
        .post_mask_i(final_post_q),
        .w_main_tbl(w_main_tbl), .w_sub_tbl(w_sub_tbl),
        .sum_valid(wsum_valid),
        .G_q30(wsum_G), .Wp_q30(wsum_Wp), .Wm_q30(wsum_Wm));

    // 节点模型所需的本笔快照（全部来自 final apply 那一刻锁存的值）
    assign node_post_o  = final_post_q;
    assign node_pre_o   = conv_pre_slot;
    assign node_ids_o   = conv_ids;
    assign node_qid_o   = conv_qid;
    assign node_tag_o   = conv_tag;
    assign node_valid_o = conv_valid & final_applied_w;
    assign node_h_o     = final_h_q;
    assign node_v_o     = final_v_q;

    assign adc2_capture_arm_o = adc2_arm;
    assign adc2_ready_o       = adc2_ready_w;
    assign adc2_rsp_valid_o   = rsp_valid;
    assign adc2_rsp_tag_o     = rsp_tag;
    assign adc2_rsp_qid_o     = rsp_qid;
    assign adc2_rsp_raw_o     = rsp_raw;
    assign adc2_rsp_range_o   = rsp_range;
    assign adc2_late_err_o    = adc2_late_err;
    // ---------------------------------------------------------------- D5 重构
    //
    // 方案 §11.2 I4："按 I3 带回的同笔 raw、实际 pre、最后应用 post、
    // 物理片号与系数 epoch 求和、去扰、舍入与退休。"
    //
    // 权重和由 owner 快照带来（rsp_G/Wp/Wm），所以即使 CONV 已被下一笔
    // 替换，本笔仍用**自己的**权重和重构 —— 这是"同笔"语义的关键。
    recon_v21 u_recon (
        .clk(clk), .rst_n(rst_n),
        .in_valid(rsp_valid),
        .adc2_raw(rsp_raw),
        .Wm_q30(rsp_Wm), .G_q30(rsp_G), .Wp_q30(rsp_Wp),
        .alpha_q30(32'd1073741824),   // alpha=1.0 旁路（首版）
        .b_q24(b_q24_cfg),
        .in_epoch(cfg_epoch), .in_tag(rsp_tag),
        .cfg_epoch(cfg_epoch),
        .out_valid(out_valid), .code_o(out_code),
        .out_sat(out_sat), .out_err(out_err), .out_tag(out_tag));

    assign adc2_release_ok_o  = adc2_release_ok;

endmodule
