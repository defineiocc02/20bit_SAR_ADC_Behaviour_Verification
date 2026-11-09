// SPDX-License-Identifier: BSD-3-Clause
// =============================================================================
// 文件名     : task1_rtl_checks_tb.sv
// 模块名     : task1_rtl_checks_tb
// 版本       : v1.0
// 功能       : 模块一调度、DEM 与桥接的逐沿对拍测试台
//
// 设计说明   :
//   * 沿前/沿后两个 #1 分别用于组合守卫与观测；无时钟组合守卫必须在上升沿之前检查。
//   * clk 由手工翻转产生，没有 always 振荡器，避免仿真起始相位歧义。
//   * 计数器只统计覆盖、不参与判错；$fatal 先于完成收据，防止失败时留下假 PASS。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。

// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module task1_rtl_checks_tb;
    // 所有结构体都是 packed，向量文件一条 %h 按字段声明顺序填满整束；增删字段会静默错位，必须与生成器同步改。
    // scheduler_result_t 镜像调度器输出；acq_id/conv_id 是结构体别名，DUT 端口叫 acq_sample_id_o/conv_sample_id_o。
    typedef struct packed {
        // 40 位 ID 每槽 5 位、18 位掩码按物理片编号；位宽与端口必须逐位相等，%h 才不会错位。
        logic [39:0] acq_ids;
        logic [39:0] conv_ids;
        logic [17:0] acq_mask;
        logic [17:0] conv_mask;
        logic acq_valid;
        logic conv_valid;
        logic [31:0] acq_id;
        logic [31:0] conv_id;
        logic accepted;
        logic stall;
        logic seed_error;
        logic [31:0] lfsr_state;
    } scheduler_result_t;

    // scheduler_vector_t 把逐拍激励与整束期望打包，32 位 seed 与 18 位 eligible 按 %h 一次读入。
    typedef struct packed {
        // rst_n/clear/enable/advance/eligible/shuffle 是逐拍激励，seed_load 与 seed 占独立控制沿。
        logic rst_n;
        logic clear;
        logic enable;
        logic advance;
        logic [17:0] eligible;
        logic shuffle;
        logic seed_load;
        logic [31:0] seed;
        scheduler_result_t expected;
    } scheduler_vector_t;

    // dem_result_t 含 valid：Fig.11 在 h=15 且 DEM 开启时判非法，valid=0 时两路掩码应全零。
    typedef struct packed {
        // main_mask 136 位、sub_mask 40 位加 valid 打包，对应一次前缀的完整组合响应。
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic valid;
    } dem_result_t;

    // dem_vector_t 覆盖 9 位码、使能和 h/v 两个 4 位相位；它是纯组合激励，没有时钟字段。
    typedef struct packed {
        logic [8:0] code;
        logic enable;
        logic [3:0] horizontal;
        logic [3:0] vertical;
        dem_result_t expected;
    } dem_vector_t;

    // top_result_t 复用 scheduler_result_t 作 schedule，再叠加 DEM 状态、两组相位、使能和整束掩码。
    typedef struct packed {
        scheduler_result_t schedule;
        logic [31:0] dem_rng_state;
        logic acq_dem_enable;
        logic conv_dem_enable;
        logic [3:0] acq_h_phase;
        logic [3:0] acq_v_phase;
        logic [3:0] conv_h_phase;
        logic [3:0] conv_v_phase;
        dem_result_t masks;
        logic code_error;
    } top_result_t;

    // top_vector_t 比调度向量多出 dem_enable、两个种子和 code_*；expected 排在末尾，读入时先激励后期望。
    typedef struct packed {
        // dem_enable、两个 32 位种子与 code_* 随记录给出，TB 不自行推导期望随机状态。
        logic rst_n;
        logic clear;
        logic enable;
        logic advance;
        logic [17:0] eligible;
        logic shuffle;
        logic dem_enable;
        logic seed_load;
        logic [31:0] slice_seed;
        logic [31:0] dem_seed;
        logic code_valid;
        logic [8:0] code;
        logic [31:0] code_sample_id;
        top_result_t expected;
    } top_vector_t;

    // clk 由各检查任务手工翻转，没有 always 振荡器；一条记录恰好对应一次上升沿／下降沿。
    logic clk = 1'b0;
    // 三组「激励／实得」结构体各自配对，避免三套 DUT 输出串到同一变量上相互覆盖。
    top_vector_t top_vector;
    top_result_t top_actual;
    scheduler_vector_t scheduler_vector;
    scheduler_result_t scheduler_actual;
    dem_vector_t dem_vector;
    dem_result_t dem_actual;

    // 调度器单独例化，便于直接灌入 advance/seed_load 冲突与零种子的非法组合，不经 module1_top 的预筛。
    task1_slice_scheduler u_scheduler (
        // clk 与 scheduler_vector 各字段直接相连；seed_i 只在 seed_load 沿有意义，其余沿被忽略。
        .clk_i(clk),
        .rst_ni(scheduler_vector.rst_n),
        .clear_i(scheduler_vector.clear),
        .enable_i(scheduler_vector.enable),
        .advance_i(scheduler_vector.advance),
        .eligible_i(scheduler_vector.eligible),
        .shuffle_i(scheduler_vector.shuffle),
        // 调度器的 seed_load_i 接原始向量字段，因此冲突与零种子分支在这里是可达的。
        .seed_load_i(scheduler_vector.seed_load),
        .seed_i(scheduler_vector.seed),
        .acq_ids_o(scheduler_actual.acq_ids),
        .conv_ids_o(scheduler_actual.conv_ids),
        .acq_mask_o(scheduler_actual.acq_mask),
        .conv_mask_o(scheduler_actual.conv_mask),
        .acq_valid_o(scheduler_actual.acq_valid),
        .conv_valid_o(scheduler_actual.conv_valid),
        .acq_sample_id_o(scheduler_actual.acq_id),
        .conv_sample_id_o(scheduler_actual.conv_id),
        // slot_accepted_o 与 conv_valid_o 分别接 accepted/conv_valid；新转换组资格要两者同时有效。
        .slot_accepted_o(scheduler_actual.accepted),
        .stall_o(scheduler_actual.stall),
        .seed_error_o(scheduler_actual.seed_error),
        .lfsr_state_o(scheduler_actual.lfsr_state)
    );

    // Fig.11 叶无时钟，dem_vector 一变、#1 后即稳定；这里验的是映射本身，不是同笔冻结。
    fig11_dem u_dem (
        .code_i(dem_vector.code),
        // dem_enable_i 接 dem_vector.enable，h/v 相位接 horizontal/vertical，三者同一拍生效。
        .dem_enable_i(dem_vector.enable),
        .horizontal_phase_i(dem_vector.horizontal),
        .vertical_phase_i(dem_vector.vertical),
        .main_mask_o(dem_actual.main_mask),
        .sub_mask_o(dem_actual.sub_mask),
        .valid_o(dem_actual.valid)
    );

    // 顶层例化把 top_vector 展开到 module1_top；它复用调度器与 Fig.11，但本 TB 三者并行独立例化。
    module1_top u_module1 (
        // clk 与调度器、DEM 共用同一手工时钟，三个组件各自成环、互不推进对方状态。
        .clk_i(clk),
        .rst_ni(top_vector.rst_n),
        .clear_i(top_vector.clear),
        .enable_i(top_vector.enable),
        .advance_i(top_vector.advance),
        .eligible_i(top_vector.eligible),
        .shuffle_i(top_vector.shuffle),
        .dem_enable_i(top_vector.dem_enable),
        .seed_load_i(top_vector.seed_load),
        .slice_seed_i(top_vector.slice_seed),
        .dem_seed_i(top_vector.dem_seed),
        // code_valid_i、code_i 与 code_sample_id_i 逐拍给出，用于制造错 ID 与非法前缀场景。
        .code_valid_i(top_vector.code_valid),
        .code_i(top_vector.code),
        .code_sample_id_i(top_vector.code_sample_id),
        .acq_ids_o(top_actual.schedule.acq_ids),
        .conv_ids_o(top_actual.schedule.conv_ids),
        .acq_mask_o(top_actual.schedule.acq_mask),
        .conv_mask_o(top_actual.schedule.conv_mask),
        .acq_valid_o(top_actual.schedule.acq_valid),
        .conv_valid_o(top_actual.schedule.conv_valid),
        // acq_sample_id_o 落到 schedule.acq_id，端口名与结构体字段名不同，靠端口表逐项对齐。
        .acq_sample_id_o(top_actual.schedule.acq_id),
        .conv_sample_id_o(top_actual.schedule.conv_id),
        .slot_accepted_o(top_actual.schedule.accepted),
        .stall_o(top_actual.schedule.stall),
        .seed_error_o(top_actual.schedule.seed_error),
        .slice_lfsr_state_o(top_actual.schedule.lfsr_state),
        .dem_rng_state_o(top_actual.dem_rng_state),
        .acq_dem_enable_o(top_actual.acq_dem_enable),
        .conv_dem_enable_o(top_actual.conv_dem_enable),
        .acq_h_phase_o(top_actual.acq_h_phase),
        .acq_v_phase_o(top_actual.acq_v_phase),
        .conv_h_phase_o(top_actual.conv_h_phase),
        .conv_v_phase_o(top_actual.conv_v_phase),
        .main_mask_o(top_actual.masks.main_mask),
        .sub_mask_o(top_actual.masks.sub_mask),
        .mask_valid_o(top_actual.masks.valid),
        // code_error_o 收进 top_actual.code_error，与 mask_valid 一起构成资格比较。
        .code_error_o(top_actual.code_error)
    );

    // 顶层计数器按 N+1 发布的事件分类：accepts/conversions/stalls/seed_errors 与两类前缀覆盖。
    int top_cycles;
    // 计数器只统计覆盖分布，不参与通过／失败；真正判错只有各处 $fatal。
    int top_accepts;
    int top_conversions;
    int top_stalls;
    int top_seed_errors;
    int top_active_prefixes;
    int top_bypass_prefixes;
    int top_code_errors;

    // 调度器与 DEM 各自一组计数器，分开统计才能定位失败落在哪一个组件。
    int scheduler_cycles;
    int scheduler_accepts;
    // scheduler_conversions 只数 accepted && conv_valid 的拍，用来确认采集组确实晋升成转换组。
    int scheduler_conversions;
    int scheduler_stalls;
    int scheduler_seed_errors;
    // dem_active/dem_bypass/dem_invalid 把每个 DEM 向量拍分成三类，三类之和应等于用例数。
    int dem_active;
    int dem_bypass;
    int dem_invalid;

    // 调度检查有时钟：驱动 → clk=1 → #1 → 比较 → clk=0，两个 #1 分别负责建立与读出。
