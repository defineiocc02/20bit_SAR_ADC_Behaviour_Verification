//=============================================================================
// @file    spi_slave.sv
// @brief   按寄存器实体访问的 Mode 3 配置 SPI 从机。
// @details 严格访问将完整实体原子提交；SINGLE_INST 为每个寄存器重新接收指令。
//          主机与器件的 CRC 分开覆盖；一次 CRC 错误阻断本 CS 帧的后续写入。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.35–42；地址、顺序、种子或电气约定未明确时拒绝受影响请求。
// 时钟域: 调用方须保证 clk >= 8*SCLK；CS/SCLK 先同步，SDI 须满足过采样建立/保持要求。
// 复位: rst_n 低有效、异步置位；同步后的 CS 上升沿取消不完整事务。
// 延迟: 由串行位驱动；元数据准备、提交和地址推进占用 SCLK 边沿之间的 clk 间隔。
// 吞吐: 以寄存器实体为提交边界；同一 CS 帧能否继续下一实体，
//     由 SINGLE_INST、CRC 及访问资格决定。不承载转换结果数据。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: ad4080_regs 提供寄存器元数据与状态。
// 边界: 工程诊断不添加器件 CSR；改帧结构字段后，须结束本帧才能继续发送数据。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module spi_slave #(
    parameter int P_SHORT_ADDR_BITS = -1,  // P_SHORT_ADDR_BITS: -1 为未绑定，7 为已支持短地址格式。
    // P_WRITE_SDO_DRIVE: -1 未绑定；仅写 DATA 阶段采用 0 高阻、1 驱动零。
    //     状态与 CRC 阶段由各自逻辑控制。
    parameter int P_WRITE_SDO_DRIVE = -1
) (
    input  logic clk, rst_n,
    input  logic cs_n, sclk, sdi,
    output logic sdo, sdo_oe,
    input  logic interface_ready,
    input  logic reset_poll_only_i = 1'b0, // 供电、Pad 和所有权均合格的软复位初始化轮询许可
    input  logic restart_sync_i = 1'b0,    // 核心域换代，原 SPI 帧停止解析。
    input  logic crc_en, send_status, single_inst, short_instr, addr_asc,
    input  logic strict_access,
    input  logic [15:0] status_long,
    input  logic [7:0] status_short,
    output logic [15:0] addr, wdata,
    output logic [1:0] write_bytes,
    output logic we,
    input  logic [15:0] rdata,
    input  logic [1:0] entity_bytes,
    input  logic address_valid, read_only, access_supported,
    input  logic [3:0] access_reject_reason,
    output logic crc_err_pulse, clk_cnt_err_pulse, partial_err_pulse,
    output logic addr_err_pulse, readonly_err_pulse, not_ready_err_pulse,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason
);
    localparam logic [3:0] REJECT_UNBOUND = 4'd1, REJECT_ADDRESS = 4'd3,
        REJECT_READONLY = 4'd4, REJECT_PARTIAL = 4'd5, REJECT_CRC = 4'd6,
        REJECT_CLOCK = 4'd7, REJECT_NOT_READY = 4'd8;
    // 指令收齐后用 PREPARE 锁住该实体的 bank 元数据，随后启用数据边沿。
    // CRC 先检查再生成提交，ADVANCE 保证提交完成才允许换地址。
    typedef enum logic [2:0] {IDLE, INSTRUCTION, PREPARE, DATA, CRC,
                              ADVANCE, WAIT_CS} phase_t;
    phase_t phase;
    // CS/SCLK 的双级同步切断外部边沿到状态机的异步路径。
    // 表2指定主机可在 SCLK 上升沿后立即改变 SDI。外部时钟沿先锁位，
    // 再将整位交给核心；解析器使用已有的同步上升事件推进。
    // SCLK 先过 sck_meta 与 sck_sync，解析器在下一核心沿使用
    // sdi_core_sample_q。即使两时钟同沿，数据也可在中间沿完成采样。
    // 目标 STA 对 sdi_edge_q/Q 到 sdi_core_sample_q/D 设 datapath-only
    // 最大延迟，一核心周期扣除源 C→Q、接收建立和不确定度；目标库逐角验收。
    // SCLK Pad 到 sdi_edge_q 独立做输入建立/保持，核心时钟须 >= 8*SCLK。
    (* ASYNC_REG = "TRUE" *) logic cs_meta, cs_sync, sck_meta, sck_sync;
    logic sdi_edge_q, sdi_core_sample_q;

    always_ff @(posedge sclk or negedge rst_n) begin
        if (!rst_n) sdi_edge_q <= 1'b0;
        else sdi_edge_q <= sdi;
    end
    logic cs_prev, sck_prev;
    wire cs_fall = cs_prev && !cs_sync;
    wire cs_rise = !cs_prev && cs_sync;
    wire sck_fall = sck_prev && !sck_sync;
    wire sck_rise = !sck_prev && sck_sync;
    // Table 2 允许 CS 在末个 SCLK 采样沿上升。同拍先收当前位，
    // 下一拍才按更新后的状态检查是否完成。独立同步器下只裁决已采到的沿。
    logic cs_close_pending;
    wire close_with_sample = cs_rise && sck_rise;
    logic crc_frame, status_frame, single_frame, short_frame, ascend_frame, strict_frame;
    logic read_command, poisoned, optional_entity, data_started;
    logic poll_only_frame;
    // 未连接或未知许可均封闭。该信号须已经同步到配置执行时钟。
    bit reset_poll_requested, normal_access_ready;
    always_comb begin
        reset_poll_requested = reset_poll_only_i == 1'b1;
        normal_access_ready = interface_ready == 1'b1;
    end
    // 初始化期间仅接受本来就是轮询的帧。恢复后允许这种帧收完，
    // 失去合法供电/所有权且普通权限也为低时立即撤销。
    wire frame_access_ready = poll_only_frame ? (reset_poll_requested || normal_access_ready) :
                                                  (normal_access_ready && !reset_poll_requested);
    logic framing_write;
    logic [4:0] instruction_bits, data_bits;
    logic [2:0] crc_bits;
    logic [14:0] instruction_shift, rx_shift;
    logic [15:0] tx_shift, status_shift;
    logic [15:0] pending_write;
    logic [1:0] length_r;
    logic address_valid_r, read_only_r, supported_r;
    logic [3:0] reject_reason_r;
    logic [7:0] host_crc, device_crc, host_crc_expected, crc_tx;
    // 表 18：流读仅首实体需要控制器校验，后续数据和器件 CRC 仍完整送出。
    wire host_crc_required = !read_command || !optional_entity;
    // p37 的地址指针是固定 15 位，短格式只缩短首地址的指令字段。
    wire [15:0] advanced_addr = ascend_frame ? addr + {14'd0, length_r} :
                                               addr - {14'd0, length_r};
    // 表 19 的后续 seed 是本实体起址。现行有效 bank 地址都可用一字节
    // 无损表示。扩展到 0x0100 以上的有效地址需明确到 CRC8 种子的映射。
    wire crc_address_seed_missing = crc_frame && optional_entity && addr[15:8] != 8'd0;
    logic [6:0] crc_rx;
    wire [15:0] instruction_next = {instruction_shift[14:0], sdi_core_sample_q};
    wire [15:0] payload_next = {rx_shift[14:0], sdi_core_sample_q};
    wire [7:0] crc_rx_next = {crc_rx[6:0], sdi_core_sample_q};
    wire instruction_done = instruction_bits == (short_frame ? 5'd7 : 5'd15);
    // bank 选择每个实体的线宽。严格双字节的最后一个数据位晚于普通
    // 字节模式一个字节，避免先落下半个字段再报告部分访问。
    wire payload_done = data_bits == (length_r == 2'd2 ? 5'd15 : 5'd7);
    // wdata 保持第一线序字节在 [7:0]，高低意义由已绑定的 bank 解码。
    // 这个换位与 ADDR_ASCENSION 分开，避免发送降序时反两次。
    wire [15:0] completed_write = (length_r == 2'd2) ?
                                   {payload_next[7:0], payload_next[15:8]} :
                                   {8'd0, payload_next[7:0]};

    // Table 19 首实体从 0xA5 与实际线序开始。写时两路消耗同一 SDI，
    // 读时主机 CRC 覆盖填充，而器件 CRC 覆盖本拍实际输出的数据。
    function automatic [7:0] crc_bit(input logic [7:0] state, input logic bit_in);
        crc_bit = {state[6:0], 1'b0} ^ ((state[7] ^ bit_in) ? 8'h07 : 8'h00);
    endfunction

    // 记录该帧首次失败后进入 WAIT_CS。到 CS 重新选择前隔离后续时钟，
    // CRC 错误、地址不支持等情况不会把后续负载误当成新事务。
    task automatic reject_frame(input logic [3:0] reason);
        begin
            cfg_reject <= 1'b1;
            cfg_reject_reason <= reason;
            poisoned <= 1'b1;
            sdo_oe <= 1'b0;
            phase <= WAIT_CS;
        end
    endtask

    initial begin
        if (P_SHORT_ADDR_BITS != -1 && P_SHORT_ADDR_BITS != 7)
            $fatal(1, "spi_slave: supported explicit short format is R/W plus 7 address bits");
        if (P_WRITE_SDO_DRIVE < -1 || P_WRITE_SDO_DRIVE > 1)
            $fatal(1, "spi_slave: write SDO policy must be unbound, Hi-Z, or drive-zero");
    end

    // 核心侧与独立 SCLK 捕获使用硬复位。换代只在核心沿取消旧帧，
    // 并保持 SCLK 输入触发器的异步复位端不经过功能逻辑。
    task automatic reset_edges();
            cs_meta <= 1'b1; cs_sync <= 1'b1; cs_prev <= 1'b1;
            sck_meta <= 1'b1; sck_sync <= 1'b1; sck_prev <= 1'b1;
            sdi_core_sample_q <= 1'b0;
            cs_close_pending <= 1'b0;
    endtask

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) reset_edges();
        else if (restart_sync_i) reset_edges();
        else begin
            cs_meta <= cs_n; cs_sync <= cs_meta; cs_prev <= cs_sync;
            sdi_core_sample_q <= sdi_edge_q;
            sck_meta <= sclk; sck_sync <= sck_meta; sck_prev <= sck_sync;
            cs_close_pending <= close_with_sample;
        end
    end

    task automatic reset_parser();
            phase <= IDLE; sdo <= 1'b0; sdo_oe <= 1'b0;
            crc_frame <= 1'b0; status_frame <= 1'b0; single_frame <= 1'b0;
            short_frame <= 1'b0; ascend_frame <= 1'b0; strict_frame <= 1'b1;
            read_command <= 1'b0; poisoned <= 1'b0; optional_entity <= 1'b0;
            poll_only_frame <= 1'b0;
            data_started <= 1'b0; framing_write <= 1'b0;
            instruction_bits <= '0; data_bits <= '0; crc_bits <= '0;
            instruction_shift <= '0; rx_shift <= '0; tx_shift <= '0;
            status_shift <= '0; pending_write <= '0; length_r <= 2'd1;
            address_valid_r <= 1'b0; read_only_r <= 1'b0; supported_r <= 1'b0;
            reject_reason_r <= REJECT_UNBOUND;
            host_crc <= 8'hA5; device_crc <= 8'hA5; host_crc_expected <= '0;
            crc_rx <= '0; crc_tx <= '0;
            addr <= '0; wdata <= '0; write_bytes <= 2'd1; we <= 1'b0;
            crc_err_pulse <= 1'b0; clk_cnt_err_pulse <= 1'b0;
            partial_err_pulse <= 1'b0; addr_err_pulse <= 1'b0;
            readonly_err_pulse <= 1'b0; not_ready_err_pulse <= 1'b0;
            cfg_reject <= 1'b0; cfg_reject_reason <= '0;
    endtask

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) reset_parser();
        else if (restart_sync_i) reset_parser();
        else begin
            we <= 1'b0;
            crc_err_pulse <= 1'b0; clk_cnt_err_pulse <= 1'b0;
            partial_err_pulse <= 1'b0; addr_err_pulse <= 1'b0;
            readonly_err_pulse <= 1'b0; not_ready_err_pulse <= 1'b0;
            cfg_reject <= 1'b0; cfg_reject_reason <= '0;
            // 即使须等下一拍确认收帧结果，末位采样之后就释放外部输出。
            if (close_with_sample) sdo_oe <= 1'b0;
            if ((cs_rise && !close_with_sample) || cs_close_pending) begin
                // 完成状态 ADVANCE 可直接收帧。仍在 CRC 阶段说明首个待提交
                // 实体缺校验。数据缺位时按严格实体宽度区分少字节和钟数错。
                if (!poisoned) begin
                    if (phase == CRC) begin
                        // 对后续流读，提前结束的是器件 CRC 的出线时钟，
                        // 控制器在表 18 中无独立 CRC 发送义务。
                        crc_err_pulse <= host_crc_required;
                        clk_cnt_err_pulse <= !host_crc_required;
                        cfg_reject <= 1'b1;
                        cfg_reject_reason <= host_crc_required ? REJECT_CRC : REJECT_CLOCK;
                    end else if ((phase == INSTRUCTION && instruction_bits != 0) ||
                                 (phase == PREPARE && !optional_entity) ||
                                 // 流末尾预读下个地址是可选实体。未启动其数据相的
                                 // 事务可在上个实体之后正常退出。
                                 (phase == DATA && (!optional_entity || data_started))) begin
                        clk_cnt_err_pulse <= 1'b1;
                        cfg_reject <= 1'b1;
                        if (phase == DATA && strict_frame && length_r == 2'd2 && data_bits >= 5'd8) begin
                            partial_err_pulse <= 1'b1;
                            cfg_reject_reason <= REJECT_PARTIAL;
                        end else cfg_reject_reason <= REJECT_CLOCK;
                    end
                end
                phase <= IDLE; sdo <= 1'b0; sdo_oe <= 1'b0;
                instruction_bits <= '0; data_bits <= '0; crc_bits <= '0;
                instruction_shift <= '0; rx_shift <= '0; tx_shift <= '0;
                poisoned <= 1'b0; data_started <= 1'b0;
            end else if (cs_fall) begin
                phase <= INSTRUCTION;
                // 软复位后的专用轮询按默认长指令，无 CRC 和附带状态。
                // 不在指令被证明是 0x11 读之前打开其它状态驱动。
                poll_only_frame <= reset_poll_requested;
                crc_frame <= crc_en && !reset_poll_requested;
                status_frame <= send_status && !reset_poll_requested;
                single_frame <= single_inst && !reset_poll_requested;
                short_frame <= short_instr && !reset_poll_requested;
                ascend_frame <= addr_asc; strict_frame <= strict_access;
                // 图 67/70 的首采样可与 CS 下降同拍。此位参与指令
                // 和 CRC 起始累计，避免后续把首数据位当成地址末位。
                instruction_bits <= sck_rise ? 5'd1 : 5'd0;
                instruction_shift <= sck_rise ? {14'd0, sdi_core_sample_q} : 15'd0;
                host_crc <= sck_rise ? crc_bit(8'hA5, sdi_core_sample_q) : 8'hA5;
                device_crc <= sck_rise ? crc_bit(8'hA5, sdi_core_sample_q) : 8'hA5;
                poisoned <= 1'b0; optional_entity <= 1'b0;
                data_started <= 1'b0;
                sdo <= 1'b0; sdo_oe <= send_status && !reset_poll_requested;
                // 首降沿若与选片同拍，状态首位也在这个拍预装。首升沿同拍时
                // 预装后续状态位，外部首位建立仍由 pad/主机实际时序保证。
                if (send_status && !reset_poll_requested && (sck_fall || sck_rise)) begin
                    sdo <= short_instr ? status_short[7] : status_long[15];
                    status_shift <= short_instr ? {status_short[6:0], 9'd0} :
                                                  {status_long[14:0], 1'b0};
                end
                if (!normal_access_ready && !reset_poll_requested) begin
                    not_ready_err_pulse <= 1'b1;
                    reject_frame(REJECT_NOT_READY);
                end else if (!reset_poll_requested && short_instr && P_SHORT_ADDR_BITS != 7)
                    reject_frame(REJECT_UNBOUND);
            end else if (!cs_sync || close_with_sample) begin
                // 已开始的配置事务若失去电源/接口许可，撤销尚未提交的实体。
                // 闲置及等待退场路径不会因重复采样该许可而重复报错。
                if (phase != IDLE && phase != WAIT_CS && !frame_access_ready) begin
                    not_ready_err_pulse <= 1'b1;
                    reject_frame(REJECT_NOT_READY);
                end else if (phase == PREPARE && poll_only_frame &&
                             (!read_command || addr != 16'h0011)) begin
                    // p28 允许读 0x11，p82 的提前事务错误与被忽略的写分开。
                    // 只有非法事务产生事件；合法轮询读 bank 的忙态与粘滞视图。
                    not_ready_err_pulse <= 1'b1;
                    reject_frame(REJECT_NOT_READY);
                end else if (phase == PREPARE) begin
                    // 译码组合字段只在地址稳定后取一次，防止数据推进过程
                    // 中部分访问宽度、只读属性或有效地址被串到下一个实体。
                    length_r <= entity_bytes;
                    address_valid_r <= address_valid;
                    read_only_r <= read_only;
                    supported_r <= access_supported && !crc_address_seed_missing;
                    reject_reason_r <= crc_address_seed_missing ? REJECT_UNBOUND : access_reject_reason;
                    // 严格实体的实际开头由 bank 的 ASC 起址与宽度决定。
                    // 同帧后续实体没有新指令，只把当前地址本身装为种子。
                    if (crc_frame && optional_entity) begin
                        host_crc <= addr[7:0];
                        device_crc <= addr[7:0];
                    end
                    tx_shift <= {rdata[7:0], rdata[15:8]};
                    rx_shift <= '0; data_bits <= '0; data_started <= 1'b0;
                    phase <= DATA;
                end else if (phase == ADVANCE) begin
                    // 寄存器堆完成提交的整个时钟沿保持 addr 不变，再推进到下一实体。
                    // 0x00/01/10 会改变帧格式。完成当前实体后等待新 CS，
                    // 后续指令和 CRC 的模式开关只在新帧生效。
                    if (framing_write || poll_only_frame) begin
                        poisoned <= 1'b0;
                        phase <= WAIT_CS;
                    end else if (single_frame) begin
                        phase <= INSTRUCTION;
                        instruction_bits <= '0; instruction_shift <= '0;
                        host_crc <= 8'hA5; device_crc <= 8'hA5;
                        optional_entity <= 1'b0;
                    end else begin
                        // p37/图 60 的滚转与 CRC 可共用同一个地址计数。
                        // 落在空洞时由实际 bank 判断 ADDRESS_INVALID，与
                        // 是否有后续首实体种子无关。
                        addr <= {1'b0, advanced_addr[14:0]};
                        optional_entity <= 1'b1;
                        phase <= PREPARE;
                    end
                end else if (sck_fall) begin
                    case (phase)
                        INSTRUCTION: begin
                            // 普通指令阶段只有 SEND_STATUS 时设备才获得 SDO。
                            // 状态宽度随帧指令格式选择，后续逐位从同一快照推出。
                            sdo_oe <= status_frame;
                            if (instruction_bits == 0) begin
                                sdo <= short_frame ? status_short[7] : status_long[15];
                                status_shift <= short_frame ? {status_short[6:0], 9'd0} :
                                                             {status_long[14:0], 1'b0};
                            end else begin
                                sdo <= status_shift[15];
                                status_shift <= {status_shift[14:0], 1'b0};
                            end
                        end
                        DATA: begin
                            if (!data_started) begin
                                if (!address_valid_r) begin
                                    addr_err_pulse <= 1'b1; reject_frame(REJECT_ADDRESS);
                                end else if (!supported_r) begin
                                    if (reject_reason_r==REJECT_PARTIAL) partial_err_pulse<=1'b1;
                                    reject_frame(reject_reason_r);
                                end
                                else if (!read_command && read_only_r) begin
                                    readonly_err_pulse <= 1'b1; reject_frame(REJECT_READONLY);
                                // 写时 SDO 驱动方式是独立绑定，未知时不能用
                                // 高阻替代真实写数据相的驱动零要求。
                                end else if (!read_command && P_WRITE_SDO_DRIVE != 0 && P_WRITE_SDO_DRIVE != 1)
                                    reject_frame(REJECT_UNBOUND);
                                else begin
                                    data_started <= 1'b1;
                                    sdo <= read_command ? tx_shift[15] : 1'b0;
                                    sdo_oe <= read_command || (P_WRITE_SDO_DRIVE == 1);
                                    tx_shift <= {tx_shift[14:0], 1'b0};
                                end
                            end else begin
                                sdo <= read_command ? tx_shift[15] : 1'b0;
                                sdo_oe <= read_command || (P_WRITE_SDO_DRIVE == 1);
                                tx_shift <= {tx_shift[14:0], 1'b0};
                            end
                        end
                        CRC: begin
                            sdo <= crc_tx[7];
                            sdo_oe <= 1'b1;
                            crc_tx <= {crc_tx[6:0], 1'b0};
                        end
                        default: sdo_oe <= 1'b0;
                    endcase
                end else if (sck_rise) begin
                    case (phase)
                        INSTRUCTION: begin
                            instruction_shift <= instruction_next[14:0];
                            host_crc <= crc_bit(host_crc, sdi_core_sample_q);
                            device_crc <= crc_bit(device_crc, sdi_core_sample_q);
                            if (instruction_done) begin
                                read_command <= short_frame ? instruction_next[7] : instruction_next[15];
                                addr <= short_frame ? {9'd0, instruction_next[6:0]} :
                                                      {1'b0, instruction_next[14:0]};
                                instruction_bits <= '0;
                                optional_entity <= 1'b0;
                                phase <= PREPARE;
                            end else instruction_bits <= instruction_bits + 5'd1;
                        end
                        DATA: begin
                            if (data_started) begin
                                rx_shift <= payload_next[14:0];
                                host_crc <= crc_bit(host_crc, sdi_core_sample_q);
                                device_crc <= crc_bit(device_crc, read_command ? sdo : sdi_core_sample_q);
                                if (payload_done) begin
                                    pending_write <= completed_write;
                                    framing_write <= !read_command &&
                                        ((addr == 16'h0000) || (addr == 16'h0001) || (addr == 16'h0010));
                                    // 暂存完整数据并切至校验相。CRC 模式此时
                                    // 仍没有 bank 写脉冲，收到完整校验才能提交。
                                    if (crc_frame) begin
                                        host_crc_expected <= crc_bit(host_crc, sdi_core_sample_q);
                                        crc_tx <= crc_bit(device_crc, read_command ? sdo : sdi_core_sample_q);
                                        crc_rx <= '0; crc_bits <= '0;
                                        phase <= CRC;
                                    end else begin
                                        if (!read_command) begin
                                            wdata <= completed_write;
                                            write_bytes <= length_r; we <= 1'b1;
                                        end
                                        phase <= ADVANCE;
                                    end
                                end else data_bits <= data_bits + 5'd1;
                            end
                        end
                        CRC: begin
                            crc_rx <= crc_rx_next[6:0];
                            if (crc_bits == 3'd7) begin
                                // 校验在第八个 CRC 位进入后比较，包含最后一位。
                                // 错帧走相同 WAIT_CS 丢弃，旧寄存器继续有效。
                                if (host_crc_required && crc_rx_next != host_crc_expected) begin
                                    crc_err_pulse <= 1'b1;
                                    reject_frame(REJECT_CRC);
                                end else begin
                                    if (!read_command) begin
                                        wdata <= pending_write;
                                        write_bytes <= length_r; we <= 1'b1;
                                    end
                                    phase <= ADVANCE;
                                end
                            end else crc_bits <= crc_bits + 3'd1;
                        end
                        WAIT_CS: begin
                            if (!poisoned) begin
                                clk_cnt_err_pulse <= 1'b1;
                                reject_frame(REJECT_CLOCK);
                            end
                        end
                        default: ;
                    endcase
                end
            end else begin
                phase <= IDLE; sdo <= 1'b0; sdo_oe <= 1'b0;
            end
        end
    end
endmodule
`default_nettype wire
