`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_sram_bank_foundry_wrap.sv
// 模块名     : ad4080_sram_bank_foundry_wrap
// 版本       : v1.0
// 功能       : 单银行 4096 × 20-bit 1RW 工艺厂 SRAM 编译宏替换封装。
//              映射至工业标准 40 nm/28 nm 编译宏引脚（CEB/WEB/BWEB/SLP/SD/RTSEL/WTSEL），
//              并与 result_bank_4k20 保持逐拍同口径（同步 1 拍读、写周期保持原输出）。
// 设计说明   :
//   * [流片接缝] 默认提供可综合行为存储模型供 Verilator/Yosys 回归；后端收到目标
//     工艺厂 4096×20 1RW Compiler 网表与 .lib/.lef 后，仅需定义 USE_FOUNDRY_SRAM_MACRO
//     绑定供应商硬宏即可零改逻辑流片（配合 sram_macro_gate.py 校验窗口与角点）。
//   * [低功耗隔离] slp_i（轻睡保持）与 sd_i（深关断）屏蔽访问并将输出钳位为 0，
//     防止睡眠/掉电态的未知电平向逻辑域传播。
// 变更记录   :
//   2026-11-24  v1.0 初版。独立单文件单模块以通过 Verilator -Wall DECLFILENAME 检查。
// =============================================================================
module ad4080_sram_bank_foundry_wrap (
    // --- 工艺厂标准 1RW 4096×20 SRAM 物理引脚 ---
    input  logic        clk_i,        // 同步 SRAM 时钟（上升沿采样全部控制与数据）
    input  logic        ceb_i,        // 低有效片选（0=选中，1=待机）
    input  logic        web_i,        // 低有效写使能（0=写，1=读；仅 ceb_i=0 有效）
    input  logic [11:0] addr_i,       // 4096 深度字地址
    input  logic [19:0] d_i,          // 20-bit 写数据
    input  logic [19:0] bweb_i,       // 低有效按位写掩码（全 0 表示整字写）
    input  logic        slp_i,        // 轻睡保持（1=保留阵列内容，屏蔽读写并钳位输出）
    input  logic        sd_i,         // 关断模式（1=关闭外围与阵列，强制输出清零）
    input  logic [1:0]  rtsel_i,      // 读裕量工艺调谐（由 OTP/DFT 驱动）
    input  logic [1:0]  wtsel_i,      // 写裕量工艺调谐（由 OTP/DFT 驱动）
    output logic [19:0] q_o           // 同步 1 拍读数据（写周期保持原值，睡眠/关断钳 0）
);

    // =========================================================================
    // 1. 工艺厂硬宏替换接缝（默认提供可综合行为存储模型）
    // =========================================================================
    logic [19:0] q_raw_q;
    wire         active_access = !ceb_i && !slp_i && !sd_i;

`ifdef USE_FOUNDRY_SRAM_MACRO
    // [流片绑定点] 替换为目标工艺厂 4096×20 1RW Compiler 认证黑盒实例
    foundry_sram_1rw_4096x20 u_foundry_sram (
        .CLK  (clk_i),
        .CEB  (ceb_i),
        .WEB  (web_i),
        .A    (addr_i),
        .D    (d_i),
        .BWEB (bweb_i),
        .SLP  (slp_i),
        .SD   (sd_i),
        .RTSEL(rtsel_i),
        .WTSEL(wtsel_i),
        .Q    (q_raw_q)
    );
`else
    logic [19:0] mem [0:4095];

    // [行为等价模型] 存储阵列不复位以贴合真实 SRAM；支持按位写掩码 bweb_i。
    always_ff @(posedge clk_i) begin
        if (active_access) begin
            if (!web_i) begin
                for (int b = 0; b < 20; b++) begin
                    if (!bweb_i[b]) mem[addr_i][b] <= d_i[b];
                end
            end else begin
                q_raw_q <= mem[addr_i];
            end
        end
    end

    // 显式归约工艺调谐引脚，确保行为仿真与 Yosys 检查零 UNUSEDSIGNAL 警告
    wire unused_sram_trim = ^{rtsel_i, wtsel_i};
`endif

    // [低功耗输出钳位] 睡眠或关断时强制输出 0，避免浮空/X 态漏入逻辑域
    assign q_o = (slp_i || sd_i) ? 20'd0 : q_raw_q;

endmodule
`default_nettype wire
