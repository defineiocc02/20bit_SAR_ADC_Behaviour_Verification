//=============================================================================
// @file    cal_two_point.sv
// @brief   两点增益/失调校准（常数除法）
// @details 由两点 (x1,y1)/(x2,y2) 求 m、c，再折算成 GAIN/OFFSET 寄存器值；
//          x1/x2 为编译期常数，除法退化为常数乘法。
// @project AD4080 数字子系统
// @author  AD4080 Digital Team
// @date    2026-10-19
// @version 2.0.0
//-----------------------------------------------------------------------------
// 手册依据 : AD4080 Rev.B p.71（系统误差校正流程）
// 时钟域   : clk 单时钟
// 复位     : rst_n 异步低有效
// 延迟     : 3 clk
// 吞吐     : 1 次/3 clk
// 可综合性 : 是；两个常数乘法器
// 依赖     : 无
// 修订记录 :
//   1.0.0 2026-10-19 初版
//   2.0.0 2026-10-19 测试点改补码；INV_DX_K 位宽修正；512 字面量修正
//=============================================================================
// 设计说明（迁移自原文件头，保留原始意图）：
//   数据手册依据：AD4080 Rev.B p.71
//     m = (y2 - y1) / (x2 - x1)
//     c = y1 - m*x1
//     GAIN[9:0] = 512 + (m - 1) / (0.015594/512)
//     OFFSET[11:0] = round(c)  (1 LSB = ADC LSB, 12-bit two's complement)
//
//   本模块假设两点测试码 x1/x2 是**已知常数**（默认 10%/90% 满幅）。
//   因此 1/(x2-x1) 和 1/((x2-x1)*K) 都是编译期常数，除法变成常数乘法，
//   不需要运行时除法器。若 x1/x2 需要运行时配置，必须另加除法器/倒数 LUT。
//
//   默认常数（FS=2^20，**二进制补码**）：
//     测试点取满量程的 10% / 90%：x1=-419430, x2=+419430, DX=838860
//     K = 0.015594/511 = 3.0516634e-5（分母是 511，不是 512，见 sys_error_corr）
//     SHIFT_K=20, INV_DX_K=round(1/(DX*K)*2^20)=40961
//     SHIFT_O=30, INV_DX=round(2^30/DX)=1280
//
//   注意（旧版本三个致命错误，均已修正）：
//     1. X1/X2 用的是 offset-binary 的 104858/943718，补码域下应取负/正对称点。
//     2. INV_DX_K 声明成 16-bit signed，41041 > 32767 会溢出成 -24495。
//     3. 10'sd512 在 10-bit 有符号里等于 -512（512 需要 11 bit）。
//
//   流水：3 级；start -> done 延迟 3 个时钟。
//   输出 clamp：GAIN 0..1023；OFFSET -2048..2047。
//=============================================================================

`timescale 1ns/1ps
`default_nettype none

module cal_two_point #(
    parameter int IN_W      = 20,
    parameter int X1_CODE   = -419430,
    parameter int X2_CODE   = 419430,
    parameter int INV_DX_K  = 40961,
    parameter int SHIFT_K   = 20,
    parameter int INV_DX    = 1280,
    parameter int SHIFT_O   = 30
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     start,
    input  logic [IN_W-1:0]          y1,
    input  logic [IN_W-1:0]          y2,
    output logic [9:0]               gain_code,
    output logic signed [11:0]       offset_code,
    output logic                     done,
    output logic                     error                // 输入非法/除零等错误（当前恒 0）
);

    localparam int DX = X2_CODE - X1_CODE;

    // 显式定宽常数：避免 int/无符号把乘法结果截到 32 位
    localparam logic signed [IN_W:0]       DX_S        = DX;
    localparam logic signed [IN_W:0]       X1_S        = X1_CODE;
    localparam logic signed [17:0]         INV_DX_K_S  = INV_DX_K;
    localparam logic signed [11:0]         INV_DX_S    = INV_DX;

    localparam int PROD_G_W  = (IN_W + 2) + 18;   // 22 + 18 = 40
    localparam int OFF_NUM_W = (IN_W + 2) + (IN_W + 1); // 42
    localparam int OFF_PROD_W= OFF_NUM_W + 12;    // 54

    logic signed [IN_W:0]         dy_s1;
    logic signed [IN_W+1:0]       num_g_s1;
    logic [IN_W-1:0]              y1_s1;
    logic                         v_s1;

    logic signed [PROD_G_W-1:0]   gprod_s2;
    logic signed [OFF_NUM_W-1:0]  offnum_s2;
    logic signed [OFF_PROD_W-1:0] offprod_s2;
    logic                         v_s2;

    wire signed [PROD_G_W-1:0]    gd_s2  = (gprod_s2 + (1 << (SHIFT_K-1))) >>> SHIFT_K;
    wire signed [OFF_PROD_W-1:0]  oc_s2  = (offprod_s2 + (1 << (SHIFT_O-1))) >>> SHIFT_O;
    wire signed [PROD_G_W-1:0]    gain_calc = 11'sd512 + gd_s2;   // 夹紧前先算，避免有符号->无符号转换

    logic [9:0]                   gain_s3;
    logic signed [11:0]           off_s3;
    logic                         v_s3;

    // Stage 1: dy = y2 - y1; num_g = dy - DX
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dy_s1    <= '0;
            num_g_s1 <= '0;
            y1_s1    <= '0;
            v_s1     <= 1'b0;
        end else if (start) begin
            dy_s1    <= $signed(y2) - $signed(y1);
            num_g_s1 <= ($signed(y2) - $signed(y1)) - DX_S;
            y1_s1    <= y1;
            v_s1     <= 1'b1;
        end else begin
            v_s1 <= 1'b0;
        end
    end

    // Stage 2: constant-multiply the two-point formulas
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gprod_s2  <= '0;
            offnum_s2 <= '0;
            offprod_s2<= '0;
            v_s2      <= 1'b0;
        end else begin
            v_s2 <= v_s1;
            if (v_s1) begin
                gprod_s2  <= num_g_s1 * INV_DX_K_S;
                offnum_s2 <= $signed(y1_s1) * DX_S
                             - dy_s1 * X1_S;
                offprod_s2<= ($signed(y1_s1) * DX_S
                              - dy_s1 * X1_S) * INV_DX_S;
            end
        end
    end

    // Stage 3: clamp and output
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            gain_s3  <= 10'd512;
            off_s3   <= 12'sd0;
            v_s3     <= 1'b0;
            error    <= 1'b0;
        end else begin
            v_s3 <= v_s2;
            if (v_s2) begin
                if (11'sd512 + gd_s2 < 0)        gain_s3 <= 10'd0;
                else if (11'sd512 + gd_s2 > 1023) gain_s3 <= 10'd1023;
                else                              gain_s3 <= gain_calc[9:0];

                if (oc_s2 < -2048)      off_s3 <= -12'sd2048;
                else if (oc_s2 > 2047)  off_s3 <= 12'sd2047;
                else                    off_s3 <= $signed(oc_s2[11:0]);
                error <= 1'b0;
            end
        end
    end

    assign gain_code   = gain_s3;
    assign offset_code = off_s3;
    assign done        = v_s3;

endmodule

`default_nettype wire
