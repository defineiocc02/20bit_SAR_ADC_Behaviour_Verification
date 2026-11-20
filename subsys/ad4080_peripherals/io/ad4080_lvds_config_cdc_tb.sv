`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_lvds_config_cdc_tb.sv
// 模块名     : ad4080_lvds_config_cdc_tb
// 版本       : v1.0
// 功能       : 以独立时钟和真实停钟检验两字节配置的提交、确认及复位重送。
// 设计说明   :
//   * 用不同 A/B 的不透明数据检测撕裂。测试台不依赖内部翻转位或寄存器。
//   * 源在核心负沿改变已生效 CSR。目的端在 IO 负沿改变空闲和许可，
//     随后检查目的正沿的提交及经核心同步返回的 ready。
//   * 训练位只用来识别旧数据；本夹具不复制任何模式合法性或 PHY 规则。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_lvds_config_cdc_tb;
    // =========================================================================
    // 1. 时钟与桥接端口
    // =========================================================================
    // --- 核心权威 CSR 和两域复位 ---
    logic clk_core = 1'b0;
    logic clk_io = 1'b0;
    logic core_running = 1'b1;
    logic io_running = 1'b0;
    logic core_reset_n = 1'b0;
    logic io_reset_n = 1'b0;
    logic live_core = 1'b0;
    logic [15:0] csr = 16'h4070;  // 先提供非零冷启配置，旧训练用 A[4]
    logic ready_core;

    // --- IO 允许与外部可见的整组提交 ---
    logic idle_io = 1'b1;
    logic allow_io = 1'b0;
    logic [15:0] visible;
    logic valid_io, pending_io, applied_io;
    logic [15:0] pad_filter = 16'h2000;       // 即使 A/B 不变，也要送出新电气档和滤波设置。
    logic [15:0] observed_pad_filter;
    int commits = 0;

    ad4080_lvds_config_cdc u_dut (
        .clk_core_i(clk_core), .core_reset_ni(core_reset_n), .cfg_live_core_i(live_core),
        .cfg_a_core_i(csr[15:8]), .cfg_b_core_i(csr[7:0]),
        .cfg_c_core_i(pad_filter[15:8]), .cfg_filter_core_i(pad_filter[7:0]),
        .cfg_c_io_o(observed_pad_filter[15:8]), .cfg_filter_io_o(observed_pad_filter[7:0]), .cfg_ready_core_o(ready_core),
        .clk_io_i(clk_io), .io_reset_ni(io_reset_n), .io_idle_i(idle_io),
        .io_apply_allow_i(allow_io), .cfg_a_io_o(visible[15:8]), .cfg_b_io_o(visible[7:0]),
        .cfg_valid_io_o(valid_io), .cfg_pending_io_o(pending_io), .cfg_applied_io_o(applied_io)
    );

    // [时序] 独立的 125 MHz 和 400 MHz 加相位偏移，避免两个激励边沿竞争。
    initial forever begin
        #4ns;
        if (core_running) clk_core = !clk_core;
    end
    initial begin
        #413ps;
        forever begin
            #1250ps;
            if (io_running) clk_io = !clk_io;
        end
    end

    // [回执] 在目的采样沿后读取应用脉冲。停钟时高电平不反复计数。
    initial forever begin
        @(posedge clk_io);
        #1ps;
        if (applied_io) commits++;
    end

    // =========================================================================
    // 2. 端口检查与有界等待
    // =========================================================================
    task automatic require_ok(input logic condition, input string reason);
        if (condition !== 1'b1) $fatal(1, "%s t=%0t", reason, $time);
    endtask

    task automatic wait_pending;
        // [CDC] 观察本域 pending 才推进激励，避免用固定延时假定跨域相位。
        repeat (24) begin
            @(negedge clk_io);
            if (pending_io) return;
        end
        $fatal(1, "request did not reach IO");
    endtask

    // [确认] 只在应用脉冲且整组可见后允许返回。此时源 ack 还需再过同步器。
    task automatic expect_apply(input logic [15:0] expected);
        repeat (32) begin
            @(negedge clk_io);
            if (applied_io) begin
                require_ok(valid_io && !pending_io && visible == expected,
                           "commit did not publish the expected atomic snapshot");
                return;
            end
        end
        $fatal(1, "IO did not apply configuration %04x", expected);
    endtask

    task automatic wait_ready;
        repeat (16) begin
            @(negedge clk_core);
            if (ready_core) return;
        end
        $fatal(1, "core did not receive acknowledgement of its actual CSR");
    endtask

    task automatic write_csr(input logic [15:0] value, input logic require_admit);
        @(negedge clk_core);
        if (require_admit) require_ok(ready_core, "CSR write started on a busy bridge");
        csr = value;
        #1ps;
        // [准入] core ready 要比较当前实际 CSR，不能等收到旧邮箱的下一拍更新。
        require_ok(!ready_core, "the new CSR was acknowledged before transfer");
    endtask

    // [撤销] 检查在下一时钟沿前遮蔽，不能用停钟之后的延迟复位来通过。
    task automatic check_invalid;
        #10ps;
        require_ok(!ready_core && !valid_io && !pending_io && !applied_io && visible == '0,
                   "inactive session exposed configuration or handshake");
    endtask

    task automatic hold_old_image(input logic [15:0] expected);
        repeat (4) begin
            @(negedge clk_io);
            require_ok(valid_io && pending_io && visible == expected && !applied_io && !ready_core,
                       "pending update disturbed the previously applied configuration");
        end
    endtask

    // =========================================================================
    // 3. 已应用快照、复位和停钟的定向场景
    // =========================================================================
    initial begin
        // [冷启] 核心已有权威 CSR，未运行 IO 时不能提前返回已接收。
        repeat (3) @(negedge clk_core);
        core_reset_n = 1'b1;
        io_reset_n = 1'b1;
        live_core = 1'b1;
        repeat (6) @(negedge clk_core);
        check_invalid();
        io_running = 1'b1;
        wait_pending();
        repeat (3) begin
            @(negedge clk_io);
            require_ok(pending_io && !valid_io && !ready_core && visible == '0,
                       "initial payload was acknowledged without IO permission");
        end
        allow_io = 1'b1;
        expect_apply(16'h4070);
        require_ok(!ready_core, "acknowledgement did not wait for core synchronizers");
        wait_ready();
        require_ok(commits == 1, "initial snapshot applied more than once");
        $display("PASS cold start, no early valid and one acknowledged snapshot");

        // [忙] 即使同一拍 A/B 都改变，旧帧先继续。仅空闲还不足以绕过许可门。
        @(negedge clk_io);
        idle_io = 1'b0;
        write_csr(16'h50b5, 1'b1);
        wait_pending();
        hold_old_image(16'h4070);
        idle_io = 1'b1;
        allow_io = 1'b0;
        hold_old_image(16'h4070);
        allow_io = 1'b1;
        expect_apply(16'h50b5);
        wait_ready();
        require_ok(commits == 2, "busy deferral duplicated the commit");
        $display("PASS old active profile stable until idle and permission coincide");

        // [停钟] 无目标采样沿时保持旧应用和未应答的源邮箱。复钟后受理一次。
        @(negedge clk_io);
        io_running = 1'b0;
        write_csr(16'h44b3, 1'b1);
        repeat (8) @(negedge clk_core);
        require_ok(!ready_core && visible == 16'h50b5 && commits == 2,
                   "halted IO incorrectly applied or acknowledged an update");
        io_running = 1'b1;
        expect_apply(16'h44b3);
        wait_ready();
        require_ok(commits == 3, "resumed IO did not apply exactly once");
        $display("PASS IO clock stop holds the current image, ordered application on resume");

        // [错峰] 直接扰动忙时的权威值，确保已发的快照守住两个字节，
        // 往返确认后才补送最终当前值。正常软件写按 ready 门控。
        @(negedge clk_io);
        allow_io = 1'b0;
        write_csr(16'h40c0, 1'b1);
        wait_pending();
        write_csr(16'h51d2, 1'b0);
        write_csr(16'h50e4, 1'b0);
        hold_old_image(16'h44b3);
        allow_io = 1'b1;
        expect_apply(16'h40c0);
        require_ok(!ready_core, "older snapshot hid the newer actual CSR");
        @(negedge clk_io);
        require_ok(!applied_io, "applied stayed high past its pulse");
        expect_apply(16'h50e4);
        wait_ready();
        require_ok(commits == 5, "unexpected intermediate or duplicate snapshot");
        $display("PASS in-flight image held intact, next handshake uses latest actual CSR");

        // [IO 复位] 已应用训练配置且另一个训练快照已同步到 IO。停钟复位
        // 撤销两者，实际 CSR 在此窗口改变，以区分新投递与旧请求错续。
        @(negedge clk_io);
        allow_io = 1'b0;
        write_csr(16'h51fb, 1'b1);
        wait_pending();
        io_running = 1'b0;
        io_reset_n = 1'b0;
        check_invalid();
        @(negedge clk_core);
        csr = 16'h44f7;
        io_reset_n = 1'b1;
        repeat (5) @(negedge clk_core);
        // 此时核心可送新请求，尚无 IO 停钟解除后的采样沿承认任何载荷。
        check_invalid();
        allow_io = 1'b1;
        io_running = 1'b1;
        expect_apply(16'h44f7);
        wait_ready();
        require_ok(commits == 6, "IO reset released an old or duplicate image");
        $display("PASS IO-only reset with stopped clock hides training and resends retained CSR");

        write_csr(16'h5012, 1'b1);
        expect_apply(16'h5012);
        wait_ready();
        // [核心复位] 反向测试源停钟。即便其端口暂留旧训练，合格前也不能重送。
        core_running = 1'b0;
        core_reset_n = 1'b0;
        live_core = 1'b0;
        check_invalid();
        repeat (3) @(negedge clk_io);
        core_reset_n = 1'b1;
        repeat (10) begin
            @(negedge clk_io);
            check_invalid();
        end
        core_running = 1'b1;
        repeat (5) @(negedge clk_core);
        // 核心复钟足够长后仍关闭，证明原因是 CSR 未合格而不只是没时钟。
        check_invalid();
        @(negedge clk_core);
        csr = 16'h4036;
        live_core = 1'b1;
        expect_apply(16'h4036);
        wait_ready();
        require_ok(commits == 8, "core reset republished stale training");
        $display("PASS core-only reset waits for qualified CSR before restoring IO values");

        // 只写 C 或 0x29 时同样开启新一轮握手。I/O 忙时四个字段都必须停在旧配置。
        @(negedge clk_io); idle_io = 0;
        @(negedge clk_core); pad_filter = 16'hF00B;
        repeat (5) @(negedge clk_io);
        require_ok(observed_pad_filter == 16'h2000 && !ready_core, "C/filter snapshot changed mid-frame");
        idle_io = 1;
        expect_apply(csr);
        wait_ready();
        require_ok(observed_pad_filter == 16'hF00B && commits == 9, "same A/B hid a changed C/filter");
        $display("AD4080 LVDS CONFIG CDC REGRESSION PASS commits=%0d", commits);
        $finish;
    end

    initial begin
        #10us;
        $fatal(1, "LVDS config CDC watchdog expired");
    end
endmodule

`default_nettype wire
