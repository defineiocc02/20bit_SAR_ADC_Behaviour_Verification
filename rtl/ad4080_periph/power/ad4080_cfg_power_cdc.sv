`timescale 1ns/1ps
`default_nettype none

// =============================================================================
// 文件名     : ad4080_cfg_power_cdc.sv
// 模块名     : ad4080_cfg_power_cdc
// 版本       : v1.0
// 功能       : 向 AON 交付一笔完整配置命令，返回执行结果和已生效读回。
// 设计说明   : 两端共用硬 POR，由各自时钟同步释放。任一时钟停止可保留
//              在途请求或应答。AON 采纳沿之后取快照，核心直到真实响应
//              到达才恢复发送。禁止在无确认时重试造成重复提交。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================
module ad4080_cfg_power_cdc (
    // --- 配置 SPI 所在核心域 ---
    input  logic       clk_core_i,           // 可停；握手稳定到该域恢复
    input  logic       rst_core_ni,          // 只接硬 POR 的核心复位
    input  logic       cmd_valid_core_i,     // CRC/长度校验完成的单沿候选
    input  logic [1:0] cmd_op_core_i,        // 0 mode，1 soft reset，2 LDO
    input  logic [1:0] cmd_value_core_i,     // 与操作原子保存
    output logic       cmd_ready_core_o,     // 高时可排队一笔，不代表 AON 已采纳
    output logic       rsp_valid_core_o,     // 结论已到核心，不重复发布
    output logic       rsp_accept_core_o,    // 来自 AON 的执行裁决
    output logic [1:0] rsp_op_core_o,        // 原候选的操作码
    output logic [1:0] mode_core_o,          // 0x02 的已执行模式快照
    output logic [1:0] ldo_pd_core_o,        // 0x18 的已执行配置快照
    // --- 常供域，一次提交一次决议 ---
    input  logic       clk_aon_i,            // 主管提交时钟
    input  logic       rst_aon_ni,           // 同一硬 POR 的 AON 复位
    input  logic       cmd_evaluate_aon_i,   // 同步 CS 退场后允许作出一次裁决
    output logic       cmd_valid_aon_o,      // 只在一个接受沿提供候选
    output logic [1:0] cmd_op_aon_o,        // 经过同步请求后读取保持的包体
    output logic [1:0] cmd_value_aon_o,     // 保持期间不跨拍修改
    input  logic       cmd_accept_aon_i,     // 本沿是否执行
    input  logic [1:0] mode_aon_i,          // 主管实际已提交的模式
    input  logic [1:0] ldo_pd_aon_i         // 主管实际已提交的关断位
);
    // =========================================================================
    // 1. 送出的命令和返回的读回采用各自保持区
    // =========================================================================
    logic req_core_q, outstanding_core_q;
    logic [1:0] op_core_q, value_core_q;
    logic req_aon_taken_q, response_aon_waiting_q, command_accepted_aon_q;
    logic send_aon_q, returned_core_q;
    logic [3:0] status_aon_q;                // 模式与 LDO 字整体保持，避免两位撕裂
    logic report_command_aon_q, accept_aon_q;
    (* ASYNC_REG = "TRUE" *) logic [1:0] req_aon_q, returned_aon_q, send_core_q;

    assign cmd_ready_core_o = rst_core_ni && !outstanding_core_q && !rsp_valid_core_o;
    // SPI 接收器在最后一个 SCLK 采样沿完成实体，可能早于 CS 拉高。
    // CS 退场前保留请求。退场后即由主管裁决空闲及供电条件，忙态仍可拒绝。
    assign cmd_valid_aon_o = rst_aon_ni && cmd_evaluate_aon_i &&
                             (req_aon_q[1] != req_aon_taken_q) && !response_aon_waiting_q;
    assign cmd_op_aon_o = op_core_q;
    assign cmd_value_aon_o = value_core_q;

    // =========================================================================
    // 2. AON 用采纳后的真实状态组装回执
    // =========================================================================
    always_ff @(posedge clk_aon_i or negedge rst_aon_ni) begin
        if (!rst_aon_ni) begin
            req_aon_q <= '0;
            returned_aon_q <= '0;
            req_aon_taken_q <= 1'b0;
            response_aon_waiting_q <= 1'b0;
            command_accepted_aon_q <= 1'b0;
            send_aon_q <= 1'b0;
            status_aon_q <= '0;
            report_command_aon_q <= 1'b0;
            accept_aon_q <= 1'b0;
        end else begin
            req_aon_q <= {req_aon_q[0], req_core_q};
            returned_aon_q <= {returned_aon_q[0], returned_core_q};
            if (cmd_valid_aon_o) begin
                req_aon_taken_q <= req_aon_q[1];
                response_aon_waiting_q <= 1'b1;
                command_accepted_aon_q <= cmd_accept_aon_i;
            end
            // 至少后一个 AON 沿才取已更新状态。既有状态包尚未确认时
            // 回执在单笔槽等待，主管仍只执行一次。外部管理改变也会刷新读回。
            if (send_aon_q == returned_aon_q[1] &&
                (response_aon_waiting_q || status_aon_q != {mode_aon_i, ldo_pd_aon_i})) begin
                status_aon_q <= {mode_aon_i, ldo_pd_aon_i};
                report_command_aon_q <= response_aon_waiting_q;
                accept_aon_q <= command_accepted_aon_q;
                send_aon_q <= !send_aon_q;
                if (response_aon_waiting_q) response_aon_waiting_q <= 1'b0;
            end
        end
    end

    // =========================================================================
    // 3. 核心只在持有真实回执时解除在途保护
    // =========================================================================
    always_ff @(posedge clk_core_i or negedge rst_core_ni) begin
        if (!rst_core_ni) begin
            req_core_q <= 1'b0;
            outstanding_core_q <= 1'b0;
            op_core_q <= '0;
            value_core_q <= '0;
            send_core_q <= '0;
            returned_core_q <= 1'b0;
            rsp_valid_core_o <= 1'b0;
            rsp_accept_core_o <= 1'b0;
            rsp_op_core_o <= '0;
            mode_core_o <= '0;
            ldo_pd_core_o <= '0;
        end else begin
            send_core_q <= {send_core_q[0], send_aon_q};
            rsp_valid_core_o <= 1'b0;
            if (cmd_valid_core_i && cmd_ready_core_o) begin
                req_core_q <= !req_core_q;
                outstanding_core_q <= 1'b1;
                op_core_q <= cmd_op_core_i;
                value_core_q <= cmd_value_core_i;
            end
            // 两级通知稳定后才取 AON 保持的字。未返回确认前源不得更新该字，
            // 同时也不覆盖仍须用来匹配回复的核心操作码。
            if (send_core_q[1] != returned_core_q) begin
                {mode_core_o, ldo_pd_core_o} <= status_aon_q;
                returned_core_q <= send_core_q[1];
                if (report_command_aon_q) begin
                    rsp_valid_core_o <= 1'b1;
                    rsp_accept_core_o <= accept_aon_q;
                    rsp_op_core_o <= op_core_q;
                    outstanding_core_q <= 1'b0;
                end
            end
        end
    end
endmodule

`default_nettype wire
