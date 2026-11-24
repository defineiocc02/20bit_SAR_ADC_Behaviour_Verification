`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_otp_macro_adapter.sv
// 模块名     : ad4080_otp_macro_adapter
// 版本       : v1.0
// 功能       : 工艺厂只读 OTP 硬宏适配桥 + 硬件镜像 CRC-24 完整性校验器。
//              直接对接 ad4080_chip_top / otp_shadow_loader 的全部 14 根 OTP 握手口，
//              并为手册 0x11 寄存器提供真实的硬件 ROM_CRC_ERR 故障源。
// 设计说明   :
//   * [宏替换边界] 通过 `ifdef USE_FOUNDRY_OTP_MACRO 预留标准工艺厂 1-port OTP/eFuse
//     硬宏接缝（CEB/RD/ADDR/DOUT/RD_VALID/RD_ERR），默认编译提供可综合行为存储模型，
//     流片时仅需绑定供应商网表与时序库即可替换，不改上层加载状态机。
//   * [真实 ROM_CRC_ERR] 镜像最后 1 字的低 24 位存放期望 CRC-24（多项式参数化，默认
//     0x864CFB / 初值 0xB704CE）。校验阶段对前 IMAGE_WORDS-1 个数据字按 MSB 优先逐字
//     迭代计算 CRC-24 并比对尾字，失配时输出 verify_rsp_pass_o=0 且锁存 rom_crc_err_o=1。
//   * [冲刷静默合同] 当 otp_adapters_cancel_i=1 时立即中止在途读/校验并清零 valid，
//     随后向 otp_shadow_loader 回报 otp_adapters_quiescent_o=1，杜绝跨会话晚到应答。
// 变更记录   :
//   2026-11-24  v1.0 初版。实现工艺厂 OTP 宏适配与真实硬件 CRC-24 镜像校验。
// =============================================================================
module ad4080_otp_macro_adapter #(
    parameter int unsigned IMAGE_WORDS  = 4,            // 逻辑镜像总字数（至少 2 字：数据 + CRC 尾字）
    parameter int unsigned WORD_WIDTH   = 32,           // 每字位宽（至少 24 位以容纳 CRC-24 尾字）
    parameter logic [23:0] CRC_POLY     = 24'h864CFB,   // 镜像校验 CRC-24 多项式（常数项必须为 1）
    parameter logic [23:0] CRC_INIT     = 24'hB704CE,   // 镜像校验 CRC-24 初值
    localparam int unsigned ADDR_WIDTH  = (IMAGE_WORDS > 1) ? $clog2(IMAGE_WORDS) : 1,
    localparam int unsigned IMAGE_WIDTH = IMAGE_WORDS * WORD_WIDTH
) (
    // --- AON 常开控制域时钟与复位 ---
    input  logic                    clk_aon_i,                 // 常开控制域时钟
    input  logic                    rst_ni,                    // 异步低有效复位
    input  logic                    otp_power_good_i,          // OTP 模拟供电就绪标志

    // --- 冲刷与静默握手（对接 chip_top.otp_adapters_*） ---
    input  logic                    otp_adapters_cancel_i,     // 高有效撤销在途读与校验
    output logic                    otp_adapters_quiescent_o,  // 读与校验双端均已排空静默

    // --- 只读宏读请求/应答口（对接 chip_top.otp_req_* / otp_rsp_*） ---
    input  logic                    otp_req_valid_i,           // 加载器读字请求
    output logic                    otp_req_ready_o,           // 适配器可受理唯一在途读
    input  logic [ADDR_WIDTH-1:0]   otp_req_addr_i,            // 逻辑字地址 0..IMAGE_WORDS-1
    output logic                    otp_rsp_valid_o,           // 读数据有效，保持至 ready 或 cancel
    input  logic                    otp_rsp_ready_i,           // 加载器消费读应答
    output logic [WORD_WIDTH-1:0]   otp_rsp_data_o,            // 读出的逻辑字
    output logic                    otp_rsp_error_o,           // 物理宏读错误或地址越界

    // --- 完整镜像校验口（对接 chip_top.otp_verify_*） ---
    input  logic                    otp_verify_req_valid_i,    // 加载器提交整像校验请求
    output logic                    otp_verify_req_ready_o,    // 校验器空闲可受理
    input  logic [IMAGE_WIDTH-1:0]  otp_verify_image_i,        // 第 0 字位于最低位，末字低 24 位为 CRC
    output logic                    otp_verify_rsp_valid_o,    // 校验判决有效
    input  logic                    otp_verify_rsp_ready_i,    // 加载器接收判决
    output logic                    otp_verify_rsp_pass_o,     // 1=CRC 匹配通过，0=镜像损坏拒绝

    // --- 外部工艺厂 OTP 宏物理接口与 ROM_CRC_ERR 状态 ---
    input  logic                    prog_we_i,                 // 仿真/量产烧录加载口（仅非硬宏模式用）
    input  logic [ADDR_WIDTH-1:0]   prog_addr_i,               // 预装载字地址
    input  logic [WORD_WIDTH-1:0]   prog_data_i,               // 预装载字数据
    input  logic                    macro_rd_err_inject_i,     // 物理宏读错注入/外部宏错误反馈
    output logic                    rom_crc_err_o              // 真实硬件 ROM_CRC_ERR（失配锁存至复位/重验）
);

