`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_result_transport.sv
// 模块名     : ad4080_result_transport
// 版本       : v1.0
// 功能       : 跨域搬运逐笔转换结果和模式，在 IO 域逐拍交付 DDR 装载双沿位。
// 设计说明   :
//   * 完整 22 位字由 Gray FIFO 搬运，地址所属字槽随指针握手交接。
//     满和等对侧上线都可拒收，核心本地复位释放后才开始统计丢弃。
//   * 任一域复位或共同电源许可撤销同时失效两边队列元数据和在发帧。
//     各域独立同步释放，收到对侧已释放后方可开始新的会话。
//   * IO 装载拍由上层定位。位映射调用唯一的 ad4080_ddr_edge_words，
//     末槽与下一字装入可同沿发生，PHY 必须在该沿采走旧槽。
//   * 逻辑端口覆盖连续无编码 LVDS。相对 CNV 的起始拍、DCO、双沿输出、
//     Pad 隔离与 OE 由 PHY 和上层时序及供电合同提供。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 审查后报告本地复位释放至双域上线之间的拒收，和已上线满写分开诊断。
// =============================================================================
module ad4080_result_transport #(
    parameter int unsigned FIFO_ADDR_WIDTH = 3  // FIFO 深度 2^N；输出在发帧独占一个额外寄存器
) (
    // --- 时钟与共代失效 ---
    input  logic        clk_core_i,           // 结果提交所在核心时钟
    input  logic        clk_io_i,             // 已定位的 DDR pair 装载拍
    input  logic        core_reset_ni,        // 低有效，置低冲刷整条链路
    input  logic        io_reset_ni,          // 低有效，置低冲刷整条链路
    input  logic        power_good_async_i,   // 常开域合成的双电源资格，低时共同冲刷

    // --- 无回压核心结果入口 ---
    input  logic [19:0] result_core_i,        // 原结果位串，和两个配置位同沿提交
    input  logic        result_valid_core_i,  // 每个高电平上升沿代表新结果
    input  logic        dual_lane_core_i,     // 随样本冻结，在 IO 域保持到本帧退休
    input  logic        intf_check_core_i,    // 该笔使用固定测试字，随样本跨域
    output logic        core_online_o,        // 本域可工作且收到对侧同步释放
    output logic        core_full_o,          // 核心域保守满，可能等待远端指针同步
    output logic        core_accept_o,        // 本沿将入队的可观测组合资格
    output logic        core_drop_o,          // 本地复位释放后未能入队的提交，沿后报告一拍
    output logic        core_overflow_o,      // 同一会话中首次满且提交后粘滞
    output logic        core_drained_o,       // 所有受理结果均越过 IO 末槽的核心侧确认

    // --- 无回压 DDR 宏装载端 ---
    input  logic        io_start_allow_i,     // IO 域允许领取新帧；在发帧持续推进
    output logic        io_online_o,          // PHY 可在本域观察的链路复位释放许可
    output logic        io_pair_valid_o,      // 高时本上升沿 PHY 必须收走当前双沿位
    output logic        io_first_o,           // 本槽为连续帧的首槽
    output logic        io_last_o,            // 本槽为连续帧的末槽
    output logic        io_dual_lane_o,       // 只与有效槽同拍使用，DB OE 另由 PHY 实现
    output logic        io_intf_check_o,      // 只与有效槽同拍使用，标记固定字来源
    output logic        io_da_fall_o,         // 当前槽 DA 先发下降沿位
    output logic        io_da_rise_o,         // 当前槽 DA 随后的上升沿位
    output logic        io_db_fall_o,         // 当前槽 DB 先发下降沿位，单 lane 时为 0
    output logic        io_db_rise_o          // 当前槽 DB 随后的上升沿位
);

    // =========================================================================
    // 1. 跨域共用复位请求及本域准入
    // =========================================================================
    localparam int unsigned DEPTH = 1 << FIFO_ADDR_WIDTH;  // 要求 2 的幂，便于 Gray 回绕
    localparam int unsigned PTR_WIDTH = FIFO_ADDR_WIDTH + 1;
    localparam logic [PTR_WIDTH-1:0] FULL_MASK = {2'b11, {(FIFO_ADDR_WIDTH-1){1'b0}}};

    logic epoch_reset_n, core_reset_n, io_reset_n;
    logic core_alive_q, io_alive_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] core_reset_pipe_q, io_reset_pipe_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] io_alive_core_q, core_alive_io_q;

`ifndef SYNTHESIS
    // [参数] 深度二合法；正地址宽度限定在整型深度能表达的范围内。
    if (FIFO_ADDR_WIDTH < 1 || FIFO_ADDR_WIDTH > 30) begin : g_bad_address_width
        initial $fatal(1, "FIFO_ADDR_WIDTH must be in 1..30");
    end
