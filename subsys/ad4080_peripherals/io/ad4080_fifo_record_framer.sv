`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_fifo_record_framer.sv
// 模块名     : ad4080_fifo_record_framer
// 版本       : v1.0
// 功能       : 同域输出 FIFO 同步头、N 个样本及外部绑定的 CRC24 尾。
// 设计说明   :
//   * Rev. B 图 87–89 的 0xAA 头和 20/24 位样本分别走不同实体宽度。
//   * 单 LVDS 直接接连续结果 transport 的 FALL/RISE pair。原 transport
//     一字内部不可反压，已出 pair 遇撤回 ready 则废弃整条记录。
//   * SPI 有一字预取，旧字末 beat 可同沿接新字。CRC 是已排线的尾字，
//     外部服务提供本次唯一响应或报错，不在这里选择硅片校验算法。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

module ad4080_fifo_record_framer #(
    parameter bit SPI_CRC_TAIL_BOUND = 1'b0   // 默认采用图 87/88 与 ADI 读驱动的时钟数
) (
    // --- IO 域管理 ---
    input  logic        clk_i,                  // 一拍为一个可交付 PHY beat
    input  logic        rst_ni,                 // 异步置复位，释放由域拥有者同步
    input  logic        power_good_i,           // 低时屏蔽交付，时钟停摆时由外部复位协同
    input  logic        abort_i,                // 上游截断、超时或主机退出，由 IO 域提交

    // --- 同一帧发布 ---
    input  logic        start_valid_i,          // 提交命令，即使非法也在 ready 沿消费
    output logic        start_ready_o,          // 可检查并消费命令，成功另见 accept
    output logic        start_accept_o,         // 本沿同时启动外部 CRC/读回会话
    input  logic [1:0]  mode_i,                 // 0 单 LVDS，1 单 SPI，2 四 SPI
    input  logic [14:0] sample_count_i,         // 1…16384，按结果采集模式预先冻结
    input  logic        format_bound_i,         // 当前模式经过产品格式来源绑定
    input  logic        crc_service_ready_i,    // 可独占新 CRC 会话，旧响应已排清

    // --- 现成单 LVDS raw transport ---
    input  logic        payload_pair_valid_i,   // 当前字已发的 pair 必须本沿承接
    input  logic [1:0]  payload_pair_i,         // [1] FALL，[0] 随后的 RISE
    input  logic        payload_pair_first_i,   // 第一个转换 pair，按字重复
    input  logic        payload_pair_last_i,    // 第十个转换 pair，按字重复
    output logic        payload_start_allow_o,  // 只用于装载新字，不冻结在发字

    // --- 结果 SPI 并行样本源 ---
    input  logic        sample_valid_i,         // 直至握手前保持样本与 last
    output logic        sample_ready_o,         // 一字有空或本沿旧字退役后还需再读
    input  logic [19:0] sample_i,               // 二补码，符号扩展至 24 位
    input  logic        sample_last_i,          // 整条记录最后一个样本的源标记

    // --- CRC 适配器 ---
    input  logic        crc_valid_i,            // 只在本次会话提供，须保持到握手
    output logic        crc_ready_o,            // 最后 payload 退役沿或单独尾等待
    input  logic [23:0] crc_i,                  // 原始 bit 顺序已由外部服务绑定
    input  logic        crc_error_i,            // 与响应同沿有效，失败中止而非补零尾
    output logic        crc_cancel_o,           // 异常后请求 CRC 服务清除旧会话

    // --- 单一记录到具体 PHY 的 beat ---
    output logic        beat_valid_o,           // valid 和 ready 同高才接受
    input  logic        beat_ready_i,           // LVDS 已出 payload pair 时不可撤回
    output logic [3:0]  beat_o,                 // LVDS [1:0]，SPI1 [0]→SDOB，SPI4 [3:0]→A/B/C/D
    output logic [2:0]  beat_width_o,           // 1、2、4 位，空闲无有效位
    output logic        beat_first_o,           // 仅整条记录同步头的首 beat
    output logic        beat_last_o,            // CRC 尾末 beat，或默认 SPI 的末样本末 beat
    output logic [1:0]  beat_kind_o,            // 0 同步头，1 数据，2 CRC
    output logic        busy_o,                 // 命令已接受，尾尚未交付或正在等待
    output logic        done_o,                 // 尾末 beat 已接受，当拍完成脉冲
    output logic        error_o,                // 拒绝或中止脉冲，见 error_code_o
    output logic [2:0]  error_code_o            // 1 模式/长度，2 未绑定，3 计数/槽，4 CRC，5 掉电，6 abort
);

    // =========================================================================
    // 1. 模式和整条记录的剩余状态
    // =========================================================================
    localparam logic [1:0] MODE_LVDS = 2'd0, MODE_SPI1 = 2'd1, MODE_SPI4 = 2'd2;
    localparam logic [7:0] SYNC_HEADER = 8'hAA;         // 图 87/88/89，四线模式每线共享同一头
    typedef enum logic [2:0] {IDLE, HEADER, PAYLOAD, WAIT_CRC, TRAILER} state_t;
    state_t state_q;                                   // 整条记录而非某单样本的进度
    logic [1:0]  mode_q;                               // 启动接受后保持到正常末尾或中止
    logic [14:0] words_left_q;                         // 尚未从输出退役的转换字数量
    logic [4:0]  slot_q;                               // 当前部件的物理 beat，最大 23
    logic [19:0] spi_sample_q;                         // SPI 单字预取，符号位保留
    logic        spi_valid_q;                          // 仅空时或旧字末 beat 允许覆盖
    logic [23:0] crc_q;                                // 握手时一次性捕获完整 wire-order 尾部

    logic traffic_live, is_lvds, is_spi4, use_crc;
    logic command_fire, command_good, binding_good;
    logic beat_fire, header_last, word_last_slot, crc_last_slot;
    logic word_retire, final_payload, sample_fire, crc_fire;
    logic pair_shape_bad, pair_gap_bad, pair_unexpected, pair_backpressure;
    logic bad_length, fail_active;
    logic [23:0] spi_signed_word;
    logic [3:0]  spi_payload_nibble;
    int unsigned slot_index;                           // 统一算术位宽，消除窄计数的乘法回绕

    // =========================================================================
    // 2. 命令、原始 transport 和 CRC 握手
    // =========================================================================
    // [优先级] 复位/掉电/显式中止先阻断所有接口。CRC 拒绝、长度错误另外
    // 取消本次会话。CRC 服务接到取消后至少有一拍观察时间，再接受新命令。
    assign traffic_live = rst_ni && power_good_i && !abort_i;
    assign busy_o = state_q != IDLE;
    assign is_lvds = mode_q == MODE_LVDS;
    assign is_spi4 = mode_q == MODE_SPI4;
    assign use_crc = is_lvds || SPI_CRC_TAIL_BOUND;
    assign start_ready_o = traffic_live && !busy_o && !crc_cancel_o;
    assign command_fire = start_valid_i && start_ready_o;
    assign command_good = (mode_i == MODE_LVDS || mode_i == MODE_SPI1 || mode_i == MODE_SPI4) &&
                          sample_count_i >= 15'd1 && sample_count_i <= 15'd16384;
    assign binding_good = format_bound_i && (mode_i != MODE_LVDS || !payload_pair_valid_i) &&
                          ((mode_i != MODE_LVDS && !SPI_CRC_TAIL_BOUND) ||
                           (crc_service_ready_i && !crc_valid_i));
    assign start_accept_o = command_fire && command_good && binding_good;

    assign header_last = slot_q == (is_lvds ? 5'd3 : 5'd7);
    assign word_last_slot = slot_q == (is_lvds ? 5'd9 : is_spi4 ? 5'd5 : 5'd23);
    assign crc_last_slot = slot_q == (is_lvds ? 5'd11 : is_spi4 ? 5'd5 : 5'd23);
    assign pair_shape_bad = is_lvds && state_q == PAYLOAD && payload_pair_valid_i &&
                            (payload_pair_first_i != (slot_q == 0) || payload_pair_last_i != word_last_slot);
    assign pair_gap_bad = is_lvds && state_q == PAYLOAD && slot_q != 0 && !payload_pair_valid_i;
    assign pair_unexpected = is_lvds && busy_o && state_q != PAYLOAD && payload_pair_valid_i;
    assign pair_backpressure = is_lvds && state_q == PAYLOAD && payload_pair_valid_i && !beat_ready_i;
    assign beat_fire = beat_valid_o && beat_ready_i;
    assign word_retire = state_q == PAYLOAD && beat_fire && word_last_slot;
    assign final_payload = word_retire && words_left_q == 15'd1;

    // 头末拍允许 transport 同沿装第一字。字末拍能换字，最终字不得
    // 抢走下一记录的首字。没有 pair 时可等待 CDC 交付，不改变剩余字数。
    assign payload_start_allow_o = traffic_live && is_lvds && beat_ready_i &&
        !pair_shape_bad && !pair_gap_bad && !pair_unexpected &&
        ((state_q == HEADER && header_last) || (state_q == PAYLOAD && !final_payload));

    // [边界] SPI 的 last 与将接受的字对应。若此沿同时退役旧字，需用
    // 原剩余数 2 检查新字的 last，不能用旧字的结束条件再判一次。
    assign sample_ready_o = traffic_live && !is_lvds && (state_q == HEADER || state_q == PAYLOAD) &&
                            (!spi_valid_q || (word_retire && words_left_q > 15'd1));
    assign sample_fire = sample_valid_i && sample_ready_o;
    assign bad_length = sample_fire && (sample_last_i != (words_left_q == (word_retire ? 15'd2 : 15'd1)));

    assign crc_ready_o = traffic_live && use_crc && (state_q == WAIT_CRC || final_payload);
    assign crc_fire = crc_valid_i && crc_ready_o;
    assign fail_active = busy_o && (!power_good_i || abort_i || bad_length || pair_shape_bad ||
                         pair_gap_bad || pair_unexpected || pair_backpressure || (crc_fire && crc_error_i));

    // =========================================================================
    // 3. 物理 beat 映射
    // =========================================================================
    // 结果 SPI 的符号复制先发生。图 88 每组 payload 的 A、B、C、D
    // 引脚依次对应 nibble 的低到高位。CRC 已按线打包并免再次重排。
    assign spi_signed_word = {{4{spi_sample_q[19]}}, spi_sample_q};
    assign slot_index = int'($unsigned(slot_q));
    assign spi_payload_nibble = spi_signed_word[23-4*slot_index -: 4];

    always_comb begin
        beat_valid_o = 1'b0;
        beat_o = '0;
        beat_width_o = '0;
        beat_first_o = 1'b0;
        beat_last_o = 1'b0;
        beat_kind_o = '0;
        if (traffic_live && !pair_shape_bad && !pair_gap_bad && !pair_unexpected) begin
            case (state_q)
                HEADER: begin
                    beat_valid_o = 1'b1;
                    if (is_lvds)      beat_o = {2'b00, SYNC_HEADER[7-2*slot_index -: 2]};
                    else if (is_spi4) beat_o = {4{SYNC_HEADER[7-slot_index]}};
                    else              beat_o = {3'b000, SYNC_HEADER[7-slot_index]};
                    beat_first_o = slot_q == 0;
                end
                PAYLOAD: begin
                    beat_valid_o = is_lvds ? payload_pair_valid_i : spi_valid_q;
                    if (is_lvds) beat_o = {2'b00, payload_pair_i};
                    else if (is_spi4)
                        beat_o = {spi_payload_nibble[0], spi_payload_nibble[1],
                                  spi_payload_nibble[2], spi_payload_nibble[3]};
                    else beat_o = {3'b000, spi_signed_word[23-slot_index]};
                    beat_kind_o = 2'd1;
                    beat_last_o = beat_valid_o && !use_crc && words_left_q == 15'd1 && word_last_slot;
                end
                TRAILER: begin
                    beat_valid_o = 1'b1;
                    if (is_lvds)      beat_o = {2'b00, crc_q[23-2*slot_index -: 2]};
                    else if (is_spi4) beat_o = crc_q[23-4*slot_index -: 4];
                    else              beat_o = {3'b000, crc_q[23-slot_index]};
                    beat_kind_o = 2'd2;
                    beat_last_o = crc_last_slot;
                end
                default: begin end
            endcase
            if (beat_valid_o) beat_width_o = is_lvds ? 3'd2 : is_spi4 ? 3'd4 : 3'd1;
        end
    end

    // =========================================================================
    // 4. 记录推进和异常退出
    // =========================================================================
    // [异常] 装载错误可出现在已走若干有效 beat 后，必须由 PHY/主机按
    // 中止丢弃部分记录。CRC 缺不可用服务时在同步头出线前拒绝启动。
    // [正常] 末样本和 CRC 可同拍交付，若 CRC 尚未到达则独立等待。
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state_q <= IDLE;
            mode_q <= MODE_LVDS;
            slot_q <= '0;
            words_left_q <= '0;
            spi_sample_q <= '0;
            spi_valid_q <= 1'b0;
            crc_q <= '0;
            done_o <= 1'b0;
            error_o <= 1'b0;
            error_code_o <= '0;
            crc_cancel_o <= 1'b0;
        end else begin
            done_o <= 1'b0;
            error_o <= 1'b0;
            error_code_o <= '0;
            crc_cancel_o <= 1'b0;
            if (fail_active) begin
                state_q <= IDLE;
                slot_q <= '0;
                words_left_q <= '0;
                spi_valid_q <= 1'b0;
                error_o <= 1'b1;
                crc_cancel_o <= use_crc;
                if (!power_good_i) error_code_o <= 3'd5;
                else if (abort_i || pair_backpressure) error_code_o <= 3'd6;
                else if (bad_length || pair_shape_bad || pair_gap_bad || pair_unexpected) error_code_o <= 3'd3;
                else error_code_o <= 3'd4;
            end else if (command_fire) begin
                if (start_accept_o) begin
                    state_q <= HEADER;
                    mode_q <= mode_i;
                    words_left_q <= sample_count_i;
                    spi_valid_q <= 1'b0;
                    slot_q <= '0;
                end else begin
                    error_o <= 1'b1;
                    error_code_o <= command_good ? 3'd2 : 3'd1;
                end
            end else begin
                if (sample_fire) begin
                    spi_sample_q <= sample_i;
                    spi_valid_q <= 1'b1;
                end else if (word_retire) begin
                    spi_valid_q <= 1'b0;
                end

                if (beat_fire) begin
                    slot_q <= slot_q + 1'b1;
                    case (state_q)
                        HEADER: if (header_last) begin
                            state_q <= PAYLOAD;
                            slot_q <= '0;
                        end
                        PAYLOAD: if (word_last_slot) begin
                            words_left_q <= words_left_q - 1'b1;
                            slot_q <= '0;
                            if (final_payload) begin
                                if (use_crc) state_q <= WAIT_CRC;
                                else begin
                                    state_q <= IDLE;
                                    done_o <= 1'b1;
                                end
                            end
                        end
                        TRAILER: if (crc_last_slot) begin
                            state_q <= IDLE;
                            slot_q <= '0;
                            done_o <= 1'b1;
                        end
                        default: begin end
                    endcase
                end
                if (crc_fire) begin
                    crc_q <= crc_i;
                    state_q <= TRAILER;
                    slot_q <= '0;
                end
            end
        end
    end
endmodule

`default_nettype wire
