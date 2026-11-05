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
    typedef enum logic [2:0] {IDLE, INSTRUCTION, PREPARE, DATA, CRC,
                              ADVANCE, WAIT_CS} phase_t;
    phase_t phase;
    (* ASYNC_REG = "TRUE" *) logic cs_meta, cs_sync, sck_meta, sck_sync;
    logic cs_prev, sck_prev;
    wire cs_fall = cs_prev && !cs_sync;
    wire cs_rise = !cs_prev && cs_sync;
    wire sck_fall = sck_prev && !sck_sync;
    wire sck_rise = !sck_prev && sck_sync;
    logic crc_frame, status_frame, single_frame, short_frame, ascend_frame, strict_frame;
    logic read_command, poisoned, optional_entity, data_started, wrap_unbound;
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
    logic [6:0] crc_rx;
    wire [15:0] instruction_next = {instruction_shift[14:0], sdi};
    wire [15:0] payload_next = {rx_shift[14:0], sdi};
    wire [7:0] crc_rx_next = {crc_rx[6:0], sdi};
    wire instruction_done = instruction_bits == (short_frame ? 5'd7 : 5'd15);
    wire payload_done = data_bits == (length_r == 2'd2 ? 5'd15 : 5'd7);
    wire [15:0] completed_write = (length_r == 2'd2) ?
                                   {payload_next[7:0], payload_next[15:8]} :
                                   {8'd0, payload_next[7:0]};

    function automatic [7:0] crc_bit(input logic [7:0] state, input logic bit_in);
        crc_bit = {state[6:0], 1'b0} ^ ((state[7] ^ bit_in) ? 8'h07 : 8'h00);
    endfunction

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

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cs_meta <= 1'b1; cs_sync <= 1'b1; cs_prev <= 1'b1;
            sck_meta <= 1'b1; sck_sync <= 1'b1; sck_prev <= 1'b1;
        end else begin
            cs_meta <= cs_n; cs_sync <= cs_meta; cs_prev <= cs_sync;
            sck_meta <= sclk; sck_sync <= sck_meta; sck_prev <= sck_sync;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase <= IDLE; sdo <= 1'b0; sdo_oe <= 1'b0;
            crc_frame <= 1'b0; status_frame <= 1'b0; single_frame <= 1'b0;
            short_frame <= 1'b0; ascend_frame <= 1'b0; strict_frame <= 1'b1;
            read_command <= 1'b0; poisoned <= 1'b0; optional_entity <= 1'b0;
            data_started <= 1'b0; wrap_unbound <= 1'b0; framing_write <= 1'b0;
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
        end else begin
            we <= 1'b0;
            crc_err_pulse <= 1'b0; clk_cnt_err_pulse <= 1'b0;
            partial_err_pulse <= 1'b0; addr_err_pulse <= 1'b0;
            readonly_err_pulse <= 1'b0; not_ready_err_pulse <= 1'b0;
            cfg_reject <= 1'b0; cfg_reject_reason <= '0;
            if (cs_rise) begin
                if (!poisoned) begin
                    if (phase == CRC) begin
                        crc_err_pulse <= 1'b1;
                        cfg_reject <= 1'b1; cfg_reject_reason <= REJECT_CRC;
                    end else if ((phase == INSTRUCTION && instruction_bits != 0) ||
                                 (phase == PREPARE && !optional_entity) ||
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
                crc_frame <= crc_en; status_frame <= send_status;
                single_frame <= single_inst; short_frame <= short_instr;
                ascend_frame <= addr_asc; strict_frame <= strict_access;
                instruction_bits <= '0; instruction_shift <= '0;
                host_crc <= 8'hA5; device_crc <= 8'hA5;
                poisoned <= 1'b0; optional_entity <= 1'b0;
                data_started <= 1'b0; wrap_unbound <= 1'b0;
                sdo <= 1'b0; sdo_oe <= send_status;
                if (interface_ready !== 1'b1) begin
                    not_ready_err_pulse <= 1'b1;
                    reject_frame(REJECT_NOT_READY);
                end else if (short_instr && P_SHORT_ADDR_BITS != 7)
                    reject_frame(REJECT_UNBOUND);
            end else if (!cs_sync) begin
                if (phase != IDLE && phase != WAIT_CS && interface_ready !== 1'b1) begin
                    not_ready_err_pulse <= 1'b1;
                    reject_frame(REJECT_NOT_READY);
                end else if (phase == PREPARE) begin
                    length_r <= entity_bytes;
                    address_valid_r <= address_valid;
                    read_only_r <= read_only;
                    supported_r <= access_supported && !wrap_unbound;
                    reject_reason_r <= wrap_unbound ? REJECT_UNBOUND : access_reject_reason;
                    tx_shift <= {rdata[7:0], rdata[15:8]};
                    rx_shift <= '0; data_bits <= '0; data_started <= 1'b0;
                    phase <= DATA;
                end else if (phase == ADVANCE) begin
                    // 寄存器堆完成提交的整个时钟沿保持 addr 不变，再推进到下一实体。
                    if (framing_write) begin
                        poisoned <= 1'b0;
                        phase <= WAIT_CS;
                    end else if (single_frame) begin
                        phase <= INSTRUCTION;
                        instruction_bits <= '0; instruction_shift <= '0;
                        host_crc <= 8'hA5; device_crc <= 8'hA5;
                        optional_entity <= 1'b0;
                    end else begin
                        if ((ascend_frame && (addr > (16'h7fff - {14'd0, length_r}))) ||
                            (!ascend_frame && (addr < {14'd0, length_r}))) begin
                            wrap_unbound <= 1'b1;
                        end else begin
                            addr <= ascend_frame ? addr + {14'd0, length_r} :
                                                   addr - {14'd0, length_r};
                            wrap_unbound <= 1'b0;
                        end
                        // 后续 CRC 实体需要尚未披露的地址到种子映射，因此保持不可用。
                        if (crc_frame) wrap_unbound <= 1'b1;
                        optional_entity <= 1'b1;
                        phase <= PREPARE;
                    end
                end else if (sck_fall) begin
                    case (phase)
                        INSTRUCTION: begin
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
                            host_crc <= crc_bit(host_crc, sdi);
                            device_crc <= crc_bit(device_crc, sdi);
                            if (instruction_done) begin
                                read_command <= short_frame ? instruction_next[7] : instruction_next[15];
                                addr <= short_frame ? {9'd0, instruction_next[6:0]} :
                                                      {1'b0, instruction_next[14:0]};
                                instruction_bits <= '0;
                                optional_entity <= 1'b0; wrap_unbound <= 1'b0;
                                phase <= PREPARE;
                            end else instruction_bits <= instruction_bits + 5'd1;
                        end
                        DATA: begin
                            if (data_started) begin
                                rx_shift <= payload_next[14:0];
                                host_crc <= crc_bit(host_crc, sdi);
                                device_crc <= crc_bit(device_crc, read_command ? sdo : sdi);
                                if (payload_done) begin
                                    pending_write <= completed_write;
                                    framing_write <= !read_command &&
                                        ((addr == 16'h0000) || (addr == 16'h0001) || (addr == 16'h0010));
                                    if (crc_frame) begin
                                        host_crc_expected <= crc_bit(host_crc, sdi);
                                        crc_tx <= crc_bit(device_crc, read_command ? sdo : sdi);
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
                                if (crc_rx_next != host_crc_expected) begin
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
