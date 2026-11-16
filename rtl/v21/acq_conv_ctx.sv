// =============================================================================
// 文件名     : acq_conv_ctx.sv
// 模块名     : acq_conv_ctx
// 版本       : v1.1
// 功能       : ACQ / CONV 两笔独立上下文的冻结、物理片号路由与旧组释放资格。
//              对应方案 v1.4 §4.2（不可变描述符）、§4.3（两笔驱动）与 §11.2 I1。
// 设计说明   :
//   * 为什么需要两套独立上下文：论文 p.182 "8 sDACs converting the signal, and
//     8 sDACs acquiring the signal, dynamically selected from a pool of 18"。
//     采集与转换必须同时持有各自片号、相位、qid、pre，否则 B 建立自己的预置时
//     会覆写 A 仍在转换所需的上下文 —— 那是方案 I1 的核心验收点。
//
//   * 四态生命周期（v1.1 修正，v1.0 漏了 SAMPLED）：
//       IDLE    --prepare_fire-->  ACQ      冻结片号/相位/word/pre
//       ACQ     --sample_event & input_captured-->  SAMPLED   输入已采
//       SAMPLED --下一次 prepare_fire-->  CONV      原组进转换，同时建新 ACQ
//       CONV    --captured & hold & quiet-->  IDLE  释放回池
//     v1.0 把"建立 ACQ"和"搬到 CONV"合成了一步，导致 A 在 ACQ 时无法接受 B 的
//     prepare，两笔流水根本转不起来。缺的正是"已采样但尚未进 CONV"这个中间态。
//
//   * slot 与物理片：sched18 给出升序 slot 与 8 个物理片号（5 bit/个）。
//     dem_mask_v3 / dither_pre 只认 slot（0..7），所以必须由本层按**冻结的**
//     片号把 slot 级结果路由成物理 18 片接口。用本拍 sched_ids 会把 B 的
//     pre 写到 A 的片号上。
//
//   * 释放资格：CONV 只有在
//       ① 该笔的余差已捕获（residue_captured）
//       ② 宏回报 hold 完成（hold_ack）
//       ③ 沿后安静窗计满（QUIET_CYCLES）
//     三者同时满足后才回 IDLE。期间这些片不得被重新分配。
//
//   * 宏桩：本阶段 capture / hold / quiet 由输入端口模拟，
//     不接真实 ADC2 数值通路（I3 才接）。
// 变更记录   :
//   2026-11-15  v1.0 初版（四态缺失，两笔流水无法推进）。
//   2026-11-15  v1.1 补 SAMPLED 中间态；prepare/commit 分离；片号路由用冻结值。
// =============================================================================
module acq_conv_ctx #(
    // 沿后安静窗长度（拍）。方案未给具体拍数，取 2 拍作为 [E] 工程取值。
    parameter int QUIET_CYCLES = 2
) (
    input  logic        clk,
    input  logic        rst_n,

    // ---------------------------------------------------------------- prepare
    // prepare_valid & prepare_ready 同高的上升沿接受一笔新 ACQ。
    // 被拒请求不推进 PRNG（由上层保证：只有 commit_fire 才推进 sched18）。
    input  logic        prepare_valid,
    output logic        prepare_ready,
    input  logic [31:0] prepare_tag,
    input  logic [5:0]  prepare_word,      // D1..D6
    input  logic [2:0]  prepare_h,
    input  logic [2:0]  prepare_v_dem,
    input  logic [2:0]  prepare_v_pre,
    input  logic        prepare_half,
    input  logic        prepare_coin,
    input  logic [2:0]  prepare_flash,     // 本笔 Flash 码，随描述符冻结
    input  logic        prepare_qid,       // 本笔 qid，随描述符冻结
    input  logic        stop_i,            // 停收新样本：已采过的 ACQ 仍要能转 CONV

    // ---------------------------------------------------------------- 采样事件
    input  logic        sample_event,
    input  logic        input_captured,
    input  logic        residue_captured,
    input  logic        hold_ack,

    // ---------------------------------------------------------------- 片组
    input  logic [17:0] sched_acq,
    input  logic [17:0] sched_conv,
    input  logic [17:0] sched_idle,
    input  logic [39:0] sched_ids,       // 本拍（沿前）采集组片号
    input  logic [39:0] sched_ids_next,  // adv 推进后的采集组片号

    output logic [17:0] acq_sel_o,
    output logic [17:0] conv_sel_o,
    output logic [17:0] idle_sel_o,

    // ---------------------------------------------------------------- 叶子驱动
    output logic [2:0]  acq_h_o,
    output logic [2:0]  acq_v_dem_o,
    output logic [2:0]  acq_v_pre_o,
    output logic        acq_half_o,
    output logic        acq_coin_o,
    output logic [5:0]  acq_word_o,
    output logic [2:0]  acq_U_o,
    input  logic [31:0] acq_pre_slot_i,    // slot 级 pre，由 dither_pre 驱动

    output logic [2:0]  conv_h_o,
    output logic [2:0]  conv_v_dem_o,
    output logic        conv_half_o,
    output logic        conv_coin_o,
    output logic [5:0]  conv_word_o,
    output logic [31:0] acq_pre_slot_o,    // ACQ 冻结的 slot 级 pre（观测用）
    output logic [31:0] acq_tag_o,         // ACQ 冻结的 tag（commit 沿给事件层用）
    output logic [2:0]  acq_flash_o,       // ACQ 冻结的 Flash 码
    output logic [2:0]  conv_flash_o,      // CONV 冻结的 Flash 码
    output logic        acq_qid_o,         // ACQ 冻结的 qid
    output logic        conv_qid_o,        // CONV 冻结的 qid

    output logic [39:0] acq_ids_o,
    output logic [39:0] conv_ids_o,
    output logic        acq_valid_o,
    output logic        acq_sampled_o,     // ACQ 已采、等进 CONV
    output logic        conv_valid_o,

    // ---------------------------------------------------------------- 物理写门
    output logic [71:0]  rdac_pre_state_o,
    output logic [17:0]  pre_wr_o,
    output logic [359:0] rdac_post_state_o,
    output logic [17:0]  post_wr_o,

    // ---------------------------------------------------------------- 观测
    output logic        prepare_fire_o,
    output logic        commit_fire_o,     // 原 ACQ 进 CONV（仅用于事件层）
    // **sched18 的推进信号**。
    //
    // 原来是 adv = commit_fire（= do_promote），只在 acq_can_promote 时成立。
    // 但**首笔** A 的 prepare 时 ACQ 槽是空的（acq_slot_free=1），
    // acq_can_promote=0，于是 adv=0 —— sched18 根本不推进。
    // 结果 A/B 冻结成同一组片号（审计问题 1）。
    //
    // 正确语义：**每一笔被接受的 prepare 都占用一个新的采样周期**，
    // 无论它是"填空槽"还是"顶替已采样的旧组"，都必须推进 sched18。
    output logic        sched_adv_o,
    output logic        release_fire_o,
    output logic [31:0] conv_tag_o,
    output logic [2:0]  conv_U_o,
    output logic [31:0] conv_pre_slot_o
);

    // U 由同一 word 派生（U = 4*D6 + D4），不接受矛盾的外部 U。
    wire [2:0] prep_U = {1'b0, prepare_word[5]} * 3'd4 + {2'b0, prepare_word[3]};

    // ---------------------------------------------------------------- ACQ
    logic [39:0] acq_ids_q;
    logic [31:0] acq_tag_q, acq_pre_q;
    logic [5:0]  acq_word_q;
    logic [2:0]  acq_h_q, acq_v_dem_q, acq_v_pre_q, acq_U_q;
    logic        acq_half_q, acq_coin_q;
    logic        acq_valid_q;
    logic        acq_sampled_q;
    logic [2:0]  acq_flash_q;
    logic        acq_qid_q;

    // ---------------------------------------------------------------- CONV
    logic [39:0] conv_ids_q;
    logic [31:0] conv_tag_q, conv_pre_q;
    logic [5:0]  conv_word_q;
    logic [2:0]  conv_h_q, conv_v_dem_q, conv_U_q;
    logic        conv_half_q, conv_coin_q;
    logic        conv_valid_q;
    logic [2:0]  conv_flash_q;
    logic        conv_qid_q;
    logic        conv_captured_q, conv_held_q;
    logic [3:0]  quiet_cnt_q;

    // ---------------------------------------------------------------- 仲裁
    // 接受的规则：ACQ 槽空闲，或 ACQ 已采样（可以把它推进 CONV 腾出槽位）。
    // 这样只要 ACQ 已采过，下一笔 prepare 就能进来，不用等 CONV 释放。
    wire acq_slot_free  = ~acq_valid_q;
    // 已采样的 ACQ 可以被提升：要么下一笔 prepare 把它顶进 CONV，
    // 要么 stop 排空时单独提升（方案 §11.2 I1："停收后已采过的 B 必须能
    // 独立转入 CONV 并完成 E2 捕获，无须再发一笔 prepare"）。
    // **promote 还要求 CONV 槽可用**。
    //
    // 反例（审查已 VCS 复现）：A 在 CONV、hold=0、release=0（未满足释放），
    // B 已采。此时 C 的 prepare 仍被接受，把 B 提升进 CONV ——
    // CONV 从 A1 被覆盖成 B2，A 的上下文丢失。
    //
    // 根因：`prepare_ready` 只看 ACQ 槽（`acq_can_promote`），
    // 没检查 CONV 是否还占着；而 promote 的目标正是 CONV。
    //
    // 正确语义：可以把已采样的 ACQ 顶进 CONV，但前提是 CONV 已经释放
    // （或本拍正在释放，即 `release_fire_o`，那样槽位在下一拍才空）。
    wire conv_release_ok = conv_valid_q & conv_captured_q & conv_held_q
                         & (quiet_cnt_q >= QUIET_CYCLES[3:0]);
    assign release_fire_o = conv_release_ok;

    wire conv_slot_free  = ~conv_valid_q | release_fire_o;
    wire acq_can_promote = acq_valid_q & acq_sampled_q & conv_slot_free;
    assign prepare_ready = rst_n & (acq_slot_free | acq_can_promote);

    wire do_prepare = prepare_valid & prepare_ready;
    // 排空提升：stop 时若 ACQ 已采样且 CONV 槽可用，单独提升。
    // 要求 CONV 空闲，否则会覆盖尚未释放的那一笔。
    wire do_drain   = stop_i & acq_can_promote & ~conv_valid_q;
    wire do_promote = (do_prepare & acq_can_promote) | do_drain;

    // prepare_fire：真正建立新 ACQ（排空不建立新 ACQ）
    assign prepare_fire_o = do_prepare;

    // commit_fire：把已采样的 ACQ 提升为 CONV（用于推进 sched18）
    assign commit_fire_o = do_promote;
    assign sched_adv_o   = do_prepare;    // 任何被接受的 prepare 都推进

    // 释放资格


    // ---------------------------------------------------------------- 时序
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acq_ids_q <= 40'd0; acq_tag_q <= 32'd0; acq_pre_q <= 32'd0;
            acq_word_q <= 6'd0;
            acq_h_q <= 3'd0; acq_v_dem_q <= 3'd0; acq_v_pre_q <= 3'd0;
            acq_U_q <= 3'd0; acq_half_q <= 1'b0; acq_coin_q <= 1'b0;
            acq_valid_q <= 1'b0; acq_sampled_q <= 1'b0; acq_flash_q <= 3'd0;
            acq_qid_q <= 1'b0;

            conv_ids_q <= 40'd0; conv_tag_q <= 32'd0; conv_pre_q <= 32'd0;
            conv_word_q <= 6'd0;
            conv_h_q <= 3'd0; conv_v_dem_q <= 3'd0; conv_U_q <= 3'd0;
            conv_half_q <= 1'b0; conv_coin_q <= 1'b0;
            conv_valid_q <= 1'b0; conv_captured_q <= 1'b0; conv_held_q <= 1'b0;
            conv_flash_q <= 3'd0; conv_qid_q <= 1'b0;
            quiet_cnt_q <= 4'd0;
        end else begin
            // ---- 1. 采样沿：ACQ 标记已采；CONV 记录捕获/hold ----
            if (sample_event) begin
                if (acq_valid_q && input_captured) acq_sampled_q <= 1'b1;
                if (conv_valid_q && residue_captured) conv_captured_q <= 1'b1;
                if (conv_valid_q && hold_ack)         conv_held_q     <= 1'b1;
            end else if (conv_valid_q && hold_ack) begin
                // hold 可能晚于捕获沿单独到达
                conv_held_q <= 1'b1;
            end

            // ---- 2. 安静窗：捕获之后开始计 ----
            if (!conv_valid_q || !conv_captured_q) quiet_cnt_q <= 4'd0;
            else if (quiet_cnt_q < QUIET_CYCLES[3:0])
                quiet_cnt_q <= quiet_cnt_q + 4'd1;

            // ---- 3a. 排空提升（stop）：只提升，不建新 ACQ ----
            if (do_drain) begin
                conv_ids_q      <= acq_ids_q;
                conv_tag_q      <= acq_tag_q;
                conv_pre_q      <= acq_pre_q;
                conv_word_q     <= acq_word_q;
                conv_h_q        <= acq_h_q;
                conv_v_dem_q    <= acq_v_dem_q;
                conv_U_q        <= acq_U_q;
                conv_half_q     <= acq_half_q;
                conv_coin_q     <= acq_coin_q;
                conv_valid_q    <= 1'b1;
                conv_flash_q    <= acq_flash_q;
                conv_qid_q      <= acq_qid_q;
                conv_captured_q <= 1'b0;
                conv_held_q     <= 1'b0;
                quiet_cnt_q     <= 4'd0;
                // ACQ 空出来
                acq_valid_q     <= 1'b0;
                acq_sampled_q   <= 1'b0;
            end

            // ---- 3b. prepare：先提升旧 ACQ 到 CONV，再装新 ACQ ----
            if (do_prepare) begin
                if (acq_can_promote) begin
                    // 旧的（已采过的）ACQ 进 CONV
                    conv_ids_q      <= acq_ids_q;
                    conv_tag_q      <= acq_tag_q;
                    conv_pre_q      <= acq_pre_q;
                    conv_word_q     <= acq_word_q;
                    conv_h_q        <= acq_h_q;
                    conv_v_dem_q    <= acq_v_dem_q;
                    conv_U_q        <= acq_U_q;
                    conv_half_q     <= acq_half_q;
                    conv_coin_q     <= acq_coin_q;
                    conv_valid_q    <= 1'b1;
                    conv_flash_q    <= acq_flash_q;
                    conv_qid_q      <= acq_qid_q;
                    conv_captured_q <= 1'b0;
                    conv_held_q     <= 1'b0;
                    quiet_cnt_q     <= 4'd0;
                end
                // 装新 ACQ（用本拍 sched_ids 作为它的冻结片号）
                // 冻结**沿前**的采集组片号（= sched18 推进前的 acq_q）。
                //
                // 为什么不是 sched_ids_next：sched18 的语义是
                //   adv 时 conv_q <= acq_q;  acq_q <= acq_next
                // 也就是"本拍被采样的组"就是沿前的 acq_q。
                // 用 next 会让本笔拿到**下一笔**的片组（实测 A 变成
                // 0x8C1ED62D48 而不是 0x000FF）。
                //
                // 审计问题 1 的真因不在取沿前/沿后，而在
                // **commit_fire 让 sched18 提前推进**：A 的 prepare 已经把
                // acq_q 推到 B 组，于是 B 的 prepare 读到的是 A 推进后的值。
                acq_ids_q     <= sched_ids;
                acq_tag_q     <= prepare_tag;
                // 冻结本笔的 slot 级 pre（由 dither_pre 按本拍 word/v_pre 给出）。
                // 踩过的坑：v1.1 漏了这一句，acq_pre_q 永远是复位值 0，
                // 于是 pre 路由全 0 —— 编译能过、综合能过，但功能是死的。
                acq_pre_q     <= acq_pre_slot_i;
                acq_word_q    <= prepare_word;
                acq_h_q       <= prepare_h;
                acq_v_dem_q   <= prepare_v_dem;
                acq_v_pre_q   <= prepare_v_pre;
                acq_half_q    <= prepare_half;
                acq_coin_q    <= prepare_coin;
                acq_U_q       <= prep_U;
                acq_valid_q   <= 1'b1;
                acq_sampled_q <= 1'b0;
                acq_flash_q   <= prepare_flash;
                acq_qid_q     <= prepare_qid;
            end

            // ---- 4. 释放 ----
            //
            // 同沿优先级：**promote 优先于 release**。
            //
            // 为什么必须显式处理：A 在 CONV 满足释放条件、B 在 ACQ 已采样，
            // 此时来一笔 prepare C —— B 要被提升进 CONV，A 要释放，
            // 两者落在同一个 always_ff 里。原来的写法把 release 放在后面，
            // 它的 `conv_valid_q <= 1'b0` 会覆盖 promote 刚写的 `1'b1`，
            // 结果是"B 被提升后立刻消失"，而且 conv_* 数据还停在 B 上、
            // valid=0 —— 极难查的状态错乱。
            //
            // 正确语义：CONV 这个槽在同一沿被"释放+重用"，
            // 它应当是 B 的上下文且 valid=1（A 的事件流到此结束）。
            if (release_fire_o && !do_promote) begin
                conv_valid_q    <= 1'b0;
                conv_captured_q <= 1'b0;
                conv_held_q     <= 1'b0;
                quiet_cnt_q     <= 4'd0;
            end
        end
    end

    // ---------------------------------------------------------------- 观测
    assign acq_ids_o     = acq_ids_q;
    assign conv_ids_o    = conv_ids_q;
    assign acq_valid_o   = acq_valid_q;
    assign acq_sampled_o = acq_sampled_q;
    assign conv_valid_o  = conv_valid_q;

    assign acq_h_o       = acq_h_q;
    assign acq_v_dem_o   = acq_v_dem_q;
    assign acq_v_pre_o   = acq_v_pre_q;
    assign acq_half_o    = acq_half_q;
    assign acq_coin_o    = acq_coin_q;
    assign acq_word_o    = acq_word_q;
    assign acq_U_o       = acq_U_q;
    assign acq_pre_slot_o = acq_pre_q;
    assign acq_tag_o      = acq_tag_q;
    assign acq_flash_o    = acq_flash_q;
    assign conv_flash_o   = conv_flash_q;
    assign acq_qid_o      = acq_qid_q;
    assign conv_qid_o     = conv_qid_q;

    assign conv_h_o      = conv_h_q;
    assign conv_v_dem_o  = conv_v_dem_q;
    assign conv_half_o   = conv_half_q;
    assign conv_coin_o   = conv_coin_q;
    assign conv_word_o   = conv_word_q;
    assign conv_U_o      = conv_U_q;
    assign conv_tag_o    = conv_tag_q;
    assign conv_pre_slot_o = conv_pre_q;

    // ---------------------------------------------------------------- 片组
    // ---------------------------------------------------------------- 宏选通
    //
    // **对外片组选通必须来自本笔冻结的片号，不是调度器的当前位图。**
    //
    // 反例（独立 VCS 已复现）：sched18 在 `accepted prepare` 那一拍就推进，
    // 所以 `sched_acq` 出的是**推进后**的位图；而 pre/post 实际写的是
    // 冻结在描述符里的片号。两者错开一组：
    //     ROLE_A:  acq_sel=3bd00  pre_wr=000ff   -> 交集为 0
    //     ROLE_AB: acq_sel=040fe  pre_wr=3bd00   -> 两个角色都领先一组
    //
    // 这些端口在合同里是"数字到模拟的实际片组选通"，不是调度预览，
    // 所以必须与实际写入的片组一致。
    //
    // 做法：把 8 个冻结片号展开成 18 位掩码。
    //   · ACQ 侧：prepare 被接受那一拍用**本拍新分配**的片号（sched_ids），
    //     其余时刻用 acq_ids_q
    //   · CONV 侧：用 conv_ids_q（该笔进 CONV 时冻结）
    // 这与 pre 路由（pre_route_ids）使用同一套片号来源，保证一致。
    //
    // ACQ 掩码的三种来源：
    //   ① prepare 那一拍   -> 本拍新分配的 sched_ids（要被冻结的值）
    //   ② 已有 ACQ 描述符  -> acq_ids_q（冻结片号，与 pre 写入一致）
    //   ③ 首笔尚未装载     -> **sched_acq（调度器初态）**
    //
    // ③ 是必需的：复位后 sched18 初态就是 8 片 ACQ（R03"首采样前无 CONV"），
    // 而描述符还没装载（acq_ids_q 全 0）。若用 acq_ids_q 展开，
    // "8 个片号都是 0" 会被解释成"只有片 0"，掩码变成 0x00001
    // （实测回归报 acq=1，期望 8）。
    wire        acq_desc_valid = acq_valid_q | do_prepare;
    wire [39:0] acq_route_ids  = do_prepare ? sched_ids : acq_ids_q;


    // **必须用 valid 屏蔽**：复位后 IDs 全 0，若直接展开，
    // "8 个片号都是 0" 会被解释成"片 0 被选中"，掩码变成 0x00001。
    // 反例实测：ROLE_A 时 conv=00001 而期望 0（A 尚未跨采样沿，
    // CONV 组应完全为空）。
    logic [17:0] acq_sel_c, conv_sel_c;
    always_comb begin
        acq_sel_c  = 18'd0;
        conv_sel_c = 18'd0;
        if (acq_desc_valid) begin
            for (int s = 0; s < 8; s++)
                acq_sel_c[acq_route_ids[s*5 +: 5]] = 1'b1;
        end else begin
            acq_sel_c = sched_acq;      // 首笔未装载：调度器初态
        end
        if (conv_valid_q) begin
            for (int s = 0; s < 8; s++)
                conv_sel_c[conv_ids_q[s*5 +: 5]] = 1'b1;
        end
    end
    assign acq_sel_o  = acq_sel_c;
    assign conv_sel_o = conv_sel_c;

    // 仍在 CONV（未释放）的片不得被重新分配。
    // 用 generate + 归约，避免 function 变量下标（综合器会 report unsupported）。
    logic [17:0] conv_hold_mask;
    genvar gh;
    generate
        for (gh = 0; gh < 18; gh++) begin : g_hold
            logic hit;
            always_comb begin
                hit = 1'b0;
                for (int i = 0; i < 8; i++)
                    if (conv_ids_q[i*5 +: 5] == 5'(gh)) hit = 1'b1;
            end
            assign conv_hold_mask[gh] = hit;
        end
    endgenerate

    // ---------------------------------------------------------------- IDLE 分区
    //
    // **IDLE 必须是 ACQ/CONV 实际角色分区的补集**，不能直接取 sched_idle。
    //
    // 反例（独立 VCS 实测，A 转换 / B 采集的稳定态）：
    //     ACQ=3bd00(8) CONV=000ff(8) IDLE=00200(1)   -> 只有 17 片被覆盖
    //   应为 IDLE=04200（10 片），18 片完整分区。
    //
    // 根因：`sched_idle` 是 sched18 在 accepted prepare 那一拍**推进后**的
    // 位图，而 acq_sel_o / conv_sel_o 已改为从**冻结片号**展开
    // （见上方"宏选通"一节）。两者错开一组，补集就不完整。
    //
    // 修法：IDLE = 全 18 片的补集（扣掉实际 ACQ 与 CONV 的并集）。
    // 这样"三集合互斥且并集 = 全部 18 片"是**构造性成立**的，
    // 不依赖调度器位图与冻结片号是否同步。
    //
    // 保持不变的部分：`conv_hold_mask` 的扣减逻辑（CONV 未释放时
    // 该组既不算 IDLE 也不算可用），以及调度算法本身不动。
    logic [17:0] idle_sel_c;
    always_comb begin
        idle_sel_c = ~(acq_sel_c | conv_sel_c) & 18'h3FFFF;   // 全 18 片掩码
    end
    assign idle_sel_o = idle_sel_c;

    // ---------------------------------------------------------------- 物理路由
    //
    // **pre 必须打到"本拍正在建立的那一笔"的片号**，不是 acq_ids_q。
    //
    // 踩过的坑（审计问题 1）：原来 pre 用 acq_ids_q + acq_valid_q。
    // 但在 prepare 那一拍，acq_ids_q 还是**上一笔（A）**的片号
    // （非阻塞赋值要到沿后才更新），于是：
    //   acq_sel = 0x3BD00（B 的新片，sched18 已更新）
    //   pre_wr  = 0x000FF（A 的旧片）   <- 打错地方
    //   post_wr = 0x000FF（A 的片，正确）
    //   两者交集 = 0x000FF，即同一组片同时被 pre 和 post 写。
    //
    // 正确语义：prepare 被接受的那一拍，新 ACQ 描述符的内容就是
    // `sched_ids` / `prepare_*`（这些正是要被冻结进描述符的值），
    // 所以 pre 应当按它们路由；其余时刻按已冻结的 acq_ids_q 路由。
    wire [39:0] pre_route_ids = do_prepare ? sched_ids : acq_ids_q;
    wire        pre_route_en  = do_prepare | acq_valid_q;

    // **pre 的数据必须来自冻结描述符**，不能直接取组合输入 `acq_pre_slot_i`。
    //
    // 反例（审查已 VCS 复现）：A 已受理后，`prepare_valid=0`（不再受理），
    // 但上游把 word 从 0 改成 63 —— `acq_pre_slot_i` 是 dither_pre 对
    // **当前 word** 的组合输出，于是物理 pre 从 0 变成 0xEF...
    // 而 A 的冻结快照 `acq_pre_q` 仍是 0，两者不一致。
    // 这就是"未被受理的输入改变了本笔物理预置"。
    //
    // 正确语义：
    //   · prepare 被受理那一拍 -> 用当拍输入（这正是要被冻结的值）
    //   · 其余时刻            -> 用已冻结的 acq_pre_q
    wire [31:0] pre_route_data = do_prepare ? acq_pre_slot_i : acq_pre_q;

    logic [71:0] pre_state_c;
    logic [17:0] pre_wr_c;
    // 纯组合展开：每个物理片位的值 = 若该片号等于某个 slot 的冻结片号，
    // 则取该 slot 的 nib。用 generate 生产 18×4 个独立 always_comb，
    // 每个都显式赋初值，保证全路径赋值（不会被推断成锁存器）。
    genvar gp, gb;
    generate
        for (gp = 0; gp < 18; gp++) begin : g_pre_p
            for (gb = 0; gb < 4; gb++) begin : g_pre_b
                logic v;
                always_comb begin
                    v = 1'b0;
                    if (pre_route_en) begin
                        for (int s = 0; s < 8; s++)
                            if (pre_route_ids[s*5 +: 5] == 5'(gp))
                                v = pre_route_data[s*4 + gb];
                    end
                end
                assign pre_state_c[gp*4 + gb] = v;
            end
        end
    endgenerate

    generate
        for (gp = 0; gp < 18; gp++) begin : g_pre_wr
            logic w;
            always_comb begin
                w = 1'b0;
                if (pre_route_en)
                    for (int s = 0; s < 8; s++)
                        if (pre_route_ids[s*5 +: 5] == 5'(gp)) w = 1'b1;
            end
            assign pre_wr_c[gp] = w;
        end
    endgenerate

    assign rdac_pre_state_o  = pre_state_c;
    assign pre_wr_o          = pre_wr_c;
    // post 由 I2 在 apply_fire 时给出；I1 阶段恒 0 且写门关闭
    assign rdac_post_state_o = 360'd0;
    assign post_wr_o         = 18'd0;

endmodule
