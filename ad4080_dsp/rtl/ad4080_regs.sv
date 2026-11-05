//=============================================================================
// @file    ad4080_regs.sv
// @brief   寄存器实体的原子访问与显式能力拒绝。
// @details 严格多字节访问由 SPI 缓存后一次提交。软件复位序列要求调用方明确
//          提供空闲状态；未满足捕获交互约定时拒绝，不静默修改受影响的状态。
// @project SAR20 / source-bounded digital processing
// @author  Project maintainers; contributions are tracked by Git.
// @date    2026-11-05
// @version Git content version; no separate device revision is implied.
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.27,35–42,73–94；A01–A11 仍需外部明确绑定。
// 时钟域: clk；寄存器事务、GPIO 回读与捕获状态输入均同步。
// 复位: 硬件复位或合法的两次写软件复位；W1C 清除优先于同沿状态置位。
// 延迟: 读数据与访问元数据为组合逻辑；写入在 we 被接受的时钟沿提交。
// 吞吐: 每个 clk 最多接受一个完整寄存器实体。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: cfg_reject 为工程诊断；不静默保存不支持字段，也不将其映射为新的器件 CSR。
//=============================================================================
`timescale 1ns/1ps
`default_nettype none

module ad4080_regs #(
    parameter int P_MULTIBYTE_ORDER = -1,
    parameter int P_OFFSET_LSB_ADDR = -1,
    parameter int P_PRODUCT_ID_L = -1,
    parameter int P_SHORT_ADDR_BITS = -1,
    parameter bit P_GAIN_MAP_VALID = 1'b0,
    parameter bit P_GAIN_CODE0_SUPPORTED = 1'b0,
    parameter bit P_COMP_COEFF_VALID = 1'b0
) (
    input  logic clk, rst_n,
    input  logic [15:0] addr, wdata,
    input  logic [1:0] write_bytes,
    input  logic we,
    output logic [15:0] rdata,
    output logic [1:0] entity_bytes,
    output logic address_valid, read_only, access_supported,
    output logic [3:0] access_reject_reason,
    input  logic adc_idle,
    input  logic [3:0] gpio_levels,
    input  logic capture_active, capture_incomplete, capture_io_active,
    input  logic st_fifo_full, st_fifo_read_done, st_hi_flag, st_lo_flag,
    input  logic crc_err_pulse, clk_cnt_err_pulse, partial_err_pulse,
    input  logic addr_err_pulse, readonly_err_pulse, not_ready_err_pulse,
    output logic hi_clr, lo_clr,
    output logic signed [11:0] offset_code,
    output logic [9:0] gain_code,
    output logic [1:0] filter_sel,
    output logic [3:0] dec_sel,
    output logic fsync_pulse,
    output logic [1:0] fifo_mode,
    output logic [14:0] fifo_watermark,
    output logic fifo_arm, fifo_clear,
    input  logic wm_wb_valid,
    input  logic [14:0] wm_wb_data,
    output logic [10:0] event_hyst,
    output logic [11:0] event_hi, event_lo,
    output logic event_en, hi_route, lo_route,
    output logic [3:0] gpio_dir, gpio_reg,
    output logic [3:0][3:0] gpio_sel,
    output logic crc_en, send_status, addr_asc, single_inst, short_instr,
    output logic strict_access, sw_reset_pulse,
    output logic [7:0] status, interface_status,
    output logic [15:0] status_long,
    output logic [7:0] status_short,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason
);
    localparam logic [3:0] REJECT_UNBOUND=4'd1, REJECT_UNSUPPORTED=4'd2,
        REJECT_ADDRESS=4'd3, REJECT_READONLY=4'd4, REJECT_PARTIAL=4'd5,
        REJECT_NOT_IDLE=4'd8, REJECT_VALUE=4'd9;
    logic signed [11:0] offset_r;
    logic [9:0] gain_r;
    logic [1:0] filter_r, fifo_mode_r;
    logic [3:0] dec_r;
    logic [14:0] watermark_r;
    logic [10:0] hyst_r;
    logic [11:0] hi_r, lo_r;
    logic event_r, hi_route_r, lo_route_r;
    logic [3:0] gpio_dir_r, gpio_data_r;
    logic [3:0][3:0] gpio_sel_r;
    logic crc_r, send_status_r, strict_r, ascend_r, single_r, short_r;
    logic [1:0] crc_enable_bits_r, crc_inverse_bits_r;
    logic [7:0] scratch_r;
    logic reset_armed, por_r, por_analog_r;
    logic not_ready_r, clock_r, crc_error_r, readonly_r, partial_r, address_r;
    logic [15:0] pair_value;
    wire [14:0] watermark_candidate = clamp_watermark(pair_value[14:0]);
    wire software_reset_allowed = (adc_idle === 1'b1) && !capture_incomplete && !capture_io_active;
    wire sw_reset_request = we && write_bytes==2'd1 && addr == 16'h0000 && reset_armed &&
                            !(wdata[7] && wdata[0]);
    wire sw_reset_now = sw_reset_request && software_reset_allowed;

    initial begin
        if (P_MULTIBYTE_ORDER != -1 && P_MULTIBYTE_ORDER != 0)
            $fatal(1,"ad4080_regs: multibyte order must be explicitly bound or unavailable");
        if (P_OFFSET_LSB_ADDR != -1 && P_OFFSET_LSB_ADDR != 32'h00000025)
            $fatal(1,"ad4080_regs: OFFSET binding conflicts with the remaining confirmed register map");
        if (P_PRODUCT_ID_L < -1 || P_PRODUCT_ID_L > 255)
            $fatal(1,"ad4080_regs: PRODUCT_ID_L binding is missing or not an 8-bit value");
    end

    function automatic [14:0] clamp_watermark(input logic [14:0] value);
        if (value == 0) clamp_watermark = 15'd1;
        else if (value > 15'd16384) clamp_watermark = 15'd16384;
        else clamp_watermark = value;
    endfunction

    function automatic logic gpio_function_supported(input logic [3:0] value);
        // CNV 到滤波输出的延迟尚未绑定，FILTER_RDY 选择不可用。
        gpio_function_supported = (value <= 4'd10) && (value != 4'd3);
    endfunction

    function automatic [22:0] decode_pair(input logic [15:0] value,
                                           input logic [15:0] base);
        logic [15:0] wire_value;
        logic [1:0] length;
        logic supported, first_high;
        logic [3:0] reason;
        begin
            wire_value='0; length=2'd1; supported=1'b1; reason=4'd0;
            first_high=1'b0;
            if (strict_r) begin
                length=2'd2;
                if (P_MULTIBYTE_ORDER != 0) begin
                    supported=1'b0; reason=(P_MULTIBYTE_ORDER==-1) ? REJECT_UNBOUND : REJECT_UNSUPPORTED;
                end else begin
                    first_high=!ascend_r;
                    if (addr != (first_high ? base+16'd1 : base)) begin
                        supported=1'b0; reason=REJECT_PARTIAL;
                    end
                    wire_value=first_high ? {value[7:0],value[15:8]} : value;
                end
            end else wire_value=(addr==base) ? {8'd0,value[7:0]} : {8'd0,value[15:8]};
            decode_pair={wire_value,length,supported,reason};
        end
    endfunction

    function automatic [15:0] merged_pair(input logic [15:0] old_value,
                                          input logic [15:0] base);
        logic first_high;
        begin
            first_high = !ascend_r;
            if (write_bytes == 2'd2)
                merged_pair = first_high ? {wdata[7:0],wdata[15:8]} : wdata;
            else if (addr == base) merged_pair = {old_value[15:8],wdata[7:0]};
            else merged_pair = {wdata[7:0],old_value[7:0]};
        end
    endfunction

    always_comb begin
        rdata='0; entity_bytes=2'd1; address_valid=1'b1; read_only=1'b0;
        access_supported=1'b1; access_reject_reason=4'd0;
        case (addr)
            16'h0000: rdata={8'd0,2'b00,ascend_r,1'b1,4'd0};
            16'h0001: rdata={8'd0,single_r,3'd0,short_r,3'd0};
            16'h0002: rdata=16'd0;
            16'h0003: begin rdata=16'h0007; read_only=1'b1; end
            16'h0004: begin
                read_only=1'b1;
                if (P_PRODUCT_ID_L < 0 || P_PRODUCT_ID_L > 255) begin
                    access_supported=1'b0; access_reject_reason=REJECT_UNBOUND;
                end
                else rdata={8'd0,8'(P_PRODUCT_ID_L)};
            end
            16'h0005: begin rdata=16'h0000; read_only=1'b1; end
            16'h0006: begin rdata=16'h0002; read_only=1'b1; end
            16'h000A: rdata={8'd0,scratch_r};
            16'h000B: begin rdata=16'h0083; read_only=1'b1; end
            16'h000C: begin rdata=16'h0056; read_only=1'b1; end
            16'h000D: begin rdata=16'h0004; read_only=1'b1; end
            16'h0010: rdata={8'd0,crc_enable_bits_r,strict_r,send_status_r,2'd0,crc_inverse_bits_r};
            16'h0011: rdata={8'd0,interface_status};
            16'h0014: rdata={8'd0,status};
            16'h0015: rdata=16'h0040;
            16'h0016: rdata=16'h0000;
            16'h0017: rdata=16'h0020;
            16'h0019: rdata={8'd0,gpio_levels,gpio_dir_r};
            16'h001A: rdata={8'd0,gpio_sel_r[1],gpio_sel_r[0]};
            16'h001B: rdata={8'd0,gpio_sel_r[3],gpio_sel_r[2]};
            16'h001C: rdata={8'd0,event_r,hi_route_r,lo_route_r,3'd0,fifo_mode_r};
            16'h001D,16'h001E: {rdata,entity_bytes,access_supported,access_reject_reason}=
                decode_pair({1'b0,watermark_r},16'h001D);
            16'h001F,16'h0020: {rdata,entity_bytes,access_supported,access_reject_reason}=
                decode_pair({5'd0,hyst_r},16'h001F);
            16'h0021,16'h0022: {rdata,entity_bytes,access_supported,access_reject_reason}=
                decode_pair({4'd0,hi_r},16'h0021);
            16'h0023,16'h0024: {rdata,entity_bytes,access_supported,access_reject_reason}=
                decode_pair({4'd0,lo_r},16'h0023);
            16'h0025,16'h0026: begin
                {rdata,entity_bytes,access_supported,access_reject_reason}=decode_pair({4'd0,offset_r},16'h0025);
                if (P_OFFSET_LSB_ADDR != 32'h00000025) begin
                    access_supported=1'b0; access_reject_reason=REJECT_UNBOUND;
                end
            end
            16'h0027,16'h0028: {rdata,entity_bytes,access_supported,access_reject_reason}=
                decode_pair({6'd0,gain_r},16'h0027);
            16'h0029: rdata={8'd0,1'b0,dec_r,1'b0,filter_r};
            default: begin address_valid=1'b0; access_supported=1'b0; access_reject_reason=REJECT_ADDRESS; end
        endcase
    end

    always_comb begin
        case (addr)
            16'h001D,16'h001E: pair_value=merged_pair({1'b0,watermark_r},16'h001D);
            16'h001F,16'h0020: pair_value=merged_pair({5'd0,hyst_r},16'h001F);
            16'h0021,16'h0022: pair_value=merged_pair({4'd0,hi_r},16'h0021);
            16'h0023,16'h0024: pair_value=merged_pair({4'd0,lo_r},16'h0023);
            16'h0025,16'h0026: pair_value=merged_pair({4'd0,offset_r},16'h0025);
            16'h0027,16'h0028: pair_value=merged_pair({6'd0,gain_r},16'h0027);
            default: pair_value=wdata;
        endcase
    end

    task automatic reject_write(input logic [3:0] reason);
        begin cfg_reject<=1'b1; cfg_reject_reason<=reason; end
    endtask

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            offset_r<='0; gain_r<=10'd512; filter_r<=2'd0; dec_r<=4'd0;
            fifo_mode_r<=2'd0; watermark_r<=15'd16384; hyst_r<='0; hi_r<='0; lo_r<='0;
            event_r<=1'b0; hi_route_r<=1'b0; lo_route_r<=1'b0;
            gpio_dir_r<=4'b0001; gpio_data_r<='0; gpio_sel_r<='0;
            crc_r<=1'b0; send_status_r<=1'b0; strict_r<=1'b1;
            ascend_r<=1'b0; single_r<=1'b0; short_r<=1'b0;
            crc_enable_bits_r<=2'd0; crc_inverse_bits_r<=2'd3;
            scratch_r<='0; reset_armed<=1'b0; por_r<=1'b1; por_analog_r<=1'b1;
            not_ready_r<=1'b0; clock_r<=1'b0; crc_error_r<=1'b0;
            readonly_r<=1'b0; partial_r<=1'b0; address_r<=1'b0;
            hi_clr<=1'b0; lo_clr<=1'b0; fifo_arm<=1'b0; fifo_clear<=1'b0;
            fsync_pulse<=1'b0; sw_reset_pulse<=1'b0;
            cfg_reject<=1'b0; cfg_reject_reason<='0;
        end else if (sw_reset_now) begin
            // 软件复位只重置配置，不清捕获 RAM 或已完成记录的会话状态。
            offset_r<='0; gain_r<=10'd512; filter_r<=2'd0; dec_r<=4'd0;
            fifo_mode_r<=2'd0; watermark_r<=15'd16384; hyst_r<='0; hi_r<='0; lo_r<='0;
            event_r<=1'b0; hi_route_r<=1'b0; lo_route_r<=1'b0;
            gpio_dir_r<=4'b0001; gpio_data_r<='0; gpio_sel_r<='0;
            crc_r<=1'b0; send_status_r<=1'b0; strict_r<=1'b1;
            single_r<=1'b0; short_r<=1'b0;
            crc_enable_bits_r<=2'd0; crc_inverse_bits_r<=2'd3;
            scratch_r<='0; reset_armed<=1'b0; por_r<=1'b1; por_analog_r<=1'b1;
            not_ready_r<=1'b0; clock_r<=1'b0; crc_error_r<=1'b0;
            readonly_r<=1'b0; partial_r<=1'b0; address_r<=1'b0;
            hi_clr<=1'b1; lo_clr<=1'b1; fifo_arm<=1'b0; fifo_clear<=1'b0;
            fsync_pulse<=1'b1; sw_reset_pulse<=1'b1;
            cfg_reject<=1'b0; cfg_reject_reason<='0;
        end else begin
            hi_clr<=1'b0; lo_clr<=1'b0; fifo_arm<=1'b0; fifo_clear<=1'b0;
            fsync_pulse<=1'b0; sw_reset_pulse<=1'b0;
            cfg_reject<=1'b0; cfg_reject_reason<='0;
            if (crc_err_pulse) crc_error_r<=1'b1;
            if (clk_cnt_err_pulse) clock_r<=1'b1;
            if (partial_err_pulse) partial_r<=1'b1;
            if (addr_err_pulse) address_r<=1'b1;
            if (readonly_err_pulse) readonly_r<=1'b1;
            if (not_ready_err_pulse) not_ready_r<=1'b1;
            if (wm_wb_valid) watermark_r<=wm_wb_data;
            if (we) begin
                if (!address_valid) begin address_r<=1'b1; reject_write(REJECT_ADDRESS); end
                else if (!access_supported) reject_write(access_reject_reason);
                else if (read_only) begin readonly_r<=1'b1; reject_write(REJECT_READONLY); end
                else if (write_bytes != entity_bytes) begin partial_r<=1'b1; reject_write(REJECT_PARTIAL); end
                else case (addr)
                    16'h0000: begin
                        if (wdata[7] && wdata[0]) begin
                            if (software_reset_allowed) reset_armed<=1'b1;
                            else reject_write(REJECT_NOT_IDLE);
                        end else if (reset_armed) begin
                            reset_armed<=1'b0;
                            reject_write(REJECT_NOT_IDLE);
                        end else ascend_r<=wdata[5];
                    end
                    16'h0001: begin
                        if (wdata[3] && P_SHORT_ADDR_BITS != 7) reject_write(REJECT_UNBOUND);
                        else begin single_r<=wdata[7]; short_r<=wdata[3]; end
                    end
                    16'h0002: if (wdata[1:0] != 2'd0) reject_write(REJECT_UNSUPPORTED);
                    16'h000A: scratch_r<=wdata[7:0];
                    16'h0010: begin
                        strict_r<=wdata[5]; send_status_r<=wdata[4];
                        crc_enable_bits_r<=wdata[7:6]; crc_inverse_bits_r<=wdata[1:0];
                        crc_r<=(wdata[7:6]==2'b01)&&(wdata[1:0]==2'b10);
                    end
                    16'h0011: begin
                        if (wdata[7]) not_ready_r<=1'b0;
                        if (wdata[4]) clock_r<=1'b0;
                        if (wdata[3]) crc_error_r<=1'b0;
                        if (wdata[2]) readonly_r<=1'b0;
                        if (wdata[1]) partial_r<=1'b0;
                        if (wdata[0]) address_r<=1'b0;
                    end
                    16'h0014: begin
                        if (wdata[5]) hi_clr<=1'b1;
                        if (wdata[4]) lo_clr<=1'b1;
                        if (wdata[3]) por_analog_r<=1'b0;
                        if (wdata[0]) por_r<=1'b0;
                    end
                    16'h0015,16'h0016,16'h0017: reject_write(REJECT_UNSUPPORTED);
                    16'h0019: begin gpio_dir_r<=wdata[3:0]; gpio_data_r<=wdata[7:4]; end
                    16'h001A,16'h001B: begin
                        if (!gpio_function_supported(wdata[3:0]) || !gpio_function_supported(wdata[7:4]))
                            reject_write(REJECT_UNSUPPORTED);
                        else if (addr==16'h001A) begin gpio_sel_r[0]<=wdata[3:0]; gpio_sel_r[1]<=wdata[7:4]; end
                        else begin gpio_sel_r[2]<=wdata[3:0]; gpio_sel_r[3]<=wdata[7:4]; end
                    end
                    16'h001C: begin
                        if (wdata[4]) reject_write(REJECT_UNSUPPORTED);
                        else if (wdata[1:0]!=2'd0 && capture_active) reject_write(REJECT_NOT_IDLE);
                        else if (wdata[1:0]==2'd3 && watermark_r[1:0]!=0) reject_write(REJECT_VALUE);
                        else begin
                            event_r<=wdata[7]; hi_route_r<=wdata[6]; lo_route_r<=wdata[5];
                            fifo_mode_r<=wdata[1:0]; fifo_arm<=wdata[1:0]!=0;
                            fifo_clear<=wdata[1:0]==0;
                        end
                    end
                    16'h001D,16'h001E: begin
                        if (pair_value[15] || (fifo_mode_r==2'd3 && watermark_candidate[1:0]!=0))
                            reject_write(REJECT_VALUE);
                        else watermark_r<=watermark_candidate;
                    end
                    16'h001F,16'h0020: begin
                        if (pair_value[15:11]!=0) reject_write(REJECT_VALUE);
                        else hyst_r<=pair_value[10:0];
                    end
                    16'h0021,16'h0022: begin
                        if (pair_value[15:12]!=0) reject_write(REJECT_VALUE);
                        else hi_r<=pair_value[11:0];
                    end
                    16'h0023,16'h0024: begin
                        if (pair_value[15:12]!=0) reject_write(REJECT_VALUE);
                        else lo_r<=pair_value[11:0];
                    end
                    16'h0025,16'h0026: begin
                        if (pair_value[15:12]!=0) reject_write(REJECT_VALUE);
                        else offset_r<=pair_value[11:0];
                    end
                    16'h0027,16'h0028: begin
                        if (pair_value[15:10]!=0) reject_write(REJECT_VALUE);
                        else if (pair_value[9:0]!=10'd512 &&
                            (!P_GAIN_MAP_VALID || (pair_value[9:0]==0 && !P_GAIN_CODE0_SUPPORTED)))
                            reject_write(REJECT_UNBOUND);
                        else gain_r<=pair_value[9:0];
                    end
                    16'h0029: begin
                        if (wdata[1:0]==2'd3 && !P_COMP_COEFF_VALID) reject_write(REJECT_UNBOUND);
                        else begin filter_r<=wdata[1:0]; dec_r<=wdata[6:3]; fsync_pulse<=1'b1; end
                    end
                    default: begin address_r<=1'b1; reject_write(REJECT_ADDRESS); end
                endcase
            end
        end
    end

    assign status={st_fifo_full,st_fifo_read_done,st_hi_flag,st_lo_flag,por_analog_r,2'b00,por_r};
    assign interface_status={not_ready_r,2'b00,clock_r,crc_error_r,readonly_r,partial_r,address_r};
    assign status_long={2'b00,status[7:4],status[2:1],status[3],status[0],
                        interface_status[7],interface_status[4:0]};
    assign status_short={1'b0,status[0],interface_status[7],interface_status[4:0]};
    assign offset_code=offset_r; assign gain_code=gain_r;
    assign filter_sel=filter_r; assign dec_sel=dec_r;
    assign fifo_mode=fifo_mode_r; assign fifo_watermark=watermark_r;
    assign event_hyst=hyst_r; assign event_hi=hi_r; assign event_lo=lo_r;
    assign event_en=event_r; assign hi_route=hi_route_r; assign lo_route=lo_route_r;
    assign gpio_dir=gpio_dir_r; assign gpio_reg=gpio_data_r; assign gpio_sel=gpio_sel_r;
    assign crc_en=crc_r; assign send_status=send_status_r; assign strict_access=strict_r;
    assign addr_asc=ascend_r; assign single_inst=single_r; assign short_instr=short_r;
endmodule
`default_nettype wire
