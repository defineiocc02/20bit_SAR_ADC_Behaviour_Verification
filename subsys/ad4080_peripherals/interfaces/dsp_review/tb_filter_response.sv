`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : tb_filter_response.sv
// 模块名     : tb_filter_response
// 版本       : v1.0
// 功能       : 用正弦和有符号动态码驱动现有滤波 RTL，采集实际有效输出。
//
// 设计说明   :
//   * mode 1 测 SINC1；mode 2 读共用 CIC 的 SINC5 端口；mode 3
//     读 PF1 有效码。三个模式的周期和码值由主机独立评估。
//   * 正弦频率以输入采样频率 Fs 归一。测试时钟仅用于数值边沿计数。
//   * 动态向量加入空拍，最后一个完整抽取组不借后续输入排空。
//     同步清除和异步复位各在已经发出最后一笔组数据后取消待发结果。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================
module tb_filter_response;
    // =========================================================================
    // 1. 唯一在测的 SINC1 和共用 SINC5/PF1 路径
    // =========================================================================
    logic clk = 0, rst_n = 0, filter_sync = 0, valid_in = 0;
    logic [19:0] data_in;
    logic [3:0] dec_sel;
    logic [1:0] selected_mode;
    logic [19:0] sinc1_code, sinc5_code, pf1_code;
    logic sinc1_valid, sinc5_valid, pf1_valid, rejected;
    wire out_valid = (selected_mode == 1) ? sinc1_valid :
                     (selected_mode == 2) ? sinc5_valid : pf1_valid;
    wire [19:0] out_code = (selected_mode == 1) ? sinc1_code :
                           (selected_mode == 2) ? sinc5_code : pf1_code;

    // 两条实际数值路径均使用同一个输入时间轴，非选中链不接受样本。
    sinc1_decim u_sinc1 (
        .clk, .rst_n, .filter_sync, .valid_in(valid_in && selected_mode == 1),
        .data_in, .dec_sel, .data_out(sinc1_code), .valid_out(sinc1_valid)
    );
    sinc5_comp u_cic_pf1 (
        .clk, .rst_n, .filter_sync, .valid_in(valid_in && selected_mode != 1),
        .data_in, .dec_sel, .pf1_enable_i(selected_mode == 3),
        .sinc5_data_o(sinc5_code), .sinc5_valid_o(sinc5_valid),
        .data_out(pf1_code), .valid_out(pf1_valid), .processing_reject(rejected)
    );
    always #1 clk <= ~clk;

    // =========================================================================
    // 2. 记录可核对的实际数值、数量和核心时序
    // =========================================================================
    int output_file, trial_id, discarded_outputs;
    int core_cycle, first_core_cycle, input_count, output_count;
    bit active = 0;
    // 计数在同一过程兼顾输入沿和非阻塞更新后的结果，保留气泡的核心周期。
    initial forever begin
        @(posedge clk);
        core_cycle++;
        if (active && rst_n && !filter_sync && valid_in) begin
            if (input_count == 0) first_core_cycle = core_cycle;
            input_count++;
        end
        #0.1;
        if (active) begin
            if (rejected) $fatal(1, "unexpected filter numerical rejection");
            if (out_valid) begin
                output_count++;
                if (output_count > discarded_outputs)
                    $fdisplay(output_file, "%0d %0d %0d %0d", trial_id,
                              output_count, core_cycle-first_core_cycle+1, $signed(out_code));
            end
        end
    end

    function automatic logic [19:0] dynamic_code(input int n);
        // 正负轨端和低频相关码同时出现，避免只有直流时位宽/历史故障被掩盖。
        if (n%79 == 0) return 20'h80000;
        if (n%79 == 1) return 20'h7ffff;
        return 20'(((n*30119+(n%37)*7919)%880000)-440000);
    endfunction

    initial begin : stimulus
        int input_file, mode, code, decimation, collect, kind, samples, expected_outputs, trials;
        real f_over_fs, phase;
        input_file = $fopen("trials.txt", "r");
        output_file = $fopen("observed.txt", "w");
        if (input_file == 0 || output_file == 0) $fatal(1,"missing sweep stimulus or output");
        trials = 0;
        repeat (3) @(negedge clk);
        rst_n = 1;
        while ($fscanf(input_file, "%d %d %d %d %d %d %d %f %f", trial_id, mode, code,
                       decimation, discarded_outputs, collect, kind, f_over_fs, phase) == 9) begin
            if (mode < 1 || mode > 3 || code < 0 || code > 15 || decimation < 2
                || collect < 1 || discarded_outputs < 0 || f_over_fs < 0.0 || f_over_fs > 0.5)
                $fatal(1, "invalid sweep request %0d", trial_id);
            @(negedge clk);
            selected_mode = 2'(mode);
            dec_sel = 4'(code);
            filter_sync = 1;
            valid_in = 0;
            @(negedge clk);
            filter_sync = 0;
            input_count = 0;
            output_count = 0;
            first_core_cycle = 0;
            active = 1;
            // 务必恰在完整抽取边界停样，覆盖新的排空合同。
            samples = kind >= 2 ? decimation : (discarded_outputs+collect)*decimation;
            for (int n=0; n<samples; n++) begin
                @(negedge clk);
                if (kind == 1 && n%11 == 0) begin
                    valid_in = 0;
                    @(negedge clk);
                end
                valid_in = 1;
                data_in = kind == 0
                    ? 20'(int'($floor(262144.0*$sin(6.283185307179586*f_over_fs*n+phase)+0.5)))
                    : dynamic_code(n);
            end
            @(negedge clk);
            valid_in = 0;
            expected_outputs = discarded_outputs+collect;
            if (kind >= 2) begin
                // 下一沿恰可产生第一个 SINC1 结果，较深的链仍在途。
                expected_outputs = 0;
                if (kind == 2) begin
                    filter_sync = 1;
                    valid_in = 1;  // 同步同沿的新样本应被清流优先吞掉。
                end else begin
                    #0.2;
                    rst_n = 0;
                end
                @(negedge clk);
                rst_n = 1;
                filter_sync = 0;
                valid_in = 0;
            end
            repeat (12) @(negedge clk);
            if (input_count != samples || output_count != expected_outputs)
                $fatal(1,"trial %0d accepted %0d/%0d outputs %0d/%0d",
                       trial_id,input_count,samples,output_count,expected_outputs);
            active = 0;
            trials++;
        end
        if (!$feof(input_file)) $fatal(1,"malformed filter sweep source");
        $fclose(input_file);
        $fclose(output_file);
        $display("FILTER_RESPONSE_TRACE_COMPLETE trials=%0d",trials);
        $finish;
    end
endmodule
`default_nettype wire
