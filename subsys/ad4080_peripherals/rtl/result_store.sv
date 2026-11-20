`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : result_store.sv
// 模块名     : result_store / result_bank_4k20
// 版本       : v1.0
// 功能       : 将结果缓存的单口逻辑地址映射到四个 SRAM 银行。
// 设计说明   : Rev.B 第 56–57 页给出 16,384 字和单口互斥；四银行与本握手为工程选择。
//              上层管理 FIFO 的事件、写读指针及记录边界；本文件只交付物理存储适配。
// 变更记录   : 首次交付可推断 RAM 及可替换的工艺宏接缝。
// =============================================================================
module result_store (
    // --- 同步 SRAM 控制域；rst_n 在此域同步释放 ---
    input  wire        clk,
    input  wire        rst_n,
    // --- 上层已经仲裁的单一请求；写、读在同一沿互斥 ---
    input  wire        req_i,       // 高电平表示本沿有效；支持逐拍要求
    input  wire        write_i,     // 1 写，0 读；没有第二个并发端口
    input  wire [13:0] addr_i,      // [13:12] 选银行，[11:0] 为本银行字地址
    input  wire [19:0] data_i,      // 原样存储 20 bit，不解释码制或数值
    output wire [19:0] data_o,      // 仅 read_valid_o=1 时有意义
    output reg         read_valid_o // 读沿后得到该笔响应；写沿和空拍均撤销
);
    // =========================================================================
    // 1. 每个请求只激活一个银行，读返回记住请求时的银行编号。
    // =========================================================================
    reg [1:0] read_bank_q;
    wire [19:0] bank_data [0:3];
    genvar i;
    generate for (i=0; i<4; i=i+1) begin : banks
        wire enable = rst_n && req_i && addr_i[13:12] == i[1:0];
        result_bank_4k20 bank (
            .clk(clk), .enable(enable), .write_en(write_i), .addr(addr_i[11:0]),
            .data_i(data_i), .data_o(bank_data[i])
        );
    end endgenerate

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            read_bank_q <= 2'b00;
            read_valid_o <= 1'b0;
        end else begin
            read_valid_o <= req_i && !write_i;
            if (req_i && !write_i)
                read_bank_q <= addr_i[13:12];
        end
    end
    assign data_o = bank_data[read_bank_q];
endmodule

// =============================================================================
// 2. 单银行替换边界。硅实现用授权 1RW 编译宏及其时序模型替换此叶子。
// 存储阵列不复位；上电或早触发时未写位置可含未知值。读写优先级不可改成双口。
// =============================================================================
module result_bank_4k20 (
    input  wire        clk, enable, write_en,
    input  wire [11:0] addr,       // 4096 深度
    input  wire [19:0] data_i,     // 数据端的位序互相独立
    output reg  [19:0] data_o      // 同步读口；写周期保持原输出
);
    reg [19:0] mem [0:4095];
    always @(posedge clk) begin
        if (enable) begin
            if (write_en)
                mem[addr] <= data_i;
            else
                data_o <= mem[addr];
        end
    end
endmodule
`default_nettype wire
