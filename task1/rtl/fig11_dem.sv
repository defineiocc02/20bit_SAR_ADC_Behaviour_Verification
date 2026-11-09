// =============================================================================
// 文件名     : fig11_dem.sv
// 模块名     : fig11_dem
// 版本       : v1.0
// 功能       : 把 9 位 RDAC 码翻成 Fig.11 分段电容的组合开关掩码
//
// 设计说明   :
//   * code 位域分为 H(高 4 位温度计段)、B4(两个 1C)、B5~B8(子阵列二进制段)。
//   * 主 17 位、子 5 位按 slot 打包，slot 0 在最低位；置位表示底板选高参考。
//   * DEM 的 15 环与 16 环轮转用 15-h / 16-v 折算，避免减法回绕；h=15 为非法相位。
//   * 主、子节点各自的名义选通电容在 DEM 开启/旁路时守恒；末个 1C 恒接低参考。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。

// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module fig11_dem (
    // code[8:5]=H 决定每片 15 个 2C 里选几个，B4=code[4] 管主侧 1C，B5～B8=code[3:0] 管子侧；9 位一次性组合译码。
    input  logic [  8:0] code_i,
    input  logic         dem_enable_i,
    // h 在 15 个 2C 单元内轮转，v 在 16 个主侧 1C 位置内轮转；两者按样本冻结后送入，本模块不产生随机。
    input  logic [  3:0] horizontal_phase_i,
    input  logic [  3:0] vertical_phase_i,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic         valid_o
);
    // 每片主阵列 15×2C + 2×1C = 17 个开关位，子阵列 4 个位控单元加 1 个固定低参考的 dummy = 5 位。
    localparam int SlotCount = 8;
    localparam int ThermCount = 15;
    localparam int MainCount = 17;
    localparam int SubCount = 5;

    // base_therm 是 H 个 1 的温度计码；rotated_therm 与 selected_ones 分别是 15 环、16 环上的轮转结果。
    logic [ThermCount-1:0] base_therm;
    logic [ThermCount-1:0] rotated_therm;
    logic [        15:0] selected_ones;
    logic [SubCount-1:0] sub_select;

    // H=0 得全 0、H=15 得全 1，温度计码天然限幅在 15 个 2C 上，不需要额外饱和逻辑。
    // B0 是 code[8]。B0～B3 给出每片选通的 2C 个数。
    for (genvar unit_idx = 0; unit_idx < ThermCount; unit_idx++) begin : gen_therm
        assign base_therm[unit_idx] = code_i[8:5] > 4'(unit_idx);
    end
    // 每片相对 slot 0 的起点偏移是 slot 号。一次可变轮转后用常量连线产生各片结果。
    assign rotated_therm = (base_therm << horizontal_phase_i)
                         | (base_therm >> (4'd15 - horizontal_phase_i));
    // B4=1 时从 16 个 1C 中取连续 8 个位置，移过末尾后从 0 继续。
    // 右移量写成 16-v，v=0 时右半为 0，8 个连 1 不会重复置位；5 位减法也避免 0-0 回绕。
    assign selected_ones = (16'h00ff << vertical_phase_i)
                         | (16'h00ff >> (5'd16 - {1'b0, vertical_phase_i}));
    // 每片的 B5～B8 对应子单元 0～3。末端 1C 固定接低参考。
    // B5～B8 留在子单元 0～3，B4 只切主侧 1C，主、子节点各自的名义选通电容因此守恒，DEM 不跨节点搬权重。
    assign sub_select = {1'b0, code_i[0], code_i[1], code_i[2], code_i[3]};
    // 旁路态忽略相位恒判合法，避免 dem_enable_i=0 时残留的 h=15 把正常码一并拒绝。
    assign valid_o = !dem_enable_i || (horizontal_phase_i != 4'd15);

    for (genvar slot = 0; slot < SlotCount; slot++) begin : gen_slot
        logic [MainCount-1:0] main_select;
        for (genvar unit_idx = 0; unit_idx < ThermCount; unit_idx++) begin : gen_unit
            localparam int SourceUnit = (unit_idx + ThermCount - slot) % ThermCount;
            assign main_select[unit_idx] = dem_enable_i
                ? rotated_therm[SourceUnit] : base_therm[unit_idx];
        end
        // 两个主侧 1C 分到 [15]/[16]：旁路时只落单元 15，DEM 开启时按 16 环位置的偶／奇分别接通。
        assign main_select[15] = code_i[4]
            && (!dem_enable_i || selected_ones[2*slot]);
        assign main_select[16] = code_i[4] && dem_enable_i && selected_ones[2*slot+1];
        // 非法相位下把主、子掩码整片清零，外部才不会把退化后的轮转结果当成有效开关命令。
        assign main_mask_o[MainCount*slot+:MainCount] = valid_o ? main_select : '0;
        assign sub_mask_o[SubCount*slot+:SubCount] = valid_o ? sub_select : '0;
    end
endmodule