// =========================================================================
// 2. 自检任务组（调度 / DEM / 顶层）
// =========================================================================
    task automatic check_scheduler(input string path);
        // record 是读入缓冲，先读局部变量再赋给激励寄存器，$fscanf 不直接改写正在驱动 DUT 的信号。
        scheduler_vector_t record;
        int file_id;
        int scanned;
        // 打开失败即 $fatal，不回退内置用例，避免「没跑到数据」被误当成通过。
        file_id = $fopen(path, "r");
        // file_id==0 视作环境错误而非 DUT 错误，与缺 plusargs 一样直接 $fatal。
        if (file_id == 0) $fatal(1, "cannot open scheduler vectors: %s", path);
        // %h 一次读满整条 packed 记录；scanned!=1 结束循环，残留非法字符由后面的 $feof 兜住。
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            // 先驱动激励再给沿前 #1，让 available_mask/rank 组合网络稳定，然后才制造上升沿。
            scheduler_vector = record;
            // 沿前 #1 给组合逻辑建立时间，沿后 #1 等时序输出稳定；两个 #1 缺一都会采到过渡值。
            #1;
            // 手工上升沿触发调度器；N 沿判定、N+1 发布的响应在沿后才能读到。
            clk = 1'b1;
            #1;
            // 用 !== 比较整束 packed 结构体，X/Z 也算不等价；失败信息带全部激励便于定位具体向量。
            if (scheduler_actual !== scheduler_vector.expected) begin
                // 失败信息列出 rst_n/clear/enable/advance/eligible/shuffle/seed 等全部激励，便于复现该拍。
                $fatal(1, "scheduler cycle=%0d rst_n=%b clear=%b enable=%b advance=%b eligible=%h shuffle=%b seed_load=%b seed=%h expected=%h got=%h",
                       scheduler_cycles, scheduler_vector.rst_n, scheduler_vector.clear,
                       scheduler_vector.enable, scheduler_vector.advance,
                       scheduler_vector.eligible, scheduler_vector.shuffle,
                       scheduler_vector.seed_load, scheduler_vector.seed,
                       scheduler_vector.expected, scheduler_actual);
            end
            // 四类计数用 int'(...) 把逻辑值转成 0/1 累加，accepts/conversions/stalls/seed_errors 各不相混。
            scheduler_cycles++;
            scheduler_accepts += int'(scheduler_actual.accepted);
            scheduler_conversions += int'(scheduler_actual.accepted && scheduler_actual.conv_valid);
            scheduler_stalls += int'(scheduler_actual.stall);
            scheduler_seed_errors += int'(scheduler_actual.seed_error);
            // 下降沿收尾一个 cycle，clk 不回 0 会让下一条记录缺上升沿。
            clk = 1'b0;
            // 末尾再读一条推进文件指针，与 while 的 scanned==1 形成标准读数循环。
            scanned = $fscanf(file_id, "%h", record);
        end
        // 必须干净停在 EOF；文件截断或混入非法字符时静默结束会漏掉大批用例。
        if (!$feof(file_id)) $fatal(1, "malformed scheduler vector file");
        $fclose(file_id);
    endtask

    // DEM 检查无时钟：读一条、驱动、#1 后直接四态比较，验的是组合映射而非时序。
    task automatic check_dem(input string path);
        // DEM 记录读入局部变量 record，再赋给 dem_vector，避免 $fscanf 直接改写驱动信号。
        dem_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        // 打开 DEM 向量文件失败即致命，与另两套检查同一处理口径。
        if (file_id == 0) $fatal(1, "cannot open DEM vectors: %s", path);
        // %h 一次读满 dem_vector_t：code/enable/h/v 加整束 expected，一条记录一个组合拍。
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            dem_vector = record;
            #1;
            // !== 一次覆盖 main_mask、sub_mask 与 valid；valid=0 的非法相位也要求掩码全零。
            if (dem_actual !== dem_vector.expected) begin
                // 失败信息带 code/enable/h/v 与整束期望／实得，便于定位具体相位组合。
                $fatal(1, "DEM code=%0d enable=%b h=%0d v=%0d expected=%h got=%h",
                       dem_vector.code, dem_vector.enable, dem_vector.horizontal,
                       dem_vector.vertical, dem_vector.expected, dem_actual);
            end
            // 按 valid 与 enable 把每拍归入 invalid/active/bypass，用来确认三类相位都被覆盖。
            if (!dem_actual.valid) dem_invalid++;
            else if (dem_vector.enable) dem_active++;
            else dem_bypass++;
            scanned = $fscanf(file_id, "%h", record);
        end
        // DEM 向量文件同样必须恰好停在 EOF。
        if (!$feof(file_id)) $fatal(1, "malformed DEM vector file");
        $fclose(file_id);
    endtask

    // 顶层检查与调度检查同构，但多一道无时钟的组合守卫：非活动拍必须先把掩码和错误清零。
    task automatic check_top(input string path);
        // 记录先读进局部 record，再赋给 top_vector；三套检查都用同一套缓冲写法。
        top_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        // fopen 返回 0 即致命，与另两套检查同一处理口径；三套向量路径都由 plusargs 注入。
        if (file_id == 0) $fatal(1, "cannot open top vectors: %s", path);
        // 每条记录是一个完整 cycle：激励、期望束一次性 %h 读入。
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            top_vector = record;
            // 先给 #1 让复位／清空／禁用的组合清零生效，再在时钟沿之前检查守卫。
            #1;
            // 非活动拍守卫只查 masks 与 code_error；这项验的是组合 active 门控，不是时序复位分支。
            if (!top_vector.rst_n || top_vector.clear || !top_vector.enable) begin
                if (top_actual.masks !== '0 || top_actual.code_error) begin
                    $fatal(1, "top cycle=%0d failed inactive combinational guard", top_cycles);
                end
            end
            // 手工上升沿：module1_top 在 N 沿判定、N+1 发布，故比较放在沿后 #1。
            clk = 1'b1;
            #1;
            // top_actual 与 expected 同类型，整束 !== 一次覆盖组、ID、配置、相位、掩码和资格。
            if (top_actual !== top_vector.expected) begin
                // 失败信息带 cycle/code/code_id 与整束十六进制，便于定位错 ID 或错配置那一拍。
                $fatal(1, "top cycle=%0d code=%0d code_id=%0d expected=%h got=%h",
                       top_cycles, top_vector.code, top_vector.code_sample_id,
                       top_vector.expected, top_actual);
            end
            // 顶层事件计数：accepts/conversions/stalls/seed_errors 与两类有效前缀覆盖分别累加。
            top_cycles++;
            top_accepts += int'(top_actual.schedule.accepted);
            top_conversions += int'(top_actual.schedule.accepted && top_actual.schedule.conv_valid);
            top_stalls += int'(top_actual.schedule.stall);
            top_seed_errors += int'(top_actual.schedule.seed_error);
            // active/bypass 前缀按 conv_dem_enable 区分，两者之和应等于 mask_valid 的前缀数。
            top_active_prefixes += int'(top_actual.masks.valid && top_actual.conv_dem_enable);
            top_bypass_prefixes += int'(top_actual.masks.valid && !top_actual.conv_dem_enable);
            // code_error 每沿计数，用来发现错 ID 或转换组缺失这类只在资格位暴露的问题。
            top_code_errors += int'(top_actual.code_error);
            // 下降沿收尾；clk 不回 0 会让下一条记录缺上升沿。
            clk = 1'b0;
            scanned = $fscanf(file_id, "%h", record);
        end
        // 顶层向量文件也必须干净停在 EOF。
        if (!$feof(file_id)) $fatal(1, "malformed top vector file");
        $fclose(file_id);
    endtask

    // 三套向量路径全部由 plusargs 提供，缺失就直接致命，不猜默认路径以免跑错数据集。
    initial begin : run_checks
        string scheduler_path;
        string dem_path;
        string top_path;
        // 三个 plusargs 用 || 串起来，任一未给出都会触发同一条 $fatal。
        if (!$value$plusargs("scheduler_vectors=%s", scheduler_path) ||
            !$value$plusargs("dem_vectors=%s", dem_path) ||
            !$value$plusargs("top_vectors=%s", top_path)) begin
            $fatal(1, "scheduler_vectors, dem_vectors, and top_vectors plusargs are required");
        end
        // 按组件顺序自检：先调度、再 DEM、最后集成顶层，失败时便于逐层缩小范围。
        check_scheduler(scheduler_path);
        check_dem(dem_path);
        check_top(top_path);
        // 完成收据一次打出所有计数器供 runner 核对；任一 $fatal 先触发就不会打印这一行。
        $display("TASK1_RTL_CHECKS_COMPLETE scheduler_cycles=%0d scheduler_accepts=%0d scheduler_conversions=%0d scheduler_stalls=%0d scheduler_seed_errors=%0d dem_active=%0d dem_bypass=%0d dem_invalid=%0d top_cycles=%0d top_accepts=%0d top_conversions=%0d top_stalls=%0d top_seed_errors=%0d top_active_prefixes=%0d top_bypass_prefixes=%0d top_code_errors=%0d",
                 scheduler_cycles, scheduler_accepts, scheduler_conversions, scheduler_stalls,
                 scheduler_seed_errors, dem_active, dem_bypass, dem_invalid,
                 top_cycles, top_accepts, top_conversions, top_stalls, top_seed_errors,
                 top_active_prefixes, top_bypass_prefixes, top_code_errors);
        // $finish 正常终止；若前面有 $fatal，这一行与完成收据都不会执行。
        $finish;
    end
endmodule
