//===========================================================================
// calib_regs.sv -- 标定系数寄存器 + 合法性校验 + cfg_ready
//===========================================================================
// 职责一句话
//   保存 offset_q / adc2_min_q / adc2_max_q 三个 Q32 电压系数与 dem_en /
//   bridge_en / sampling_mask_en 三个控制位；接受 `validate` 脉冲做合法性校验，
//   通过才把 `cfg_ready` 拉高；`clear_valid` 撤销生效状态以便重载。
//   本模块**不做**权重存储（weight_store 的事）也不做任何算术。
//
// 来源
//   docs/rtl/P2_INTERFACE.md §9（M13）；rtl/README.md §3.2；
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §4.3 / §5；
//   adi_model/fixed_point.FixedPointReconstructor.__post_init__ 的校验口径。
//   寄存器镜像字段名对齐 sim/vectors/registers_*.json。
//
// 单位契约
//   电压系数一律为"归一化到 Vfs"的 Q32 有符号整数（V_BITS = 64）。
//   三个控制位为 0/1。
//
// 参数来源分级
//   P_ADC2_BITS 默认取自 rtl_params.vh（[披露]）。本模块**不消费**该参数 ——
//   它是冻结接口的一部分（P2 §9），为的是让调用方不必记忆"哪些模块需要它"。
//   控制位的复位默认值取头文件的 DEM_ENABLE / DEM_BRIDGE_ENABLE（[推导]）。
//
// 契约与不变量 / 适用域
//   * 读写窗口（P2 §9 表）：`cfg_ready = 0` 且 `wr_en` -> 写入生效；
//     `cfg_ready = 1` 且 `wr_en` -> 写入被忽略、`err_code = ERR_CFG_WRITE`。
//   * validate 仅在无写入、无 config_busy、权重与三标量完整时提交（ADR 0016）。
//     通过则 `cfg_ready <= 1` 且 `err_code <= 0`；
//     失败则 `cfg_ready <= 0` 且 `err_code` 置为对应错误码。
//   * `clear_valid = 1` -> cfg_ready 与 scalar_written 清零（要求完整重载）。它与 `validate` 同拍时
//     **clear 胜**（撤销优先于生效）。
//   * **DEM 陷阱（P1 §0.2）**：`DEM_ENABLE`（头文件）是**复位默认值 0**，而
//     `DEM_BRIDGE_ENABLE` 是 1。看起来"桥接开着"，实际 DEM 不工作。这是**故意的**：
//     运行时必须由软件写 `sel = 3, data_b = 1` 把 dem_en 打开。任何"忘了打开"的
//     联调都会安静地退化（全绿但什么都没覆盖）。
//   * **如实登记：`ERR_V_RANGE` 在本模块内不可能触发。** 三个电压系数的端口宽度
//     恰为 V_BITS 位有符号，`[-2^63, 2^63)` 是**类型系统保证**的，RTL 里不存在
//     可观测的越界输入，加一个恒假的比较器只会浪费面积并制造"检查过了"的假象。
//     该检查在**回读/载入工具**一侧（JSON 解析）落地。同理 `ERR_W_RANGE` /
//     `ERR_W_SUM` 属于权重检查，而本模块的冻结端口表没有权重输入，故由
//     weight_store 在写入点守卫（见其模块头）。详见 sar20_digital_core.sv 的
//     "偏离登记"。
//   * `err_code` 是**寄存器**（保存最近一次错误），不是脉冲；成功 validate 会清它。
//===========================================================================
`include "rtl_params.vh"

module calib_regs #(
    parameter int P_ADC2_BITS = ADC2_BITS
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      wr_en,        // 一拍脉冲
    input  logic [3:0]                sel,          // 0 offset_q / 1 adc2_min_q / 2 adc2_max_q
                                                   // 3 dem_en / 4 bridge_en / 5 sampling_mask_en
    input  logic signed [V_BITS-1:0]  data_v,       // sel <= 2 用
    input  logic                      data_b,       // sel >= 3 用
    input  logic                      weights_ready, // all weights written in this load epoch
    input  logic                      config_busy,   // top-level write/serialization/in-flight work
    input  logic                      validate,     // 一拍脉冲：跑合法性校验
    input  logic                      clear_valid,  // 一拍脉冲：撤销 cfg_ready
    output logic                      cfg_ready,
    output logic [31:0]               err_code,
    output logic signed [V_BITS-1:0]  offset_q,
    output logic signed [V_BITS-1:0]  adc2_min_q,
    output logic signed [V_BITS-1:0]  adc2_max_q,
    output logic                      dem_en,
    output logic                      bridge_en,
    output logic                      sampling_mask_en
);

  // ---- 错误码（RTL 设计选择：P2 §9 给了名字未给数值）----------------------
  // ⚠️ 这份常量在 sar20_digital_core.sv 里有一份**同值副本**（没有可共用的头文件：
  //    rtl/params/ 是生成物，禁止手工追加）。改这里必须同步改那里。
  localparam logic [31:0] ERR_NONE        = 32'd0;
  localparam logic [31:0] ERR_W_RANGE     = 32'd1;   // 由 weight_store 在写入点守卫
  localparam logic [31:0] ERR_W_SUM       = 32'd2;   // 由 weight_store 在写入点守卫
  localparam logic [31:0] ERR_RANGE_EMPTY = 32'd3;   // 非空范围检查
  localparam logic [31:0] ERR_V_RANGE     = 32'd4;   // 类型系统保证，见模块头
  localparam logic [31:0] ERR_CFG_WRITE   = 32'd5;   // cfg_ready=1 时的写被拒

  localparam logic [31:0] ERR_INCOMPLETE = 32'd6;
  logic [2:0] scalar_written;
  logic signed [V_BITS-1:0] off_r, min_r, max_r;
  logic                     dem_r, brg_r, smk_r;

  assign offset_q         = off_r;
  assign adc2_min_q       = min_r;
  assign adc2_max_q       = max_r;
  assign dem_en           = dem_r;
  assign bridge_en        = brg_r;
  assign sampling_mask_en = smk_r;

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      off_r     <= {V_BITS{1'b0}};
      min_r     <= {V_BITS{1'b0}};
      max_r     <= {V_BITS{1'b0}};
      dem_r     <= DEM_ENABLE[0];          // 复位默认 = 0（故意的，见模块头 DEM 陷阱）
      brg_r     <= DEM_BRIDGE_ENABLE[0];   // 复位默认 = 1
      smk_r     <= 1'b0;                   // dither_mode != "sampling"（DITHER_MODE = 0）
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
        if (wr_en || config_busy) begin
          err_code <= ERR_CFG_WRITE; // reject commit, preserve active config
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
      end else if (wr_en) begin
        if (cfg_ready) begin
          err_code <= ERR_CFG_WRITE;
        end else begin
          case (sel)
            4'd0: begin off_r <= data_v; scalar_written[0] <= 1'b1; end
            4'd1: begin min_r <= data_v; scalar_written[1] <= 1'b1; end
            4'd2: begin max_r <= data_v; scalar_written[2] <= 1'b1; end
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
