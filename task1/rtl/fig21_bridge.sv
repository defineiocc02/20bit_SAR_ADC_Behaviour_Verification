// =============================================================================
// 文件名     : fig21_bridge.sv
// 模块名     : fig21_bridge
// 版本       : v1.0
// 功能       : Fig.21 跨 B4~B6 的候选开关映射与 Q32 名义补偿
//
// 设计说明   :
//   * m=4B4+2B5+B6 决定保留列与行编码；原点列恒 0，{columns[15],columns} 拼成 17 位。
//   * B5/B6 从子阵列搬出，B7/B8 仍留在子节点；迁移后与旧码本存在非零名义电荷差。
//   * correction_q32_o 只在按 Fig.11 名义码本重构时加一次；按迁移后真实位权重构时取零。
//   * 补偿按 code[3:2] 三档取值，掩码无效时清零，防止重复计入扰动。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。

// =========================================================================
// 1. 模块接口与端口分组
// =========================================================================
module fig21_bridge (
    // code[8:5]=H 选普通列数，code[4:2] 编码开启保留列的片数 m=4B4+2B5+B6，code[3:2] 决定补偿档。
    input  logic [  8:0] code_i,
    // R 是冻结的保留列号（0～15），v 是纵向起始 slot（0～7）；两者取自转换组相位，不随已决前缀改变。
    input  logic [  3:0] reserved_column_i,
    input  logic [  2:0] vertical_slot_i,
    // 主掩码每片 17 位：逻辑列 15 复制一份给两个 1C 物理单元，故 16 列拼成 17 位；slot 0 在最低位。
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic [ 31:0] correction_q32_o
);
    // 16 个逻辑列、8 个 slot；主 17 位/片、子 5 位/片沿用 Fig.11 的物理打包，便于上层在两套映射间二选一。
    localparam int SlotCount = 8;
    localparam int ColumnCount = 16;
    localparam int MainCount = 17;
    localparam int SubCount = 5;

    // base_columns 是 H 个 1 的温度计列（原点列保留为 0），regular_columns 是它按 R 环移后的普通列。
    logic [ColumnCount-1:0] base_columns;
    logic [ColumnCount-1:0] regular_columns;
    logic [ColumnCount-1:0] reserved_onehot;
    logic [  SlotCount-1:0] base_rows;
    logic [  SlotCount-1:0] selected_rows;
    logic [   SubCount-1:0] sub_select;

    // 原点位置保留，从下一列起取 H 列。所有 slot 共享普通列。
    // 原点列恒 0：保留列不参与 H 的普通选择，避免同一列被同时当作普通列和保留列计数。
    assign base_columns[0] = 1'b0;
    for (genvar column_idx = 1; column_idx < ColumnCount; column_idx++) begin : gen_columns
        assign base_columns[column_idx] = code_i[8:5] >= 4'(column_idx);
    end
    // 环移量用 16-R（5 位减法），R=0 时右半为 0，普通列仍是 base_columns；所有 slot 共用这一组列。
    assign regular_columns = (base_columns << reserved_column_i)
                           | (base_columns >> (5'd16 - {1'b0, reserved_column_i}));
    // 保留列单独做成 onehot，只有被 m 选中的 slot 才把它叠加到自己的列集合上。
    assign reserved_onehot = 16'h0001 << reserved_column_i;

    // m 表示由 B4、B5、B6 编码的 slot 数。只有选中的 slot 接通保留列。
    assign base_rows[SlotCount-1] = 1'b0;
    for (genvar row_idx = 0; row_idx < SlotCount-1; row_idx++) begin : gen_rows
        assign base_rows[row_idx] = code_i[4:2] > 3'(row_idx);
    end
    // 8 片环移用 8-v，v=0 时右半为 0；4'd8 保证减法不回绕（v 仅 3 位，取 0～7）。
    assign selected_rows = (base_rows << vertical_slot_i)
                        | (base_rows >> (4'd8 - {1'b0, vertical_slot_i}));
    // B5、B6 已搬到保留列，子单元 0/1 恒 0；B7、B8 仍按 code[1:0] 落在本片，末尾 dummy 恒低。
    assign sub_select = {1'b0, code_i[0], code_i[1], 2'b00};

    // 每片列集合 = 共享普通列 + 本片是否命中保留列；子位图对所有片相同，不随 slot 变化。
    for (genvar slot = 0; slot < SlotCount; slot++) begin : gen_slot
        logic [ColumnCount-1:0] columns;
        assign columns = regular_columns | (selected_rows[slot] ? reserved_onehot : '0);
        // 逻辑列 15 由两个 1C 物理单元同控。
        assign main_mask_o[MainCount*slot+:MainCount] = {columns[15], columns};
        assign sub_mask_o[SubCount*slot+:SubCount] = sub_select;
    end

    // 该补偿只在按 Fig.11 名义码本重构残差时加一次；按迁移后真实位权重构必须置零，重复加会双倍计。
    // Cb=C 且子节点共 16C 时，跨段选通增加 (4B5+2B6)C/17。
    // 采用 ±Vfs 参考及八片主阵列共 256C，补偿为 round((2B5+B6)*2^26/17)。
    // Q32 是以 V/Vfs 归一化的电压补偿，仅 code[3:2] 三档非零；B5=B6=0 时无跨段搬位，补偿为 0。
// =========================================================================
// 2. 桥接补偿查表（纯组合，无时序逻辑）
// =========================================================================
    always_comb begin
        case (code_i[3:2])
            2'b01: correction_q32_o = 32'd3947580;
            2'b10: correction_q32_o = 32'd7895160;
            2'b11: correction_q32_o = 32'd11842741;
            default: correction_q32_o = '0;
        endcase
    end
endmodule
