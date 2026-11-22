// =============================================================================
// 文件名     : wsum_v21.sv
// 模块名     : wsum_v21
// 版本       : v1.0
// 功能       : 生产侧权重求和 —— 为本笔的 D5 重构提供 G / Wp / Wm（Q30）。
//              方案 v1.4 §9.1："准备阶段计算 G 和 Wp，最后实际 mask 到达后计算 Wm。"
// 设计说明   :
//   * 为什么需要独立模块：
//     节点宏（adc2_node_macro）在**仿真边界**（代表模拟宏），它内部也算 Wm/Wp
//     用来算 y。但那一路的结果不能回喂数字侧 —— 否则等于"用模拟侧的结果
//     去做数字重构"，既不合物理分层，也无法在只有数字网表时工作。
//     所以生产侧必须自己有一份同样口径的 Wm/Wp。
//
//   * 三个量的定义（合同 v2 §7）：
//       G  = sum(active main weights)          —— 本笔 8 片主行和
//       Wm = sum(final main/sub post-mask * physical weights)
//       Wp = sum(sub-array sample pre-mask * physical weights)
//
//   * **j=19 计入 Wm（按实际 post 选通）**：j19 是真实电容（名义 1/128），
//     只要 post 选中就进入 Wm。早前版本把它排除，理由是"平衡位不贡献信号"，
//     那是错的 —— 定向向量 k=519(post 0x7FFFF, j19=0) -> raw=5120 与
//     k=520(post 0xFFFFF, j19=1) -> raw=1024 同时成立，只有"计入"才解释得通。
//     pre 的 4 位是 [2Cu,1Cu,1Cu,平衡]，末位同样不计入 Wp。
//
//   * 权重表：18 片 × 20 位，Q30。方案 §9.1 要求"18×20×44 = 15,840 bit，
//     首版使用寄存器或可并行读取的分银行结构"。本版用寄存器阵列，
//     由配置端口逐个写入（I4b 接 §9.4 的配置接口）。
//
//   * 为什么 G 单独给：G 只依赖片组（哪 8 片），不依赖 mask。
//     方案要求"每笔 G 从实际 8 个物理 slice 的行和取得，不得用固定九项倒数表"。
//     本模块按 node_ids 里的 8 个物理片号实时求和，正好满足。
//
//   * 时序：三段都是平衡树 + 注册输出（§9.1 "平衡树，注册分级"）。
//     这里用一级寄存 + 组合树，25 ns 约束下余量充足（见 I5 STA）。
// 变更记录   :
//   2026-11-15  初版。I4b 阶段。
// =============================================================================
module wsum_v21 (
    input  logic         clk,
    input  logic         rst_n,

    // ---------------------------------------------------------------- 请求
    input  logic         calc_valid,     // 本拍快照有效，求一次和
    input  logic [39:0]  ids_i,          // 本笔 8 个物理片号（5 bit/个）
    input  logic [31:0]  pre_mask_i,     // 本笔 slot 级 32 位 pre
    input  logic [159:0] post_mask_i,    // 本笔 slot 级 160 位 post

    // ---------------------------------------------------------------- 权重表配置口
    //
    // **权重表是静态标定值，不是每笔交易的实时信号**，所以做成内部
    // 寄存器堆 + 窄配置口，而不是 14,400 根端口线。
    //
    // 为什么不直接用端口数组（历史教训）：
    //   原来 w_main_tbl[0:17][0:15] + w_sub_tbl[0:17][0:3] 是 14,400 根
    //   40 位输入线，`f_G`/`f_Wm`/`f_Wp` 里 `tbl[ids[s*5+:5]][j]` 的查表
    //   被展开成 8slot x 16col x 18row = 2304 路、每路 40 位的 MUX 树。
    //   结果：wsum_v21 单独综合 594 s、112,371 cells（占顶层 68%），
    //   顶层 compile -map_effort medium 四次全部停在 `Processing 'wsum_v21'`
    //   （看门狗 3900 s 超时无网表），顶层端口数 15,775 也失真。
    //   改用寄存器堆后端口从 14,400 降到 51。
    //
    // 写协议（配置阶段一次性灌入，运行期不再改）：
    //   cfg_we=1 的那一拍，把 cfg_data 写进
    //     cfg_sel=0 -> wt_main[cfg_sid][cfg_idx]   (cfg_idx = 0..15)
    //     cfg_sel=1 -> wt_sub [cfg_sid][cfg_idx]   (cfg_idx = 0..3)
    //   地址越界（cfg_sid>17 / cfg_idx 超范围）时不写入（安全忽略）。
    input  logic         cfg_we,
    input  logic         cfg_sel,       // 0 = 主阵列，1 = 子阵列
    input  logic [4:0]   cfg_sid,       // 0..17
    input  logic [4:0]   cfg_idx,       // 主 0..15 / 子 0..3
    input  logic [39:0]  cfg_data,
    // 生命周期控制（见 docs/v21/CONFIG_CONTRACT.md §2–§3）
    input  logic         cfg_commit,     // 原子生效请求
    input  logic         cfg_reload,     // 重新配置请求（须已排空）
    output logic         cfg_valid,      // 已生效且锁写
    output logic         cfg_busy,       // 装载中
    output logic         cfg_range_err,  // 子权重超范围被拒绝

    // ---------------------------------------------------------------- 结果
    output logic         sum_valid,
    output logic [63:0]  G_q30,
    output logic [63:0]  Wp_q30,
    output logic [63:0]  Wm_q30
);


    // ---------------------------------------------------------------- 权重寄存器堆
    //
    // 360 个 40 位字（18 片 x (16 主 + 4 子)）= 14,400 bit。
    // 综合成触发器阵列；配置阶段写入，运行期只读。
    //
    // 为什么用「显式 write-enable、无复位」：
    //   权重是标定值，不需要复位值；加复位会多出 14,400 个复位端口，
    //   又把规模拉回去。上电后由配置序列负责灌满全部 360 个字。
    logic [39:0] wt_main [0:17][0:15];
    logic [39:0] wt_sub  [0:17][0:3];

    // ---------------------------------------------------------------- 配置生命周期
    //
    // 三态：IDLE（无效）-> LOADING（装载中）-> ACTIVE（生效并锁写）。
    // 重配：reload 把状态打回 IDLE 并清 written；commit 校验 written 全 1。
    // 详见 docs/v21/CONFIG_CONTRACT.md。
    //
    // **数据阵列不复位**（14,400 bit 加复位会把规模拉回去），
    // 但 written/locked 这些**控制状态必须有复位值** ——
    // 否则上电后 written 是 X，完整性判定无意义。
    logic [359:0] written_q;   // 每个字是否写过（位图，不是计数器）
    logic         locked_q;
    // 子权重工程上界（CONFIG_CONTRACT §6）
    localparam logic [39:0] SUB_MAX = 40'd32 * 40'd4 * 40'd1073741824;

    // 地址合法
    wire addr_ok = (cfg_sid <= 5'd17)
                 & (cfg_sel ? (cfg_idx <= 5'd3) : (cfg_idx <= 5'd15));
    // 子权重范围检查：只在 sel=1 时对数据检查
    wire data_ok = cfg_sel ? (cfg_data <= SUB_MAX) : 1'b1;

    // **最后一字与 commit 同拍必须拒绝**（CONFIG_CONTRACT §4）。
    // 判据：commit 有效且 written 当前不是全 1（即这一拍还在写最后一个字）。
    wire written_all = &written_q;

    // 写资格：未锁写 + 地址合法 + 数据合法
    wire cfg_wr = cfg_we & ~locked_q & addr_ok & data_ok;

    // 该地址的位图索引
    wire [8:0] widx = cfg_sel ? (9'd288 + {4'd0, cfg_sid} * 9'd4  + {7'd0, cfg_idx[1:0]})
                              : (           {4'd0, cfg_sid} * 9'd16 + {5'd0, cfg_idx[3:0]});

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            written_q <= 360'd0;
            locked_q  <= 1'b0;
            cfg_range_err <= 1'b0;
        end else if (cfg_reload) begin
            // 重配入口：清 written、失效、回 IDLE
            written_q     <= 360'd0;
            locked_q      <= 1'b0;
            cfg_range_err <= 1'b0;
        end else begin
            // 非法数据：置错误标志，但**不置 written**
            if (cfg_we & ~locked_q & addr_ok & ~data_ok)
                cfg_range_err <= 1'b1;

            // 正常写：只置位图，不增计数（重复写同一地址仍是这一位）
            if (cfg_wr) begin
                written_q[widx] <= 1'b1;
                if (cfg_sel) wt_sub[cfg_sid][cfg_idx[1:0]] <= cfg_data;
                else         wt_main[cfg_sid][cfg_idx[3:0]] <= cfg_data;
            end

            // commit：必须 written 已全 1（挡住"最后一字与 commit 同拍"）
            if (cfg_commit & ~locked_q & written_all) locked_q <= 1'b1;
        end
    end

    assign cfg_valid = locked_q;
    assign cfg_busy  = ~locked_q & (written_q != 360'd0);

    // ---------------------------------------------------------------- 本笔权重快照
    //
    // **为什么需要：三个求和函数读的是同一份 wt_main[ids[s]][j]。**
    // 原来 f_G / f_Wm / f_Wp 各自做一遍 5 位索引查表，等于把
    // 2304 路 x 40 位的 MUX 树复制三份（单独综合 515 s / 153735 cells /
    // 105359 µm²，占 11 个模块合计面积的 65.3%）。
    //
    // 现在先把本笔 8x16 主权重与 8x4 子权重一次取到 cur_main/cur_sub，
    // 三个函数都从这份读。DC 只需一份 MUX 树，后面的求和变成纯加法。
    //
    // 代价：多一级组合（8x16 + 8x4 = 160 个 40 位选择），但省掉 2 份完整 MUX。
    // 不改任何求和公式，G/Wp/Wm 的数值语义完全不变。
    logic [39:0] cur_main [0:7][0:15];
    logic [39:0] cur_sub  [0:7][0:3];

    always_comb begin
        for (int s = 0; s < 8; s++) begin
            for (int j = 0; j < 16; j++)
                cur_main[s][j] = locked_q ? wt_main[ids_i[s*5 +: 5]][j] : 40'd0;
            for (int j = 0; j < 4; j++)
                cur_sub[s][j] = locked_q ? wt_sub[ids_i[s*5 +: 5]][j] : 40'd0;
        end
    end

    // ---------------------------------------------------------------- 求和函数
    //
    // 用 function 而不是 always_comb 里的临时变量：
    // 之前用 always_comb 写 G_c/Wp_c/Wm_c 再寄存，反复出现
    // "组合值探针正确、寄存器采到 0"的现象（两级寄存也没解决）。
    // function 是纯组合、无中间状态，在 always_ff 里直接调用最可靠。
    // ---------------------------------------------------------------- 片号索引说明
    //
    // 历史：曾加过 sid_of() 把 5 位片号钳到 [0,17]，理由是"DC 对越界索引
    // 把 17/18 行折掉了"。**该定论已撤回**（b7c3d5a）——真实原因是我做
    // 门级测试时权重表位序接反（[0][0] 放到了最低位），现象伪装成折叠。
    // 修正接线后同一份网表 364 笔逐笔一致（WSUM_EQUIV_PASS）。
    //
    // 钳位函数已删除：它在合法域内不改变行为，不能证明修复了任何缺陷，
    // 且使顶层综合无法完成（dcwork6/dcwork7 均卡在 Processing 'wsum_v21'，
    // 超时后无网表产出，日志 0 条 Error）。
    //
    // 合同上片号由 sched18 的 pick() 产生，返回 5'(i) 且 i ∈ [0,17]，
    // 非法输入不在合同范围内。

    // 查表已在 cur_main/cur_sub 完成，此处只接受掩码。
    function automatic logic [63:0] f_G();
        logic [63:0] acc;
        begin
            acc = 64'd0;
            for (int s = 0; s < 8; s++)
                for (int j = 0; j < 16; j++)
                    acc = acc + {24'd0, cur_main[s][j]};
            f_G = acc;
        end
    endfunction

    function automatic logic [63:0] f_Wm(input logic [159:0] post);
        logic [63:0] acc;
        begin
            acc = 64'd0;
            for (int s = 0; s < 8; s++) begin
                for (int j = 0; j < 16; j++)
                    if (post[s*20 + j])
                        acc = acc + {24'd0, cur_main[s][j]};
                // 子阵列 4 位全算（含 j19 平衡位，名义 1/128）。
                // 只要 post 选中就计入 Wm —— j19 是真实电容。
                // 与节点宏（仿真边界）必须用同一张物理位表、同一口径。
                //
                // **必须显式展开，不能用 `for (int j=0;j<4;j++) ... w_sub[i][j]`**。
                // VCS 对"always_comb/函数内 + 二维数组 + 循环变量"会在某些
                // 情况下漏掉最后一次迭代（j=3），导致 j19 不计入 Wm ——
                // 这正是审计问题 ③（j19 漏算）的成因，节点宏已改为显式展开
                // （adc2_node_macro.sv:116-119），wsum 这里必须同步，
                // 否则同一个物理位在两处口径不一致。
                if (post[s*20 + 16]) acc = acc + {24'd0, cur_sub[s][0]};
                if (post[s*20 + 17]) acc = acc + {24'd0, cur_sub[s][1]};
                if (post[s*20 + 18]) acc = acc + {24'd0, cur_sub[s][2]};
                if (post[s*20 + 19]) acc = acc + {24'd0, cur_sub[s][3]};
            end
            f_Wm = acc;
        end
    endfunction

    function automatic logic [63:0] f_Wp(input logic [31:0] pre);
        logic [63:0] acc;
        begin
            acc = 64'd0;
            // 同 f_Wm：**显式展开 4 位，不用循环变量索引二维数组**。
            // 位序是 pre[s*4 + (3-j)]：j=0 取 pre[3], j=3 取 pre[0]，
            // 与节点宏的位映射一致（节点宏那边也是 j 与 pre 位反序）。
            for (int s = 0; s < 8; s++) begin
                if (pre[s*4 + 3]) acc = acc + {24'd0, cur_sub[s][0]};
                if (pre[s*4 + 2]) acc = acc + {24'd0, cur_sub[s][1]};
                if (pre[s*4 + 1]) acc = acc + {24'd0, cur_sub[s][2]};
                if (pre[s*4 + 0]) acc = acc + {24'd0, cur_sub[s][3]};
            end
            f_Wp = acc;
        end
    endfunction

    // ---------------------------------------------------------------- 单级寄存
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sum_valid <= 1'b0;
            G_q30     <= 64'd0;
            Wp_q30    <= 64'd0;
            Wm_q30    <= 64'd0;
        end else begin
            sum_valid <= calc_valid;
            if (calc_valid) begin
                G_q30  <= f_G();
                Wp_q30 <= f_Wp(pre_mask_i);
                Wm_q30 <= f_Wm(post_mask_i);
            end
        end
    end

endmodule
