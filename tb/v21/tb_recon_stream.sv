`timescale 1ns/1ps
// 来源：独立复核 isscc9p8_rework_decision_20261116/recon/6bf4a2a-vcs-av3l9epb/
//       snapshot/tb_recon_stream.sv（原样入库）
// 作用：用**连续受理**激励（相邻拍不同笔）暴露流水取数点错误。
//       原 tb_i4_recon 保持同一总线 7 拍，掩盖了这类问题。
//       配对工具：tools/recon_oracle.py；入口：tools/check_recon_stream.sh

// =============================================================================
// 文件名     : tb_recon_stream.sv
// 模块名     : tb_recon_stream
// 功能       : 对固定版本重构模块施加逐拍输入并抓取实际退休和级间快照。
// 设计说明   : 激励包含相邻接受沿与无效沿总线变化。期望值由独立整数程序计算。
//              在上升沿前后分开采集倒数结果，以便查清同沿非阻塞赋值的归属。
// 变更记录   : 本次有界只读核验的新测试台。
// =============================================================================
module tb_recon_stream;
    // ---------------------------------------------------------------- 1. 时钟和接口
    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rst_n = 1'b0;
    logic in_valid = 1'b0;
    logic [12:0] raw_word = '0;
    logic signed [26:0] offset_q24 = '0;
    logic [31:0] alpha_q30 = '0;
    logic [63:0] gain_q30 = '0;
    logic [63:0] post_q30 = '0;
    logic [63:0] pre_q30 = '0;
    logic [31:0] in_tag = '0;
    wire out_valid, out_sat, out_err;
    wire [19:0] out_code;
    wire [31:0] out_tag;

    recon_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .in_valid(in_valid), .adc2_raw(raw_word),
        .Wm_q30(post_q30), .G_q30(gain_q30), .Wp_q30(pre_q30),
        .alpha_q30(alpha_q30), .b_q24(offset_q24),
        .in_epoch(8'd3), .in_tag(in_tag), .cfg_epoch(8'd3),
        .out_valid(out_valid), .code_o(out_code),
        .out_sat(out_sat), .out_err(out_err), .out_tag(out_tag)
    );

    // ---------------------------------------------------------------- 2. 激励和事件记录
    initial begin
        int fd_in, fd_out, parsed, rows;
        int scenario, tick, rv, valid, tag, raw, offset;
        longint unsigned alpha, gain, post, pre;
        string vec_path, trace_path;
        logic ri_before_valid, ri_before_err;
        logic [26:0] ri_before_i;
        rows = 0;
        if (!$value$plusargs("VEC=%s", vec_path)) $fatal(1, "missing +VEC");
        if (!$value$plusargs("TRACE=%s", trace_path)) $fatal(1, "missing +TRACE");
        fd_in = $fopen(vec_path, "r");
        fd_out = $fopen(trace_path, "w");
        if (fd_in == 0 || fd_out == 0) $fatal(1, "cannot open input/trace");
        $fdisplay(fd_out, {"scenario,tick,reset_n,in_valid,in_tag,raw,b,alpha,G,Wm,Wp,",
            "s1b_valid,s1b_tag,Z,s2a_valid,s2a_tag,N,ri_valid_pre,ri_I_pre,ri_err_pre,",
            "ri_valid_post,ri_I_post,join_valid,join_tag,join_N,join_I,",
            "s3_valid,s3_tag,X,out_valid,out_tag,out_code,out_sat,out_err"});
        while (!$feof(fd_in)) begin
            parsed = $fscanf(fd_in, "%d %d %d %d %d %d %d %d %d %d %d",
                scenario, tick, rv, valid, tag, raw, offset, alpha, gain, post, pre);
            if (parsed != 11) begin
                if ($feof(fd_in) && parsed < 0) break;
                $fatal(1, "malformed vector after %0d rows (%0d fields)", rows, parsed);
            end
            // 在下降沿换输入，下一上升沿前保留整半拍建立时间。
            @(negedge clk);
            rst_n = 1'(rv);
            in_valid = 1'(valid);
            in_tag = 32'(tag);
            raw_word = 13'(raw);
            offset_q24 = 27'(offset);
            alpha_q30 = 32'(alpha);
            gain_q30 = gain;
            post_q30 = post;
            pre_q30 = pre;
            @(posedge clk);
            // 同一沿更新的寄存器只能消费沿前值，不能用沿后结果替代。
            ri_before_valid = dut.ri_valid;
            ri_before_err = dut.ri_err;
            ri_before_i = dut.ri_i;
            #1;
            $fdisplay(fd_out, {"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,",
                "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,",
                "%0d,%0d,%0d,%0d,%0d,%0d,",
                "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d"},
                scenario, tick, rst_n, in_valid, in_tag, raw_word, offset_q24,
                alpha_q30, gain_q30, post_q30, pre_q30,
                dut.v1_q, dut.tag1_q, dut.z1_q, dut.v2a_q, dut.tag2a_q, dut.n2a_q,
                ri_before_valid, ri_before_i, ri_before_err,
                dut.ri_valid, dut.ri_i, dut.v2_q, dut.tag2_q, dut.n2_q, dut.i2_q,
                dut.v3_q, dut.tag3_q, dut.x3_q,
                out_valid, out_tag, out_code, out_sat, out_err);
            rows++;
        end
        // 最后一次采样在测试窗内完成。退出时以实际写入数校验采集完整性。
        $fclose(fd_in);
        $fclose(fd_out);
        if (rows == 0) $fatal(1, "empty stimulus");
        $display("RECON_STREAM_CAPTURE_COMPLETE rows=%0d", rows);
        $finish;
    end
endmodule
