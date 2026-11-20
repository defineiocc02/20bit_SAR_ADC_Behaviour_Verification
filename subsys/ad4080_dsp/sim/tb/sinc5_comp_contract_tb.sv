`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : sinc5_comp_contract_tb.sv
// 模块名     : sinc5_comp_contract_tb
// 版本       : v1.0
// 功能       : 核对共用 CIC 后的实际 PF1 偶相输出、停样及清同步。
//
// 设计说明   :
//   * 用实际前级 SINC5 每个结果驱动独立 64 位全 35 项点积。直接使用
//     全序历史和扩展系数，和生产叶的对称配对及归约树独立。
//   * 前史为零。第二个、第四个及其后偶数前级结果均为真实数字。
//     其外部 CNV 发布时刻另由产品相位台架验证。
//   * 在已经得到有效候选、最后一个 FIR 寄存器尚未提交时清同步。
//   * 关闭 PF1 后共用 CIC 继续输出 SINC5 速率，且没有 PF1 数据。
// 变更记录   : 2026-11-20 迁移到同一份 35 抽头 Q22 系数及新优先级合同。
// =============================================================================
module sinc5_comp_contract_tb;
    `include "sinc5_comp_coeff_lut.svh"

    // =========================================================================
    // 1. 唯一实际数值叶与全点积参考
    // =========================================================================
    logic clk = 0, rst_n = 0, filter_sync = 0, valid_in = 0;
    logic pf1_enable_i = 1;
    logic [19:0] data_in = 0;
    logic [3:0] dec_sel = 4'd1;  // 测试基础 N=4，总抽取 8
    wire [19:0] data_out, sinc5_data_o;
    wire valid_out, sinc5_valid_o, processing_reject;
    sinc5_comp dut (.*);
    always #5 clk <= ~clk;

    int cycle, index, s5_total, compares, changed, positives, negatives, checks;
    int previous;
    longint signed history [0:PMODE_COMP_TAPS-2]; // 模型保留未经配对的历史
    longint signed expected_queue[$];
    int due_queue[$];

    task automatic require(input bit yes, input string why);
        checks++;
        if (!yes) $fatal(1,"COMP: %s",why);
    endtask

    function automatic longint signed floor_div(input longint signed n, d);
        // RTL 的算术右移会向负无穷。这里用整式写成独立显式地板商。
        return n >= 0 ? n/d : -((-n+d-1)/d);
    endfunction

    function automatic longint signed coeff(input int tap);
        int mirrored;
        // 系数文件只存一半。模型独立按原始时间顺序访问每一项。
        mirrored = tap < PMODE_COMP_TAPS/2 ? tap : PMODE_COMP_TAPS-1-tap;
        return longint'($signed(sinc5_comp_coeff(3'd1,mirrored)));
    endfunction

    initial forever begin
        longint signed current, acc, expected, got_expected;
        int due;
        @(posedge clk);
        cycle++;
        if (!rst_n || filter_sync) begin
            expected_queue.delete();
            due_queue.delete();
            index = 0;
            foreach (history[i]) history[i] = 0;
        end else if (sinc5_valid_o) begin
            current = longint'($signed(sinc5_data_o));
            s5_total++;
            if (s5_total > 1 && current != longint'(previous)) changed++;
            previous = int'(current);
            // 读取的是叶输入端本拍看到的先前 SINC5 输出，点积下一核心拍退役。
            if (pf1_enable_i && index%2 == 1) begin
                acc = current*coeff(0);
                for (int j=1; j<PMODE_COMP_TAPS; j++) acc += history[j-1]*coeff(j);
                expected = floor_div(acc+(64'sd1 << (PMODE_COMP_COEF_FRAC-1)),
                                     64'sd1 << PMODE_COMP_COEF_FRAC);
                if (expected > 524287) expected = 524287;
                if (expected < -524288) expected = -524288;
                expected_queue.push_back(expected);
                due_queue.push_back(cycle+1);
            end
            for (int j=PMODE_COMP_TAPS-2; j>0; j--) history[j] = history[j-1];
            history[0] = current;
            index++;
        end
        #1;
        if (rst_n) begin
            require(!processing_reject,"a canonical coefficient table must be bound");
            if (filter_sync || !pf1_enable_i)
                require(!valid_out,"sync or disabled PF1 leaked an output");
            else if (valid_out) begin
                require(expected_queue.size() > 0 && due_queue.size() > 0,
                        "output without an even SINC5 sample");
                got_expected = expected_queue.pop_front();
                due = due_queue.pop_front();
                require(due == cycle,"registered full FIR latency");
                require(longint'($signed(data_out)) == got_expected,"independent signed FIR dot product");
                compares++;
                if ($signed(data_out) > 0) positives++;
                if ($signed(data_out) < 0) negatives++;
            end else if (due_queue.size() > 0)
                require(due_queue[0] > cycle,"missing expected PF1 output");
        end
    end

    // =========================================================================
    // 2. 动态码、在途组被清除和单独观察 SINC5
    // =========================================================================
    task automatic feed(input int n);
        // 正负动态码跨越完整 FIR 支持。不规则空拍与最后的整组停样均要排空。
        for (int i=0; i<n; i++) begin
            @(negedge clk);
            valid_in = 1;
            data_in = 20'((i%128<64) ? (150000-(i%64)*3000) : (-150000+(i%64)*3000));
            if (i%5 == 0) begin
                @(negedge clk);
                valid_in = 0;
            end
        end
        @(negedge clk);
        valid_in = 0;
        repeat (12) @(negedge clk);
    endtask

    initial begin
        int old_compare, old_s5, waited;
        repeat (3) @(negedge clk);
        rst_n = 1;
        filter_sync = 1;
        @(negedge clk);
        filter_sync = 0;
        old_compare = compares;
        old_s5 = s5_total;
        feed(512);
        require(s5_total-old_s5 == 128 && compares-old_compare == 64,
                "exact doubled rate after stopping at a whole group");

        // 只有确认有一笔待提交的真输出后才清同步，避免空流水的假阳性。
        waited = 0;
        while (expected_queue.size() == 0 && waited < 200) begin
            @(negedge clk);
            valid_in = 1;
            data_in = 20'd40000;
            waited++;
        end
        require(expected_queue.size() > 0,"sync cancellation fixture is empty");
        filter_sync = 1;
        @(negedge clk);
        filter_sync = 0;
        valid_in = 0;
        repeat (6) @(negedge clk);
        require(expected_queue.size() == 0,"sync did not reset the reference epoch");

        old_compare = compares;
        old_s5 = s5_total;
        feed(128);
        require(s5_total-old_s5 == 32 && compares-old_compare == 16,
                "fresh epoch even phase after sync");
        @(negedge clk);
        filter_sync = 1;
        pf1_enable_i = 0;
        @(negedge clk);
        filter_sync = 0;
        old_s5 = s5_total;
        feed(64);
        require(s5_total-old_s5 == 16 && compares == 80,
                "SINC5 remains live without PF1");
        require(positives > 10 && negatives > 10 && changed > 50,
                "nonvacuous signed dynamic FIR coverage");
        $display("SINC5_COMP_CONTRACT_COMPLETE fir_compares=%0d positive=%0d negative=%0d changed_s5=%0d sync_cancels=1 checks=%0d",
                 compares,positives,negatives,changed,checks);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1,"COMP watchdog");
    end
endmodule
`default_nettype wire
