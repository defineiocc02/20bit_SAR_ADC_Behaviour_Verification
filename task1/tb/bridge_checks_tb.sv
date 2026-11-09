// SPDX-License-Identifier: BSD-3-Clause
// =============================================================================
// 文件名     : bridge_checks_tb.sv
// 模块名     : bridge_checks_tb
// 版本       : v1.0
// 功能       : Fig.21 桥接候选的逐沿向量对拍测试台
//
// 设计说明   :
//   * packed struct 与 %h 单条记录的字段顺序是契约，增删字段会静默错位。
//   * acq_id 与 acq_sample_id_o 的命名不一致靠端口表对齐；用 !== 做四态比较。
//   * 沿前/沿后两个 #1 分别用于组合守卫与观测；$feof 兜住截断或非法字符。
//   * 计数器只统计覆盖、不参与判错；$fatal 必须早于完成收据输出。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。

// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module task1_bridge_checks_tb;
    // 所有结构体都用 packed，向量文件里一条 %h 按字段声明顺序逐位填入；增删字段会静默错位，改结构体必须同步改生成器。
    // scheduler_result_t 逐字段镜像调度器输出；acq_id/conv_id 是结构体别名，DUT 端口叫 acq_sample_id_o/conv_sample_id_o。
    typedef struct packed {
        // 40 位 ID 每槽 5 位、18 位掩码按物理片编号；位宽与 DUT 端口必须逐位相等，%h 才不会错位。
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

    // dem_result_t 含 valid 位，对应 Fig.11 映射的相位合法性；桥接叶没有 valid 输出，用的是下面的 leaf_result_t。
    typedef struct packed {
        // main_mask 136 位、sub_mask 40 位加 valid 打包，对应一次前缀的完整组合响应。
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic valid;
    } dem_result_t;

    // top_result_t 是 module1_top 输出束的镜像，不含桥接专属信号；schedule 复用上面那套结构体以免字段顺序分叉。
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

    // top_vector_t 把逐拍激励与整束期望打包成一条记录；expected 排在最后，读入时先激励后期望。
    typedef struct packed {
        // rst_n/clear/enable/advance/eligible/shuffle 是逐拍激励，dem_enable 与两个种子在请求沿绑定。
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
        // code_valid、9 位 code 与 32 位同笔 ID 逐拍给出，TB 不自行推导期望随机状态。
        logic code_valid;
        logic [8:0] code;
        logic [31:0] code_sample_id;
        top_result_t expected;
    } top_vector_t;

    // bridge_vector_t 在 top_vector_t 之上追加桥接使能和三项期望，字段顺序必须与 check_top 里拼接的顺序一致。
    typedef struct packed {
        top_vector_t base;
        logic enable_bridge;
        logic expected_acq_bridge;
        logic expected_conv_bridge;
        logic [31:0] expected_correction;
    } bridge_vector_t;

    // leaf_result_t 只有掩码与补偿：fig21_bridge 是纯组合叶，没有时钟、复位和 valid 端口。
    typedef struct packed {
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic [31:0] correction;
    } leaf_result_t;

    // leaf_vector_t 覆盖 code(9)、R(4)、v(3) 三维组合，期望值由独立参考逐组算出，TB 不重复推导物理电荷。
    typedef struct packed {
        logic [8:0] code;
        logic [3:0] reserved_column;
        logic [2:0] vertical_slot;
        leaf_result_t expected;
    } leaf_vector_t;

    // clk 由 initial 块手工翻转，没有独立振荡 always；一条向量记录恰好对应一个上升沿加一个下降沿。
    logic clk = 1'b0;
    // top_vector/leaf_vector 是驱动 DUT 的激励寄存器，*_actual 是采样回来的结果，两者共用同一套 packed 定义。
    bridge_vector_t top_vector;
    top_result_t top_actual;
    // acq_bridge、conv_bridge 与 correction_q32 不属于 top_result_t，单独声明并在比较时拼到末尾。
    logic acq_bridge, conv_bridge;
    logic [31:0] correction_q32;
    leaf_vector_t leaf_vector;
    leaf_result_t leaf_actual;

    // 叶例化无时钟：leaf_vector 一变，#1 后 main/sub/correction 就必须稳定，可直接做四态比较。
    fig21_bridge u_leaf (
        // code、保留列 R、纵向起点 v 都取同一条 leaf_vector 记录的字段，不经过冻结，验的是组合映射本身。
        .code_i(leaf_vector.code),
        .reserved_column_i(leaf_vector.reserved_column),
        .vertical_slot_i(leaf_vector.vertical_slot),
        .main_mask_o(leaf_actual.main_mask),
        .sub_mask_o(leaf_actual.sub_mask),
        // 叶的 correction_q32_o 接到 leaf_actual.correction，使 !== 一次覆盖掩码与补偿。
        .correction_q32_o(leaf_actual.correction)
    );

    // 顶层例化把 top_vector.base 各字段逐一展开；bridge_enable_i 来自扩展字段，其余沿用基础向量。
    module1_bridge_top u_module1 (
        .clk_i(clk),
        // clk 由手工翻转提供；rst_ni/clear_i/enable_i 取 base 字段，保证非活动拍的组合守卫可测。
        .rst_ni(top_vector.base.rst_n),
        .clear_i(top_vector.base.clear),
        .enable_i(top_vector.base.enable),
        .advance_i(top_vector.base.advance),
        .eligible_i(top_vector.base.eligible),
        .shuffle_i(top_vector.base.shuffle),
        .dem_enable_i(top_vector.base.dem_enable),
        // bridge_enable_i 来自 bridge_vector_t 的扩展字段，是顶层相对基础向量唯一的额外输入。
        .bridge_enable_i(top_vector.enable_bridge),
        .seed_load_i(top_vector.base.seed_load),
        .slice_seed_i(top_vector.base.slice_seed),
        .dem_seed_i(top_vector.base.dem_seed),
        .code_valid_i(top_vector.base.code_valid),
        .code_i(top_vector.base.code),
        .code_sample_id_i(top_vector.base.code_sample_id),
        // 输出全部收进 top_actual，其中 acq_sample_id_o 落到 schedule.acq_id，端口名与字段名不同需靠端口表对齐。
        .acq_ids_o(top_actual.schedule.acq_ids),
        .conv_ids_o(top_actual.schedule.conv_ids),
        .acq_mask_o(top_actual.schedule.acq_mask),
        .conv_mask_o(top_actual.schedule.conv_mask),
        .acq_valid_o(top_actual.schedule.acq_valid),
        .conv_valid_o(top_actual.schedule.conv_valid),
        .acq_sample_id_o(top_actual.schedule.acq_id),
        .conv_sample_id_o(top_actual.schedule.conv_id),
        .slot_accepted_o(top_actual.schedule.accepted),
        .stall_o(top_actual.schedule.stall),
        .seed_error_o(top_actual.schedule.seed_error),
        .slice_lfsr_state_o(top_actual.schedule.lfsr_state),
        .dem_rng_state_o(top_actual.dem_rng_state),
        .acq_dem_enable_o(top_actual.acq_dem_enable),
        .conv_dem_enable_o(top_actual.conv_dem_enable),
        // 两个桥接使能位不进 top_actual，单独接出来；它们与 correction_q32 一起构成桥接专属的三项期望。
        .acq_bridge_enable_o(acq_bridge),
        .conv_bridge_enable_o(conv_bridge),
        .acq_h_phase_o(top_actual.acq_h_phase),
        .acq_v_phase_o(top_actual.acq_v_phase),
        .conv_h_phase_o(top_actual.conv_h_phase),
        .conv_v_phase_o(top_actual.conv_v_phase),
        .main_mask_o(top_actual.masks.main_mask),
        .sub_mask_o(top_actual.masks.sub_mask),
        // correction_q32 与 mask_valid 分开接：valid 归 top_actual.masks.valid，补偿单独比较，不挤进结构体位宽。
        .correction_q32_o(correction_q32),
        .mask_valid_o(top_actual.masks.valid),
        .code_error_o(top_actual.code_error)
    );

    // 以下计数器只统计覆盖分布，不参与通过／失败判定；真正判错只有各处 $fatal。
    int leaf_cases;
    int leaf_nonzero_corrections;
    // 顶层计数器按事件分类：accepts/stalls/seed_errors/code_errors 对应 N+1 发布的四类响应。
    int top_cycles;
    int top_accepts;
    int top_stalls;
    int top_seed_errors;
    int top_code_errors;
    int top_bridge_prefixes;
    int top_fig11_dem_prefixes;
    int top_fig11_bypass_prefixes;
    int top_nonzero_corrections;

    // 叶检查纯组合：读一条记录、驱动、等 #1、整束 !== 比较，不需要时钟沿。
// =========================================================================
// 2. 自检任务组（check_leaf / check_top）
// =========================================================================
    task automatic check_leaf(input string path);
        // record 是读入缓冲，先读到局部变量再赋给激励寄存器，避免 $fscanf 直接改写正在驱动 DUT 的信号。
        leaf_vector_t record;
        int file_id;
        int scanned;
        // fopen 返回 0 即视为缺文件直接致命；不回退到内置用例，避免「没跑到数据」被当成通过。
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open bridge leaf vectors: %s", path);
        // %h 一次读满整条 packed 记录；scanned != 1 即结束循环，残留非十六进制内容交给后面的 $feof 兜住。
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            // 先写激励寄存器再等 #1，让 u_leaf 的组合网络稳定；顺序颠倒会采到上一笔的掩码。
            leaf_vector = record;
            #1;
            // 用 !== 而非 !=：X/Z 传播也算不等价，组合叶一旦有未驱动位不会被静默放过。
            if (leaf_actual !== leaf_vector.expected) begin
                $fatal(1, "bridge leaf code=%0d R=%0d v=%0d expected=%h got=%h",
                       leaf_vector.code, leaf_vector.reserved_column, leaf_vector.vertical_slot,
                       leaf_vector.expected, leaf_actual);
            end
            // leaf_cases 只数成功比较的记录数；leaf_nonzero_corrections 记录 code[3:2] 非零、补偿应非零的档位覆盖。
            leaf_cases++;
            leaf_nonzero_corrections += int'(leaf_actual.correction != '0);
            // 循环末尾再读下一条，保证 while 条件与文件推进同步，不会重复比较最后一条记录。
            scanned = $fscanf(file_id, "%h", record);
        end
        // 正常退出必须恰好停在 EOF；文件被截断或含非法字符时静默结束会漏掉大批用例。
        if (!$feof(file_id)) $fatal(1, "malformed bridge leaf vector file");
        $fclose(file_id);
    endtask

    // 顶层检查需要一个显式上升沿：驱动 → 无沿的组合守卫 → clk=1 → 比较 → clk=0。
    task automatic check_top(input string path);
        bridge_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open bridge top vectors: %s", path);
        // 每条记录是一条完整 cycle：激励、期望基础束、三项桥接期望一次性 %h 读入。
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            // 先给 #1 让复位／清空／禁用的组合清零生效，再在无时钟时查守卫。
            top_vector = record;
            #1;
            // 非活动拍必须在时钟沿之前就看到掩码、code_error 和补偿全零；这项查的是组合门控而非时序复位。
            if (!top_vector.base.rst_n || top_vector.base.clear || !top_vector.base.enable) begin
                if (top_actual.masks !== '0 || top_actual.code_error || correction_q32 !== '0) begin
                    $fatal(1, "bridge top cycle=%0d failed inactive guard", top_cycles);
                end
            end
            // 手工给上升沿；DUT 在 N 沿判定、N+1 发布，故比较放在沿后的 #1。
            clk = 1'b1;
            #1;
            // 把 {top_actual, acq_bridge, conv_bridge, correction_q32} 与期望按同一顺序拼接，顺序错位会把不同字段比到一起。
            if ({top_actual, acq_bridge, conv_bridge, correction_q32} !==
                {top_vector.base.expected, top_vector.expected_acq_bridge,
                 top_vector.expected_conv_bridge, top_vector.expected_correction}) begin
                $fatal(1, "bridge top cycle=%0d code=%0d code_id=%0d expected=%h/%b/%b/%h got=%h/%b/%b/%h",
                       top_cycles, top_vector.base.code, top_vector.base.code_sample_id,
                       top_vector.base.expected, top_vector.expected_acq_bridge,
                       top_vector.expected_conv_bridge, top_vector.expected_correction,
                       top_actual, acq_bridge, conv_bridge, correction_q32);
            end
            // 有效前缀按 conv_bridge 与 conv_dem_enable 分成桥接、Fig.11-DEM、Fig.11-旁路三类，互斥且合计应等于有效前缀数。
            top_cycles++;
            top_accepts += int'(top_actual.schedule.accepted);
            top_stalls += int'(top_actual.schedule.stall);
            top_seed_errors += int'(top_actual.schedule.seed_error);
            top_code_errors += int'(top_actual.code_error);
            top_bridge_prefixes += int'(top_actual.masks.valid && conv_bridge);
            top_fig11_dem_prefixes += int'(top_actual.masks.valid && !conv_bridge
                                           && top_actual.conv_dem_enable);
            top_fig11_bypass_prefixes += int'(top_actual.masks.valid && !conv_bridge
                                              && !top_actual.conv_dem_enable);
            // top_nonzero_corrections 数补偿非零的前缀，用来发现漏加／双加补偿这类只在数值上暴露的错误。
            top_nonzero_corrections += int'(correction_q32 != '0);
            // 下降沿收尾一条 cycle；clk 不回到 0 会让下一条记录的上升沿缺失。
            clk = 1'b0;
            scanned = $fscanf(file_id, "%h", record);
        end
        // 与叶检查同理，顶层向量文件也必须干净地停在 EOF。
        if (!$feof(file_id)) $fatal(1, "malformed bridge top vector file");
        $fclose(file_id);
    endtask

    // 两个向量路径由 plusargs 提供，缺失就直接致命，不猜默认路径以免跑错数据集。
    initial begin : run_checks
        string leaf_path;
        string top_path;
        if (!$value$plusargs("leaf_vectors=%s", leaf_path) ||
            !$value$plusargs("top_vectors=%s", top_path)) begin
            $fatal(1, "leaf_vectors and top_vectors plusargs are required");
        end
        // 先跑纯组合叶再跑时序顶层：叶失败时不会浪费一轮顶层仿真，也便于先定位映射错误。
        check_leaf(leaf_path);
        check_top(top_path);
        // 完成收据把各计数器一次打出供 runner 核对；任一 $fatal 先触发就不会打印完成行。
        $display("TASK1_BRIDGE_CHECKS_COMPLETE leaf_cases=%0d leaf_nonzero_corrections=%0d top_cycles=%0d top_accepts=%0d top_stalls=%0d top_seed_errors=%0d top_code_errors=%0d top_bridge_prefixes=%0d top_fig11_dem_prefixes=%0d top_fig11_bypass_prefixes=%0d top_nonzero_corrections=%0d",
                 leaf_cases, leaf_nonzero_corrections, top_cycles, top_accepts, top_stalls,
                 top_seed_errors, top_code_errors, top_bridge_prefixes, top_fig11_dem_prefixes,
                 top_fig11_bypass_prefixes, top_nonzero_corrections);
        // $finish 正常终止仿真；若前面有 $fatal，这一行和完成收据都不会执行。
        $finish;
    end
endmodule
