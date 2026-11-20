`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_csr_power_bridge.sv
// 模块名     : ad4080_csr_power_bridge
// 版本       : v1.0
// 功能       : 把真实配置 SPI 已接收的寄存器实体接到独立电源服务壳。
// 设计说明   :
//   * 与 SPI 执行时钟和电源监督器使用同一 AON 域。低功耗下保留模式
//     和复位控制，通用写入按全访问许可审定。
//   * 0x02 的来源是已提交的服务模式，保留码 01 和非零保留位拒绝。
//     0x14 只覆盖 POR_ANA bit3，同一位也映射到长状态 bit7。
//   * 0x1A/0x1B 的两个选择字段作为一个实体校验。禁用和未定义编码
//     均拒绝，表列合法但尚未绑定的功能仍交给原 bank 裁决。
//   * 显式启用 ADI 固定数据接口配方时，只接纳 0x15–0x17
//     当前可见值的重写。这些 no-op 不改变数据接口或存储。
//   * 软件复位的第二步须由服务与原 bank 在同一沿批准。桥跟踪原接口的
//     一位待完成阶段，使原 bank 的实际复位与服务受理均服从同沿裁决。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_csr_power_bridge #(
    parameter bit P_ACK_FIXED_DATA_DEFAULT = 1'b0  // 交付 owner 确认固定数据接口配方后启用
) (
    // --- AON 时间基准 ---
    input  logic        clk_i,
    input  logic        rst_ni,
    // --- 已完成的 SPI 寄存器实体 ---
    input  logic        spi_write_i,
    input  logic [15:0] spi_addr_i,
    input  logic [15:0] spi_wdata_i,
    input  logic [1:0]  spi_bytes_i,
    input  logic [15:0] bank_rdata_i,
    input  logic [7:0]  bank_status_i,
    input  logic [15:0] bank_status_long_i,
    output logic [15:0] spi_rdata_o,
    output logic [7:0]  device_status_o,
    output logic [15:0] spi_status_long_o,
    output logic        bank_we_o,
    output logic        bank_adc_idle_o,
    // --- 真实客户资格，与原 bank 的捕获判据同源 ---
    input  logic        adc_idle_i,
    input  logic        capture_incomplete_i,
    input  logic        capture_io_active_i,
    input  logic        clients_idle_i,
    // --- 实际电源服务状态和同沿握手 ---
    input  logic        cfg_control_allow_i,
    input  logic        cfg_full_allow_i,
    input  logic        por_ana_event_i,      // AON 单周期故障事件
    input  logic [1:0]  mode_active_i,        // 模式壳回报已接受的实际模式
    input  logic        mode_accept_i,
    output logic        mode_req_valid_o,
    output logic [1:0]  mode_req_o,
    input  logic        sw_reset_accept_i,
    output logic        sw_reset_req_o,
    // --- 集成诊断口，维持原厂商状态位分配 ---
    output logic        reject_o,
    output logic [3:0]  reject_reason_o
);
    localparam logic [15:0] CONFIG_A = 16'h0000;
    localparam logic [15:0] DEVICE_CONFIG = 16'h0002;
    localparam logic [15:0] DEVICE_STATUS = 16'h0014;
    localparam logic [15:0] GPIO_CONFIG_B = 16'h001a;
    localparam logic [15:0] GPIO_CONFIG_C = 16'h001b;
    logic reset_armed_q, por_ana_q;

    wire mode_selected = spi_addr_i == DEVICE_CONFIG;
    wire control_selected = mode_selected || spi_addr_i == CONFIG_A;
    wire write_allowed = control_selected ? (cfg_control_allow_i === 1'b1)
                                          : (cfg_full_allow_i === 1'b1);
    wire one_byte = spi_bytes_i === 2'd1;
    wire mode_code_valid = (spi_wdata_i[1:0] === 2'b00) ||
                           (spi_wdata_i[1:0] === 2'b10) ||
                           (spi_wdata_i[1:0] === 2'b11); // Rev. B 表 11 与表 34
    wire mode_value_valid = one_byte && (spi_wdata_i[7:2] === 6'd0) && mode_code_valid;
    // 表 53/54 列出 0–9 功能，0xA 明确为勿用，0xB–0xF 未列出功能。
    // 0x3 仍送给原 bank，是否有 FILTER_RDY 时序绑定由原接口判定。
    wire gpio_mux_selected = spi_addr_i == GPIO_CONFIG_B || spi_addr_i == GPIO_CONFIG_C;
    wire gpio_mux_defined = ((spi_wdata_i[7:4] <= 4'h9) === 1'b1) &&
                           ((spi_wdata_i[3:0] <= 4'h9) === 1'b1);
    wire gpio_mux_invalid = gpio_mux_selected && one_byte && !gpio_mux_defined;
    // 官方 init 对固定字段也执行读改写。只吸收无副作用的重复值，实质
    // 模式或电气配置变化继续交给原 bank 拒绝，缺失或不确定的数据也拒绝。
    wire fixed_data_selected = spi_addr_i >= 16'h0015 && spi_addr_i <= 16'h0017;
    wire fixed_data_noop = P_ACK_FIXED_DATA_DEFAULT && fixed_data_selected && one_byte &&
                           ((spi_wdata_i[7:0] == bank_rdata_i[7:0]) === 1'b1);
    assign mode_req_o = spi_wdata_i[1:0];
    assign mode_req_valid_o = spi_write_i && mode_selected && write_allowed && mode_value_valid;
    assign bank_we_o = spi_write_i && !mode_selected && write_allowed && !gpio_mux_invalid && !fixed_data_noop;

    // [同沿提交] 隔开“可结束序列”和“服务实际接收”。只有后者放行 bank
    // 的第二次写。否决时 bank 接收写入并走原先的撤销待提交和诊断路径。
    // 两处待完成状态都只检查可提交的一字节实体，避免丢 CS 或坏 CRC 时前移。
    wire reset_write = bank_we_o && spi_addr_i == CONFIG_A && one_byte;
    wire arm_reset = reset_write && spi_wdata_i[7] && spi_wdata_i[0];
    wire finish_reset = reset_write && reset_armed_q && !arm_reset;
    wire reset_clients_safe = (adc_idle_i === 1'b1) && (clients_idle_i === 1'b1);
    wire capture_safe = (capture_incomplete_i === 1'b0) && (capture_io_active_i === 1'b0);
    assign sw_reset_req_o = finish_reset && reset_clients_safe && capture_safe;
    assign bank_adc_idle_o = reset_clients_safe && (!finish_reset || (sw_reset_accept_i === 1'b1));

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) reset_armed_q <= 1'b0;
        else if (finish_reset) reset_armed_q <= 1'b0;
        else if (arm_reset && reset_clients_safe && capture_safe) reset_armed_q <= 1'b1;
    end

    // [状态] 数字 POR 仍由原 bank 的 bit0 表达。参考事件、新软复位请求
    // 的同沿受理都优先于本拍写 1 清位，使新的故障不会被清旧状态吞掉。
    wire clear_analog = bank_we_o && spi_addr_i == DEVICE_STATUS && one_byte && spi_wdata_i[3];
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) por_ana_q <= 1'b1;
        else if ((por_ana_event_i === 1'b1) || (sw_reset_accept_i === 1'b1)) por_ana_q <= 1'b1;
        else if (clear_analog) por_ana_q <= 1'b0;
    end

    always_comb begin
        spi_rdata_o = bank_rdata_i;
        if (mode_selected) spi_rdata_o = {14'd0, mode_active_i};
        else if (spi_addr_i == DEVICE_STATUS) spi_rdata_o[3] = por_ana_q;
        device_status_o = bank_status_i;
        device_status_o[3] = por_ana_q;
        spi_status_long_o = bank_status_long_i;
        spi_status_long_o[7] = por_ana_q;
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            reject_o <= 1'b0;
            reject_reason_o <= 4'd0;
        end else begin
            reject_o <= 1'b0;
            reject_reason_o <= 4'd0;
            if (spi_write_i && write_allowed && gpio_mux_invalid) begin
                reject_o <= 1'b1;
                reject_reason_o <= 4'd2;
            end else if (spi_write_i && (!write_allowed || (mode_selected && mode_accept_i !== 1'b1))) begin
                reject_o <= 1'b1;
                reject_reason_o <= (mode_selected && !mode_value_valid) ? 4'd9 : 4'd8;
            end
        end
    end
endmodule
`default_nettype wire
