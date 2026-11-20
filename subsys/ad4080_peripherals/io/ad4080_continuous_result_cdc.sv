`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_continuous_result_cdc.sv
// 模块名     : ad4080_continuous_result_cdc
// 版本       : v1.0
// 功能       : 从原 DSP 输出域向连续 LVDS 装载域传递已配对的结果和 tag。
// 设计说明   :
//   * 两项完整字接力，写和读各自拥有地址，由同步后的 Gray 进度转交。
//     结果域无回压，写口满就拒绝本笔，装载域自动消费并配对。
//   * 共同 reset_ni 异步清除两端状态，各时钟域独立同步释放。任一时钟停
//     止也能清空旧指针。数据寄存器保留，只有同步发布过的新字可读。
//   * [边界] 静态约束须覆盖 Gray 指针位间偏差与稳定数据到目的端的路径。
//     两级同步的延迟从真实轴上计入首位上升沿前的结果装载期限。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_continuous_result_cdc #(
    parameter int unsigned TAG_WIDTH = 16  // 与 CNV 预约使用同一宽度
) (
    // --- 核心域无回压的原始输出 ---
    input  logic                 clk_core_i,    // 原 DSP 提交结果的时钟
    input  logic                 reset_ni,     // 共同代际复位和供电资格
    input  logic                 valid_core_i,  // 每个高电平上升沿表示一笔新结果
    input  logic [TAG_WIDTH-1:0] tag_core_i,    // 与结果原子到达
    input  logic [19:0]          word_core_i,   // 从原 DSP 输出取数
    output logic                 ready_core_o, // 本沿仍有写入容量，供诊断使用
    output logic                 drop_core_o,  // 本地释放后的未受理结果，脉冲

    // --- 接定时发射叶，不允许 IO 侧停住已可见的字 ---
    input  logic                 clk_io_i,     // 同相 CLK+，决定 CNV 预约和发帧
    output logic                 valid_io_o,   // 本沿提交整字，有效即自动取走
    output logic [TAG_WIDTH-1:0] tag_io_o,     // 帧调度器核验实际转换身份
    output logic [19:0]          word_io_o     // 与 tag 一并稳定至该沿
);
    // =========================================================================
    // 1. 共同置低与本地释放
    // =========================================================================
    (* ASYNC_REG = "TRUE" *) logic [1:0] core_release_q, io_release_q;
    logic core_reset_n, io_reset_n;

    // [RDC] 两端读写状态同代清除。外部须把任一路故障送到共同复位树，
    // 禁止仅复位一边还允许另一边继续推进所持指针。
    assign core_reset_n = core_release_q[1];
    assign io_reset_n = io_release_q[1];

    always_ff @(posedge clk_core_i or negedge reset_ni) begin
        if (!reset_ni) core_release_q <= '0;
        else core_release_q <= {core_release_q[0], 1'b1};
    end

    always_ff @(posedge clk_io_i or negedge reset_ni) begin
        if (!reset_ni) io_release_q <= '0;
        else io_release_q <= {io_release_q[0], 1'b1};
    end

    // =========================================================================
    // 2. 两项所有权和整字传递
    // =========================================================================
    logic [TAG_WIDTH+19:0] word0_q, word1_q;    // 两组触发器保存结果与同笔 tag
    logic [1:0] write_gray_q, read_gray_q;       // 00,01,11,10 循环
    (* ASYNC_REG = "TRUE" *) logic [1:0] read_core_meta_q, read_core_sync_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] write_io_meta_q, write_io_sync_q;
    logic write_word, full_core;

    // [判满] 深度为二时相距两项的 Gray 状态恰为按位反相。地址位即
    // Gray 两位异或。跨域指针晚到只会保守拒收，保护未读地址。
    assign full_core = write_gray_q == ~read_core_sync_q;
    assign ready_core_o = reset_ni && core_reset_n && !full_core;
    assign write_word = valid_core_i && ready_core_o;
    assign valid_io_o = reset_ni && io_reset_n && read_gray_q != write_io_sync_q;

    // [原子性] 目的端至少在写指针经两级捕获后读取；读指针回传前，
    // 源域保留该槽。组合读路径须使用 bundled-data 的实现约束。
    assign {tag_io_o, word_io_o} = valid_io_o ? ((^read_gray_q) ? word1_q : word0_q) : '0;

    always_ff @(posedge clk_core_i or negedge core_reset_n) begin
        if (!core_reset_n) begin
            read_core_meta_q <= '0;
            read_core_sync_q <= '0;
            write_gray_q     <= '0;
            drop_core_o      <= 1'b0;
        end else begin
            read_core_meta_q <= read_gray_q;
            read_core_sync_q <= read_core_meta_q;
            drop_core_o      <= valid_core_i && !ready_core_o;
            if (write_word) write_gray_q <= {write_gray_q[0], ~write_gray_q[1]};
        end
    end

    // [数据] 不用送入复位树，读权限来自新会话的同步指针。
    always_ff @(posedge clk_core_i) begin
        if (write_word && !(^write_gray_q)) word0_q <= {tag_core_i, word_core_i};
        if (write_word &&  (^write_gray_q)) word1_q <= {tag_core_i, word_core_i};
    end

    always_ff @(posedge clk_io_i or negedge io_reset_n) begin
        if (!io_reset_n) begin
            write_io_meta_q <= '0;
            write_io_sync_q <= '0;
            read_gray_q     <= '0;
        end else begin
            write_io_meta_q <= write_gray_q;
            write_io_sync_q <= write_io_meta_q;
            if (valid_io_o) read_gray_q <= {read_gray_q[0], ~read_gray_q[1]};
        end
    end
endmodule

`default_nettype wire
