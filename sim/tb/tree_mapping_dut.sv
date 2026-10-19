// Small mapped-netlist witness for the generate-tree synthesis contract.
// Ports stay flat so the same testbench drives RTL and Vivado funcsim netlists.
module tree_mapping_dut (
    input wire [6:0] flash3,
    input wire [510:0] flash9,
    input wire sampling,
    input wire [9:0] ids,
    input wire [3:0] main_on,
    input wire [1:0] sub_on, dither_rail,
    input wire [431:0] weights,
    output wire [2:0] code3,
    output wire [8:0] code9,
    output wire [63:0] total, gain,
    output wire signed [65:0] rails,
    output wire invalid
);
    sadc_enc #(.P_B1(3),.P_N_CMP(7)) enc3(.cmp_raw(flash3),.sadc_code(code3));
    sadc_enc #(.P_B1(9),.P_N_CMP(511)) enc9(.cmp_raw(flash9),.sadc_code(code9));
    cal_weight_reduce #(.P_N_SLICES(3),.P_N_ACTIVE(2),.P_N_MAIN(2),
        .P_N_SUB(1),.P_DIT_N(2),.P_DIT_END(3)) reduce(
        .sampling_mask_en(sampling),.slice_id(ids),.main_on(main_on),
        .sub_on(sub_on),.dither_rail(dither_rail),.w_rom(weights),
        .sum_W(total),.sum_Wa(gain),.rails(rails),.invalid_slice(invalid), .row_total('0));
endmodule
