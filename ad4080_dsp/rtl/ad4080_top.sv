//=============================================================================
// @file    ad4080_top.sv
// @brief   AD4080风格数字处理原型的组合顶层
// @details 输入/并行输出为补码；组合配置SPI、校正、滤波、捕获FIFO、事件、GPIO和串行帧。
//          CNV只提供启动脉冲及忙时再次启动的数字代理检查，不测量模拟tCONV。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk；SPI/CNV/GPIO为异步引脚，由各入口处理；其余控制必须同步
// 复位     : rst_n异步低有效；系统集成必须同步释放
// 延迟     : 由子模块决定；顶层不增加数据流水
// 吞吐     : 并行输入最多1样本/clk；串行路径另有位预算
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : ad4080_regs、spi_slave、cnv_ctrl、ad4080_dsp_chain、event_detect、fifo_capture、gpio_ctrl、lvds_framer
// 边界     : IN_W=20、FIFO_DEPTH=16384为当前工程配置；未证明所有参数组合。
//          串行发送使用滤波结果，不使用FIFO读口；SPI数据读出、DDR PHY、Manchester未实现。
//          软件寄存器复位不清FIFO；完整重新开始需外部rst_n或项目封装的DSP restart。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module ad4080_top #(
    parameter int IN_W       = 20,
    parameter int FIFO_DEPTH = 16384
) (
    input  logic                 clk,
    input  logic                 rst_n,

    // ---- 配置 SPI（Mode 3）----
    input  logic                 cs_n,
    input  logic                 sclk,
    input  logic                 sdi,
    output logic                 sdo,
    output logic                 sdo_oe,

    // ---- 转换启动 ----
    input  logic                 cnv_p,
    input  logic                 cnv_n,
    output logic                 cnv_pulse,

    // ---- ADC 核结果（20-bit 补码）----
    input  logic                 valid_in,
    input  logic [IN_W-1:0]      x,

    // ---- 同步 / 触发 / 读出 ----
    input  logic                 filter_sync,   // 高有效单拍
    input  logic                 ext_event,     // 外部事件触发
    input  logic                 arm,           // 软件 arm（与寄存器写 OR）
    input  logic                 rd_en,

    // ---- LVDS ----
    input  logic                 bit_en,
    output logic [1:0]           ser_data,
    output logic [1:0]           ser_frame,
    output logic                 dco,

    // ---- GPIO ----
    input  logic [3:0]           gpio_i,
    output logic [3:0]           gpio_o,
    output logic [3:0]           gpio_oe,

    // ---- 结果 ----
    output logic [IN_W-1:0]      dout,
    output logic                 dout_valid,
    output logic [7:0]           status,                  // DEVICE_STATUS 镜像，供 SPI 回读
    output logic                 correction_clip_low,      // 校正级事件，不随滤波/FIFO 延迟
    output logic                 correction_clip_high
);

    // ------------------------------------------------------------------
    // 内部信号
    // ------------------------------------------------------------------
    logic [15:0] addr;
    logic [7:0]  wdata, rdata;
    logic        we;
    logic        crc_err_pulse, clk_cnt_err_pulse;

    logic signed [11:0] offset_code;
    logic [9:0]         gain_code;
    logic [1:0]         filter_sel;
    logic [3:0]         dec_sel;

    logic [1:0]         fifo_mode;
    logic [14:0]        fifo_watermark;
    logic               fifo_arm;

    logic [10:0]        event_hyst;
    logic [11:0]        event_hi, event_lo;
    logic               event_en, hi_route, lo_route, cnv_err_route;

    logic [3:0]         gpio_dir, gpio_reg;
    logic [3:0][3:0]    gpio_sel;
    logic               gpio_fsync, gpio_ext_event;

    logic               lvds_cnv_en, lane_sel, intf_mode;
    logic               lvds_self_clk, lvds_mnc_en;
    logic [3:0]         lvds_cnv_clk_cnt;
    logic               crc_en, send_status;
    logic               sdo_en, addr_asc, single_inst, short_instr;
    logic               sw_reset_pulse;
    logic               sdo_i, sdo_oe_i;

    logic               hi_clr, lo_clr;
    logic               hi_flag, lo_flag, hi_pulse, lo_pulse;
    logic               hi_dtct, lo_dtct, alert_n;
    logic               cnv_err_flag;
    logic               full_flag;
    logic               wm_wb_valid;
    logic [14:0]        wm_wb_data;

    logic               fifo_full, fifo_empty, fifo_ovf, fifo_unf;
    logic               fifo_rd_valid, captured, read_done;
    logic [IN_W-1:0]    fifo_rd_data;
    logic               cap_full;

    logic               cnv_busy;
    logic               cnv_err_pulse;
    logic               cnv_err_routed;

    logic [IN_W-1:0]    chain_y;
    logic               chain_valid;
    logic               chain_settled;
    logic               clip_low, clip_high;
    logic               ch_full, ch_empty, ch_ovf, ch_unf;

    logic               fsync_all;
    logic               fsync_pulse;
    logic               fifo_event;
    logic [1:0]         ser_data_i, ser_frame_i;
    logic               dco_i;

    assign correction_clip_low  = clip_low;
    assign correction_clip_high = clip_high;

    assign fsync_all      = filter_sync | gpio_fsync | fsync_pulse;
    assign cnv_err_routed = cnv_err_pulse & cnv_err_route;
    // INT_EVENT_EN=0 走外部事件，=1 走内部阈值检测（手册 Table 26）
    assign fifo_event     = event_en ? ((hi_pulse & hi_route) | (lo_pulse & lo_route))
                                     : (ext_event | gpio_ext_event);

    // ------------------------------------------------------------------
    // SPI + 寄存器
    // ------------------------------------------------------------------
    spi_slave u_spi (
        .clk(clk), .rst_n(rst_n),
        .cs_n(cs_n), .sclk(sclk), .sdi(sdi),
        .sdo(sdo_i), .sdo_oe(sdo_oe_i),
        .crc_en(crc_en), .send_status(send_status), .status_byte(status),
        .single_inst(single_inst), .short_instr(short_instr), .addr_asc(addr_asc),
        .addr(addr), .wdata(wdata), .we(we),
        .rdata(rdata),
        .crc_err_pulse(crc_err_pulse), .clk_cnt_err_pulse(clk_cnt_err_pulse)
    );

    // SDO_ENABLE（0x00[4]，复位 1）为 0 时 SDO 关闭
    assign sdo    = sdo_i & sdo_en;
    assign sdo_oe = sdo_oe_i & sdo_en;

    ad4080_regs u_regs (
        .clk(clk), .rst_n(rst_n),
        .addr(addr), .wdata(wdata), .we(we), .rdata(rdata),
        .st_fifo_full(cap_full),
        .st_fifo_read_done(read_done),
        .st_hi_flag(hi_flag & hi_route),
        .st_lo_flag(lo_flag & lo_route),
        .adc_cnv_err_pulse(cnv_err_routed),
        .crc_err_pulse(crc_err_pulse),
        .clk_cnt_err_pulse(clk_cnt_err_pulse),
        .hi_clr(hi_clr), .lo_clr(lo_clr),
        .offset_code(offset_code), .gain_code(gain_code),
        .filter_sel(filter_sel), .dec_sel(dec_sel),
        .fifo_mode(fifo_mode), .fifo_watermark(fifo_watermark),
        .fifo_arm(fifo_arm),
        .event_hyst(event_hyst), .event_hi(event_hi), .event_lo(event_lo),
        .event_en(event_en), .hi_route(hi_route), .lo_route(lo_route),
        .cnv_err_route(cnv_err_route),
        .gpio_dir(gpio_dir), .gpio_reg(gpio_reg), .gpio_sel(gpio_sel),
        .lvds_cnv_en(lvds_cnv_en), .lane_sel(lane_sel), .intf_mode(intf_mode),
        .lvds_self_clk(lvds_self_clk), .lvds_mnc_en(lvds_mnc_en),
        .lvds_cnv_clk_cnt(lvds_cnv_clk_cnt),
        .crc_en(crc_en), .send_status(send_status),
        .sdo_en(sdo_en), .addr_asc(addr_asc),
        .single_inst(single_inst), .short_instr(short_instr),
        .sw_reset_pulse(sw_reset_pulse),
        .cnv_err_flag(cnv_err_flag),
        .wm_wb_valid(wm_wb_valid), .wm_wb_data(wm_wb_data),
        .fsync_pulse(fsync_pulse),
        .status(status)
    );

    // ------------------------------------------------------------------
    // CNV 同步 + tCONV 违反检测
    // ------------------------------------------------------------------
    cnv_ctrl u_cnv (
        .clk(clk), .rst_n(rst_n),
        .cnv_p(cnv_p), .cnv_n(cnv_n), .lvds_en(lvds_cnv_en),
        .busy(cnv_busy),
        .cnv_pulse(cnv_pulse), .cnv_err(cnv_err_pulse)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)         cnv_busy <= 1'b0;
        else if (valid_in)  cnv_busy <= 1'b0;
        else if (cnv_pulse) cnv_busy <= 1'b1;
    end

    // ------------------------------------------------------------------
    // 校正 + 滤波
    // ------------------------------------------------------------------
    ad4080_dsp_chain #(.IN_W(IN_W), .USE_FIFO(1'b0)) u_chain (
        .clk(clk), .rst_n(rst_n), .valid_in(valid_in), .x(x),
        .offset_code(offset_code), .gain_code(gain_code),
        .filter_sel(filter_sel), .dec_sel(dec_sel),
        .filter_sync(fsync_all), .rd_en(1'b0),
        .y(chain_y), .valid_out(chain_valid), .settled(chain_settled),
        .clip_low(clip_low), .clip_high(clip_high),
        .fifo_full(ch_full), .fifo_empty(ch_empty),
        .fifo_overflow(ch_ovf), .fifo_underflow(ch_unf)
    );

    // ------------------------------------------------------------------
    // 事件检测（对滤波后的结果）
    // ------------------------------------------------------------------
    event_detect u_event (
        .clk(clk), .rst_n(rst_n),
        .enable(event_en),
        .clear_hi(hi_clr), .clear_lo(lo_clr),
        .sample_valid(chain_valid), .data_in(chain_y),
        .hi_thresh(event_hi), .lo_thresh(event_lo), .hysteresis(event_hyst),
        .hi_dtct(hi_dtct), .lo_dtct(lo_dtct),
        .hi_flag(hi_flag), .lo_flag(lo_flag),
        .hi_pulse(hi_pulse), .lo_pulse(lo_pulse),
        .alert_n(alert_n)
    );

    // ------------------------------------------------------------------
    // FIFO 捕获
    // ------------------------------------------------------------------
    fifo_capture #(.DATA_W(IN_W), .DEPTH(FIFO_DEPTH)) u_capture (
        .clk(clk), .rst_n(rst_n),
        .arm(arm | fifo_arm), .event_in(fifo_event), .rd_en(rd_en),
        .mode(fifo_mode), .watermark(fifo_watermark),
        .valid_in(chain_valid), .data_in(chain_y),
        .rd_data(fifo_rd_data), .rd_valid(fifo_rd_valid),
        .full(fifo_full), .empty(fifo_empty),
        .overflow(fifo_ovf), .underflow(fifo_unf),
        .read_done(read_done), .captured(captured),
        .full_flag(full_flag),
        .wm_wb_valid(wm_wb_valid), .wm_wb_data(wm_wb_data)
    );

    // FIFO_MODE=00 时 FIFO 关闭，结果直通到并行结果口（NOTE(choice):
    // 真实器件此时数据只走 LVDS/SPI，dout 是本数字模型给验证用的观察口）。
    assign dout       = (fifo_mode == 2'b00) ? chain_y : fifo_rd_data;
    assign dout_valid = (fifo_mode == 2'b00) ? chain_valid : fifo_rd_valid;
    assign cap_full   = fifo_full | full_flag;

    // ------------------------------------------------------------------
    // GPIO
    // ------------------------------------------------------------------
    // GPIO 是异步引脚，进数字域前先做 2FF 同步（综合时加 ASYNC_REG）
    logic [3:0] gpio_s0, gpio_s1;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gpio_s0 <= 4'b0;
            gpio_s1 <= 4'b0;
        end else begin
            gpio_s0 <= gpio_i;
            gpio_s1 <= gpio_s0;
        end
    end

    gpio_ctrl u_gpio (
        .gpio_i(gpio_s1), .gpio_dir(gpio_dir), .gpio_reg(gpio_reg),
        .gpio_sel(gpio_sel),
        .spi_sdo(sdo),
        .fifo_full(cap_full),
        .fifo_read_done(read_done),
        .filt_rdy(chain_valid & chain_settled),
        .hi_dtct(hi_dtct), .lo_dtct(lo_dtct),
        .alert_n(alert_n & ~(cnv_err_flag & cnv_err_route)),
        .gpio_o(gpio_o), .gpio_oe(gpio_oe),
        .filter_sync(gpio_fsync), .ext_event(gpio_ext_event)
    );

    // ------------------------------------------------------------------
    // LVDS 并串（1/2 lane，空闲重复上一帧）
    //   intf_mode=1 表示数据改由 SPI 回读，LVDS 发送器掉电 -> 输出清零。
    //   lvds_self_clk=1 表示自时钟模式，DCO 关闭。
    //   lvds_mnc_en / lvds_cnv_clk_cnt 只做寄存器保存：Manchester 与
    //   时钟对齐属于电气层，本数字模型不实现。
    // ------------------------------------------------------------------
    lvds_framer u_lvds (
        .clk(clk), .rst_n(rst_n),
        .valid_in(chain_valid), .data_in(chain_y),
        .lane_sel(lane_sel), .bit_en(bit_en),
        .ser_data(ser_data_i), .ser_frame(ser_frame_i), .dco(dco_i)
    );

    assign ser_data  = intf_mode ? 2'b00 : ser_data_i;
    assign ser_frame = intf_mode ? 2'b00 : ser_frame_i;
    assign dco       = (intf_mode || lvds_self_clk) ? 1'b0 : dco_i;

endmodule

`default_nettype wire
