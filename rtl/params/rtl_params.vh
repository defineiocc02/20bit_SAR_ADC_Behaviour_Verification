// ---------------------------------------------------------------------------
// rtl_params.vh —— 由 tools/export_rtl_params.py 自动生成，请勿手工编辑。
// config      : paper_literal
// revision    : v8.0.0-1-g517bd2f (517bd2f92b67fae73b6e2346bc42d88e5ee514de)
// dirty tree  : 1
// fixed point : W_FRAC/Q30 W_BITS/48 V_FRAC/Q32 V_BITS/64 ACC_BITS/96 OUT_BITS/20
// payload sha : 39b12360d1858e7cfbe05f18614d5d2f270e11627fda33b942b00f4a266d83a8
// 算术契约    : docs/rtl/RTL_ARITHMETIC_CONTRACT.md（冻结；改动须走新 ADR）
// ---------------------------------------------------------------------------
`ifndef RTL_PARAMS_VH
`define RTL_PARAMS_VH

// ---- 定点格式 —— docs/rtl/RTL_ARITHMETIC_CONTRACT.md §2 ----
localparam [5:0] W_FRAC = 6'd30;  // derived: ADR 0014
localparam [6:0] W_BITS = 7'd48;  // derived: ADR 0014
localparam [5:0] V_FRAC = 6'd32;  // derived: ADR 0014
localparam [6:0] V_BITS = 7'd64;  // derived: ADR 0014
localparam [7:0] ACC_BITS = 8'd96;  // derived: ADR 0014
localparam [5:0] OUT_BITS = 6'd20;  // derived: ADR 0014

// ---- 阵列几何 ----
localparam [4:0] N_SLICES = 5'd18;  // derived: config.n_slices
localparam [3:0] N_ACTIVE = 4'd8;  // derived: config.n_active
localparam [6:0] N_UNIT_MAIN = 7'd63;  // derived: config.dac_n_main
localparam [4:0] N_UNIT_SUB = 5'd8;  // derived: config.dac_n_sub
localparam [6:0] N_UNIT_TOTAL = 7'd71;  // derived: dac_n_main + dac_n_sub
localparam [9:0] DAC_LEVELS = 10'd512;  // derived: config.dac_levels
localparam [0:0] DAC_COMPLETE_RANGE = 1'd1;  // derived: config

// ---- 码栅格（唯一真相源，勿在 RTL 内重复推导：ADR 0004） ----
localparam [3:0] B1 = 4'd9;  // derived: config.b1
localparam [9:0] STAGE1_LEVELS = 10'd512;  // derived: config.stage1_levels
localparam [9:0] N_UNITS_SIG = 10'd512;  // derived: config.n_units_sig
localparam [9:0] UNITS_PER_LSB1 = 10'd1;  // derived: config.units_per_lsb1
localparam [9:0] UNITS_PER_D1 = 10'd1;  // derived: sadc
localparam [9:0] N_UNITS_HEADROOM = 10'd0;  // derived: config
localparam [9:0] K0 = 10'd0;  // derived: mapper.encode
localparam [4:0] ADC2_BITS = 5'd12;  // derived: config.adc2_n_bits

// ---- DEM 状态机与开关置换几何（dem.py 为开关译码主路径） ----
localparam [9:0] DEM_STATES = 10'd512;  // derived: mapper.N_DEM_STATES
localparam [31:0] DEM_LCG_A = 32'd2654435761;  // derived: mapper._LCG_A
localparam [9:0] DEM_LCG_A_MOD = 10'd433;  // derived: mapper._LCG_A mod mapper.N_DEM_STATES - RTL 只需低位，勿手算
localparam [3:0] DEM_ROT_WIDTH = 4'd8;  // derived: dem.py: ceil(sqrt(n_main))
localparam [3:0] DEM_ROT_HEIGHT = 4'd8;  // derived: dem.py: ceil(n_main/width)
localparam [4:0] DEM_SUB_ROLL = 5'd8;  // derived: dem.py: sub_order period
localparam [0:0] DEM_ENABLE = 1'd0;  // derived: config.dem_enable - 复位默认值；运行时由寄存器覆盖
localparam [0:0] DEM_BRIDGE_ENABLE = 1'd1;  // derived: config.dem_bridge_enable - 复位默认值；仅当 DEM_ENABLE 同时为 1 才生效

// ---- dither 模式与掩码 ----
localparam [1:0] DITHER_MODE = 2'd0;  // derived: 0 off/1 analog/2 quantizer/3 sampling
localparam [3:0] DITHER_UNITS_TOTAL = 4'd0;  // derived: config.dither_units_total
localparam [0:0] DITHER_SPLIT_IS_SUB = 1'd1;  // derived: config.dither_split_bank
localparam [0:0] DITHER_DISCRETE = 1'd1;  // derived: config.dither_discrete
localparam [3:0] DITHER_UNITS_RANGE = 4'd2;  // derived: config.dither_units_range

// ---- RTL 专有量 [假设] —— 非模型参数，改动需同步计划与 ADR ----
localparam [5:0] PHASES = 6'd16;  // assumed: RTL 设计选择，见计划 §4.2

`endif  // RTL_PARAMS_VH
