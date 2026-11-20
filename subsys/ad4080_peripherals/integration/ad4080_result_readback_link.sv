`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_result_readback_link.sv
// 模块名     : ad4080_result_readback_link
// 版本       : v1.0
// 功能       : 预留异步队列空位，将采后 SRAM 读回的 20 位载荷交付单 lane DDR pair。
// 设计说明   :
//   * 在途读只有一笔。端口接受这一笔时独占保留一个空位，直到下个核心沿
//     收走 SRAM 返回。按 Rev. B p.59 仅准 FIFO 单 lane 载荷读回，
//     拒绝双 lane 或检查码申请。完整事务的首字节和 CRC24 留外层组帧。
//   * 功能 IP 保留单口写和读的外部仲裁。适配器接管该连接上的全部读返回。
//   * I/O 会话或任一端复位失效时同时冲刷预留、队列和半帧。I/O 当前只
//     暂停领取新帧时保持会话，允许正在发送的帧走完。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_result_readback_link #(
    parameter int unsigned FIFO_ADDR_WIDTH = 3  // 异步队列深度为 2^N，另有 I/O 在发槽寄存器
) (
    // --- 会话与本域时钟 ---
    input  logic        clk_core_i,                     // 结果存储的同一核心时钟
    input  logic        clk_io_i,                       // PHY 的 pair 装载时钟
    input  logic        fifo_reset_ni,                  // 低有效，连接壳层 FIFO 硬复位
    input  logic        io_reset_ni,                    // 低有效，连接壳层 I/O 接口复位
    input  logic        io_session_allow_i,             // 壳层 I/O 字许可，撤销时共代冲刷
    input  logic        io_start_allow_i,               // 本域帧调度；低时暂缓新帧

    // --- 核心域上游读描述符与壳层单口仲裁侧 ---
    input  logic        read_req_valid_core_i,          // 地址/模式持至读受理或明确模式拒绝
    output logic        read_req_ready_core_o,          // 当前能预留运输空位并取得 SRAM 读口
    input  logic [13:0] read_addr_core_i,               // 逻辑 14 位，银行映射由 SRAM 执行
    input  logic        read_dual_lane_core_i,           // 此入口仅单 lane，请求为 1 时拒绝
    input  logic        read_intf_check_core_i,          // 接口检查属于实时口，请求为 1 时拒绝
    output logic        sram_read_req_valid_core_o,     // 给功能口仲裁器，只经读选择通路
    input  logic        sram_read_req_ready_core_i,     // 此拍已选中本读参数，且壳层有读许可
    output logic [13:0] sram_read_addr_core_o,          // 与读选择通路一起转发
    input  logic        sram_read_rsp_valid_core_i,     // SRAM 读受理后下一拍取样，专供此链接
    input  logic [19:0] sram_read_rsp_data_core_i,     // 与上述返回有效同拍取得完整 20 位字

    // --- 核心诊断及排空 ---
    output logic        link_idle_core_o,              // 无预留且末槽均退休，或会话已被冲刷
    output logic        link_online_core_o,            // transport 已见两域释放，可以检查容量
    output logic        link_full_core_o,              // 两级指针同步给出的保守满状态
    output logic        read_mode_error_core_o,        // 与非法 valid 同周期，ready 低且 SRAM 无事务
    output logic        read_protocol_error_core_o,    // 缺失/意外有效或预留无法交付，复位前禁止新读
    output logic        transport_drop_core_o,         // 预留契约若破坏导致满丢，保留底层诊断
    output logic        transport_overflow_core_o,     // 丢弃满队列后的会话粘滞记录

    // --- I/O 域无反压 DDR pair 输出 ---
    output logic        io_online_o,                   // 两个域完成共同会话的释放握手
    output logic        io_pair_valid_o,               // 当前上升沿交付一个双沿 pair
    output logic        io_first_o,                    // 当前帧第一槽
    output logic        io_last_o,                     // 当前帧最后一槽
    output logic        io_dual_lane_o,                // FIFO 载荷入口固定单 lane
    output logic        io_intf_check_o,                // FIFO 载荷入口始终选择实读数据
    output logic        io_da_fall_o,                   // DA 此槽第一边沿位
    output logic        io_da_rise_o,                   // DA 此槽随后边沿位
    output logic        io_db_fall_o,                   // DB 单 lane 模式恒零
    output logic        io_db_rise_o                    // DB 此槽随后边沿位
);
    // =========================================================================
    // 1. 会话失效、容量预留与 SRAM 返回配对
    // =========================================================================
    logic core_epoch_no;
    logic can_reserve, reserve_now, transport_accept, transport_drained;
    logic read_pending_q;           // 置位的下一核心沿必须看到且交走恰好一个返回
    logic mode_supported;           // 读指针只能按真正的单 lane 受理前移
    logic retired_idle_q;           // 核心寄存的末槽退休确认，冲刷时回空

    ad4080_pwr_reset_sync u_local_epoch (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_ni && io_reset_ni && io_session_allow_i),
        .ready_no(core_epoch_no)
    );

    // [容量] 此处是 transport 唯一写入源。在该笔返回之前禁止再开一笔读，
    // 因而已看见的空位不能被别的源消耗。读仲裁停住时不提前消耗预留。
    // 多余返回会抢先封住发起口，避免把无法配对的数据赋给一笔新地址。
    assign mode_supported = !read_dual_lane_core_i && !read_intf_check_core_i;
    assign read_mode_error_core_o = read_req_valid_core_i && !mode_supported;
    assign can_reserve = mode_supported && core_epoch_no && link_online_core_o && !link_full_core_o && !read_pending_q &&
                         !read_protocol_error_core_o && !sram_read_rsp_valid_core_i;
    assign sram_read_req_valid_core_o = read_req_valid_core_i && can_reserve;
    assign sram_read_addr_core_o = read_addr_core_i;
    assign read_req_ready_core_o = can_reserve && sram_read_req_ready_core_i;
    assign reserve_now = sram_read_req_valid_core_o && sram_read_req_ready_core_i;

    // [排空] 末槽退休比 FIFO 弹出晚。单口已受理但还没入队的那一笔也算在途。
    // 核心寄存这个确认，使 BIST 启动后 I/O 共同冲刷不撤销已经获得的静默确认。
    assign link_idle_core_o = retired_idle_q && !read_pending_q && !reserve_now;

    always_ff @(posedge clk_core_i or negedge core_epoch_no) begin
        if (!core_epoch_no) retired_idle_q <= 1'b1;
        else retired_idle_q <= transport_drained;
    end

    // [时序] 共同冲刷优先。待返回的固定一拍窗口内，transport 必须实际
    // 接受。如果缺失、重复或无法交付，锁存诊断并停发读以免错配下笔数据。
    // 已成功排队的早期帧按原时序退休，新的会话才清除本诊断。
    always_ff @(posedge clk_core_i or negedge core_epoch_no) begin
        if (!core_epoch_no) begin
            read_pending_q <= 1'b0;
            read_protocol_error_core_o <= 1'b0;
        end else if (link_online_core_o) begin
            if (read_pending_q) begin
                read_pending_q <= 1'b0;
                if (!transport_accept) read_protocol_error_core_o <= 1'b1;
            end else if (sram_read_rsp_valid_core_i) begin
                read_protocol_error_core_o <= 1'b1;
            end else if (reserve_now) begin
                read_pending_q <= 1'b1;
            end
        end
    end

    // =========================================================================
    // 2. 唯一的跨域搬运和边沿映射路径
    // =========================================================================
    // [边界] io_session_allow 使用共代复位，能在中途取消当前帧。普通停发
    // 只通过 io_start_allow，留出现有帧的最后一槽。映射由 transport 调用
    // 原 mapper，壳层的另一路组合边沿向量无需接到这条物理装载口。
    ad4080_result_transport #(.FIFO_ADDR_WIDTH(FIFO_ADDR_WIDTH)) u_transport (
        .clk_core_i, .clk_io_i, .core_reset_ni(fifo_reset_ni), .io_reset_ni,
        .power_good_async_i(io_session_allow_i),
        .result_core_i(sram_read_rsp_data_core_i),
        .result_valid_core_i(read_pending_q && sram_read_rsp_valid_core_i),
        .dual_lane_core_i(1'b0), .intf_check_core_i(1'b0),
        .core_online_o(link_online_core_o), .core_full_o(link_full_core_o),
        .core_accept_o(transport_accept), .core_drop_o(transport_drop_core_o),
        .core_overflow_o(transport_overflow_core_o), .core_drained_o(transport_drained),
        .io_start_allow_i, .io_online_o, .io_pair_valid_o, .io_first_o, .io_last_o,
        .io_dual_lane_o, .io_intf_check_o, .io_da_fall_o, .io_da_rise_o, .io_db_fall_o, .io_db_rise_o
    );
endmodule

`default_nettype wire
