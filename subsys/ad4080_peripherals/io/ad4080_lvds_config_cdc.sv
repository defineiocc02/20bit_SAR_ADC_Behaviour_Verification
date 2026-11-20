`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_lvds_config_cdc.sv
// 模块名     : ad4080_lvds_config_cdc
// 版本       : v1.0
// 功能       : 以源侧保持加双向确认，将输出接口 A/B/C 和滤波配置的同一组 CSR 快照交付到 IO 域。
// 设计说明   :
//   * 来源是实际 CSR。待确认期间锁住整组字节，同步器只传申请及确认翻转位。
//     来源端 ready 表示当前 CSR 已在 IO 空闲边界应用，不是单次写入脉冲。
//   * IO 忙时保有完整旧快照直至在途帧结束。pending 用于阻止新帧，
//     idle 与许可共同批准换档， applied 在该沿之后报告一拍。
//   * 两域共代异步撤销，各域独立同步释放。复位后自动送当前真实 CSR，
//     包括只失去 IO 供电而核心 CSR 继续保留的情况。
//   * 本模块不解释模式位。传输用的保持和应用寄存器不代替 CSR 所有者。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================
module ad4080_lvds_config_cdc (
    // --- 核心域与当前 CSR ---
    input  logic       clk_core_i,         // 核心 CSR 已在此域稳定
    input  logic       core_reset_ni,      // 0 清除两域投递状态并封闭 IO 快照
    input  logic       cfg_live_core_i,    // 当前 CSR 在本读写纪元已合格，独立于所选模式
    input  logic [7:0] cfg_a_core_i,       // 当前 0x15 全字节
    input  logic [7:0] cfg_b_core_i,       // 当前 0x16 全字节
    input  logic [7:0] cfg_c_core_i, cfg_filter_core_i, // 电气控制字与滤波口径跟随同一次交接
    output logic       cfg_ready_core_o,   // IO 已确认当前快照，供 CSR 写入准入判据使用

    // --- IO 应用握手和已发布快照 ---
    input  logic       clk_io_i,           // 绑定后的本地 IO 装载时钟
    input  logic       io_reset_ni,        // 0 清除两域投递状态，恢复取真实 CSR
    input  logic       io_idle_i,          // 接收者已结束旧在途工作，允许改变快照
    input  logic       io_apply_allow_i,   // IO 域应用资格，冷启阶段可独立给出
    output logic [7:0] cfg_a_io_o,         // 应用的 0x15，invalid 时钳零
    output logic [7:0] cfg_c_io_o, cfg_filter_io_o,     // 有效时与 A/B 同沿生效
    output logic [7:0] cfg_b_io_o,         // 应用的 0x16，和 A 同沿提交
    output logic       cfg_valid_io_o,     // 当前供电纪元至少应用一份已知快照
    output logic       cfg_pending_io_o,   // 同步请求待应用，接收者须停止准入新帧
    output logic       cfg_applied_io_o    // 同沿整组应用并发送确认；下一沿自动消失
);
    // =========================================================================
    // 1. 共同失效与各域复位释放
    // =========================================================================
    logic link_reset_n;
    logic core_ready_n, io_ready_n;

    // [RDC] 单侧复位也结束另一端的申请/应答纪元，避免在停钟恢复后等一个
    // 旧翻转位。保持信号须接电源所有者已经钳位的资格，异步撤销需物理闭合。
    assign link_reset_n = core_reset_ni && io_reset_ni;
    ad4080_pwr_reset_sync u_core_release (
        .clk_i(clk_core_i), .hold_ni(link_reset_n), .ready_no(core_ready_n)
    );
    ad4080_pwr_reset_sync u_io_release (
        .clk_i(clk_io_i), .hold_ni(link_reset_n), .ready_no(io_ready_n)
    );

    // =========================================================================
    // 2. 核心邮箱与回执
    // =========================================================================
    logic [31:0] csr_image, snapshot_core_q;  // A/B/C 与滤波设置作为同一张图交付
    logic request_core_q, sent_core_q;
    logic acknowledge_io_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] acknowledge_core_sync_q;
    logic current_ack_core;

    assign csr_image = {cfg_a_core_i, cfg_b_core_i, cfg_c_core_i, cfg_filter_core_i};
    assign current_ack_core = request_core_q == acknowledge_core_sync_q[1];
    assign cfg_ready_core_o = link_reset_n && core_ready_n && cfg_live_core_i &&
                              sent_core_q && current_ack_core && snapshot_core_q == csr_image;

    // [原子性] 依据已生效的实际 CSR 取快照，免去在 CSR 写入沿捕获旧值。
    // 保存数据直到 IO 回执同步完成。若来源在忙时自行改动，下一个握手会
    // 取届时的整组当前值；软件写入应另受 ready 限制以保留逐笔语义。
    always_ff @(posedge clk_core_i or negedge core_ready_n) begin
        if (!core_ready_n) begin
            acknowledge_core_sync_q <= '0;
            snapshot_core_q         <= '0;
            request_core_q          <= 1'b0;
            sent_core_q             <= 1'b0;
        end else begin
            acknowledge_core_sync_q <= {acknowledge_core_sync_q[0], acknowledge_io_q};
            if (cfg_live_core_i && current_ack_core && (!sent_core_q || snapshot_core_q != csr_image)) begin
                snapshot_core_q <= csr_image;
                request_core_q  <= !request_core_q;
                sent_core_q     <= 1'b1;
            end
        end
    end

    // =========================================================================
    // 3. 目的端空闲提交和快照可见范围
    // =========================================================================
    (* ASYNC_REG = "TRUE" *) logic [1:0] request_io_sync_q;
    logic [31:0] installed_io_q;  // 仅在应用沿改变，已有帧可继续用原快照
    logic installed_valid_q, applied_io_q;
    logic io_session_live, apply_io;

    // [撤销] 公共复位直接封闭可见数据和握手，即使 IO 当下停钟。
    // 新纪元第一份配置由真实 CSR 重送；复位高电平本身不能释放旧训练模式。
    assign io_session_live = link_reset_n && io_ready_n;
    assign cfg_valid_io_o = io_session_live && installed_valid_q;
    assign cfg_pending_io_o = io_session_live && request_io_sync_q[1] != acknowledge_io_q;
    assign cfg_applied_io_o = io_session_live && applied_io_q;
    assign {cfg_a_io_o, cfg_b_io_o, cfg_c_io_o, cfg_filter_io_o} = cfg_valid_io_o ? installed_io_q : '0;
    assign apply_io = cfg_pending_io_o && io_idle_i && io_apply_allow_i;

    // [CDC] 保持中的整组数据由双级同步申请授予读取权。第一次可领取的
    // 目标沿前须满足整体数据稳定；布线约束以源快照寄存器到此采样点验收。
    always_ff @(posedge clk_io_i or negedge io_ready_n) begin
        if (!io_ready_n) begin
            request_io_sync_q <= '0;
            acknowledge_io_q  <= 1'b0;
            installed_io_q    <= '0;
            installed_valid_q <= 1'b0;
            applied_io_q      <= 1'b0;
        end else begin
            request_io_sync_q <= {request_io_sync_q[0], request_core_q};
            applied_io_q <= apply_io;
            if (apply_io) begin
                installed_io_q    <= snapshot_core_q;
                installed_valid_q <= 1'b1;
                acknowledge_io_q  <= request_io_sync_q[1];
            end
        end
    end
endmodule

`default_nettype wire
