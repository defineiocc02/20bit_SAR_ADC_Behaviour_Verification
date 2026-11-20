`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : calibration_verify_tb.sv
// 模块名     : calibration_verify_tb
// 版本       : v1.0
// 功能       : 逐周期回放系统校正寄存器码和样本，采集冻结 DSP 的实际输出。
//
// 设计说明   :
//   * 用与 sar20_ad4080_top 相同的 MSB 翻转接入 20 位偏移二进制采样码。
//   * 校正计算全部在同仓冻结的 sys_error_corr 中执行；逐拍整数参考及误差
//     统计由测试脚本提供。无效拍仍采集 valid、拒绝和饱和，便于检查清流水。
//   * 输入样本与寄存器码在下降沿更新，随后在上升沿接受；采集延迟 1 ns，
//     以观测流水寄存器实际提交的结果，而非上一拍值。
//
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================
module calibration_verify_tb #(
    parameter int MAP_K = 1,   // 本工程演示增益分子
    parameter int MAP_SHIFT = 15 // 本工程演示增益小数位数
);
    // =========================================================================
    // 1. 冻结 DSP 的工程码制接口
    // =========================================================================
    logic clk = 1'b0, rst_n = 1'b0;
    logic valid_in = 1'b0, cancel = 1'b0;
    logic [19:0] raw_ob = '0;
    logic signed [11:0] offset_code = '0; // OFFSET 来自写寄存器的原始 12 位
    logic [9:0] gain_code = 10'd512;
    logic [19:0] x, y;
    logic valid_out, clip_low, clip_high, processing_reject;

    // [接线] 后端 SPI 码制和数字重构码制之间仅翻转符号位，与生产适配层一致。
    assign x = {~raw_ob[19], raw_ob[18:0]};
    always #5 clk <= ~clk;

    sys_error_corr #(
        .P_GAIN_MAP_VALID(1'b1), .K_GAIN(MAP_K), .SHIFT(MAP_SHIFT),
        .P_GAIN_CODE0_SUPPORTED(1'b0)
    ) u_frozen (.*);

    // =========================================================================
    // 2. 回放及观察流水每拍结果
    // =========================================================================
    initial begin : replay_trace
        int input_file, output_file;
        int reset_value, valid_value, cancel_value, raw_value, offset_value, gain_value;
        int row_index;

        input_file = $fopen("stimulus.txt", "r");
        output_file = $fopen("observed.txt", "w");
        if (input_file == 0 || output_file == 0) $fatal(1, "calibration replay file open failed");
        row_index = 0;
        while ($fscanf(input_file, "%d %d %d %d %d %d", reset_value, valid_value,
                       cancel_value, raw_value, offset_value, gain_value) == 6) begin
            // [输入] 先检查取值宽度，避免坏刺激被低位截断后误报逐码一致。
            if (reset_value < 0 || reset_value > 1 || valid_value < 0 || valid_value > 1
                || cancel_value < 0 || cancel_value > 1 || raw_value < 0 || raw_value > 20'hfffff
                || offset_value < 0 || offset_value > 12'hfff || gain_value < 0 || gain_value > 10'h3ff)
                $fatal(1, "calibration replay bad vector at row %0d", row_index);
            @(negedge clk);
            rst_n       = 1'(reset_value);
            valid_in    = 1'(valid_value);
            cancel      = 1'(cancel_value);
            raw_ob      = 20'(raw_value);
            offset_code = 12'(offset_value);
            gain_code   = 10'(gain_value);
            @(posedge clk);
            #1;
            $fdisplay(output_file, "%0d %0d %05h %05h %0d %0d %0d", row_index,
                      valid_out, y, {~y[19], y[18:0]}, clip_low, clip_high, processing_reject);
            row_index++;
        end
        if (!$feof(input_file)) $fatal(1, "calibration replay incomplete input at row %0d", row_index);
        $fclose(input_file);
        $fclose(output_file);
        $display("RTL_CALIBRATION_TRACE cycles=%0d", row_index);
        $finish;
    end
endmodule
`default_nettype wire
