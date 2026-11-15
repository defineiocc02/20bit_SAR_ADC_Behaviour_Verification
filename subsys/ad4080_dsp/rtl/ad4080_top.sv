//=============================================================================
// @file    ad4080_top.sv
// @brief   受已知来源范围约束的并行 DSP 与配置接口。
// @details 集成一条校正、滤波和捕获链，不代替结果数据 PHY 或 CNV 转换启动。
//          工程诊断与厂家寄存器映射分开，缺失来源绑定时拒绝受影响的功能。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.27,35–42,56–71,73–94；排除项见 DSP_G1_ACTIVE_2。
// 时钟域: clk；SPI/GPIO 在入口同步，其余控制状态由调用方同步提供。
// 复位: rst_n 低有效、异步置位、由外部同步释放；软件复位保留已完成的 FIFO 记录。
// 延迟: 由子模块决定；工程配置拒绝事件额外经过一个寄存沿。
// 吞吐: 每个 clk 最多一个数值输入；捕获写入与完成记录读取互斥。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: spi_slave、ad4080_regs、ad4080_dsp_chain、event_detect、fifo_capture、gpio_ctrl。
// 边界: 不保证串行结果接口或物理时序；未知来源绑定不自动获得替代实现。
//=============================================================================
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
    parameter int C0=32'h40000000,
    parameter int C1=32'h40000000,
    parameter int C2=32'h40000000,
    parameter int P_MULTIBYTE_ORDER=-1,
    parameter int P_OFFSET_LSB_ADDR=-1,
    parameter int P_PRODUCT_ID_L=-1,
    parameter int P_SHORT_ADDR_BITS=-1,
    parameter int P_WRITE_SDO_DRIVE=-1
) (
    input logic clk, rst_n,
    input logic cs_n, sclk, sdi,
    output logic sdo, sdo_oe,
    input logic interface_ready, adc_idle,
    input logic valid_in,
    input logic [IN_W-1:0] x,
    input logic filter_sync, ext_event, arm, rd_en,
    input logic [3:0] gpio_i,
    output logic [3:0] gpio_o, gpio_oe,
    output logic [IN_W-1:0] dout,
    output logic dout_valid,
    output logic [7:0] status,
    output logic correction_clip_low, correction_clip_high,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason,
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
    logic fifo_arm,fifo_clear,wm_wb_valid,fsync_pulse,unused_sw_reset_pulse;
    logic [10:0] event_hyst;
    logic [11:0] event_hi,event_lo;
    logic event_en,hi_route,lo_route,hi_clr,lo_clr;
    logic hi_dtct,lo_dtct,hi_flag,lo_flag,hi_pulse,lo_pulse,alert_n;
    logic [3:0] gpio_dir,gpio_data,gpio_levels;
    logic [3:0][3:0] gpio_sel;
    (* ASYNC_REG="TRUE" *) logic [3:0] gpio_meta,gpio_sync;
    logic gpio_fsync,gpio_event;
    logic [IN_W-1:0] chain_data,fifo_data;
    logic chain_valid,fifo_valid,clip_low,clip_high;
    logic captured,read_done,full_flag,capture_active,capture_incomplete;
    logic unused_fifo_full,unused_fifo_empty,unused_fifo_overflow,unused_fifo_underflow;
    wire fsync_all=filter_sync|gpio_fsync|fsync_pulse;
    wire fifo_event=event_en ? ((hi_pulse&hi_route)|(lo_pulse&lo_route)) : (ext_event|gpio_event);
    wire capture_io_active=arm|fifo_arm|fifo_clear|rd_en|fifo_valid;

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
    ad4080_regs #(.P_MULTIBYTE_ORDER(P_MULTIBYTE_ORDER),.P_OFFSET_LSB_ADDR(P_OFFSET_LSB_ADDR),
        .P_PRODUCT_ID_L(P_PRODUCT_ID_L),.P_SHORT_ADDR_BITS(P_SHORT_ADDR_BITS),
        .P_GAIN_MAP_VALID(P_GAIN_MAP_VALID),.P_GAIN_CODE0_SUPPORTED(P_GAIN_CODE0_SUPPORTED),
        .P_COMP_COEFF_VALID(P_COMP_COEFF_VALID)) u_regs (
        .clk(clk),.rst_n(rst_n),.addr(addr),.wdata(wdata),.write_bytes(write_bytes),.we(we),
        .rdata(rdata),.entity_bytes(entity_bytes),.address_valid(address_valid),.read_only(read_only),
        .access_supported(access_supported),.access_reject_reason(access_reject_reason),
        .adc_idle(adc_idle),.gpio_levels(gpio_levels),.capture_active(capture_active),
        .capture_incomplete(capture_incomplete),.capture_io_active(capture_io_active),
        .st_fifo_full(full_flag),.st_fifo_read_done(read_done),
        .st_hi_flag(hi_flag&hi_route),.st_lo_flag(lo_flag&lo_route),
        .crc_err_pulse(crc_error),.clk_cnt_err_pulse(clock_error),.partial_err_pulse(partial_error),
        .addr_err_pulse(address_error),.readonly_err_pulse(readonly_error),.not_ready_err_pulse(notready_error),
        .hi_clr(hi_clr),.lo_clr(lo_clr),.offset_code(offset_code),.gain_code(gain_code),
        .filter_sel(filter_sel),.dec_sel(dec_sel),.fsync_pulse(fsync_pulse),
        .fifo_mode(fifo_mode),.fifo_watermark(watermark),.fifo_arm(fifo_arm),.fifo_clear(fifo_clear),
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
        .clk(clk),.rst_n(rst_n),.valid_in(valid_in),.x(x),.offset_code(offset_code),.gain_code(gain_code),
        .filter_sel(filter_sel),.dec_sel(dec_sel),.filter_sync(fsync_all),
        .y(chain_data),.valid_out(chain_valid),.clip_low(clip_low),.clip_high(clip_high),
        .processing_reject(processing_reject)
    );
    event_detect u_event (
        .clk(clk),.rst_n(rst_n),.enable(event_en),.hi_route(hi_route),.lo_route(lo_route),
        .clear_hi(hi_clr),.clear_lo(lo_clr),.sample_valid(chain_valid),.data_in(chain_data),
        .hi_thresh(event_hi),.lo_thresh(event_lo),.hysteresis(event_hyst),
        .hi_dtct(hi_dtct),.lo_dtct(lo_dtct),.hi_flag(hi_flag),.lo_flag(lo_flag),
        .hi_pulse(hi_pulse),.lo_pulse(lo_pulse),.alert_n(alert_n)
    );
    fifo_capture #(.DATA_W(IN_W),.DEPTH(FIFO_DEPTH)) u_capture (
        .clk(clk),.rst_n(rst_n),.clear_capture(fifo_clear),.arm(arm|fifo_arm),
        .event_in(fifo_event),.rd_en(rd_en),.mode(fifo_mode),.watermark(watermark),
        .valid_in(chain_valid),.data_in(chain_data),.rd_data(fifo_data),.rd_valid(fifo_valid),
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
    gpio_ctrl u_gpio (
        .gpio_i(gpio_sync),.gpio_dir(gpio_dir),.gpio_reg(gpio_data),.gpio_sel(gpio_sel),
        .spi_sdo(sdo),.spi_sdo_oe(sdo_oe),.fifo_full(full_flag),.fifo_read_done(read_done),
        .hi_dtct(hi_dtct),.lo_dtct(lo_dtct),.alert_n(alert_n),
        .gpio_o(gpio_o),.gpio_oe(gpio_oe),.filter_sync(gpio_fsync),.ext_event(gpio_event)
    );
    assign gpio_levels=(gpio_o&gpio_oe)|(gpio_sync&~gpio_oe);
    // 软件复位可将 FIFO_MODE 改为 00；已完成的记录仍须继续从捕获读口输出。
    assign dout=captured ? fifo_data : chain_data;
    assign dout_valid=captured ? fifo_valid : ((fifo_mode==0) && chain_valid);
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
