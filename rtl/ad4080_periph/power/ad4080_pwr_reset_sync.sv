`timescale 1ns/1ps

// =============================================================================
// 文件名     : ad4080_pwr_reset_sync.sv
// 模块名     : ad4080_pwr_reset_sync
// 版本       : v1.0
// 功能       : 在给定的时钟域建立异步断言、同步释放的低有效复位。
//
// 设计说明   :
//   * 目的时钟停止时仍能进入安全状态。恢复时必须在该时钟域获得两个有效沿。
//   * 输入的电源有效信号应先做硬件电平转换和有效态钳位，由接收方的供电域驱动。
//   * 两级属性仅提供实现意图。复位树、亚稳态 MTBF 和 recovery/removal
//     仍须使用目标库及工具验证。用途包含跨域返回的就绪指示。
//
// 变更记录   : 多个独立资格分别同步，再按目的域复位合同合并结果。
// =============================================================================
module ad4080_pwr_reset_sync #(
    parameter int HOLD_COUNT = 1,
    parameter bit RELEASE_ANY = 1'b0,  // 仅用于供电缺失屏蔽参考故障的或关系。
    parameter logic [HOLD_COUNT-1:0] SAMPLE_ONLY = '0 // 数字协议许可由目的域取样。
) (
    // --- 目的时钟与保持条件 ---
    input  logic clk_i,       // 释放所在时钟域；停钟时仍接受异步断言
    input  logic [HOLD_COUNT-1:0] hold_ni, // 每位单独驱动一条目的域释放链。
    output logic ready_no    // 低有效复位，在第二个满足条件的目的时钟沿释放
);
    // =========================================================================
    // 1. 复位保持和释放
    // =========================================================================
    logic [HOLD_COUNT-1:0] released;
    for (genvar source = 0; source < HOLD_COUNT; source++) begin : g_source
        wire hold_one_n = hold_ni[source];
        (* ASYNC_REG = "TRUE" *) logic [1:0] release_q;
        if (SAMPLE_ONLY[source]) begin : g_sampled
            // 最高位是硬复位。已在源域登记的协议许可不驱动异步端。
            wire hard_reset_n = hold_ni[HOLD_COUNT-1];
            always_ff @(posedge clk_i or negedge hard_reset_n) begin
                if (!hard_reset_n) release_q <= '0;
                else release_q <= {release_q[0], hold_one_n};
            end
        end else begin : g_async
            // 独立物理失供可立即清对应两个级。
            always_ff @(posedge clk_i or negedge hold_one_n) begin
                if (!hold_one_n) release_q <= '0;
                else release_q <= {release_q[0], 1'b1};
            end
        end
        assign released[source] = release_q[1];
    end
    assign ready_no = RELEASE_ANY ? (|released) : (&released);
endmodule
