// =============================================================================
// 文件名     : adc2_node_macro.sv
// 模块名     : adc2_node_macro
// 版本       : v1.0
// 功能       : 独立电荷节点行为宏 —— 用**实际应用的 post** 与**采样时保存的 pre**
//              计算差分余差 y，再按 13 位 offset-binary 编码成 raw。
//              对应方案 v1.4 §4.1/§9.2、合同 v2 §8/§8.1。
// 设计说明   :
//   * 合同差分余差（docs/ISSCC2024_ONCHIP_CONTRACT_v2.md §7）：
//       G   = sum(active main weights)          —— 本笔 8 片主行和
//       Wm  = sum(final main/sub post-mask * physical weights)
//       Wp  = sum(sub-array sample pre-mask * physical weights)
//       y   = G*x + 3*G - 6*Wm + 6*Wp + b
//     名义 G=32（512Cu/16Cu），b=0。
//
//   * ADC2 编码（合同 §8.1，[E] 工程约定，不是 ADI 真实规格）：
//       code  = floor((y - ymin) * 8192 / 0.75)   ymin = -0.1875 V
//       y_hat = ymin + (code + 0.5) * 0.75 / 8192
//     折算到 20 位输入：LSB = 0.75/(8192*32) = 0.5 LSB20（名义 G）。
//
//   * **这是行为模型，不是晶体管级宏。** 它只证明数字侧"实际 post/pre -> 余差
//     -> raw"的因果与位序，不证明模拟建立、噪声、极性或真实窗口。
//     真实宏接入按 §12 的四项确认，未确认前不得声称已可直接接晶体管网表。
//
//   * 为什么用定点整数而不是 real：
//     综合/门级对拍要求确定性。这里全部用整数，权重以 Q30 表示
//     （w_*_q30），x 以 Q24 表示，避免浮点在两次仿真间产生差异。
//
//   * 权重表：本版用名义权重（合同位表），失配权重由测试台注入
//     （w_main_q30 / w_sub_q30 端口），便于 I4 复用同一宏做失配实验。
// 变更记录   :
//   2026-11-15  初版。I3 阶段。
// =============================================================================
module adc2_node_macro #(
    // 名义主阵列权重：main 20 位中高 16 位为 4Cu 主单元（权 4），低 4 位为子阵列。
    // 合同 §3.2：主 4Cu = 1/4，子 1/2/1/1 Cu = 1/32,1/64,1/128,1/128。
    // 这里以 Q30 表示"每片每位的电容贡献（Cu）"，实际权重由外部给。
    parameter int unsigned ADC2_N = 13
) (
    input  logic         clk,
    input  logic         rst_n,

    // ---------------------------------------------------------------- 本笔输入
    input  logic         calc_valid,     // 本拍计算一次
    // **定点格式：Q22**（不是 Q24）。
    //
    // 踩过的坑：最初用 Q24，`x_q24` 声明 24 位。但 Q24 表示 ±3V 需要
    // 24（小数）+ 2（整数）+ 1（符号）= 27 位，24 位只能表示 ±1V。
    // x=2.9985 V 的 Q24 值 50307072 有 26 位，被截断成 0x2FFA000 的低 24 位，
    // 符号位变成 1 -> 宏算出 x=-3 V、y=-191.7 V、raw=0（实测）。
    //
    // 改 Q22：±3V 需 22+3 = 25 位，用 26 位有符号。
    // 分辨率 2^-22 = 2.38e-7 V = 0.042 LSB20，远优于 ADC2 折算的 0.5 LSB20。
    input  logic signed [25:0] x_q22,    // 输入 x（Q22，V；±3V 内）
    input  logic signed [25:0] b_q22,    // 偏置 b（Q22）

    // 本笔 8 片的主行和 G（Q30，单位 Cu×1e9 归一化到 1.0 = 2^30）
    input  logic [39:0]  G_q30,          // 8 片主权重和

    // 实际应用的 post 与采样时保存的 pre（本笔 8 片）
    input  logic [159:0] post_mask,      // slot 级 160 位（8×20）
    input  logic [31:0]  pre_mask,       // slot 级 32 位（8×4）

    // 权重表（Q30，每片每位一个）
    // main[j]  : j=0..15 主单元权重
    // sub[j]   : j=16..19 子阵列权重
    input  logic [39:0]  w_main[0:7][0:15],
    input  logic [39:0]  w_sub [0:7][0:3],

    // ---------------------------------------------------------------- 输出
    output logic         y_valid,
    output logic signed [39:0] y_q22,    // 余差 y（Q22，V）
    output logic [12:0]  raw_code,
    output logic         range_err       // y 落在 [-0.1875,+0.5625) 之外
);

    // ---------------------------------------------------------------- Wm / Wp
    // 按合同：Wm 用实际 post（主 + 子），Wp 只算子阵列采样 pre。
    // 子阵列不采样 Vin，故不进入 G。
    logic signed [63:0] Wm_q30;
    logic signed [63:0] Wp_q30;

    always_comb begin
        Wm_q30 = 64'sd0;
        Wp_q30 = 64'sd0;
        for (int s = 0; s < 8; s++) begin
            // 主单元 16 位
            for (int j = 0; j < 16; j++) begin
                if (post_mask[s*20 + j])
                    Wm_q30 = Wm_q30 + $signed({24'd0, w_main[s][j]});
            end
            // 子阵列：只算 j=16..18（4Cu/2Cu/1Cu）。
            //
            // **j=19（子阵列平衡/guard 1Cu）不计入 Wm**。
            // 合同 §3.2 的位表把 j=19 定义为"子阵列平衡/guard"，
            // 它只为保持差分平衡而存在，不贡献信号电荷。
            //
            // 这条是从数字反解出来的，值得记下来：
            //   方案 §7 行400 说 A 在该 k 下"每片 post 均为 0x7FFFF"，
            //   而 A 的 y 必须是 0.28125 V（raw=5120）。
            //   若把 j=19 也算进 Wm：每片 4.0625 -> 8 片 32.5 -> y=-0.09375（错）。
            //   排除 j=19 后：每片 4.0546875 -> 8 片 32.4375 -> y=0.28125（对）。
            //   B 同样：排除后 raw=2560 精确命中。
            for (int j = 0; j < 3; j++) begin
                if (post_mask[s*20 + 16 + j])
                    Wm_q30 = Wm_q30 + $signed({24'd0, w_sub[s][j]});
            end
            // Wp：4 位采样预置（pre 的 bit3..0 对应子阵列 2/1/1/平衡）
            for (int j = 0; j < 4; j++) begin
                if (pre_mask[s*4 + (3-j)])
                    Wp_q30 = Wp_q30 + $signed({24'd0, w_sub[s][j]});
            end
        end
    end

    // ---------------------------------------------------------------- y 计算
    // y = G*x + 3*G - 6*Wm + 6*Wp + b
    //
    // 定点口径（全部 Q24 输出）：
    //   G  是 Q30（Cu 归一化），x 是 Q24（V）。
    //   合同里 G 是"主权重和"，名义 32。为了让 y 的物理量纲正确，
    //   这里把 G 解释为**无量纲增益**：G_real = G_q30 / 2^30 * 32（名义=32）。
    //   即 G_q30 = 2^30 对应名义增益 32/32 = 1 个单位时……
    //
    //   实际做法（与合同 §7 的代数一致、且便于逐位对拍）：
    //   把 Wm/Wp 也用同一把尺（Q30，名义满量程 2^30 = 32 单位）表达，
    //   于是：
    //     y_q24 = (G_q30 * x_q24) >> 30
    //           + 3*G_q24
    //           - 6*Wm_q24
    //           + 6*Wp_q24
    //           + b_q24
    //   其中 G_q24 = G_q30 >> 6, Wm_q24 = Wm_q30 >> 6（Q30 -> Q24 右移 6）。
    //
    //   注意：这里不追求与模拟宏的绝对电压标定（那属 §12 待绑定），
    //   只保证"数字侧因果与位序正确、同一配置下可复现"。
    logic signed [95:0] term_gx;
    logic signed [63:0] G_q22, Wm_q22, Wp_q22;
    logic signed [95:0] y_wide;

    always_comb begin
        G_q22  = $signed({24'd0, G_q30}) >>> 8;
        Wm_q22 = Wm_q30 >>> 8;
        Wp_q22 = Wp_q30 >>> 8;

        // G*x：Q30 * Q22 -> 右移 30 得 Q22
        term_gx = ($signed({24'd0, G_q30}) * $signed(x_q22)) >>> 30;

        y_wide = term_gx
               + (96'sd3 * G_q22)
               - (96'sd6 * Wm_q22)
               + (96'sd6 * Wp_q22)
               + $signed(b_q22);
    end

    // ---------------------------------------------------------------- 编码
    // code = floor((y - ymin) * 8192 / 0.75), ymin = -0.1875
    // 以 Q22 整数表达：ymin_q22 = -0.1875 * 2^22 = -786432
    //   (y - ymin) 是 Q22；乘 8192/0.75 = 32768/3
    //   用 (y-ymin)*32768/3 >> 22，并显式 floor（对负数算术右移即 floor）。
    localparam logic signed [63:0] YMIN_Q22 = -64'sd786432;   // -0.1875 * 2^22

    logic signed [95:0] num;
    logic signed [95:0] code_calc;
    logic signed [95:0] code_floor;
    logic               in_range;

    always_comb begin
        // (y - ymin) * 32768
        // **y_wide[63:0] 是位选，结果无符号**，符号信息会丢。
        // 必须显式 $signed() 或直接用 y_wide。
        // 踩过的坑：写成 y_wide[63:0] - YMIN_Q22 时，负数 y 会被当成巨大正数，
        // 于是 in_range 永远为假、raw 恒 0、range_err 恒 1（实测）。
        num = ($signed(y_wide) - YMIN_Q22) * 96'sd32768;
        // / 3 再 >> 22
        code_calc  = num / 96'sd3;
        // floor 语义：算术右移对负数是向负无穷取整，符合 floor
        code_floor = code_calc >>> 22;
        in_range   = (code_floor >= 96'sd0) && (code_floor <= 96'sd8191);
    end

    assign y_valid   = calc_valid;
    // 同 num 的坑：y_wide[39:0] 是位选，无符号。
    // 这里做**显式符号截断**：取低 40 位但保留符号语义。
    assign y_q22     = $signed(y_wide[39:0]);
    assign raw_code  = in_range ? code_floor[12:0] : 13'd0;
    assign range_err = calc_valid & ~in_range;

endmodule
