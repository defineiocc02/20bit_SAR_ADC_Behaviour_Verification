// 模块：calib_regs；标量/控制配置、完整性校验与配置生效状态。
// 端口/单位：offset/min/max为有符号V/Vfs Q32；四控制位为{quantizer,sampling,bridge,DEM}。
// 时钟/复位：clk上升沿、同步低有效rst_n；clear撤销ready及标量完成位，保留数值供回读。
// 同沿优先级：reset>clear>validate>controls_write>wr_en>保持。
// 提交：无写/忙冲突、权重和三标量完整、min<max且两种dither互斥时validate使ready=1。
// 异常：提交冲突只报ERR_CFG_WRITE并保留ready；其余合法性失败撤销ready；错误码保持至clear/成功提交。
// 写入：ready后拒写；leaf旧sel3..5接口保留，生产顶层由0x1018延迟原子提交全部四控制位。
// 适用域：64位端口类型已限制电压数值范围，JSON解析器负责载入前越界校验；P_ADC2_BITS不参与运算。
// 来源：RTL_ARITHMETIC_CONTRACT.md §4/§5、P2_INTERFACE.md；片外求系数、片上应用。
`include "rtl_params.vh"

module calib_regs #(
    parameter int P_ADC2_BITS = int'(ADC2_BITS)
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      wr_en,          // 一拍脉冲
    input  logic [3:0]                sel,            // 0/1/2: offset/min/max；3/4/5: DEM/bridge/sampling
    input  logic signed [V_BITS - 1:0] data_v,         // sel = 0..2 用
    input  logic                      data_b,         // sel = 3..5 用，其他 sel 报错
    input  logic                      controls_write, // 四个控制位原子更新
    input  logic [3:0]                controls_data,  // {quantizer, sampling, bridge, DEM}
    output logic                      quantizer_dither_en,
    input  logic                      weights_ready,  // 当前载入 epoch 已写满所有权重
    input  logic                      config_busy,    // 顶层写入/串行化/在途工作
    input  logic                      validate,       // 一拍脉冲：跑合法性校验
    input  logic                      clear_valid,    // 一拍脉冲：撤销 cfg_ready
    output logic                      cfg_ready,
    output logic [31:0]               err_code,
    output logic signed [V_BITS - 1:0] offset_q,
    output logic signed [V_BITS - 1:0] adc2_min_q,
    output logic signed [V_BITS - 1:0] adc2_max_q,
    output logic                      dem_en,
    output logic                      bridge_en,
    output logic                      sampling_mask_en
);

  `include "rtl_error_codes.vh"

  // 三个完成位只记录本 epoch 是否写过；系数本身可在提交前重复覆盖。
  logic [2:0] scalar_written;
  logic signed [V_BITS - 1:0] off_r, min_r, max_r;
  logic                     dem_r, brg_r, smk_r, qdith_r;

  assign offset_q           = off_r;
  assign adc2_min_q          = min_r;
  assign adc2_max_q          = max_r;
  assign dem_en             = dem_r;
  assign bridge_en          = brg_r;
  assign sampling_mask_en   = smk_r;
  assign quantizer_dither_en = qdith_r;

  // 唯一时序边界：所有系数、完成位、提交位与错误码均在 posedge 更新。
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      off_r     <= {V_BITS{1'b0}};
      min_r     <= {V_BITS{1'b0}};
      max_r     <= {V_BITS{1'b0}};
      dem_r     <= DEM_ENABLE[0];          // 复位默认0；通过原子控制写启用
      brg_r     <= DEM_BRIDGE_ENABLE[0];   // 复位默认 = 1
      qdith_r   <= (DITHER_MODE == 2);
      smk_r     <= (DITHER_MODE == 3);
      cfg_ready <= 1'b0;
      err_code  <= ERR_NONE;
      scalar_written <= 3'b000;
    end else begin
      // An epoch starts at reset/clear. Commit and writes are mutually
      // exclusive; never validate old values and accept new ones on one edge.
      if (clear_valid) begin
        cfg_ready <= 1'b0;
        scalar_written <= 3'b000;
        err_code <= ERR_NONE;
      end else if (validate) begin
        if (wr_en || controls_write || config_busy) begin
          err_code <= ERR_CFG_WRITE; // reject commit, preserve active config
        end else if (smk_r && qdith_r) begin
          cfg_ready <= 1'b0;
          err_code <= ERR_DITHER_MODE;
        end else if (!($signed(min_r) < $signed(max_r))) begin
          cfg_ready <= 1'b0;
          err_code <= ERR_RANGE_EMPTY;
        end else if (!weights_ready || !(&scalar_written)) begin
          cfg_ready <= 1'b0;
          err_code <= ERR_INCOMPLETE;
        end else begin
          cfg_ready <= 1'b1;
          err_code <= ERR_NONE;
        end
      end else if (controls_write) begin
        if (cfg_ready || wr_en)
          err_code <= ERR_CFG_WRITE;
        else
          {qdith_r, smk_r, brg_r, dem_r} <= controls_data;
      end else if (wr_en) begin
        if (cfg_ready) begin
          err_code <= ERR_CFG_WRITE;
        end else begin
          case (sel)
            4'd0: begin
              off_r <= data_v;
              scalar_written[0] <= 1'b1;
            end
            4'd1: begin
              min_r <= data_v;
              scalar_written[1] <= 1'b1;
            end
            4'd2: begin
              max_r <= data_v;
              scalar_written[2] <= 1'b1;
            end
            4'd3: dem_r <= data_b;
            4'd4: brg_r <= data_b;
            4'd5: smk_r <= data_b;
            default: err_code <= ERR_CFG_WRITE;
          endcase
        end
      end
    end
  end
endmodule
