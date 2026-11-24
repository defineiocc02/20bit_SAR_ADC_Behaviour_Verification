`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_lvds_phy_wrapper.sv
// 模块名     : ad4080_lvds_phy_wrapper
// 版本       : v1.0
// 功能       : 400 MHz DDR LVDS 输出 ODDR 与可编程 DCO 相位对齐 PHY 边界封装。
//              直接对接 ad4080_chip_top 的 continuous_da/db_fall/rise、dco 与 OE 口，
//              支持 0°（边沿对齐）/ 90°（中心对齐）/ 180°（反相）DCO 发射相位选择。
// 设计说明   :
//   * [闭合 SPI-CLOCK 边界] ad4080_continuous_lvds 在 CLK+ 上升沿交付本周期 FALL/RISE
//     位对，物理 ODDR 与 DCO 相位偏置留给外层 PHY。本封装将 ODDR、DCO 相位多路器、
//     self/echo 真三态 OE 门控及差分驱动边界收口在单一可替换模块内。
//   * [工艺替换接缝] 通过 `ifdef USE_FOUNDRY_LVDS_PHY 预留工艺厂模拟 LVDS TX 宏与
//     100 Ω 片上端接/ESD Pad 接口；默认编译给出纯同步可综合双沿模型供 Verilator/
//     Yosys 检查（无锁存器，兼容 400 MHz 单路 / 200 MHz 双路 DDR）。
//   * [相位选择] dco_phase_sel_i:
//       2'd0 -> 0° 同相边沿对齐（DCO 与 DA/DB 同沿翻转，依赖接收端 IDELAY）
//       2'd1 -> 90° 正交中心对齐（使用 clk_io_90_i 驱动 DCO，DCO 边沿落在 1.25 ns UI 正中）
//       2'd2 -> 180° 反相边沿对齐（反相极性发射）
//       2'd3 -> 保留，安全回退为 0° 同相。
// 变更记录   :
//   2026-11-24  v1.0 初版。实现 ODDR 与三档可编程 DCO 相位 LVDS PHY 边界封装。
// =============================================================================
module ad4080_lvds_phy_wrapper (
    // --- 高速 IO 时钟与正交 90° 发射时钟 ---
    input  logic       clk_io_i,            // 主 DDR IO 时钟（单路 400 MHz / 双路 200 MHz）
    input  logic       clk_io_90_i,         // 滞后 90° 正交时钟（90° 中心对齐 DCO 模式用）
    input  logic       rst_ni,              // 异步低有效复位

    // --- DCO 相位与模拟幅度控制 ---
    input  logic [1:0] dco_phase_sel_i,     // 0=0°边沿, 1=90°中心对齐, 2=180°反相
    input  logic [2:0] lvds_vod_ctrl_i,     // 来自 chip_top.lvds_vod_control_io_o[2:0]（3-bit 摆幅编码）

    // --- 对接 ad4080_chip_top 连续 LVDS 逻辑边界 ---
    input  logic       da_fall_i,           // 本周期 FALL 沿发射的 DA 数据位
    input  logic       da_rise_i,           // 紧随 RISE 沿发射的 DA 数据位
    input  logic       db_fall_i,           // 双 lane FALL 沿发射的 DB 数据位
    input  logic       db_rise_i,           // 双 lane RISE 沿发射的 DB 数据位
    input  logic       da_oe_i,             // DA 差分驱动使能（高有效）
    input  logic       db_oe_i,             // DB 差分驱动使能（单 lane 为 0）
    input  logic       dco_oe_i,            // DCO 差分时钟输出使能（self 模式为 0，echo 为 1）

    // --- 对外物理差分/单端 Pad 边界 ---
    output logic       da_ddr_o,            // ODDR 合成后的 DA 双沿逻辑输出
    output logic       db_ddr_o,            // ODDR 合成后的 DB 双沿逻辑输出
    output logic       dco_ddr_o,           // 经相位选择与 OE 门控后的 DCO 输出
    output logic       da_pad_p_o,          // DA 差分正向输出（OE=0 时静默为 0）
    output logic       da_pad_n_o,          // DA 差分反向输出（OE=0 时静默为 0）
    output logic       db_pad_p_o,          // DB 差分正向输出
    output logic       db_pad_n_o,          // DB 差分反向输出
    output logic       dco_pad_p_o,         // DCO 差分正向输出（self 模式静默为 0）
    output logic       dco_pad_n_o,         // DCO 差分反向输出
    output logic [2:0] vod_boost_pad_o      // 透传至模拟 LVDS TX 驱动级的 3-bit 摆幅编码
);

    // =========================================================================
    // 1. 数据与使能的上升沿原子快照（防跨半周期毛刺）
    // =========================================================================
    // [协议对齐] ad4080_continuous_lvds 在 posedge clk_io_i 交付 (fall, rise) 位对，
    // 随后的 clk_io_i 低电平窗口发 fall 位、紧接高电平窗口发 rise 位。
    logic da_fall_q, da_rise_q, db_fall_q, db_rise_q;
    logic da_oe_q, db_oe_q, dco_oe_q;

    always_ff @(posedge clk_io_i or negedge rst_ni) begin
        if (!rst_ni) begin
            da_fall_q <= 1'b0;
            da_rise_q <= 1'b0;
            db_fall_q <= 1'b0;
            db_rise_q <= 1'b0;
            da_oe_q   <= 1'b0;
            db_oe_q   <= 1'b0;
            dco_oe_q  <= 1'b0;
        end else begin
            da_fall_q <= da_fall_i;
            da_rise_q <= da_rise_i;
            db_fall_q <= db_fall_i;
            db_rise_q <= db_rise_i;
            da_oe_q   <= da_oe_i;
            db_oe_q   <= db_oe_i;
            dco_oe_q  <= dco_oe_i;
        end
    end

    // [无毛刺时钟门控] dco_oe_q 若直接与 clk_io_i 相与会在高半周切换产生毛刺（PrimeTime
    // 报 clock_gating_default Hold 违例）；在 clk_io_i 下降沿（低电平期）重采样为 dco_oe_neg_q，
    // 确保 DCO 使能仅在时钟低电平时切换，消除数字时钟门控毛刺。
    logic dco_oe_neg_q;
    always_ff @(negedge clk_io_i or negedge rst_ni) begin
        if (!rst_ni) dco_oe_neg_q <= 1'b0;
        else         dco_oe_neg_q <= dco_oe_q;
    end

    // =========================================================================
    // 2. ODDR 双沿合成与可编程 DCO 相位边界（工艺宏替换点）
    // =========================================================================
