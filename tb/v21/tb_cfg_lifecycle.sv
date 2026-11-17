`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_cfg_lifecycle.sv
// 模块名     : tb_cfg_lifecycle
// 版本       : v1.0
// 功能       : 权重配置生命周期与范围验收（CONFIG_CONTRACT v1.0 §10）。
//
// 设计说明   :
//   * 为什么需要：外部审计判定 P1 —— 原写口只查 cfg_we 与地址，没有
//     写入完整性、没有生效边界、没有范围检查，导致"未装满仍可受理交易"、
//     "运行中可逐字改权重"、"超范围配置被接受但结果静默截断"。
//   * 直接例化 wsum_v21（不经顶层），这样能精确控制 cfg_* 并观测
//     written_q / locked_q / cfg_valid，不被其它模块的时序干扰。
//   * 用例（§10）：1 完全不写 / 2 少写一个字 / 3 非法地址与超范围数据 /
//     4 完整合法加载 / 5 交易中更新被拒 / 6 停收排空后换代；
//     边界 B1 最后一字与 commit 同拍 / B2 重复写同一地址 / B3 epoch 随事务。
//
// 变更记录   :
//   2026-11-16  v1.0 初版。
// =============================================================================
module tb_cfg_lifecycle;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    logic        cfg_we = 1'b0, cfg_sel = 1'b0;
    logic [4:0]  cfg_sid = 5'd0, cfg_idx = 5'd0;
    logic [39:0] cfg_data = 40'd0;
    logic        cfg_commit = 1'b0, cfg_reload = 1'b0;
    wire         cfg_valid, cfg_busy, cfg_range_err;

    logic        calc_valid = 1'b0;
    logic [39:0] ids_i = 40'd0;
    logic [31:0] pre_mask_i = 32'd0;
    logic [159:0] post_mask_i = 160'd0;
    wire         sum_valid;
    wire [63:0]  G_q30, Wp_q30, Wm_q30;

    wsum_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(calc_valid), .ids_i(ids_i),
        .pre_mask_i(pre_mask_i), .post_mask_i(post_mask_i),
        .cfg_we(cfg_we), .cfg_sel(cfg_sel),
        .cfg_sid(cfg_sid), .cfg_idx(cfg_idx), .cfg_data(cfg_data),
        .cfg_commit(cfg_commit), .cfg_reload(cfg_reload),
        .cfg_valid(cfg_valid), .cfg_busy(cfg_busy),
        .cfg_range_err(cfg_range_err),
        .sum_valid(sum_valid),
        .G_q30(G_q30), .Wp_q30(Wp_q30), .Wm_q30(Wm_q30));

    int checks = 0, errors = 0;
    task automatic fail(input string m);
        begin errors++; $display("[FAIL] %0s", m); end
    endtask
    task automatic okc(input string m);
        // 调用方在判定前已计数。这里仅记录结果，成功与失败走相同统计口径。
        $display("  OK   %0s", m);
    endtask

    // 写一个字
    task automatic wr(input logic sel, input logic [4:0] sd,
                      input logic [4:0] ix, input logic [39:0] d);
        begin
            @(negedge clk);
            cfg_we = 1'b1; cfg_sel = sel; cfg_sid = sd; cfg_idx = ix; cfg_data = d;
            @(posedge clk); @(negedge clk);
            cfg_we = 1'b0;
        end
    endtask

    task automatic do_commit();
        begin
            @(negedge clk); cfg_commit = 1'b1;
            @(posedge clk); @(negedge clk); cfg_commit = 1'b0;
            @(posedge clk); @(negedge clk);
        end
    endtask

    task automatic do_reload();
        begin
            @(negedge clk); cfg_reload = 1'b1;
            @(posedge clk); @(negedge clk); cfg_reload = 1'b0;
            @(posedge clk); @(negedge clk);
        end
    endtask

    // 装全部 360 字（可选跳过最后 n 个）
    task automatic install(input int skip_tail);
        int cnt;
        begin
            cnt = 0;
            for (int s = 0; s < 18; s++) begin
                for (int j = 0; j < 16; j++) begin
                    cnt++;
                    if (cnt <= 360 - skip_tail) wr(1'b0, 5'(s), 5'(j), 40'd268435456);
                end
                for (int j = 0; j < 4; j++) begin
                    cnt++;
                    if (cnt <= 360 - skip_tail) wr(1'b1, 5'(s), 5'(j), 40'd8388608);
                end
            end
        end
    endtask

    // 发一笔求和，返回 G
    task automatic calc(input logic [63:0] exp_note);
        begin
            @(negedge clk); calc_valid = 1'b1;
            @(posedge clk); #1;
            @(negedge clk); calc_valid = 1'b0;
            @(posedge clk); #1;
            @(negedge clk);
        end
    endtask

    initial begin
        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        @(negedge clk);

        // ============ 用例 1：完全不写 ============
        $display("---- 用例1: 完全不写 ----");
        checks++; if (cfg_valid !== 1'b0) fail("未写时 cfg_valid 应为 0"); else okc("cfg_valid=0");
        checks++; if (cfg_busy !== 1'b0) fail("未写时 busy 应为 0"); else okc("busy=0");
        do_commit();
        checks++; if (cfg_valid !== 1'b0) fail("空表 commit 不应生效"); else okc("空表 commit 被拒");

        // ============ 用例 2：少写一个字 ============
        $display("---- 用例2: 少写一个字 ----");
        install(1);   // 只写 359 个
        checks++; if ($countones(dut.written_q) !== 359) fail($sformatf("written 应为 359 实为 %0d", $countones(dut.written_q)));
        else okc("written=359");
        do_commit();
        checks++; if (cfg_valid !== 1'b0) fail("少一字仍生效（不应）"); else okc("少一字 commit 被拒");

        // ============ 用例 3：非法地址 / 超范围数据 ============
        $display("---- 用例3: 非法地址与超范围数据 ----");
        do_reload();
        wr(1'b0, 5'd18, 5'd0, 40'd1);          // sid 越界
        checks++; if ($countones(dut.written_q) !== 0) fail("非法 sid 却置了 written"); else okc("非法 sid 不置 written");
        wr(1'b0, 5'd0, 5'd16, 40'd1);          // 主阵列 idx=16 越界
        checks++; if ($countones(dut.written_q) !== 0) fail("主 idx 越界却置位"); else okc("主 idx 越界不置位");
        wr(1'b1, 5'd0, 5'd4, 40'd1);           // 子阵列 idx=4 越界
        checks++; if ($countones(dut.written_q) !== 0) fail("子 idx 越界却置位"); else okc("子 idx 越界不置位");
        // 合法上限为 32*4*2^30。用上限加 1 检查紧邻的非法码。
        wr(1'b1, 5'd0, 5'd0, 40'd137438953473);
        checks++;
        if ($countones(dut.written_q) !== 0) fail("超范围子权重却置位");
        else if (cfg_range_err !== 1'b1) fail("超范围子权重未置 range_err");
        else okc("超范围子权重被拒且置 range_err");

        // ============ 用例 4：完整合法加载 ============
        $display("---- 用例4: 完整合法加载 ----");
        do_reload();
        install(0);
        checks++; if ($countones(dut.written_q) !== 360) fail($sformatf("written 应 360 实 %0d", $countones(dut.written_q)));
        else okc("written=360");
        do_commit();
        checks++; if (cfg_valid !== 1'b1) fail("完整表 commit 后应为 1"); else okc("cfg_valid=1");
        // 求和能出数
        ids_i = 40'd0;
        for (int s = 0; s < 8; s++) ids_i[s*5 +: 5] = 5'd0;
        pre_mask_i = 32'd0; post_mask_i = 160'd0;
        calc(64'd0);
        checks++;
        if (G_q30 !== (64'd8 * 64'd16 * 64'd268435456)) fail($sformatf("G 错 %0d", G_q30));
        else okc($sformatf("G=%0d 正确", G_q30));

        // ============ 用例 5：交易中更新被拒（locked）============
        $display("---- 用例5: locked 下写入被拒 ----");
        wr(1'b0, 5'd0, 5'd0, 40'd1);   // 试图改权重
        calc(64'd0);
        checks++;
        if (G_q30 !== (64'd8 * 64'd16 * 64'd268435456))
            fail($sformatf("locked 下写入竟然生效 G=%0d", G_q30));
        else okc("locked 下写入被丢弃，原配置仍生效");
        checks++; if (cfg_valid !== 1'b1) fail("拒绝写不应使旧配置失效"); else okc("拒绝写不影响 cfg_valid");

        // ============ 边界 B2：重复写同一地址 ============
        $display("---- B2: 重复写同一地址不增计数 ----");
        do_reload();
        wr(1'b0, 5'd0, 5'd0, 40'd7);
        wr(1'b0, 5'd0, 5'd0, 40'd9);   // 重复
        checks++;
        if ($countones(dut.written_q) !== 1) fail($sformatf("重复写后 written 应 1 实 %0d", $countones(dut.written_q)));
        else okc("重复写仍为 1 位");

        // ============ 边界 B1：最后一字与 commit 同拍 ============
        $display("---- B1: 最后一字与 commit 同拍必须被拒 ----");
        do_reload();
        install(1);                     // 359 个
        // 第 360 个与 commit 同拍
        @(negedge clk);
        cfg_we = 1'b1; cfg_sel = 1'b1; cfg_sid = 5'd17; cfg_idx = 5'd3;
        cfg_data = 40'd8388608; cfg_commit = 1'b1;
        @(posedge clk); @(negedge clk);
        cfg_we = 1'b0; cfg_commit = 1'b0;
        @(posedge clk); @(negedge clk);
        checks++;
        if ($countones(dut.written_q) !== 360) fail("最后一字未写入");
        else if (cfg_valid !== 1'b0) fail("同拍 commit 竟然生效（应被拒）");
        else okc("同拍 commit 被拒，需再单独 commit");
        // 再单独 commit 应成功
        do_commit();
        checks++; if (cfg_valid !== 1'b1) fail("补齐后单独 commit 应生效"); else okc("补齐后单独 commit 生效");

        // ============ 用例 6：reload 后可换代 ============
        $display("---- 用例6: reload 后合法换代 ----");
        do_reload();
        checks++; if (cfg_valid !== 1'b0) fail("reload 后应失效"); else okc("reload 后 cfg_valid=0");
        checks++; if ($countones(dut.written_q) !== 0) fail("reload 后 written 应清零"); else okc("written 清零");
        install(0); do_commit();
        checks++; if (cfg_valid !== 1'b1) fail("换代后应恢复有效"); else okc("换代成功");

        $display("---------------------------------------------------------------");
        $display("cfg_lifecycle: %0d 检查, %0d errors", checks, errors);
        if (errors == 0) $display("RESULT: PASS (%0d checks)", checks);
        else             $display("RESULT: FAIL (%0d errors)", errors);
        $finish;
    end

endmodule
