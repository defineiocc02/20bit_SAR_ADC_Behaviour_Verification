`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : adc_digital_ip_v21_40msps.sv
// 模块名     : adc_digital_ip_v21_40msps
// 版本       : v1.0
// 功能       : 双时钟域 40 MSPS（25 ns）真实时序架构转换核顶层。
//              主帧域 clk_fs（40 MHz / 25 ns）运行片组调度、采样预置、权重求和、
//              ADC2 捕获归属与 7 级定点重构；快速事件域 ev_clk_i（自定时 7 ns 突发）
//              仅运行 sar_evt_v21、dem_mask_v3 与物理后置开关路由。
// 设计说明   :
//   * [架构动机] 单时钟顶层 adc_digital_ip_v21 每笔需 13 拍才能闭环，迫使全核
//     在仿真中跑 640 MHz / 1.6 GHz 理想时钟，无法在 40 nm 工艺下闭环签核。
//     本顶层将占面积 >95% 的重逻辑（sched18、wsum_v21、recon_v21）全部收口到
//     真实 40 MHz（25 ns）主帧时钟，仅将 ~1k 门的已决事件跟随环路置于自定时
//     ev_clk_i 突发域（每帧 [0.5..8.0 ns] 翻转 6–9 次，[8..25 ns] RA 建立窗内
//     完全静默，彻底消除数字开关对共享 ×32 RA 与无缓冲 Fine REF_IN 的串扰）。
//   * [无负沿流水] CONV 上下文在 posedge clk_fs（t=0）冻结整帧 25 ns；ev_clk_i
//     在 t<=8 ns 锁存 final_post_q 并翻转 1-bit 完成令牌 done_toggle_ev_q。
//     wsum_v21 在下一拍 posedge clk_fs（t=25 ns，享有 17 ns 建立裕量）随同
//     adc2_capture 同沿锁存本笔 G/Wp/Wm，并保持至 t=50 ns（ADC2 返回 raw 沿）
//     供 recon_v21 S1a 直接采纳；反压时二者同步保持，零额外寄存器、零负沿时钟。
//   * [跨窗防护] 1-bit 帧令牌（conv_toggle_fs_q == done_toggle_ev_q）保证每帧
//     仅本帧事件完成后才允许 adc2_capture_arm_o 置位；漏事件或 RA 安静窗内
//     异常脉冲均由 missed_evt_err_o / quiet_evt_err_o 显式报错。
// 变更记录   :
//   2026-11-24  v1.0 初版。实现 40 MHz 主帧 + 自定时事件域双域 40 MSPS 转换核。
// =============================================================================
module adc_digital_ip_v21_40msps #(
    // [综合收敛开关] 1=标称单位增益(2^30)直通，使 DC 常量传播剪除 recon_v21 内 64-bit DW_div
    // （系统级增益校正由外围 sys_error_corr.sv 承担，见 RECON_ALPHA_DIVIDER.md 选项 A）；
    // 0=启用完整动态 alpha_q30_cfg_i 除法端口。
    parameter bit ALPHA_UNIT_BYPASS = 1'b1
) (
    // --- 主帧时钟域（40 MHz，周期 25 ns）与公共异步低有效复位 ---
    input  logic         clk_fs,               // 40 MHz 公共帧时钟（驱动 >95% 核逻辑）
    input  logic         ev_clk_i,             // SADC 自定时比较事件突发时钟（仅 [0.5..8 ns] 活动）
    input  logic         rst_n,                // 异步低有效复位，两域由顶层同步释放

    // --- 帧级采样事务入口（clk_fs 域，每 25 ns 受理一笔） ---
    input  logic         prepare_valid_i,      // 请求受理一笔新 ACQ 样本
    output logic         prepare_ready_o,      // 配置就绪、未停收且非错误时为高
    input  logic [31:0]  prepare_tag_i,        // 样本唯一追踪标签
    input  logic [5:0]   prepare_word_i,       // dither_force_i=1 时使用的外部 6-bit 扰动字
    input  logic [2:0]   prepare_h_i,          // 本笔 DEM 横向相位（3-bit）
    input  logic [2:0]   prepare_v_i,          // 本笔 DEM / pre 纵向相位（3-bit）
    input  logic         prepare_half_i,       // 保留列半单元选择位
    input  logic         prepare_coin_i,       // 50% 半格跨界桥接随机位
    input  logic [2:0]   prepare_U_i,          // 强制字模式下的上段 U 冗余校验
    input  logic [2:0]   prepare_flash_i,      // 提前提供的 Flash 粗码（可被 ev_flash 覆盖）
    input  logic         prepare_qid_i,        // 交替量化器编号（0/1），失序拒收
    input  logic         stop_i,               // 停收新样本，允许在途 ACQ/CONV 正常排空

    // --- 公共采样孔径与模拟握手（clk_fs 域） ---
    input  logic         sample_event_i,       // 公共采样沿（每 25 ns 有效一拍）
    input  logic         input_captured_i,     // 本沿 ACQ 已锁存模拟输入
    input  logic         residue_captured_i,   // 本沿 ADC2 已捕获当前 CONV 余差
    input  logic         hold_ack_i,           // 模拟宏回报 hold 完成

    // --- 自定时快速跟随事件入口（ev_clk_i 域，仅在粗转换窗活动） ---
    input  logic         ev_frame_start_i,     // 帧首启动脉冲（装载冻结 CONV 上下文至 sar_evt_v21）
    input  logic         ev_bit_valid_i,       // 本沿提交一次已决比较事件
    input  logic         ev_bit_i,             // 比较判决位（1=接受）
    input  logic [3:0]   ev_bit_index_i,       // 物理位号（0..5 或 9=512 保护门限）
    input  logic [3:0]   ev_event_index_i,     // 本笔提交序号（1..7 或 1..5）
    input  logic         ev_qid_i,             // 产生事件的量化器编号
    input  logic         ev_flash_valid_i,     // 真实 Flash 判决随帧首或首事件到达
    input  logic [2:0]   ev_flash_code_i,      // 实测 3-bit Flash 粗码

    // --- 18 选 8 切片池配置与状态（clk_fs 域） ---
    input  logic         sched_seed_we_i,      // 同步重播种片组 PRNG
    input  logic [31:0]  sched_seed_i,         // 非零 32-bit 种子
    output logic [17:0]  acq_sel_o,            // 当前正在采集的 8 片热编码
    output logic [17:0]  conv_sel_o,           // 当前正在转换的 8 片热编码
    output logic [17:0]  idle_sel_o,           // 当前轮空备用的 2 片热编码

    // --- 物理模拟宏驱动边界 ---
    output logic [71:0]  rdac_pre_state_o,     // 18 片 × 4-bit 采样预置状态（clk_fs 域）
    output logic [17:0]  pre_wr_o,             // 采样预置写门（clk_fs 域）
    output logic [359:0] rdac_post_state_o,    // 18 片 × 20-bit 转换开关状态（ev_clk_i 域）
    output logic [17:0]  post_wr_o,            // 转换开关写门（ev_clk_i 域，RA 窗内恒为 0）
    output logic [13:0]  sadc_pre_state_o,     // 双 SADC 低段预置（qid0:[6:0], qid1:[13:7]）
    output logic [1:0]   sadc_pre_wr_o,        // 双 SADC 预置写门（clk_fs 域）
    input  logic         dither_force_i,       // 0=片内 xorshift32，1=外部强制字重放

    // --- 13-bit ADC2 接口与仿真节点快照（clk_fs 域） ---
    input  logic         adc2_valid_i,         // ADC2 返回有效
    output logic         adc2_ready_o,         // 可接收本笔细码
    input  logic [12:0]  adc2_raw_i,           // 13-bit 细码，窗口 [-0.1875, +0.5625) V
    input  logic         adc2_range_err_i,     // ADC2 越窗标志
    output logic         adc2_capture_arm_o,   // 沿前捕获授权（事件完成后置高）
    output logic [159:0] node_post_o,          // 本笔锁存的 slot 级最终 post 掩码
    output logic [31:0]  node_pre_o,           // 本笔冻结的 slot 级 pre 掩码
    output logic [39:0]  node_ids_o,           // 本笔 8 个物理片号（8×5-bit）
    output logic [31:0]  node_tag_o,           // 本笔转换标签
    output logic         node_valid_o,         // 本笔最终 post 已就绪，可供节点宏求值
    output logic         adc2_late_err_o,      // 迟到/无主 raw 错标志

    // --- 权重表配置口（360 × 40-bit，clk_fs 域） ---
    input  logic         w_cfg_we_i,           // 单字写使能
    input  logic         w_cfg_sel_i,          // 0=主阵列(0..15)，1=子阵列(0..3)
    input  logic [4:0]   w_cfg_sid_i,          // 物理片号 0..17
    input  logic [4:0]   w_cfg_idx_i,          // 单元编号
    input  logic [39:0]  w_cfg_data_i,         // Q30 权重字
    input  logic         w_cfg_commit_i,       // 360 字写满后原子提交生效
    input  logic         w_cfg_reload_i,       // 重新装载请求
    output logic         w_cfg_valid_o,        // 权重表生效标志
    output logic         w_cfg_busy_o,         // 权重表装载中标志
    output logic         w_cfg_range_err_o,    // 子权重越界拒绝标志
    input  logic [7:0]   cfg_epoch_i,          // 当前系数版本号
    input  logic signed [26:0] b_q24_cfg_i,    // ADC2 偏置（Q24，27 位有符号）
    input  logic [31:0]  alpha_q30_cfg_i,      // ADC2 增益校正（2^30 旁路）

    // --- 20-bit 定点输出与双域诊断（clk_fs 域 / ev_clk_i 域） ---
    output logic         out_valid_o,          // 每 25 ns 退休一笔有效 20-bit 码
    output logic [19:0]  out_code_o,           // 20-bit 重构码
    output logic         out_sat_o,            // 末级码饱和标志
    output logic         out_err_o,            // 合同/范围/epoch 错误标志
    output logic [31:0]  out_tag_o,            // 对应退休样本标签
    output logic         ev_apply_fire_o,      // 事件域当拍合法写门（观测用）
    output logic [9:0]   ev_final_k_o,         // 事件域最终 RDAC 命令码
    output logic         ev_done_o,            // 当前帧事件已完成（1-bit 令牌匹配）
    output logic         missed_evt_err_o,     // 捕获沿到来但事件未完成（粘滞错误）
    output logic         quiet_evt_err_o,      // RA 安静窗内收到非法事件脉冲（粘滞错误）
    output logic         err_o                 // 汇总协议/配置/双域错误标志
);

    // =========================================================================
    // 1. 独立片内 Split Dither 字源与预置译码（clk_fs 域）
    // =========================================================================
    // [合同B8] 独立 xorshift32 字源与单时钟核保持完全一致的初值和递推，
    // 保证同一输入下两种顶层产生逐位一致的扰动序列与 20-bit 出码。
    localparam logic [31:0] DITHER_SEED = 32'hC001_D00D;

    logic [31:0] dither_rng_q;
    logic        next_qid_q;
    logic        prepare_fire;
    logic [31:0] pre_slot_leaf;
    logic [6:0]  sadc_pre_leaf;
    logic [2:0]  selected_upper;
    logic [3:0]  unused_pre_f;

    function automatic logic [31:0] dither_advance(input logic [31:0] state);
        logic [31:0] x;
        x = state ^ (state << 13);
        x = x ^ (x >> 17);
        return x ^ (x << 5);
    endfunction

    wire [31:0] dither_next = dither_advance(dither_rng_q);
    wire [5:0]  prepare_dither_word = dither_force_i ? prepare_word_i : dither_next[5:0];

    dither_pre u_pd (
        .word_i    (prepare_dither_word),
        .v_phase_i (prepare_v_i),
        .pre_o     (pre_slot_leaf),
        .U_o       (selected_upper),
        .f_o       (unused_pre_f),
        .sadc_pre_o(sadc_pre_leaf)
    );

    wire invalid_upper = dither_force_i && (prepare_U_i != selected_upper);
    wire prepare_error = rst_n && prepare_valid_i && (invalid_upper || (prepare_qid_i != next_qid_q));

    // =========================================================================
    // 2. 18 选 8 片组调度器与单周期帧晋升上下文（clk_fs 域）
    // =========================================================================
    // [40 MSPS 关键] 每个 posedge clk_fs（25 ns）是一帧边界：
    //   1) 若当前 CONV 有效且本帧事件完成、ADC2 捕获槽可接收，同沿完成捕获登记与 wsum 锁存；
    //   2) 若 ACQ 已采完且 CONV 槽释放/空，同沿将 ACQ 晋升为新 CONV，并翻转帧令牌；
    //   3) 若有合法 prepare，同沿装载新 ACQ 并推进 sched18 与 dither 字源。
    // 从而彻底消除单时钟顶层的空拍与 QUIET_CYCLES 浪费（25 ns 帧后 17 ns 本身即安静窗）。
    logic [17:0] s18_acq, s18_conv, s18_idle;
    logic [39:0] s18_ids, unused_s18_ids_next;
    logic [31:0] unused_s18_rng;

    sched18 u_sched (
        .clk        (clk_fs),
        .reset_n    (rst_n),
        .seed_we    (sched_seed_we_i),
        .seed_i     (sched_seed_i),
        .adv        (prepare_fire),
        .acq_mask_o (s18_acq),
        .conv_mask_o(s18_conv),
        .idle_mask_o(s18_idle),
        .ids_o      (s18_ids),
        .ids_next_o (unused_s18_ids_next),
        .rng_o      (unused_s18_rng)
    );

    // ACQ 与 CONV 独立描述符寄存器（在整帧 25 ns 内保持准静态，安全供事件域读取）
    logic        acq_valid_q, acq_sampled_q;
    logic [39:0] acq_ids_q;
    logic [31:0] acq_tag_q, acq_pre_q;
    logic [2:0]  acq_h_q, acq_v_q, acq_U_q, acq_flash_q;
    logic        acq_half_q, acq_coin_q, acq_qid_q;

    logic        conv_valid_q;
    logic        conv_toggle_fs_q;          // 每晋升一笔新 CONV 翻转一次，作为 1-bit 帧令牌
    logic [39:0] conv_ids_q;
    logic [31:0] conv_tag_q, conv_pre_q;
    logic [2:0]  conv_h_q, conv_v_q, conv_U_q, conv_flash_q;
    logic        conv_half_q, conv_coin_q, conv_qid_q;

    // 事件域完成令牌与锁存结果（前置声明，供帧域仲裁与 wsum 使用）
    logic         done_toggle_ev_q;
    logic [159:0] final_post_ev_q;
    logic         quiet_evt_err_q;
    logic         missed_evt_err_q;
    logic         ev_err_w;

    // 帧内事件完成判定：1-bit 令牌一致代表本帧所有已决位已应用且 final_post_ev_q 稳定
    wire frame_evt_done = conv_valid_q && (done_toggle_ev_q == conv_toggle_fs_q);

    // ADC2 owner 接收资格（槽空或本拍正好消费旧 owner）
    logic adc2_ready_w;
    wire  capture_ready_fs = adc2_ready_w;
    wire  capture_fire = conv_valid_q && sample_event_i && residue_captured_i
                         && frame_evt_done && capture_ready_fs;
    wire  capture_miss = conv_valid_q && sample_event_i && residue_captured_i
                         && (!frame_evt_done || !capture_ready_fs);

    // 本拍 CONV 可释放：原本空闲，或本拍完成捕获且宏确认 hold（或同沿放行）
    wire conv_releasing = conv_valid_q && capture_fire && hold_ack_i;
    wire conv_slot_free = !conv_valid_q || conv_releasing;
    // ACQ 在本拍或此前已完成采样即可晋升到 CONV
    wire acq_ready_to_promote = acq_valid_q && (acq_sampled_q || (sample_event_i && input_captured_i));
    wire promote_fire = acq_ready_to_promote && conv_slot_free;

    // 受理门禁：权重表生效、未停收、请求无错，且 ACQ 槽空或本拍正晋升腾空
    wire acq_slot_free = !acq_valid_q || promote_fire;
    assign prepare_ready_o = rst_n && w_cfg_valid_o && !stop_i && !prepare_error && acq_slot_free;
    assign prepare_fire    = prepare_valid_i && prepare_ready_o;

    // -------------------------------------------------------------------------
    // 片选热编码生成（严格保证 ACQ 与 CONV 任意时刻互斥、与 IDLE 构成 18 片划分）
    // -------------------------------------------------------------------------
    // [防越界索引] 采用外层常值物理片号循环，避免 5-bit 变址写 18-bit 向量
    // 触发 Verilator -Werror-SELRANGE；未活跃 ACQ 期间仍与 conv_mask_live 互斥。
    function automatic logic [17:0] ids_to_mask(input logic [39:0] ids, input logic valid);
        logic [17:0] m;
        m = 18'd0;
        if (valid) begin
            for (int p = 0; p < 18; p++) begin
                for (int s = 0; s < 8; s++) begin
                    if (ids[s*5 +: 5] == 5'(p))
                        m[p] = 1'b1;
                end
            end
        end
        return m;
    endfunction

    wire [17:0] acq_mask_live  = ids_to_mask(acq_ids_q, acq_valid_q);
    wire [17:0] conv_mask_live = ids_to_mask(conv_ids_q, conv_valid_q);
    assign acq_sel_o  = acq_valid_q ? acq_mask_live : (s18_acq & ~conv_mask_live);
    assign conv_sel_o = conv_mask_live;
    assign idle_sel_o = ~(acq_sel_o | conv_sel_o);

    // -------------------------------------------------------------------------
    // 物理 pre 路由（slot 级 32-bit -> 18 片 × 4-bit，在 prepare_fire 沿同拍注册）
    // -------------------------------------------------------------------------
    // [正常/停收路径] 仅在 prepare_fire 有效沿向 s18_ids 选中的 8 片发出 pre_wr_o，
    // 其余 10 片保持原状；停收（stop_i=1）或错误拒收时 pre_wr_o 恒为全 0，
    // 确保不误写正在转换的 CONV 片组，也不消耗 dither_rng_q 与 sched18 状态。
    logic [71:0] pre_phys_c;
    logic [17:0] pre_wr_c;
    genvar gp_pre;
    generate
        for (gp_pre = 0; gp_pre < 18; gp_pre++) begin : g_pre_phys
            logic       hit;
            logic [3:0] nib;
            always_comb begin
                hit = 1'b0;
                nib = 4'd0;
                if (prepare_fire) begin
                    for (int s = 0; s < 8; s++) begin
                        if (s18_ids[s*5 +: 5] == 5'(gp_pre)) begin
                            hit = 1'b1;
                            nib = pre_slot_leaf[s*4 +: 4];
                        end
                    end
                end
            end
            assign pre_wr_c[gp_pre]            = hit;
            assign pre_phys_c[gp_pre*4 +: 4]   = nib;
        end
    endgenerate

    // -------------------------------------------------------------------------
    // 主帧域时序更新（纯 posedge clk_fs，单向异步低有效复位）
    // -------------------------------------------------------------------------
    // [单拍流水三动作同沿优先级]
    //   1) 捕获/释放：旧 CONV 在本沿满足 frame_evt_done & capture_ready_fs 即完成捕获，
    //      若 hold_ack_i=1 同时释放 CONV 槽；若事件未完却来捕获沿则置 missed_evt_err_q。
    //   2) 晋升：ACQ 在本沿或此前已采完且 CONV 槽空/正释放，则将 ACQ 晋升进 CONV，
    //      同步翻转 1-bit 帧令牌 conv_toggle_fs_q，冻结整帧 25 ns 供事件域读取。
    //   3) 受理：新 prepare 同沿装入 ACQ，推进 dither_rng_q、next_qid_q 与 SADC 预置。
    always_ff @(posedge clk_fs or negedge rst_n) begin
        if (!rst_n) begin
            dither_rng_q     <= DITHER_SEED;
            next_qid_q       <= 1'b0;
            acq_valid_q      <= 1'b0;
            acq_sampled_q    <= 1'b0;
            acq_ids_q        <= 40'd0;
            acq_tag_q        <= 32'd0;
            acq_pre_q        <= 32'd0;
            acq_h_q          <= 3'd0;
            acq_v_q          <= 3'd0;
            acq_U_q          <= 3'd0;
            acq_flash_q      <= 3'd0;
            acq_half_q       <= 1'b0;
            acq_coin_q       <= 1'b0;
            acq_qid_q        <= 1'b0;
            conv_valid_q     <= 1'b0;
            conv_toggle_fs_q <= 1'b0;
            conv_ids_q       <= 40'd0;
            conv_tag_q       <= 32'd0;
            conv_pre_q       <= 32'd0;
            conv_h_q         <= 3'd0;
            conv_v_q         <= 3'd0;
            conv_U_q         <= 3'd0;
            conv_flash_q     <= 3'd0;
            conv_half_q      <= 1'b0;
            conv_coin_q      <= 1'b0;
            conv_qid_q       <= 1'b0;
            rdac_pre_state_o <= 72'd0;
            pre_wr_o         <= 18'd0;
            sadc_pre_state_o <= 14'd0;
            sadc_pre_wr_o    <= 2'b00;
            missed_evt_err_q <= 1'b0;
        end else begin
            // 1) 记录漏事件/捕获失败粘滞标志
            if (capture_miss) missed_evt_err_q <= 1'b1;

            // 2) CONV 槽释放与 ACQ -> CONV 晋升
            if (promote_fire) begin
                conv_valid_q     <= 1'b1;
                conv_toggle_fs_q <= ~conv_toggle_fs_q;
                conv_ids_q       <= acq_ids_q;
                conv_tag_q       <= acq_tag_q;
                conv_pre_q       <= acq_pre_q;
                conv_h_q         <= acq_h_q;
                conv_v_q         <= acq_v_q;
                conv_U_q         <= acq_U_q;
                conv_flash_q     <= acq_flash_q;
                conv_half_q      <= acq_half_q;
                conv_coin_q      <= acq_coin_q;
                conv_qid_q       <= acq_qid_q;
            end else if (conv_releasing) begin
                conv_valid_q <= 1'b0;
            end

            // 3) ACQ 槽装载、采样标志与物理 pre 注册
            pre_wr_o      <= pre_wr_c;
            sadc_pre_wr_o <= 2'b00;
            for (int p = 0; p < 18; p++) begin
                if (pre_wr_c[p])
                    rdac_pre_state_o[p*4 +: 4] <= pre_phys_c[p*4 +: 4];
            end

            if (prepare_fire) begin
                dither_rng_q  <= dither_next;
                next_qid_q    <= ~next_qid_q;
                acq_valid_q   <= 1'b1;
                acq_sampled_q <= 1'b0;
                acq_ids_q     <= s18_ids;
                acq_tag_q     <= prepare_tag_i;
                acq_pre_q     <= pre_slot_leaf;
                acq_h_q       <= prepare_h_i;
                acq_v_q       <= prepare_v_i;
                acq_U_q       <= selected_upper;
                acq_flash_q   <= prepare_flash_i;
                acq_half_q    <= prepare_half_i;
                acq_coin_q    <= prepare_coin_i;
                acq_qid_q     <= prepare_qid_i;
                sadc_pre_wr_o <= prepare_qid_i ? 2'b10 : 2'b01;
                if (prepare_qid_i) sadc_pre_state_o[13:7] <= sadc_pre_leaf;
                else               sadc_pre_state_o[6:0]  <= sadc_pre_leaf;
            end else if (promote_fire) begin
                acq_valid_q   <= 1'b0;
                acq_sampled_q <= 1'b0;
            end else if (acq_valid_q && sample_event_i && input_captured_i) begin
                acq_sampled_q <= 1'b1;
            end
        end
    end

    // =========================================================================
    // 3. 自定时快速事件跟随域（ev_clk_i，仅 ~1k 门，RA 建立窗内 0 翻转）
    // =========================================================================
    // [时序边界] 本域仅在每帧 [0.5..8.0 ns] 接收 6–9 个自定时比较脉冲：
    //   - 首脉冲 ev_frame_start_i=1 装载准静态 CONV 描述符（已在 t=0 稳定 >0.5 ns）；
    //   - 后续脉冲逐位推进 sar_evt_v21 -> dem_mask_v3 -> post_phys 并注册写门；
    //   - 末次合法 apply 同时锁存 final_post_ev_q 并回写 1-bit 完成令牌；
    //   - 完成后的 [8..25 ns] 期间若误收脉冲，写门恒 0 且 quiet_evt_err_q 置位。
    logic        ev_valid_unused, ev_k_bad_w, ev_apply_fire_w, ev_last_evt_w;
    logic        ev_final_applied_w, ev_done_w, ev_qid_w;
    logic [3:0]  ev_idx_unused, ev_bit_idx_unused;
    logic [9:0]  ev_k_w, ev_final_k_w;
    logic [31:0] ev_tag_w;
    logic [159:0] post_slot_ev_w;

    sar_evt_v21 u_evt_fast (
        .clk             (ev_clk_i),
        .rst_n           (rst_n),
        .start_i         (ev_frame_start_i && conv_valid_q),
        .abort_i         (1'b0),
        .tag_i           (conv_tag_q),
        .qid_i           (conv_qid_q),
        .upper_i         (conv_U_q),
        .flash_valid_i   (ev_frame_start_i || ev_flash_valid_i),
        .flash_code_i    (ev_flash_valid_i ? ev_flash_code_i : conv_flash_q),
        .bit_valid_i     (ev_bit_valid_i),
        .bit_i           (ev_bit_i),
        .bit_index_i     (ev_bit_index_i),
        .event_index_i   (ev_event_index_i),
        .sar_qid_i       (ev_qid_i),
        .sar_final_i     (1'b0),
        .ev_valid_o      (ev_valid_unused),
        .ev_event_index_o(ev_idx_unused),
        .ev_bit_index_o  (ev_bit_idx_unused),
        .k_o             (ev_k_w),
        .k_bad_o         (ev_k_bad_w),
        .apply_fire_o    (ev_apply_fire_w),
        .final_applied_o (ev_final_applied_w),
        .final_k_o       (ev_final_k_w),
        .tag_o           (ev_tag_w),
        .qid_o           (ev_qid_w),
        .err_o           (ev_err_w),
        .done_o          (ev_done_w),
        .last_evt_o      (ev_last_evt_w)
    );

    dem_mask_v3 u_dem_fast (
        .k_i       (ev_k_w),
        .h_phase_i (conv_h_q),
        .v_phase_i (conv_v_q),
        .coin_i    (conv_coin_q),
        .acq_seq_i ({conv_half_q, 3'b000}),
        .mask_o    (post_slot_ev_w)
    );

    // -------------------------------------------------------------------------
    // 物理 18×20 post 路由（准静态片号预译码 + 扁平 8:1 MUX，压缩 ev_clk 关键路径）
    // -------------------------------------------------------------------------
    // [时序优化] conv_ids_q 在 clk_fs 上升沿冻结整帧 25 ns；先按纯准静态信号预译码每片
    // 命中的 slot 编号（3-bit），使 ev_clk 突发路径只剩 1 级 8:1 扁平选通，避开
    // 8 路 5-bit 串联优先级比较链，显著压缩 ev_clk 逻辑级数。
    logic [359:0] post_phys_ev_c;
    logic [17:0]  post_wr_ev_c;
    genvar gp_post;
    generate
        for (gp_post = 0; gp_post < 18; gp_post++) begin : g_post_fast
            logic        slot_hit_qstatic;
            logic [2:0]  slot_idx_qstatic;
            logic [19:0] slot_mux_c;
            always_comb begin
                slot_hit_qstatic = 1'b0;
                slot_idx_qstatic = 3'd0;
                for (int s = 0; s < 8; s++) begin
                    if (conv_ids_q[s*5 +: 5] == 5'(gp_post)) begin
                        slot_hit_qstatic = 1'b1;
                        slot_idx_qstatic = 3'(s);
                    end
                end
                case (slot_idx_qstatic)
                    3'd0: slot_mux_c = post_slot_ev_w[0   +: 20];
                    3'd1: slot_mux_c = post_slot_ev_w[20  +: 20];
                    3'd2: slot_mux_c = post_slot_ev_w[40  +: 20];
                    3'd3: slot_mux_c = post_slot_ev_w[60  +: 20];
                    3'd4: slot_mux_c = post_slot_ev_w[80  +: 20];
                    3'd5: slot_mux_c = post_slot_ev_w[100 +: 20];
                    3'd6: slot_mux_c = post_slot_ev_w[120 +: 20];
                    default: slot_mux_c = post_slot_ev_w[140 +: 20];
                endcase
            end
            assign post_wr_ev_c[gp_post]            = ev_apply_fire_w && conv_valid_q && slot_hit_qstatic;
            assign post_phys_ev_c[gp_post*20 +: 20] = slot_mux_c;
        end
    endgenerate

    // -------------------------------------------------------------------------
    // 事件域输出注册与 1-bit 完成令牌回写（完全独立于 clk_fs，无多驱动）
    // -------------------------------------------------------------------------
    // [正常/异常路径]
    //   * 每次合法 apply 仅置位本笔 8 片的 post_wr_o 并更新对应 20-bit 物理开关；
    //   * 末次 apply（ev_last_evt_w=1）同沿冻结 final_post_ev_q 并回写令牌
    //     done_toggle_ev_q <= conv_toggle_fs_q，随后尾拍关闭写门进入 17 ns 安静窗；
    //   * 安静窗内（令牌已匹配）若模拟比较器误发毛刺脉冲，写门恒 0 且置 quiet_evt_err_q。
    always_ff @(posedge ev_clk_i or negedge rst_n) begin
        if (!rst_n) begin
            rdac_post_state_o <= 360'd0;
            post_wr_o         <= 18'd0;
            final_post_ev_q   <= 160'd0;
            done_toggle_ev_q  <= 1'b0;
            quiet_evt_err_q   <= 1'b0;
        end else begin
            post_wr_o <= post_wr_ev_c;
            for (int p = 0; p < 18; p++) begin
                if (post_wr_ev_c[p])
                    rdac_post_state_o[p*20 +: 20] <= post_phys_ev_c[p*20 +: 20];
            end
            if (ev_apply_fire_w && ev_last_evt_w) begin
                final_post_ev_q  <= post_slot_ev_w;
                done_toggle_ev_q <= conv_toggle_fs_q;
            end else if ((done_toggle_ev_q == conv_toggle_fs_q) && conv_valid_q && ev_bit_valid_i) begin
                // [安静窗告警] 本帧已进入 RA 建立安静窗后若仍收到事件脉冲，置粘滞错
                quiet_evt_err_q <= 1'b1;
            end
        end
    end

    // =========================================================================
    // 4. 权重求和（wsum_v21）、ADC2 归属与 7 级定点重构（纯 40 MHz clk_fs 域）
    // =========================================================================
    // [同沿配对] capture_fire 时 wsum_v21 锁存本笔 G/Wp/Wm（输入已在 t<=8 ns 稳定，
    // 享有 17 ns 建立时间），adc2_capture 同沿登记 owner（tag/qid/epoch）。
    // 下一帧 ADC2 返回 raw 时，recon_v21 直接读取 wsum_v21 已寄存的同笔 G/Wp/Wm，
    // 若 ADC2 延迟返回则 capture_fire=0，wsum_v21 寄存器自动保持本笔权重不被覆盖。
    logic        wsum_valid_w;
    logic [63:0] wsum_G_q, wsum_Wp_q, wsum_Wm_q;

    wsum_v21 u_wsum_fs (
        .clk          (clk_fs),
        .rst_n        (rst_n),
        .calc_valid   (capture_fire),
        .ids_i        (conv_ids_q),
        .pre_mask_i   (conv_pre_q),
        .post_mask_i  (final_post_ev_q),
        .cfg_we       (w_cfg_we_i),
        .cfg_sel      (w_cfg_sel_i),
        .cfg_sid      (w_cfg_sid_i),
        .cfg_idx      (w_cfg_idx_i),
        .cfg_data     (w_cfg_data_i),
        .cfg_commit   (w_cfg_commit_i),
        .cfg_reload   (w_cfg_reload_i),
        .cfg_valid    (w_cfg_valid_o),
        .cfg_busy     (w_cfg_busy_o),
        .cfg_range_err(w_cfg_range_err_o),
        .sum_valid    (wsum_valid_w),
        .G_q30        (wsum_G_q),
        .Wp_q30       (wsum_Wp_q),
        .Wm_q30       (wsum_Wm_q)
    );

    logic        rsp_valid_w, rsp_qid_w, rsp_range_w;
    logic [31:0] rsp_tag_w;
    logic [12:0] rsp_raw_w;
    logic [7:0]  rsp_epoch_w;
    logic        unused_owner_busy, unused_hold_done, unused_release_ok;
    logic [63:0] unused_rsp_G, unused_rsp_Wp, unused_rsp_Wm;

    adc2_capture u_adc2_fs (
        .clk               (clk_fs),
        .rst_n             (rst_n),
        .capture_arm_req   (frame_evt_done),
        .capture_reg_req   (capture_fire),
        .capture_tag       (conv_tag_q),
        .capture_qid       (conv_qid_q),
        .capture_phase_h   (conv_h_q),
        .capture_phase_v   (conv_v_q),
        .capture_G_q30     (64'd0),
        .capture_Wp_q30    (64'd0),
        .capture_Wm_q30    (64'd0),
        .adc2_capture_arm_o(adc2_capture_arm_o),
        .adc2_hold_ack_i   (hold_ack_i),
        .adc2_valid_i      (adc2_valid_i),
        .adc2_ready_o      (adc2_ready_w),
        .adc2_raw_i        (adc2_raw_i),
        .adc2_range_err_i  (adc2_range_err_i),
        .rsp_valid_o       (rsp_valid_w),
        .rsp_tag_o         (rsp_tag_w),
        .rsp_qid_o         (rsp_qid_w),
        .rsp_raw_o         (rsp_raw_w),
        .rsp_range_o       (rsp_range_w),
        .cfg_epoch         (cfg_epoch_i),
        .rsp_epoch_o       (rsp_epoch_w),
        .owner_busy_o      (unused_owner_busy),
        .hold_done_o       (unused_hold_done),
        .late_err_o        (adc2_late_err_o),
        .release_ok_o      (unused_release_ok),
        .rsp_G_q30_o       (unused_rsp_G),
        .rsp_Wp_q30_o      (unused_rsp_Wp),
        .rsp_Wm_q30_o      (unused_rsp_Wm)
    );

    recon_v21 #(
        .ALPHA_FIXED_UNIT (ALPHA_UNIT_BYPASS)
    ) u_recon_fs (
        .clk       (clk_fs),
        .rst_n     (rst_n),
        .in_valid  (rsp_valid_w),
        .adc2_raw  (rsp_raw_w),
        .Wm_q30    (wsum_Wm_q),
        .G_q30     (wsum_G_q),
        .Wp_q30    (wsum_Wp_q),
        .alpha_q30 (ALPHA_UNIT_BYPASS ? 32'd1073741824 : alpha_q30_cfg_i),
        .b_q24     (b_q24_cfg_i),
        .in_epoch  (rsp_epoch_w),
        .in_tag    (rsp_tag_w),
        .cfg_epoch (cfg_epoch_i),
        .out_valid (out_valid_o),
        .code_o    (out_code_o),
        .out_sat   (out_sat_o),
        .out_err   (out_err_o),
        .out_tag   (out_tag_o)
    );

    // =========================================================================
    // 5. 节点快照与观测输出汇聚
    // =========================================================================
    assign adc2_ready_o     = adc2_ready_w;
    assign node_post_o      = final_post_ev_q;
    assign node_pre_o       = conv_pre_q;
    assign node_ids_o       = conv_ids_q;
    assign node_tag_o       = conv_tag_q;
    assign node_valid_o     = frame_evt_done;
    assign ev_apply_fire_o  = ev_apply_fire_w;
    assign ev_final_k_o     = ev_final_k_w;
    assign ev_done_o        = frame_evt_done;
    assign missed_evt_err_o = missed_evt_err_q;
    assign quiet_evt_err_o  = quiet_evt_err_q;
    assign err_o            = prepare_error || ev_err_w || ev_k_bad_w
                              || missed_evt_err_q || quiet_evt_err_q;

    // 显式归约未消费辅助信号，保证 Verilator -Wall 与 Yosys 零警告
    wire unused_dualrate_obs = ^{s18_conv, s18_idle, unused_s18_ids_next, unused_s18_rng,
        unused_pre_f, ev_valid_unused, ev_idx_unused, ev_bit_idx_unused,
        ev_final_applied_w, ev_done_w, ev_tag_w, ev_qid_w,
        wsum_valid_w, rsp_qid_w, rsp_range_w, alpha_q30_cfg_i,
        unused_owner_busy, unused_hold_done, unused_release_ok,
        unused_rsp_G, unused_rsp_Wp, unused_rsp_Wm};

endmodule
`default_nettype wire
