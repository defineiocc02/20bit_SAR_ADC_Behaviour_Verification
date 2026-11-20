`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_peripheral_product.sv
// 模块名     : ad4080_peripheral_product
// 版本       : v1.0
// 功能       : 复用固定 DSP、记录控制和单口四行 SRAM，交付采后单 lane 记录。
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
    parameter bit COMP_BOUND = 1'b0,             // 关闭时原寄存器拒绝补偿选择
    parameter int COMP_C0 = 0,
    parameter int COMP_C1 = 0,
    parameter int COMP_C2 = 0,
    parameter int SPI_MULTIBYTE_ORDER = -1,
    parameter int OFFSET_LSB_ADDRESS = -1,
    parameter int SPI_WRITE_SDO_DRIVE = 1, // Rev. B p.35，四线写数据相无 CRC 返回时驱动 0
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
    output logic frame_error_io_o,
    output logic [2:0] frame_error_code_io_o,

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
    logic aon_capture_clear, aon_clients_idle, aon_read_unlocked;
    logic services_bist_ready, record_locked, link_idle;
    logic [3:0] config_idle_pipe_q;  // 保证原 SPI 末沿同步链退出后才冻结记录
    logic write_req, write_ready, read_req, read_ready, read_taken, store_ready, store_valid;
    logic [13:0] write_addr, read_addr, next_record_addr;
    logic [19:0] write_data, store_data;
    logic [14:0] record_count_core, record_count_io;
    logic dsp_record_available, discard_record, capture_clear_q, bist_clear_q, bist_reinit_ack_q;
    logic qualify_reinit, record_start_io, record_start_ready_io, frame_done_io;
    logic unused_read_fault;

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
    ad4080_pwr_reset_sync u_aon_capture (
        .clk_i(clk_aon_i), .hold_ni(fifo_reset_n && !capture_incomplete_core_o), .ready_no(aon_capture_clear)
    );
    ad4080_pwr_reset_sync u_aon_read (
        .clk_i(clk_aon_i), .hold_ni(fifo_reset_n && !record_locked && link_idle), .ready_no(aon_read_unlocked)
    );
    ad4080_pwr_reset_sync u_aon_idle (
        .clk_i(clk_aon_i),
        .hold_ni(fifo_reset_n && sample_source_idle_core_i && config_idle_pipe_q[3] && !write_req && !read_req),
        .ready_no(aon_clients_idle)
    );
    assign bist_ready_aon_o = services_bist_ready && aon_read_unlocked;
    assign record_available_core_o = dsp_record_available && !bist_contents_invalid_core_o &&
                                     !capture_clear_q && !discard_record;

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
        .P_COMP_COEFF_VALID(COMP_BOUND), .C0(COMP_C0), .C1(COMP_C1), .C2(COMP_C2),
        .P_MULTIBYTE_ORDER(SPI_MULTIBYTE_ORDER), .P_OFFSET_LSB_ADDR(OFFSET_LSB_ADDRESS),
        .P_WRITE_SDO_DRIVE(SPI_WRITE_SDO_DRIVE)
    ) u_dsp (
        .clk(clk_core_i), .rst_n(fifo_reset_n), .cs_n, .sclk, .sdi, .sdo, .sdo_oe,
        .interface_ready(cfg_allow_core), .adc_idle(sample_source_idle_core_i && !record_locked && !bist_hold_core),
        .valid_in(sample_valid_core_i && conversion_allow_core_o), .x(sample_signed_core_i),
        .filter_sync(filter_sync_core_i), .ext_event(ext_event_core_i), .arm(1'b0), .rd_en(read_taken),
        .gpio_i, .gpio_o, .gpio_oe, .dout(), .dout_valid(), .status(status_o),
        .correction_clip_low(), .correction_clip_high(),
        .cfg_reject(cfg_reject_o), .cfg_reject_reason(cfg_reject_reason_o), .processing_reject(processing_reject_o),
        .clear_record_i(capture_clear_q || discard_record || bist_clear_q),
        .store_write_ready_i(write_ready), .store_read_valid_i(store_valid), .store_read_data_i(store_data),
        .store_write_req_o(write_req), .store_write_addr_o(write_addr), .store_write_data_o(write_data),
        .record_read_offer_o(dsp_record_available), .record_read_addr_o(next_record_addr),
        .record_read_count_o(record_count_core), .capture_incomplete_o(capture_incomplete_core_o),
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
        .cfg_init_done_aon_o(), .cfg_control_write_allow_aon_o(), .cfg_full_access_allow_aon_o(cfg_allow_aon),
        .operating_mode_aon_o(), .wake_busy_aon_o(), .wake_recover_req_aon_o,
        .mode_req_accept_aon_o, .sw_reset_accept_aon_o, .isolate_io_o(), .iovdd_ldo_en_o(), .ref_cmo_en_o(),
        .por_ana_event_aon_o(), .conversion_allow_aon_o(), .conversion_allow_core_o,
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
        .io_word_valid_i(1'b0), .io_word_ready_o(io_record_epoch_o), .io_sample_i(20'b0),
        .io_dual_lane_i(1'b0), .io_intf_check_i(1'b0), .io_edge_words_valid_o(), .io_da_fall_o(), .io_da_rise_o(),
        .io_db_fall_o(), .io_db_rise_o(), .io_slot_count_o()
    );

    // =========================================================================
    // 3. 捕获会话锁、读回与真正 I/O 记录
    // =========================================================================
    ad4080_product_record_session u_session (
        .clk_core_i, .clk_io_i, .rst_core_ni(fifo_reset_n), .io_reset_ni(io_reset_n), .io_allow_i(io_record_epoch_o),
        .command_valid_core_i(record_command_core_i), .command_ready_core_o(record_ready_core_o),
        .can_lock_core_i(config_idle_pipe_q[3] && sample_source_idle_core_i && !bist_hold_core &&
                         link_idle && !discard_record && !capture_clear_q),
        .captured_core_i(record_available_core_o), .captured_words_core_i(record_count_core), .read_taken_core_i(read_taken),
        .locked_core_o(record_locked), .reject_core_o(record_rejected_core_o),
        .done_core_o(record_done_core_o), .failed_core_o(record_failed_core_o), .discard_core_o(discard_record),
        .start_valid_io_o(record_start_io), .start_ready_io_i(record_start_ready_io), .count_io_o(record_count_io),
        .done_io_i(frame_done_io), .error_io_i(frame_error_io_o)
    );

    ad4080_fifo_lvds_record_link u_record (
        .clk_core_i, .clk_io_i, .fifo_reset_ni(fifo_reset_n), .io_reset_ni(io_reset_n), .io_session_allow_i(io_record_epoch_o),
        .record_start_valid_i(record_start_io), .record_start_ready_o(record_start_ready_io),
        .record_start_accept_o(crc_start_io_o), .record_count_i(record_count_io),
        .format_bound_i(format_bound_io_i), .crc_service_ready_i(crc_service_ready_io_i),
        .abort_i(abort_io_i), .crc_valid_i(crc_valid_io_i), .crc_ready_o(crc_ready_io_o),
        .crc_i(crc_io_i), .crc_error_i(crc_error_io_i), .crc_cancel_o(crc_cancel_io_o),
        .record_done_o(frame_done_io), .record_error_o(frame_error_io_o), .record_error_code_o(frame_error_code_io_o),
        .read_req_valid_core_i(record_locked && record_available_core_o), .read_req_ready_core_o(),
        .read_addr_core_i(next_record_addr), .sram_read_req_valid_core_o(read_req), .sram_read_req_ready_core_i(read_ready),
        .sram_read_addr_core_o(read_addr), .sram_read_rsp_valid_core_i(store_valid), .sram_read_rsp_data_core_i(store_data),
        .record_idle_core_o(link_idle), .read_fault_core_o(unused_read_fault),
        .beat_ready_i(beat_ready_io_i), .beat_valid_o(beat_valid_io_o), .beat_pair_o(beat_pair_io_o),
        .beat_first_o(beat_first_io_o), .beat_last_o(beat_last_io_o), .beat_kind_o(beat_kind_io_o)
    );
endmodule

`default_nettype wire
