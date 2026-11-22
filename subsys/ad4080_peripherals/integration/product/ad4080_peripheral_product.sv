`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_peripheral_product.sv
// 模块名     : ad4080_peripheral_product
// 版本       : v1.0
// 功能       : 复用固定 DSP、记录控制和单口四行 SRAM，交付采后记录与连续数据。
// 设计说明   : 上游交已重构补码样本和静默资格。记录地址来自原 FIFO 状态，
//              写受理和读退休均由实际单口受理沿驱动。CRC 服务在模块外。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_peripheral_product #(
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
    parameter bit P_CNV_TIMING_BOUND = 1'b1,       // 产品默认使用已译码 CNV 拍点
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

    // --- DSP 上游和配置接点 ---
    input  logic cnv_tick_core_i,                     // 已译码 CNV 上升沿，核心时钟内单拍
    input  logic [31:0] sample_tag_core_i,             // 同笔上游实际转换标签，与有效结果同沿
    input  logic sample_valid_core_i,                  // 仅在转换许可有效时送结果
    input  logic [19:0] sample_signed_core_i,           // 已重构 20 位二补码，转换算法在外部
    input  logic sample_source_idle_core_i,            // 上游停止且已知数值链在途输出排尽
    output logic conversion_allow_core_o,
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
    output logic [1:0] bist_fail_reason_core_o
);
    // =========================================================================
    // 1. 控制面排空和单口独占
    // =========================================================================
    logic fifo_reset_n, io_reset_n, cfg_allow_aon, cfg_allow_core, bist_hold_core;
    logic cfg_control_allow_aon, cfg_control_allow_core, cfg_poll_allow_aon, cfg_poll_allow_core;
    logic pwr_cmd_valid, pwr_cmd_ready, pwr_rsp_valid, pwr_rsp_accept;
    logic [1:0] pwr_cmd_op, pwr_cmd_value, pwr_rsp_op, pwr_mode, pwr_ldo_pd;
    logic aon_capture_clear, aon_clients_idle, aon_read_unlocked, aon_cfg_eval;
    logic services_bist_ready, record_locked, link_idle;
    logic [3:0] config_idle_pipe_q;  // 保证原 SPI 末沿同步链退出后才冻结记录
    logic write_req, write_ready, read_req, read_ready, read_taken, store_ready, store_valid;
    logic [13:0] write_addr, read_addr, next_record_addr;
    logic [19:0] write_data, store_data;
    logic [14:0] record_count_core, record_count_io;
    logic dsp_record_available, discard_record, capture_clear_q, bist_clear_q, bist_reinit_ack_q;
    logic qualify_reinit, record_start_io, record_start_ready_io, frame_done_io;
    logic unused_read_fault;
    logic capture_fifo_full, capture_metadata_clear;
    logic fifo_pin_status_reset_n, first_payload_seen_io_q, last_payload_seen_io_q;
    logic pin_fifo_full, pin_fifo_read_done;
    logic [3:0] gpio_oe_core;            // DSP 方向选择，隔离由本层应用
    logic por_ana_event_core;
    (* ASYNC_REG = "TRUE" *) logic [1:0] csr_fifo_meta_q, csr_fifo_sync_q;
    logic [7:0] data_cfg_a_core, data_cfg_b_core, data_cfg_c_core, filter_cfg_core;
    logic [7:0] data_cfg_a_io, data_cfg_b_io, data_cfg_c_io, filter_cfg_io;
    logic cfg_valid_io, cfg_pending_io, cfg_pause_io_q, cfg_ready_core;
    logic unused_cfg_applied_io, stream_enabled_core, stream_flush_core, stream_word_valid_core;
    logic stream_epoch_hold_n, stream_reset_io_n;
    logic [19:0] stream_word_core, stream_word_io;
    logic [31:0] stream_tag_core, stream_tag_io;
    logic stream_valid_io, unused_stream_ready;
    logic stream_idle_io, stream_config_ok_io, stream_cnv_rejected_io;
    logic stream_train_io, stream_enable_io, stream_mode_core_ready_io;
    logic selected_io_permission, fifo_record_allow_io;
    logic result_spi_selected_async, result_spi_selected_core;
    logic command_core_launch, command_core_reject, spi_manual_reject_core_q;
    logic [1:0] result_mode_io;                     // 权威配置从同一个跨域快照解码
    (* ASYNC_REG = "TRUE" *) logic [1:0] stream_enabled_io_q, stream_idle_core_q, stream_train_core_q;

    always_ff @(posedge clk_core_i or negedge cs_n) begin
        if (!cs_n) config_idle_pipe_q <= '0;
        else config_idle_pipe_q <= {config_idle_pipe_q[2:0], 1'b1};
    end

    // [AON] 一个布防中的记录即便暂时没有总线活动也须拒绝掉电/软复位。
    // 失败截断仅清当前记录元数据，保留同一物理阵列和其它供电边界。
    ad4080_pwr_reset_sync u_cfg_permission (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_n && cfg_allow_aon && !record_locked && !bist_hold_core),
        .ready_no(cfg_allow_core)
    );
    // 0x00/0x02 在原配置 pad 仍有足够电源时进入同一 AON 所有者。普通
    // 配置以及 SDO 驱动仍使用 full 权限，因而 IOVDD 断开不能留活输出。
    ad4080_pwr_reset_sync u_cfg_control_permission (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_n && cfg_control_allow_aon), .ready_no(cfg_control_allow_core)
    );
    // 软件初始化复用原 SPI 接收和 SDO 许可。仅 0x11 可以在 AON 真正
    // 受理后、物理 pad 保持有效的窄窗口读取，不解锁一般寄存器。
    ad4080_pwr_reset_sync u_cfg_poll_permission (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_n && cfg_poll_allow_aon), .ready_no(cfg_poll_allow_core)
    );
    ad4080_pwr_reset_sync u_aon_capture (
        .clk_i(clk_aon_i), .hold_ni(fifo_reset_n && !capture_incomplete_core_o), .ready_no(aon_capture_clear)
    );
    ad4080_pwr_reset_sync u_aon_read (
        .clk_i(clk_aon_i), .hold_ni(fifo_reset_n && !record_locked && link_idle && stream_idle_core_q[1]),
        .ready_no(aon_read_unlocked)
    );
    // 原 SPI 末位可以早于 CS 关闭。只等待本事务退出，再在 AON 活动沿
    // 判捕获、读口及上游空闲，避免把同步释放较晚的 CS 误判为忙客户。
    ad4080_pwr_reset_sync u_aon_cfg_eval (
        .clk_i(clk_aon_i), .hold_ni(fifo_reset_n && config_idle_pipe_q[3]), .ready_no(aon_cfg_eval)
    );
    ad4080_pwr_reset_sync u_aon_idle (
        .clk_i(clk_aon_i),
        .hold_ni(fifo_reset_n && sample_source_idle_core_i && config_idle_pipe_q[3] && !write_req && !read_req),
        .ready_no(aon_clients_idle)
    );
    assign bist_ready_aon_o = services_bist_ready && aon_read_unlocked;
    assign record_available_core_o = dsp_record_available && !bist_contents_invalid_core_o &&
                                     !capture_clear_q && !discard_record;

    // [GPIO] FULL 在真实捕获完成时上升。进入读锁后持住此前阶段的 FULL，
    // SRAM 地址预取不改变管脚。首/末逻辑 TX 装载沿直接更新两位 I/O 状态；
    // 重新布防、显式清除或断电异步切掉旧状态，即使当时没有 I/O 时钟。
    ad4080_pwr_reset_sync u_pin_status_reset (
        .clk_i(clk_io_i),
        .hold_ni(fifo_reset_n && io_record_epoch_o && !capture_incomplete_core_o && !capture_metadata_clear),
        .ready_no(fifo_pin_status_reset_n)
    );
    always_ff @(posedge clk_io_i or negedge fifo_pin_status_reset_n) begin
        if (!fifo_pin_status_reset_n) begin
            first_payload_seen_io_q <= 1'b0;
            last_payload_seen_io_q <= 1'b0;
        end else begin
            if (first_payload_word_load_io_o) first_payload_seen_io_q <= 1'b1;
            if (last_payload_word_load_io_o) last_payload_seen_io_q <= 1'b1;
        end
    end
    assign pin_fifo_full = io_record_epoch_o && !capture_incomplete_core_o && !capture_metadata_clear &&
                           (capture_fifo_full || record_locked) && !first_payload_seen_io_q;
    assign pin_fifo_read_done = last_payload_seen_io_q;

    // 表11 IOVDD 不可用时切断 GPO。软初始化仅保留已核供电的 GPIO0/SDO
    // 状态只读回路，且必须有实际 SPI 发射资格，不能绕过其他 GPIO 的隔离。
    assign gpio_oe = io_reset_n ? gpio_oe_core :
                     (cfg_poll_allow_core && sdo_oe ? (gpio_oe_core & 4'b0001) : 4'b0000);

    // [CSR] SPI 在另一个时钟域看到的是稳定的双级取样。GPIO 视图用原始
    // 管脚状态，不能将这两个时钟的报告延迟加到物理输出沿。
    always_ff @(posedge clk_core_i or negedge fifo_reset_n) begin
        if (!fifo_reset_n) begin
            csr_fifo_meta_q <= '0;
            csr_fifo_sync_q <= '0;
        end else begin
            csr_fifo_meta_q <= {pin_fifo_full, pin_fifo_read_done};
            csr_fifo_sync_q <= csr_fifo_meta_q;
        end
    end

    // [CNV] 两个已译码沿的距离必须有实际时基才能报最短 tCONV 错误。
    // cnv_pulse 仅保留检测语义，转换数据 valid 来自原数值入口。
    generate
        if (CORE_CLOCK_HZ > 0) begin : g_cnv_interval
            logic unused_cnv_pulse;
            cnv_ctrl #(.P_USE_CNV_TICK(1'b1), .CORE_CLOCK_HZ(CORE_CLOCK_HZ)) u_cnv (
                .clk(clk_core_i), .rst_n(fifo_reset_n), .cnv_p(1'b0), .busy(1'b0),
                .cnv_tick_i(cnv_tick_core_i), .cnv_pulse(unused_cnv_pulse), .cnv_err(conversion_timing_error_core_o)
            );
        end else begin : g_cnv_unbound
            assign conversion_timing_error_core_o = 1'b0;
        end
    endgenerate

    // [退休] 无空闲许可的新读保持在读出链，不推进原 DSP 的游标。写优先
    // 是单口仲裁兜底，正常捕获与完成记录处在原状态机的互斥窗口。
    assign write_ready = store_ready && write_req;
    assign read_taken = read_req && read_ready;
    assign read_ready = store_ready && !write_req && record_locked;
    assign qualify_reinit = bist_reinit_request_core_i && bist_done_core_o &&
                            bist_contents_invalid_core_o && !bist_busy_core_o && link_idle;
    always_ff @(posedge clk_core_i or negedge fifo_reset_n) begin
        if (!fifo_reset_n) begin
            capture_clear_q <= 1'b0;
            capture_write_loss_core_o <= 1'b0;
            bist_clear_q <= 1'b0;
            bist_reinit_ack_q <= 1'b0;
        end else begin
            // 数值输出无回压。意外失去写口时不得跳过一个字还宣称完整。
            capture_clear_q <= write_req && !write_ready;
            if (write_req && !write_ready) capture_write_loss_core_o <= 1'b1;
            // 先注册 clear，避免 link_idle、地址资格、clear 组合绕回彼此。
            // 捕获 clear 被采样后才回报已重建，满足测试独占的退场约束。
            bist_clear_q <= qualify_reinit;
            bist_reinit_ack_q <= bist_clear_q;
        end
    end

    // =========================================================================
    // 2. 固定 DSP 原数值链及唯一物理存储
    // =========================================================================
    ad4080_top #(
        .IN_W(20), .FIFO_DEPTH(16384), .P_GAIN_MAP_VALID(GAIN_BOUND), .K_GAIN(GAIN_K), .SHIFT(GAIN_SHIFT),
        .P_MULTIBYTE_ORDER(SPI_MULTIBYTE_ORDER), .P_OFFSET_LSB_ADDR(OFFSET_LSB_ADDRESS),
        .P_SHORT_ADDR_BITS(7), .P_PRODUCT_ID_L(32'h00000050),
        .P_WRITE_SDO_DRIVE(SPI_WRITE_SDO_DRIVE), .P_CNV_TIMING_BOUND(P_CNV_TIMING_BOUND),
        .P_CNV_ERROR_BOUND(CORE_CLOCK_HZ > 0), .P_PHY_FIFO_STATUS_BOUND(1'b1),
        .P_POR_ANA_EVENT_ENABLE(1'b1), .P_CONTINUOUS_LVDS_BOUND(1'b1), .P_AON_CFG_BOUND(1'b1)
    ) u_dsp (
        .clk(clk_core_i), .rst_n(fifo_reset_n), .cs_n, .sclk, .sdi, .sdo, .sdo_oe,
        .restart_sync_i(1'b0), .stored_word_valid_o(), // 产品软复位仍由真实 SPI/AON 仲裁。
        .interface_ready(cfg_control_allow_core), .pwr_cfg_full_allow_i(cfg_allow_core),
        .pwr_cfg_poll_allow_i(cfg_poll_allow_core),
        .adc_idle(sample_source_idle_core_i && !record_locked && !bist_hold_core),
        .pwr_cmd_valid_o(pwr_cmd_valid), .pwr_cmd_op_o(pwr_cmd_op), .pwr_cmd_value_o(pwr_cmd_value),
        .pwr_cmd_ready_i(pwr_cmd_ready), .pwr_rsp_valid_i(pwr_rsp_valid), .pwr_rsp_accept_i(pwr_rsp_accept),
        .pwr_rsp_op_i(pwr_rsp_op), .pwr_mode_i(pwr_mode), .pwr_ldo_pd_i(pwr_ldo_pd),
        .valid_in(sample_valid_core_i && conversion_allow_core_o), .x(sample_signed_core_i),
        .filter_sync(filter_sync_core_i), .ext_event(ext_event_core_i), .arm(1'b0), .rd_en(read_taken),
        .gpio_i, .gpio_o, .gpio_oe(gpio_oe_core), .dout(), .dout_valid(), .status(status_o),
        .cnv_tick_i(cnv_tick_core_i), .conversion_timing_error_i(conversion_timing_error_core_o),
        .cnv_data_timing_error_o(cnv_data_timing_error_core_o),
        .por_ana_event_i(por_ana_event_core),
        .phy_fifo_full_i(pin_fifo_full), .phy_fifo_read_done_i(pin_fifo_read_done),
        .csr_fifo_full_i(csr_fifo_sync_q[1]), .csr_fifo_read_done_i(csr_fifo_sync_q[0]),
        .data_config_ready_i(continuous_config_ready_core_o), .sample_tag_i(sample_tag_core_i),
        .data_intf_config_a_o(data_cfg_a_core), .data_intf_config_b_o(data_cfg_b_core),
        .data_intf_config_c_o(data_cfg_c_core), .filter_config_o(filter_cfg_core),
        .continuous_bypass_o(), .continuous_enable_o(stream_enabled_core), .continuous_flush_o(stream_flush_core),
        .continuous_word_valid_o(stream_word_valid_core),
        .continuous_word_o(stream_word_core), .continuous_word_tag_o(stream_tag_core),
        .correction_clip_low(), .correction_clip_high(),
        .cfg_reject(cfg_reject_o), .cfg_reject_reason(cfg_reject_reason_o), .processing_reject(processing_reject_o),
        .clear_record_i(capture_clear_q || discard_record || bist_clear_q),
        .store_write_ready_i(write_ready), .store_read_valid_i(store_valid), .store_read_data_i(store_data),
        .store_write_req_o(write_req), .store_write_addr_o(write_addr), .store_write_data_o(write_data),
        .record_read_offer_o(dsp_record_available), .record_read_addr_o(next_record_addr),
        .record_read_count_o(record_count_core), .capture_incomplete_o(capture_incomplete_core_o),
        .capture_fifo_full_o(capture_fifo_full), .capture_metadata_clear_o(capture_metadata_clear),
        .fifo_empty_o(fifo_empty_core_o), .fifo_full_o(fifo_full_core_o)
    );

    ad4080_peripheral_services #(
        .AON_MAX_CLK_HZ(AON_MAX_CLK_HZ), .OTP_IMAGE_WORDS(OTP_WORDS),
        .OTP_WORD_WIDTH(OTP_WORD_WIDTH), .OTP_TIMEOUT_CYCLES(OTP_TIMEOUT)
    ) u_services (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i,
        .por_d_ok_async_i, .vdd33_ok_async_i, .ref_ok_async_i, .iovdd_ok_async_i, .ana_ready_async_i,
        .clients_idle_aon_i(aon_clients_idle && aon_read_unlocked), .capture_incomplete_aon_i(!aon_capture_clear),
        .sw_reset_req_aon_i, .mode_req_valid_aon_i, .mode_req_aon_i, .wake_recover_done_aon_i,
        .cfg_hw_reset_no(), .core_reset_no(), .fifo_reset_no(fifo_reset_n), .io_reset_no(io_reset_n), .ana_reset_no(),
        .cfg_init_done_aon_o(), .cfg_control_write_allow_aon_o(cfg_control_allow_aon), .cfg_full_access_allow_aon_o(cfg_allow_aon),
        .cfg_poll_allow_aon_o(cfg_poll_allow_aon),
        .operating_mode_aon_o(), .wake_busy_aon_o(), .wake_recover_req_aon_o,
        .mode_req_accept_aon_o, .sw_reset_accept_aon_o, .isolate_io_o(), .iovdd_ldo_en_o, .ref_cmo_en_o(),
        .vdd11_ldo_pd_safe_aon_i, .vdd11_ldo_en_o,
        .pwr_cmd_valid_core_i(pwr_cmd_valid), .pwr_cmd_op_core_i(pwr_cmd_op), .pwr_cmd_value_core_i(pwr_cmd_value),
        .pwr_cmd_evaluate_aon_i(aon_cfg_eval),
        .pwr_cmd_ready_core_o(pwr_cmd_ready), .pwr_rsp_valid_core_o(pwr_rsp_valid), .pwr_rsp_accept_core_o(pwr_rsp_accept),
        .pwr_rsp_op_core_o(pwr_rsp_op), .pwr_mode_core_o(pwr_mode), .pwr_ldo_pd_core_o(pwr_ldo_pd),
        .por_ana_event_aon_o(), .por_ana_event_core_o(por_ana_event_core),
        .conversion_allow_aon_o(), .conversion_allow_core_o,
        .otp_power_good_aon_i, .otp_adapters_quiescent_aon_i, .otp_adapters_cancel_aon_o,
        .otp_req_valid_aon_o, .otp_req_ready_aon_i, .otp_req_addr_aon_o,
        .otp_rsp_valid_aon_i, .otp_rsp_ready_aon_o, .otp_rsp_data_aon_i, .otp_rsp_error_aon_i,
        .otp_verify_req_valid_aon_o, .otp_verify_req_ready_aon_i, .otp_verify_image_aon_o,
        .otp_verify_rsp_valid_aon_i, .otp_verify_rsp_ready_aon_o, .otp_verify_rsp_pass_aon_i,
        .otp_busy_aon_o(), .otp_error_code_aon_o(), .otp_shadow_valid_aon_o(), .otp_shadow_image_aon_o(),
        .bist_mode_req_aon_i(bist_request_aon_i && aon_read_unlocked), .bist_mode_ready_aon_o(services_bist_ready),
        .bist_mode_grant_aon_o(bist_grant_aon_o), .bist_func_hold_core_o(bist_hold_core),
        .bist_func_quiesced_core_i(sample_source_idle_core_i && !record_locked && link_idle),
        .bist_start_core_i, .bist_start_ready_core_o, .bist_checkerboard_core_i,
        .bist_func_reinit_done_core_i(bist_reinit_ack_q), .bist_contents_invalid_core_o,
        .bist_busy_core_o, .bist_done_core_o, .bist_fail_reason_core_o,
        .bist_fail_addr_valid_core_o(), .bist_fail_addr_core_o(),
        .bist_fail_expected_core_o(), .bist_fail_actual_core_o(),
        .result_req_valid_core_i(write_req || read_req), .result_req_ready_core_o(store_ready),
        .result_write_core_i(write_req), .result_addr_core_i(write_req ? write_addr : read_addr),
        .result_write_data_core_i(write_data), .result_read_valid_core_o(store_valid), .result_read_data_core_o(store_data),
        .io_word_valid_i(stream_train_io), .io_word_ready_o(selected_io_permission), .io_sample_i(20'b0),
        .fifo_record_allow_io_o(fifo_record_allow_io),
        .io_dual_lane_i(data_cfg_a_io[2]), .io_intf_check_i(stream_train_io),
        .io_edge_words_valid_o(), .io_da_fall_o(), .io_da_rise_o(),
        .io_db_fall_o(), .io_db_rise_o(), .io_slot_count_o()
    );

    // =========================================================================
    // 3. 捕获会话锁、读回与真正 I/O 记录
    // =========================================================================
    // [SPI结果] FIFO_FULL 在实际末写发布。真实 DCLK 接受头首位后，
    // 将本窗口资格同步回核心，用八个头周期为物理取数留出时间。
    // 未选记录可切备用模式；过早或停住的 DCS 不占用地址、不形成会话。
    ad4080_pwr_reset_sync u_spi_selection (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_n && result_spi_selected_async),
        .ready_no(result_spi_selected_core)
    );
    // LVDS 的外部记录命令保持原入口；SPI 档提交该重复命令时显式拒绝。
    assign command_core_launch = data_cfg_a_core[0]
        ? (cfg_ready_core && result_spi_selected_core && record_available_core_o) : record_command_core_i;
    always_ff @(posedge clk_core_i or negedge fifo_reset_n) begin
        if (!fifo_reset_n) spi_manual_reject_core_q <= 1'b0;
        else spi_manual_reject_core_q <= record_ready_core_o && data_cfg_a_core[0] && record_command_core_i;
    end
    assign record_rejected_core_o = command_core_reject || spi_manual_reject_core_q;
    always_comb begin
        // DATA_INTF_CONFIG_B 的 LVDS 延迟和时钟输出位只用于连续叶。
        // 采后 LVDS 必须为单 lane。采后 SPI 可选单线的 B 或四线 A/B/C/D。
        case (cfg_valid_io ? data_cfg_a_io : 8'b0)
            8'h40: result_mode_io = 2'd0;
            8'h41: result_mode_io = 2'd1;
            8'h45: result_mode_io = 2'd2;
            default: result_mode_io = 2'd3;
        endcase
    end

    ad4080_product_record_session u_session (
        .clk_core_i, .clk_io_i, .rst_core_ni(fifo_reset_n), .io_reset_ni(io_reset_n), .io_allow_i(io_record_epoch_o),
        .command_valid_core_i(command_core_launch), .command_ready_core_o(record_ready_core_o),
        // 图91在 CNV 自由运行时读出已冻结记录。只有冻结资格允许在上游
        // 仍送码时取锁；空记录命令仍在原静默窗口处理。
        .can_lock_core_i(config_idle_pipe_q[3] &&
                         (sample_source_idle_core_i || record_available_core_o) && !bist_hold_core &&
                         link_idle && !discard_record && !capture_clear_q),
        .captured_core_i(record_available_core_o), .captured_words_core_i(record_count_core), .read_taken_core_i(read_taken),
        .locked_core_o(record_locked), .reject_core_o(command_core_reject),
        .done_core_o(record_done_core_o), .failed_core_o(record_failed_core_o), .discard_core_o(discard_record),
        .start_valid_io_o(record_start_io), .start_ready_io_i(record_start_ready_io), .count_io_o(record_count_io),
        .done_io_i(frame_done_io), .error_io_i(frame_error_io_o)
    );

    ad4080_fifo_lvds_record_link #(.SPI_RESULT_BOUND(1'b1)) u_record (
        .clk_core_i, .clk_io_i, .fifo_reset_ni(fifo_reset_n), .io_reset_ni(io_reset_n), .io_session_allow_i(io_record_epoch_o),
        .record_start_valid_i(record_start_io), .record_start_ready_o(record_start_ready_io),
        .record_start_accept_o(crc_start_io_o), .record_count_i(record_count_io),
        .result_mode_io_i(result_mode_io), .result_dcs_ni, .result_dclk_i, .result_sdo_o, .result_sdo_oe_o,
        .result_capture_full_core_i(record_available_core_o || (record_locked && data_cfg_a_core[0])),
        .result_spi_selected_async_o(result_spi_selected_async),
        .result_spi_ready_io_o,
        .format_bound_i(result_mode_io == 2'd0 ? format_bound_io_i : result_mode_io != 2'd3),
        .crc_service_ready_i(crc_service_ready_io_i),
        .abort_i(abort_io_i), .crc_valid_i(crc_valid_io_i), .crc_ready_o(crc_ready_io_o),
        .crc_i(crc_io_i), .crc_error_i(crc_error_io_i), .crc_cancel_o(crc_cancel_io_o),
        .record_done_o(frame_done_io), .record_error_o(frame_error_io_o), .record_error_code_o(frame_error_code_io_o),
        .read_req_valid_core_i(record_locked && record_available_core_o), .read_req_ready_core_o(),
        .read_addr_core_i(next_record_addr), .sram_read_req_valid_core_o(read_req), .sram_read_req_ready_core_i(read_ready),
        .sram_read_addr_core_o(read_addr), .sram_read_rsp_valid_core_i(store_valid), .sram_read_rsp_data_core_i(store_data),
        .record_idle_core_o(link_idle), .read_fault_core_o(unused_read_fault),
        .beat_ready_i(beat_ready_io_i), .beat_valid_o(beat_valid_io_o), .beat_pair_o(beat_pair_io_o),
        .beat_first_o(beat_first_io_o), .beat_last_o(beat_last_io_o), .beat_kind_o(beat_kind_io_o),
        .first_payload_word_load_io_o, .last_payload_word_load_io_o
    );

    // =========================================================================
    // 4. 权威寄存器配置和真正带标签的连续数据
    // =========================================================================
    // [模式] 连续流使用原 DSP 判定的连续资格，采后记录由原记录路径持有。
    // 固定字选择原监督器的历史 REFIN 资格。普通存储读不会在有旧训练档
    // 时误借固定字许可，也不会拿当前 REFIN 给正常记录附加条件。
    assign stream_train_io = cfg_valid_io && stream_enabled_io_q[1] && data_cfg_a_io[4];
    // 外供待机可继续读已保存的结果 SPI，同时关闭连续 LVDS。两者共用真实
    // I/O 故障撤销，但使用不同模式许可，避免待机永远锁住旧捕获。
    assign io_record_epoch_o = !stream_train_io &&
        ((result_mode_io == 2'd1 || result_mode_io == 2'd2) ? fifo_record_allow_io : selected_io_permission);
    assign stream_enable_io = cfg_valid_io && stream_reset_io_n && stream_enabled_io_q[1] &&
                              !data_cfg_a_io[0] && selected_io_permission && !cfg_pause_io_q;
    assign continuous_cnv_allowed_io_o = stream_enable_io && stream_config_ok_io && stream_mode_core_ready_io &&
                                         !stream_train_io && !cfg_pending_io;
    assign continuous_cnv_rejected_io_o = stream_cnv_rejected_io || (cnv_tick_io_i && !continuous_cnv_allowed_io_o);
    assign continuous_config_ready_core_o = cfg_ready_core && sample_source_idle_core_i &&
                                           (stream_idle_core_q[1] || stream_train_core_q[1]);

    always_ff @(posedge clk_core_i or negedge fifo_reset_n) begin
        if (!fifo_reset_n) begin
            stream_idle_core_q <= '0;
            stream_train_core_q <= '0;
        end else begin
            stream_idle_core_q <= {stream_idle_core_q[0], stream_idle_io};
            stream_train_core_q <= {stream_train_core_q[0], stream_train_io};
        end
    end

    // [改档] 本轮已预约帧在旧档位下退场，期间停止新预约。训练连发时在
    // 最后一对的释放沿后暂停，至少一个 I/O 沿关闭叶子再应用新快照。
    always_ff @(posedge clk_io_i or negedge io_reset_n) begin
        if (!io_reset_n) begin
            stream_enabled_io_q <= '0;
            cfg_pause_io_q <= 1'b0;
        end else begin
            stream_enabled_io_q <= {stream_enabled_io_q[0], stream_enabled_core};
            if (!cfg_pending_io) cfg_pause_io_q <= 1'b0;
            else if (stream_idle_io || (stream_train_io && continuous_pair_last_o)) cfg_pause_io_q <= 1'b1;
        end
    end

    // 源 CSR 变更立即撤新预约许可，应用后在目的时钟同步放回。A/B/C 和
    // 滤波设置为一份稳定快照。应用资格独立，避免冷启动等自己的回执。
    ad4080_pwr_reset_sync u_continuous_admission (
        .clk_i(clk_io_i), .hold_ni(io_reset_n && cfg_ready_core), .ready_no(stream_mode_core_ready_io)
    );
    ad4080_lvds_config_cdc u_stream_config (
        .clk_core_i, .clk_io_i, .core_reset_ni(fifo_reset_n), .io_reset_ni(io_reset_n),
        .cfg_a_core_i(data_cfg_a_core), .cfg_b_core_i(data_cfg_b_core), .cfg_live_core_i(fifo_reset_n),
        .cfg_c_core_i(data_cfg_c_core), .cfg_filter_core_i(filter_cfg_core),
        .cfg_ready_core_o(cfg_ready_core), .io_idle_i(stream_idle_io && cfg_pause_io_q), .io_apply_allow_i(io_reset_n),
        .cfg_a_io_o(data_cfg_a_io), .cfg_b_io_o(data_cfg_b_io), .cfg_valid_io_o(cfg_valid_io),
        .cfg_c_io_o(data_cfg_c_io), .cfg_filter_io_o(filter_cfg_io),
        .cfg_pending_io_o(cfg_pending_io), .cfg_applied_io_o(unused_cfg_applied_io)
    );

    // 控制量本身由宏消费，默认幅值编码 010 对应无效配置时的安全请求。
    // 输出来自已经生效的同组配置，避免半字节换档带动 IO 偏置。
    assign lvds_rx_double_io_o = cfg_valid_io && data_cfg_c_io[7];
    // 表49只在 LVDS 接口生效；SPI 档保留 CSR 原值，切回 LVDS 时恢复。
    assign lvds_cnv_input_select_io_o = cfg_valid_io && !data_cfg_a_io[0] && data_cfg_b_io[0];
    always_comb begin
        case (cfg_valid_io ? data_cfg_c_io[6:4] : 3'b000)
            3'b001, 3'b010, 3'b100: lvds_vod_control_io_o = data_cfg_c_io[6:4];
            default: lvds_vod_control_io_o = 3'b010;
        endcase
    end

    // SYNC 或重新构型同时清 DSP→IO 两槽和在发帧/重复字，即使某域停钟。
    // 共同异步断言，在各域的复位同步器放行后才有新的装载。普通 A/B 改
    // 档仍用上方显式排完旧帧后切换的快照握手。
    assign stream_epoch_hold_n = fifo_reset_n && io_reset_n && stream_enabled_core && !stream_flush_core;
    ad4080_pwr_reset_sync u_stream_epoch (
        .clk_i(clk_io_i), .hold_ni(stream_epoch_hold_n), .ready_no(stream_reset_io_n)
    );
    ad4080_continuous_result_cdc #(.TAG_WIDTH(32)) u_stream_cdc (
        .clk_core_i, .clk_io_i, .reset_ni(stream_epoch_hold_n && cfg_ready_core),
        .valid_core_i(stream_word_valid_core && !data_cfg_a_core[4] && !data_cfg_a_core[0]),
        .tag_core_i(stream_tag_core), .word_core_i(stream_word_core), .ready_core_o(unused_stream_ready),
        .drop_core_o(continuous_result_drop_core_o), .valid_io_o(stream_valid_io),
        .tag_io_o(stream_tag_io), .word_io_o(stream_word_io)
    );
    ad4080_continuous_lvds #(.TAG_WIDTH(32)) u_stream (
        .clk_dclk_i(clk_io_i), .rst_ni(stream_reset_io_n), .enable_i(stream_enable_io),
        .filter_mode_i(filter_cfg_io[1:0] != 0), .clear_i(1'b0),
        .config_a_i(data_cfg_a_io), .config_b_i(data_cfg_b_io), .config_ok_o(stream_config_ok_io), .idle_o(stream_idle_io),
        .cnv_event_i(cnv_tick_io_i && continuous_cnv_allowed_io_o), .cnv_tag_i(cnv_tag_io_i),
        .result_valid_i(stream_valid_io), .result_tag_i(stream_tag_io), .result_i(stream_word_io),
        .cnv_rejected_o(stream_cnv_rejected_io), .result_rejected_o(continuous_result_rejected_io_o),
        .frame_missing_o(continuous_frame_missing_io_o), .frame_missing_tag_o(continuous_frame_missing_tag_o),
        .pair_valid_o(continuous_pair_valid_o), .pair_first_o(continuous_pair_first_o), .pair_last_o(continuous_pair_last_o),
        .pair_tag_o(continuous_pair_tag_o), .da_fall_o(continuous_da_fall_o), .da_rise_o(continuous_da_rise_o),
        .pair_source_tag_o(continuous_pair_source_tag_o), .pair_repeat_o(continuous_pair_repeat_o),
        .db_fall_o(continuous_db_fall_o), .db_rise_o(continuous_db_rise_o), .dco_clk_o(continuous_dco_clk_o),
        .dco_oe_o(continuous_dco_oe_o), .da_oe_o(continuous_da_oe_o), .db_oe_o(continuous_db_oe_o)
    );
endmodule

`default_nettype wire
