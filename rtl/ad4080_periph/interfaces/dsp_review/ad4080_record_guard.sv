`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_record_guard.sv
// 模块名     : ad4080_record_guard
// 版本       : v1.0
// 功能       : 对受控配置 SPI 记录、复位、起录命令授权，隔离未合格的触发。
// 设计说明   :
//   * 受信任命令发送器先申报作用，授权后才发对应的一条 SPI 帧。spi_permit_o
//     接原 DSP 的 interface_ready。发行端负责命令分类与结果确认，原始 CS
//     若能旁路该发送器，单靠此接口无法辨识它是否把写事务伪装成读事务。
//   * cnv_tick_i 源于同步后的物理 CNV 上升沿。epoch_start_i 表示本批已按
//     时序执行的 FILTER_SYNC 或旁路开始，须与该沿同拍。profile_valid_i
//     证明当前 CNV、处理入口和所选输出之间关系已绑定；输入 valid 不能反推 CNV。
//   * 起录先过公开 CNV 数，再过实际 DSP 的选中数值历史。模式切换或节拍失效
//     会清资格，未绑定相位直接闭锁。qualified 仅用于起录权限，不外送器件 RDY。
//   * 起录受理后锁住 CSR/复位，直到上层证实记录读毕或安全取消；是否成功发送
//     该 SPI 帧不推测成释放，即使在临界点失去 CNV 也不误开写权限。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 2026-11-20 对已绑定 PF1 用 35 个真字及 35D+12 个 CNV 判定起录门槛。
// =============================================================================
module ad4080_record_guard #(
    parameter int MAX_CNV_GAP_CYCLES = 32  // 同步 clk 周期；正式上限由 CNV 最慢速及 CDC 延迟决定。
) (
    // --- 时钟、CNV 与真实数据边界 ---
    input  logic        clk_i,                   // 与受控发送器和同步后脉冲同域。
    input  logic        rst_ni,                  // 系统复位清所有信任资格与会话。
    input  logic        cnv_stream_i,             // 转换源显式确认正在运行，不由 valid 派生。
    input  logic        cnv_tick_i,               // 每个实际 CNV 上升沿只交付一次。
    input  logic        epoch_start_i,            // 同拍 cnv_tick 已完成物理相序资格确认。
    input  logic        profile_valid_i,          // 模式/率、CNV 和数据映射已由集成方绑定。
    input  logic [1:0]  filter_mode_i,            // 原 DSP 已提交/读回的 FILTER_SEL。
    input  logic [3:0]  dec_code_i,               // 原 DSP 已提交/读回的 SINC_DEC_RATE。
    input  logic [15:0] profile_min_cnv_i,        // 已绑定的额外硬件等待，取手册与该值之大者。
    input  logic        selected_valid_i,        // FIFO=00 时取同批、所选链路的新数值有效。
    input  logic        filter_sync_req_i,       // 已同步的实际滤波复位请求，不能在记录期间旁路。
    output logic        filter_sync_o,           // 接 DSP 同域 filter_sync 端，禁用时保证低。

    // --- 受信任命令源和实际送帧 ---
    input  logic        command_i,                // 单拍申请；读取 grant 或拒绝原因后决定发帧。
    input  logic [1:0]  operation_i,             // 0 读，1 配置，2 软件复位，3 起录。
    input  logic        adc_idle_i,               // 外围确认转换及在途结果已排空。
    input  logic        frame_end_i,              // CS 及其同步清理结束，收回本帧授权。
    input  logic        frame_ok_i,               // 仅实际写入已确认且整帧结束时同拍为 1。
    input  logic        session_end_i,            // READ_DONE 或可信取消证明无活动事务后确认。
    output logic        grant_o,                  // 命令时沿的准许结果。
    output logic        spi_permit_o,             // 令原 SPI 有权消费该帧，守护位失效可撤销。
    output logic        rejected_o,               // 命令被拒绝或在途授权被收回的脉冲。
    output logic [2:0]  reject_reason_o,          // 最后一次拒绝原因保持至下一错误或复位。
    output logic        record_locked_o,          // 覆盖待起录、写入和读回全程。
    output logic        capture_qualified_o,      // 起录前仅作本地命令许可，非器件 RDY。

    // --- 外事件的会话限制 ---
    input  logic        event_i,                  // 同域事件；提前或失锁的事件不挂起待重放。
    output logic        event_o,
    output logic        event_blocked_o
);
    // =========================================================================
    // 1. 公布抽取率与已知数字实现的早期结果
    // =========================================================================
    localparam logic [1:0] READ_OP = 2'd0, CFG_OP = 2'd1, RESET_OP = 2'd2, ARM_OP = 2'd3;
    localparam logic [2:0] RECORD_BUSY = 3'd1, NOT_IDLE = 3'd2, NOT_BOUND = 3'd3,
                           NEED_BATCH = 3'd4, FRAME_BUSY = 3'd5, REVOKED = 3'd6;
    localparam int GAP_W = $clog2(MAX_CNV_GAP_CYCLES + 1);
    logic [3:0] clamped_dec;
    logic [15:0] n_cnv, manual_min_cnv, cnv_floor;
    logic [5:0] result_floor;
    logic profile_supported;

    // [依据] Rev. B Table 29/62 的 D 对 SINC1/SINC5 等于 N，COMP 总率 2N。
    // SINC5 的 comb 当前不跨抽取点增加额外延迟。五个长度 N 的均值卷积
    // 支持长 5(N−1)+1，第五个完整抽取点对于全部合法 N 均有完整历史。
    // PF1 数值路径按总倍率 2N 计 CNV。35 个实数是起录下界，profile_valid
    // 仍须保证外部相位绑定，不能从这些计数直接推断器件 RDY。
    always_comb begin
        clamped_dec = dec_code_i;
        if (filter_mode_i == 2'd1 && dec_code_i > 4'd9) clamped_dec = 4'd9;
        if (filter_mode_i >= 2'd2 && dec_code_i > 4'd7) clamped_dec = 4'd7;
        n_cnv = 16'd2 << clamped_dec;
        manual_min_cnv = 16'd1;
        result_floor = 6'd1;
        case (filter_mode_i)
            2'd1: manual_min_cnv = n_cnv + 16'd3;
            2'd2: begin
                manual_min_cnv = (n_cnv << 2) + n_cnv + 16'd6;
                result_floor = 6'd5;
            end
            2'd3: begin
                manual_min_cnv = 16'd70*n_cnv + 16'd12;
                result_floor = 6'd35;
            end
            default: begin end
        endcase
        cnv_floor = manual_min_cnv > profile_min_cnv_i ? manual_min_cnv : profile_min_cnv_i;
    end
    assign profile_supported = profile_valid_i;

    // =========================================================================
    // 2. 同批资格与无 CNV 检测
    // =========================================================================
    logic [1:0] mode_q;
    logic [3:0] dec_q;
    logic [15:0] floor_q, actual_cnv_q;
    logic [5:0] numeric_count_q;  // PF1 至少 35 字，且允许 CNV 先后造成再等若干字。
    logic [GAP_W-1:0] gap_q;
    logic epoch_q, settled_q, sync_was_high_q;
    logic frame_q, revoked_q, session_q, started_q;
    logic [1:0] frame_op_q;

    wire tick_timeout = !cnv_tick_i && (gap_q >= GAP_W'(MAX_CNV_GAP_CYCLES-1));
    wire batch_live = epoch_q && !epoch_start_i && cnv_stream_i && profile_supported &&
                      filter_mode_i == mode_q && dec_code_i == dec_q && cnv_floor == floor_q &&
                      !tick_timeout;
    assign filter_sync_o = rst_ni && filter_sync_req_i && !session_q && !frame_q;
    assign capture_qualified_o = batch_live && settled_q && !session_q && !filter_sync_o;

    // [优先级] 新批清旧批计数。运行中停 CNV、丢失节拍或改 mode/rate 清本批。
    // 数值阈值在有效沿才确认，禁止只靠时间、只靠早期 valid 或旧批的残留数。
    // 记录期间 dout_valid 切换为读回语义，因此锁住数值计数，保持本批资格只用来撤权。
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            mode_q          <= 0;
            dec_q           <= 0;
            floor_q         <= 0;
            actual_cnv_q    <= 0;
            numeric_count_q <= 0;
            gap_q           <= 0;
            epoch_q         <= 0;
            settled_q       <= 0;
        end else if (epoch_start_i && !session_q && !frame_q) begin
            mode_q          <= filter_mode_i;
            dec_q           <= dec_code_i;
            floor_q         <= cnv_floor;
            actual_cnv_q    <= cnv_tick_i ? 16'd1 : 16'd0;
            numeric_count_q <= 0;
            gap_q           <= 0;
            epoch_q         <= cnv_tick_i && cnv_stream_i && profile_supported &&
                               (filter_mode_i == 2'd0 || filter_sync_o);
            settled_q       <= 0;
        end else if (!batch_live || session_end_i ||
                     (filter_sync_o && (!sync_was_high_q || cnv_tick_i)) ||
                     (grant_o && (operation_i == CFG_OP || operation_i == RESET_OP))) begin
            epoch_q         <= 0;
            settled_q       <= 0;
            gap_q           <= 0;
            numeric_count_q <= 0;
        end else begin
            if (cnv_tick_i) begin
                gap_q <= 0;
                if (actual_cnv_q != 16'hffff) actual_cnv_q <= actual_cnv_q + 1'b1;
            end else gap_q <= gap_q + 1'b1;
            if (selected_valid_i && !session_q && !filter_sync_o) begin
                if (numeric_count_q != 6'h3f) numeric_count_q <= numeric_count_q + 1'b1;
                if (actual_cnv_q >= floor_q && numeric_count_q >= result_floor - 1'b1)
                    settled_q <= 1'b1;
            end
        end
    end

    // 同步请求开始先撤掉上批。若复位持续跨到本批第二个 CNV，则本批也撤销。
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) sync_was_high_q <= 0;
        else sync_was_high_q <= filter_sync_o;
    end

    // =========================================================================
    // 3. 命令及会话仲裁
    // =========================================================================
    logic [2:0] reason;
    always_comb begin
        reason = 0;
        if (frame_q) reason = FRAME_BUSY;
        else if (operation_i != READ_OP && session_q) reason = RECORD_BUSY;
        else if ((operation_i == CFG_OP || operation_i == RESET_OP) && !adc_idle_i) reason = NOT_IDLE;
        else if (operation_i == ARM_OP && !profile_supported) reason = NOT_BOUND;
        else if (operation_i == ARM_OP && !capture_qualified_o) reason = NEED_BATCH;
    end
    assign grant_o = rst_ni && command_i && reason == 0;
    wire frame_prereq = (frame_op_q == READ_OP) ||
                        ((frame_op_q == CFG_OP || frame_op_q == RESET_OP) && adc_idle_i) ||
                        (frame_op_q == ARM_OP && batch_live && settled_q);
    assign spi_permit_o = rst_ni && frame_q && !revoked_q && frame_prereq;
    assign record_locked_o = session_q;
    assign event_o = rst_ni && event_i && started_q && batch_live;
    assign event_blocked_o = rst_ni && event_i && !event_o;

    // [失败路径] 中途撤权后即使 CNV 再动也不能恢复旧帧。收尾仍需同步确认，
    // 只有记录明确结束才撤掉锁，避免写可能已经进入原 DSP 的记录。
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            frame_q         <= 0;
            revoked_q       <= 0;
            frame_op_q      <= READ_OP;
            session_q       <= 0;
            started_q       <= 0;
            rejected_o      <= 0;
            reject_reason_o <= 0;
        end else begin
            rejected_o <= 0;
            if (session_end_i && !frame_q) begin
                session_q <= 0;
                started_q <= 0;
            end
            if (frame_q && frame_end_i) begin
                frame_q <= 0;
                if (frame_op_q == ARM_OP && frame_ok_i && spi_permit_o) started_q <= 1;
            end
            if (frame_q && !revoked_q && !frame_prereq) begin
                revoked_q       <= 1;
                rejected_o      <= 1;
                reject_reason_o <= REVOKED;
            end
            if (filter_sync_req_i && session_q) begin
                rejected_o      <= 1;
                reject_reason_o <= RECORD_BUSY;
            end
            if (command_i) begin
                if (grant_o) begin
                    frame_q    <= 1;
                    revoked_q  <= 0;
                    frame_op_q <= operation_i;
                    if (operation_i == ARM_OP) session_q <= 1;
                end else begin
                    rejected_o      <= 1;
                    reject_reason_o <= reason;
                end
            end
        end
    end
endmodule
`default_nettype wire
