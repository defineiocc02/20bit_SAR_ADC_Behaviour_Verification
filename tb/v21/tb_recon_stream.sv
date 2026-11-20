`timescale 1ns/1ps
// 来源：独立复核 isscc9p8_rework_decision_20261116 的逐拍重构测试台。
// 作用：用**连续受理**激励（相邻拍不同笔）暴露流水取数点错误。
//       原 tb_i4_recon 保持同一总线 7 拍，掩盖了这类问题。
//       配对工具：tools/recon_oracle.py；入口：tools/check_recon_stream.sh

// =============================================================================
// 文件名     : tb_recon_stream.sv
// 模块名     : tb_recon_stream
// 功能       : 对固定版本重构模块施加逐拍输入并抓取实际退休和级间快照。
// 设计说明   : 激励包含相邻接受沿与无效沿总线变化。期望值由独立整数程序计算。
//              在上升沿前后分开采集倒数结果，以便查清同沿非阻塞赋值的归属。
// 变更记录   : 增加有符号截断边界探针，统一逐拍 CSV 的列序与文件结束条件。
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

    // ---- 有符号截断边界 ------------------------------------------------------
    // 合同内的真实输入到不了 32 位溢出点。独立探针仍从端口发送真实 tag/valid，
    // 仅在 N/I 汇合后替换一次 N，令 X 精确等于待检查的边界；不计入端口事务轨迹。
    logic signed [95:0] injected_n;
    task automatic check_limit(input logic signed [95:0] target_x, input logic bad_alpha,
                               input logic [31:0] tag, input logic [19:0] code,
                               input logic sat, input logic err);
        @(negedge clk);
        in_valid = 1'b1; in_tag = tag; raw_word = 13'd2048; offset_q24 = 27'sd768;
        gain_q30 = 64'd32 << 30; post_q30 = 64'd16 << 30; pre_q30 = '0;
        alpha_q30 = bad_alpha ? 32'd1 : 32'd1073741824;
        @(negedge clk);
        in_valid = 1'b0;
        repeat (4) @(negedge clk);
        // G=32×2^30 给 I=2^25，因此 N=X×2^11 时算术右移精确恢复 X。
        // 先验明身份和 I 再注入，延迟变化时应失败，避免把探针施到另一笔。
        if ({dut.v2_q, dut.tag2_q} !== {1'b1, tag} || dut.i2_q !== 64'd33554432)
            $fatal(1, "S3 probe missed join tag=%0d", tag);
        injected_n = target_x <<< 11;
        force dut.n2_q = injected_n;
        @(negedge clk);
        release dut.n2_q;
        @(posedge clk); #1;
        if ({out_valid, out_tag, out_code, out_sat, out_err} !== {1'b1, tag, code, sat, err})
            $fatal(1, "S3 boundary tag=%0d X=%0d code=%h sat=%b err=%b", tag,
                   target_x, out_code, out_sat, out_err);
    endtask

    // ---------------------------------------------------------------- 2. 激励和事件记录
    initial begin
        int fd_in, fd_out, parsed, rows;
        int scenario, tick, rv, valid, tag, raw, offset;
        longint unsigned alpha, gain, post, pre;
        string vec_path, trace_path, line, extra;
        logic ri_before_valid, ri_before_err;
        logic [26:0] ri_before_i;
        rows = 0;
        if (!$value$plusargs("VEC=%s", vec_path)) $fatal(1, "missing +VEC");
        if (!$value$plusargs("TRACE=%s", trace_path)) $fatal(1, "missing +TRACE");
        fd_in = $fopen(vec_path, "r");
        fd_out = $fopen(trace_path, "w");
        if (fd_in == 0 || fd_out == 0) $fatal(1, "cannot open input/trace");
        // 拼接字符串形成的是打包整数，模拟器可以把整个表头打印为一个十进制数。
        // 每段独立传入字面格式串，最后一段才换行，CSV 列序与 Python 检查器一致。
        $fwrite(fd_out, "scenario,tick,reset_n,in_valid,in_tag,raw,b,alpha,G,Wm,Wp,");
        $fwrite(fd_out, "s1b_valid,s1b_tag,Z,s2a_valid,s2a_tag,N,ri_valid_pre,ri_I_pre,ri_err_pre,");
        $fwrite(fd_out, "ri_valid_post,ri_I_post,join_valid,join_tag,join_N,join_I,");
        $fdisplay(fd_out, "s3_valid,s3_tag,X,out_valid,out_tag,out_code,out_sat,out_err");
        // 以成功读到一行作为循环条件。完整的最后一行和 EOF 分开处理，
        // 多余字段、缺项或行内乱码必须失败，否则会错用上一行的总线。
        while ($fgets(line, fd_in) != 0) begin
            parsed = $sscanf(line, "%d %d %d %d %d %d %d %d %d %d %d %s",
                scenario, tick, rv, valid, tag, raw, offset, alpha, gain, post, pre, extra);
            if (parsed != 11)
                $fatal(1, "malformed vector after %0d rows (%0d fields)", rows, parsed);
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
            $fwrite(fd_out, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,",
                scenario, tick, rst_n, in_valid, in_tag, raw_word, offset_q24,
                alpha_q30, gain_q30, post_q30, pre_q30);
            $fwrite(fd_out, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,",
                dut.v1_q, dut.tag1_q, dut.z1_q, dut.v2a_q, dut.tag2a_q, dut.n2a_q,
                ri_before_valid, ri_before_i, ri_before_err);
            $fwrite(fd_out, "%0d,%0d,%0d,%0d,%0d,%0d,",
                dut.ri_valid, dut.ri_i, dut.v2_q, dut.tag2_q, dut.n2_q, dut.i2_q);
            $fdisplay(fd_out, "%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                dut.v3_q, dut.tag3_q, dut.x3_q,
                out_valid, out_tag, out_code, out_sat, out_err);
            rows++;
        end
        // 最后一次采样在测试窗内完成。退出时以实际写入数校验采集完整性。
        $fclose(fd_in);
        $fclose(fd_out);
        if (rows == 0) $fatal(1, "empty stimulus");
        // 可表示的两个端点走正常出码并饱和；各再跨一步应改走错误通路。
        // 上游真错必须保留，并须允许其后的合法中点正常退休。
        check_limit(-96'sd2147483648, 0, 801, 20'h00000, 1, 0);
        check_limit( 96'sd2147483647, 0, 802, 20'hfffff, 1, 0);
        check_limit(-96'sd2147483649, 0, 803, 20'h00000, 0, 1);
        check_limit( 96'sd2147483648, 0, 804, 20'h00000, 0, 1);
        check_limit( 96'sd0,          1, 805, 20'h00000, 0, 1);
        check_limit( 96'sd0,          0, 806, 20'h80000, 0, 0);
        $display("RECON_S3_LIMITS_PASS checks=6");
        $display("RECON_STREAM_CAPTURE_COMPLETE rows=%0d", rows);
        $finish;
    end
endmodule