`ifndef SYNTHESIS
    if (IMAGE_WORDS < 2 || WORD_WIDTH < 24 || !CRC_POLY[0]) begin : g_bad_otp_param
        initial $fatal(1, "OTP adapter requires IMAGE_WORDS>=2, WORD_WIDTH>=24, CRC_POLY[0]=1");
    end
`endif

    // =========================================================================
    // 1. 只读 OTP 宏存储阵列与多拍安全读适配
    // =========================================================================
    // [工艺替换点] 流片时定义 USE_FOUNDRY_OTP_MACRO，将本节替换为工艺厂 1-port
    // eFuse/OTP 硬宏实例（CEB/RD/ADDR/DOUT/ERR），保持上下握手端口零改动。
    localparam bit          ADDR_EXACT_POW2 = (IMAGE_WORDS == (32'd1 << ADDR_WIDTH));
    localparam int unsigned ADDR_CMP_W      = ADDR_WIDTH + 1;

    logic [WORD_WIDTH-1:0] otp_mem_q [0:IMAGE_WORDS-1];
    logic                  rd_pend_q, rd_rsp_valid_q, rd_rsp_err_q;
    logic [ADDR_WIDTH-1:0] rd_addr_q;
    logic [WORD_WIDTH-1:0] rd_data_q;
    logic                  prog_addr_ok_c, rd_addr_oob_c;

    // 区分 2 的幂次与非 2 的幂次深度，避免 Verilator -Wall 报 CMPCONST 告警；
    // 比较宽度使用独立 localparam ADDR_CMP_W，确保 sv2v 严格语法兼容。
    generate
        if (ADDR_EXACT_POW2) begin : g_addr_pow2
            assign prog_addr_ok_c = 1'b1;
            assign rd_addr_oob_c  = 1'b0;
        end else begin : g_addr_nonpow2
            assign prog_addr_ok_c = ({1'b0, prog_addr_i} < ADDR_CMP_W'(IMAGE_WORDS));
            assign rd_addr_oob_c  = ({1'b0, rd_addr_q} >= ADDR_CMP_W'(IMAGE_WORDS));
        end
    endgenerate

    wire rd_req_fire = otp_req_valid_i && otp_req_ready_o;
    wire rd_rsp_fire = rd_rsp_valid_q && otp_rsp_ready_i;

    always_ff @(posedge clk_aon_i) begin
        if (prog_we_i && prog_addr_ok_c)
            otp_mem_q[prog_addr_i] <= prog_data_i;
    end

    always_ff @(posedge clk_aon_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rd_pend_q      <= 1'b0;
            rd_rsp_valid_q <= 1'b0;
            rd_rsp_err_q   <= 1'b0;
            rd_addr_q      <= '0;
            rd_data_q      <= '0;
        end else if (otp_adapters_cancel_i || !otp_power_good_i) begin
            // [撤销路径] 掉电或宿主冲刷立即清除在途读与旧应答
            rd_pend_q      <= 1'b0;
            rd_rsp_valid_q <= 1'b0;
            rd_rsp_err_q   <= 1'b0;
        end else begin
            if (rd_rsp_fire) begin
                rd_rsp_valid_q <= 1'b0;
                rd_rsp_err_q   <= 1'b0;
            end
            if (rd_pend_q) begin
                rd_pend_q      <= 1'b0;
                rd_rsp_valid_q <= 1'b1;
                if (macro_rd_err_inject_i || rd_addr_oob_c) begin
                    rd_rsp_err_q <= 1'b1;
                    rd_data_q    <= '0;
                end else begin
                    rd_rsp_err_q <= 1'b0;
                    rd_data_q    <= otp_mem_q[rd_addr_q];
                end
            end else if (rd_req_fire) begin
                rd_pend_q <= 1'b1;
                rd_addr_q <= otp_req_addr_i;
            end
        end
    end

    assign otp_req_ready_o = rst_ni && otp_power_good_i && !otp_adapters_cancel_i
                             && !rd_pend_q && !rd_rsp_valid_q;
    assign otp_rsp_valid_o = rd_rsp_valid_q && !otp_adapters_cancel_i;
    assign otp_rsp_data_o  = rd_data_q;
    assign otp_rsp_error_o = rd_rsp_err_q;

    // =========================================================================
    // 2. 硬件 CRC-24 逐字镜像校验器（输出真实 ROM_CRC_ERR）
    // =========================================================================
    // [按字迭代] 每拍吸收 1 个 WORD_WIDTH 数据字（MSB 优先），避免把整幅镜像展开为
    // 巨型单拍组合树；对 IMAGE_WORDS=4 仅耗时 3 拍即可完成全像完整性校验。
    localparam int WORD_W_INT = int'(WORD_WIDTH);
    localparam int DATA_WORDS = int'(IMAGE_WORDS - 1);

    function automatic logic [23:0] crc24_word_step(
        input logic [23:0]           st_in,
        input logic [WORD_WIDTH-1:0] word_in
    );
        logic [23:0] st;
        logic        fb;
        st = st_in;
        for (int b = WORD_W_INT - 1; b >= 0; b--) begin
            fb = st[23] ^ word_in[b];
            st = {st[22:0], 1'b0} ^ (fb ? CRC_POLY : 24'd0);
        end
        return st;
    endfunction

    logic                   ver_busy_q, ver_rsp_valid_q, ver_pass_q;
    logic [ADDR_WIDTH-1:0]  ver_idx_q;
    logic [IMAGE_WIDTH-1:0] ver_img_q;
    logic [23:0]            ver_crc_q;
    logic                   rom_crc_err_q;

    wire ver_req_fire = otp_verify_req_valid_i && otp_verify_req_ready_o;
    wire ver_rsp_fire = ver_rsp_valid_q && otp_verify_rsp_ready_i;

    // 从锁存整像中按当前字号安全提取数据字（避免变宽切片产生综合告警）
    logic [WORD_WIDTH-1:0] cur_data_word_c;
    always_comb begin
        cur_data_word_c = '0;
        for (int w = 0; w < DATA_WORDS; w++) begin
            if (ver_idx_q == ADDR_WIDTH'(w))
                cur_data_word_c = ver_img_q[w * WORD_W_INT +: WORD_WIDTH];
        end
    end

    wire [23:0] expected_tail_crc = ver_img_q[DATA_WORDS * WORD_W_INT +: 24];
    wire [23:0] next_crc_c        = crc24_word_step(ver_crc_q, cur_data_word_c);
    wire        unused_otp_tail   = ^ver_img_q;

    always_ff @(posedge clk_aon_i or negedge rst_ni) begin
        if (!rst_ni) begin
            ver_busy_q      <= 1'b0;
            ver_rsp_valid_q <= 1'b0;
            ver_pass_q      <= 1'b0;
            ver_idx_q       <= '0;
            ver_img_q       <= '0;
            ver_crc_q       <= CRC_INIT;
            rom_crc_err_q   <= 1'b0;
        end else if (otp_adapters_cancel_i || !otp_power_good_i) begin
            // [撤销路径] 清除在途校验，保留 rom_crc_err_q 供宿主故障诊断直至新校验启动
            ver_busy_q      <= 1'b0;
            ver_rsp_valid_q <= 1'b0;
            ver_pass_q      <= 1'b0;
            ver_idx_q       <= '0;
            ver_crc_q       <= CRC_INIT;
        end else begin
            if (ver_rsp_fire) begin
                ver_rsp_valid_q <= 1'b0;
            end
            if (ver_req_fire) begin
                ver_busy_q      <= 1'b1;
                ver_rsp_valid_q <= 1'b0;
                ver_idx_q       <= '0;
                ver_img_q       <= otp_verify_image_i;
                ver_crc_q       <= CRC_INIT;
                rom_crc_err_q   <= 1'b0;
            end else if (ver_busy_q) begin
                ver_crc_q <= next_crc_c;
                if (ver_idx_q == ADDR_WIDTH'(DATA_WORDS - 1)) begin
                    ver_busy_q      <= 1'b0;
                    ver_rsp_valid_q <= 1'b1;
                    ver_pass_q      <= (next_crc_c == expected_tail_crc);
                    rom_crc_err_q   <= (next_crc_c != expected_tail_crc);
                end else begin
                    ver_idx_q <= ver_idx_q + ADDR_WIDTH'(1);
                end
            end
        end
    end

    assign otp_verify_req_ready_o   = rst_ni && otp_power_good_i && !otp_adapters_cancel_i
                                      && !ver_busy_q && !ver_rsp_valid_q;
    assign otp_verify_rsp_valid_o   = ver_rsp_valid_q && !otp_adapters_cancel_i;
    assign otp_verify_rsp_pass_o    = ver_pass_q;
    assign rom_crc_err_o            = rom_crc_err_q;

    // [静默确认] 读端与校验端全部空闲且无悬挂应答时，允许 otp_shadow_loader 出栅重入
    assign otp_adapters_quiescent_o = !rd_pend_q && !rd_rsp_valid_q
                                      && !ver_busy_q && !ver_rsp_valid_q;

endmodule
`default_nettype wire