`endif

    // [掉电] 两复位源与常开电源资格组合成统一的异步置低。实现时需要
    // 经批准的 reset-tree/隔离单元；有一侧停钟时也要封闭双方的接口。
    assign epoch_reset_n = core_reset_ni && io_reset_ni && power_good_async_i;
    assign core_reset_n = core_reset_pipe_q[1];
    assign io_reset_n = io_reset_pipe_q[1];
    assign core_online_o = epoch_reset_n && core_reset_n && io_alive_core_q[1];
    assign io_online_o = epoch_reset_n && io_reset_n && core_alive_io_q[1];

    always_ff @(posedge clk_core_i or negedge epoch_reset_n) begin
        if (!epoch_reset_n) core_reset_pipe_q <= '0;
        else core_reset_pipe_q <= {core_reset_pipe_q[0], 1'b1};
    end

    always_ff @(posedge clk_io_i or negedge epoch_reset_n) begin
        if (!epoch_reset_n) io_reset_pipe_q <= '0;
        else io_reset_pipe_q <= {io_reset_pipe_q[0], 1'b1};
    end

    // =========================================================================
    // 2. 字槽所有权、跨域指针和核心入口
    // =========================================================================
    // [原子性] 地址槽仅由核心侧写，IO 侧在已同步的写指针宣布可读后
    // 锁存整字。指针握手保证字槽稳定，不能分别给数据或模式打同步器。
    logic [21:0] fifo_memory_q [0:DEPTH-1];
    logic [PTR_WIDTH-1:0] write_bin_q, write_gray_q;
    logic [PTR_WIDTH-1:0] read_bin_q, read_gray_q;
    logic [PTR_WIDTH-1:0] retired_bin_q, retired_gray_q;
    (* ASYNC_REG = "TRUE" *) logic [PTR_WIDTH-1:0] read_gray_core_s1_q, read_gray_core_s2_q;
    (* ASYNC_REG = "TRUE" *) logic [PTR_WIDTH-1:0] retired_gray_core_s1_q, retired_gray_core_s2_q;
    (* ASYNC_REG = "TRUE" *) logic [PTR_WIDTH-1:0] write_gray_io_s1_q, write_gray_io_s2_q;
    logic full_core, empty_io, load_frame;

    function automatic logic [PTR_WIDTH-1:0] binary_to_gray(input logic [PTR_WIDTH-1:0] value);
        binary_to_gray = value ^ (value >> 1);
    endfunction

    // [满空] 当前指针判满，深度的最后一个格子仍可写。只看两级同步的
    // 远端指针可能晚几拍开放容量，不能误把一个未读槽当成空槽写入。
    assign full_core = write_gray_q == (read_gray_core_s2_q ^ FULL_MASK);
    assign empty_io = read_gray_q == write_gray_io_s2_q;
    assign core_full_o = core_online_o && full_core;
    assign core_accept_o = core_online_o && result_valid_core_i && !full_core;

    // [排空] read 指针递增时数据仍有整帧在 IO 端。用对应末槽退休的
    // Gray 指针做排空确认，避免测试接管时截断最后一帧。
    assign core_drained_o = core_online_o && !core_accept_o &&
                            write_gray_q == retired_gray_core_s2_q;

    // [RDC] 本域先在已释放复位的时钟沿标记 alive，再将该数据位同步给
    // 对侧。复位树节点仅用来复位，避免兼作远端逻辑的握手数据。
    always_ff @(posedge clk_core_i or negedge core_reset_n) begin
        if (!core_reset_n) begin
            core_alive_q           <= 1'b0;
            io_alive_core_q        <= '0;
            read_gray_core_s1_q    <= '0;
            read_gray_core_s2_q    <= '0;
            retired_gray_core_s1_q <= '0;
            retired_gray_core_s2_q <= '0;
        end else begin
            core_alive_q           <= 1'b1;
            io_alive_core_q        <= {io_alive_core_q[0], io_alive_q};
            read_gray_core_s1_q    <= read_gray_q;
            read_gray_core_s2_q    <= read_gray_core_s1_q;
            retired_gray_core_s1_q <= retired_gray_q;
            retired_gray_core_s2_q <= retired_gray_core_s1_q;
        end
    end

    always_ff @(posedge clk_io_i or negedge io_reset_n) begin
        if (!io_reset_n) begin
            io_alive_q        <= 1'b0;
            core_alive_io_q    <= '0;
            write_gray_io_s1_q <= '0;
            write_gray_io_s2_q <= '0;
        end else begin
            io_alive_q        <= 1'b1;
            core_alive_io_q    <= {core_alive_io_q[0], core_alive_q};
            write_gray_io_s1_q <= write_gray_q;
            write_gray_io_s2_q <= write_gray_io_s1_q;
        end
    end

    // [故障] 本地复位释放后，仍等对侧上线或 FIFO 满的提交都报告丢弃。
    // 只有已上线的满写锁住 overflow。硬复位及本地同步释放期间不记样本。
    always_ff @(posedge clk_core_i or negedge core_reset_n) begin
        if (!core_reset_n) begin
            write_bin_q     <= '0;
            write_gray_q    <= '0;
            core_drop_o     <= 1'b0;
            core_overflow_o <= 1'b0;
        end else begin
            core_drop_o <= result_valid_core_i && !core_accept_o;
            if (core_full_o && result_valid_core_i) core_overflow_o <= 1'b1;
            if (core_accept_o) begin
                write_bin_q  <= write_bin_q + 1'b1;
                write_gray_q <= binary_to_gray(write_bin_q + 1'b1);
            end
        end
    end

    // [复位] 数据阵列可保持旧内容。新会话的写指针从零开始，重新写入并
    // 同步发布后 IO 才能读到该地址，避免复位阵列导致不必要的清零布线。
    always_ff @(posedge clk_core_i) begin
        if (core_accept_o)
            fifo_memory_q[write_bin_q[FIFO_ADDR_WIDTH-1:0]] <=
                {intf_check_core_i, dual_lane_core_i, result_core_i};
    end

    // =========================================================================
    // 3. IO 在发帧与逻辑槽
    // =========================================================================
    logic frame_active_q;
    logic [21:0] frame_q;                // 位 21 测试，位 20 双 lane，低 20 位结果
    logic [3:0] slot_q, slot_count;
    logic [9:0] da_fall_slots, da_rise_slots, db_fall_slots, db_rise_slots;

    // [位序] 复用唯一组合映射逻辑。本块只保有帧状态和有效槽索引；
    // 物理 FALL→RISE 装载顺序与宏所支持的边沿配对由集成方闭合。
    ad4080_ddr_edge_words u_edge_words (
        .sample_i(frame_q[19:0]), .dual_lane_i(frame_q[20]), .intf_check_i(frame_q[21]),
        .da_fall_o(da_fall_slots), .da_rise_o(da_rise_slots),
        .db_fall_o(db_fall_slots), .db_rise_o(db_rise_slots), .slot_count_o(slot_count)
    );

    assign io_pair_valid_o = io_online_o && frame_active_q;
    assign io_first_o = io_pair_valid_o && slot_q == 0;
    assign io_last_o = io_pair_valid_o && slot_q == slot_count - 4'd1;
    assign io_dual_lane_o = io_pair_valid_o && frame_q[20];
    assign io_intf_check_o = io_pair_valid_o && frame_q[21];
    assign io_da_fall_o = io_pair_valid_o && da_fall_slots[slot_q];
    assign io_da_rise_o = io_pair_valid_o && da_rise_slots[slot_q];
    assign io_db_fall_o = io_pair_valid_o && db_fall_slots[slot_q];
    assign io_db_rise_o = io_pair_valid_o && db_rise_slots[slot_q];

    // [同沿] 末槽仍由旧帧驱动并被 PHY 取走；下一笔若可领取，则同一
    // 沿锁存。下一周期从槽零连续推进。禁止把 io_start_allow 当反压用。
    assign load_frame = io_online_o && io_start_allow_i && !empty_io &&
                        (!frame_active_q || io_last_o);

    always_ff @(posedge clk_io_i or negedge io_reset_n) begin
        if (!io_reset_n) begin
            read_bin_q     <= '0;
            read_gray_q    <= '0;
            retired_bin_q  <= '0;
            retired_gray_q <= '0;
            frame_active_q <= 1'b0;
            frame_q        <= '0;
            slot_q         <= '0;
        end else begin
            if (io_last_o) begin
                retired_bin_q  <= retired_bin_q + 1'b1;
                retired_gray_q <= binary_to_gray(retired_bin_q + 1'b1);
            end

            if (load_frame) begin
                read_bin_q     <= read_bin_q + 1'b1;
                read_gray_q    <= binary_to_gray(read_bin_q + 1'b1);
                frame_q        <= fifo_memory_q[read_bin_q[FIFO_ADDR_WIDTH-1:0]];
                frame_active_q <= 1'b1;
                slot_q         <= '0;
            end else if (io_last_o) begin
                frame_active_q <= 1'b0;
                slot_q         <= '0;
            end else if (io_pair_valid_o) begin
                slot_q <= slot_q + 1'b1;
            end
        end
    end
endmodule

`default_nettype wire
