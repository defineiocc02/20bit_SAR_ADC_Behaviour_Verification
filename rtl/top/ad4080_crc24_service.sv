`timescale 1ns/1ps
`default_nettype none
// =============================================================================
// 文件名     : ad4080_crc24_service.sv
// 模块名     : ad4080_crc24_service
// 版本       : v1.0
// 功能       : 面向 AD4080 FIFO LVDS 读出帧的可编程硬件 CRC-24 实时流式计算服务。
//              直接对接 ad4080_chip_top / ad4080_fifo_record_framer 的 CRC 握手口，
//              在 400 MHz DDR（每拍 2-bit）速率下零等待产出已排线的 24-bit 校验尾。
// 设计说明   :
//   * [参数解耦] ADI Rev.B 手册未公开商业芯片精确 CRC-24 多项式；本模块绝不硬编码
//     伪造原厂参数，而是提供运行期/OTP 可配置的 poly/init/xorout/include_header/
//     refout/wire_order 接口，与 io/adi_crc/crc24.py 的直接式 MSB LFSR 口径逐位一致。
//     一旦评估板抓包通过 fifo_crc_probe.py 唯一辨识参数，仅需改 OTP 绑定即可流片。
//   * [零等待末拍] ad4080_fifo_record_framer 在末个 payload 拍同沿拉高 crc_ready_i；
//     本模块将当拍 2-bit beat_pair_i 组合并入 crc_next_state 并实时完成 refout/xorout/
//     wire_reorder，确保 crc_valid_o 与 crc_o 在末载荷沿即可零延迟握手。
//   * [安全边界] 未锁定配置或多项式常数项不为 1（!cfg_poly_i[0]）时 format_bound_o=0，
//     若强行启动则报 crc_error_o=1，绝不输出伪装合法的零尾；支持 crc_cancel_i 与
//     epoch 翻转立即隔离并清除旧会话。
// 变更记录   :
//   2026-11-24  v1.0 初版。实现 2-bit/beat 实时可编程硬件 CRC-24 服务。
// =============================================================================
module ad4080_crc24_service (
    // --- IO 域时钟与复位 ---
    input  logic        clk_io_i,              // 高速 IO 域时钟（与 FIFO framer 同域）
    input  logic        rst_ni,                // 异步低有效复位（由域拥有者同步释放）

    // --- 可编程 CRC-24 规格（可由 OTP 影子镜像或配置寄存器绑定） ---
    input  logic        cfg_profile_bound_i,   // 1 表示已绑定经实测/授权确认的硅片参数
    input  logic [23:0] cfg_poly_i,            // 省略 x^24 的正向多项式，bit0 必须为 1
    input  logic [23:0] cfg_init_i,            // 24-bit 寄存器初值
    input  logic [23:0] cfg_xorout_i,          // 24-bit 最终异或掩码（在输出域施加）
    input  logic        cfg_include_header_i,  // 1=覆盖 0xAA 同步头与载荷，0=仅覆盖载荷
    input  logic        cfg_refout_i,          // 1=余数做 24-bit 反转后再施加 xorout
    input  logic [1:0]  cfg_wire_order_i,      // 0=msb, 1=lsb, 2=byte_reverse, 3=lsb_per_byte

    // --- 与 ad4080_chip_top / ad4080_fifo_record_framer 对接的会话与节拍口 ---
    input  logic        io_record_epoch_i,     // 记录会话代际翻转位，变化时废弃在途旧计算
    input  logic        crc_start_i,           // 与 framer start_accept_o 相连，开启新会话
    input  logic        crc_cancel_i,          // 与 framer crc_cancel_o 相连，撤销旧会话
    input  logic        crc_ready_i,           // 与 framer crc_ready_o 相连，末载荷沿采样尾字
    input  logic        beat_valid_i,          // 当前 PHY beat 有效
    input  logic        beat_ready_i,          // 当前 PHY beat 被接收
    input  logic [1:0]  beat_pair_i,           // 2-bit DDR 载荷：[1] 先发(FALL)，[0] 后发(RISE)
    input  logic [1:0]  beat_kind_i,           // 0=同步头，1=载荷，2=CRC 尾

    // --- 输出至 ad4080_chip_top ---
    output logic        format_bound_o,        // 配置合法且已绑定，接入 chip_top.format_bound_io_i
    output logic        crc_service_ready_o,   // 空闲且无撤销，可独占受理下一条记录会话
    output logic        crc_valid_o,           // 会话活跃时持续有效，末载荷沿与 crc_ready_i 握手
    output logic        crc_error_o,           // 非法多项式/配置未绑定/会话冲突错误标志
    output logic [23:0] crc_o                  // 已按 wire_order 排线的 24-bit CRC 尾（bit23 先发）
);

    // =========================================================================
    // 1. 直接式 MSB LFSR 单步与 2-bit DDR 推进函数（与 crc24.py 同口径）
    // =========================================================================
    // [位序约定] beat_pair_i[1] 为先发位（FALL 沿），beat_pair_i[0] 为后发位（RISE 沿）。
    // 单步公式与 io/adi_crc/crc24.py::feed 完全等价：
    //   fb = state[23] ^ bit_in; next = {state[22:0], 1'b0} ^ (fb ? poly : 24'd0)。
    function automatic logic [23:0] crc24_step1(
        input logic [23:0] st,
        input logic        bit_in,
        input logic [23:0] poly
    );
        logic fb;
        fb = st[23] ^ bit_in;
        return {st[22:0], 1'b0} ^ (fb ? poly : 24'd0);
    endfunction

    function automatic logic [23:0] crc24_step2(
        input logic [23:0] st,
        input logic [1:0]  pair_bits,
        input logic [23:0] poly
    );
        logic [23:0] mid;
        mid = crc24_step1(st, pair_bits[1], poly);
        return crc24_step1(mid, pair_bits[0], poly);
    endfunction

    function automatic logic [23:0] bit_reverse24(input logic [23:0] v);
        logic [23:0] r;
        for (int i = 0; i < 24; i++) r[i] = v[23 - i];
        return r;
    endfunction

    function automatic logic [7:0] bit_reverse8(input logic [7:0] b);
        logic [7:0] r;
        for (int i = 0; i < 8; i++) r[i] = b[7 - i];
        return r;
    endfunction

    function automatic logic [23:0] apply_wire_order(
        input logic [23:0] v,
        input logic [1:0]  order
    );
        logic [23:0] r;
        case (order)
            2'd1:    r = bit_reverse24(v);                             // lsb
            2'd2:    r = {v[7:0], v[15:8], v[23:16]};                  // byte_reverse
            2'd3:    r = {bit_reverse8(v[23:16]),
                          bit_reverse8(v[15:8]),
                          bit_reverse8(v[7:0])};                       // lsb_per_byte
            default: r = v;                                            // msb
        endcase
        return r;
    endfunction

    // =========================================================================
    // 2. 会话锁存寄存器与实时组合前瞻
    // =========================================================================
    localparam logic [1:0] KIND_HEADER  = 2'd0;
    localparam logic [1:0] KIND_PAYLOAD = 2'd1;

    logic        active_q;                     // 当前存在正在计算的记录会话
    logic        err_q;                        // 本会话参数非法或协议异常
    logic        epoch_q;                      // 本会话启动时快照的 record epoch
    logic [23:0] state_q;                      // 已吸收历史节拍的 24-bit LFSR 状态
    logic [23:0] poly_q, xorout_q;             // 会话内冻结参数，防运行中篡改
    logic        inc_hdr_q, refout_q;
    logic [1:0]  wire_order_q;

    // 配置合法性：必须显式绑定且多项式常数项为 1（不可约/标准 CRC-24 必需）
    wire cfg_valid_now = cfg_profile_bound_i && cfg_poly_i[0];
    assign format_bound_o = cfg_valid_now;

    // 掉电/BIST/撤销导致的代际变化立即使旧会话失效
    wire epoch_mismatch = active_q && (io_record_epoch_i != epoch_q);
    wire beat_fire      = active_q && !err_q && !epoch_mismatch && beat_valid_i && beat_ready_i;
    wire absorb_beat    = beat_fire && ((beat_kind_i == KIND_PAYLOAD) ||
                                        (beat_kind_i == KIND_HEADER && inc_hdr_q));

    // [末拍零等待] 若本沿正在吸收末个 payload beat，同时把这两位并入输出余数
    wire [23:0] state_next_c = absorb_beat ? crc24_step2(state_q, beat_pair_i, poly_q) : state_q;
    wire [23:0] ref_val_c    = refout_q ? bit_reverse24(state_next_c) : state_next_c;
    wire [23:0] xor_val_c    = ref_val_c ^ xorout_q;
    wire [23:0] wire_crc_c   = apply_wire_order(xor_val_c, wire_order_q);

    // =========================================================================
    // 3. 会话生命周期状态机（启动 / 吸收 / 末拍退役 / 撤销冲刷）
    // =========================================================================
    always_ff @(posedge clk_io_i or negedge rst_ni) begin
        if (!rst_ni) begin
            active_q     <= 1'b0;
            err_q        <= 1'b0;
            epoch_q      <= 1'b0;
            state_q      <= 24'd0;
            poly_q       <= 24'd0;
            xorout_q     <= 24'd0;
            inc_hdr_q    <= 1'b0;
            refout_q     <= 1'b0;
            wire_order_q <= 2'd0;
        end else if (crc_cancel_i || epoch_mismatch) begin
            // [撤销路径] 立即清空会话，下一拍恢复 crc_service_ready_o=1
            active_q <= 1'b0;
            err_q    <= 1'b0;
            state_q  <= 24'd0;
        end else if (crc_start_i) begin
            // [启动路径] 冻结当拍 CRC 参数并装载初值；非法配置记错并在末拍报错退出
            active_q     <= 1'b1;
            err_q        <= !cfg_valid_now;
            epoch_q      <= io_record_epoch_i;
            state_q      <= cfg_init_i;
            poly_q       <= cfg_poly_i;
            xorout_q     <= cfg_xorout_i;
            inc_hdr_q    <= cfg_include_header_i;
            refout_q     <= cfg_refout_i;
            wire_order_q <= cfg_wire_order_i;
        end else if (active_q) begin
            if (crc_ready_i) begin
                // [正常退出] 末载荷沿 framer 已捕获 crc_o，本会话退役回空闲
                active_q <= 1'b0;
                err_q    <= 1'b0;
                state_q  <= 24'd0;
            end else if (absorb_beat) begin
                state_q <= state_next_c;
            end
        end
    end

    // 输出边界：空闲且无撤销时可接新会话；错误时清零 crc_o 避免泄漏脏值
    assign crc_service_ready_o = rst_ni && !active_q && !crc_cancel_i;
    assign crc_valid_o         = active_q && !crc_cancel_i && !epoch_mismatch;
    assign crc_error_o         = active_q && err_q;
    assign crc_o               = (crc_valid_o && !err_q) ? wire_crc_c : 24'd0;

endmodule
`default_nettype wire
