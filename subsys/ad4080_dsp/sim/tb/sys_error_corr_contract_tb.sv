`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : sys_error_corr_contract_tb.sv
// 模块名     : sys_error_corr_contract_tb
// 版本       : v1.0
// 功能       : 核对先加偏置后乘增益的整数序列及原有提交时序。
//
// 设计说明   :
//   * 期望值由带符号有理数对整和舍入得到，独立于 RTL 的增益增量写法。
//   * 既试单位增益和常规绑定，也可覆盖最大 K、最小 SHIFT、合法零码。
//   * 偏置超过 20 位轨端后可经衰减回到范围内，另测末端两侧才饱和。
//   * 同沿复位或取消优先于新 valid 以及到期样本。
//
// 变更记录   : 2026-11-20 对照手册图 96 并补码宽、半步和重启向量。
// =============================================================================
module sys_error_corr_contract_tb #(
    parameter int KG = 3,    // 工程增益分子
    parameter int SH = 13,   // 工程增益小数位数
    parameter bit ALLOW_ZERO = 1'b0 // 定向覆盖可显式绑定的零码
);
    // =========================================================================
    // 1. 共用激励和独立整式参考
    // =========================================================================
    localparam int MIN_CODE = -524288;
    localparam int MAX_CODE = 524287;
    localparam longint signed MIN_WIDE = longint'(MIN_CODE);
    localparam longint signed MAX_WIDE = longint'(MAX_CODE);
    localparam longint signed UNIT = 64'sd1 << SH;
    logic clk = 1'b0, rst_n = 1'b0, valid_in = 1'b0, cancel = 1'b0;
    logic [19:0] x = '0;                    // 以实际 20 位线码驱动
    logic signed [11:0] offset_code = '0;   // 模拟同沿寄存器值
    logic [9:0] gain_code = 10'd512;
    wire [19:0] y, y_unbound;
    wire valid_out, clip_low, clip_high, processing_reject;
    wire unbound_valid, unbound_low, unbound_high, unbound_reject;
    int checks = 0, compares = 0, rejects = 0, cancels = 0;

    always #5 clk <= ~clk;

    sys_error_corr #(
        .P_GAIN_MAP_VALID(1'b1), .K_GAIN(KG), .SHIFT(SH), .P_GAIN_CODE0_SUPPORTED(ALLOW_ZERO)
    ) dut (.*);

    // 同输入的无映射实例只处理单位增益，仍需正常支持带符号 OFFSET。
    sys_error_corr missing (
        .clk, .rst_n, .valid_in, .cancel, .x, .offset_code, .gain_code,
        .y(y_unbound), .valid_out(unbound_valid), .clip_low(unbound_low),
        .clip_high(unbound_high), .processing_reject(unbound_reject)
    );

    function automatic longint signed floor_div(input longint signed n, d);
        // SV 负整数默认向零截断。用数学地板求值以覆盖负半步的方向。
        return n >= 0 ? n / d : -((-n + d - 1) / d);
    endfunction

    task automatic require(input bit condition, input string reason);
        checks++;
        if (!condition) $fatal(1, "CORR: %s", reason);
    endtask

    task automatic sample(input int xi, gain, off);
        longint signed biased, raw, expected, unity_expected;
        bit low, high, rejected;

        biased = longint'(xi) + longint'(off);
        // 直接取 (x+O)*g 的整式分子，避免与 RTL 的增量截位共用错误。
        raw = floor_div(biased * (UNIT + (longint'(gain) - 512) * KG) + UNIT/2, UNIT);
        low = raw < MIN_WIDE;
        high = raw > MAX_WIDE;
        expected = low ? MIN_WIDE : (high ? MAX_WIDE : raw);
        unity_expected = biased < MIN_WIDE ? MIN_WIDE : (biased > MAX_WIDE ? MAX_WIDE : biased);
        rejected = (gain == 0) && !ALLOW_ZERO;

        @(negedge clk);
        x = 20'(xi);
        gain_code = 10'(gain);
        offset_code = 12'(off);
        valid_in = 1;
        @(posedge clk);
        #1;
        require(processing_reject == rejected, "bound zero endpoint policy");
        require(unbound_reject == (gain != 512), "unbound gain acceptance");
        if (gain != 512) rejects++;
        @(negedge clk);
        valid_in = 0;
        @(posedge clk);
        #1;
        require(!valid_out && !unbound_valid, "latency shorter than two intervals");
        @(posedge clk);
        #1;
        require(valid_out == !rejected, "valid/reject outcome");
        if (!rejected) begin
            require(longint'($signed(y)) == expected,
                    $sformatf("numeric output x=%0d gain=%0d offset=%0d expected=%0d actual=%0d",
                              xi, gain, off, expected, $signed(y)));
            require(clip_low == low && clip_high == high, "final saturation flags");
            compares++;
        end
        require(unbound_valid == (gain == 512), "missing-map output valid");
        if (gain == 512) begin
            require(longint'($signed(y_unbound)) == unity_expected, "unity offset output");
            require(unbound_low == (biased < MIN_WIDE) && unbound_high == (biased > MAX_WIDE),
                    "unity endpoint flags");
            compares++;
        end
        @(posedge clk);
        #1;
        require(!valid_out && !unbound_valid, "unexpected repeat output");
    endtask

    // =========================================================================
    // 2. 原向量、偏置宽度、舍入及清流水
    // =========================================================================
    initial begin
        repeat (3) @(negedge clk);
        rst_n = 1;
        sample(0, 512, 0);
        sample(MIN_CODE, 512, -2048);
        sample(MAX_CODE, 512, 2047);
        sample(-500001, 1023, -700);
        sample(500001, 1023, 700);
        sample(-3, 513, 0);
        sample(3, 511, 0);
        sample(42, 0, 1);
        for (int i = 0; i < 32; i++)
            sample((i%2 == 0 ? 1 : -1)*(100+i*713), 1+(i*31)%1023, (i*97)%4096-2048);

        // 轨端正负偏置迫使前级保留额外符号位。低于单位增益时，两端
        // 都可能返回可表示范围，提前截位会造成大误差。
        sample(MIN_CODE, 1, -2048);
        sample(MAX_CODE, 1, 2047);
        sample(MIN_CODE, 1023, -2048);
        sample(MAX_CODE, 1023, 2047);

        // 正负偏置与多个增益。默认 K=3/S=13 时 ±4096 乘增益增量
        // 分别为 ±1.5 码，从而验证半步总是选择较大整数。
        sample(0, 'h300, 1024);
        sample(0, 'h100, -1024);
        sample(3967, 513, 129);
        sample(-3967, 513, -129);

        // 两笔连续待提交样本再遇取消。第三笔的非法增益也不能产生拒绝。
        @(negedge clk);
        valid_in = 1;
        x = 20'd123;
        offset_code = -12'sd128;
        gain_code = 10'd512;
        @(negedge clk);
        x = 20'd456;
        offset_code = 12'sd64;
        @(negedge clk);
        cancel = 1;
        gain_code = 0;
        @(posedge clk);
        #1;
        require(!valid_out && !unbound_valid && !processing_reject && !unbound_reject,
                "cancel suppresses mature output and edge input");
        @(negedge clk);
        cancel = 0;
        valid_in = 0;
        repeat (4) begin
            @(posedge clk);
            #1;
            require(!valid_out && !unbound_valid, "cancel leaked a pending sample");
        end
        cancels++;

        // 异步复位在两拍之间断言。随后使用另一组系数重启并重验延迟。
        @(negedge clk);
        valid_in = 1;
        gain_code = 10'd512;
        @(posedge clk);
        #2;
        rst_n = 0;
        #1;
        require(!valid_out && !unbound_valid && !clip_low && !clip_high && !processing_reject,
                "asynchronous reset clears pending sample");
        @(negedge clk);
        rst_n = 1;
        valid_in = 0;
        repeat (3) begin
            @(posedge clk);
            #1;
            require(!valid_out && !unbound_valid, "reset leaked a pending sample");
        end
        sample(36, 768, -6);
        require(compares >= 50 && rejects >= 40 && cancels == 1, "vacuous correction checks");
        $display("SYS_ERROR_CORR_CONTRACT_COMPLETE comparisons=%0d unbound_rejects=%0d cancels=%0d checks=%0d",
                 compares, rejects, cancels, checks);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "CORR watchdog");
    end
endmodule
`default_nettype wire
