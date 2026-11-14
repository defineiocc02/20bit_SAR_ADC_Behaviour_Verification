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
    output logic [26:0] inv_q60,        // floor(2^60/G)
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

    // 左移结果取低 36 位：数学结果必 < 2^36，高位只是中间溢出
    logic [63:0] shl;
    always_comb begin
        if (b == 6'd0) begin
            m36 = 36'd0;
        end else if (b <= 6'd36) begin
            shl = {20'd0, g_q30} << (6'd36 - b);
            m36 = shl[35:0];
        end else begin
            m36 = 36'(g_q30 >> (b - 6'd36));
        end
    end

    assign m   = m36[35:5];        // >>5，31 bit，Q30
    assign idx = m[29:24];         // 6 bit（bit30 为隐含整数位）

    // 64 项种子表：LUT[i] = 2^60 // (2^30 + (i+0.5)·2^24)
    // 数值与 src/adi_model/recip_q60.py 的 LUT 完全一致（同一式子生成）。
    logic [30:0] lut [0:63];
    initial begin
        lut[ 0] = 31'd1065418243; lut[ 1] = 31'd1049152316;
        lut[ 2] = 31'd1033375590; lut[ 3] = 31'd1018066322;
        lut[ 4] = 31'd1003204039; lut[ 5] = 31'd988769449;
        lut[ 6] = 31'd974744350;  lut[ 7] = 31'd961111562;
        lut[ 8] = 31'd947854851;  lut[ 9] = 31'd934958867;
        lut[10] = 31'd922409083;  lut[11] = 31'd910191744;
        lut[12] = 31'd898293813;  lut[13] = 31'd886702925;
        lut[14] = 31'd875407346;  lut[15] = 31'd864395933;
        lut[16] = 31'd853658096;  lut[17] = 31'd843183763;
        lut[18] = 31'd832963354;  lut[19] = 31'd822987745;
        lut[20] = 31'd813248245;  lut[21] = 31'd803736570;
        lut[22] = 31'd794444817;  lut[23] = 31'd785365448;
        lut[24] = 31'd776491262;  lut[25] = 31'd767815382;
        lut[26] = 31'd759331234;  lut[27] = 31'd751032532;
        lut[28] = 31'd742913262;  lut[29] = 31'd734967665;
        lut[30] = 31'd727190230;  lut[31] = 31'd719575672;
        lut[32] = 31'd712118929;  lut[33] = 31'd704815146;
        lut[34] = 31'd697659662;  lut[35] = 31'd690648007;
        lut[36] = 31'd683775887;  lut[37] = 31'd677039179;
        lut[38] = 31'd670433919;  lut[39] = 31'd663956296;
        lut[40] = 31'd657602648;  lut[41] = 31'd651369447;
        lut[42] = 31'd645253302;  lut[43] = 31'd639250946;
        lut[44] = 31'd633359232;  lut[45] = 31'd627575130;
        lut[46] = 31'd621895717;  lut[47] = 31'd616318177;
        lut[48] = 31'd610839793;  lut[49] = 31'd605457944;
        lut[50] = 31'd600170102;  lut[51] = 31'd594973824;
        lut[52] = 31'd589866753;  lut[53] = 31'd584846610;
        lut[54] = 31'd579911196;  lut[55] = 31'd575058382;
        lut[56] = 31'd570286113;  lut[57] = 31'd565592401;
        lut[58] = 31'd560975320;  lut[59] = 31'd556433010;
        lut[60] = 31'd551963668;  lut[61] = 31'd547565551;
        lut[62] = 31'd543236970;  lut[63] = 31'd538976288;
    end

    assign u0 = lut[idx];

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

    logic v2, g0_d, ovf_d;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            v2 <= 1'b0; g0_d <= 1'b0; ovf_d <= 1'b0; inv_q60 <= 27'd0;
        end else begin
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
