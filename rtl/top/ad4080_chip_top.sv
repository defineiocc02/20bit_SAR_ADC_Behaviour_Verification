// =============================================================================
// 文件名     : ad4080_chip_top.sv
// 模块名     : ad4080_chip_top
// 版本       : 随 Git 源版本
// 功能       : 将 SAR 转换、DSP、控制面和两类结果读出接成同一个数字顶层。
// 设计说明   : 模拟宏、OTP 和 PHY 使用公开端口。转换结果在 DSP 入口换码，
//              片内仅由外围服务持有一份 16384×20 的结果存储。
// 时钟域     : 常开控制、转换与 DSP、IO 域。CDC 保留在外围所有者处。
// =============================================================================
`timescale 1ns/1ps
`default_nettype none

module ad4080_chip_top #(
    parameter int unsigned AON_MAX_CLK_HZ = 10_000_000,
    parameter int unsigned OTP_WORDS = 1,         // 逻辑读取字数，须由授权数据绑定
    parameter int unsigned OTP_WORD_WIDTH = 1,
    parameter int unsigned OTP_TIMEOUT = 16,      // AON 适配器每步等待预算
    parameter bit GAIN_BOUND = 1'b0,             // 非单位工程映射需要显式选择
    parameter int GAIN_K = -1,
    parameter int GAIN_SHIFT = -1,
    parameter int SPI_MULTIBYTE_ORDER = -1,
    parameter int OFFSET_LSB_ADDRESS = -1,
    parameter int SPI_WRITE_SDO_DRIVE = 1, // Rev. B p.35，四线写数据相无 CRC 返回时驱动 0
    parameter int unsigned CORE_CLOCK_HZ = 0,    // 实际核心时钟；0 表示 tCONV 物理时长未绑定
    localparam int unsigned OTP_ADDR_WIDTH = OTP_WORDS > 1 ? $clog2(OTP_WORDS) : 1,
    localparam int unsigned OTP_IMAGE_WIDTH = OTP_WORDS * OTP_WORD_WIDTH
) (
    // --- 电源和上游管理域 ---
    input  logic clk_aon_i,                             // 常开控制时钟
    input  logic clk_core_i,                            // DSP、FIFO 和功能单口共享时钟
    input  logic clk_io_i,                              // 位对装载域
    input  logic external_io_i,                         // 冷启动 strap，1 为外供
    input  logic por_d_ok_async_i, vdd33_ok_async_i, ref_ok_async_i, iovdd_ok_async_i, ana_ready_async_i,
    input  logic sw_reset_req_aon_i, mode_req_valid_aon_i,// 裸 AON 管理端，独立于 SPI 数值配置
    input  logic [1:0] mode_req_aon_i,
    input  logic wake_recover_done_aon_i,               // 模拟适配器恢复确认
    input  logic vdd11_ldo_pd_safe_aon_i,               // RST/隔离完成后的核心 LDO 安全关断资格
    output logic vdd11_ldo_en_o, iovdd_ldo_en_o,         // 对供电宏的实际请求，开关由宏执行
    output logic sw_reset_accept_aon_o, mode_req_accept_aon_o, wake_recover_req_aon_o,

    // --- OTP 仍以真实适配器握手表示 ---
    input  logic otp_power_good_aon_i, otp_adapters_quiescent_aon_i,
    output logic otp_adapters_cancel_aon_o,
    output logic otp_req_valid_aon_o,
    input  logic otp_req_ready_aon_i,
    output logic [OTP_ADDR_WIDTH-1:0] otp_req_addr_aon_o,
    input  logic otp_rsp_valid_aon_i, otp_rsp_error_aon_i,
    input  logic [OTP_WORD_WIDTH-1:0] otp_rsp_data_aon_i,
    output logic otp_rsp_ready_aon_o,
    output logic otp_verify_req_valid_aon_o,
    input  logic otp_verify_req_ready_aon_i,
    output logic [OTP_IMAGE_WIDTH-1:0] otp_verify_image_aon_o,
    input  logic otp_verify_rsp_valid_aon_i, otp_verify_rsp_pass_aon_i,
    output logic otp_verify_rsp_ready_aon_o,

    // --- 已译码采样边沿和控制接口 ---
    input  logic cnv_tick_core_i,                     // 已译码 CNV 上升沿，核心时钟内单拍
    input  logic sample_source_idle_core_i,            // 模拟适配器确认已有转换和数值结果排尽
    output logic conversion_allow_core_o,
    output logic conversion_reset_no,              // 与 AON 控制的转换核复位同代
    input  logic cs_n, sclk, sdi,                     // 固定 DSP 配置 SPI 入口
    output logic sdo, sdo_oe,
    input  logic [3:0] gpio_i,
    output logic [3:0] gpio_o, gpio_oe,
    input  logic filter_sync_core_i, ext_event_core_i, // 同域逻辑事件，由外层确定真实 CNV
    output logic [7:0] status_o,
    output logic cfg_reject_o,
    output logic [3:0] cfg_reject_reason_o,
    output logic processing_reject_o,
    output logic cnv_data_timing_error_core_o,         // 结果拍点协议，独立于器件最短转换间隔错误
    output logic conversion_timing_error_core_o,      // CORE_CLOCK_HZ 已绑才比较相邻 CNV 实际间隔

    // --- 记录与 I/O 域外部 CRC 服务 ---
    input  logic record_command_core_i,                // 请求发布当前一整条完成记录
    output logic record_ready_core_o,                 // 可检查命令，拒绝另给脉冲
    output logic record_rejected_core_o,              // 当前无完整记录
    output logic record_done_core_o,                  // 组帧末尾已交付
    output logic record_failed_core_o,                // 失败退场，若已经读过则丢弃部分记录
    output logic record_available_core_o, capture_incomplete_core_o,
    output logic fifo_full_core_o, fifo_empty_core_o,  // 原计数满/空，区别于逻辑水位状态
    output logic capture_write_loss_core_o,           // 实际物理口失许可以及在途写丢失
    input  logic format_bound_io_i, crc_service_ready_io_i,
    input  logic abort_io_i, crc_valid_io_i, crc_error_io_i,
    input  logic [23:0] crc_io_i,
    output logic crc_start_io_o, crc_ready_io_o, crc_cancel_io_o,
    output logic io_record_epoch_o,                   // 掉电或 BIST 作废时 CRC 服务同步丢旧会话
    input  logic beat_ready_io_i,                     // 已输出载荷不得停拍
    output logic beat_valid_io_o, beat_first_io_o, beat_last_io_o,
    output logic [1:0] beat_pair_io_o, beat_kind_io_o,
    output logic first_payload_word_load_io_o, last_payload_word_load_io_o, // TX 首/末样本移入
    output logic frame_error_io_o,
    output logic [2:0] frame_error_code_io_o,
    input  logic result_dcs_ni, result_dclk_i,          // 采后结果 SPI 独立物理片选/时钟
    output logic [3:0] result_sdo_o, result_sdo_oe_o,    // 依次为 A、B、C、D
    output logic result_spi_ready_io_o,                 // 载荷邮箱就绪诊断，主机依 GPIO FIFO_FULL 起读

    // --- 连续 LVDS，时隙与采后记录的 I/O 端口独立 ---
    input  logic cnv_tick_io_i,                        // 同源 CNV，已在转发时钟域对齐
    input  logic [31:0] cnv_tag_io_i,                  // 与上游实际转换同一身份
    output logic continuous_config_ready_core_o,     // 0x15/16 最新快照已实际应用
    output logic continuous_cnv_allowed_io_o,         // 新预约的有效入口，已有帧仍允许退役
    output logic continuous_pair_valid_o, continuous_pair_first_o, continuous_pair_last_o,
    output logic [31:0] continuous_pair_tag_o, continuous_pair_source_tag_o,
    output logic continuous_pair_repeat_o,             // 滤波连续帧重复同一个真实已公布结果
    output logic continuous_da_fall_o, continuous_da_rise_o, continuous_db_fall_o, continuous_db_rise_o,
    output logic continuous_dco_clk_o, continuous_dco_oe_o, continuous_da_oe_o, continuous_db_oe_o,
    output logic continuous_cnv_rejected_io_o, continuous_result_rejected_io_o, continuous_frame_missing_io_o,
    output logic [31:0] continuous_frame_missing_tag_o,
    output logic continuous_result_drop_core_o,       // 两槽数据 CDC 无法接受上游实际结果
    output logic lvds_rx_double_io_o,                 // 0x17 已应用的 CLK 输入终端控制
    output logic [2:0] lvds_vod_control_io_o,           // 输出级请求编码，物理幅值由宏实现
    output logic lvds_cnv_input_select_io_o,           // 0x16[0] 已应用的 CNV 输入选择

    // --- SRAM 破坏性测试与恢复 ---
    input  logic bist_request_aon_i,                  // 排空并独占，无活动读记录才接手
    output logic bist_ready_aon_o, bist_grant_aon_o,
    input  logic bist_start_core_i, bist_checkerboard_core_i,
    output logic bist_start_ready_core_o,
    input  logic bist_reinit_request_core_i,           // 测试终态后明确允许清旧指针
    output logic bist_contents_invalid_core_o, bist_busy_core_o, bist_done_core_o,
    output logic [1:0] bist_fail_reason_core_o,

    // --- 同一转换的数字接口与静默故障诊断 ---
    input  logic analog_fault_core_i,  // 同域故障，包括未返回结果的模拟异常
    output logic stream_fault_core_o,  // 跨笔隔离，转换域硬/软复位清除
    output logic sample_retired_core_o, // DSP 接收码字的实际沿
    // ---------------------------------------------------------------- 事务
    input  logic        prepare_valid,
    output logic        prepare_ready,
    input  logic [31:0] prepare_tag,
    input  logic [5:0]  prepare_word,    // dither_force_i=1 时使用的定向六位字
    input  logic [2:0]  prepare_h,
    input  logic [2:0]  prepare_v,
    input  logic        prepare_half,
    input  logic        prepare_coin,
    input  logic [2:0]  prepare_U,       // 强制字的上段校验；生产生字时忽略
    input  logic [2:0]  prepare_flash,   // 提前提供的 F；同笔实测返回可在首提交前更新
    input  logic        prepare_qid,     // 从 0 起按接受顺序交替，编号不符拒收
    input  logic        stop_i,          // 停收新样本：已采过的 ACQ 仍要能转 CONV 排空

    // ---------------------------------------------------------------- 采样事件
    input  logic        sample_event,
    input  logic        input_captured,
    input  logic        residue_captured,
    input  logic        hold_ack,

    // ---------------------------------------------------------------- 事件入口
    input  logic        ev_start_i,      // 保留输入；顶层在实际 commit 时启动事件上下文
    input  logic        ev_bit_valid_i,
    input  logic        ev_bit_i,
    input  logic [3:0]  ev_bit_index_i,
    input  logic [3:0]  ev_event_index_i,
    input  logic        ev_qid_i,
    input  logic        ev_flash_valid_i, // 真实 Flash 返回；身份取 ev_qid_i
    input  logic [2:0]  ev_flash_code_i,  // 在采样沿至事件 1 之间可更新准备时的 F

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
    // ---------------------------------------------------------------- 权重表配置口
    //
    // 每次装一个 40 位系数，由选择位和地址定位到主表或子表。
    // 写入字段含地址、选择、数据和使能共 52 位，提交、重装、状态另行给出。
    //
    // 写序列（上电后一次性灌 360 个字）：
    //   for sid in 0..17: for j in 0..15: cfg_sel=0, cfg_sid=sid, cfg_idx=j, cfg_we=1
    //   for sid in 0..17: for j in 0..3 : cfg_sel=1, cfg_sid=sid, cfg_idx=j, cfg_we=1
    input  logic         w_cfg_we,
    input  logic         w_cfg_sel,
    input  logic [4:0]   w_cfg_sid,
    input  logic [4:0]   w_cfg_idx,
    input  logic [39:0]  w_cfg_data,
    input  logic         w_cfg_commit,   // 原子生效
    input  logic         w_cfg_reload,   // 重新配置（须先排空）
    output logic         w_cfg_valid,    // 配置已生效
    output logic         w_cfg_busy,
    output logic         w_cfg_range_err,  // 子权重超范围被拒
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
    output logic [159:0] post_slot_o,   // slot 级 160 位（便于与参考对拍）

    // ---- D4 扰动注入（新增引脚放在端口表末尾，原有引脚次序保留）----
    input  logic         dither_force_i,    // 生产接 0；定向重放接 1，使用 prepare_word/U
    output logic [13:0]  sadc_pre_state_o, // qid0 位 [6:0]，qid1 位 [13:7]，高有效采样预置
    output logic [1:0]   sadc_pre_wr_o,    // 受理沿后一个时钟周期有效，只写该笔 qid
    input  logic [31:0]  alpha_q30_cfg = 32'd1073741824 // 重构受理沿采纳，默认 Q30 单位增益。
);
    // =========================================================================
    // 1. 一套转换核和共同的退役资格
    // =========================================================================
    // 缺数、超时、合同错误均可触发隔离。饱和为有效端点码，保留给下游。
    logic core_stop, source_idle;
    logic guard_valid, guard_active, bad_slot, sample_window;
    logic [19:0] guard_code, dsp_code;
    logic [31:0] dsp_tag;
    // 兼容相同的并行台架 guard 端口。单芯片原始 out_tag/out_err 已公开。
    logic unused_rejected, unused_rejected_after_fault;
    logic [31:0] unused_accepted_tag, unused_rejected_tag;
    logic [4:0] unused_rejected_flags;

    assign core_stop = stop_i || !conversion_allow_core_o || stream_fault_core_o;
    adc_digital_ip_v21 u_converter (
        .clk(clk_core_i), .rst_n(conversion_reset_no),
        .prepare_valid(prepare_valid && conversion_allow_core_o && !stream_fault_core_o), .prepare_ready,
        .prepare_tag, .prepare_word, .prepare_h, .prepare_v, .prepare_half, .prepare_coin, .prepare_U,
        .prepare_flash, .prepare_qid, .stop_i(core_stop), .sample_event, .input_captured,
        .residue_captured, .hold_ack, .ev_start_i, .ev_bit_valid_i, .ev_bit_i, .ev_bit_index_i,
        .ev_event_index_i, .ev_qid_i, .ev_flash_valid_i, .ev_flash_code_i, .sched_ext_sel, .sched_acq,
        .sched_conv, .sched_idle, .sched_ids, .sched_seed_we, .sched_seed, .acq_sel_o, .conv_sel_o,
        .idle_sel_o, .rdac_pre_state_o, .pre_wr_o, .rdac_post_state_o, .post_wr_o, .adc2_valid_i,
        .adc2_ready_o, .adc2_raw_i, .adc2_range_err_i, .adc2_capture_arm_o, .node_post_o, .node_pre_o,
        .node_ids_o, .node_qid_o, .node_tag_o, .node_valid_o, .node_h_o, .node_v_o, .adc2_rsp_valid_o,
        .adc2_rsp_tag_o, .adc2_rsp_qid_o, .adc2_rsp_raw_o, .adc2_rsp_range_o, .adc2_late_err_o,
        .adc2_release_ok_o, .w_cfg_we, .w_cfg_sel, .w_cfg_sid, .w_cfg_idx, .w_cfg_data, .w_cfg_commit,
        .w_cfg_reload, .w_cfg_valid, .w_cfg_busy, .w_cfg_range_err, .cfg_epoch, .b_q24_cfg, .out_valid,
        .out_code, .out_sat, .out_err, .out_tag, .apply_fire_o, .k_o, .k_bad_o, .final_applied_o,
        .final_k_o, .err_o, .done_o, .tag_o, .qid_o, .post_slot_o, .dither_force_i, .sadc_pre_state_o,
        .sadc_pre_wr_o, .alpha_q30_cfg
    );

    sar20_result_guard u_retirement (
        .clk(clk_core_i), .rst_n(conversion_reset_no), .restart_sync(1'b0),
        .sar_dout(out_code), .sar_dout_valid(out_valid), .sar_sample_id(out_tag),
        .sar_flags({4'b0, out_err}), .sar_analog_ovf(analog_fault_core_i),
        // 越窗标志与对应响应同拍有效；只有这个沿有资格隔离整条新码流。
        .sar_conversion_fault(err_o || adc2_late_err_o || bad_slot ||
                              (adc2_rsp_valid_o && adc2_rsp_range_o)),
        .signed_input(guard_code), .stream_active(guard_active), .stream_fault(stream_fault_core_o),
        .sample_accepted(guard_valid), .accepted_sample_id(unused_accepted_tag),
        .sample_rejected(unused_rejected), .rejected_sample_id(unused_rejected_tag),
        .rejected_flags(unused_rejected_flags), .rejected_after_fault(unused_rejected_after_fault)
    );

    // =========================================================================
    // 2. 真实 CNV 下的单笔交接。每个物理边沿结束上一转换的接收时窗。
    // =========================================================================
    // 已完成码在下一 CNV 由 DSP 采纳。启动或同步阶段未开放的槽按其
    // 原有约定跳过；下一笔能同沿入槽。同一个时窗完成两笔则锁止，
    // 避免以换码或覆盖样本掩盖宏与数字路径间的截止期失配。
    logic pending_q;
    logic [19:0] code_q;
    logic [31:0] tag_q;
    assign bad_slot = conversion_allow_core_o && out_valid && pending_q && !cnv_tick_core_i;
    assign sample_retired_core_o = guard_active && conversion_allow_core_o &&
                                   cnv_tick_core_i && sample_window && (pending_q || guard_valid);
    assign dsp_code = pending_q ? code_q : guard_code;
    assign dsp_tag  = pending_q ? tag_q : out_tag;
    // 若 CNV 沿已有保留码，新码留给下个槽。空槽时允许同沿来码。
    wire keep_new = guard_valid && conversion_allow_core_o && (!cnv_tick_core_i || pending_q);
    always_ff @(posedge clk_core_i or negedge conversion_reset_no) begin
        if (!conversion_reset_no) pending_q <= 1'b0;
        else if (!guard_active || !conversion_allow_core_o) pending_q <= 1'b0;
        else if (keep_new) pending_q <= 1'b1;
        else if (cnv_tick_core_i) pending_q <= 1'b0;
    end
    always_ff @(posedge clk_core_i) begin
        if (keep_new) begin
            code_q <= guard_code;
            tag_q <= out_tag;
        end
    end
    // 外部适配器还须保证未完成转换已排空；本地负责已完成而未交接的字。
    assign source_idle = sample_source_idle_core_i && !pending_q && !out_valid;

    // =========================================================================
    // 3. 复用单存储的外围产品装配
    // =========================================================================
    // 原 DSP 的 FIFO 只保留队列元数据。SRAM、记录 SPI、组帧、连续 LVDS
    // 和 BIST、供电、OTP 各自沿用外围产品已有的服务与复位。
    ad4080_peripheral_product #(
        .AON_MAX_CLK_HZ(AON_MAX_CLK_HZ), .OTP_WORDS(OTP_WORDS), .OTP_WORD_WIDTH(OTP_WORD_WIDTH),
        .OTP_TIMEOUT(OTP_TIMEOUT), .GAIN_BOUND(GAIN_BOUND), .GAIN_K(GAIN_K), .GAIN_SHIFT(GAIN_SHIFT),
        .SPI_MULTIBYTE_ORDER(SPI_MULTIBYTE_ORDER), .OFFSET_LSB_ADDRESS(OFFSET_LSB_ADDRESS),
        .SPI_WRITE_SDO_DRIVE(SPI_WRITE_SDO_DRIVE), .P_CNV_TIMING_BOUND(1'b1),
        .CORE_CLOCK_HZ(CORE_CLOCK_HZ)
    ) u_peripheral (
        .sample_tag_core_i(dsp_tag), .sample_valid_core_i(sample_retired_core_o),
        .sample_signed_core_i(dsp_code),
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i, .por_d_ok_async_i, .vdd33_ok_async_i,
        .ref_ok_async_i, .iovdd_ok_async_i, .ana_ready_async_i, .sw_reset_req_aon_i,
        .mode_req_valid_aon_i, .mode_req_aon_i, .wake_recover_done_aon_i, .vdd11_ldo_pd_safe_aon_i,
        .vdd11_ldo_en_o, .iovdd_ldo_en_o, .sw_reset_accept_aon_o, .mode_req_accept_aon_o,
        .wake_recover_req_aon_o, .otp_power_good_aon_i, .otp_adapters_quiescent_aon_i,
        .otp_adapters_cancel_aon_o, .otp_req_valid_aon_o, .otp_req_ready_aon_i, .otp_req_addr_aon_o,
        .otp_rsp_valid_aon_i, .otp_rsp_error_aon_i, .otp_rsp_data_aon_i, .otp_rsp_ready_aon_o,
        .otp_verify_req_valid_aon_o, .otp_verify_req_ready_aon_i, .otp_verify_image_aon_o,
        .otp_verify_rsp_valid_aon_i, .otp_verify_rsp_pass_aon_i, .otp_verify_rsp_ready_aon_o,
        .cnv_tick_core_i, .sample_source_idle_core_i(source_idle), .conversion_allow_core_o,
        .conversion_reset_no, .cs_n, .sclk, .sdi, .sdo, .sdo_oe, .gpio_i, .gpio_o, .gpio_oe,
        .filter_sync_core_i, .ext_event_core_i, .status_o, .cfg_reject_o, .cfg_reject_reason_o,
        .processing_reject_o, .cnv_data_timing_error_core_o, .conversion_timing_error_core_o,
        .record_command_core_i, .record_ready_core_o, .record_rejected_core_o, .record_done_core_o,
        .record_failed_core_o, .record_available_core_o, .capture_incomplete_core_o, .fifo_full_core_o,
        .fifo_empty_core_o, .capture_write_loss_core_o, .format_bound_io_i, .crc_service_ready_io_i,
        .abort_io_i, .crc_valid_io_i, .crc_error_io_i, .crc_io_i, .crc_start_io_o, .crc_ready_io_o,
        .crc_cancel_io_o, .io_record_epoch_o, .beat_ready_io_i, .beat_valid_io_o, .beat_first_io_o,
        .beat_last_io_o, .beat_pair_io_o, .beat_kind_io_o, .first_payload_word_load_io_o,
        .last_payload_word_load_io_o, .frame_error_io_o, .frame_error_code_io_o, .result_dcs_ni,
        .result_dclk_i, .result_sdo_o, .result_sdo_oe_o, .result_spi_ready_io_o, .cnv_tick_io_i,
        .cnv_tag_io_i, .continuous_config_ready_core_o, .continuous_cnv_allowed_io_o,
        .continuous_pair_valid_o, .continuous_pair_first_o, .continuous_pair_last_o,
        .continuous_pair_tag_o, .continuous_pair_source_tag_o, .continuous_pair_repeat_o,
        .continuous_da_fall_o, .continuous_da_rise_o, .continuous_db_fall_o, .continuous_db_rise_o,
        .continuous_dco_clk_o, .continuous_dco_oe_o, .continuous_da_oe_o, .continuous_db_oe_o,
        .continuous_cnv_rejected_io_o, .continuous_result_rejected_io_o, .continuous_frame_missing_io_o,
        .continuous_frame_missing_tag_o, .continuous_result_drop_core_o, .lvds_rx_double_io_o,
        .lvds_vod_control_io_o, .lvds_cnv_input_select_io_o, .bist_request_aon_i, .bist_ready_aon_o,
        .bist_grant_aon_o, .bist_start_core_i, .bist_checkerboard_core_i, .bist_start_ready_core_o,
        .bist_reinit_request_core_i, .bist_contents_invalid_core_o, .bist_busy_core_o, .bist_done_core_o,
        .bist_fail_reason_core_o, .sample_window_core_o(sample_window)
    );
endmodule
`default_nettype wire
