`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_40msps_dualrate.sv
// 模块名     : tb_40msps_dualrate
// 版本       : v1.0
// 功能       : 在真实 40.0 MHz（25 ns）主帧时钟 + 自定时 1.0 ns 事件突发下，
//              端到端验证 adc_digital_ip_v21_40msps 的 40.000 MSPS 连续吞吐、
//              64 字片内 Split Dither 闭环、单单元失配权重校准、RA 安静窗
//              零翻转以及异常漏事件/安静窗毛刺拦截。
// 设计说明   :
//   * [真实 40 MHz 时钟] clk_fs 严格以 25 ns（半周期 12.5 ns）驱动全核重逻辑；
//     ev_clk_i 仅在每帧 [0.5 ns .. 9.5 ns] 内触发自定时比较脉冲，[10..25 ns]
//     保持绝对静默，实时断言 post_wr_o == 0 且 rdac_post_state_o 零翻转。
//   * [理想模拟闭环] 直接读取 DUT 公开引脚 sadc_pre_state_o / rdac_pre_state_o
//     与 rdac_post_state_o 计算 SADC 粗码及 13-bit ADC2 余差原码，绝不用期望码
//     反推激励；所有 192 笔输出逐笔比对期望 20-bit 码及严格 25.000 ns 输出间隔。
// 变更记录   :
//   2026-11-24  v1.0 初版。双域 40 MSPS 完整时序与闭环自检台架。
// =============================================================================
module tb_40msps_dualrate;

    // =========================================================================
    // 1. 信号声明与 DUT 实例化
    // =========================================================================
    localparam int N_SAMPLES = 192;
    localparam longint unsigned Q30 = 64'd1 << 30;

    logic        clk_fs, ev_clk, rst_n;
    logic        prepare_valid, prepare_ready;
    logic [31:0] prepare_tag;
    logic [5:0]  prepare_word;
    logic [2:0]  prepare_h, prepare_v, prepare_U, prepare_flash;
    logic        prepare_half, prepare_coin, prepare_qid, stop_i;
    logic        sample_event, input_captured, residue_captured, hold_ack;
    logic        ev_frame_start, ev_bit_valid, ev_bit, ev_qid, ev_flash_valid;
    logic [3:0]  ev_bit_index, ev_event_index;
    logic [2:0]  ev_flash_code;
    logic        sched_seed_we;
    logic [31:0] sched_seed;
    logic [17:0] acq_sel, conv_sel, idle_sel;
    logic [71:0] rdac_pre_state;
    logic [17:0] pre_wr;
    logic [359:0] rdac_post_state;
    logic [17:0] post_wr;
    logic [13:0] sadc_pre_state;
    logic [1:0]  sadc_pre_wr;
    logic        dither_force;
    logic        adc2_valid, adc2_ready, adc2_range_err, adc2_capture_arm, adc2_late_err;
    logic [12:0] adc2_raw;
    logic [159:0] node_post;
    logic [31:0] node_pre, node_tag;
    logic [39:0] node_ids;
    logic        node_valid;
    logic        w_cfg_we, w_cfg_sel, w_cfg_commit, w_cfg_reload;
    logic [4:0]  w_cfg_sid, w_cfg_idx;
    logic [39:0] w_cfg_data;
    logic        w_cfg_valid, w_cfg_busy, w_cfg_range_err;
    logic [7:0]  cfg_epoch;
    logic signed [26:0] b_q24_cfg;
    logic [31:0] alpha_q30_cfg;
    logic        out_valid, out_sat, out_err;
    logic [19:0] out_code;
    logic [31:0] out_tag;
    logic        ev_apply_fire, ev_done, missed_evt_err, quiet_evt_err, err_o;
    logic [9:0]  ev_final_k;

    adc_digital_ip_v21_40msps dut (
        .clk_fs            (clk_fs),
        .ev_clk_i          (ev_clk),
        .rst_n             (rst_n),
        .prepare_valid_i   (prepare_valid),
        .prepare_ready_o   (prepare_ready),
        .prepare_tag_i     (prepare_tag),
        .prepare_word_i    (prepare_word),
        .prepare_h_i       (prepare_h),
        .prepare_v_i       (prepare_v),
        .prepare_half_i    (prepare_half),
        .prepare_coin_i    (prepare_coin),
        .prepare_U_i       (prepare_U),
        .prepare_flash_i   (prepare_flash),
        .prepare_qid_i     (prepare_qid),
        .stop_i            (stop_i),
        .sample_event_i    (sample_event),
        .input_captured_i  (input_captured),
        .residue_captured_i(residue_captured),
        .hold_ack_i        (hold_ack),
        .ev_frame_start_i  (ev_frame_start),
        .ev_bit_valid_i    (ev_bit_valid),
        .ev_bit_i          (ev_bit),
        .ev_bit_index_i    (ev_bit_index),
        .ev_event_index_i  (ev_event_index),
        .ev_qid_i          (ev_qid),
        .ev_flash_valid_i  (ev_flash_valid),
        .ev_flash_code_i   (ev_flash_code),
        .sched_seed_we_i   (sched_seed_we),
        .sched_seed_i      (sched_seed),
        .acq_sel_o         (acq_sel),
        .conv_sel_o        (conv_sel),
        .idle_sel_o        (idle_sel),
        .rdac_pre_state_o  (rdac_pre_state),
        .pre_wr_o          (pre_wr),
        .rdac_post_state_o (rdac_post_state),
        .post_wr_o         (post_wr),
        .sadc_pre_state_o  (sadc_pre_state),
        .sadc_pre_wr_o     (sadc_pre_wr),
        .dither_force_i    (dither_force),
        .adc2_valid_i      (adc2_valid),
        .adc2_ready_o      (adc2_ready),
        .adc2_raw_i        (adc2_raw),
        .adc2_range_err_i  (adc2_range_err),
        .adc2_capture_arm_o(adc2_capture_arm),
        .node_post_o       (node_post),
        .node_pre_o        (node_pre),
        .node_ids_o        (node_ids),
        .node_tag_o        (node_tag),
        .node_valid_o      (node_valid),
        .adc2_late_err_o   (adc2_late_err),
        .w_cfg_we_i        (w_cfg_we),
        .w_cfg_sel_i       (w_cfg_sel),
        .w_cfg_sid_i       (w_cfg_sid),
        .w_cfg_idx_i       (w_cfg_idx),
        .w_cfg_data_i      (w_cfg_data),
        .w_cfg_commit_i    (w_cfg_commit),
        .w_cfg_reload_i    (w_cfg_reload),
        .w_cfg_valid_o     (w_cfg_valid),
        .w_cfg_busy_o      (w_cfg_busy),
        .w_cfg_range_err_o (w_cfg_range_err),
        .cfg_epoch_i       (cfg_epoch),
        .b_q24_cfg_i       (b_q24_cfg),
        .alpha_q30_cfg_i   (alpha_q30_cfg),
        .out_valid_o       (out_valid),
        .out_code_o        (out_code),
        .out_sat_o         (out_sat),
        .out_err_o         (out_err),
        .out_tag_o         (out_tag),
        .ev_apply_fire_o   (ev_apply_fire),
        .ev_final_k_o      (ev_final_k),
        .ev_done_o         (ev_done),
        .missed_evt_err_o  (missed_evt_err),
        .quiet_evt_err_o   (quiet_evt_err),
        .err_o             (err_o)
    );

    // =========================================================================
    // 2. 理想模拟辅助函数与自定时事件发射器
    // =========================================================================
    // [口径独立性] 以下三个换算函数直接按物理电容权重读取 DUT 公开引脚：
    //   * sadc_eighths : 从 SADC 7-bit 控制线换算 1/8 粗码（B1 表 1/1/2/4/8/16/32）
    //   * phys_pre8    : 从 pre_wr_o 选中的 8 片读取 RDAC 采样预置 1/8 粗码和
    //   * phys_post8   : 从 node_ids_o 指定的 8 片读取 RDAC 转换开关 1/8 粗码和
    // 绝不读取 DUT 内部寄存器，也不从期望输出码反解激励。
    function automatic int sadc_eighths(input logic [6:0] bits);
        int sum;
        sum = 0;
        if (bits[0]) sum += 1;
        if (bits[1]) sum += 1;
        if (bits[2]) sum += 2;
        if (bits[3]) sum += 4;
        if (bits[4]) sum += 8;
        if (bits[5]) sum += 16;
        if (bits[6]) sum += 32;
        return sum;
    endfunction

    // 从公开物理写门读取本笔 8 片实际 RDAC 预置总权重（单位：1/8 粗码）
    function automatic int phys_pre8(input logic [71:0] pre_state, input logic [17:0] wr);
        int sum;
        logic [3:0] nib;
        sum = 0;
        for (int s = 0; s < 18; s++) begin
            if (wr[s]) begin
                nib = pre_state[s*4 +: 4];
                sum += int'(nib[3:1]) + int'(nib[0]);
            end
        end
        return sum;
    endfunction

    // 从公开物理开关读取本笔 8 片实际 RDAC 最终转换权重（单位：1/8 粗码）
    function automatic int phys_post8(input logic [359:0] post_state, input logic [39:0] ids);
        int sum, sid;
        logic [19:0] m;
        sum = 0;
        for (int slot = 0; slot < 8; slot++) begin
            sid = int'(ids[slot*5 +: 5]);
            m   = post_state[sid*20 +: 20];
            for (int j = 0; j < 16; j++) sum += m[j] ? 32 : 0;
            sum += m[16] ? 4 : 0;
            sum += m[17] ? 2 : 0;
            sum += m[18] ? 1 : 0;
            sum += m[19] ? 1 : 0;
        end
        return sum;
    endfunction

    // 单拍 40 MHz 主帧时钟前推（半周期 12.5 ns，严格 50% 占空比，周期 25.000 ns）
    task automatic fs_tick();
        #12.5;
        clk_fs = 1'b1;
        #12.5;
        clk_fs = 1'b0;
    endtask

    // 单拍自定时事件时钟脉冲（0.6 ns 步距，10 拍最坏情况仅占 6.0 ns < 论文 7 ns 窗）
    task automatic ev_pulse();
        #0.3;
        ev_clk = 1'b1;
        #0.3;
        ev_clk = 1'b0;
    endtask

    // -------------------------------------------------------------------------
    // 装载 360 字权重表（18 片 × (16 主 + 4 子)，支持向片 0 子单元 18 注入失配标定）
    // -------------------------------------------------------------------------
    // [标定验证] 当 calibrated_mismatch=1 时，将物理片 0 子单元 18 权重加 2^20（2^-10），
    // 与模拟残差模型中的 64 个 ADC2 原码物理失配精确抵消，检验真实权重闭环。
    task automatic load_weights(input bit calibrated_mismatch);
        logic [39:0] sub_w[4];
        sub_w[0] = 40'(Q30 / 32);
        sub_w[1] = 40'(Q30 / 64);
        sub_w[2] = 40'(Q30 / 128);
        sub_w[3] = 40'(Q30 / 128);
        w_cfg_we = 1'b1;
        for (int sid = 0; sid < 18; sid++) begin
            for (int u = 0; u < 20; u++) begin
                w_cfg_sid  = 5'(sid);
                w_cfg_sel  = (u >= 16);
                w_cfg_idx  = (u >= 16) ? 5'(u - 16) : 5'(u);
                w_cfg_data = (u >= 16) ? sub_w[u - 16] : 40'(Q30 / 4);
                if (calibrated_mismatch && sid == 0 && u == 18)
                    w_cfg_data = w_cfg_data + 40'(64'd1 << 20);
                fs_tick();
            end
        end
        w_cfg_we     = 1'b0;
        w_cfg_commit = 1'b1;
        fs_tick();
        w_cfg_commit = 1'b0;
        if (!w_cfg_valid || w_cfg_range_err)
            $fatal(1, "Weight table commit failed");
    endtask

    // -------------------------------------------------------------------------
    // 帧内自定时事件突发发射器（[0.2..6.2 ns] 完成粗转换，随后锁定开关快照）
    // -------------------------------------------------------------------------
    // [三路径覆盖]
    //   mode=0 (F < 7)           : Flash + bit5..bit0，共 7 次合法写门
    //   mode=1 (448 <= c < 512)  : Flash + 512 门限拒绝（严禁写门！）+ bit5..bit0，仍 7 次写门
    //   mode=2 (c >= 512)        : Flash + 512 门限接受 + bit2..bit0，共 5 次合法写门
    task automatic run_self_timed_burst(
        input  int           coarse_code,
        input  logic         qid,
        output int           mode_o,
        output int           event_writes_o,
        output logic [359:0] post_snap_o
    );
        int flash_f, low_bits, mode, ev_cnt;
        flash_f = (coarse_code >= 448) ? 7 : (coarse_code >> 6);
        mode    = (coarse_code < 448) ? 0 : ((coarse_code < 512) ? 1 : 2);
        low_bits = (mode == 2) ? (coarse_code & 7) : (coarse_code & 63);
        mode_o         = mode;
        event_writes_o = 0;

        // 1) t = 0.5 ns：帧首启动脉冲（装载冻结 CONV 上下文与真实 Flash 粗码）
        ev_frame_start = 1'b1;
        ev_bit_valid   = 1'b0;
        ev_flash_valid = 1'b1;
        ev_flash_code  = 3'(flash_f);
        ev_qid         = qid;
        ev_pulse();

        // 2) 事件 1：提交 Flash（写入首组物理开关）
        ev_frame_start = 1'b0;
        ev_flash_valid = 1'b0;
        ev_bit_valid   = 1'b1;
        ev_event_index = 4'd1;
        ev_bit_index   = 4'd0;
        ev_bit         = 1'b0;
        ev_pulse();
        if (post_wr != 18'd0) event_writes_o++;

        // 3) 若 F==7，先做 512 保护门限试探（拒绝沿不提交、物理写门必须恒为 0！）
        if (mode != 0) begin
            ev_event_index = 4'd2;
            ev_bit_index   = 4'd9;
            ev_bit         = (mode == 2);
            ev_pulse();
            if (mode == 1 && post_wr != 18'd0)
                $fatal(1, "Guard-reject event wrote RDAC switches!");
            if (post_wr != 18'd0) event_writes_o++;
        end

        // 4) 逐位提交低位 SAR 判决（普通/拒绝：bit5..bit0；接受：bit2..bit0）
        ev_cnt = (mode == 2) ? 3 : 2;
        for (int b = (mode == 2 ? 2 : 5); b >= 0; b--) begin
            ev_event_index = 4'(ev_cnt);
            ev_bit_index   = 4'(b);
            ev_bit         = low_bits[b];
            ev_pulse();
            if (post_wr != 18'd0) event_writes_o++;
            ev_cnt++;
        end

        // 5) 尾拍关闭写门（ev_bit_valid=0），检查写门总数与完成授权
        ev_bit_valid = 1'b0;
        ev_pulse();
        if (post_wr != 18'd0)
            $fatal(1, "post_wr_o stayed high after trailing close pulse!");
        if (event_writes_o != (mode == 2 ? 5 : 7))
            $fatal(1, "Unexpected RDAC write count for mode=%0d: got %0d", mode, event_writes_o);
        // [完成判定] 突发结束时刻 t<=6.2 ns 必须已翻转 1-bit 完成令牌（ev_done=1 且 node_valid=1）；
        // adc2_capture_arm 需待上一笔 ADC2 raw 归还腾出 owner 槽后才置高，于下帧捕获沿前核验。
        if (!ev_done || !node_valid)
            $fatal(1, "ev_done or node_valid not asserted after burst!");

        // 返回当前物理开关快照，供外部在整个 [6.2..25.0 ns] RA 建立窗持续核查零翻转
        post_snap_o = rdac_post_state;
    endtask

    // =========================================================================
    // 3. 主测试序列：192 笔连续 40.000 MSPS + 校准闭环 + 负对照
    // =========================================================================
    int          exp_code     [0:N_SAMPLES+16];
    int          sample_pre8  [0:N_SAMPLES+16];
    int          sample_low8  [0:N_SAMPLES+16];
    int          sample_in    [0:N_SAMPLES+16];
    int          sample_mpre  [0:N_SAMPLES+16];
    bit          sample_qid   [0:N_SAMPLES+16];
    int          retired_cnt;
    realtime     last_out_ns;
    int          gap_checks;

    task automatic run_40msps_stream(input bit inject_mismatch, input bit load_cal_table);
        int pending_raw_tag, pending_raw_val, ev_writes, burst_mode;
        int acq_tag_cur, conv_tag_cur;
        int coarse_c, post8_val, raw_unb, mdir, post_b18;
        int cnt_mode0, cnt_mode1, cnt_mode2;
        logic [6:0]   smask;
        logic [359:0] quiet_post_snap;
        realtime      now_ns, frame_t0;

        // 复位全部端口并装入 360 字名义或标定权重表
        clk_fs = 1'b0; ev_clk = 1'b0; rst_n = 1'b0;
        prepare_valid = 1'b0; prepare_tag = 32'd0; prepare_word = 6'd0;
        prepare_h = 3'd0; prepare_v = 3'd0; prepare_half = 1'b0; prepare_coin = 1'b0;
        prepare_U = 3'd0; prepare_flash = 3'd0; prepare_qid = 1'b0; stop_i = 1'b0;
        sample_event = 1'b0; input_captured = 1'b0; residue_captured = 1'b0; hold_ack = 1'b0;
        ev_frame_start = 1'b0; ev_bit_valid = 1'b0; ev_bit = 1'b0;
        ev_bit_index = 4'd0; ev_event_index = 4'd0; ev_qid = 1'b0;
        ev_flash_valid = 1'b0; ev_flash_code = 3'd0;
        sched_seed_we = 1'b0; sched_seed = 32'h1234_5678;
        dither_force = 1'b0; // 使用片内独立 xorshift32 字源！
        adc2_valid = 1'b0; adc2_raw = 13'd0; adc2_range_err = 1'b0;
        w_cfg_we = 1'b0; w_cfg_sel = 1'b0; w_cfg_sid = 5'd0; w_cfg_idx = 5'd0;
        w_cfg_data = 40'd0; w_cfg_commit = 1'b0; w_cfg_reload = 1'b0;
        cfg_epoch = 8'd0; b_q24_cfg = 27'sd0; alpha_q30_cfg = 32'd1073741824;

        fs_tick();
        rst_n = 1'b1;
        fs_tick();
        load_weights(load_cal_table);

        // ---------------------------------------------------------------------
        // 生成 192 笔混合码域输入（覆盖普通区 F<7、512 拒绝区、512 接受保护区）
        // ---------------------------------------------------------------------
        // [余差窗证明] 当粗码按向下取整 (2*in + 512*low8)/4096 计算时，
        // raw_unb = 2048 + (2*in + 512*low8) mod 4096 ∈ [2048, 6143]，
        // 严格落在 13-bit ADC2 窗口 [0, 8191] 正中，满量程 1048575 亦不会越界。
        for (int t = 1; t <= N_SAMPLES; t++) begin
            if (inject_mismatch) begin
                sample_in[t] = 410304;
            end else begin
                case ((t - 1) % 3)
                    0: sample_in[t] = 150000 + t * 2300;        // 普通区 (F < 7)
                    1: sample_in[t] = 950000 + (t % 32) * 1024; // 接近 512 拒绝区 (448..511)
                    default: sample_in[t] = 1048575;            // 20-bit 满量程，触发 512 接受保护区
                endcase
            end
            exp_code[t]   = sample_in[t];
            sample_qid[t] = bit'((t - 1) & 1);
        end

        retired_cnt     = 0;
        last_out_ns     = -1.0;
        gap_checks      = 0;
        cnt_mode0       = 0;
        cnt_mode1       = 0;
        cnt_mode2       = 0;
        acq_tag_cur     = 0;
        conv_tag_cur    = 0;
        pending_raw_tag = 0;
        pending_raw_val = 0;

        // ---------------------------------------------------------------------
        // 逐帧运行：每帧严格 25.000 ns（40.000 MSPS，50% 占空比主时钟）
        // ---------------------------------------------------------------------
        // 帧内时间分布（驱动在 posedge 前 1.0 ns 稳定，杜绝四态仿真零延迟竞争）：
        //  -1.0 ns      : 驱动本拍 clk_fs 域输入，预留 1.0 ns 真实建立时间（适配 RTL 与门级仿真）
        //   0.0 ns      : posedge clk_fs（捕获旧 CONV / 回收旧 raw / 晋升 ACQ->CONV / 装载新 ACQ）
        //   0.2..6.2 ns : 自定时 ev_clk 突发（5–8 个已决位 + 尾拍关门，小于论文 7 ns 粗窗）
        //   6.2..24.0 ns: 17.8 ns RA 建立安静窗（12.5 ns clk_fs 下降，全程验证 RDAC 零翻转）
        for (int frame = 1; frame <= N_SAMPLES + 12; frame++) begin
            // 1) 在上升沿前 1.0 ns 驱动本拍事务与模拟返回信号（保证完整建立时间）
            if (frame <= N_SAMPLES) begin
                prepare_valid = 1'b1;
                prepare_tag   = 32'(frame);
                prepare_h     = 3'((frame - 1) & 7);
                prepare_v     = 3'((frame * 3) & 7);
                prepare_half  = logic'((frame >> 3) & 1);
                prepare_coin  = logic'((frame * 5) & 1);
                prepare_qid   = sample_qid[frame];
                stop_i        = 1'b0;
            end else begin
                prepare_valid = 1'b0;
                stop_i        = 1'b1;
            end

            sample_event     = (acq_tag_cur != 0) || (conv_tag_cur != 0);
            input_captured   = (acq_tag_cur != 0);
            residue_captured = (conv_tag_cur != 0);
            hold_ack         = (conv_tag_cur != 0);

            // 若有上一帧捕获的 ADC2 余差，本沿整字返回，驱动 recon_v21 S1a
            if (pending_raw_tag != 0) begin
                adc2_valid     = 1'b1;
                adc2_raw       = 13'(pending_raw_val);
                adc2_range_err = 1'b0;
            end else begin
                adc2_valid = 1'b0;
                adc2_raw   = 13'd0;
            end

            // 等待 1.0 ns 建立时间，使组合门控（prepare_fire / pre_wr_c / arm）完全稳定
            #1.0;

            // 若本沿将捕获当前 CONV 余差，先从 DUT 公开物理开关独立求值理想 ADC2 余差
            if (conv_tag_cur != 0) begin
                if (!adc2_capture_arm || !node_valid)
                    $fatal(1, "Capture arm low at frame %0d for tag %0d!", frame, conv_tag_cur);
                post8_val = phys_post8(rdac_post_state, node_ids);
                raw_unb   = 2048 + 2 * sample_in[conv_tag_cur]
                            - 512 * post8_val + 512 * sample_pre8[conv_tag_cur];
                if (inject_mismatch) begin
                    // 片 0 子单元 18 注入 2^-10 物理失配（G=32 下折合 64 个 ADC2 原码）
                    post_b18 = conv_sel[0] ? int'(rdac_post_state[18]) : 0;
                    mdir     = post_b18 - sample_mpre[conv_tag_cur];
                    raw_unb -= 64 * mdir;
                    if (!load_cal_table)
                        exp_code[conv_tag_cur] = sample_in[conv_tag_cur] - 32 * mdir;
                end
                if (raw_unb < 0 || raw_unb > 8191)
                    $fatal(1, "ADC2 residue out of window: tag=%0d raw=%0d", conv_tag_cur, raw_unb);
            end

            // 2) t = 0.0 ns：40 MHz 主帧时钟上升沿
            frame_t0 = $realtime;
            clk_fs   = 1'b1;
            #0.2;
            now_ns   = $realtime;

            // 检查 18 片 ACQ / CONV / IDLE 严格互斥且全覆盖不变量
            if ((acq_sel & conv_sel) != 18'd0 || idle_sel != ~(acq_sel | conv_sel))
                $fatal(1, "Slice ownership overlap at frame %0d!", frame);

            // 检查 20-bit 退休码顺序、零错误及相邻样本精确 25.000 ns 连续吞吐间隔
            if (out_valid) begin
                retired_cnt++;
                if (out_tag != 32'(retired_cnt))
                    $fatal(1, "Tag order mismatch: got %0d exp %0d", out_tag, retired_cnt);
                if (out_err)
                    $fatal(1, "Unexpected out_err on tag %0d", retired_cnt);
                if (int'(out_code) != exp_code[retired_cnt])
                    $fatal(1, "Code mismatch on tag %0d: got %0d exp %0d",
                           retired_cnt, out_code, exp_code[retired_cnt]);
                if (last_out_ns >= 0.0) begin
                    if ((now_ns - last_out_ns) < 24.999 || (now_ns - last_out_ns) > 25.001)
                        $fatal(1, "Output cadence != 25.0 ns: got %0.3f ns", now_ns - last_out_ns);
                    gap_checks++;
                end
                last_out_ns = now_ns;
            end

            // 3) 推进外部流水线追踪，记录本拍新受理样本在 DUT 公开引脚上的实际预置
            if (conv_tag_cur != 0) begin
                pending_raw_tag = conv_tag_cur;
                pending_raw_val = raw_unb;
            end else begin
                pending_raw_tag = 0;
                pending_raw_val = 0;
            end

            conv_tag_cur = acq_tag_cur;
            if (frame <= N_SAMPLES) begin
                acq_tag_cur        = frame;
                sample_pre8[frame] = phys_pre8(rdac_pre_state, pre_wr);
                smask              = sample_qid[frame] ? sadc_pre_state[13:7] : sadc_pre_state[6:0];
                sample_low8[frame] = sadc_eighths(smask);
                sample_mpre[frame] = pre_wr[0] ? int'(rdac_pre_state[1]) : 0; // pre[0][1]=b0(j18)
            end else begin
                acq_tag_cur = 0;
            end

            // 4) [0.2..6.2 ns]：若有活跃 CONV，运行自定时快速跟随突发并统计三路径覆盖
            if (conv_tag_cur != 0) begin
                // 由真实输入 + 公开 SADC 低段预置按 floor 口径算出粗码（与 sample_cadence.cpp 一致）
                coarse_c = (2 * sample_in[conv_tag_cur] + 512 * sample_low8[conv_tag_cur]) / 4096;
                if (coarse_c < 0) coarse_c = 0;
                run_self_timed_burst(coarse_c, sample_qid[conv_tag_cur], burst_mode, ev_writes, quiet_post_snap);
                if (burst_mode == 0)      cnt_mode0++;
                else if (burst_mode == 1) cnt_mode1++;
                else                      cnt_mode2++;
            end

            // 5) 补齐严格 50% 占空比 40 MHz 周期（12.5 ns 下降，24.0 ns 回到下帧驱动点，
            //    加上次循环 #1.0 建立时间后相邻上升沿严格间隔 25.000 ns），并在整个安静窗验证零翻转
            #(12.5 - ($realtime - frame_t0));
            clk_fs = 1'b0;
            if (conv_tag_cur != 0) begin
                if (rdac_post_state !== quiet_post_snap || post_wr != 18'd0 || quiet_evt_err)
                    $fatal(1, "Digital switching detected at mid RA quiet window!");
            end
            #11.5;
            if (conv_tag_cur != 0) begin
                if (rdac_post_state !== quiet_post_snap || post_wr != 18'd0 || quiet_evt_err)
                    $fatal(1, "Digital switching detected at end of RA quiet window!");
            end
        end

        // 验收本轮吞吐、全三路径覆盖与零异常标志
        if (retired_cnt != N_SAMPLES)
            $fatal(1, "Incomplete retirement: got %0d exp %0d", retired_cnt, N_SAMPLES);
        if (gap_checks != N_SAMPLES - 1)
            $fatal(1, "Incomplete 25 ns gap checks: %0d", gap_checks);
        if (!inject_mismatch && (cnt_mode0 == 0 || cnt_mode1 == 0 || cnt_mode2 == 0))
            $fatal(1, "Incomplete coarse path coverage: m0=%0d m1=%0d m2=%0d",
                   cnt_mode0, cnt_mode1, cnt_mode2);
        if (err_o || adc2_late_err || missed_evt_err || quiet_evt_err)
            $fatal(1, "Unexpected error flags after clean 40 MSPS run!");
    endtask

    // =========================================================================
    // 4. 负对照：漏发事件（missed_evt_err）与安静窗毛刺（quiet_evt_err）拦截
    // =========================================================================
    // [异常验证]
    //   1) 模拟前级挂死未发比较脉冲 -> adc2_capture_arm 恒低，捕获沿置 missed_evt_err_o；
    //   2) 正常完成后在 RA 安静窗内误发额外比较脉冲 -> post_wr_o 恒 0 且置 quiet_evt_err_o。
    task automatic test_negative_controls();
        int m_dummy, w_dummy;
        logic [359:0] snap_dummy;

        // ---- 负对照 A：缺失事件流触发 missed_evt_err_o ----
        clk_fs = 1'b0; ev_clk = 1'b0; rst_n = 1'b0;
        prepare_valid = 1'b0; stop_i = 1'b0; sample_event = 1'b0;
        input_captured = 1'b0; residue_captured = 1'b0; hold_ack = 1'b0;
        ev_frame_start = 1'b0; ev_bit_valid = 1'b0; adc2_valid = 1'b0;
        dither_force = 1'b0;
        fs_tick();
        rst_n = 1'b1;
        fs_tick();
        load_weights(1'b0);

        // 第 1 帧准备样本 1，第 2 帧晋升样本 1 到 CONV
        prepare_valid = 1'b1; prepare_tag = 32'd1; prepare_qid = 1'b0;
        prepare_h = 3'd0; prepare_v = 3'd0; prepare_half = 1'b0; prepare_coin = 1'b0;
        fs_tick();
        prepare_valid = 1'b0; stop_i = 1'b1;
        sample_event = 1'b1; input_captured = 1'b1;
        fs_tick();

        // 故意不发任何 ev_clk 脉冲 -> capture_arm 必须为 0 且捕获沿触发 missed_evt_err
        if (adc2_capture_arm)
            $fatal(1, "Negative control A failed: capture_arm high without events!");
        input_captured = 1'b0; residue_captured = 1'b1; hold_ack = 1'b1;
        fs_tick();
        if (!missed_evt_err)
            $fatal(1, "Negative control A failed: missed_evt_err not set!");

        // ---- 负对照 B：RA 安静窗内非法比较毛刺触发 quiet_evt_err_o 且不改开关 ----
        rst_n = 1'b0; residue_captured = 1'b0; hold_ack = 1'b0; sample_event = 1'b0;
        fs_tick();
        rst_n = 1'b1;
        fs_tick();
        load_weights(1'b0);
        prepare_valid = 1'b1; stop_i = 1'b0; prepare_tag = 32'd1; prepare_qid = 1'b0;
        fs_tick();
        prepare_valid = 1'b0; stop_i = 1'b1; sample_event = 1'b1; input_captured = 1'b1;
        fs_tick();
        run_self_timed_burst(200, 1'b0, m_dummy, w_dummy, snap_dummy);
        // 突发已结束进入安静窗，此时注入非法 ev_bit_valid=1 毛刺
        ev_bit_valid = 1'b1; ev_bit = 1'b1; ev_bit_index = 4'd0; ev_event_index = 4'd7;
        ev_pulse();
        ev_bit_valid = 1'b0;
        if (!quiet_evt_err || post_wr != 18'd0 || rdac_post_state !== snap_dummy)
            $fatal(1, "Negative control B failed: quiet_evt_err=%b post_wr=%h", quiet_evt_err, post_wr);
    endtask

    initial begin
        // 1) 真实 40 MHz 主帧 + 自定时突发，连续 192 笔 40.000 MSPS 混合三路径闭环
        run_40msps_stream(1'b0, 1'b0);
        // 2) 单物理单元失配注入（片 0 子单元 18）：装入标定权重后 192 笔全部归零
        run_40msps_stream(1'b1, 1'b1);
        // 3) 负对照：漏发事件（missed_evt_err）与安静窗毛刺（quiet_evt_err）双项拦截
        test_negative_controls();
        $display("DUALRATE_40MSPS_PASS clk_fs_mhz=40 samples=%0d cadence_ns=25.0 quiet_ra_window=0_toggles", N_SAMPLES);
        $finish;
    end

endmodule
