`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : noos_spi_rtl.sv
// 模块名     : noos_spi_rtl
// 版本       : v1.0
// 功能       : 将 C 主机 SPI 引脚接到冻结解码、寄存器和独立电源服务。
// 设计说明   :
//   * 仿真夹具使用同一 100 MHz AON 时钟及外供 IOVDD，板级输入已合规。
//     保留硬 POR、初始化计时及模式和复位的实际 AON 裁决。
//   * 只比较配置和状态。已完成转换、外部触发、结果数据接口不在此夹具内，
//     参考和模拟 ready 由静态合规夹具提供。
//   * 诊断端供仿真主机单独采集。SDO 仍由真实 SPI 与供电许可决定。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module noos_spi_rtl (
    // --- 仿真主机驱动 ---
    input  logic       clk_i,           // 连续 AON 及配置执行时钟
    input  logic       power_ok_i,      // 合规的数字电源释放 POR
    input  logic       cs_ni, sclk_i, sdi_i,
    // --- 实际 RTL 输出 ---
    output logic       sdo_o, sdo_oe_o, // 独立 SDI/SDO 的配置模型
    output logic       ready_o,         // 初始化完成且 IOVDD 允许读回
    output logic       convert_ok_o,    // 监督器新转换许可
    output logic       reject_o,
    output logic [3:0] reject_reason_o
);
    wire rst_n, cfg_control, new_por, recover_req;
    wire mode_valid, mode_accept, sw_req, sw_accept, bank_idle;
    wire [1:0] mode_req, mode_active;
    wire write_entity, bank_we, address_valid, read_only, access_supported;
    wire [1:0] write_bytes, entity_bytes;
    wire [15:0] addr, wdata, bank_rdata, spi_rdata, bank_long, spi_long;
    wire [7:0] bank_status, status_short;
    wire sdo_drive;
    wire crc_en, send_status, single_inst, short_instr, addr_asc, strict_access;
    wire crc_err, clocks_err, partial_err, addr_err, ro_err, init_err;
    wire spi_reject, bank_reject, bridge_reject;
    wire [3:0] access_reason, spi_reason, bank_reason, bridge_reason;

    // 夹具不维持转换任务，故 clients_idle 恒真。恢复请求到达后立刻
    // 回复本夹具的模拟 ready，监督器自身的待机/睡眠计时照常执行。
    ad4080_pwr_supervisor #(.AON_MAX_CLK_HZ(100_000_000)) service (
        .clk_aon_i(clk_i), .clk_core_i(clk_i), .clk_io_i(clk_i), .external_io_i(1'b1),
        .por_d_ok_async_i(power_ok_i), .vdd33_ok_async_i(1'b1), .ref_ok_async_i(1'b1),
        .iovdd_ok_async_i(1'b1), .ana_ready_async_i(1'b1), .clients_idle_aon_i(1'b1),
        .sw_reset_req_aon_i(sw_req), .mode_req_valid_aon_i(mode_valid), .mode_req_aon_i(mode_req),
        .wake_recover_done_aon_i(recover_req),
        .cfg_hw_reset_no(rst_n), .core_reset_no(), .fifo_reset_no(), .io_reset_no(), .ana_reset_no(),
        .cfg_init_done_aon_o(), .cfg_control_write_allow_aon_o(cfg_control),
        .cfg_full_access_allow_aon_o(ready_o), .fifo_read_allow_aon_o(), .lvds_allow_aon_o(),
        .conversion_allow_aon_o(convert_ok_o), .operating_mode_aon_o(mode_active),
        .wake_busy_aon_o(), .wake_recover_req_aon_o(recover_req),
        .mode_req_accept_aon_o(mode_accept), .sw_reset_accept_aon_o(sw_accept),
        .por_ana_event_aon_o(new_por), .isolate_io_o(), .iovdd_ldo_en_o(), .ref_cmo_en_o()
    );

    // 原配置 SPI 执行所有命令解析、实体长度检查和位序。主机模型没有寄存器镜像。
    spi_slave #(.P_SHORT_ADDR_BITS(7), .P_WRITE_SDO_DRIVE(1)) spi (
        .clk(clk_i), .rst_n(rst_n), .cs_n(cs_ni), .sclk(sclk_i), .sdi(sdi_i),
        .sdo(sdo_o), .sdo_oe(sdo_drive), .interface_ready(cfg_control),
        .crc_en(crc_en), .send_status(send_status), .single_inst(single_inst), .short_instr(short_instr),
        .addr_asc(addr_asc), .strict_access(strict_access), .status_long(spi_long), .status_short(status_short),
        .addr(addr), .wdata(wdata), .write_bytes(write_bytes), .we(write_entity),
        .rdata(spi_rdata), .entity_bytes(entity_bytes), .address_valid(address_valid), .read_only(read_only),
        .access_supported(access_supported), .access_reject_reason(access_reason),
        .crc_err_pulse(crc_err), .clk_cnt_err_pulse(clocks_err), .partial_err_pulse(partial_err),
        .addr_err_pulse(addr_err), .readonly_err_pulse(ro_err), .not_ready_err_pulse(init_err),
        .cfg_reject(spi_reject), .cfg_reject_reason(spi_reason)
    );

    // AD4080 表 36 与固定 no-OS 的 AD4080_PRODUCT_ID 均给低字节 0x50，
    // 第 77 页的标题复位值另有冲突。此夹具只验证该公开驱动身份配方。
    ad4080_regs #(.P_MULTIBYTE_ORDER(0), .P_OFFSET_LSB_ADDR(32'h25),
                  .P_PRODUCT_ID_L(32'h50), .P_SHORT_ADDR_BITS(7)) bank (
        .clk(clk_i), .rst_n(rst_n), .addr(addr), .wdata(wdata), .write_bytes(write_bytes), .we(bank_we),
        .rdata(bank_rdata), .entity_bytes(entity_bytes), .address_valid(address_valid),
        .read_only(read_only), .access_supported(access_supported), .access_reject_reason(access_reason),
        .adc_idle(bank_idle), .gpio_levels(4'b0), .capture_active(1'b0),
        .capture_incomplete(1'b0), .capture_io_active(1'b0),
        .st_fifo_full(1'b0), .st_fifo_read_done(1'b0), .st_hi_flag(1'b0), .st_lo_flag(1'b0),
        .crc_err_pulse(crc_err), .clk_cnt_err_pulse(clocks_err), .partial_err_pulse(partial_err),
        .addr_err_pulse(addr_err), .readonly_err_pulse(ro_err), .not_ready_err_pulse(init_err),
        .hi_clr(), .lo_clr(), .offset_code(), .gain_code(), .filter_sel(), .dec_sel(), .fsync_pulse(),
        .fifo_mode(), .fifo_watermark(), .fifo_arm(), .fifo_clear(), .wm_wb_valid(1'b0), .wm_wb_data(15'd0),
        .event_hyst(), .event_hi(), .event_lo(), .event_en(), .hi_route(), .lo_route(),
        .gpio_dir(), .gpio_reg(), .gpio_sel(), .crc_en(crc_en), .send_status(send_status), .addr_asc(addr_asc),
        .single_inst(single_inst), .short_instr(short_instr), .strict_access(strict_access),
        .sw_reset_pulse(), .status(bank_status), .interface_status(),
        .status_long(bank_long), .status_short(status_short), .cfg_reject(bank_reject), .cfg_reject_reason(bank_reason)
    );

    // 本次厂商 API 配方只回写三组公开默认数据配置。不同数据配置继续拒绝。
    ad4080_csr_power_bridge #(.P_ACK_FIXED_DATA_DEFAULT(1'b1)) bridge (
        .clk_i(clk_i), .rst_ni(rst_n), .spi_write_i(write_entity), .spi_addr_i(addr),
        .spi_wdata_i(wdata), .spi_bytes_i(write_bytes), .bank_rdata_i(bank_rdata),
        .bank_status_i(bank_status), .bank_status_long_i(bank_long), .spi_rdata_o(spi_rdata),
        .device_status_o(), .spi_status_long_o(spi_long),
        .bank_we_o(bank_we), .bank_adc_idle_o(bank_idle), .adc_idle_i(1'b1),
        .capture_incomplete_i(1'b0), .capture_io_active_i(1'b0), .clients_idle_i(1'b1),
        .cfg_control_allow_i(cfg_control), .cfg_full_allow_i(ready_o), .por_ana_event_i(new_por),
        .mode_active_i(mode_active), .mode_accept_i(mode_accept), .mode_req_valid_o(mode_valid),
        .mode_req_o(mode_req), .sw_reset_accept_i(sw_accept), .sw_reset_req_o(sw_req),
        .reject_o(bridge_reject), .reject_reason_o(bridge_reason)
    );

    // 与现有顶层的故障优先级一致。GPIO 保留码等项目诊断可通过此夹具上报，
    // 不改写厂商 0x11 的位分配。
    assign sdo_oe_o = ready_o && sdo_drive;
    assign reject_o = spi_reject || bank_reject || bridge_reject;
    assign reject_reason_o = spi_reject ? spi_reason : bank_reject ? bank_reason :
                             bridge_reject ? bridge_reason : 4'd0;
endmodule
`default_nettype wire
