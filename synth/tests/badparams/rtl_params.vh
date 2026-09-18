// ---------------------------------------------------------------------------
// synth/tests/badparams/rtl_params.vh
// ---------------------------------------------------------------------------
// ⚠️ 这是**故意改坏的**参数头文件，只用于证明"DC 侧参数一致性检查能变红"。
//    除了 PHASES 26 -> 8 这一处，其余与 rtl/params/rtl_params.vh 逐字相同。
//
//    **不要把它当成参数来源**：任何综合都不应该用这个目录当 --incdirs，
//    只有在跑"红例"（验证门禁本身有效）时才用。
//
//    红/绿两例（synth/README.md §7.1）：
//      绿：--incdirs rtl/params               -> DC_PARAM_OK，rc=0
//      红：--incdirs synth/tests/badparams    -> DC_PARAM_MISMATCH: PHASES = 8 != 16，rc=5
//
//    为什么要留这个文件：`elaborate` 时会整块丢掉 initial 块（VER-281），
//    顶层原本那 17 条 $fatal 参数自检在综合里一条都不执行。检查搬到 DC 流程里
//    之后，"这个检查真的会红吗"必须有个可复现的答案 —— 否则又是一个
//    "没人检查的检查"。判据必须能变红，否则等于没有。
//
//    维护：如果 rtl/params/rtl_params.vh 更新了，本文件需要同步更新
//    （保持"只差 PHASES 一处"这个性质，否则红例会变成"多处不一致"，
//    归因就不再唯一）。
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
// ↓↓↓ 红例：这里被故意改成 8（真值是 16）↓↓↓
localparam [5:0] PHASES = 6'd8;  // assumed: RTL 设计选择，见计划 §4.2

`endif  // RTL_PARAMS_VH
