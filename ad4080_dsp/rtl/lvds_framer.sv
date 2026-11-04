//=============================================================================
// @file    lvds_framer.sv
// @brief   单/双lane的SDR位序与重复帧原型
// @details 每bit_en输出单lane的1bit或双lane各1bit，MSB先行；帧尾装载下一帧。
//          只有一个latest-pending寄存器，无ready/队列；相邻valid可覆盖尚未装帧的结果。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据 : 功能映射见docs/ad4080_traceability.md；不声称器件逐拍等价。
// 时钟域   : clk；valid/data/lane_sel/bit_en必须同步
// 复位     : rst_n异步低有效；先发送复位零帧
// 延迟     : 新数据在后续帧边界装载；与边界同沿valid由data_in优先
// 吞吐     : 单lane20次bit_en/帧；双lane10次，不等同16tick输入的无损接口
// 可综合性 : RTL结构；目标库映射、约束、SRAM/PHY及STA需单独验收。
// 依赖     : 无
// 边界     : dco是bit_en寄存电平；bit_en恒高时dco不翻转，不能当真实回送时钟。
//          不实现DDR/差分PHY/Manchester；lane_sel必须在系统定义的空闲/复位边界更改。
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
                // 每个 bit_en 输出当前 MSB（1 lane 1 位，2 lane 2 位）
                ser_data[0]  <= shreg[19];
                ser_data[1]  <= lane2 ? shreg[18] : 1'b0;
                ser_frame[0] <= (phase == 5'd0);
                ser_frame[1] <= lane2 && (phase == 5'd0);
                shreg        <= lane2 ? {shreg[17:0], 2'b00}
                                      : {shreg[18:0], 1'b0};

                if (phase == last) begin
                    // 帧尾：为下一帧装载。有新数据用新数据，否则重复上一帧。
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
