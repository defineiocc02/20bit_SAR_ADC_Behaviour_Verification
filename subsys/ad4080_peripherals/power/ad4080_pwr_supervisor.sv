`timescale 1ns/1ps

// =============================================================================
// 文件名     : ad4080_pwr_supervisor.sv
// 模块名     : ad4080_pwr_supervisor
// 版本       : v1.0
// 功能       : 管理外围电源模式、复位、隔离与访问许可。
//
// 设计说明   :
//   * AD4080 Rev. B p.24–28 提供供电、等待和保留行为；比较器、LDO、
//     I/O 单元及配置/FIFO/采样功能由各自的实现提供。
//   * AON 位于 VDD11，由连续内部时钟运行；核心域及数据控制域可以停钟。
//     配置和 FIFO 的硬复位仅由核心 POR 触发，软件复位以独立事件提交。
//   * 同沿裁决：硬 POR、合格软件复位、合格模式切换、正常恢复。REFIN 或
//     VDD33 丢失立即封住模拟路径并重启在途唤醒计时，保留 VDD11 内数据。
//   * 时长以 AON 时钟的最快工作频率求整。附加恢复时间以外部完成握手
//     表达，仿真无需假定一个手册未给定的时间值。
//
// 变更记录   : 2026-11-20 增加参考故障事件与无效时钟绑定保护。
// =============================================================================
module ad4080_pwr_supervisor #(
    parameter int unsigned AON_MAX_CLK_HZ = 10_000_000  // 最快实际频率；构建绑定值，单位 Hz，必须大于零
) (
    // =========================================================================
    // 1. 顶层电源、时钟和数字 IP 接点
    // =========================================================================
    // --- 时钟与电源宏回传 ---
    input  logic       clk_aon_i,                        // VDD11 内连续管理时钟，独立于 CNV、SCLK、DCLK
    input  logic       clk_core_i,                       // 核心控制与 FIFO 的有效时钟，可停
    input  logic       clk_io_i,                         // 数据接口内部预置时钟，需在允许首个外部边沿前运行
    input  logic       external_io_i,                    // 冷启动固定，1 表示 VDD11 和 IOVDD 全部外供
    input  logic       por_d_ok_async_i,                 // 1 表示 VDD11 核心 POR 撤销
    input  logic       vdd33_ok_async_i,                 // 1 表示 VDD33 合规且配置输入保护可放行
    input  logic       ref_ok_async_i,                   // 参考监测的故障及恢复门限均由模拟宏实现
    input  logic       iovdd_ok_async_i,                 // 1 表示 IOVDD 工作范围有保证
    input  logic       ana_ready_async_i,                // 1 表示模拟链已从复位或掉压恢复，可以接收 CNV

    // --- 管理时钟域内命令与握手 ---
    input  logic       clients_idle_aon_i,               // 转换、结果事务和数据口均已空闲
    input  logic       sw_reset_req_aon_i,               // 配置 IP 已译码的单周期请求，需回收 accept
    input  logic       mode_req_valid_aon_i,             // 和请求值原子送达 AON 域，仅保持一个周期
    input  logic [1:0] mode_req_aon_i,                   // 00 正常、10 待机、11 睡眠；01 保留
    input  logic       wake_recover_done_aon_i,          // 新请求建立后才给完成，保持至请求撤销

    // --- 硬复位与外围复位 ---
    output logic       cfg_hw_reset_no,                  // AON 域；仅核心硬 POR 清除配置保留态
    output logic       core_reset_no,                    // 核心时钟域；硬 POR 和软件初始化可复位非保持数据通路
    output logic       fifo_reset_no,                    // 核心时钟域；仅硬 POR 清 FIFO 逻辑有效态
    output logic       io_reset_no,                      // I/O 时钟域；可清接口事务状态，勿接 FIFO 指针
    output logic       ana_reset_no,                     // AON 安全控制；模拟宏适配逻辑负责接收

    // --- 访问、状态与事务裁决 ---
    output logic       cfg_init_done_aon_o,              // 300 µs 初始化期后放行非状态访问
    output logic       cfg_control_write_allow_aon_o,    // 0x00/0x02 的复位和模式控制写许可
    output logic       cfg_full_access_allow_aon_o,      // 配置读回和普通写许可，读回依赖 IOVDD
    output logic       fifo_read_allow_aon_o,            // 读出已有 FIFO 的许可；须再检查读端协议及数据有效
    output logic       lvds_allow_aon_o,                 // 仅正常态的数据模式许可；不承诺有结果或测试字
    output logic       conversion_allow_aon_o,           // 新转换电源与模式许可；再叠加系统服务互斥
    output logic [1:0] operating_mode_aon_o,             // 被接受的模式；唤醒中先报告 00，看 wake_busy
    output logic       wake_busy_aon_o,                  // 模式已回到 00，仍在等待恢复
    output logic       wake_recover_req_aon_o,           // 手册 t_wake-up 后开始核验附加恢复
    output logic       mode_req_accept_aon_o,            // 在 AON 同沿提交模式；0 时请求由上游处理
    output logic       sw_reset_accept_aon_o,            // 在 AON 同沿清非保留配置，优先于模式写
    output logic       por_ana_event_aon_o,              // VDD33 有效时的新 REFIN 故障事件，由配置端锁存为 W1C

    // --- 物理边界的控制请求 ---
    output logic       isolate_io_o,                     // 高有效；需由活电域的专用隔离/钳位单元执行
    output logic       iovdd_ldo_en_o,                  // 内供 IOVDD LDO 的高有效工作请求
    output logic       ref_cmo_en_o,                     // 参考及 CMO 缓冲器的工作请求
    input  logic [1:0] cfg_ldo_pd_aon_i = 2'b00          // 已受理的内供关断位，外供忽略
);
    // =========================================================================
    // 2. 公共时序常量与状态
    // =========================================================================
    localparam logic [1:0] MODE_NORMAL  = 2'b00;
    localparam logic [1:0] MODE_STANDBY = 2'b10;
    localparam logic [1:0] MODE_SLEEP   = 2'b11;

    // [依据] Rev. B p.24、p.26–28。64 位中间量避免频率与微秒相乘溢出。
    // 向上取整后以完整的目的周期计数，较慢的 AON 只会加长等待。
    localparam bit TIMING_CONFIG_VALID = AON_MAX_CLK_HZ != 0;
    localparam longint unsigned TICK_RATE = 64'(AON_MAX_CLK_HZ);
    localparam int unsigned INIT_TICKS = 32'((TICK_RATE * 300 + 999_999) / 1_000_000);
    localparam int unsigned STBY_TICKS = 32'((TICK_RATE * 100 + 999_999) / 1_000_000);
    localparam int unsigned SLEEP_TICKS = 32'((TICK_RATE * 180 + 999_999) / 1_000_000);
    localparam int TIMER_WIDTH = (INIT_TICKS < 2) ? 1 : $clog2(INIT_TICKS);

    typedef enum logic [2:0] {
        RUN_NORMAL, LOW_STANDBY, LOW_SLEEP, WAKE_DELAY, WAKE_RECOVER
    } mode_state_t;

    mode_state_t mode_q;
    logic [TIMER_WIDTH-1:0] init_ticks_q; // 初始化完整周期的累计值
    logic [TIMER_WIDTH-1:0] wake_ticks_q; // 在模拟电源合格条件下累计唤醒周期
    logic [TIMER_WIDTH-1:0] wake_last_q;  // 保留进入唤醒前的模式所选计数终值
    logic init_done_q;
    logic analog_run_req_q;  // 保持单比特实现，模式译码的瞬态不直接接复位树
    logic recover_rearmed_q; // done 在本次握手中已观察到低值，旧完成不能误放行
    logic ldo_io_en_q;       // 独立于隔离控制更新，先钳位再关内供 LDO
    logic pad_safe_aon;
    logic ana_power_ok_aon;
    logic ana_ready_aon;
    logic core_live_aon;
    logic io_live_aon;
    logic core_release_ni;
    logic io_release_ni;
    logic core_pd_aon, io_pd_aon; // 已受理的独立控制，PG 输入仍来自真实监测器
    logic wake_power_ok;
    logic ref_fault_hold_no; // 异步捕获，跨 AON 时钟边沿延长短故障
    (* ASYNC_REG = "TRUE" *) logic [1:0] ref_fault_sync_q;
    logic ref_fault_prev_q; // 区分故障区间的起点及持续低压

    // =========================================================================
    // 3. 复位树与跨域返回
    // =========================================================================
    // [保持] 配置及 FIFO 的硬复位只感知核心电源，避免在 REFIN 低或 IOVDD
    // 关断时清配置、数据或读指针。存储阵列本体不必逐字清零，由有效态管理。
    ad4080_pwr_reset_sync u_cfg_por (
        .clk_i(clk_aon_i), .hold_ni(por_d_ok_async_i), .ready_no(cfg_hw_reset_no)
    );
    ad4080_pwr_reset_sync u_fifo_por (
        .clk_i(clk_core_i), .hold_ni(por_d_ok_async_i), .ready_no(fifo_reset_no)
    );

    // [上电] VDD33 合格才接收配置输入活动。REFIN 比较器由 VDD33 供电，
    // 复位链同时看两者，避免比较器掉电时遗留的“参考正常”放行模拟域。
    ad4080_pwr_reset_sync u_pad_safe (
        .clk_i(clk_aon_i),
        .hold_ni(por_d_ok_async_i && vdd33_ok_async_i),
        .ready_no(pad_safe_aon)
    );
    ad4080_pwr_reset_sync u_ana_power (
        .clk_i(clk_aon_i),
        .hold_ni(por_d_ok_async_i && vdd33_ok_async_i && ref_ok_async_i),
        .ready_no(ana_power_ok_aon)
    );

    // [状态] 参考监测器由 VDD33 供电，VDD33 缺失时单独钳位参考状态输入。
    // 用异步低电平捕获没有跨过 AON 沿的 REFIN 故障，再同步成单周期事件。
    // POR 与软件复位初值仍由配置端负责，持续低压只发一次参考故障事件。
    ad4080_pwr_reset_sync u_ref_fault_hold (
        .clk_i(clk_aon_i),
        .hold_ni(!vdd33_ok_async_i || ref_ok_async_i),
        .ready_no(ref_fault_hold_no)
    );

    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            ref_fault_sync_q <= '0;
            ref_fault_prev_q <= 1'b0;
            por_ana_event_aon_o <= 1'b0;
        end else begin
            ref_fault_sync_q <= {ref_fault_sync_q[0], !ref_fault_hold_no};
            ref_fault_prev_q <= ref_fault_sync_q[1];
            por_ana_event_aon_o <= ref_fault_sync_q[1] && !ref_fault_prev_q;
        end
    end

    // [局部释放] 软件初始化可重启数据路径；低功耗时外供 IOVDD 仍可使用。
    // 核心和 I/O 各自释放。许可返回 AON 需再次同步，不能借用另一域的释放沿。
    assign core_pd_aon = !external_io_i && cfg_ldo_pd_aon_i[1];
    assign io_pd_aon = !external_io_i && (cfg_ldo_pd_aon_i[0] || core_pd_aon);
    assign core_release_ni = cfg_hw_reset_no && init_done_q && !core_pd_aon;
    assign io_release_ni = core_release_ni && (external_io_i || (analog_run_req_q && !io_pd_aon))
                         && iovdd_ok_async_i;

    ad4080_pwr_reset_sync u_core_reset (
        .clk_i(clk_core_i), .hold_ni(core_release_ni), .ready_no(core_reset_no)
    );
    ad4080_pwr_reset_sync u_io_reset (
        .clk_i(clk_io_i), .hold_ni(io_release_ni), .ready_no(io_reset_no)
    );
    ad4080_pwr_reset_sync u_core_live (
        .clk_i(clk_aon_i), .hold_ni(core_reset_no), .ready_no(core_live_aon)
    );
    ad4080_pwr_reset_sync u_io_live (
        .clk_i(clk_aon_i), .hold_ni(io_reset_no), .ready_no(io_live_aon)
    );

    // [握手] 模拟可以在唤醒过程中释放复位以完成建立。转换仍由独立许可关断，
    // 以免等“模拟就绪”时又把模拟一直扣在复位内。
    ad4080_pwr_reset_sync u_ana_reset (
        .clk_i(clk_aon_i),
        .hold_ni(core_release_ni && ana_power_ok_aon && analog_run_req_q),
        .ready_no(ana_reset_no)
    );
    ad4080_pwr_reset_sync u_ana_ready (
        .clk_i(clk_aon_i),
        .hold_ni(ana_reset_no && ana_ready_async_i),
        .ready_no(ana_ready_aon)
    );

    // =========================================================================
    // 4. 访问许可与初始化
    // =========================================================================
    // [接口] 低功耗内供时保留配置控制写入口；全功能配置另需读回所在的
    // IOVDD 域。外供 IOVDD 时 VDD33 可以撤除，SPI 数据口仍可读取先前的 FIFO。
    assign cfg_init_done_aon_o = init_done_q;
    assign cfg_control_write_allow_aon_o = init_done_q && pad_safe_aon;
    assign cfg_full_access_allow_aon_o = cfg_control_write_allow_aon_o && io_live_aon;
    assign fifo_read_allow_aon_o = io_live_aon && (external_io_i || mode_q == RUN_NORMAL);
    assign lvds_allow_aon_o = io_live_aon && mode_q == RUN_NORMAL;
    assign conversion_allow_aon_o = core_live_aon && ana_ready_aon && mode_q == RUN_NORMAL;
    assign isolate_io_o = !io_live_aon;

    // [软件复位] 配置 IP 仅在合格的同沿消费此事件。FIFO 载荷、指针有效态
    // 及 ADDR_ASCENSION 走硬 POR 边界。初始化忙期间由配置 IP 上报 NOT_READY_ERR。
    assign sw_reset_accept_aon_o = sw_reset_req_aon_i && clients_idle_aon_i
                                 && cfg_control_write_allow_aon_o;

    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            init_ticks_q <= '0;
            init_done_q <= 1'b0;
        end else if (sw_reset_accept_aon_o) begin
            init_ticks_q <= '0;
            init_done_q <= 1'b0;
        // [非法绑定] 频率为零时保留初始化守卫，避免 ticks-1 下溢后提前放行。
        end else if (!init_done_q && TIMING_CONFIG_VALID) begin
            if (init_ticks_q == TIMER_WIDTH'(INIT_TICKS - 1)) begin
                init_done_q <= 1'b1;
            end else begin
                init_ticks_q <= init_ticks_q + 1'b1;
            end
        end
    end

    // =========================================================================
    // 5. 模式受理、恢复和内供 IOVDD 控制
    // =========================================================================
    // [裁决] 一次请求对应一个 AON 沿；上游先排干事务，再读组合 accept 并提交。
    // 省电模式只接受返回 00，醒来途中禁止重复模式写覆盖上一次恢复。
    always_comb begin
        mode_req_accept_aon_o = 1'b0;
        if (mode_req_valid_aon_i && cfg_control_write_allow_aon_o
            && clients_idle_aon_i && !sw_reset_accept_aon_o && !core_pd_aon) begin
            case (mode_q)
                RUN_NORMAL:
                    mode_req_accept_aon_o = (mode_req_aon_i != 2'b01);
                LOW_STANDBY, LOW_SLEEP:
                    mode_req_accept_aon_o = (mode_req_aon_i == MODE_NORMAL);
                default:
                    mode_req_accept_aon_o = 1'b0;
            endcase
        end
    end

    assign operating_mode_aon_o = (mode_q == LOW_STANDBY) ? MODE_STANDBY :
                                  (mode_q == LOW_SLEEP) ? MODE_SLEEP : MODE_NORMAL;
    assign wake_busy_aon_o = (mode_q == WAKE_DELAY) || (mode_q == WAKE_RECOVER);
    assign wake_power_ok = init_done_q && ana_power_ok_aon;
    assign wake_recover_req_aon_o = (mode_q == WAKE_RECOVER) && recover_rearmed_q
                                 && wake_power_ok;

    // [恢复] 100/180 µs 从接受唤醒时起算，参考不合格时重起计数。
    // 随后再等本次恢复的 done 和模拟 ready。电源在等待中掉落则撤回请求，
    // 恢复后得重新计时并等新的完成，旧完成不能打开 CNV。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            mode_q <= RUN_NORMAL;
            analog_run_req_q <= 1'b1;
            wake_ticks_q <= '0;
            wake_last_q <= '0;
            recover_rearmed_q <= 1'b0;
        end else if (sw_reset_accept_aon_o && wake_busy_aon_o) begin
            mode_q <= WAKE_DELAY;
            wake_ticks_q <= '0;
            recover_rearmed_q <= 1'b0;
        end else begin
            case (mode_q)
                RUN_NORMAL: begin
                    if (mode_req_accept_aon_o && mode_req_aon_i != MODE_NORMAL) begin
                        mode_q <= (mode_req_aon_i == MODE_STANDBY) ? LOW_STANDBY : LOW_SLEEP;
                        analog_run_req_q <= 1'b0;
                    end
                end
                LOW_STANDBY, LOW_SLEEP: begin
                    // 软件复位也能唤醒，但保留回应前的模拟恢复联锁。
                    if (mode_req_accept_aon_o || sw_reset_accept_aon_o) begin
                        mode_q <= WAKE_DELAY;
                        analog_run_req_q <= 1'b1;
                        wake_ticks_q <= '0;
                        wake_last_q <= (mode_q == LOW_STANDBY)
                                     ? TIMER_WIDTH'(STBY_TICKS - 1)
                                     : TIMER_WIDTH'(SLEEP_TICKS - 1);
                        recover_rearmed_q <= 1'b0;
                    end
                end
                WAKE_DELAY: begin
                    if (!wake_power_ok) begin
                        wake_ticks_q <= '0;
                    end else if (wake_ticks_q == wake_last_q) begin
                        mode_q <= WAKE_RECOVER;
                    end else begin
                        wake_ticks_q <= wake_ticks_q + 1'b1;
                    end
                end
                WAKE_RECOVER: begin
                    if (!wake_power_ok) begin
                        mode_q <= WAKE_DELAY;
                        wake_ticks_q <= '0;
                        recover_rearmed_q <= 1'b0;
                    end else if (!wake_recover_done_aon_i) begin
                        recover_rearmed_q <= 1'b1;
                    end else if (recover_rearmed_q && ana_ready_aon) begin
                        mode_q <= RUN_NORMAL;
                        recover_rearmed_q <= 1'b0;
                    end
                end
                default: begin
                    // 非法编码至少维持数据许可关闭，等待受控重新初始化。
                    mode_q <= LOW_SLEEP;
                    analog_run_req_q <= 1'b0;
                end
            endcase
        end
    end

    // [隔离] 退出数据域时复位和隔离先建立。下一 AON 沿确认钳位请求
    // 已经为高，再撤 LDO 请求。物理实现需证明该间隔覆盖最坏钳位延迟。
    // 冷启动由模拟默认电路置为使能，避免未起电的 AON 决定自身启动顺序。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            ldo_io_en_q <= 1'b1;
        end else if (analog_run_req_q && !io_pd_aon) begin
            ldo_io_en_q <= 1'b1;
        end else if (isolate_io_o) begin
            ldo_io_en_q <= 1'b0;
        end
    end

    assign iovdd_ldo_en_o = !external_io_i && ldo_io_en_q;
    assign ref_cmo_en_o = pad_safe_aon && mode_q != LOW_SLEEP;
endmodule
