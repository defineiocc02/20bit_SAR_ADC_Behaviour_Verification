`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_peripheral_services.sv
// 模块名     : ad4080_peripheral_services
// 版本       : v1.0
// 功能       : 装配外围供电管理、只读 OTP 提交、结果单口 SRAM 和边沿字映射。
// 设计说明   :
//   * AON 域持有 OTP 镜像与测试会话。核心域持有单口 SRAM 及 MBIST。
//     I/O 只接受本域已交付的完整字；FIFO 读回到 I/O 的搬运由外部负责。
//   * 测试申请同拍阻断新转换。核心确认保持后才回 AON 准入，退出须待核心
//     释放访问和元数据重建。硬 POR 重置双方；软件复位保留已提交 OTP。
//   * 供电和 OTP 失效撤销发布许可。模拟参考掉压与 IOVDD 掉压分别处置，
//     保持电源的已有结果仍遵循读许可，RAM 阵列按未初始化存储处理。
//   * 本壳层边界为功能 IP 端口、模数宏适配器和 I/O 接收器的握手。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// [审计] 加入未完成记录联锁，拆分保持与破坏性授权，参考故障同步事件上交 CSR。
// =============================================================================
module ad4080_peripheral_services #(
    parameter  int unsigned AON_MAX_CLK_HZ       = 10_000_000, // AON 最快时钟，休眠计时按它取整
    parameter  int unsigned OTP_IMAGE_WORDS      = 1,          // 待产品绑定的逻辑读取字数
    parameter  int unsigned OTP_WORD_WIDTH       = 1,          // 待产品绑定的宏逻辑字位数
    parameter  int unsigned OTP_TIMEOUT_CYCLES   = 1,          // 每段握手的 AON 周期预算
    localparam int unsigned OTP_ADDR_WIDTH      = (OTP_IMAGE_WORDS > 1) ? $clog2(OTP_IMAGE_WORDS) : 1,
    localparam int unsigned OTP_IMAGE_WIDTH     = OTP_IMAGE_WORDS * OTP_WORD_WIDTH
) (
    // --- 管理、核心与接口时钟 ---
    input  logic                        clk_aon_i,                      // 常开管理时钟
    input  logic                        clk_core_i,                     // SRAM 同步单口、可停的核心服务时钟
    input  logic                        clk_io_i,                       // 内部 I/O 字接口时钟，须预运行以释放许可
    input  logic                        external_io_i,                  // 静态冷启动供电 strap，1 表示全外供
    input  logic                        por_d_ok_async_i,               // VDD11 POR 合格，坏态钳低
    input  logic                        vdd33_ok_async_i,               // VDD33 合格，坏态钳低
    input  logic                        ref_ok_async_i,                 // REFIN 合格，坏态钳低
    input  logic                        iovdd_ok_async_i,               // IOVDD 合格，坏态钳低
    input  logic                        ana_ready_async_i,              // 模拟端已恢复到可以接收新转换

    // --- AON 配置 IP 接点 ---
    input  logic                        clients_idle_aon_i,             // 管理者确认转换、已受理 SRAM 及 I/O 事务空闲
    input  logic                        capture_incomplete_aon_i,       // 独立 AON 记录状态；含已布防及未完采集
    input  logic                        sw_reset_req_aon_i,             // AON 取样；由 accept 确认本拍提交
    input  logic                        mode_req_valid_aon_i,           // 和 mode 值同拍提交
    input  logic [1:0]                  mode_req_aon_i,                 // 00 正常，10 待机，11 睡眠
    input  logic                        wake_recover_done_aon_i,        // 本次恢复完成，保持到请求撤销
    output logic                        cfg_hw_reset_no,                // AON 域低有效硬复位
    output logic                        core_reset_no,                  // 核心域低有效数据通路复位
    output logic                        fifo_reset_no,                  // 核心域低有效 FIFO 元数据硬复位
    output logic                        io_reset_no,                    // I/O 域低有效接口复位
    output logic                        ana_reset_no,                   // AON 域低有效模拟安全请求
    output logic                        cfg_init_done_aon_o,            // 电源主管本次初始化结束
    output logic                        cfg_control_write_allow_aon_o,  // 仅控制地址写入基础许可
    output logic                        cfg_full_access_allow_aon_o,    // IOVDD 可用时的配置总线基础许可
    output logic [1:0]                  operating_mode_aon_o,          // 和 wake_busy 一起解读
    output logic                        wake_busy_aon_o,                // 唤醒等待或模拟恢复仍在推进
    output logic                        wake_recover_req_aon_o,         // 附加恢复开始，本次握手须重新确认
    output logic                        mode_req_accept_aon_o,          // 同沿模式提交确认
    output logic                        sw_reset_accept_aon_o,          // 同沿非保留配置重置事件
    output logic                        isolate_io_o,                   // 高有效物理隔离请求
    output logic                        iovdd_ldo_en_o,                 // 高有效内供 LDO 请求
    output logic                        ref_cmo_en_o,                   // 高有效模拟缓冲器请求
    output logic                        por_ana_event_aon_o,            // 参考故障同步事件，CSR 方负责粘滞与 W1C
    output logic                        conversion_allow_aon_o,         // AON 新转换准入，叠加 OTP 和测试状态
    output logic                        conversion_allow_core_o,        // 核心域恢复经两级，撤销可异步生效

    // --- AON 只读 OTP 宏和校验适配器 ---
    input  logic                        otp_power_good_aon_i,           // 来自 AON 的宏供电资格，掉落废弃当前会话
    input  logic                        otp_adapters_quiescent_aon_i,   // 两适配器确认取消完成，旧响应已排空
    output logic                        otp_adapters_cancel_aon_o,      // 高电平要求两适配器清除旧事务
    output logic                        otp_req_valid_aon_o,            // 逻辑读请求保持至握手
    input  logic                        otp_req_ready_aon_i,            // 宏适配器确能受理本笔
    output logic [OTP_ADDR_WIDTH-1:0]   otp_req_addr_aon_o,             // 宏适配器解释逻辑字地址
    input  logic                        otp_rsp_valid_aon_i,            // 迟到或同拍的响应都须持至应答握手
    output logic                        otp_rsp_ready_aon_o,            // 已受理本轮读请求才打开
    input  logic [OTP_WORD_WIDTH-1:0]   otp_rsp_data_aon_i,             // 由低地址到高地址填镜像
    input  logic                        otp_rsp_error_aon_i,            // 只在响应握手沿使用
    output logic                        otp_verify_req_valid_aon_o,     // 完整镜像已收齐，提交校验
    input  logic                        otp_verify_req_ready_aon_i,     // 校验适配器确能接收完整镜像
    output logic [OTP_IMAGE_WIDTH-1:0]  otp_verify_image_aon_o,         // 字 0 在最低位，暂存只对校验端可见
    input  logic                        otp_verify_rsp_valid_aon_i,     // 对应本轮镜像的通过或拒绝判决
    output logic                        otp_verify_rsp_ready_aon_o,     // 校验请求受理后才打开
    input  logic                        otp_verify_rsp_pass_aon_i,      // 由实际宏与镜像格式拥有者定义策略
    output logic                        otp_busy_aon_o,                 // 等电、冲刷或读校验期间忙
    output logic [3:0]                  otp_error_code_aon_o,           // 首个错误，复位清除
    output logic                        otp_shadow_valid_aon_o,         // 完整通过后才与影子镜像生效
    output logic [OTP_IMAGE_WIDTH-1:0]  otp_shadow_image_aon_o,         // 按本域有效位原子采纳

    // --- 测试会话及 SRAM 核心域握手 ---
    input  logic                        bist_mode_req_aon_i,            // 拉高请求进入，拉低请求退出或中止
    output logic                        bist_mode_ready_aon_o,          // 可接受下一轮会话
    output logic                        bist_mode_grant_aon_o,          // 核心已确认功能新访问被保持
    output logic                        bist_func_hold_core_o,          // 上游停止受理新功能事务
    input  logic                        bist_func_quiesced_core_i,      // 功能通路已排空，含在途 I/O 搬运
    input  logic                        bist_start_core_i,              // 与 start_ready 同沿启动测试算法
    output logic                        bist_start_ready_core_o,        // 测试独占及单口空闲均已确认
    input  logic                        bist_checkerboard_core_i,       // 1 用 checkerboard，0 用 March C-
    input  logic                        bist_func_reinit_done_core_i,   // 测试退出后 SRAM 元数据已经重建
    output logic                        bist_contents_invalid_core_o,  // 测试可能覆写载荷，旧记录应作废
    output logic                        bist_busy_core_o,               // 仅 MBIST 可驱动单口
    output logic                        bist_done_core_o,               // 测试终态，保持至下次启动
    output logic [1:0]                  bist_fail_reason_core_o,        // 0 pass，1 数据，2 协议，3 中止
    output logic                        bist_fail_addr_valid_core_o,   // 地址诊断仅在此位为 1 时有效
    output logic [13:0]                 bist_fail_addr_core_o,         // 银行及银行内地址原样报告
    output logic [19:0]                 bist_fail_expected_core_o,     // 比较故障的期望字
    output logic [19:0]                 bist_fail_actual_core_o,       // 比较故障的实读字
    input  logic                        result_req_valid_core_i,        // 功能 IP 的单口请求，须等 ready
    output logic                        result_req_ready_core_o,        // 取决于读写类型及本域许可
    input  logic                        result_write_core_i,            // 1 写，0 读
    input  logic [13:0]                 result_addr_core_i,             // [13:12] 银行，[11:0] 字地址
    input  logic [19:0]                 result_write_data_core_i,       // 结果完整字，码制由功能 IP 定义
    output logic                        result_read_valid_core_o,       // 读受理沿后有效，无应答 ready
    output logic [19:0]                 result_read_data_core_o,        // 仅与读 valid 同拍采纳

    // --- I/O 域独立边沿字接点 ---
    input  logic                        io_word_valid_i,                // 数据及模式已在本域组成同一笔
    output logic                        io_word_ready_o,                // I/O 可用、OTP 已提交且 SRAM 未作废
    input  logic [19:0]                 io_sample_i,                    // 上游完成跨域搬运后交付完整字
    input  logic                        io_dual_lane_i,                 // 与本笔字同行，1 为两 lanes
    input  logic                        io_intf_check_i,                // 与本笔字同行，1 选择接口固定测试字
    output logic                        io_edge_words_valid_o,          // 帧接收端在本域与所有边沿字一起采纳
    output logic [9:0]                  io_da_fall_o,                   // [0] 最早下降沿槽
    output logic [9:0]                  io_da_rise_o,                   // [0] 为槽 0 下降沿之后的上升沿
    output logic [9:0]                  io_db_fall_o,                   // 单 lane 时全零
    output logic [9:0]                  io_db_rise_o,                   // 双 lane 时仅槽 0..4 有效
    output logic [3:0]                  io_slot_count_o                 // 每 lane 的有效 DDR 槽数
);
    // =========================================================================
    // 1. 供电与 OTP 装配
    // =========================================================================
    logic pwr_fifo_read_allow_aon, pwr_lvds_allow_aon, pwr_conversion_allow_aon;
    logic otp_boot_ready_q;     // 跨软件初始化保留首次合格的 OTP 资格
    logic bist_idle_aon;        // 同拍阻断软复位、模式切换及转换，避免相互竞态

    ad4080_pwr_supervisor #(.AON_MAX_CLK_HZ(AON_MAX_CLK_HZ)) u_power (
        .clk_aon_i, .clk_core_i, .clk_io_i, .external_io_i,
        .por_d_ok_async_i, .vdd33_ok_async_i, .ref_ok_async_i, .iovdd_ok_async_i, .ana_ready_async_i,
        .clients_idle_aon_i(clients_idle_aon_i && !capture_incomplete_aon_i && bist_idle_aon),
        .sw_reset_req_aon_i, .mode_req_valid_aon_i, .mode_req_aon_i, .wake_recover_done_aon_i,
        .cfg_hw_reset_no, .core_reset_no, .fifo_reset_no, .io_reset_no, .ana_reset_no,
        .cfg_init_done_aon_o, .cfg_control_write_allow_aon_o, .cfg_full_access_allow_aon_o,
        .fifo_read_allow_aon_o(pwr_fifo_read_allow_aon),
        .lvds_allow_aon_o(pwr_lvds_allow_aon),
        .conversion_allow_aon_o(pwr_conversion_allow_aon),
        .operating_mode_aon_o, .wake_busy_aon_o, .wake_recover_req_aon_o,
        .mode_req_accept_aon_o, .sw_reset_accept_aon_o, .isolate_io_o, .iovdd_ldo_en_o, .ref_cmo_en_o,
        .por_ana_event_aon_o
    );

    // [保持] cfg_init_done 在软件复位后暂时撤销。仅把首次完成记入 OTP
    // 启动资格，避免软件复位被解释为宏掉电。真实宏掉电独立撤销资格。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) otp_boot_ready_q <= 1'b0;
        else if (cfg_init_done_aon_o) otp_boot_ready_q <= 1'b1;
    end

    otp_shadow_loader #(
        .IMAGE_WORDS(OTP_IMAGE_WORDS), .WORD_WIDTH(OTP_WORD_WIDTH), .TIMEOUT_CYCLES(OTP_TIMEOUT_CYCLES)
    ) u_otp (
        .clk_i(clk_aon_i), .rst_ni(cfg_hw_reset_no),
        .power_good_i(otp_boot_ready_q && otp_power_good_aon_i),
        .adapters_cancel_o(otp_adapters_cancel_aon_o), .adapters_quiescent_i(otp_adapters_quiescent_aon_i),
        .otp_req_valid_o(otp_req_valid_aon_o), .otp_req_ready_i(otp_req_ready_aon_i),
        .otp_req_addr_o(otp_req_addr_aon_o), .otp_rsp_valid_i(otp_rsp_valid_aon_i),
        .otp_rsp_ready_o(otp_rsp_ready_aon_o), .otp_rsp_data_i(otp_rsp_data_aon_i),
        .otp_rsp_error_i(otp_rsp_error_aon_i),
        .verify_req_valid_o(otp_verify_req_valid_aon_o), .verify_req_ready_i(otp_verify_req_ready_aon_i),
        .verify_image_o(otp_verify_image_aon_o), .verify_rsp_valid_i(otp_verify_rsp_valid_aon_i),
        .verify_rsp_ready_o(otp_verify_rsp_ready_aon_o), .verify_rsp_pass_i(otp_verify_rsp_pass_aon_i),
        .busy_o(otp_busy_aon_o), .error_code_o(otp_error_code_aon_o),
        .shadow_valid_o(otp_shadow_valid_aon_o), .shadow_image_o(otp_shadow_image_aon_o)
    );

    // =========================================================================
    // 2. AON 到核心的测试会话互锁
    // =========================================================================
    typedef enum logic [1:0] {BIST_IDLE, BIST_ENTER, BIST_ACTIVE, BIST_EXIT} bist_state_t;
    bist_state_t bist_state_aon_q;
    logic bist_hold_req_aon_q;    // 已受理的请求经往返确认，窄申请也会送达
    logic bist_hold_ack_core_q;   // 核心域先寄存 hold，返回链只跨单比特
    logic bist_grant_core;       // hold 先排空，实际破坏性授权由另一条释放链产生
    logic mbist_func_hold_core;  // 破坏性结束或中止后仍保留作废状态
    (* ASYNC_REG = "TRUE" *) logic [1:0] bist_req_core_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] bist_ack_aon_q;

    // [记录] 无总线活动不等于采集完成。已布防和部分采集阻止控制重置、
    // 模式切换及破坏性接管。未被受理的 BIST 电平请求不能卡死正在完成的记录。
    assign bist_mode_ready_aon_o = cfg_hw_reset_no && bist_state_aon_q == BIST_IDLE &&
                                   !bist_ack_aon_q[1] && !capture_incomplete_aon_i;
    assign bist_idle_aon = cfg_hw_reset_no && bist_state_aon_q == BIST_IDLE && !bist_ack_aon_q[1] &&
                           !(bist_mode_req_aon_i && bist_mode_ready_aon_o);
    assign bist_mode_grant_aon_o = bist_state_aon_q == BIST_ACTIVE && bist_mode_req_aon_i &&
                                   !capture_incomplete_aon_i;

    // [时序] 先拉高请求并等核心返回保持。即使上游提前撤回，仍要等本次
    // 保持返回后才能发撤销，避免慢时钟把整笔申请吞掉。撤销后，测试毁掉
    // 数据时 hold 还会等外部重建确认。只在新的低确认回到 AON 后重新准入。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            bist_state_aon_q <= BIST_IDLE;
            bist_hold_req_aon_q <= 1'b0;
            bist_ack_aon_q <= '0;
        end else begin
            bist_ack_aon_q <= {bist_ack_aon_q[0], bist_hold_ack_core_q};
            case (bist_state_aon_q)
                BIST_IDLE: if (bist_mode_req_aon_i && bist_mode_ready_aon_o) begin
                    bist_state_aon_q <= BIST_ENTER;
                    bist_hold_req_aon_q <= 1'b1;
                end
                BIST_ENTER: if (bist_ack_aon_q[1]) begin
                    if (bist_mode_req_aon_i && !capture_incomplete_aon_i) begin
                        bist_state_aon_q <= BIST_ACTIVE;
                    end else begin
                        bist_state_aon_q <= BIST_EXIT;
                        bist_hold_req_aon_q <= 1'b0;
                    end
                end
                BIST_ACTIVE: if (!bist_mode_req_aon_i || capture_incomplete_aon_i) begin
                    bist_state_aon_q <= BIST_EXIT;
                    bist_hold_req_aon_q <= 1'b0;
                end
                BIST_EXIT: if (!bist_ack_aon_q[1]) bist_state_aon_q <= BIST_IDLE;
                default: begin
                    bist_state_aon_q <= BIST_EXIT;
                    bist_hold_req_aon_q <= 1'b0;
                end
            endcase
        end
    end

    // [复位] SRAM 测试、单口返回和 FIFO 元数据共用核心硬复位。软件复位
    // 只停功能数据通路，避免将潜在已损坏的数据跟测试标志硬拆开。
    always_ff @(posedge clk_core_i or negedge fifo_reset_no) begin
        if (!fifo_reset_no) begin
            bist_req_core_q <= '0;
            bist_hold_ack_core_q <= 1'b0;
        end else begin
            bist_req_core_q <= {bist_req_core_q[0], bist_hold_req_aon_q};
            bist_hold_ack_core_q <= bist_func_hold_core_o;
        end
    end

    // [权限] ENTER 的保持回执须先到 AON 才授予测试，不能借用提前抵达核心
    // 的 hold 发第一笔破坏性命令。授权撤销立即阻断测试 SRAM 请求；原保持
    // 直到握手退场及重建结束仍在，期间已有功能读可以按许可排尽。
    ad4080_pwr_reset_sync u_bist_grant (
        .clk_i(clk_core_i), .hold_ni(fifo_reset_no && bist_mode_grant_aon_o), .ready_no(bist_grant_core)
    );
    assign bist_func_hold_core_o = bist_req_core_q[1] || mbist_func_hold_core;

    // =========================================================================
    // 3. 本域准入、单口存储与边沿字输出
    // =========================================================================
    logic read_permission_core;
    logic function_read_valid_core;
    logic store_req, store_write, store_read_valid;
    logic [13:0] store_addr;     // 保持原物理分行位序
    logic [19:0] store_wdata, store_rdata;

    // [失效] 许可撤销可以赶在目的时钟恢复前封住写口。放开必须由目的域
    // 两级释放，不能把 AON 条件直接当成核心或 I/O 域的同步 valid。
    assign conversion_allow_aon_o = pwr_conversion_allow_aon && otp_shadow_valid_aon_o && bist_idle_aon;
    ad4080_pwr_reset_sync u_conversion_permission (
        .clk_i(clk_core_i), .hold_ni(conversion_allow_aon_o && core_reset_no),
        .ready_no(conversion_allow_core_o)
    );
    ad4080_pwr_reset_sync u_read_permission (
        .clk_i(clk_core_i), .hold_ni(pwr_fifo_read_allow_aon && otp_shadow_valid_aon_o && fifo_reset_no),
        .ready_no(read_permission_core)
    );
    // [测试] 保持阶段仍允许外部排干已交付的 I/O 字。真正开始写 SRAM 后
    // 内容作废标志同时封住 I/O 事务，直到核心收到重建确认。
    ad4080_pwr_reset_sync u_io_word_permission (
        .clk_i(clk_io_i),
        .hold_ni(pwr_lvds_allow_aon && otp_shadow_valid_aon_o && io_reset_no && !bist_contents_invalid_core_o),
        .ready_no(io_word_ready_o)
    );

    // [同沿] 功能请求仅在 ready 高时到仲裁器。测试保持屏蔽新请求，
    // 先前已受理的读可排尽；电源或 OTP 许可撤销时读有效立即屏蔽。
    assign result_req_ready_core_o = !bist_func_hold_core_o
                                   && (result_write_core_i ? conversion_allow_core_o : read_permission_core);
    assign result_read_valid_core_o = function_read_valid_core && read_permission_core;

    // [静默] 已屏蔽的请求仍可能在功能端保持 valid。测试进入及结束重建都
    // 直接检查这个上游事实，避免释放后一拍执行旧请求。
    result_store_mbist u_mbist (
        .clk(clk_core_i), .rst_n(fifo_reset_no), .test_mode_i(bist_grant_core),
        .func_quiesced_i(bist_func_quiesced_core_i && !result_req_valid_core_i), .start_i(bist_start_core_i),
        .checkerboard_i(bist_checkerboard_core_i), .func_reinit_done_i(bist_func_reinit_done_core_i),
        .start_ready_o(bist_start_ready_core_o), .func_hold_o(mbist_func_hold_core),
        .contents_invalid_o(bist_contents_invalid_core_o), .busy_o(bist_busy_core_o), .done_o(bist_done_core_o),
        .fail_reason_o(bist_fail_reason_core_o), .fail_addr_valid_o(bist_fail_addr_valid_core_o),
        .fail_addr_o(bist_fail_addr_core_o), .fail_expected_o(bist_fail_expected_core_o),
        .fail_actual_o(bist_fail_actual_core_o),
        .func_req_i(result_req_valid_core_i && result_req_ready_core_o),
        .func_write_i(result_write_core_i), .func_addr_i(result_addr_core_i), .func_data_i(result_write_data_core_i),
        .func_data_o(result_read_data_core_o), .func_read_valid_o(function_read_valid_core),
        .store_req_o(store_req), .store_write_o(store_write), .store_addr_o(store_addr), .store_data_o(store_wdata),
        .store_data_i(store_rdata), .store_read_valid_i(store_read_valid)
    );

    result_store u_result_store (
        .clk(clk_core_i), .rst_n(fifo_reset_no), .req_i(store_req), .write_i(store_write), .addr_i(store_addr),
        .data_i(store_wdata), .data_o(store_rdata), .read_valid_o(store_read_valid)
    );

    // [边界] 边沿字本身是组合映射。有效信号属于本域的完整字接口；跨域
    // 转交 payload 和模式应同一事务，排空由上游在 BIST 准入前确认。
    assign io_edge_words_valid_o = io_word_valid_i && io_word_ready_o;
    ad4080_ddr_edge_words u_edge_words (
        .sample_i(io_sample_i), .dual_lane_i(io_dual_lane_i), .intf_check_i(io_intf_check_i),
        .da_fall_o(io_da_fall_o), .da_rise_o(io_da_rise_o), .db_fall_o(io_db_fall_o),
        .db_rise_o(io_db_rise_o), .slot_count_o(io_slot_count_o)
    );
endmodule

`default_nettype wire
