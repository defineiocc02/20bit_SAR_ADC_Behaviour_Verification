// =============================================================================
// 文件名     : ad4080_regs.sv
// 模块名     : ad4080_regs
// 版本       : v1.0
// 功能       : 提交完整 SPI 寄存器实体，保存已显式绑定的数值和器件事件功能。
// 设计说明   :
//   * SPI 缓存严格式双字节实体。写拒绝仅更新诊断，不污染已有寄存器。
//   * 软件复位必须同时满足主机停收、硬件排空和记录状态资格。
//   * 保留原维护者与 Git 来源。外部物理相序及未公开定点映射须显式绑定。
//-----------------------------------------------------------------------------
// 手册依据: AD4080 Rev.B pp.27,35–42,73–94；A01–A11 仍需外部明确绑定。
// 时钟域: clk；寄存器事务、GPIO 回读与捕获状态输入均同步。
// 复位: 硬件复位或合法的两次写软件复位；W1C 清除优先于同沿状态置位。
// 延迟: 读数据与访问元数据为组合逻辑；写入在 we 被接受的时钟沿提交。
// 吞吐: 每个 clk 最多接受一个完整寄存器实体。
// 可综合性: 可综合结构；工艺映射、约束与 STA 需分别验收。
// 依赖: 无子模块。
// 边界: cfg_reject 保持在工程侧。0x14 的转换错误只来自已提供的物理间隔监测。
// 变更记录   : 按明确时基开放过滤 RDY，新增 ADC_CNV_ERR sticky 与同沿 W1C 规则。
// =============================================================================
`timescale 1ns/1ps
`default_nettype none

