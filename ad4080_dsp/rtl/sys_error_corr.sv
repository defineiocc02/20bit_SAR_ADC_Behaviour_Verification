//=============================================================================
// @file    sys_error_corr.sv
// @brief   补码输入的仿射系统校正与饱和事件。
// @details delta=(x*(GAIN-512)*512+2^23)>>>24，y=saturate(x+delta+OFFSET)。
//          默认 GAIN=512、OFFSET=0 为单位映射；clip 为非粘滞事件，与校正结果同拍。
// @project SAR20 / AD4080风格数字原型
// @author  项目维护者（贡献记录见版本库）
// @date    2026-11-05
// @version 由Git内容版本追溯，不单独声明器件版本
//-----------------------------------------------------------------------------
// 手册依据: 功能映射见 docs/ad4080_traceability.md，不声称器件逐拍等价。
// 时钟域: clk；valid、数据与系数同步。
// 复位: rst_n 异步低有效，无运行中 flush 输入。
// 延迟: 接受 valid 沿到输出 valid 经过两个完整 clk 间隔。
// 吞吐: 每个 clk 可连续接受一个样本。
// 可综合性: RTL 结构；目标库映射、约束、SRAM/PHY 与 STA 须单独验收。
// 依赖: 无子模块。
// 边界: 工程配置为 IN_W=20、OFFSET_W=12、GAIN_W=10、K_GAIN=512、SHIFT=24；
//     其他参数组合须单独验证。输入输出采用 20 bit 补码，gain 为直二进制，offset 为 ADC LSB 整数。
//     加半码后算术右移，半码向正无穷；输出饱和到 -2^19..2^19-1。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module sys_error_corr #(
    parameter int IN_W      = 20,  // IN_W: 补码样本位宽，工程配置为 20 bit。
    parameter int OFFSET_W  = 12,  // OFFSET_W: 补码偏移位宽，工程配置为 12 bit。
    parameter int GAIN_W    = 10,  // GAIN_W: 直二进制增益码位宽，工程配置为 10 bit。
    parameter int K_GAIN    = 512,  // K_GAIN: 增益增量乘数，须与定点映射一致。
    parameter int SHIFT     = 24  // SHIFT: 增益乘积右移位数，工程配置为 24。
) (
    input  logic                       clk,
    input  logic                       rst_n,
    input  logic                       valid_in,
    input  logic [IN_W-1:0]            x,             // 二进制补码
    input  logic signed [OFFSET_W-1:0] offset_code,
    input  logic [GAIN_W-1:0]          gain_code,
    output logic [IN_W-1:0]            y,             // 二进制补码
    output logic                       valid_out,
    output logic                       clip_low,
    output logic                       clip_high          // 高于 +FS 饱和
);

    // 20 bit 有符号样本乘以 11 bit 有符号 (gain-512)，结果使用 31 bit。
    localparam int PROD_W  = IN_W + GAIN_W + 1;        // 31
    localparam int PRODK_W = PROD_W + 11;              // 42
    localparam int SUM_W   = IN_W + 2;                 // 22
    localparam logic signed [IN_W-1:0] MAX_CODE =   (1 << (IN_W-1)) - 1;   // +524287
    localparam logic signed [IN_W-1:0] MIN_CODE = - (1 << (IN_W-1));       // -524288
    // NOTE: 512 在 10 bit 有符号数中变为 -512（10'sd512 == -512），
    //     因此基准常数须使用 11 bit 有符号表示。
    localparam logic signed [10:0]     K_GAIN_S = K_GAIN[10:0];

    // 第一级寄存样本、偏移与增益乘积，第二级计算饱和前的三项和。
    logic signed [IN_W-1:0]          x_s1;
    logic signed [OFFSET_W-1:0]      off_s1;
    logic signed [PROD_W-1:0]        prod_s1;
    logic                            v_s1;


    logic signed [PRODK_W-1:0]       prodk_s2;
    logic signed [17:0]              corr_s2;
    logic signed [SUM_W-1:0]         sum_s2;
    logic signed [SUM_W-1:0]         x_ext, off_ext, sum_comb;
    logic                            v_s2, lo_s2, hi_s2;

    // 显式符号扩展，保证三数相加在位宽与符号上无歧义。
    assign x_ext    = $signed({{(SUM_W-IN_W){x_s1[IN_W-1]}}, x_s1});
    assign off_ext  = $signed({{(SUM_W-OFFSET_W){off_s1[OFFSET_W-1]}}, off_s1});
    wire signed [SUM_W-1:0] corr_ext = $signed({{(SUM_W-18){corr_s2[17]}}, corr_s2});
    assign sum_comb = x_ext + corr_ext + off_ext;

    assign prodk_s2 = prod_s1 * K_GAIN_S;
    // WAIVER(width-truncate): 增益项 |delta| <= 2^19*1.56% = 8176，18 bit 足够。
    //     此处显式截为 18 bit；默认参数下高位仅为符号扩展。
    // verilator lint_off WIDTHTRUNC
    assign corr_s2  = (prodk_s2 + (1 << (SHIFT-1))) >>> SHIFT;   // 18 bit 足够（|delta|<=8176）
    // verilator lint_on WIDTHTRUNC

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            x_s1    <= '0;
            off_s1  <= '0;
            prod_s1 <= '0;
            v_s1    <= 1'b0;
        end else if (valid_in) begin
            x_s1    <= $signed(x);
            off_s1  <= offset_code;
            // WAIVER(arith-width): 20-bit x 11-bit 有符号乘，结果 31 bit；
            // 不要把两个操作数都扩到 31 bit，否则综合会多出大量无效部分积。
            // verilator lint_off WIDTHEXPAND
            prod_s1 <= $signed(x) * ($signed({1'b0, gain_code}) - 11'sd512);
            // verilator lint_on WIDTHEXPAND
            v_s1    <= 1'b1;
        end else begin
            v_s1    <= 1'b0;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sum_s2    <= '0;
            v_s2      <= 1'b0;
            lo_s2     <= 1'b0;
            hi_s2     <= 1'b0;
            y         <= '0;
            valid_out <= 1'b0;
            clip_low  <= 1'b0;
            clip_high <= 1'b0;
        end else begin
            v_s2 <= v_s1;
            if (v_s1) begin
                sum_s2 <= sum_comb;
                lo_s2  <= sum_comb < $signed({{(SUM_W-IN_W){MIN_CODE[IN_W-1]}}, MIN_CODE});
                hi_s2  <= sum_comb > $signed({{(SUM_W-IN_W){MAX_CODE[IN_W-1]}}, MAX_CODE});
            end

            if (v_s2) begin
                if (lo_s2) begin
                    y         <= {1'b1, {(IN_W-1){1'b0}}};   // MIN_CODE
                    clip_low  <= 1'b1;
                    clip_high <= 1'b0;
                end else if (hi_s2) begin
                    y         <= {1'b0, {(IN_W-1){1'b1}}};   // MAX_CODE
                    clip_low  <= 1'b0;
                    clip_high <= 1'b1;
                end else begin
                    y         <= sum_s2[IN_W-1:0];
                    clip_low  <= 1'b0;
                    clip_high <= 1'b0;
                end
                valid_out <= 1'b1;
            end else begin
                valid_out <= 1'b0;
                clip_low  <= 1'b0;
                clip_high <= 1'b0;
            end
        end
    end

endmodule

`default_nettype wire
