`timescale 1ns/1ps

// =============================================================================
// 文件名     : otp_shadow_loader.sv
// 模块名     : otp_shadow_loader
// 版本       : v1.0
// 功能       : 顺序读取集成方选定的只读字区，将完成校验的完整镜像原子发布。
// 设计说明   :
//   * 宏电气适配、器件字区映射和校验策略由外部提供。实例参数来自集成合同。
//   * 各握手均在 clk_i 上升沿受理。复位异步拉低，宿主负责同步释放；
//     power_good_i 已在本域同步和资格确认，实际电压隔离由供电域实现。
//   * 同沿优先级为复位、供电失效、无归属应答、已握手结果、超时。
//     失败后禁止部分发布，仅通过协调复位和适配器冲刷开始新会话。
//   * 单笔在途。完整镜像的第一个字放在最低位；未经校验的内容仅交给校验端。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 审查后拒绝请求受理前出现的宏或校验器应答，保持同沿合法返回。
// =============================================================================
module otp_shadow_loader #(
    parameter  int unsigned IMAGE_WORDS     = 1,  // 最小占位值；集成时填逻辑读取字数
    parameter  int unsigned WORD_WIDTH      = 1,  // 最小占位值；集成时填接口每字位数
    parameter  int unsigned TIMEOUT_CYCLES  = 1,  // 每一外部握手阶段的独立周期上限
    localparam int unsigned ADDR_WIDTH      = (IMAGE_WORDS > 1) ? $clog2(IMAGE_WORDS) : 1,
    localparam int unsigned IMAGE_WIDTH     = IMAGE_WORDS * WORD_WIDTH
) (
    // --- 启动与适配器冲刷 ---
    input  logic                   clk_i,                  // 全部握手所在的单一控制域
    input  logic                   rst_ni,                 // 异步拉低，来自宿主复位同步器
    input  logic                   power_good_i,           // 本域监测值；上电等待，运行撤销即废弃会话
    output logic                   adapters_cancel_o,      // 高有效持续撤销，直到旧事务全部静默
    input  logic                   adapters_quiescent_i,   // 两适配器都已排空旧请求和应答的确认

    // --- 只读宏适配器 ---
    output logic                   otp_req_valid_o,        // 连同字地址保持至 ready 或会话撤销
    input  logic                   otp_req_ready_i,        // 适配器能受理唯一在途读请求
    output logic [ADDR_WIDTH-1:0]  otp_req_addr_o,         // 从零开始的逻辑字号，物理映射在适配器
    input  logic                   otp_rsp_valid_i,        // 只回应本次受理，保持至 ready 或撤销
    output logic                   otp_rsp_ready_o,        // 成功受理请求后的下一拍开始允许取应答
    input  logic [WORD_WIDTH-1:0]  otp_rsp_data_i,         // 宏适配器给出的逻辑字，同 valid 取样
    input  logic                   otp_rsp_error_i,        // 宏读失败或不可接受的码字与应答同行

    // --- 完整镜像校验适配器 ---
    output logic                    verify_req_valid_o,    // 收齐末字后才申请验收，整像只发一次
    input  logic                    verify_req_ready_i,    // 可接收本次完整镜像
    output logic [IMAGE_WIDTH-1:0]  verify_image_o,        // 申请有效且未撤销时，第 0 字为最低位
    input  logic                    verify_rsp_valid_i,    // 一次申请对应一次通过或拒绝判决
    output logic                    verify_rsp_ready_o,    // 申请受理后才接收判决
    input  logic                    verify_rsp_pass_i,     // 值由集成方定义的镜像校验给出

    // --- 同域发布与诊断 ---
    output logic                    busy_o,               // 复位释放后等电、加载或校验期间为高
    output logic [3:0]              error_code_o,         // 复位清零；记录首个失败，编码见 README
    output logic                    shadow_valid_o,       // 校验通过才置位，掉电或复位撤销
    output logic [IMAGE_WIDTH-1:0]  shadow_image_o        // 无效时为零；有效时保持整像
);

    // =========================================================================
    // 1. 状态编码与参数边界
    // =========================================================================
    localparam int unsigned TIMER_WIDTH = (TIMEOUT_CYCLES > 1) ? $clog2(TIMEOUT_CYCLES) : 1;
    localparam logic [TIMER_WIDTH-1:0] LAST_WAIT = TIMER_WIDTH'(TIMEOUT_CYCLES - 1);
    localparam logic [ADDR_WIDTH-1:0] LAST_ADDR = ADDR_WIDTH'(IMAGE_WORDS - 1);

    typedef enum logic [2:0] {
        ST_WAIT_POWER, ST_DRAIN, ST_READ_REQ, ST_READ_RSP,
        ST_VERIFY_REQ, ST_VERIFY_RSP, ST_COMMITTED, ST_FAILED
    } state_t;

    typedef enum logic [3:0] {
        ERR_NONE             = 4'd0,
        ERR_POWER_LOST       = 4'd1,
        ERR_OTP_READ         = 4'd2,
        ERR_IMAGE_REJECTED   = 4'd3,
        ERR_DRAIN_TIMEOUT    = 4'd4,
        ERR_READ_REQ_TIMEOUT = 4'd5,
        ERR_READ_RSP_TIMEOUT = 4'd6,
        ERR_VERIFY_REQ_TIMEOUT = 4'd7,
        ERR_VERIFY_RSP_TIMEOUT = 4'd8,
        ERR_INVALID_STATE    = 4'd9,
        ERR_UNREQUESTED_READ = 4'd10,
        ERR_UNREQUESTED_CHECK = 4'd11
    } error_t;

    state_t state_q, state_d;
    error_t fault, wait_fault;
    logic [TIMER_WIDTH-1:0] wait_q;  // 每个阶段独立预算；最后一拍握手仍有效
    logic [ADDR_WIDTH-1:0] addr_q;   // 固定到当前请求的应答受理完毕
    logic [IMAGE_WIDTH-1:0] image_q; // 暂存和已发布内容复用一份，发布后禁止回写
    logic capture_word;
    logic powered;
    logic read_request_fire, verify_request_fire;
    logic access_phase_active;     // 冲刷期间的旧应答继续由静默合同清除

`ifndef SYNTHESIS
    // [参数] 单字和单周期合法，零值无法形成有意义的读区或期限。
    if (IMAGE_WORDS == 0 || WORD_WIDTH == 0 || TIMEOUT_CYCLES == 0) begin : g_bad_parameters
        initial $fatal(1, "IMAGE_WORDS, WORD_WIDTH and TIMEOUT_CYCLES must be positive");
    end