module ad4080_regs #(
    parameter int P_MULTIBYTE_ORDER = -1,
    parameter int P_OFFSET_LSB_ADDR = -1,
    parameter int P_PRODUCT_ID_L = -1,
    parameter int P_SHORT_ADDR_BITS = -1,
    parameter bit P_GAIN_MAP_VALID = 1'b0,
    parameter bit P_GAIN_CODE0_SUPPORTED = 1'b0,
    parameter bit P_CONTINUOUS_LVDS_BOUND = 1'b0, // 存在连续 LVDS 时隙发射端才开放 A/B 配置。
    parameter bit P_CNV_TIMING_BOUND = 1'b0,   // 接入已有 CNV 定相逻辑才开放 GPIO RDY。
    parameter bit P_CNV_ERROR_BOUND = 1'b0,    // 仅可信同域 tCONV 监测提供器件错误事件。
    parameter bit P_POR_ANA_EVENT_ENABLE = 1'b0, // 仅绑定已跨域交付的实际参考故障脉冲。
    parameter bit P_AON_CFG_BOUND = 1'b0,       // 真正 AON 裁决后提交 0x00/02/18。
    parameter bit P_ROM_CRC_ERROR_BOUND = 1'b0  // 仅绑实际 ROM 校验输出。
) (
    // --- 严格实体协议 ---
    input  logic clk, rst_n,                 // CSR 与 SPI 已在同一核心域。
    input  logic [15:0] addr, wdata,          // addr 与整个写实体同时有效。
    input  logic [1:0] write_bytes,          // 大小不符不得只写首字节。
    input  logic we,                        // SPI 已收齐实体并成功完成该帧约束。
    output logic [15:0] rdata,               // 根据实体首地址、方向和字节序解读。
    output logic [1:0] entity_bytes,         // 指定 SPI 为当前访问保留的原子长度。
    output logic address_valid, read_only, access_supported,
    output logic [3:0] access_reject_reason,
    // --- 可提交配置的控制状态与时序错误 ---
    input  logic adc_idle,                   // 由上游显式给停收排空确认，不能用空拍代替。
    input  logic data_config_ready_i,         // 前次配置已经跨域受理；传输中拒绝下一次变更。
    output logic [7:0] data_intf_config_a_o, data_intf_config_b_o, data_intf_config_c_o,
    input  logic [3:0] gpio_levels,           // 脚的观察值，与 GPIO_DATA 输出寄存器不同。
    input  logic capture_active, capture_incomplete, capture_io_active, // 锁配置、保护半记录、仲裁读回三种资格。
    input  logic st_fifo_full, st_fifo_read_done, st_hi_flag, st_lo_flag, // 均为适合 CSR 的核心域视图。
    input  logic conversion_timing_error_i,   // 每个高电平核心沿表示一个新的短间隔错误。
    output logic cnv_error_alert_o,            // 同一 sticky 经 route 后供 GPIO 与状态读取。
    input  logic crc_err_pulse, clk_cnt_err_pulse, partial_err_pulse,
    input  logic addr_err_pulse, readonly_err_pulse, not_ready_err_pulse,
    // --- 同域消费者的配置与清流 ---
    output logic hi_clr, lo_clr,               // 独立 W1C，事件模块决定同沿电平/脉冲。
    output logic signed [11:0] offset_code,   // ADC LSB 为单位，带符号。
    output logic [9:0] gain_code,             // 非 0x200 必须显式提供工程比例。
    output logic [1:0] filter_sel,             // 定相模式只给可满足真实结果期限的支路。
    output logic [3:0] dec_sel,                // 保留寄存器原码，实际抽取器负责钳位。
    output logic fsync_pulse,                  // CSR 写后清滤波状态，不替代物理 SYNC 锚点。
    output logic [1:0] fifo_mode,
    output logic [14:0] fifo_watermark,
    output logic fifo_arm, fifo_clear,         // 一次实体提交对应一个控制脉冲。
    input  logic wm_wb_valid,
    input  logic [14:0] wm_wb_data,            // 环形模式记录完成时回写位置。
    output logic [10:0] event_hyst,
    output logic [11:0] event_hi, event_lo,    // 比较器中显式按二补码解释这两个编码。
    output logic event_en, hi_route, lo_route,
    output logic [3:0] gpio_dir, gpio_reg,
    output logic [3:0][3:0] gpio_sel,
    output logic crc_en, send_status, addr_asc, single_inst, short_instr,
    output logic strict_access, sw_reset_pulse,
    output logic [7:0] status, interface_status,
    output logic [15:0] status_long,
    output logic [7:0] status_short,
    output logic cfg_reject,
    output logic [3:0] cfg_reject_reason,
    input  logic por_ana_event_i = 1'b0,      // 核心域单拍，软件和硬 POR 初值仍由本 bank 设置。
    input  logic rom_crc_error_i = 1'b0,      // 实际 ROM 故障的同域只读电平。
    input  logic pwr_cfg_full_allow_i = 1'b0,  // 无 IOVDD 时只允许管理写，SDO 另行关闭。
    input  logic pwr_init_poll_i = 1'b0,       // 真实软初始化期间只读 0x11 的资格。
    input  logic pwr_cmd_ready_i = 1'b0,      // 允许本次登记，不等同于 AON 执行。
    output logic pwr_cmd_valid_o,             // 一次 CRC/实体检查后的候选。
    output logic [1:0] pwr_cmd_op_o,          // 0 mode，1 reset，2 LDO。
    output logic [1:0] pwr_cmd_value_o,       // 候选管理字段。
    input  logic pwr_rsp_valid_i = 1'b0,      // 唯一实际 AON 结果。
    input  logic pwr_rsp_accept_i = 1'b0,
    input  logic [1:0] pwr_rsp_op_i = 2'b00,
    input  logic [1:0] pwr_mode_i = 2'b00,    // AON 真实已提交读回。
    input  logic [1:0] pwr_ldo_pd_i = 2'b00
);
    localparam logic [3:0] REJECT_UNBOUND=4'd1, REJECT_UNSUPPORTED=4'd2,
        REJECT_ADDRESS=4'd3, REJECT_READONLY=4'd4, REJECT_PARTIAL=4'd5,
        REJECT_NOT_IDLE=4'd8, REJECT_VALUE=4'd9;
    logic signed [11:0] offset_r;
    logic [9:0] gain_r;
    logic [1:0] filter_r, fifo_mode_r;
    logic [3:0] dec_r;
    logic [14:0] watermark_r;                  // 装载值含 16384，不能用 14 位地址表示。
    logic [10:0] hyst_r;
    logic [11:0] hi_r, lo_r;
    logic event_r, hi_route_r, lo_route_r, cnv_route_r;
    logic cnv_error_r;                        // 遮罩只改变可见性，不丢弃已发生事件。
    logic [3:0] gpio_dir_r, gpio_data_r;
    logic [3:0][3:0] gpio_sel_r;
    logic crc_r, send_status_r, strict_r, ascend_r, single_r, short_r;
    logic [1:0] crc_enable_bits_r, crc_inverse_bits_r;
    logic [7:0] scratch_r;
    // 所有权未确定时保留已确认的整字配置，拒绝跨域过程中覆盖下一次启动用的时隙。
    wire config_edit_idle = (adc_idle === 1'b1) && (data_config_ready_i === 1'b1) &&
                            !capture_active && !capture_incomplete && !capture_io_active;
    logic reset_armed, por_r, por_analog_r;
    logic not_ready_r, clock_r, crc_error_r, readonly_r, partial_r, address_r;
    logic [15:0] pair_value;
    wire [14:0] watermark_candidate = clamp_watermark(pair_value[14:0]);
    wire software_reset_allowed = (adc_idle === 1'b1) && !capture_incomplete && !capture_io_active;
    wire sw_reset_request = we && write_bytes==2'd1 && addr == 16'h0000 && reset_armed &&
                            !(wdata[7] && wdata[0]);
    wire power_cfg_idle = software_reset_allowed && !capture_active;
    wire remote_reset = pwr_rsp_valid_i && pwr_rsp_accept_i && pwr_rsp_op_i == 2'd1;
    wire sw_reset_now = P_AON_CFG_BOUND ? remote_reset : (sw_reset_request && software_reset_allowed);
    wire mode_value_ok = wdata[7:2] == '0 && wdata[1:0] != 2'b01;
    wire ldo_value_ok = wdata[7:2] == '0;
    // [电源命令] 0x02 允许已完成、尚未回读的记录。此时 capture_active
    // 仍保留读口所有权，用未完成采集和正在读出分别表示不能切换的状态。
    // 0x18 和 AON 软复位继续要求整个会话释放。命令资格由此唯一译码，
    // 拒绝路径从同一个 valid 和队列 ready 判断，避免 bank 与 AON 受理不一致。
    assign pwr_cmd_valid_o = P_AON_CFG_BOUND && we && write_bytes == 2'd1 && access_supported &&
                             ((addr == 16'h0002 && mode_value_ok && software_reset_allowed) ||
                              (power_cfg_idle && (sw_reset_request || (addr == 16'h0018 && ldo_value_ok))));
    assign pwr_cmd_op_o = addr == 16'h0002 ? 2'd0 : (addr == 16'h0000 ? 2'd1 : 2'd2);
    assign pwr_cmd_value_o = wdata[1:0];
    wire cnv_error_w1c = we && write_bytes == 2'd1 && addr == 16'h0014 && wdata[2];
    wire conversion_cfg_locked = P_CNV_TIMING_BOUND && capture_active;

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
        // 旧数值用例保持原拒绝语义。只有显式选择定相路径才使 RDY 可到达 GPIO。
        // Rev. B 表 53/54 明确勿用 0xA。合法的 0x3 仍须由 CNV 时基绑定。
        gpio_function_supported = (value <= 4'd9) && (value != 4'd3 || P_CNV_TIMING_BOUND);
    endfunction

    // =========================================================================
    // 1. 实体顺序和只读视图
    // =========================================================================
    // 严格读写从该方向的第一个地址开始。未绑定次序时拒绝整个实体，避免
    // 高/低地址交叉覆盖阈值或校准字。同一实体的字节数也给 SPI 层用于收齐。
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

    // 非严格式仍保留另一半内容。严格式则按本事务的首字节方向还原数值。
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

    // 读回暴露当前实际寄存器，保留位在此固定为零。
    always_comb begin
        rdata='0; entity_bytes=2'd1; address_valid=1'b1; read_only=1'b0;
        access_supported=1'b1; access_reject_reason=4'd0;
        case (addr)
            16'h0000: rdata={8'd0,2'b00,ascend_r,1'b1,4'd0};
            16'h0001: rdata={8'd0,single_r,3'd0,short_r,3'd0};
            16'h0002: rdata=P_AON_CFG_BOUND ? {14'd0,pwr_mode_i} : 16'd0;
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
            16'h000E,16'h000F: rdata=16'h0000;
            16'h0010: rdata={8'd0,crc_enable_bits_r,strict_r,send_status_r,2'd0,crc_inverse_bits_r};
            16'h0011: rdata={8'd0,interface_status};
            16'h0014: rdata={8'd0,status};
            16'h0015: rdata={8'd0,data_intf_config_a_o};
            16'h0016: rdata={8'd0,data_intf_config_b_o};
            16'h0017: rdata={8'd0,data_intf_config_c_o};
            16'h0018: begin
                if (P_AON_CFG_BOUND) rdata={14'd0,pwr_ldo_pd_i};
                else begin address_valid=1'b0; access_supported=1'b0; access_reject_reason=REJECT_ADDRESS; end
            end
            16'h0019: rdata={8'd0,gpio_levels,gpio_dir_r};
            16'h001A: rdata={8'd0,gpio_sel_r[1],gpio_sel_r[0]};
            16'h001B: rdata={8'd0,gpio_sel_r[3],gpio_sel_r[2]};
            16'h001C: rdata={8'd0,event_r,hi_route_r,lo_route_r,cnv_route_r,2'd0,fifo_mode_r};
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
        if (P_AON_CFG_BOUND && (pwr_init_poll_i === 1'b1)) begin
            if (addr == 16'h0011) read_only=1'b1;
            else begin access_supported=1'b0; access_reject_reason=REJECT_NOT_IDLE; end
        end else if (P_AON_CFG_BOUND && pwr_cfg_full_allow_i !== 1'b1 && addr != 16'h0000 && addr != 16'h0002) begin
            access_supported=1'b0; access_reject_reason=REJECT_NOT_IDLE;
        end
    end

    // 单份实体合并值用于范围检查及写入，防止部分合并后检查的口径不一致。
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

    // =========================================================================
    // 2. 同沿优先级与寄存器提交
    // =========================================================================
    task automatic reject_write(input logic [3:0] reason);
        begin cfg_reject<=1'b1; cfg_reject_reason<=reason; end
    endtask

    // -------------------------------------------------------------------------
    // Rev. B p.83 表 47 的 sticky 和 p.90 表 55 的 route 各自保持。遮罩不吞掉
    // 已发生错误，重新放行可观察它。新错误与 0x14[2] W1C 同沿时保留新错误，
    // 使尚未观察的短 CNV 不丢失。HI/LO 的清除仍由原事件模块独立处理。
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) cnv_error_r <= 1'b0;
        else if (sw_reset_now) cnv_error_r <= 1'b0;
        else if (P_CNV_ERROR_BOUND && conversion_timing_error_i) cnv_error_r <= 1'b1;
        else if (cnv_error_w1c) cnv_error_r <= 1'b0;
    end
    assign cnv_error_alert_o = P_CNV_ERROR_BOUND && cnv_route_r && cnv_error_r;

    // 特殊的两次写软复位优先于普通实体提交。只有现有捕获完整且外部声明
    // 停机、无读写活动才开始或完成软件序列。
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            offset_r<='0; gain_r<=10'd512; filter_r<=2'd0; dec_r<=4'd0;
            fifo_mode_r<=2'd0; watermark_r<=15'd16384; hyst_r<='0; hi_r<='0; lo_r<='0;
            event_r<=1'b0; hi_route_r<=1'b0; lo_route_r<=1'b0; cnv_route_r<=1'b0;
            gpio_dir_r<=4'b0001; gpio_data_r<='0; gpio_sel_r<='0;
            crc_r<=1'b0; send_status_r<=1'b0; strict_r<=1'b1;
            ascend_r<=1'b0; single_r<=1'b0; short_r<=1'b0;
            crc_enable_bits_r<=2'd0; crc_inverse_bits_r<=2'd3;
            data_intf_config_a_o<=8'h40; data_intf_config_b_o<=8'h00; data_intf_config_c_o<=8'h20;
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
            event_r<=1'b0; hi_route_r<=1'b0; lo_route_r<=1'b0; cnv_route_r<=1'b0;
            gpio_dir_r<=4'b0001; gpio_data_r<='0; gpio_sel_r<='0;
            crc_r<=1'b0; send_status_r<=1'b0; strict_r<=1'b1;
            single_r<=1'b0; short_r<=1'b0;
            crc_enable_bits_r<=2'd0; crc_inverse_bits_r<=2'd3;
            data_intf_config_a_o<=8'h40; data_intf_config_b_o<=8'h00; data_intf_config_c_o<=8'h20;
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
            // 环形快照的写回先处理。同沿若存在合法主机新配置，普通写分支为最终值。
            if (wm_wb_valid) watermark_r<=wm_wb_data;
            if (we) begin
                // 共同校验失败不进地址写分支，保护所有已提交配置。
                if (!address_valid) begin address_r<=1'b1; reject_write(REJECT_ADDRESS); end
                else if (!access_supported) reject_write(access_reject_reason);
                else if (read_only) begin readonly_r<=1'b1; reject_write(REJECT_READONLY); end
                else if (write_bytes != entity_bytes) begin partial_r<=1'b1; reject_write(REJECT_PARTIAL); end
                else case (addr)
                    16'h0000: begin
                        if (wdata[7] && wdata[0]) begin
                            if (software_reset_allowed && (!P_AON_CFG_BOUND || (power_cfg_idle && pwr_cmd_ready_i)))
                                reset_armed<=1'b1;
                            else begin
                                if (P_AON_CFG_BOUND) reset_armed<=1'b0;
                                reject_write(REJECT_NOT_IDLE);
                            end
                        end else if (reset_armed) begin
                            // 唯一的第二步写消耗 arm。只有 AON 真实接受才
                            // 在回执沿执行 bank 软复位，拒收不改保留配置。
                            reset_armed<=1'b0;
                            if (!pwr_cmd_valid_o || !pwr_cmd_ready_i) reject_write(REJECT_NOT_IDLE);
                        end else ascend_r<=wdata[5];
                    end
                    16'h0001: begin
                        if (wdata[3] && P_SHORT_ADDR_BITS != 7) reject_write(REJECT_UNBOUND);
                        else begin single_r<=wdata[7]; short_r<=wdata[3]; end
                    end
                    16'h0002: begin
                        if (!P_AON_CFG_BOUND) begin
                            if (wdata[1:0] != 2'd0) reject_write(REJECT_UNSUPPORTED);
                        end else if (!mode_value_ok) reject_write(REJECT_VALUE);
                        else if (!pwr_cmd_valid_o || !pwr_cmd_ready_i) reject_write(REJECT_NOT_IDLE);
                    end
                    16'h0018: begin
                        if (!ldo_value_ok) reject_write(REJECT_VALUE);
                        else if (!pwr_cmd_valid_o || !pwr_cmd_ready_i) reject_write(REJECT_NOT_IDLE);
                    end
                    16'h000A: scratch_r<=wdata[7:0];
                    // Rev. B p80 与表43/44 对非零循环的支持相冲突。
                    // 两个寄存器仍为已定义的读零、可写复位值的实体。
                    16'h000E,16'h000F: if (wdata[7:0] != 8'd0) reject_write(REJECT_UNSUPPORTED);
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
                    // 表48：bit0 选择结果 SPI，bit2 选择各接口的 lane 数，bit4 固定字仅用于 LVDS。
                    // 两字节分属独立寄存器，逐次事务都必须给出表20中合法的 lane/CNT 组合。
                    // 配置从一整组跨域快照驱动收发端；非法组合保留旧配置。bit6 按表48恒置1。
                    16'h0015: begin
                        if (!P_CONTINUOUS_LVDS_BOUND || (wdata[7:0] & 8'hEA) != 8'h40 || (wdata[0] && wdata[4]))
                            reject_write(REJECT_UNSUPPORTED);
                        else if (!wdata[0] && ((wdata[2] && data_intf_config_b_o[7:4] > 4'd4) ||
                                 (data_intf_config_b_o[2] && (!wdata[2] || filter_r != 0 || fifo_mode_r != 0))))
                            reject_write(REJECT_VALUE);
                        else if (wdata[7:0] != data_intf_config_a_o && !config_edit_idle)
                            reject_write(REJECT_NOT_IDLE);
                        else data_intf_config_a_o <= wdata[7:0];
                    end
                    // 表49：位3关返回钟，位2只在双lane直通转换时启用802.3线码，位0控制物理CNV接收选择。
                    16'h0016: begin
                        if (!P_CONTINUOUS_LVDS_BOUND || wdata[1] ||
                            (wdata[2] && (data_intf_config_a_o[0] || !data_intf_config_a_o[2] || filter_r != 0 || fifo_mode_r != 0)))
                            reject_write(REJECT_UNSUPPORTED);
                        else if (!data_intf_config_a_o[0] && wdata[7:4] > (data_intf_config_a_o[2] ? 4'd4 : 4'd9))
                            reject_write(REJECT_VALUE);
                        else if (wdata[7:0] != data_intf_config_b_o && !config_edit_idle)
                            reject_write(REJECT_NOT_IDLE);
                        else data_intf_config_b_o <= wdata[7:0];
                    end
                    16'h0017: begin
                        // 表50规定非法 VOD 字段仍可原样读回，输出驱动端另选默认档。
                        if (!P_CONTINUOUS_LVDS_BOUND || wdata[3:0] != 0) reject_write(REJECT_UNSUPPORTED);
                        else if (wdata[7:0] != data_intf_config_c_o && !config_edit_idle)
                            reject_write(REJECT_NOT_IDLE);
                        else data_intf_config_c_o <= wdata[7:0];
                    end
                    16'h0019: begin gpio_dir_r<=wdata[3:0]; gpio_data_r<=wdata[7:4]; end
                    16'h001A,16'h001B: begin
                        if (!gpio_function_supported(wdata[3:0]) || !gpio_function_supported(wdata[7:4]))
                            reject_write(REJECT_UNSUPPORTED);
                        else if (addr==16'h001A) begin gpio_sel_r[0]<=wdata[3:0]; gpio_sel_r[1]<=wdata[7:4]; end
                        else begin gpio_sel_r[2]<=wdata[3:0]; gpio_sel_r[3]<=wdata[7:4]; end
                    end
                    16'h001C: begin
                        if (wdata[4] && !P_CNV_ERROR_BOUND) reject_write(REJECT_UNSUPPORTED);
                        else if (P_CONTINUOUS_LVDS_BOUND && !data_intf_config_a_o[0] && data_intf_config_b_o[2] &&
                                 wdata[1:0] != 0) reject_write(REJECT_UNSUPPORTED);
                        else if (wdata[1:0]!=2'd0 && capture_active) reject_write(REJECT_NOT_IDLE);
                        else if (wdata[1:0]==2'd3 && watermark_r[1:0]!=0) reject_write(REJECT_VALUE);
                        else begin
                            event_r<=wdata[7]; hi_route_r<=wdata[6]; lo_route_r<=wdata[5];
                            cnv_route_r<=wdata[4];
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
                        if (conversion_cfg_locked) reject_write(REJECT_NOT_IDLE);
                        else if (pair_value[15:12]!=0) reject_write(REJECT_VALUE);
                        else offset_r<=pair_value[11:0];
                    end
                    16'h0027,16'h0028: begin
                        if (conversion_cfg_locked) reject_write(REJECT_NOT_IDLE);
                        else if (pair_value[15:10]!=0) reject_write(REJECT_VALUE);
                        else if (pair_value[9:0]!=10'd512 &&
                            (!P_GAIN_MAP_VALID || (pair_value[9:0]==0 && !P_GAIN_CODE0_SUPPORTED)))
                            reject_write(REJECT_UNBOUND);
                        else gain_r<=pair_value[9:0];
                    end
                    16'h0029: begin
                        // 0x29 会清流水并更换唯一 PF1 的档位。采集或未读完期间
                        // 保留本批配置，避免将两种模式或系数拼入同一记录。
                        if (P_CONTINUOUS_LVDS_BOUND && !data_intf_config_a_o[0] && data_intf_config_b_o[2] &&
                            wdata[1:0] != 0) reject_write(REJECT_UNSUPPORTED);
                        else if (P_CONTINUOUS_LVDS_BOUND && {wdata[6:3],wdata[1:0]} != {dec_r,filter_r} &&
                                 !config_edit_idle) reject_write(REJECT_NOT_IDLE);
                        else if (conversion_cfg_locked) reject_write(REJECT_NOT_IDLE);
                        else begin filter_r<=wdata[1:0]; dec_r<=wdata[6:3]; fsync_pulse<=1'b1; end
                    end
                    default: begin address_r<=1'b1; reject_write(REJECT_ADDRESS); end
                endcase
            end
            // Rev. B p.24 和表 47。一个新参考故障与 bit3 W1C 同沿时，
            // 末次赋值保留新事件。公开 ROM 状态位的来源独立于参考事件。
            if (P_POR_ANA_EVENT_ENABLE && (por_ana_event_i === 1'b1)) por_analog_r<=1'b1;
            // 来自 AON 的拒收可能在 SPI 帧后才到达，仍应送工程诊断。
            if (P_AON_CFG_BOUND && pwr_rsp_valid_i && !pwr_rsp_accept_i) reject_write(REJECT_NOT_IDLE);
        end
    end

    assign status={st_fifo_full,st_fifo_read_done,st_hi_flag,st_lo_flag,por_analog_r,cnv_error_alert_o,
                   (P_ROM_CRC_ERROR_BOUND && rst_n && (rom_crc_error_i === 1'b1)),por_r};
    // p.28 的忙状态随真实初始化结束；表46 的违规事件仍由独立 W1C 清。
    assign interface_status={(not_ready_r || (P_AON_CFG_BOUND && (pwr_init_poll_i === 1'b1))),
                             2'b00,clock_r,crc_error_r,readonly_r,partial_r,address_r};
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
