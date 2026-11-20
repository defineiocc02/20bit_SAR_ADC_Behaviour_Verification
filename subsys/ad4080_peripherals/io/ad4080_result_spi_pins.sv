`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_result_spi_pins.sv
// 模块名     : ad4080_result_spi_pins
// 版本       : v1.0
// 功能       : 图 87/88 的 DCS 窗口与真实 DCLK 下降沿发射。
// 设计说明   :
//   * 捕获已经完成才准入新 DCS 的第一个 DCLK。头的定义来自唯一 framer，
//     八个外部 DCLK 给原单口的首次读取和异步 FIFO 同步留下时间。
//   * 单槽捆绑数据邮箱只保留一个位组。位组在 DCLK 下降沿真正装入 SDO，
//     返回令牌再推进 framer。邮箱不过期或边沿少一个都不会补发旧位。
//   * 采用最快允许 DCLK 时必须证明每周期能够换新位组。IO 域到 DCLK
//     是捆绑数据时间合同，需把令牌与数据在下降沿前的裕量一起做 STA。
//   * 表 6 D6/SDOD 在四线 SPI 未选时仍驱动。空闲值保持已装位或复位零，
//     不依作为器件给定的数据。VDD/隔离撤销必须立即切断所有 OE。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_result_spi_pins (
    // --- 常开 I/O 服务域与合法捕获资格 ---
    input  logic        clk_io_i,              // 连续运行，按 DCLK 最小周期预算补邮箱
    input  logic        rst_ni,                // 关闭时抑制 OE，并一起作废两个域
    input  logic [1:0]  mode_io_i,             // 1 单线，2 四线，活动 DCS 期间不得切换
    input  logic        capture_full_i,        // 同一记录真 FULL，外部观察该资格才下 DCS
    input  logic        session_io_i,          // 现有记录适配器的活跃载荷授权

    // --- 唯一 framer 的 SPI 位组，不含由外边沿直接发出的头 ---
    input  logic [7:0]  header_i,              // 固定 AA，和 framer 同一来源
    input  logic        group_valid_io_i,      // 保持组及末组标志到实际发射回执
    input  logic [3:0]  group_io_i,            // 单线 [0]，四线 [3:0]→A/B/C/D
    input  logic        group_last_io_i,       // 当前配置的最后一组（前缀或约定 CRC）
    output logic        group_taken_io_o,      // DCLK 已真正装载该组，且是本队列令牌
    output logic        payload_ready_io_o,   // 诊断：当前载荷邮箱有位组，不是主机额外协议
    output logic        abort_io_o,            // 失配边沿、欠载或提前撤片选

    // --- 与配置 SPI 完全分离的结果引脚 ---
    input  logic        dcs_ni,                // 模式 3 片选，低电平为同一物理窗口
    input  logic        dclk_i,                // 空闲高，下降沿更新 SDO
    output logic [3:0]  sdo_o,                 // [3:0] 为物理 A/B/C/D，单线只有 B 有效
    output logic [3:0]  sdo_oe_o,              // 四线 D 为表 6 特例，未选仍驱动
    output logic        selected_async_o       // 真首 DCLK 已发头，核心需两级同步后才可读 SRAM
);
    // =========================================================================
    // 1. I/O 域的单槽邮箱和返回令牌
    // =========================================================================
    logic mailbox_reset_ni, select_reset_ni, claim_reset_ni, spi_mode, quad_mode;
    logic mailbox_pending_q, tx_token_q, tx_last_q;
    logic [3:0] tx_group_q;
    logic ack_dclk_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] ack_sync_q, dcs_sync_q, fault_sync_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] selected_sync_q;
    logic window_fault_q, window_started_q, window_done_q, capture_claimed_q;
    logic dcs_previous_q, close_early_q, finished_io_q, admitted_window_io_q;
    logic completed_group, dcs_rise_io;
    logic [3:0] header_index_q;  // 0..7 在头，8 表示下一个下降沿必须有数据
    logic header_fire, payload_fire, slot_available;
    logic header_bit;

    assign spi_mode = mode_io_i == 2'd1 || mode_io_i == 2'd2;
    assign quad_mode = mode_io_i == 2'd2;
    assign mailbox_reset_ni = rst_ni && session_io_i;
    assign select_reset_ni = rst_ni && !dcs_ni;
    assign claim_reset_ni = rst_ni && (capture_full_i || session_io_i);
    assign group_taken_io_o = mailbox_pending_q && ack_sync_q[1] == tx_token_q;
    assign completed_group = group_taken_io_o && tx_last_q;
    assign payload_ready_io_o = mailbox_reset_ni && mailbox_pending_q;
    assign selected_async_o = rst_ni && !dcs_ni && spi_mode && window_started_q;

    // 令牌和位组从发出保持到真的 DCLK 退役。前八拍头可能在 session
    // 启动前已经出线，因此邮箱的 reset 不得复位 DCS 窗口计数。
    always_ff @(posedge clk_io_i or negedge mailbox_reset_ni) begin
        if (!mailbox_reset_ni) begin
            ack_sync_q <= '0;
            mailbox_pending_q <= 1'b0;
            tx_token_q <= 1'b0;
            tx_last_q <= 1'b0;
            tx_group_q <= '0;
        end else begin
            ack_sync_q <= {ack_sync_q[0], ack_dclk_q};
            if (group_taken_io_o) mailbox_pending_q <= 1'b0;
            else if (!mailbox_pending_q && group_valid_io_i && !abort_io_o) begin
                tx_group_q <= quad_mode ? group_io_i : {1'b0, group_io_i[0], 2'b00};
                tx_last_q <= group_last_io_i;
                tx_token_q <= !tx_token_q;
                mailbox_pending_q <= 1'b1;
            end
        end
    end

    // 已经完整接下最后一次 DCLK 时，可能恰好同时观察到主机收片选。
    // 终态和最后一次实际回执优先，避免合法前缀被误判成提前中止。
    assign dcs_rise_io = !dcs_previous_q && dcs_sync_q[1];
    assign abort_io_o = close_early_q || fault_sync_q[1];
    always_ff @(posedge clk_io_i or negedge rst_ni) begin
        if (!rst_ni) begin
            dcs_sync_q <= '1;
            dcs_previous_q <= 1'b1;
            fault_sync_q <= '0;
            selected_sync_q <= '0;
            close_early_q <= 1'b0;
            finished_io_q <= 1'b0;
            admitted_window_io_q <= 1'b0;
        end else begin
            dcs_sync_q <= {dcs_sync_q[0], dcs_ni};
            dcs_previous_q <= dcs_sync_q[1];
            fault_sync_q <= {fault_sync_q[0], window_fault_q};
            selected_sync_q <= {selected_sync_q[0], selected_async_o};
            if (!capture_full_i && !session_io_i) finished_io_q <= 1'b0;
            else if (completed_group) finished_io_q <= 1'b1;

            // 未满时开始的 DCS 即使延续到 FULL 也未取得任何读权，收片选
            // 不可作废刚完成的另一条记录。只关闭成功出过第一头位的窗口。
            if (!spi_mode || (!capture_full_i && !session_io_i)) begin
                close_early_q <= 1'b0;
                admitted_window_io_q <= 1'b0;
            end else if (dcs_rise_io) begin
                if (admitted_window_io_q && !finished_io_q && !completed_group) close_early_q <= 1'b1;
                admitted_window_io_q <= 1'b0;
            end else if (selected_sync_q[1]) admitted_window_io_q <= 1'b1;
        end
    end

    // =========================================================================
    // 2. DCLK 域实际装载头、SS 位及可选已绑定尾
    // =========================================================================
    assign slot_available = mailbox_reset_ni && mailbox_pending_q && ack_dclk_q != tx_token_q;
    assign header_bit = header_i[3'd7-header_index_q[2:0]];
    assign header_fire = rst_ni && !dcs_ni && spi_mode && !window_fault_q &&
                         header_index_q < 4'd8 && (window_started_q || (capture_full_i && !capture_claimed_q));
    assign payload_fire = rst_ni && !dcs_ni && spi_mode && window_started_q &&
                          !window_fault_q && !window_done_q && header_index_q == 4'd8 && slot_available;

    // 未满的尝试不给头。八个头周期结束后若无新组，记录失败而非继续移位
    // 上个词。额外 DCLK 只能观察已结束状态，不能产生新的“有效记录”。
    always_ff @(negedge dclk_i or negedge select_reset_ni) begin
        if (!select_reset_ni) begin
            header_index_q <= '0;
            window_fault_q <= 1'b0;
            window_started_q <= 1'b0;
            window_done_q <= 1'b0;
        end else if (spi_mode && !window_done_q && !window_fault_q) begin
            if (header_fire) begin
                window_started_q <= 1'b1;
                header_index_q <= header_index_q + 1'b1;
            end else if (payload_fire) begin
                if (tx_last_q) window_done_q <= 1'b1;
            end else window_fault_q <= 1'b1;
        end
    end

    // 已经在引脚启动的捕获不能仅通过抬高 DCS 再读一遍。直到上层释放
    // 该捕获（成功退役或作废），重新选中不会产生新的假 AA。
    always_ff @(negedge dclk_i or negedge claim_reset_ni) begin
        if (!claim_reset_ni) capture_claimed_q <= 1'b0;
        else if (header_fire && header_index_q == 0) capture_claimed_q <= 1'b1;
    end

    // DCS 上升不能撤销已完成的邮箱回执。四线 SDOD 曾发的值在未选时保留，
    // 不用于推断延长 SCLK 的器件真实行为。所有已认可位都在下降沿锁存。
    always_ff @(negedge dclk_i or negedge mailbox_reset_ni) begin
        if (!mailbox_reset_ni) ack_dclk_q <= 1'b0;
        else if (payload_fire) ack_dclk_q <= tx_token_q;
    end
    always_ff @(negedge dclk_i or negedge rst_ni) begin
        if (!rst_ni) sdo_o <= '0;
        else if (header_fire) sdo_o <= quad_mode ? {4{header_bit}} : {1'b0, header_bit, 2'b00};
        else if (payload_fire) sdo_o <= tx_group_q;
    end

    always_comb begin
        sdo_oe_o = '0;
        if (rst_ni && spi_mode) begin
            if (quad_mode) sdo_oe_o[0] = 1'b1;
            if (!dcs_ni && !window_fault_q && (window_started_q || (capture_full_i && !capture_claimed_q)))
                sdo_oe_o = quad_mode ? 4'b1111 : 4'b0100;
        end
    end
endmodule

`default_nettype wire
