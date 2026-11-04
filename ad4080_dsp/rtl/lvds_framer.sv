//=============================================================================
// @file    lvds_framer.sv
// @brief   LVDS 结果并串（1/2 lane，空闲重复上一帧）
// @details MSB first；单 lane 20 个 bit_en/帧，双 lane 每 lane 10 个；
//          无新数据时重复上一帧（手册 Figure 97 行为）。SDR 数字模型。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 2.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.45-53, p.66 Figure 97
// 时钟域   : clk 单时钟；bit_en 为位速率使能
// 复位     : rst_n 异步低有效
// 延迟     : valid_in -> 下一帧起点装载
// 吞吐     : 1 bit/lane/bit_en
// 可综合性 : 是；dco 为寄存输出
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版（SDR，单 lane，只发一次）
//   2.0.0 2026-10-19 增加 2 lane、重复帧、dco 寄存
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.45-53
//     - 结果 MSB first；每 lane 最高 800 Mbps；DCO 为回送时钟。
//     - 0x15 bit2 SPI_LVDS_LANES：0 = 单 lane，1 = 双 lane。
//     - p.66 Figure 97：新数据到来前，移位寄存器里保留上一个结果，
//       接口会**重复发送**旧结果，主机可重读或忽略。
//
//   本模块只做数字并串，不含 DDR PHY / 差分驱动 / 曼彻斯特编码：
//     - SDR 模型：每个 bit_en 移 1 位；真实器件是 DDR，用 2:1 mux 或 ODDR 实现，
//       位时序等价（同样 20 bit / 样本），差别只在电气层。
//     - 1 lane：20 个 bit_en 发完一帧；2 lane：每 lane 10 个 bit_en。
//     - 帧空闲时**自动重复上一帧**，与 Figure 97 的行为一致（旧版本只发一次就停）。
//
//   时序：valid_in 写入 dat_r；下一帧起点装载；ser_data 与 ser_frame 同拍寄存。
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
