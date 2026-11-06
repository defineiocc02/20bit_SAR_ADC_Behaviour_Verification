// Fig.11 分段电容的组合开关映射。slot 0 对应输出最低位。
// 2C 主单元在每片内轮转，B4 在八片共十六个独立 1C 主单元中
// 轮转。B5～B8 保留在子节点，因此主、子节点各自的名义选通电容守恒。
module fig11_dem (
    input  logic [  8:0] code_i,
    input  logic         dem_enable_i,
    input  logic [  3:0] horizontal_phase_i,
    input  logic [  3:0] vertical_phase_i,
    output logic [135:0] main_mask_o,
    output logic [ 39:0] sub_mask_o,
    output logic         valid_o
);
    localparam int SlotCount = 8;
    localparam int ThermCount = 15;
    localparam int MainCount = 17;
    localparam int SubCount = 5;

    logic [ThermCount-1:0] base_therm;
    logic [ThermCount-1:0] rotated_therm;
    logic [        15:0] selected_ones;
    logic [SubCount-1:0] sub_select;

    // B0 是 code[8]。B0～B3 给出每片选通的 2C 个数。
    for (genvar unit_idx = 0; unit_idx < ThermCount; unit_idx++) begin : gen_therm
        assign base_therm[unit_idx] = code_i[8:5] > 4'(unit_idx);
    end
    // 每片相对 slot 0 的起点偏移是 slot 号。一次可变轮转后用常量连线产生各片结果。
    assign rotated_therm = (base_therm << horizontal_phase_i)
                         | (base_therm >> (4'd15 - horizontal_phase_i));
    // B4=1 时从 16 个 1C 中取连续 8 个位置，移过末尾后从 0 继续。
    assign selected_ones = (16'h00ff << vertical_phase_i)
                         | (16'h00ff >> (5'd16 - {1'b0, vertical_phase_i}));
    // 每片的 B5～B8 对应子单元 0～3。末端 1C 固定接低参考。
    assign sub_select = {1'b0, code_i[0], code_i[1], code_i[2], code_i[3]};
    assign valid_o = !dem_enable_i || (horizontal_phase_i != 4'd15);

    for (genvar slot = 0; slot < SlotCount; slot++) begin : gen_slot
        logic [MainCount-1:0] main_select;
        for (genvar unit_idx = 0; unit_idx < ThermCount; unit_idx++) begin : gen_unit
            localparam int SourceUnit = (unit_idx + ThermCount - slot) % ThermCount;
            assign main_select[unit_idx] = dem_enable_i
                ? rotated_therm[SourceUnit] : base_therm[unit_idx];
        end
        assign main_select[15] = code_i[4]
            && (!dem_enable_i || selected_ones[2*slot]);
        assign main_select[16] = code_i[4] && dem_enable_i && selected_ones[2*slot+1];
        assign main_mask_o[MainCount*slot+:MainCount] = valid_o ? main_select : '0;
        assign sub_mask_o[SubCount*slot+:SubCount] = valid_o ? sub_select : '0;
    end
endmodule
