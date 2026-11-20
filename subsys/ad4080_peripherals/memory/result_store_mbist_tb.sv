`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : result_store_mbist_tb.sv
// 模块名     : result_store_mbist_tb
// 版本       : v1.0
// 功能       : 以独立的地址/命令序列核对全深度 MBIST、功能隔离及故障诊断。
// 设计说明   :
//   * 数据路径接原始 result_store。只在返回总线注入错值或屏蔽有效位，
//     以区分控制器定位能力和真实存储单元的电路故障覆盖。
//   * 不读取未初始化单元作为判据。测试序列每遍先填满，再对照两种底图。
// -----------------------------------------------------------------------------
// 变更记录
// 初始版本，暂无变更记录。
// =============================================================================
module result_store_mbist_tb;
    // =========================================================================
    // 1. 原始存储接线与故障注入
    // =========================================================================
    localparam int DEPTH = 16384;
    localparam logic [13:0] FAULT_ADDR = 14'h1000; // 第二银行的第一个字

    // 测试把功能端、DFT 授权、故障注入都放在 SRAM 域，避免用测试台的
    // 相位选择掩盖系统尚未验证的多域交接。
    logic clk = 0, rst_n = 0;
    logic test_mode = 0, quiesced = 0, start = 0, checkerboard = 0, reinit_done = 0;
    logic start_ready, func_hold, contents_invalid, busy, done;
    logic [1:0] fail_reason;
    logic fail_addr_valid;
    logic [13:0] fail_addr;
    logic [19:0] fail_expected, fail_actual;
    logic func_req = 0, func_write = 0, func_valid;
    logic [13:0] func_addr = '0;
    logic [19:0] func_wdata = '0, func_rdata;
    logic store_req, store_write, store_valid;
    logic [13:0] store_addr;
    logic [19:0] store_wdata, store_rdata;
    logic [13:0] observed_read_addr;
    logic inject_mismatch = 0, drop_response = 0;
    logic [19:0] injected_data;
    logic injected_valid;
    int checked_accesses = 0;

    always #12.5 clk <= ~clk; // 40 MHz 的功能设计例，仿真只验证离散时序

    result_store_mbist dut (
        .clk(clk), .rst_n(rst_n),
        .test_mode_i(test_mode), .func_quiesced_i(quiesced), .start_i(start),
        .checkerboard_i(checkerboard), .func_reinit_done_i(reinit_done),
        .start_ready_o(start_ready), .func_hold_o(func_hold),
        .contents_invalid_o(contents_invalid), .busy_o(busy), .done_o(done),
        .fail_reason_o(fail_reason), .fail_addr_valid_o(fail_addr_valid),
        .fail_addr_o(fail_addr), .fail_expected_o(fail_expected), .fail_actual_o(fail_actual),
        .func_req_i(func_req), .func_write_i(func_write), .func_addr_i(func_addr),
        .func_data_i(func_wdata), .func_data_o(func_rdata), .func_read_valid_o(func_valid),
        .store_req_o(store_req), .store_write_o(store_write), .store_addr_o(store_addr),
        .store_data_o(store_wdata), .store_data_i(injected_data),
        .store_read_valid_i(injected_valid)
    );
    // 使用原功能叶子，宏口只连接一路命令。诊断比较与存储的写入互不篡改，
    // 因而返回注入可单独验证比较器、有效位和地址延迟。
    result_store store (
        .clk(clk), .rst_n(rst_n), .req_i(store_req), .write_i(store_write),
        .addr_i(store_addr), .data_i(store_wdata), .data_o(store_rdata),
        .read_valid_o(store_valid)
    );

    // 缺响应和错 bit 都按已受理地址注入。当前宏地址已可能切到下一命令。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            observed_read_addr <= '0;
        else if (store_req && !store_write)
            observed_read_addr <= store_addr;
    end
    assign injected_data = (inject_mismatch && store_valid && (observed_read_addr == FAULT_ADDR))
                           ? (store_rdata ^ 20'h00001) : store_rdata;
    assign injected_valid = store_valid && !(drop_response && (observed_read_addr == FAULT_ADDR));

    // =========================================================================
    // 2. 入口、单口命令和恢复判据
    // =========================================================================
    // 上升沿后等一个仿真时间单位，确保看到非阻塞更新之后的状态。
    // 需要检查本沿受理条件的任务在延时前额外抽样一次。
    task automatic tick;
        @(posedge clk);
        #1;
    endtask

    // 接受启动的这拍只能换所有权。若宏在同一沿已发生写入，
    // 则已有功能返回与测试数据之间少了一道排空边界。
    task automatic launch(input bit use_checkerboard);
        @(negedge clk);
        test_mode = 1;
        quiesced = 1;
        func_req = 0;
        reinit_done = 0;
        start = 1;
        checkerboard = use_checkerboard;
        @(posedge clk);
        if (!start_ready || !func_hold || store_req)
            $fatal(1, "start handshake or request on start edge");
        #1;
        if (!busy || done || !contents_invalid || !func_hold || func_valid)
            $fatal(1, "state after accepted start");
        start = 0;
        checkerboard = !use_checkerboard; // 后续 DFT 输入变化应与本次测试无关
    endtask

    task automatic expect_access(input bit we, input logic [13:0] address,
                                 input logic [19:0] data);
        @(posedge clk);
        // 沿前检查宏请求，逐沿发现漏拍或重拍；只在写时比较写数据。
        if (!store_req || (store_write != we) || (store_addr != address) ||
            (we && (store_wdata != data)) || !busy || !func_hold ||
            !contents_invalid || func_valid)
            $fatal(1, "March command %0d want {%d %h %h} got {%d %h %h}",
                   checked_accesses, we, address, data, store_write, store_addr, store_wdata);
        // 数据比较来自独立遍历的底图，避免 DUT 的比较期望错误时被同一个错误掩盖。
        // 所有这些读均有前置写入，不以存储模型复位后的默认值通过。
        #1;
        if (store_valid != !we || (!we && store_rdata != data) || func_valid)
            $fatal(1, "March response addr=%04h want=%05h got=%05h valid=%b",
                   address, data, store_rdata, store_valid);
        checked_accesses++;
    endtask

    // 每对都用相同线性地址，第二拍才改写。若银行在读拍就递增，
    // 则 0x0FFF/0x1000 等边界会被精确定位。
    task automatic expect_pairs(input bit descending, input logic [19:0] pattern);
        logic [13:0] address;
        for (int index = 0; index < DEPTH; index++) begin
            address = descending ? 14'(DEPTH - 1 - index) : 14'(index);
            expect_access(0, address, pattern);
            expect_access(1, address, ~pattern);
        end
    endtask

    task automatic expect_complete_march(input logic [19:0] pattern);
        int count_before;
        count_before = checked_accesses;
        // 独立展开 6 段遍历，覆盖各段首末以及全部银行交界。
        for (int address = 0; address < DEPTH; address++)
            expect_access(1, 14'(address), pattern);
        expect_pairs(0, pattern);
        expect_pairs(0, ~pattern);
        expect_pairs(1, pattern);
        expect_pairs(1, ~pattern);
        for (int address = 0; address < DEPTH; address++)
            expect_access(0, 14'(address), pattern);
        // 末读在发送沿之后返回，完成只能在下一沿消费响应时报告。
        // 边界的额外访问或提前退役都可能误判最后一个字。
        @(posedge clk);
        if (store_req || !busy || !store_valid || func_valid)
            $fatal(1, "last read response must drain without an extra command");
        #1;
        if (!done || busy || !func_hold || !contents_invalid || fail_reason != 0 ||
            fail_addr_valid || checked_accesses - count_before != 10 * DEPTH)
            $fatal(1, "normal March completion");
        $display("PASS full March pattern=%05h accesses=%0d elapsed_cycles=%0d",
                 pattern, checked_accesses - count_before, 10 * DEPTH + 1);
    endtask

    // 在有启动结果、但部分恢复资格缺失时，既禁止存储新命令，
    // 也不向功能端回送测试底图。
    task automatic expect_blocked_recovery;
        @(posedge clk);
        if (store_req || func_valid) $fatal(1, "access during recovery");
        #1;
        if (!func_hold || !contents_invalid || !done || busy)
            $fatal(1, "recovery released before all conditions");
    endtask

    // 逐项改变恢复条件并保留上一条结果，可以区分撤掉旧请求、
    // 关闭 DFT 与重建记录的边界，且验证下一轮才能清掉 done。
    task automatic recover;
        @(negedge clk);
        test_mode = 1;
        quiesced = 1;
        func_req = 0;
        reinit_done = 1;
        expect_blocked_recovery(); // 已应答但仍在测试模式
        @(negedge clk);
        test_mode = 0;
        func_req = 1;
        expect_blocked_recovery(); // 旧功能请求尚未撤销
        @(negedge clk);
        func_req = 0;
        reinit_done = 0;
        expect_blocked_recovery(); // 功能记录尚未重建
        @(negedge clk);
        reinit_done = 1;
        tick();
        if (func_hold || contents_invalid || !done || store_req || func_valid)
            $fatal(1, "recovery handshake");
        @(negedge clk);
        reinit_done = 0;
    endtask

    // 故障可能使进度游标提前停止，仍必须在一遍完整扫描的界内报告，
    // 免得测试台无限等待将超时误报成正常终止。
    task automatic wait_for_done;
        for (int index = 0; index < 10 * DEPTH + 2; index++) begin
            tick();
            if (done)
                return;
        end
        $fatal(1, "MBIST terminal state timeout");
    endtask

    // =========================================================================
    // 3. 直通功能、完整遍历、注入失败和断授测
    // =========================================================================
    initial begin
        // 功能前置样本在发出未授权 start 的同一沿写入。
        // 如果仅凭 start 夺口或屏蔽功能，后续回读会丢失此字。
        repeat (2) tick();
        @(negedge clk);
        rst_n = 1;
        start = 1;
        func_req = 1;
        func_write = 1;
        func_addr = 14'h3000;
        func_wdata = 20'hac5d6;
        tick();
        if (busy || func_hold || func_valid || contents_invalid || start_ready)
            $fatal(1, "ordinary write while unqualified start asserted");
        @(negedge clk);
        start = 0;
        func_write = 0;
        tick();
        if (!func_valid || func_rdata != 20'hac5d6) $fatal(1, "functional write/read");

        // 上拍已有功能读。进入测试授权时完成应答，但不能在这拍开跑。
        @(negedge clk);
        start = 1;
        test_mode = 1;
        quiesced = 1;
        func_req = 0;
        @(posedge clk);
        if (!func_hold || !func_valid || start_ready || store_req)
            $fatal(1, "handoff failed to drain earlier functional read");
        #1;
        if (busy || contents_invalid || !start_ready)
            $fatal(1, "startup must wait for the outstanding read");
        @(negedge clk);
        quiesced = 0;
        tick();
        if (start_ready || busy || store_req) $fatal(1, "active function did not block start");
        @(negedge clk);
        quiesced = 1;
        func_req = 1;
        tick();
        if (start_ready || busy || store_req) $fatal(1, "pending request did not block start");
        @(negedge clk);
        func_req = 0;
        // 遗留的应答属于上一轮重建，不能被拿来打开一次新破坏性操作。
        // 独立条件检查防止仅凭测试模式位决定启动资格。
        reinit_done = 1;
        tick();
        if (start_ready || busy || store_req) $fatal(1, "stale reinit acknowledgment");
        $display("PASS qualification and functional read drain");

        // 活动期间的功能请求与第二个启动脉冲不能覆盖测试序列。
        launch(0);
        func_req = 1;
        func_write = 1;
        start = 1;
        expect_complete_march(20'h00000);
        start = 0;
        recover();
        // 第二种底图读回含 0 和 1，有助于发现不同逻辑位被广播或掩码。
        // 首次启动后立即改变选择输入，两遍用于核对启动锁存。
        launch(1);
        expect_complete_march(20'h55555);
        recover();

        // 银行交界点注入一位错误，首错应当取上拍读地址和匹配底图。
        // 缺读有效的重跑保持同一故障地址，以区分比较错误和接口失步。
        @(negedge clk);
        inject_mismatch = 1;
        launch(0);
        wait_for_done();
        if (busy || fail_reason != 2'd1 || !fail_addr_valid || fail_addr != FAULT_ADDR ||
            fail_expected != '0 || fail_actual != 20'h00001 || !func_hold || func_valid)
            $fatal(1, "mismatch diagnostic at bank boundary");
        $display("PASS injected bit mismatch addr=%04h expected=%05h actual=%05h",
                 fail_addr, fail_expected, fail_actual);
        recover();
        inject_mismatch = 0;
        drop_response = 1;
        launch(0);
        wait_for_done();
        if (busy || fail_reason != 2'd2 || !fail_addr_valid || fail_addr != FAULT_ADDR ||
            fail_expected != '0 || fail_actual != '0 || !func_hold || func_valid)
            $fatal(1, "missing-read diagnostic");
        $display("PASS dropped read response addr=%04h", fail_addr);
        recover();
        drop_response = 0;

        // 两种授权撤回在下一存储上升沿前施加。测试同时检查组合封锁和
        // 沿后中止状态，防止授权丢失后仍提交一个破坏性写字。
        for (int loss = 0; loss < 2; loss++) begin
            launch(1);
            repeat (4) tick();
            @(negedge clk);
            if (loss == 0)
                test_mode = 0;
            else
                quiesced = 0;
            #1;
            if (store_req || !func_hold) $fatal(1, "request after guard loss");
            tick();
            if (!done || busy || fail_reason != 2'd3 || fail_addr_valid || !contents_invalid)
                $fatal(1, "abort state");
            recover();
        end
        $display("PASS authorization loss and qualified recovery");

        // 全域复位时关宏请求并舍弃测试状态。后续只读取新写入的样本，
        // 因为物理存储内容的保留或随机性属于其电源状态。
        launch(0);
        repeat (3) tick();
        @(negedge clk);
        rst_n = 0;
        #1;
        if (store_req || !func_hold || done || busy || func_valid || contents_invalid)
            $fatal(1, "reset must clear controller, close port and retire reports");
        test_mode = 0;
        quiesced = 0;
        start = 0;
        tick();
        @(negedge clk);
        rst_n = 1;
        func_req = 1;
        func_write = 1;
        func_addr = 14'h3fff;
        func_wdata = 20'hc25d6;
        tick();
        @(negedge clk);
        func_write = 0;
        tick();
        if (!func_valid || func_rdata != func_wdata || func_hold || done || contents_invalid)
            $fatal(1, "fresh functional word after reset");
        $display("PASS reset and fresh functional read");
        $display("PASS result_store_mbist banks=4 full_scan_accesses=%0d", checked_accesses);
        $finish;
    end
endmodule
`default_nettype wire
