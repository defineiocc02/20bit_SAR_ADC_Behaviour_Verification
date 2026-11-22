`timescale 1ns/1ps

// =============================================================================
// 文件名     : tb_ad4080_pwr_supervisor.sv
// 模块名     : tb_ad4080_pwr_supervisor
// 版本       : v1.0
// 功能       : 验证电源管理接点能区分硬复位、接口隔离和模拟转换许可。
//
// 设计说明   :
//   * 三个时钟频率不同；专门停掉目的时钟，检查掉压断言与释放行为。
//   * 一个样本及两个配置位只用于观察保持边界，不含产品 SPI、FIFO 或 ADC IP。
//   * 用真实 300 / 100 / 180 µs 计数，附加恢复完成信号由测试在指定点提供。
//   * 轨电压用理想布尔量表示；本测试不能反映比较器、钳位或电源 ramp 的物理行为。
//
// 变更记录   : 2026-11-20 补低功耗旁路、短故障粘滞、重启与参数边界。
// =============================================================================
module tb_ad4080_pwr_supervisor #(
    parameter int unsigned TEST_AON_MAX_CLK_HZ = 10_000_000  // 默认为真实 10 MHz，其他档位用于边界配置
);
    // =========================================================================
    // 1. 测试时钟及接线
    // =========================================================================
    logic clk_aon_i = 1'b0;
    logic clk_core_i = 1'b0;
    logic clk_io_i = 1'b0;
    logic aon_clock_on = 1'b1;  // 注入跨不过管理时钟沿的参考短故障
    logic core_clock_on = 1'b1; // 核心欠压向量在此门控时钟，停后等待输出稳定
    logic io_clock_on = 1'b1; // I/O 欠压向量停钟，随后单独恢复轨与时钟
    logic external_io_i = 1'b0; // 两次冷启动分别取内供、全外供
    logic por_d_ok_async_i = 1'b0; // 先以布尔监测值表达低压，所有轨初始失效
    logic vdd33_ok_async_i = 1'b0;
    logic ref_ok_async_i = 1'b0;
    logic iovdd_ok_async_i = 1'b0;
    logic ana_ready_async_i = 1'b0; // 与 REFIN 分别驱动，验证参考正常和模拟建立是两道条件
    logic clients_idle_aon_i = 1'b1; // 非空闲向量模拟尚有转换或数据口事务
    logic sw_reset_req_aon_i = 1'b0;         // 仅保持到配置命令受理沿
    logic mode_req_valid_aon_i = 1'b0;       // 拒绝后撤掉，防止下一状态误消费同笔请求
    logic [1:0] mode_req_aon_i = 2'b00;
    logic wake_recover_done_aon_i = 1'b0; // 既送正常确认，也送过期确认检查重装联锁
    wire [1:0] cfg_ldo_pd_aon_i = 2'b00; // 本台架保留旧模式许可，0x18 在产品 SPI 台架检查。

    logic cfg_hw_reset_no, core_reset_no, fifo_reset_no, io_reset_no, ana_reset_no;
    logic cfg_init_done_aon_o, cfg_control_write_allow_aon_o, cfg_full_access_allow_aon_o;
    logic fifo_read_allow_aon_o, lvds_allow_aon_o, conversion_allow_aon_o;
    logic [1:0] operating_mode_aon_o;
    logic wake_busy_aon_o, wake_recover_req_aon_o, mode_req_accept_aon_o, sw_reset_accept_aon_o;
    logic isolate_io_o, iovdd_ldo_en_o, ref_cmo_en_o;
    logic por_ana_event_aon_o;               // 只验证电源包向 CSR 所有者传递的事件

    localparam logic [1:0] NORMAL  = 2'b00;
    localparam logic [1:0] STANDBY = 2'b10;
    localparam logic [1:0] SLEEP   = 2'b11;
    localparam logic [19:0] SAMPLE = 20'h5a3ce; // 20 位非对称观测字，翻转或清零均可辨识

    // [异步] 100 ns 的 AON 周期对应 10 MHz 参数。核心和接口在其他相位采样，
    // 并通过时钟停止构造实际缺少释放沿的情况。
    always #50 clk_aon_i <= aon_clock_on ? ~clk_aon_i : 1'b0;
    always #70 clk_core_i <= core_clock_on ? ~clk_core_i : 1'b0;
    always #90 clk_io_i <= io_clock_on ? ~clk_io_i : 1'b0;

    // 本测试保持信号名和方向与 DUT 端口一致。
    ad4080_pwr_supervisor #(.AON_MAX_CLK_HZ(TEST_AON_MAX_CLK_HZ)) u_dut (.*);

    // =========================================================================
    // 2. 保持边界观测器及事务驱动
    // =========================================================================
    logic cfg_mark_i = 1'b0; // 同一写事务种入两个配置标记
    logic mock_cfg_q, mock_addr_asc_q; // 普通配置和 ADDR_ASCENSION 使用不同的软件复位路径
    logic sample_write_i = 1'b0; // 在核心域发一帧合法写，测试过程固定该帧
    logic mock_fifo_valid_q; // 用有效位判断硬 POR 后旧字是否还能被读到
    logic [19:0] mock_fifo_word_q;
    logic mock_por_ana_q;                    // 单独观测 W1C 语义，供电模块只产事件
    logic mock_por_clear_i = 1'b0;           // 模拟配置端 0x14 bit 3 的合法 W1C
    int unsigned analog_events_q;            // 检查故障区间只出一次事件

    // [验收] 两类配置位同时写入。软件复位应只清普通位，硬 POR 应清两位。
    // 使用实际复位和软件事件接点，失败意味着上层无法按手册分绑寄存器。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            mock_cfg_q <= 1'b0;
            mock_addr_asc_q <= 1'b0;
        end else if (sw_reset_accept_aon_o) begin
            mock_cfg_q <= 1'b0;
        end else if (cfg_mark_i) begin
            mock_cfg_q <= 1'b1;
            mock_addr_asc_q <= 1'b1;
        end
    end

    // [验收] 被读样本与有效性仅随 FIFO 硬复位清除。测试从可读许可观察稳定
    // 字，避免把单样本模型的读写逻辑当成原 FIFO 的实现。
    always_ff @(posedge clk_core_i or negedge fifo_reset_no) begin
        if (!fifo_reset_no) begin
            mock_fifo_word_q <= '0;
            mock_fifo_valid_q <= 1'b0;
        end else if (sample_write_i) begin
            mock_fifo_word_q <= SAMPLE;
            mock_fifo_valid_q <= 1'b1;
        end
    end

    // [边界] CSR 模型体现特定一位的接线约定。复位或新故障与 W1C 同沿时
    // 置位优先，清位后持续的老故障不会按电平反复置位。
    always_ff @(posedge clk_aon_i or negedge cfg_hw_reset_no) begin
        if (!cfg_hw_reset_no) begin
            mock_por_ana_q <= 1'b1;
            analog_events_q <= 0;
        end else begin
            if (por_ana_event_aon_o) analog_events_q <= analog_events_q + 1;
            if (sw_reset_accept_aon_o || por_ana_event_aon_o) mock_por_ana_q <= 1'b1;
            else if (mock_por_clear_i) mock_por_ana_q <= 1'b0;
        end
    end

    task automatic check(input logic cond, input string context_msg);
        if (cond !== 1'b1)
            $fatal(1, "[%0t] %s", $time, context_msg);
    endtask

    task automatic tick_aon(input int n);
        repeat (n) @(posedge clk_aon_i);
        #1; // 取 AON 沿更新后的结果，避开 NBA 竞态
    endtask

    task automatic mode_write(input logic [1:0] request, input logic accepted);
        @(negedge clk_aon_i);
        mode_req_aon_i = request;
        mode_req_valid_aon_i = 1'b1;
        #1; // 组合 accept 在受理沿前稳定
        check(mode_req_accept_aon_o == accepted, $sformatf("mode request %b acceptance", request));
        @(posedge clk_aon_i);
        #1;
        mode_req_valid_aon_i = 1'b0;
    endtask

    task automatic capture_markers;
        @(negedge clk_core_i);
        // 配置和样本只能在完成各域的真实释放之后种入。
        check(core_reset_no && fifo_reset_no, "test write requires both core and FIFO reset releases");
        sample_write_i = 1'b1;
        @(posedge clk_core_i);
        #1;
        sample_write_i = 1'b0;
        @(negedge clk_aon_i);
        cfg_mark_i = 1'b1;
        tick_aon(1);
        cfg_mark_i = 1'b0;
        check(mock_fifo_valid_q && mock_cfg_q && mock_addr_asc_q, "seed retained state");
    endtask

    task automatic can_read_fifo(input string context_msg);
        check(fifo_read_allow_aon_o && mock_fifo_valid_q && mock_fifo_word_q == SAMPLE, context_msg);
    endtask

    task automatic set_all_power_good;
        // 参数专项向量把各监测器置好，避免上电顺序影响计数断点。
        por_d_ok_async_i = 1'b1;
        vdd33_ok_async_i = 1'b1;
        ref_ok_async_i = 1'b1;
        iovdd_ok_async_i = 1'b1;
        ana_ready_async_i = 1'b1;
    endtask

    task automatic software_reset;
        // 模拟配置端已经解码和排空事务，消除 SPI 帧长度的影响。
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b1;
        #1;
        check(sw_reset_accept_aon_o, "software reset request blocked while idle");
        tick_aon(1);
        sw_reset_req_aon_i = 1'b0;
    endtask

    task automatic wake_at_tick(input logic [1:0] from_mode, input int ticks);
        mode_write(from_mode, 1'b1);
        mode_write(NORMAL, 1'b1);
        // 完成握手在计时后一个目的沿观察低值，再建立请求。
        tick_aon(ticks);
        check(wake_busy_aon_o && !wake_recover_req_aon_o && !conversion_allow_aon_o,
              "parameterized wake request released early");
        tick_aon(1);
        check(wake_recover_req_aon_o, "parameterized wake request released late");
        wake_recover_done_aon_i = 1'b1;
        tick_aon(2);
        wake_recover_done_aon_i = 1'b0;
        check(conversion_allow_aon_o, "parameterized wake did not finish");
    endtask

    task automatic check_time_profile(input int init_ticks, input int stby_ticks, input int sleep_ticks);
        // 保持相同仿真时钟，只比较整数周期而非极端频率的实际波形。
        tick_aon(3);
        set_all_power_good();
        tick_aon(2); // 硬 POR 同步释放之后才起算完整初始化周期
        tick_aon(init_ticks - 1);
        check(!cfg_init_done_aon_o, "parameterized initialization released early");
        tick_aon(1);
        check(cfg_init_done_aon_o, "parameterized initialization released late");
        tick_aon(10);
        wake_at_tick(STANDBY, stby_ticks);
        wake_at_tick(SLEEP, sleep_ticks);
        $display("PASS PARAM %0d Hz INIT=%0d STBY=%0d SLEEP=%0d",
                 TEST_AON_MAX_CLK_HZ, init_ticks, stby_ticks, sleep_ticks);
    endtask

    // =========================================================================
    // 3. 定向向量
    // =========================================================================
    initial begin : run_main
        int unsigned event_count_before;

        // [验收] 相同旧版激励观测无效参数时的安全输出。最大 uint32 与刚超过
        // 整周期的频率分别校验乘法宽度和向上取整，期望值来自离线整数计算。
        if (TEST_AON_MAX_CLK_HZ == 0) begin
            tick_aon(3);
            set_all_power_good();
            tick_aon(20);
            $display("ZERO_BOUND cfg_init=%0b cfg_write=%0b conv=%0b iso=%0b",
                     cfg_init_done_aon_o, cfg_control_write_allow_aon_o,
                     conversion_allow_aon_o, isolate_io_o);
            check(!cfg_init_done_aon_o && !cfg_control_write_allow_aon_o
                  && !conversion_allow_aon_o && isolate_io_o, "invalid zero AON frequency admitted protected clients");
            $display("PASS PARAM 0 Hz protected clients held");
            $finish;
        end else if (TEST_AON_MAX_CLK_HZ == 10_000_001) begin
            check_time_profile(3001, 1001, 1801);
            $finish;
        end else if (TEST_AON_MAX_CLK_HZ == 32'hffff_ffff) begin
            check_time_profile(1_288_491, 429_497, 773_095);
            $finish;
        end

        // [验收] 核心 1.1 V 已起、电压参考尚未有效时，数字初始化可照常完成。
        // 未到 300 µs 或 VDD33 钳位不安全时，配置服务仍应受限。
        tick_aon(3);
        check(!cfg_hw_reset_no && !fifo_reset_no && isolate_io_o && iovdd_ldo_en_o, "internal cold defaults");
        @(negedge clk_aon_i);
        #7;
        por_d_ok_async_i = 1'b1;
        iovdd_ok_async_i = 1'b1;
        tick_aon(1);
        check(!cfg_hw_reset_no, "AON POR released on one edge");
        tick_aon(1);
        check(cfg_hw_reset_no && !cfg_init_done_aon_o, "AON POR did not release on two edges");
        tick_aon(2_999); // 冷启动要求的最后一个完整周期之前
        check(!cfg_init_done_aon_o && !core_reset_no, "300 us guard released early");
        tick_aon(1);
        check(cfg_init_done_aon_o && !cfg_control_write_allow_aon_o, "VDD33 pad guard or exact initialization");
        vdd33_ok_async_i = 1'b1;
        tick_aon(12);
        check(cfg_full_access_allow_aon_o && !ana_reset_no && !conversion_allow_aon_o, "configuration must work before reference");
        ref_ok_async_i = 1'b1;
        ana_ready_async_i = 1'b1;
        tick_aon(12);
        check(ana_reset_no && conversion_allow_aon_o && lvds_allow_aon_o, "normal entry");
        capture_markers();
        $display("PASS 01 cold sequencing, separate POR and 300 us guard");

        // [验收] 停 AON 后给参考监测一个 6 ns 故障。复位与转换抑制立即有效，
        // 配置及 FIFO 保持。恢复 AON 后才产生同步事件供已清除的 W1C 位再置。
        event_count_before = analog_events_q;
        @(negedge clk_aon_i);
        mock_por_clear_i = 1'b1;
        tick_aon(1);
        mock_por_clear_i = 1'b0;
        check(!mock_por_ana_q, "analog POR model was not cleared by W1C");
        @(negedge clk_aon_i);
        aon_clock_on = 1'b0;
        #147; // 先确保管理时钟已停止，再把故障放进两个虚拟沿之间
        ref_ok_async_i = 1'b0;
        #1;
        check(!ana_reset_no && !conversion_allow_aon_o && !por_ana_event_aon_o,
              "REFIN dip must take effect while AON is stopped");
        check(mock_cfg_q && cfg_hw_reset_no && core_reset_no && cfg_full_access_allow_aon_o, "REFIN dip cleared digital");
        can_read_fifo("FIFO data preserved on REFIN dip");
        #5; // 连同前一处 #1，输入合计保持故障 6 ns
        ref_ok_async_i = 1'b1;
        #24;
        check(!ana_reset_no && !conversion_allow_aon_o && !mock_por_ana_q, "analog released without an AON edge");
        aon_clock_on = 1'b1;
        tick_aon(3); // 捕获链恢复与同步后，先观察事件，再由配置模型消费
        check(por_ana_event_aon_o && !mock_por_ana_q, "6 ns REFIN fault was lost while AON stopped");
        mock_por_clear_i = 1'b1;
        tick_aon(1);
        mock_por_clear_i = 1'b0;
        check(!por_ana_event_aon_o && mock_por_ana_q && analog_events_q == event_count_before + 1,
              "new POR event must win over simultaneous W1C");
        tick_aon(6);
        check(conversion_allow_aon_o, "REFIN recovery");

        // [验收] 持续低压只记一个边沿。W1C 在故障仍低时可清除记录，
        // 下一次经过 VDD33 授权的独立参考下降才重新置位。
        ref_ok_async_i = 1'b0;
        tick_aon(7);
        check(mock_por_ana_q && analog_events_q == event_count_before + 2, "new persistent REFIN fault event");
        mock_por_clear_i = 1'b1;
        tick_aon(1);
        mock_por_clear_i = 1'b0;
        tick_aon(6); // 延长检查窗，避免重复事件被偶然漏采
        check(!mock_por_ana_q && !conversion_allow_aon_o && analog_events_q == event_count_before + 2,
              "W1C on persistent fault must not synthesize repeat events");
        ref_ok_async_i = 1'b1;
        tick_aon(8);
        vdd33_ok_async_i = 1'b0;
        #1; // 先让供电监测失效，再观察断电的参考监测输出
        ref_ok_async_i = 1'b0;
        tick_aon(6);
        check(!mock_por_ana_q && !cfg_control_write_allow_aon_o && analog_events_q == event_count_before + 2,
              "unpowered reference monitor must not cause a new POR event");
        vdd33_ok_async_i = 1'b1;
        tick_aon(3);
        ref_ok_async_i = 1'b1;
        tick_aon(8);
        $display("PASS 02 6 ns REFIN capture while AON stopped, W1C re-set and retained state");

        // [验收] 内供先钳位再关 IOVDD。VDD33 缺失不能受理唤醒写，但配置位和
        // FIFO 的有效记录仍保持。直接切换省电模式和未排空的进入请求都应拒绝。
        clients_idle_aon_i = 1'b0;
        mode_write(STANDBY, 1'b0);
        clients_idle_aon_i = 1'b1;
        mode_write(2'b01, 1'b0);
        mode_write(STANDBY, 1'b1);
        check(operating_mode_aon_o == STANDBY && isolate_io_o && !io_reset_no
              && iovdd_ldo_en_o, "internal standby clamp must lead LDO disable");
        tick_aon(1);
        check(!iovdd_ldo_en_o && cfg_control_write_allow_aon_o && !cfg_full_access_allow_aon_o
              && !fifo_read_allow_aon_o && !lvds_allow_aon_o && !conversion_allow_aon_o && ref_cmo_en_o,
              "internal standby service restrictions");
        mode_write(SLEEP, 1'b0);
        iovdd_ok_async_i = 1'b0;
        ref_ok_async_i = 1'b0;
        vdd33_ok_async_i = 1'b0;
        ana_ready_async_i = 1'b0;
        tick_aon(3);
        mode_write(NORMAL, 1'b0);
        check(mock_cfg_q && mock_fifo_valid_q && !ref_cmo_en_o, "VDD33 removal changed retained state");
        $display("PASS 03 internal standby, power sequencing and illegal requests");

        // [验收] 100 µs 后等待新恢复握手。握手在参考欠压时撤销，旧 done 即便
        // 被上游迟送也不许打开 CNV，恢复需重计时，并等待模拟宏再次就绪。
        vdd33_ok_async_i = 1'b1;
        tick_aon(3);
        ref_ok_async_i = 1'b1;
        tick_aon(5);
        mode_write(NORMAL, 1'b1);
        check(wake_busy_aon_o && operating_mode_aon_o == NORMAL && !conversion_allow_aon_o, "standby wake phase");
        tick_aon(1);
        check(iovdd_ldo_en_o, "IOVDD LDO not restarted");
        iovdd_ok_async_i = 1'b1;
        tick_aon(998); // 与前面一个周期累计为 999 个唤醒周期
        check(!wake_recover_req_aon_o && !conversion_allow_aon_o, "100 us guard released early");
        tick_aon(2);
        check(wake_recover_req_aon_o && !conversion_allow_aon_o, "missing post-wake request");
        ref_ok_async_i = 1'b0;
        #1;
        check(!wake_recover_req_aon_o && !ana_reset_no, "REFIN loss must cancel recovery request");
        tick_aon(2);
        wake_recover_done_aon_i = 1'b1;
        ref_ok_async_i = 1'b1;
        tick_aon(1_012);
        check(!wake_recover_req_aon_o && !conversion_allow_aon_o, "stale done must block rearming");
        wake_recover_done_aon_i = 1'b0;
        tick_aon(1);
        check(wake_recover_req_aon_o, "new wake request after stale acknowledgement");
        wake_recover_done_aon_i = 1'b1;
        tick_aon(3);
        check(wake_busy_aon_o && !conversion_allow_aon_o, "done alone must not bypass analog ready");
        ana_ready_async_i = 1'b1;
        tick_aon(8);
        wake_recover_done_aon_i = 1'b0;
        check(!wake_busy_aon_o && conversion_allow_aon_o && !isolate_io_o, "standby release");
        can_read_fifo("internal FIFO must be readable after wake");
        $display("PASS 04 standby 100 us and fresh recovery handshake");

        // [验收] 全外供配置通过独立硬 POR 重启。睡眠即使撤 VDD33 也可由 SPI
        // 数据口读已有 FIFO。LVDS 始终关闭；配置输入仍服从 VDD33 钳位条件。
        por_d_ok_async_i = 1'b0;
        vdd33_ok_async_i = 1'b0;
        ref_ok_async_i = 1'b0;
        ana_ready_async_i = 1'b0;
        iovdd_ok_async_i = 1'b0;
        tick_aon(3);
        external_io_i = 1'b1;
        por_d_ok_async_i = 1'b1;
        iovdd_ok_async_i = 1'b1;
        tick_aon(4);
        vdd33_ok_async_i = 1'b1;
        tick_aon(4);
        ref_ok_async_i = 1'b1;
        ana_ready_async_i = 1'b1;
        tick_aon(3_010);
        capture_markers();
        mode_write(SLEEP, 1'b1);
        check(operating_mode_aon_o == SLEEP && !iovdd_ldo_en_o && !ref_cmo_en_o
              && !lvds_allow_aon_o && !conversion_allow_aon_o && cfg_full_access_allow_aon_o
              && !isolate_io_o, "external sleep service split");
        can_read_fifo("external IOVDD sleep FIFO read");
        mode_write(STANDBY, 1'b0);
        check(operating_mode_aon_o == SLEEP && !wake_busy_aon_o,
              "rejected sleep-to-standby write must preserve sleep state");
        ref_ok_async_i = 1'b0;
        vdd33_ok_async_i = 1'b0;
        ana_ready_async_i = 1'b0;
        #1;
        check(!cfg_control_write_allow_aon_o && !cfg_full_access_allow_aon_o && mock_cfg_q, "external sleep pad protection");
        can_read_fifo("external IOVDD FIFO read with VDD33 off");
        vdd33_ok_async_i = 1'b1;
        tick_aon(3);
        ref_ok_async_i = 1'b1;
        ana_ready_async_i = 1'b1;
        tick_aon(10);
        mode_write(NORMAL, 1'b1);
        mode_write(STANDBY, 1'b0);
        mode_write(NORMAL, 1'b0);
        tick_aon(1_000); // 已跨过待机界线，仍须继续等待睡眠的长守卫
        check(!wake_recover_req_aon_o && !conversion_allow_aon_o, "sleep guard shortened by standby request");
        tick_aon(797); // 含已拒绝的两个写请求，共 1799 个唤醒周期
        check(!wake_recover_req_aon_o && !conversion_allow_aon_o, "180 us guard released early");
        tick_aon(2);
        check(wake_recover_req_aon_o && !lvds_allow_aon_o, "sleep recovery interlock");
        can_read_fifo("external FIFO available during analog wake");
        wake_recover_done_aon_i = 1'b1;
        tick_aon(2);
        wake_recover_done_aon_i = 1'b0;
        check(conversion_allow_aon_o && lvds_allow_aon_o, "sleep release");
        $display("PASS 05 external sleep, FIFO read with analog rail absent, 180 us guard");

        // [验收] 软件复位和模式写同沿时只前者被接受。FIFO 指针/有效态及
        // ADDR_ASCENSION 的绑定不应随配置重置，300 µs 守卫完整走一遍。
        @(negedge clk_aon_i);
        sw_reset_req_aon_i = 1'b1;
        mode_req_aon_i = STANDBY;
        mode_req_valid_aon_i = 1'b1;
        #1;
        check(sw_reset_accept_aon_o && !mode_req_accept_aon_o, "software reset priority");
        tick_aon(1);
        sw_reset_req_aon_i = 1'b0;
        mode_req_valid_aon_i = 1'b0;
        check(!cfg_init_done_aon_o && !core_reset_no && fifo_reset_no && cfg_hw_reset_no
              && !mock_cfg_q && mock_addr_asc_q && mock_fifo_valid_q
              && operating_mode_aon_o == NORMAL, "software reset affected retained state");
        tick_aon(2_999);
        check(!cfg_init_done_aon_o, "software reset 300 us guard early");
        tick_aon(1);
        check(cfg_init_done_aon_o, "software reset 300 us guard late");
        tick_aon(12);
        can_read_fifo("preserved FIFO readable after software reset");
        check(conversion_allow_aon_o, "conversion must resume after software initialization");
        $display("PASS 06 software reset priority and retained FIFO and address bit");

        // [验收] I/O 欠压与硬 POR 均试异步拉低。后者必须清掉配置和记录；在
        // 目的时钟停着时，电源已经恢复也不得把该域提前释放。
        io_clock_on = 1'b0;
        #400;
        @(negedge clk_aon_i);
        #9;
        iovdd_ok_async_i = 1'b0;
        #1;
        check(!io_reset_no && isolate_io_o && !cfg_full_access_allow_aon_o
              && cfg_control_write_allow_aon_o && !fifo_read_allow_aon_o && conversion_allow_aon_o,
              "IO brownout with no IO clock");
        iovdd_ok_async_i = 1'b1;
        tick_aon(10);
        check(!io_reset_no && isolate_io_o, "IO released while destination clock stopped");
        io_clock_on = 1'b1;
        tick_aon(10);
        can_read_fifo("IO PG recovery must keep FIFO data");

        capture_markers();
        core_clock_on = 1'b0;
        #310;
        @(negedge clk_aon_i);
        #7;
        por_d_ok_async_i = 1'b0;
        #1;
        check(!core_reset_no && !fifo_reset_no && !cfg_hw_reset_no && !ana_reset_no
              && isolate_io_o && !conversion_allow_aon_o && !mock_fifo_valid_q
              && !mock_cfg_q && !mock_addr_asc_q, "core POR did not reset all hardware state");
        por_d_ok_async_i = 1'b1;
        tick_aon(3_012); // AON 有充足时钟，核心域仍无目的时钟沿
        check(cfg_init_done_aon_o && !fifo_reset_no && !core_reset_no
              && !conversion_allow_aon_o, "core released while destination clock stopped");
        core_clock_on = 1'b1;
        @(posedge clk_core_i);
        #1;
        check(!fifo_reset_no && !core_reset_no, "core reset synchronizer released on first edge");
        @(posedge clk_core_i);
        #1;
        check(fifo_reset_no && core_reset_no && !mock_fifo_valid_q, "core reset release or FIFO defaults");
        tick_aon(5);
        check(conversion_allow_aon_o, "core restart does not return to normal");
        $display("PASS 07 IO/core brownout with stopped destination clocks");

        // [验收] 软件复位可从睡眠发起唤醒。恢复途中再次软件复位后，原来
        // 的睡眠来源和最长守卫仍保持，旧 done 不能让两段初始化和守卫重叠。
        capture_markers();
        mode_write(SLEEP, 1'b1);
        software_reset();
        check(wake_busy_aon_o && operating_mode_aon_o == NORMAL && !cfg_init_done_aon_o
              && mock_fifo_valid_q && !mock_cfg_q && mock_addr_asc_q, "sleep software wake boundaries");
        tick_aon(2_999);
        check(!cfg_init_done_aon_o && !wake_recover_req_aon_o && !conversion_allow_aon_o,
              "sleep software wake initialization bypassed");
        tick_aon(500); // 允许普通配置重新开放，并让第一次长守卫先消耗一段时间
        software_reset();
        check(!cfg_init_done_aon_o && wake_busy_aon_o, "software reset did not restart in-flight wake");
        wake_recover_done_aon_i = 1'b1;
        tick_aon(4_799); // 3000 + 1800 个周期最后一拍前检查，两个等待均不能跳过
        check(!wake_recover_req_aon_o && wake_busy_aon_o && !conversion_allow_aon_o,
              "sleep restart shortened the selected wake guard");
        tick_aon(3); // 超过完整守卫后才验证旧 done 仍被封住
        check(!wake_recover_req_aon_o && !conversion_allow_aon_o, "sleep restart accepted stale done");
        wake_recover_done_aon_i = 1'b0;
        tick_aon(1);
        check(wake_recover_req_aon_o, "sleep restart recovery did not rearm");
        wake_recover_done_aon_i = 1'b1;
        tick_aon(2);
        check(conversion_allow_aon_o && !wake_busy_aon_o, "sleep software wake end");
        can_read_fifo("software wake and wake restart must preserve FIFO");
        $display("PASS 08 software wake from sleep and in-flight restart");

        $display("PASS ad4080 power supervisor directed 8/8");
        $finish;
    end
endmodule
