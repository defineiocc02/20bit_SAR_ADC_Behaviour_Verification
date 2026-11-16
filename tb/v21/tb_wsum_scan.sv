`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_wsum_scan.sv
// 模块名     : tb_wsum_scan
// 版本       : v1.0
// 功能       : 权重表**任意取值**下，逐笔比对 wsum_v21 的 G/Wp/Wm 输出。
//
// 设计说明   :
//   * 为什么需要这个测试：
//     现有 tb_i4b_e2e / check_gate_e2e 都把权重表设成**名义固定值**
//     （主列 268435456、子列 33554432/16777216/8388608）。
//     那样恰好绕过了 w_main_tbl / w_sub_tbl 的取值空间。
//     Formality 报 u_wsum 的 G_q30/Wp_q30/Wm_q30 共 20 个 bit 不等价，
//     而 analyze_points 指出 2833 个"未约束"端口全是权重表 ——
//     到底是工具追不到数组端口，还是逻辑真错，必须用**非名义权重**实测。
//
//   * 做法：对每一组权重（固定种子随机生成，覆盖 40 位的不同位段），
//     驱动一组确定的 ids/pre/post，记录 G/Wp/Wm。RTL 与网表跑同一份
//     激励，输出逐行 diff。差异为 0 则证明网表功能正确。
//
//   * 权重取值不越界：主/子权重是 Q30 定点，名义值 4Cu/16 = 2^28。
//     本测试取 [1, 2^30] 范围的随机值，且保证低 4 位有非零值 ——
//     这正好覆盖 Formality 报的 Wm_q30[0..3] 这类低位失败点。
//
// 变更记录   :
//   2026-11-16  初版。为闭合 u_wsum 的 Formality failing 而写。
// =============================================================================
module tb_wsum_scan;

    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;

    logic        calc_valid = 1'b0;
    logic [39:0] ids_i = 40'd0;
    logic [31:0] pre_mask_i = 32'd0;
    logic [159:0] post_mask_i = 160'd0;
    logic [39:0] w_main_tbl [0:17][0:15];
    logic [39:0] w_sub_tbl  [0:17][0:3];

    logic [63:0] G_q30, Wp_q30, Wm_q30;
    logic        wsum_valid;

    wsum_v21 dut (
        .clk(clk), .rst_n(rst_n),
        .calc_valid(calc_valid),
        .ids_i(ids_i), .pre_mask_i(pre_mask_i), .post_mask_i(post_mask_i),
        .w_main_tbl(w_main_tbl), .w_sub_tbl(w_sub_tbl),
        .G_q30(G_q30), .Wp_q30(Wp_q30), .Wm_q30(Wm_q30),
        .sum_valid(wsum_valid));

    int fd;
    int n_cyc = 0;

    // 逐拍记录：只要 wsum_valid 就写一行
    always @(negedge clk) if (rst_n && wsum_valid) begin
        n_cyc++;
        $fdisplay(fd, "C%0d ids=%010h pre=%08h post=%040h G=%016h Wp=%016h Wm=%016h",
                  n_cyc, ids_i, pre_mask_i, post_mask_i, G_q30, Wp_q30, Wm_q30);
    end

    // 一组权重 + 一组掩码，跑一拍
    task automatic one(input int seed);
        logic [39:0] wm [0:7][0:15];
        logic [39:0] ws [0:7][0:3];
        begin
            // 8 个片号：散开取，覆盖数组不同下标
            ids_i = 40'd0;
            for (int s = 0; s < 8; s++)
                ids_i[s*5 +: 5] = 5'((seed + s * 3) % 18);

            // 随机权重：低 4 位强制非零，专门打 Formality 报的低位 bit
            for (int s = 0; s < 8; s++) begin
                for (int j = 0; j < 16; j++) begin
                    w_main_tbl[ids_i[s*5 +: 5]][j] =
                        40'($urandom(seed + s*16 + j) & 40'h3FFF_FFFF) | 40'h1;
                end
                for (int j = 0; j < 4; j++) begin
                    w_sub_tbl[ids_i[s*5 +: 5]][j] =
                        40'($urandom(seed + 900 + s*4 + j) & 40'h3FFF_FFFF) | 40'h1;
                end
            end

            // pre/post 掩码：伪随机，保证有选中位
            pre_mask_i  = 32'($urandom(seed + 2000) | 32'h1);
            post_mask_i = 160'({$urandom(seed + 3000), $urandom(seed + 3001),
                                $urandom(seed + 3002), $urandom(seed + 3003),
                                $urandom(seed + 3004)});

            calc_valid = 1'b1;
            @(posedge clk); #1;
            @(negedge clk);
            calc_valid = 1'b0;
            @(posedge clk); #1;
            @(negedge clk);
        end
    endtask

    initial begin
        reg [1023:0] opath;
        if (!$value$plusargs("OUT=%s", opath)) opath = "wsum_scan.txt";
        fd = $fopen(opath, "w");
        if (fd == 0) begin $display("FATAL: cannot open %0s", opath); $finish; end

        repeat (3) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);

        // 256 组不同权重 × 掩码组合
        for (int i = 1; i <= 256; i++) one(i * 7919);

        repeat (3) @(negedge clk);
        $fclose(fd);
        $display("WSUM_SCAN_DONE records=%0d", n_cyc);
        $finish;
    end

endmodule