`ifdef USE_FOUNDRY_LVDS_PHY
    // [流片绑定点] 替换为工艺厂认证的 ODDR + LVDS TX 模拟驱动硬宏（含 100 Ω 匹配与 ESD）
    foundry_lvds_oddr_tx u_lvds_tx (
        .CLK_0     (clk_io_i),
        .CLK_90    (clk_io_90_i),
        .RST_N     (rst_ni),
        .PHASE_SEL (dco_phase_sel_i),
        .VOD_CTRL  (lvds_vod_ctrl_i),
        .DA_FALL   (da_fall_q),
        .DA_RISE   (da_rise_q),
        .DB_FALL   (db_fall_q),
        .DB_RISE   (db_rise_q),
        .DA_OE     (da_oe_q),
        .DB_OE     (db_oe_q),
        .DCO_OE    (dco_oe_neg_q),
        .DA_DDR    (da_ddr_o),
        .DB_DDR    (db_ddr_o),
        .DCO_DDR   (dco_ddr_o)
    );
`else
    logic dco_raw_c;

    // 可综合双沿 MUX 模型：低半周发 FALL 载荷，高半周发 RISE 载荷（零锁存）
    assign da_ddr_o = da_oe_q ? (clk_io_i ? da_rise_q : da_fall_q) : 1'b0;
    assign db_ddr_o = db_oe_q ? (clk_io_i ? db_rise_q : db_fall_q) : 1'b0;

    // 三档 DCO 发射相位选择（90° 模式要求由 PLL/DLL 提供正交 clk_io_90_i）
    always_comb begin
        case (dco_phase_sel_i)
            2'd1:    dco_raw_c = clk_io_90_i;    // 90° 中心对齐（每半周期落在 UI 正中）
            2'd2:    dco_raw_c = ~clk_io_i;      // 180° 反相边沿
            default: dco_raw_c = clk_io_i;       // 0° 同相边沿
        endcase
    end

    assign dco_ddr_o = dco_oe_neg_q ? dco_raw_c : 1'b0;
`endif

    // 差分模拟接口边界（OE=0 时双端静默为 0，由物理 LVDS TX Pad 转为高阻/共模）
    assign da_pad_p_o      = da_oe_q      ?  da_ddr_o  : 1'b0;
    assign da_pad_n_o      = da_oe_q      ? ~da_ddr_o  : 1'b0;
    assign db_pad_p_o      = db_oe_q      ?  db_ddr_o  : 1'b0;
    assign db_pad_n_o      = db_oe_q      ? ~db_ddr_o  : 1'b0;
    assign dco_pad_p_o     = dco_oe_neg_q ?  dco_ddr_o : 1'b0;
    assign dco_pad_n_o     = dco_oe_neg_q ? ~dco_ddr_o : 1'b0;
    assign vod_boost_pad_o = lvds_vod_ctrl_i;

endmodule
`default_nettype wire
