// COMPONENT_SYNTHETIC_DIGITAL: flat numeric reduction witness for RTL/netlist reuse.
// Explicit fixture: 3 coefficient rows, 2 selected slots, 2+1 binary mask terms. No encoder,
// state-to-mask mapping, sampling network, or analog mechanism is modeled.
module tree_mapping_dut (
    input wire [9:0] ids,
    input wire [3:0] main_on,
    input wire [1:0] sub_on,
    input wire [431:0] weights,
    output wire [63:0] total,gain,
    output wire signed [65:0] rails,
    output wire invalid
);
  cal_weight_reduce #(.P_N_SLICES(3),.P_N_ACTIVE(2),.P_N_MAIN(2),.P_N_SUB(1),.P_USE_ROW_TOTALS(0)) reduce(
    .slice_id(ids),.main_on(main_on),.sub_on(sub_on),.w_rom(weights),
    .sum_W(total),.sum_Wa(gain),.rails(rails),.invalid_slice(invalid),.row_total('0));
endmodule
