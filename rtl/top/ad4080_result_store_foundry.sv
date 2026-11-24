`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_result_store_foundry.sv
// 模块名     : ad4080_result_store_foundry
// 版本       : v1.0
// 功能       : 四银行 16,384 × 20-bit 单口工艺厂 SRAM 结果存储顶层封装。
//              对上完全兼容 result_store 的单口仲裁协议，对下例化四颗
//              ad4080_sram_bank_foundry_wrap（CEB/WEB/BWEB/SLP/SD/RTSEL/WTSEL）。
// 设计说明   :
//   * [单口低功耗] 任意时刻最多拉低一个银行的 CEB，严格匹配 4 银行 2×2 物理窗口
//     与 40 MSPS（25 ns/字）单口带宽及动态峰值电流预算。
//   * [不改动冻结包] 独立存放于 rtl/top/，保留 rtl/ad4080_periph/rtl/result_store.sv
//     的 218 文件 SHA-256 交付清单完整无损。
// 变更记录   :
//   2026-11-24  v1.0 初版。四银行工艺厂 SRAM 包装顶层。
// =============================================================================
module ad4080_result_store_foundry (
    // --- 同步控制域 ---
    input  logic        clk_i,         // 单口仲裁时钟（与 result_store.clk 同域）
    input  logic        rst_ni,        // 异步低有效复位（仅清控制态，不复位存储阵列）

    // --- 逻辑单口请求（与 result_store 完全同协议） ---
    input  logic        req_i,         // 有效请求（写/读同沿互斥）
    input  logic        write_i,       // 1=写，0=读
    input  logic [13:0] addr_i,        // [13:12] 选银行 0..3，[11:0] 为银行内地址
    input  logic [19:0] data_i,        // 20-bit 原始结果字
    output logic [19:0] data_o,        // 读出数据（read_valid_o=1 时有效）
    output logic        read_valid_o,  // 读沿后一拍拉高；写、空拍或睡眠均清零

    // --- 工艺宏电源与 DFT 裕量控制 ---
    input  logic        slp_i,         // 全局轻睡保持请求（保留 4 银行内容）
    input  logic        sd_i,          // 全局关断请求（关闭外围并钳位输出）
    input  logic [1:0]  rtsel_i,       // 工艺读裕量调谐
    input  logic [1:0]  wtsel_i        // 工艺写裕量调谐
);

    // =========================================================================
    // 1. 四银行译码与例化（每拍至多激活 1 个银行）
    // =========================================================================
    logic [1:0]  read_bank_q;
    logic [19:0] bank_q [0:3];
    wire         awake     = rst_ni && !slp_i && !sd_i;
    wire         read_fire = awake && req_i && !write_i;

    genvar b;
    generate
        for (b = 0; b < 4; b++) begin : g_banks
            wire bank_sel = awake && req_i && (addr_i[13:12] == 2'(b));
            ad4080_sram_bank_foundry_wrap u_bank (
                .clk_i  (clk_i),
                .ceb_i  (!bank_sel),
                .web_i  (!write_i),
                .addr_i (addr_i[11:0]),
                .d_i    (data_i),
                .bweb_i (20'd0),
                .slp_i  (slp_i),
                .sd_i   (sd_i),
                .rtsel_i(rtsel_i),
                .wtsel_i(wtsel_i),
                .q_o    (bank_q[b])
            );
        end
    endgenerate

    // =========================================================================
    // 2. 同步读响应对齐与低功耗输出隔离
    // =========================================================================
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            read_bank_q  <= 2'd0;
            read_valid_o <= 1'b0;
        end else begin
            read_valid_o <= read_fire;
            if (read_fire) read_bank_q <= addr_i[13:12];
        end
    end

    assign data_o = (slp_i || sd_i) ? 20'd0 : bank_q[read_bank_q];

endmodule
`default_nettype wire
