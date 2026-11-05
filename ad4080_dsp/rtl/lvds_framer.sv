//=============================================================================
// @file    lvds_framer.sv
// @brief   单/双 lane 的 SDR 位序与重复帧原型。
// @details 每次 bit_en 输出单 lane 的 1 bit，或双 lane 各 1 bit，MSB 先行。
//          帧尾装载下一帧；仅有一个 latest-pending 寄存器，无 ready 或队列，相邻 valid 可覆盖待装帧结果。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；valid、data、lane_sel、bit_en 须同步。
// 复位: rst_n 异步低有效，先发送复位零帧。
// 延迟: 新数据在后续帧边界装载；同沿 valid 的 data_in 优先。
// 吞吐: 单 lane 每帧 20 次 bit_en，双 lane 每帧 10 次；不等于 16 tick 输入的无损接口。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: dco 是 bit_en 的寄存电平；bit_en 恒高时不翻转，不能用作真实回送时钟。
//     不实现 DDR、差分 PHY 或 Manchester；lane_sel 须在系统规定的空闲/复位边界修改。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module lvds_framer (
    input  logic        clk,                              // 系统时钟
    input  logic        rst_n,                            // 异步复位，低有效
    input  logic        valid_in,                         // 新样本有效
    input  logic [19:0] data_in,                          // 20-bit 补码结果
    input  logic        lane_sel,     // 0 = 1 lane, 1 = 2 lanes
    input  logic        bit_en,       // 位速率使能（SDR）
    output logic [1:0]  ser_data,                         // lane0/lane1 串行数据
    output logic [1:0]  ser_frame,                        // lane0/lane1 帧首
    output logic        dco                               // 回送位时钟（bit_en 寄存输出）
);

    logic [19:0] dat_r;
    logic [19:0] shreg;
    logic [4:0]  phase;
    logic        dco_r;

    wire lane2 = lane_sel;
    wire [4:0] last = lane2 ? 5'd9 : 5'd19;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dat_r     <= '0;
            shreg     <= '0;
            phase     <= '0;
            ser_data  <= '0;
            ser_frame <= '0;
            dco_r     <= 1'b0;
        end else begin
            dco_r <= bit_en;

            if (valid_in) dat_r <= data_in;

            if (bit_en) begin
                // 每次 bit_en 输出当前最高位；双 lane 同时输出相邻的两位。
                ser_data[0]  <= shreg[19];
                ser_data[1]  <= lane2 ? shreg[18] : 1'b0;
                ser_frame[0] <= (phase == 5'd0);
                ser_frame[1] <= lane2 && (phase == 5'd0);
                shreg        <= lane2 ? {shreg[17:0], 2'b00}
                                      : {shreg[18:0], 1'b0};

                if (phase == last) begin
                    // 帧尾为下一帧装载；同沿 valid 直接取 data_in，否则重复最近保存的样本。
                    shreg <= valid_in ? data_in : dat_r;
                    phase <= 5'd0;
                end else begin
                    phase <= phase + 5'd1;
                end
            end
        end
    end

    assign dco = dco_r;

endmodule

`default_nettype wire
