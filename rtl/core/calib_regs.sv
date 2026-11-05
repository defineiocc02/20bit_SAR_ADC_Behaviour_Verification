// 模块：calib_regs；显式系数装载、完整性检查与配置生效状态。
// offset/min/max为所选格式的有符号归一化电压；本模块不选择物理窗口或Q格式。
// clk上升沿同步低有效复位；clear失效完成位但保留数值。优先级： reset > clear > validate > write >
// hold。validate与写/忙碰撞保留原ready并报错。
// 仅sel0/1/2合法；不存在旧DEM/bridge/sampling/quantizer控制位或兼容别名。
// 权重和三个标量须在同一epoch写全。片外估计系数，片内仅使用冻结系数。
`timescale 1ns/1ps
`include "rtl_params.vh"
module calib_regs (
    input logic clk,
    input logic rst_n,
    input logic wr_en,
    input logic [1:0] sel,
    input logic signed [V_BITS-1:0] data_v,
    input logic weights_ready,
    input logic config_busy,
    input logic validate,
    input logic clear_valid,
    output logic cfg_ready,
    output logic [31:0] err_code,
    output logic signed [V_BITS-1:0] offset_q,
    output logic signed [V_BITS-1:0] adc2_min_q,
    output logic signed [V_BITS-1:0] adc2_max_q
);
  `include "rtl_error_codes.vh"
  logic [2:0] scalar_written;
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      offset_q <= '0;
      adc2_min_q <= '0;
      adc2_max_q <= '0;
      scalar_written <= '0;
      cfg_ready <= 1'b0;
      err_code <= ERR_NONE;
    end else if (clear_valid) begin
      scalar_written <= '0;
      cfg_ready <= 1'b0;
      err_code <= ERR_NONE;
    end else if (validate) begin
      if (wr_en || config_busy) begin
        err_code <= ERR_CFG_WRITE;
      end else if (!($signed(adc2_min_q) < $signed(adc2_max_q))) begin
        cfg_ready <= 1'b0;
        err_code <= ERR_RANGE_EMPTY;
      end else if (!weights_ready || !(&scalar_written)) begin
        cfg_ready <= 1'b0;
        err_code <= ERR_INCOMPLETE;
      end else begin
        cfg_ready <= 1'b1;
        err_code <= ERR_NONE;
      end
    end else if (wr_en) begin
      if (cfg_ready) begin
        err_code <= ERR_CFG_WRITE;
      end else begin
        case (sel)
          2'd0: begin
            offset_q <= data_v;
            scalar_written[0] <= 1'b1;
          end
          2'd1: begin
            adc2_min_q <= data_v;
            scalar_written[1] <= 1'b1;
          end
          2'd2: begin
            adc2_max_q <= data_v;
            scalar_written[2] <= 1'b1;
          end
          default: err_code <= ERR_CFG_WRITE;
        endcase
      end
    end
  end
endmodule
