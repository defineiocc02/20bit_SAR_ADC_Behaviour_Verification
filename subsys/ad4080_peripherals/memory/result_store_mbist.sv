`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : result_store_mbist.sv
// 模块名     : result_store_mbist
// 版本       : v1.0
// 功能       : 在结果 SRAM 的单口前仲裁功能访问和破坏性 March C− 测试。
// 设计说明   :
//   * 所有输入均在 SRAM 时钟域。复位低有效异步断言，释放由上层同步。
//   * 授测前先冻结新功能请求并排空旧读；仅授权且静止时启动破坏性测试。
//     通过、失配、中止后都必须由上层复位记录元数据，才归还访问权。
//   * 读响应在发址沿之后有效，下一沿抽样。测试控制沿推进在 SRAM 外，
//     首次读错与下一条测试命令可能同沿，错误检测不串到宏使能关键路径。
// -----------------------------------------------------------------------------
// 变更记录
// 初始版本，暂无变更记录。
// =============================================================================
module result_store_mbist (
    // --- SRAM 时钟域 ---
    input  logic        clk,                 // 与 result_store 同一时钟
    input  logic        rst_n,               // 同时清功能记录元数据与流水有效位
    // --- 授测、重建和诊断 ---
    input  logic        test_mode_i,         // 授权切入测试；撤回即停止发 SRAM 请求
    input  logic        func_quiesced_i,     // 功能写、读、预取均已排空
    input  logic        start_i,             // 与 start_ready_o 同沿有效的启动申请
    input  logic        checkerboard_i,       // 启动沿锁存；1 为 55555，0 为全零
    input  logic        func_reinit_done_i,  // 内容失效后，功能端已清记录元数据
    output logic        start_ready_o,       // 高表示此沿允许接受启动请求
    output logic        func_hold_o,         // 高时暂停受理新功能访问
    output logic        contents_invalid_o,  // 受理测试后至重建，逻辑记录失效
    output logic        busy_o,              // 扫描或等待最后读响应
    output logic        done_o,              // 完成黏滞至下次接受启动或复位
    output logic [1:0]  fail_reason_o,       // 0 通过，1 数据，2 读有效协议，3 中止
    output logic        fail_addr_valid_o,   // 高表示首错对应一条已受理的读
    output logic [13:0] fail_addr_o,         // 地址高两位为银行
    output logic [19:0] fail_expected_o,     // 该读预期的 20 bit 底图
    output logic [19:0] fail_actual_o,       // 读有效时捕获数据，缺响应时为默认零
    // --- 功能侧 ---
    input  logic        func_req_i,          // func_hold_o=0 时上升沿受理
    input  logic        func_write_i,        // 1 为全字写，0 为读
    input  logic [13:0] func_addr_i,         // 14 位线性字地址
    input  logic [19:0] func_data_i,         // 原始 20 bit 写字
    output logic [19:0] func_data_o,         // func_read_valid_o=1 时交付功能端
    output logic        func_read_valid_o,   // 测试期间与测试响应隔离
    // --- result_store 侧 ---
    output logic        store_req_o,         // 每沿最多一条功能或测试访问
    output logic        store_write_o,       // 与宏包装约定一致，1 为写
    output logic [13:0] store_addr_o,        // 宏包装译码为四银行
    output logic [19:0] store_data_o,        // 写数据
    input  logic [19:0] store_data_i,        // 沿后读数据
    input  logic        store_read_valid_i   // 读沿后置位，空拍与写沿后撤销
);
    // =========================================================================
    // 1. 状态、地址和读响应流水
    // =========================================================================
    typedef enum logic [1:0] {IDLE, MARCH, DRAIN, RECOVER} state_t;
    state_t state_q;                          // RECOVER 隔离测试响应和新功能访问

    localparam logic [2:0] FILL          = 3'd0;
    localparam logic [2:0] UP_RP_WN      = 3'd1;
    localparam logic [2:0] UP_RN_WP      = 3'd2;
    localparam logic [2:0] DOWN_RP_WN    = 3'd3;
    localparam logic [2:0] DOWN_RN_WP    = 3'd4;
    localparam logic [2:0] FINAL_READ    = 3'd5;
    localparam logic [13:0] LAST_ADDR    = 14'h3fff;  // 16,384 个线性字地址
    localparam logic [19:0] CHECKERBOARD = 20'h55555; // 相邻逻辑位互补

    logic [2:0]  phase_q;                    // 四个成对遍历阶段首拍读、次拍写
    logic [13:0] address_q;                  // 遍历顺序含跨银行边界
    logic        write_step_q;               // 成对阶段同地址的第二拍
    logic [19:0] pattern_q;                  // 测试开始后避免 DFT 输入变化污染底图
    logic        read_pending_q;            // 上拍受理读，与 store_read_valid_i 对齐
    logic [13:0] pending_addr_q;             // 与当前前推地址独立，用于首错定位
    logic [19:0] pending_expected_q;         // 上拍读的期望底图
    logic        guard_ok, start_accept, march_issue, recover_ok;
    logic        pair_phase, down, at_boundary, final_access;
    logic        march_write, invert_pattern;
    logic [19:0] march_data;
    logic        valid_error, data_error;

    // =========================================================================
    // 2. 端口所有权和命令生成
    // =========================================================================
    // 模式先冻结新功能请求，等待上一沿可能已受理的读回到消费者。
    // 测试中撤回授权立即关请求。换回功能所有权前另查恢复应答与空口。
    assign guard_ok = test_mode_i && func_quiesced_i;
    assign start_ready_o = rst_n && (state_q == IDLE) && guard_ok &&
                           !func_req_i && !store_read_valid_i && !func_reinit_done_i;
    assign start_accept = start_i && start_ready_o;
    assign march_issue = rst_n && (state_q == MARCH) && guard_ok;
    assign recover_ok = !test_mode_i && func_quiesced_i && !func_req_i &&
                        !store_read_valid_i && func_reinit_done_i;
    assign func_hold_o = !rst_n || test_mode_i || (state_q != IDLE);
    assign contents_invalid_o = (state_q != IDLE);
    assign busy_o = (state_q == MARCH) || (state_q == DRAIN);
    assign func_read_valid_o = rst_n && (state_q == IDLE) && store_read_valid_i;
    assign func_data_o = func_read_valid_o ? store_data_i : '0;

    // 成对阶段读的极性由阶段决定，紧随的写翻转相同地址，避免地址越步。
    assign pair_phase = (phase_q >= UP_RP_WN) && (phase_q <= DOWN_RN_WP);
    assign down = (phase_q == DOWN_RP_WN) || (phase_q == DOWN_RN_WP);
    assign at_boundary = down ? (address_q == '0) : (address_q == LAST_ADDR);
    assign final_access = (phase_q == FINAL_READ) && at_boundary;
    assign march_write = (phase_q == FILL) || write_step_q;
    assign invert_pattern = ((phase_q == UP_RN_WP) || (phase_q == DOWN_RN_WP)) ^
                            write_step_q;
    assign march_data = invert_pattern ? ~pattern_q : pattern_q;

    // 空闲时给宏确定的总线值。功能与测试均未获批准时不得在宏口产生访问。
    always_comb begin
        store_req_o = 1'b0;
        store_write_o = 1'b0;
        store_addr_o = '0;
        store_data_o = '0;
        if (march_issue) begin
            store_req_o = 1'b1;
            store_write_o = march_write;
            store_addr_o = address_q;
            store_data_o = march_data;
        end else if (!func_hold_o && func_req_i) begin
            store_req_o = 1'b1;
            store_write_o = func_write_i;
            store_addr_o = func_addr_i;
            store_data_o = func_data_i;
        end
    end

    // =========================================================================
    // 3. March 地址推进
    // =========================================================================
    // 只有命令实际交到单口后才推进。由增序转降序时从 LAST_ADDR 开始，
    // 两个降序阶段均触达地址零，最后一轮再从零增序到最大地址。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_q <= FILL;
            address_q <= '0;
            write_step_q <= 1'b0;
            pattern_q <= '0;
        end else if (start_accept) begin
            phase_q <= FILL;
            address_q <= '0;
            write_step_q <= 1'b0;
            pattern_q <= checkerboard_i ? CHECKERBOARD : '0;
        end else if (march_issue) begin
            write_step_q <= pair_phase && !write_step_q;
            if (!pair_phase || write_step_q) begin
                if (at_boundary) begin
                    if (!final_access) begin
                        phase_q <= phase_q + 3'd1;
                        address_q <= ((phase_q == UP_RN_WP) || (phase_q == DOWN_RP_WN))
                                     ? LAST_ADDR : '0;
                    end
                end else begin
                    address_q <= down ? address_q - 14'd1 : address_q + 14'd1;
                end
            end
        end
    end

    // =========================================================================
    // 4. 结果检查与恢复
    // =========================================================================
    // 四态功能模型的有效位或已写数据出现 X 也记为故障，便于定位宏配置问题。
    assign valid_error = (store_read_valid_i !== read_pending_q);
    assign data_error = read_pending_q && (store_data_i !== pending_expected_q);

    // 同沿优先级为复位、授权丢失、协议错误、数据错误、最后响应完成。
    // 保留上一拍的地址和底图，错误不能误归到沿上同时发出的测试命令。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q <= IDLE;
            done_o <= 1'b0;
            fail_reason_o <= '0;
            fail_addr_valid_o <= 1'b0;
            fail_addr_o <= '0;
            fail_expected_o <= '0;
            fail_actual_o <= '0;
            read_pending_q <= 1'b0;
            pending_addr_q <= '0;
            pending_expected_q <= '0;
        end else begin
            case (state_q)
                IDLE: if (start_accept) begin
                    state_q <= MARCH;
                    done_o <= 1'b0;
                    fail_reason_o <= '0;
                    fail_addr_valid_o <= 1'b0;
                    fail_addr_o <= '0;
                    fail_expected_o <= '0;
                    fail_actual_o <= '0;
                end
                MARCH, DRAIN: begin
                    if (!guard_ok || valid_error || data_error || (state_q == DRAIN)) begin
                        state_q <= RECOVER;
                        done_o <= 1'b1;
                        read_pending_q <= 1'b0;
                        if (!guard_ok) begin
                            fail_reason_o <= 2'd3;
                        end else if (valid_error || data_error) begin
                            fail_reason_o <= valid_error ? 2'd2 : 2'd1;
                            fail_addr_valid_o <= read_pending_q;
                            fail_addr_o <= read_pending_q ? pending_addr_q : '0;
                            fail_expected_o <= read_pending_q ? pending_expected_q : '0;
                            fail_actual_o <= store_read_valid_i ? store_data_i : '0;
                        end
                    end else begin
                        read_pending_q <= !march_write;
                        if (!march_write) begin
                            pending_addr_q <= address_q;
                            pending_expected_q <= march_data;
                        end
                        if (final_access)
                            state_q <= DRAIN;
                    end
                end
                RECOVER: if (recover_ok)
                    state_q <= IDLE;
                default: state_q <= RECOVER;
            endcase
        end
    end
endmodule
`default_nettype wire
