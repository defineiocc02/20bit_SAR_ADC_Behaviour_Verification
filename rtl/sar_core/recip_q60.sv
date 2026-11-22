`timescale 1ns/1ps
// D5 · 动态 G 的 Q60 倒数单元（替换 9 组预计算 inv37 查表）
//
// 用途：random18 每笔事务的 8 片组合不同，G 随之变化（跨度 1.668%），
// 固定 9 组倒数一定错。本模块在运行时算
//
//     inv_q60 = floor(2^60 / G)       27 bit（G ≥ 2^34 时）
//
// 与 recon_d5_w2 的 `num*inv >> 36` 配套 ⇒ D_eff = (6Wm−3G)/G·2^24。
//
// 定点格式为什么必须是 Q60（不是 Q40）：
//   G ≈ 2^35 ⇒ 2^40/G ≈ 32，整数粒度 1/32 = 3.1%，换算到 20 bit 输出是
//   上万 LSB。Q60 下 2^60/G ≈ 2^25，相对分辨率 2^-25 ⇒ D_eff 误差 ≈ 0.02 LSB。
//   （此前的 Python 原型用 FRAC=40，见 docs/RANDOM18_RTL.md §2 的错误记录。）
//
// 流水（2 拍，与顶层 E0→E3 的对齐见 digital_top_w.sv）：
//   S1 归一化 + 64 项 LUT 种子
//        b  = bit_length(G)
//        M  = G·2^(36−b) ∈ [2^35, 2^36)     （M>>5 = 尾数 m ∈ [2^30,2^31)）
//        idx= m[29:24]（bit30 是隐含整数位，不参与索引）
//        u0 = LUT[idx] ≈ 2^60/m_mid
//   S2 两次 Newton（定长整数，逐位与 src/adi_model/recip_q60.py 一致）
//        e  = 2^30 − ((m·u)>>30)
//        u  = u + ((u·e)>>30)
//        inv= u >> (b−31)
//
// 收敛：初值相对误差 ≤ 0.79%（区间宽 1/64），两次迭代 → 6.2e-5 → 3.9e-9，
// 已低于 Q60 自身的量化（2^-25），故 inv 与精确值最多差 1 个最低位
// （实测 43758 个片组中 1246 组差 1，最大相对误差 3.0e-8）。
//
// 位宽：m,u ≤ 2^31 → m·u ≤ 2^62；u·e 中 |e| ≤ 2^24 → ≤ 2^55。全部 64 位内。
module recip_q60 (
    input  logic        clk,
    input  logic        reset_n,
    input  logic        valid_i,
    input  logic [43:0] g_q30,          // G = Σ_{8 片} Σ_{16 主列} w
    output logic        valid_o,
    output logic [26:0] inv_q60,        // floor(2^60/G)（最多差 1）
    // **与 inv_q60 同笔的 G**。
    // 为什么需要它：下游修正级必须用"产生这个 inv 的那个 G"去算余数。
    // 从模块外部按固定拍数对齐极易出错（本轮就错了三次：
    // 直接用当前 g / 用 2 级 g_d2 / 用 1 级 g_d1 都不对，因为探针观察
    // 与真实相位受非阻塞赋值影响）。由内部把 G 与 inv 一起寄存，
    // 下游直接用即可，不再依赖任何外部对齐假设。
    output logic [43:0] g_q30_o,
    output logic        err_o           // G = 0（合同违例，不静默输出错值）
);

    localparam logic [32:0] ONE30 = 33'sd1073741824;   // 2^30

    // ================================================================ S1
    logic [5:0]  b;
    logic [35:0] m36;
    logic [30:0] m;
    logic [5:0]  idx;
    logic [30:0] u0;

    always_comb begin
        b = 6'd0;
        for (int i = 0; i < 44; i++) if (g_q30[i]) b = 6'(i + 1);
    end

    // b≤36 时原数的高位全零，先限宽再归一等效并覆盖所有组合分支。
    always_comb begin
        if (b == 6'd0) begin
            m36 = 36'd0;
        end else if (b <= 6'd36) begin
            m36 = 36'(g_q30) << (6'd36 - b);
        end else begin
            m36 = 36'(g_q30 >> (b - 6'd36));
        end
    end

    assign m   = m36[35:5];        // >>5，31 bit，Q30
    wire [4:0] unused_fractional_tail = m36[4:0]; // 按 Q30 合同向下截断。
    assign idx = m[29:24];         // 6 bit（bit30 为隐含整数位）

    // 64 项种子表：LUT[i] = 2^60 // (2^30 + (i+0.5)·2^24)
    // 数值与 src/adi_model/recip_q60.py 的 LUT 完全一致（同一式子生成）。
    //
    // **必须用 case 语句，不能用 `initial begin lut[..]=.. end`**：
    // DC 对 initial 块直接忽略（dc.log: VER-281 "The statements in
    // initial blocks are ignored"），于是 lut 全 X → u0 成常量 →
    // u0_r_reg 被 OPT-1206 删除 → Newton 迭代退化 → inv_q60 不再是
    // G 的函数。网表里可见 `assign inv_q60[0..2] = inv_q60[26]`。
    //
    // 该缺陷已用三条独立证据确认：
    //   · Formality 20 failing（i_q60_reg 19 bit + Wm_q30_reg[39]）
    //   · 门级等价 58 处差异，全在 out_code（RTL fff00 vs 网表 80000）
    //   · 网表结构 inv_q60[0..2] 同接一根网 + VER-281 警告
    function automatic logic [30:0] f_lut(input logic [5:0] i);
        case (i)
            6'd0 : f_lut = 31'd1065418243;
            6'd1 : f_lut = 31'd1049152316;
            6'd2 : f_lut = 31'd1033375590;
            6'd3 : f_lut = 31'd1018066322;
            6'd4 : f_lut = 31'd1003204039;
            6'd5 : f_lut = 31'd988769449;
            6'd6 : f_lut = 31'd974744350;
            6'd7 : f_lut = 31'd961111562;
            6'd8 : f_lut = 31'd947854851;
            6'd9 : f_lut = 31'd934958867;
            6'd10: f_lut = 31'd922409083;
            6'd11: f_lut = 31'd910191744;
            6'd12: f_lut = 31'd898293813;
            6'd13: f_lut = 31'd886702925;
            6'd14: f_lut = 31'd875407346;
            6'd15: f_lut = 31'd864395933;
            6'd16: f_lut = 31'd853658096;
            6'd17: f_lut = 31'd843183763;
            6'd18: f_lut = 31'd832963354;
            6'd19: f_lut = 31'd822987745;
            6'd20: f_lut = 31'd813248245;
            6'd21: f_lut = 31'd803736570;
            6'd22: f_lut = 31'd794444817;
            6'd23: f_lut = 31'd785365448;
            6'd24: f_lut = 31'd776491262;
            6'd25: f_lut = 31'd767815382;
            6'd26: f_lut = 31'd759331234;
            6'd27: f_lut = 31'd751032532;
            6'd28: f_lut = 31'd742913262;
            6'd29: f_lut = 31'd734967665;
            6'd30: f_lut = 31'd727190230;
            6'd31: f_lut = 31'd719575672;
            6'd32: f_lut = 31'd712118929;
            6'd33: f_lut = 31'd704815146;
            6'd34: f_lut = 31'd697659662;
            6'd35: f_lut = 31'd690648007;
            6'd36: f_lut = 31'd683775887;
            6'd37: f_lut = 31'd677039179;
            6'd38: f_lut = 31'd670433919;
            6'd39: f_lut = 31'd663956296;
            6'd40: f_lut = 31'd657602648;
            6'd41: f_lut = 31'd651369447;
            6'd42: f_lut = 31'd645253302;
            6'd43: f_lut = 31'd639250946;
            6'd44: f_lut = 31'd633359232;
            6'd45: f_lut = 31'd627575130;
            6'd46: f_lut = 31'd621895717;
            6'd47: f_lut = 31'd616318177;
            6'd48: f_lut = 31'd610839793;
            6'd49: f_lut = 31'd605457944;
            6'd50: f_lut = 31'd600170102;
            6'd51: f_lut = 31'd594973824;
            6'd52: f_lut = 31'd589866753;
            6'd53: f_lut = 31'd584846610;
            6'd54: f_lut = 31'd579911196;
            6'd55: f_lut = 31'd575058382;
            6'd56: f_lut = 31'd570286113;
            6'd57: f_lut = 31'd565592401;
            6'd58: f_lut = 31'd560975320;
            6'd59: f_lut = 31'd556433010;
            6'd60: f_lut = 31'd551963668;
            6'd61: f_lut = 31'd547565551;
            6'd62: f_lut = 31'd543236970;
            6'd63: f_lut = 31'd538976288;
            default: f_lut = 31'd0;   // 6 位索引全覆盖，default 只为可综合性
        endcase
    endfunction

    assign u0 = f_lut(idx);

    // S1 打拍
    logic        v1;
    logic [30:0] m_r, u0_r;
    logic [5:0]  b_r;
    logic        g0_r;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            v1 <= 1'b0; m_r <= 31'd0; u0_r <= 31'd0; b_r <= 6'd0; g0_r <= 1'b0;
        end else begin
            v1   <= valid_i;
            m_r  <= m;
            u0_r <= u0;
            b_r  <= b;
            g0_r <= (g_q30 == 44'd0);
        end
    end

    // ================================================================ S2
    // 两次 Newton，级间不进寄存器（同一拍内两级乘法串行，25 ns 预算内）
    logic signed [32:0] e1, u1, e2, u2;
    logic signed [63:0] p1, p2, p3, p4;

    always_comb begin
        p1 = $signed({2'b0, m_r}) * $signed({2'b0, u0_r});          // ≤ 2^62
        e1 = ONE30 - 33'(p1 >>> 30);

        p2 = $signed({2'b0, u0_r}) * e1;                             // |e1| ≤ 2^24
        u1 = $signed({2'b0, u0_r}) + 33'(p2 >>> 30);

        p3 = $signed({2'b0, m_r}) * u1;
        e2 = ONE30 - 33'(p3 >>> 30);

        p4 = u1 * e2;                                                // |e2| ≤ 2^15
        u2 = u1 + 33'(p4 >>> 30);
    end

    // 反归一化：inv = u2 >> (b−31)，b<31 时左移
    logic [32:0] u2u;
    logic [26:0] inv_c;
    always_comb begin
        u2u = 33'(u2);
        if (b_r >= 6'd31) inv_c = 27'(u2u >> (b_r - 6'd31));
        else              inv_c = 27'(u2u << (6'd31 - b_r));
    end

    // g 的两级延迟，与 v1/v2 完全同步
    logic [43:0] g_s1;

    logic v2, g0_d, ovf_d;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            v2 <= 1'b0; g0_d <= 1'b0; ovf_d <= 1'b0; inv_q60 <= 27'd0;
            g_s1 <= 44'd0;
        end else begin
            g_s1    <= g_q30;
            // **g_q30_o 必须与 inv_q60 同一拍就绪**。
            // inv_q60 在 v2 那一拍更新，而 v2 <= v1 <= valid_i，
            // 即 inv 对应的是 2 拍前采入的 g_q30。
            // g_s1 在 v1 那拍就是 g_q30(T-1)，到 v2 那拍已是 g_q30(T-2)…
            // 实测用 g_s2 会多一拍（i=0/err=1 出现在第 4 笔），
            // 正确取 **g_s1**（它在 v2 那一拍正好是 2 拍前的 g）。
            g_q30_o <= g_s1;
            v2      <= v1;
            g0_d    <= g0_r;
            // 输入合同：G ≥ 2^34（b ≥ 35）才保证 inv < 2^27；
            // 越界必须报错，不能静默截断成 27 位
            ovf_d   <= (b_r < 6'd35);
            inv_q60 <= (g0_r ? 27'd0 : inv_c);
        end
    end

    assign valid_o = v2;
    assign err_o   = v2 & (g0_d | ovf_d);

endmodule
