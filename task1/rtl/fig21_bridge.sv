// Fig.21 跨 B4～B6 的候选开关映射，使用 Fig.11 的物理单元。
// slot 0 对应输出最低位。保留列与纵向起始 slot 由上层按同笔冻结。
// correction_q32_o 为归一化输入电压补偿。恢复该笔名义残差时加一次。
module fig21_bridge (
    input  logic [  8:0] code_i,
    input  logic [  3:0] reserved_column_i,
    input  logic [  2:0] vertical_slot_i,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic [ 31:0] correction_q32_o
);
    localparam int SlotCount = 8;
    localparam int ColumnCount = 16;
    localparam int MainCount = 17;
    localparam int SubCount = 5;

    logic [ColumnCount-1:0] base_columns;
    logic [ColumnCount-1:0] regular_columns;
    logic [ColumnCount-1:0] reserved_onehot;
    logic [  SlotCount-1:0] base_rows;
    logic [  SlotCount-1:0] selected_rows;
    logic [   SubCount-1:0] sub_select;

    // 原点位置保留，从下一列起取 H 列。所有 slot 共享普通列。
    assign base_columns[0] = 1'b0;
    for (genvar column_idx = 1; column_idx < ColumnCount; column_idx++) begin : gen_columns
        assign base_columns[column_idx] = code_i[8:5] >= 4'(column_idx);
    end
    assign regular_columns = (base_columns << reserved_column_i)
                           | (base_columns >> (5'd16 - {1'b0, reserved_column_i}));
    assign reserved_onehot = 16'h0001 << reserved_column_i;

    // m 表示由 B4、B5、B6 编码的 slot 数。只有选中的 slot 接通保留列。
    assign base_rows[SlotCount-1] = 1'b0;
    for (genvar row_idx = 0; row_idx < SlotCount-1; row_idx++) begin : gen_rows
        assign base_rows[row_idx] = code_i[4:2] > 3'(row_idx);
    end
    assign selected_rows = (base_rows << vertical_slot_i)
                        | (base_rows >> (4'd8 - {1'b0, vertical_slot_i}));
    assign sub_select = {1'b0, code_i[0], code_i[1], 2'b00};

    for (genvar slot = 0; slot < SlotCount; slot++) begin : gen_slot
        logic [ColumnCount-1:0] columns;
        assign columns = regular_columns | (selected_rows[slot] ? reserved_onehot : '0);
        // 逻辑列 15 由两个 1C 物理单元同控。
        assign main_mask_o[MainCount*slot+:MainCount] = {columns[15], columns};
        assign sub_mask_o[SubCount*slot+:SubCount] = sub_select;
    end

    // Cb=C 且子节点共 16C 时，跨段选通增加 (4B5+2B6)C/17。
    // 采用 ±Vfs 参考及八片主阵列共 256C，补偿为 round((2B5+B6)*2^26/17)。
    always_comb begin
        case (code_i[3:2])
            2'b01: correction_q32_o = 32'd3947580;
            2'b10: correction_q32_o = 32'd7895160;
            2'b11: correction_q32_o = 32'd11842741;
            default: correction_q32_o = '0;
        endcase
    end
endmodule