`endif

    // =========================================================================
    // 2. 握手门控与全像可见性
    // =========================================================================
    // [边界] 取消信号保持到两个适配器确认静默。请求源收到取消后清除旧
    // 响应和未受理的 valid，恢复后首笔读不会吃到上次超时的晚到返回。
    assign powered = rst_ni && power_good_i;
    assign adapters_cancel_o = !powered || state_q == ST_WAIT_POWER ||
                               state_q == ST_DRAIN || state_q == ST_FAILED;
    assign otp_req_valid_o = powered && state_q == ST_READ_REQ;
    assign otp_req_addr_o = addr_q;
    assign otp_rsp_ready_o = powered && state_q == ST_READ_RSP;
    assign verify_req_valid_o = powered && state_q == ST_VERIFY_REQ;
    assign verify_image_o = verify_req_valid_o ? image_q : '0;
    assign verify_rsp_ready_o = powered && state_q == ST_VERIFY_RSP;
    assign read_request_fire = otp_req_valid_o && otp_req_ready_i;
    assign verify_request_fire = verify_req_valid_o && verify_req_ready_i;
    assign access_phase_active = otp_req_valid_o || otp_rsp_ready_o ||
                                 verify_req_valid_o || verify_rsp_ready_o;

    // [发布] 用终态作可见性门控可省去第二份存储。PG 也参与输出门控，
    // 防止掉电与通过判决同沿时短暂发布；真实多电源域仍需专用隔离。
    assign busy_o = rst_ni && state_q != ST_COMMITTED && state_q != ST_FAILED;
    assign shadow_valid_o = powered && state_q == ST_COMMITTED;
    assign shadow_image_o = shadow_valid_o ? image_q : '0;

    // =========================================================================
    // 3. 状态推进与冲突裁决
    // =========================================================================
    // [时序] 读申请和应答分拍。适配器如在申请接受沿同时产生 valid，
    // 必须把结果保持到 read/verify 的 ready，不能只在接受沿打脉冲。
    // 初上电的等电窗口由宿主管理；其他外部等待分别计时，便于定位故障。
    always_comb begin
        state_d = state_q;
        capture_word = 1'b0;
        fault = ERR_NONE;
        wait_fault = ERR_NONE;

        unique case (state_q)
            ST_WAIT_POWER: begin
                if (power_good_i) state_d = ST_DRAIN;
            end
            ST_DRAIN: begin
                // [重入] 同时观察两路 valid 为低，避免带着可见旧响应出栅。
                if (adapters_quiescent_i && !otp_rsp_valid_i && !verify_rsp_valid_i)
                    state_d = ST_READ_REQ;
                else
                    wait_fault = ERR_DRAIN_TIMEOUT;
            end
            ST_READ_REQ: begin
                if (read_request_fire) state_d = ST_READ_RSP;
                else wait_fault = ERR_READ_REQ_TIMEOUT;
            end
            ST_READ_RSP: begin
                if (otp_rsp_valid_i && otp_rsp_ready_o) begin
                    if (otp_rsp_error_i) begin
                        fault = ERR_OTP_READ;
                    end else begin
                        capture_word = 1'b1;
                        state_d = (addr_q == LAST_ADDR) ? ST_VERIFY_REQ : ST_READ_REQ;
                    end
                end else begin
                    wait_fault = ERR_READ_RSP_TIMEOUT;
                end
            end
            ST_VERIFY_REQ: begin
                if (verify_request_fire) state_d = ST_VERIFY_RSP;
                else wait_fault = ERR_VERIFY_REQ_TIMEOUT;
            end
            ST_VERIFY_RSP: begin
                // [发布] 只有本轮的明确通过判决能到达已提交态。
                if (verify_rsp_valid_i && verify_rsp_ready_o) begin
                    if (verify_rsp_pass_i) state_d = ST_COMMITTED;
                    else fault = ERR_IMAGE_REJECTED;
                end else begin
                    wait_fault = ERR_VERIFY_RSP_TIMEOUT;
                end
            end
            ST_COMMITTED, ST_FAILED: begin
                // [终态] 发布后数据固定；失败只经复位清除，旧响应不可重启。
            end
            default: fault = ERR_INVALID_STATE;
        endcase

        // [优先级] 掉电打断有效会话。访问阶段中，尚未受理请求且本沿
        // 无新请求握手的应答不可归属，优先封闭；随请求同沿给 valid
        // 仍合法，应答端需保持到下一阶段的 ready。正常握手可在期限末沿推进。
        if (!power_good_i && state_q != ST_WAIT_POWER && state_q != ST_FAILED)
            fault = ERR_POWER_LOST;
        else if (access_phase_active && otp_rsp_valid_i && !otp_rsp_ready_o && !read_request_fire)
            fault = ERR_UNREQUESTED_READ;
        else if (access_phase_active && verify_rsp_valid_i && !verify_rsp_ready_o && !verify_request_fire)
            fault = ERR_UNREQUESTED_CHECK;
        else if (wait_fault != ERR_NONE && wait_q == LAST_WAIT)
            fault = wait_fault;

        if (fault != ERR_NONE) begin
            state_d = ST_FAILED;
            capture_word = 1'b0;
        end
    end

    // =========================================================================
    // 4. 时序存储与失败清理
    // =========================================================================
    // [原子性] 等待、失败和重置始终遮蔽影子。任何失败清除暂存，
    // 防止重入把前一会话已读的部分字和新响应拼成一份镜像。
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state_q      <= ST_WAIT_POWER;
            error_code_o <= ERR_NONE;
            wait_q       <= '0;
            addr_q       <= '0;
            image_q      <= '0;
        end else begin
            state_q <= state_d;
            if (fault != ERR_NONE) error_code_o <= fault;

            if (wait_fault != ERR_NONE && state_d == state_q)
                wait_q <= wait_q + 1'b1;
            else
                wait_q <= '0;

            if (state_d == ST_FAILED) begin
                image_q <= '0;
                addr_q  <= '0;
            end else if (capture_word) begin
                image_q[addr_q * WORD_WIDTH +: WORD_WIDTH] <= otp_rsp_data_i;
                if (addr_q != LAST_ADDR) addr_q <= addr_q + 1'b1;
            end
        end
    end
endmodule
