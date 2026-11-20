`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : cnv_ctrl.sv
// 模块名     : cnv_ctrl
// 版本       : v1.0
// 功能       : 监控已译码 CNV 边沿，或量化检查核心域 CNV tick 的最短间隔。
// 设计说明   :
//   * 定时模式由上层显式绑定 clk 频率。Rev. B p.7 表 2 的 25 ns 下限
//     上取整成核心周期，输出给 p.83 表 47 的状态所有者。
//   * 同沿优先级为异步复位、观察 tick 并重装等待、空拍推进等待。每笔
//     tick 结束上一笔间隔并成为新参考，包括过快或遇忙的事件。
//   * 译码电平模式保留两级同步加上升沿及忙检测。定时输入由外部保证与
//     clk 同步，每个高电平采样沿是一笔，允许相邻事件。
//   * 端口为监视事件和异常上报。差分 CNV、亚拍时间、转换发起及结果
//     无效后的处置由 PHY 和转换所有者负责。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 增加显式绑定时钟的核心 tick 最短间隔检查。
// =============================================================================
module cnv_ctrl #(
    parameter bit          P_USE_CNV_TICK = 1'b0,  // 1 采用已同步的直接事件与最短间隔
    parameter int unsigned CORE_CLOCK_HZ  = 0      // 定时模式必须绑定实际核心频率，单位 Hz
) (
    // --- 全局与事件源 ---
    input  logic clk,          // busy 与定时模式 cnv_tick_i 所在域
    input  logic rst_n,        // 低有效异步置位，同步释放由时钟所有者保证
    input  logic cnv_p,        // 旧接口的已译码电平，仅电平模式取样
    input  logic busy,         // 可选转换者碰撞条件，隔离时序检查时可接 0

    // --- 观测与诊断 ---
    output logic cnv_pulse,    // 观察到的事件，即使该笔报错也保留事件
    output logic cnv_err,      // 每个异常采样沿后为高，由同域状态位捕捉
    input  logic cnv_tick_i    // 定时模式的同步输入，未选模式接 0
);

    // =========================================================================
    // 1. 兼容的已译码电平入口
    // =========================================================================
    if (!P_USE_CNV_TICK) begin : g_level_input
        (* ASYNC_REG = "TRUE" *) logic input_meta, input_sync;
        logic previous;
        wire unused_cnv_tick = cnv_tick_i;  // 只在定时模式消费此端口

        // [时序] 边沿提取在同步后完成。原接口要求稳定电平能被同步采到，
        // 脉冲与 busy 同域有效；错误报告相对于边沿事件延后一个采样沿。
        assign cnv_pulse = input_sync && !previous;
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                input_meta <= 1'b0;
                input_sync <= 1'b0;
                previous   <= 1'b0;
                cnv_err    <= 1'b0;
            end else begin
                input_meta <= cnv_p;
                input_sync <= input_meta;
                previous   <= input_sync;
                cnv_err    <= cnv_pulse && busy;
            end
        end

    // =========================================================================
    // 2. 核心域 tick 与最短间隔
    // =========================================================================
    end else if (CORE_CLOCK_HZ == 0) begin : g_missing_frequency
        // [参数] 禁止用默认频率代替上层时钟绑定，仿真和综合应拒绝此构型。
        initial $fatal(1, "cnv_ctrl: CORE_CLOCK_HZ must be positive in CNV tick mode");
        assign cnv_pulse = 1'b0;
        assign cnv_err   = 1'b0;
    end else begin : g_tick_input
        localparam int unsigned CNV_LIMIT_HZ = 40_000_000;  // 1 / 25 ns
        // [上取整] 用商和非零余数，避免先乘 25 或先加除数引起整数溢出。
        localparam int unsigned MIN_CNV_CYCLES = (CORE_CLOCK_HZ / CNV_LIMIT_HZ) +
            ((CORE_CLOCK_HZ % CNV_LIMIT_HZ == 0) ? 0 : 1);
        localparam int unsigned WAIT_WIDTH = (MIN_CNV_CYCLES > 1) ? $clog2(MIN_CNV_CYCLES) : 1;
        localparam logic [WAIT_WIDTH-1:0] RELOAD_WAIT = WAIT_WIDTH'(MIN_CNV_CYCLES - 1);

        logic [WAIT_WIDTH-1:0] remaining_q;  // 本沿开始前仍不可受理的采样沿数
        wire unused_cnv_level = cnv_p;        // 该源不参与定时模式

        // [并行] 回显任何已定位的 tick，监视错误不会悄悄改变事件身份。
        // 相邻两沿都可观察到 tick；物理 CNV 是否折叠由外部桥负责。
        assign cnv_pulse = rst_n && cnv_tick_i;

        // [区间] tick 在 n 沿采下，重装 N-1。n+N-1 尚有一拍待等，
        // n+N 前计数已为零，恰好可接收。首事件与长空闲均用饱和的零值。
        // 每个错误 tick 也重置间隔参考，避免越过过快一笔后误报节拍合法。
        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                remaining_q <= '0;
                cnv_err     <= 1'b0;
            end else begin
                cnv_err <= cnv_tick_i && (busy || remaining_q != '0);
                if (cnv_tick_i)
                    remaining_q <= RELOAD_WAIT;
                else if (remaining_q != '0)
                    remaining_q <= remaining_q - 1'b1;
            end
        end
    end
endmodule

`default_nettype wire
