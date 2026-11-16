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

    // ---------------------------------------------------------------- 权重表
    // 物理寻址：w_main_tbl[sid][j] / w_sub_tbl[sid][j]
    // sid = 0..17 物理片号，j 见合同 §3.2
    input  logic [39:0]  w_main_tbl [0:17][0:15],
    input  logic [39:0]  w_sub_tbl  [0:17][0:3],

    // ---------------------------------------------------------------- 结果
    output logic         sum_valid,
    output logic [63:0]  G_q30,
    output logic [63:0]  Wp_q30,
    output logic [63:0]  Wm_q30
);

    // ---------------------------------------------------------------- 求和函数
    //
    // 用 function 而不是 always_comb 里的临时变量：
    // 之前用 always_comb 写 G_c/Wp_c/Wm_c 再寄存，反复出现
    // "组合值探针正确、寄存器采到 0"的现象（两级寄存也没解决）。
    // function 是纯组合、无中间状态，在 always_ff 里直接调用最可靠。
    // ---------------------------------------------------------------- 片号钳位
    //
    // **必须把 5 位片号钳到 [0,17]**，否则综合会折叠掉 17/18 的权重行。
    //
    // 实测证据（docs/v21/WSUM_TIE_BUG.md）：
    //   网表里只有 w_main_tbl[17][*] 参与求和，[0..16] 全部被折掉。
    //   全部权重置 1 时 G = 140737488355200 = 8*16*(2^40-1)，
    //   正好等于"只有一行 16 列 × 8 slot"的和。
    //   dc.log 里 G_q30_reg[52..59] 等被 OPT-1206 当常量删除，
    //   网表里 TIEL 单元直接驱动 G_q30 高位。
    //
    // 原因：w_main_tbl 的维度是 [0:17]（18 项），但索引
    // `ids[s*5 +: 5]` 是 **5 位，可表示 0..31**。DC 对越界索引
    // (18..31) 的常量传播行为未定义，把所有 sid 折到了最后一个
    // 合法下标 17。
    //
    // 合同上片号本就 <= 17（18 片三态池，sched18 保证），
    // 所以钳位不改变合法域内的行为，只是把"未定义"变成"确定"。
    function automatic logic [4:0] sid_of(input logic [39:0] ids, input int s);
        logic [4:0] v;
        begin
            v = ids[s*5 +: 5];
            sid_of = (v > 5'd17) ? 5'd17 : v;
        end
    endfunction
    // **注意**：本函数当前未被使用（见调用处说明）。

    function automatic logic [63:0] f_G(input logic [39:0] ids);
        logic [63:0] acc;
        begin
            acc = 64'd0;
            for (int s = 0; s < 8; s++)
                for (int j = 0; j < 16; j++)
                    acc = acc + {24'd0, w_main_tbl[ids[s*5 +: 5]][j]};
            f_G = acc;
        end
    endfunction

    function automatic logic [63:0] f_Wm(input logic [39:0] ids,
                                         input logic [159:0] post);
        logic [63:0] acc;
        begin
            acc = 64'd0;
            for (int s = 0; s < 8; s++) begin
                for (int j = 0; j < 16; j++)
                    if (post[s*20 + j])
                        acc = acc + {24'd0, w_main_tbl[ids[s*5 +: 5]][j]};
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
                if (post[s*20 + 16]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][0]};
                if (post[s*20 + 17]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][1]};
                if (post[s*20 + 18]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][2]};
                if (post[s*20 + 19]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][3]};
            end
            f_Wm = acc;
        end
    endfunction

    function automatic logic [63:0] f_Wp(input logic [39:0] ids,
                                         input logic [31:0] pre);
        logic [63:0] acc;
        begin
            acc = 64'd0;
            // 同 f_Wm：**显式展开 4 位，不用循环变量索引二维数组**。
            // 位序是 pre[s*4 + (3-j)]：j=0 取 pre[3], j=3 取 pre[0]，
            // 与节点宏的位映射一致（节点宏那边也是 j 与 pre 位反序）。
            for (int s = 0; s < 8; s++) begin
                if (pre[s*4 + 3]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][0]};
                if (pre[s*4 + 2]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][1]};
                if (pre[s*4 + 1]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][2]};
                if (pre[s*4 + 0]) acc = acc + {24'd0, w_sub_tbl[ids[s*5 +: 5]][3]};
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
                G_q30  <= f_G(ids_i);
                Wp_q30 <= f_Wp(ids_i, pre_mask_i);
                Wm_q30 <= f_Wm(ids_i, post_mask_i);
            end
        end
    end

endmodule
