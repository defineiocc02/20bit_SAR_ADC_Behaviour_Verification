// =============================================================================
// 文件名     : ad4080_top.sv
// 模块名     : ad4080_top
// 版本       : v1.0
// 功能       : 复用一条校正、滤波、阈值和捕获链，支持显式绑定的 CNV 结果拍点。
// 设计说明   :
//   * 厂家配置语义与工程拍点错误分开。即使选中滤波器已产生数值 valid，
//     定相路径仍须核实其为下一公布沿所对应的一组实际输入。
//   * 保留原维护者和版本来源，贡献由 Git 记录。CNV 发生器、Pad 和结果 PHY 由外部绑定。
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.27,35–42,56–71,73–94；排除项见 DSP_G1_ACTIVE_2。
// 时钟域: clk；SPI/GPIO 在入口同步，其余控制状态由调用方同步提供。
// 复位: rst_n 低有效、异步置位、由外部同步释放；软件复位保留已完成的 FIFO 记录。
// 延迟: 由子模块决定；工程配置拒绝事件额外经过一个寄存沿。
// 吞吐: 每个 clk 最多一个数值输入；捕获写入与完成记录读取互斥。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: spi_slave、ad4080_regs、ad4080_dsp_chain、event_detect、fifo_capture、gpio_ctrl。
// 边界: 引脚实际建立与传播由接入方确认。未知绑定按参数关闭对应功能。
// 变更记录   : 新增旁路/SINC1 的真实 CNV 公布拍，分离 GPIO 与 CSR 的保存状态。
// =============================================================================
`timescale 1ns/1ps
`default_nettype none
module ad4080_top #(
    parameter int IN_W=20,
    parameter int FIFO_DEPTH=16384,
    parameter bit P_GAIN_MAP_VALID=1'b0,
    parameter int K_GAIN=-1,
    parameter int SHIFT=-1,
    parameter bit P_GAIN_CODE0_SUPPORTED=1'b0,
    parameter bit P_COMP_COEFF_VALID=1'b0,
    parameter bit P_CONTINUOUS_LVDS_BOUND=1'b0, // 产品接入真实移位和配置 CDC 时启用。
    parameter int C0=32'h40000000,
    parameter int C1=32'h40000000,
    parameter int C2=32'h40000000,
    parameter int P_MULTIBYTE_ORDER=-1,
    parameter int P_OFFSET_LSB_ADDR=-1,
    parameter int P_PRODUCT_ID_L=-1,
    parameter int P_SHORT_ADDR_BITS=-1,
    parameter int P_WRITE_SDO_DRIVE=-1,
    parameter bit P_CNV_TIMING_BOUND=1'b0,       // 绑定真实核心域 CNV 与对应完成码。
    parameter bit P_PHY_FIFO_STATUS_BOUND=1'b0,  // 物理接口和 CSR 的记录状态由集成方分别提供。
    parameter bit P_CNV_ERROR_BOUND=1'b0,        // 绑定明确核心频率的同域转换间隔错误。
    parameter bit P_POR_ANA_EVENT_ENABLE=1'b0  // 只在产品连接可信参考故障交付后开启。
) (
    // --- 核心、SPI 与并行转换数据 ---
    input logic clk, rst_n,             // 核心域，外部保证异步复位同步解除。
    input logic cs_n, sclk, sdi,        // SPI 自行同步，引脚建立须于实际配置速率验收。
    output logic sdo, sdo_oe,           // 区分配置位和可驱动资格。
    input logic interface_ready, adc_idle,  // SPI 实体允许与已完整排空分开给出。
    input logic valid_in,               // 定相时须与规定 CNV 同沿，旧路径按核心沿收码。
    input logic [IN_W-1:0] x,           // 有效采样的二补码，不从 tag 或 GPIO 推导数值。
    input logic filter_sync, ext_event, arm, rd_en,  // 同域同步/触发/布防/读，CSR 另有本地来源。
    input logic [3:0] gpio_i,           // GPIO 输入同步后再执行本地同步/触发选择。
    output logic [3:0] gpio_o, gpio_oe, // 输出路径直接驱动选择器，保留源事件相位。
    output logic [IN_W-1:0] dout,       // 直接读取保持最新公布字，完成记录可使用原 FIFO 读口。
    output logic dout_valid,            // 数值公布或明确接受的读取，含义随 FIFO 模式切换。
    output logic [7:0] status,
    output logic correction_clip_low, correction_clip_high,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason,
    // --- 器件时基及两套同源 FIFO 状态视图 ---
    input logic cnv_tick_i,                      // 经与 GPIO 匹配的同步采样/边沿检测后同域单拍。
    input logic conversion_timing_error_i,       // 同域短 CNV 间隔事件，单独进入器件 sticky。
    input logic phy_fifo_full_i, phy_fifo_read_done_i, // GPIO 直接用实际接口事件，不能再打核心同步拍。
    input logic csr_fifo_full_i, csr_fifo_read_done_i, // 0x14 读取所需的核心域同步视图。
    output logic cnv_data_timing_error_o,        // 数据缺失/错位的工程诊断，与 0x14[2] 区分。
    // --- 连续读出的已校正样本与来源标签 ---
    input  logic data_config_ready_i,            // 来自实际 I/O 配置交接；未受理的下一次写会被拒绝。
    input  logic [31:0] sample_tag_i,            // 与进入校正器的转换结果同拍。
    output logic [7:0] data_intf_config_a_o, data_intf_config_b_o,
    output logic continuous_bypass_o, continuous_word_valid_o,
    output logic [IN_W-1:0] continuous_word_o,
    output logic [31:0] continuous_word_tag_o,
    input  logic por_ana_event_i = 1'b0,         // 核心域事件，与 0x14[3] 同沿消费。
    output logic processing_reject
);
    logic [15:0] addr,wdata,rdata,status_long;
    logic [7:0] status_short,unused_interface_status;
    logic [1:0] write_bytes,entity_bytes;
    logic we,address_valid,read_only,access_supported;
    logic [3:0] access_reject_reason;
    logic crc_error,clock_error,partial_error,address_error,readonly_error,notready_error;
    logic crc_en,send_status,single_inst,short_instr,addr_asc,strict_access;
    logic spi_reject,bank_reject,capture_reject;
    logic [3:0] spi_reason,bank_reason,capture_reason;
    logic signed [11:0] offset_code;
    logic [9:0] gain_code;
    logic [1:0] filter_sel,fifo_mode;
    logic [3:0] dec_sel;
    logic [14:0] watermark,wm_wb_data;
    logic fifo_arm,fifo_clear_request,wm_wb_valid,fsync_pulse,unused_sw_reset_pulse;
    logic [10:0] event_hyst;
    logic [11:0] event_hi,event_lo;
    logic event_en,hi_route,lo_route,hi_clr,lo_clr;
    logic hi_dtct,lo_dtct,hi_flag,lo_flag,hi_pulse,lo_pulse,event_alert_n,cnv_error_alert;
    logic [3:0] gpio_dir,gpio_data,gpio_levels;
    logic [3:0][3:0] gpio_sel;
    (* ASYNC_REG="TRUE" *) logic [3:0] gpio_meta,gpio_sync;
    logic gpio_fsync,gpio_event;
    logic [IN_W-1:0] chain_data,fifo_data;
    logic chain_valid,fifo_valid,clip_low,clip_high;
    logic captured,read_done,full_flag,capture_active,capture_incomplete;
    logic unused_fifo_full,unused_fifo_empty,unused_fifo_overflow,unused_fifo_underflow;
    logic chain_input_valid, timing_chain_clear, timing_abort_capture, filter_rdy_n;
    logic event_sample_valid, fifo_sample_valid, direct_sample_valid, internal_fifo_event;
    logic [IN_W-1:0] event_sample, fifo_sample, direct_sample;

    wire pin_sync = filter_sync | gpio_fsync;
    wire fsync_all = pin_sync | fsync_pulse | timing_chain_clear;
    wire fifo_clear = fifo_clear_request | timing_abort_capture;
    wire threshold_pulse = (hi_pulse & hi_route) | (lo_pulse & lo_route);
    wire fifo_event=event_en ? internal_fifo_event : (ext_event|gpio_event);
    wire capture_io_active=arm|fifo_arm|fifo_clear|rd_en|fifo_valid;
    wire csr_full_view = P_PHY_FIFO_STATUS_BOUND ? csr_fifo_full_i : full_flag;
    wire csr_done_view = P_PHY_FIFO_STATUS_BOUND ? csr_fifo_read_done_i : read_done;
    wire gpio_full_view = P_PHY_FIFO_STATUS_BOUND ? phy_fifo_full_i : full_flag;
    wire gpio_done_view = P_PHY_FIFO_STATUS_BOUND ? phy_fifo_read_done_i : read_done;

    // =========================================================================
    // 1. 可选的真实 CNV 结果边界
    // =========================================================================
    // [时基] Fig.99 的 E1 由真实 SYNC 和匹配同步后的 tick 同沿确认。滤波从 E3 收原码，
    //        旁路在首 CNV 建批，随后收上一 CNV 的完成码。发送者负责 core setup/hold。
    // [归属] 一个已接齐的原码块只等待一个真实数值结果，下一 CNV 消费数值。
    //        SINC5 以第 5 组为完整历史，真实字再延 3 CNV 对齐 p.69 的公布拍。
    //        公布后下一 CNV 给原 FIFO。晚到和重复的数值不能冒作本批 RDY。
    // [退场] 停 CNV 时保留相位，不新增比较、公布或写。允许在完整块边界停止供码，
    //        已完整的末字仍按后续两个 CNV 公布及写入。新采集需要新批。
    generate if (P_CNV_TIMING_BOUND) begin : g_cnv
        logic accepting_q, bypass_start_q, skip_edge_q, seen_raw_q, drained_q;
        logic sync_prev_q, sync_tick_seen_q, fault_q;
        logic [10:0] raw_left_q;                  // 当前块尚需的真实原码数。
        logic due_q, pending_num_q, store_pending_q, direct_valid_q, rdy_n_q, stored_event_q;
        logic [IN_W-1:0] pending_word_q, released_word_q;
        // SINC5 数据通过一次固定的小暂存。含空位的 CNV 延迟不改变真实字顺序。
        logic [2:0] sinc5_warm_groups_q, sinc5_delay_valid_q;
        logic [IN_W-1:0] sinc5_delay_word_q [0:2];

        wire sinc5_on = filter_sel == 2'd2;
        wire [3:0] max_dec = sinc5_on ? 4'd7 : 4'd9;
        wire [3:0] capped_dec = dec_sel > max_dec ? max_dec : dec_sel;
        wire [10:0] group_size = (filter_sel != 2'd0) ? (11'd2 << capped_dec) : 11'd1;
        wire fifo_new_arm = (arm | fifo_arm) && !capture_active && fifo_mode != 0;
        wire restart = fsync_pulse || fifo_clear_request || fifo_new_arm;
        wire raw_slot = cnv_tick_i && accepting_q && !skip_edge_q;
        wire drained_now = raw_slot && !valid_in && seen_raw_q && raw_left_q == group_size;
        wire raw_fault = (valid_in && !raw_slot) || (raw_slot && !valid_in && !drained_now);
        wire numeric_fault = (chain_valid && (!due_q || pending_num_q)) ||
                             (cnv_tick_i && due_q && !(pending_num_q || chain_valid));
        wire physical_fault = P_CNV_ERROR_BOUND && conversion_timing_error_i;
        wire fault_now = !pin_sync && !restart && !fault_q && (raw_fault || numeric_fault || physical_fault);
        wire run_edge = cnv_tick_i && !pin_sync && !restart && !fault_q && !fault_now;
        wire take_number = run_edge && due_q;
        wire publish = run_edge && (sinc5_on ? sinc5_delay_valid_q[2] : due_q);
        wire [IN_W-1:0] cutoff_word = pending_num_q ? pending_word_q : chain_data;
        wire last_word_drained = drained_q && !due_q && !store_pending_q && !(|sinc5_delay_valid_q);
        wire raw_take = raw_slot && valid_in && !pin_sync && !restart && !fault_q && !fault_now;
        wire interrupted_capture = (pin_sync && !sync_prev_q && seen_raw_q) ||
                                   (pin_sync && cnv_tick_i && sync_tick_seen_q) ||
                                   fault_q || fault_now || last_word_drained;

        assign chain_input_valid = raw_take;
        assign timing_chain_clear = restart || fault_q || fault_now;
        assign timing_abort_capture = capture_incomplete && interrupted_capture;
        assign cnv_data_timing_error_o = fault_q;
        assign event_sample_valid = publish;
        assign event_sample = sinc5_on ? sinc5_delay_word_q[2] : cutoff_word;
        assign fifo_sample_valid = run_edge && store_pending_q;
        assign fifo_sample = released_word_q;
        // 比较器脉冲由公布沿发出。等同字下一 CNV 的写沿再告诉原 FIFO，
        // 保持模式 10 排除触发字，模式 11 将同沿的触发字记在前史。
        assign internal_fifo_event = fifo_sample_valid && (stored_event_q || threshold_pulse);
        assign filter_rdy_n = rdy_n_q;
        assign direct_sample_valid = direct_valid_q;
        assign direct_sample = released_word_q;

        // ---------------------------------------------------------------------
        // [优先级] 物理 SYNC 优先于同沿布防/CSR 重启，从而 E1 不会被误丢。
        //          SYNC 若还跨到 E2 则报失配。故障取消尚未交付的结果与部分记录。
        // [末尾] 数值脉冲已提前到时先暂存；恰在公布沿前建立的数值可直接消费。
        //        一次公布不取新 raw 做替代。RDY 到下次预计结果的前一 CNV 回高。
        // ---------------------------------------------------------------------
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                accepting_q <= 1'b0;
                bypass_start_q <= 1'b1;
                skip_edge_q <= 1'b0;
                seen_raw_q <= 1'b0;
                drained_q <= 1'b0;
                sync_prev_q <= 1'b0;
                sync_tick_seen_q <= 1'b0;
                fault_q <= 1'b0;
                raw_left_q <= 11'd1;
                due_q <= 1'b0;
                pending_num_q <= 1'b0;
                store_pending_q <= 1'b0;
                direct_valid_q <= 1'b0;
                rdy_n_q <= 1'b1;
                stored_event_q <= 1'b0;
                sinc5_warm_groups_q <= '0;
                sinc5_delay_valid_q <= '0;
                for (int stage=0; stage<3; stage++) sinc5_delay_word_q[stage] <= '0;
                pending_word_q <= '0;
                released_word_q <= '0;
            end else begin
                sync_prev_q <= pin_sync;
                if (!pin_sync) sync_tick_seen_q <= 1'b0;
                direct_valid_q <= publish;
                if (pin_sync || restart || fault_now) begin
                    due_q <= 1'b0;
                    pending_num_q <= 1'b0;
                    store_pending_q <= 1'b0;
                    rdy_n_q <= 1'b1;
                    stored_event_q <= 1'b0;
                    sinc5_warm_groups_q <= '0;
                    sinc5_delay_valid_q <= '0;
                    if (!pin_sync || !sync_prev_q || cnv_tick_i) begin
                        seen_raw_q <= 1'b0;
                        drained_q <= 1'b0;
                        raw_left_q <= group_size;
                        accepting_q <= pin_sync && cnv_tick_i && !sync_tick_seen_q;
                        skip_edge_q <= filter_sel != 2'd0;
                    end
                    if (pin_sync && cnv_tick_i) begin
                        sync_tick_seen_q <= 1'b1;
                        bypass_start_q <= 1'b0;
                        fault_q <= sync_tick_seen_q;
                    end else if (!pin_sync) begin
                        bypass_start_q <= !fault_now && (filter_sel == 2'd0);
                        fault_q <= fault_now;
                    end
                end else if (run_edge) begin
                    store_pending_q <= publish;
                    stored_event_q <= 1'b0;
                    due_q <= raw_take && raw_left_q == 11'd1;
                    pending_num_q <= 1'b0;
                    if (sinc5_on) begin
                        sinc5_delay_valid_q <= {sinc5_delay_valid_q[1:0], take_number && sinc5_warm_groups_q == 3'd4};
                        sinc5_delay_word_q[2] <= sinc5_delay_word_q[1];
                        sinc5_delay_word_q[1] <= sinc5_delay_word_q[0];
                        if (take_number) begin
                            sinc5_delay_word_q[0] <= cutoff_word;
                            if (sinc5_warm_groups_q != 3'd4) sinc5_warm_groups_q <= sinc5_warm_groups_q + 1'b1;
                        end
                    end
                    if (bypass_start_q && filter_sel == 2'd0) begin
                        accepting_q <= 1'b1;
                        skip_edge_q <= 1'b0;
                        bypass_start_q <= 1'b0;
                    end else if (skip_edge_q && accepting_q) begin
                        skip_edge_q <= 1'b0;
                    end
                    if (raw_take) begin
                        seen_raw_q <= 1'b1;
                        raw_left_q <= (raw_left_q == 11'd1) ? group_size : raw_left_q - 11'd1;
                    end else if (drained_now) begin
                        accepting_q <= 1'b0;
                        drained_q <= 1'b1;
                    end
                    if (publish) begin
                        released_word_q <= event_sample;
                        rdy_n_q <= (filter_sel == 2'd0);
                    end else if (sinc5_on ? (sinc5_delay_valid_q[1] || (!accepting_q && !(|sinc5_delay_valid_q))) :
                                 (!accepting_q || (raw_take && raw_left_q == 11'd1))) begin
                        rdy_n_q <= 1'b1;
                    end
                end else begin
                    if (chain_valid && !fault_q) begin
                        pending_num_q <= 1'b1;
                        pending_word_q <= chain_data;
                    end
                    if (threshold_pulse && store_pending_q) stored_event_q <= 1'b1;
                end
            end
        end
    end else begin : g_untimed
        // 旧的并行数值用例无需新增 pin。由常量参数展开，未连接输入不参与运算。
        assign chain_input_valid = valid_in;
        assign timing_chain_clear = 1'b0;
        assign timing_abort_capture = 1'b0;
        assign cnv_data_timing_error_o = 1'b0;
        assign event_sample_valid = chain_valid;
        assign event_sample = chain_data;
        assign fifo_sample_valid = chain_valid;
        assign fifo_sample = chain_data;
        assign direct_sample_valid = chain_valid;
        assign direct_sample = chain_data;
        assign filter_rdy_n = 1'b1;
        assign internal_fifo_event = threshold_pulse;
    end endgenerate

    // =========================================================================
    // 2. 寄存器、唯一数值链及状态输出
    // =========================================================================

    spi_slave #(.P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),.P_WRITE_SDO_DRIVE(P_WRITE_SDO_DRIVE)) u_spi (
        .clk(clk),.rst_n(rst_n),.cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),
        .interface_ready(interface_ready),.crc_en(crc_en),.send_status(send_status),
        .single_inst(single_inst),.short_instr(short_instr),.addr_asc(addr_asc),.strict_access(strict_access),
        .status_long(status_long),.status_short(status_short),.addr(addr),.wdata(wdata),
        .write_bytes(write_bytes),.we(we),.rdata(rdata),.entity_bytes(entity_bytes),
        .address_valid(address_valid),.read_only(read_only),.access_supported(access_supported),
        .access_reject_reason(access_reject_reason),.crc_err_pulse(crc_error),.clk_cnt_err_pulse(clock_error),
        .partial_err_pulse(partial_error),.addr_err_pulse(address_error),
        .readonly_err_pulse(readonly_error),.not_ready_err_pulse(notready_error),
        .cfg_reject(spi_reject),.cfg_reject_reason(spi_reason)
    );
    ad4080_regs #(.P_CONTINUOUS_LVDS_BOUND(P_CONTINUOUS_LVDS_BOUND),
        .P_MULTIBYTE_ORDER(P_MULTIBYTE_ORDER),.P_OFFSET_LSB_ADDR(P_OFFSET_LSB_ADDR),
        .P_PRODUCT_ID_L(P_PRODUCT_ID_L),.P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),
        .P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),.P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),
        .P_COMP_COEFF_VALID(P_COMP_COEFF_VALID), .P_CNV_TIMING_BOUND(P_CNV_TIMING_BOUND),
        .P_CNV_ERROR_BOUND(P_CNV_ERROR_BOUND), .P_POR_ANA_EVENT_ENABLE(P_POR_ANA_EVENT_ENABLE)) u_regs (
        .clk(clk),.rst_n(rst_n),.addr(addr),.wdata(wdata),.write_bytes(write_bytes),.we(we),
        .rdata(rdata),.entity_bytes(entity_bytes),.address_valid(address_valid),.read_only(read_only),
        .access_supported(access_supported),.access_reject_reason(access_reject_reason),
        .data_config_ready_i, .data_intf_config_a_o, .data_intf_config_b_o,
        .adc_idle(adc_idle),.gpio_levels(gpio_levels),.capture_active(capture_active),
        .capture_incomplete(capture_incomplete),.capture_io_active(capture_io_active),
        .st_fifo_full(csr_full_view),.st_fifo_read_done(csr_done_view),
        .st_hi_flag(hi_flag&hi_route),.st_lo_flag(lo_flag&lo_route),
        .conversion_timing_error_i,.cnv_error_alert_o(cnv_error_alert),.por_ana_event_i,
        .crc_err_pulse(crc_error),.clk_cnt_err_pulse(clock_error),.partial_err_pulse(partial_error),
        .addr_err_pulse(address_error),.readonly_err_pulse(readonly_error),.not_ready_err_pulse(notready_error),
        .hi_clr(hi_clr),.lo_clr(lo_clr),.offset_code(offset_code),.gain_code(gain_code),
        .filter_sel(filter_sel),.dec_sel(dec_sel),.fsync_pulse(fsync_pulse),
        .fifo_mode(fifo_mode),.fifo_watermark(watermark),.fifo_arm(fifo_arm),.fifo_clear(fifo_clear_request),
        .wm_wb_valid(wm_wb_valid),.wm_wb_data(wm_wb_data),.event_hyst(event_hyst),
        .event_hi(event_hi),.event_lo(event_lo),.event_en(event_en),.hi_route(hi_route),.lo_route(lo_route),
        .gpio_dir(gpio_dir),.gpio_reg(gpio_data),.gpio_sel(gpio_sel),
        .crc_en(crc_en),.send_status(send_status),.addr_asc(addr_asc),.single_inst(single_inst),
        .short_instr(short_instr),.strict_access(strict_access),.sw_reset_pulse(unused_sw_reset_pulse),
        .status(status),.interface_status(unused_interface_status),
        .status_long(status_long),.status_short(status_short),
        .cfg_reject(bank_reject),.cfg_reject_reason(bank_reason)
    );
    ad4080_dsp_chain #(.IN_W(IN_W),.P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),.K_GAIN(K_GAIN),.SHIFT(SHIFT),
        .P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),.P_COMP_COEFF_VALID(P_COMP_COEFF_VALID),
        .C0(C0),.C1(C1),.C2(C2)) u_chain (
        .clk(clk),.rst_n(rst_n),.valid_in(chain_input_valid),.x(x),.offset_code(offset_code),.gain_code(gain_code),
        .filter_sel(filter_sel),.dec_sel(dec_sel),.filter_sync(fsync_all),
        .y(chain_data),.valid_out(chain_valid),.clip_low(clip_low),.clip_high(clip_high),
        .processing_reject(processing_reject)
    );
    event_detect u_event (
        .clk(clk),.rst_n(rst_n),.enable(event_en),.hi_route(hi_route),.lo_route(lo_route),
        .clear_hi(hi_clr),.clear_lo(lo_clr),.sample_valid(event_sample_valid),.data_in(event_sample),
        .hi_thresh(event_hi),.lo_thresh(event_lo),.hysteresis(event_hyst),
        .hi_dtct(hi_dtct),.lo_dtct(lo_dtct),.hi_flag(hi_flag),.lo_flag(lo_flag),
        .hi_pulse(hi_pulse),.lo_pulse(lo_pulse),.alert_n(event_alert_n)
    );
    fifo_capture #(.DATA_W(IN_W),.DEPTH(FIFO_DEPTH)) u_capture (
        .clk(clk),.rst_n(rst_n),.clear_capture(fifo_clear),.arm(arm|fifo_arm),
        .event_in(fifo_event),.rd_en(rd_en),.mode(fifo_mode),.watermark(watermark),
        .valid_in(fifo_sample_valid),.data_in(fifo_sample),.rd_data(fifo_data),.rd_valid(fifo_valid),
        .full(unused_fifo_full),.empty(unused_fifo_empty),
        .overflow(unused_fifo_overflow),.underflow(unused_fifo_underflow),
        .read_done(read_done),.captured(captured),.full_flag(full_flag),
        .wm_wb_valid(wm_wb_valid),.wm_wb_data(wm_wb_data),
        .capture_active(capture_active),.capture_incomplete(capture_incomplete),
        .cfg_reject(capture_reject),.cfg_reject_reason(capture_reason)
    );
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin gpio_meta<='0; gpio_sync<='0; end
        else begin gpio_meta<=gpio_i; gpio_sync<=gpio_meta; end
    end
    gpio_ctrl #(.P_CNV_TIMING_BOUND(P_CNV_TIMING_BOUND)) u_gpio (
        .gpio_i(gpio_sync),.gpio_dir(gpio_dir),.gpio_reg(gpio_data),.gpio_sel(gpio_sel),
        .spi_sdo(sdo),.spi_sdo_oe(sdo_oe),.fifo_full(gpio_full_view),.fifo_read_done(gpio_done_view),
        .filter_rdy_n,.hi_dtct(hi_dtct),.lo_dtct(lo_dtct),.alert_n(event_alert_n && !cnv_error_alert),
        .gpio_o(gpio_o),.gpio_oe(gpio_oe),.filter_sync(gpio_fsync),.ext_event(gpio_event)
    );
    assign gpio_levels=(gpio_o&gpio_oe)|(gpio_sync&~gpio_oe);
    // 连续 LVDS 的时隙在 I/O 域选择。这里提交“数值实际完成”的原码结果，
    // 事件比较和 FIFO 的外部公布沿另由 CNV 时基决定，不能用后者延迟替代可发送时刻。
    assign continuous_bypass_o = P_CONTINUOUS_LVDS_BOUND && fifo_mode == 0 && filter_sel == 0 && !captured;
    assign continuous_word_o = chain_data;
    assign continuous_word_valid_o = continuous_bypass_o && chain_valid && !fsync_all;
    generate if (P_CONTINUOUS_LVDS_BOUND) begin : g_stream_tag
        logic [31:0] tag_s1, tag_s2;
        // 图96修正流水在受理沿后的第2个核心沿发布。标签与相同输入同沿采纳；
        // 空拍继续移动，valid 由真正的数值出口给出。复位或取消同时退休未交付标签。
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin tag_s1<='0; tag_s2<='0; continuous_word_tag_o<='0; end
            else if (fsync_all) begin tag_s1<='0; tag_s2<='0; continuous_word_tag_o<='0; end
            else begin
                if (chain_input_valid) tag_s1<=sample_tag_i;
                tag_s2<=tag_s1;
                continuous_word_tag_o<=tag_s2;
            end
        end
    end else begin : g_no_stream_tag
        assign continuous_word_tag_o = '0;
    end endgenerate
    // 软件复位可将 FIFO_MODE 改为 00；已完成的记录仍须继续从捕获读口输出。
    assign dout=captured ? fifo_data : direct_sample;
    assign dout_valid=captured ? fifo_valid : ((fifo_mode==0) && direct_sample_valid);
    assign correction_clip_low=clip_low;
    assign correction_clip_high=clip_high;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin cfg_reject<=1'b0; cfg_reject_reason<='0; end
        else begin
            cfg_reject<=spi_reject|bank_reject|capture_reject;
            if (spi_reject) cfg_reject_reason<=spi_reason;
            else if (bank_reject) cfg_reject_reason<=bank_reason;
            else if (capture_reject) cfg_reject_reason<=capture_reason;
            else cfg_reject_reason<=4'd0;
        end
    end
endmodule
`default_nettype wire
