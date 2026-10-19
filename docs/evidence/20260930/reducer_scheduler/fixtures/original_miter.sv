// PPA refactoring equivalence test. The reference below preserves the previous
// cal_weight_reduce implementation from commit 2de928066508e2cfd009df3e7c6b2936bec6837c.
// Production all 43,758 unordered 8-of-18 allocations are crossed with every
// 4-bit rail pattern and both sampling modes. This is a finite simulation miter,
// not a formal all-state proof. Algebraic justification is in ADR 0020.
`timescale 1ns/1ps
module cal_weight_reduce_ppa_case #(
 parameter int NS=18, NA=8, NM=63, NB=8, ND=4, DE=71, SEED=1,
 parameter bit ALL_SUBSETS=0
)(output logic complete=0);
 logic sampling=0;
 logic [NA-1:0][4:0] ids;
 logic [NA-1:0][NM-1:0] main_on;
 logic [NA-1:0][NB-1:0] sub_on;
 logic [ND-1:0] dr;
 logic [NS-1:0][NM+NB-1:0][47:0] weights;
 wire [63:0] old_total,old_gain,new_total,new_gain;
 wire signed[65:0] old_rails,new_rails;
 wire old_invalid,new_invalid;
 int checks=0,subsets=0;
 logic [31:0] rng=32'(SEED);
 cal_weight_reduce_reference #(.P_N_SLICES(NS),.P_N_ACTIVE(NA),.P_N_MAIN(NM),.P_N_SUB(NB),.P_DIT_N(ND),.P_DIT_END(DE)) ref_dut(
 .sampling_mask_en(sampling),.slice_id(ids),.main_on(main_on),.sub_on(sub_on),.dither_rail(dr),.w_rom(weights),.sum_W(old_total),.sum_Wa(old_gain),.rails(old_rails),.invalid_slice(old_invalid));
 cal_weight_reduce #(.P_N_SLICES(NS),.P_N_ACTIVE(NA),.P_N_MAIN(NM),.P_N_SUB(NB),.P_DIT_N(ND),.P_DIT_END(DE)) dut(
 .sampling_mask_en(sampling),.slice_id(ids),.main_on(main_on),.sub_on(sub_on),.dither_rail(dr),.w_rom(weights),.sum_W(new_total),.sum_Wa(new_gain),.rails(new_rails),.invalid_slice(new_invalid));
 function automatic logic[31:0] random_word();
  rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;
 endfunction
 task automatic check();
  bit bad;
  #1;checks++;
  bad=sampling&&(DE>NM+NB);
  for(int a=0;a<NA;a++)begin
   bad|=(int'(ids[a])>=NS);
   for(int b=0;b<a;b++)bad|=(ids[a]==ids[b]);
  end
  if({new_total,new_gain,new_rails,new_invalid}!=={old_total,old_gain,old_rails,old_invalid})
   $fatal(1,"reducer mismatch NS=%0d NM=%0d ND=%0d DE=%0d check=%0d",NS,NM,ND,DE,checks);
  if(new_invalid!==bad)$fatal(1,"invalid slice flag mismatch");
 endtask
 task automatic all_rails();
  for(int mode=0;mode<2;mode++)begin
   sampling=1'(mode);
   for(int r=0;r<(1<<ND);r++)begin dr=ND'(r);check();end
  end
 endtask
 task automatic base_ids();
  for(int a=0;a<NA;a++)ids[a]=5'(a);
 endtask
 task automatic random_weights();
  for(int s=0;s<NS;s++)for(int u=0;u<NM+NB;u++)weights[s][u]={16'(random_word()),random_word()};
 endtask
 initial begin
  int comb[NA],perm[NS],pos,j,tmp;
  bit finished;
  base_ids();main_on='0;sub_on='0;dr='0;
  // Legal extremes and illegal signed-looking bit patterns are compared bit-exactly.
  // Weight ports are unsigned; negative encodings are deliberately outside loader validity.
  for(int pattern=0;pattern<6;pattern++)begin
   for(int s=0;s<NS;s++)for(int u=0;u<NM+NB;u++)begin
    case(pattern)
     0:weights[s][u]=0;
     1:weights[s][u]=1;
     2:weights[s][u]=48'h7fffffffffff;
     3:weights[s][u]=48'h800000000000;
     4:weights[s][u]=48'hffffffffffff;
     5:weights[s][u]=(u%2)?48'h555555555555:48'haaaaaaaaaaaa;
    endcase
   end
   main_on='0;sub_on='0;all_rails();
   main_on='1;sub_on='1;all_rails();
  end
  random_weights();
  // Every address value in every logical slot, every rail pattern, both modes.
  // This exercises physical endpoints, duplicate IDs, and out-of-range IDs.
  for(int a=0;a<NA;a++)for(int s=0;s<32;s++)begin
   base_ids();ids[a]=5'(s);all_rails();
  end
  // Every unordered production 8-of-18 allocation x 16 rail patterns x 2 modes.
  if(ALL_SUBSETS)begin
   for(int a=0;a<NA;a++)comb[a]=a;
   finished=0;
   while(!finished)begin
    for(int a=0;a<NA;a++)begin
     ids[a]=5'(comb[a]);
     for(int u=0;u<NM;u++)main_on[a][u]=1'((comb[a]+u+a)%2);
     for(int u=0;u<NB;u++)sub_on[a][u]=1'((comb[a]+u)%2);
    end
    all_rails();subsets++;
    pos=NA-1;
    while(pos>=0)begin
     if(comb[pos]!=NS-NA+pos)break;
     pos--;
    end
    if(pos<0)finished=1;
    else begin
     comb[pos]++;
     for(int a=pos+1;a<NA;a++)comb[a]=comb[a-1]+1;
    end
   end
   if(subsets!=43758)$fatal(1,"wrong allocation coverage count");
  end
  // Vary weight bits, permutations, switch masks, invalid IDs, and rail patterns.
  for(int n=0;n<2000;n++)begin
   if(n%16==0)random_weights();
   for(int s=0;s<NS;s++)perm[s]=s;
   for(int s=NS-1;s>0;s--)begin
    j=int'(random_word()%32'(s+1));tmp=perm[s];perm[s]=perm[j];perm[j]=tmp;
   end
   for(int a=0;a<NA;a++)begin
    ids[a]=(n%2==0) ? 5'(perm[a]) : 5'(random_word());
    for(int u=0;u<NM;u++)main_on[a][u]=1'(random_word());
    for(int u=0;u<NB;u++)sub_on[a][u]=1'(random_word());
   end
   sampling=1'(random_word());dr=ND'(random_word());check();
  end
  if(checks!=(12+NA*32)*(2<<ND)+2000+(ALL_SUBSETS?43758*(2<<ND):0))
   $fatal(1,"miter coverage count mismatch");
  $display("REDUCE_MITER_CASE_PASS ns=%0d active=%0d units=%0d dither=%0d end=%0d checks=%0d allocations=%0d",NS,NA,NM+NB,ND,DE,checks,subsets);
  complete=1;
 end
endmodule
module cal_weight_reduce_ppa_tb;
 wire[6:0] complete;
 cal_weight_reduce_ppa_case #(.ALL_SUBSETS(1),.SEED(20260930)) prod(.complete(complete[0]));
 cal_weight_reduce_ppa_case #(.NS(3),.NA(2),.NM(2),.NB(1),.ND(2),.DE(3),.SEED(2)) bridge(.complete(complete[1]));
 cal_weight_reduce_ppa_case #(.NS(1),.NA(1),.NM(127),.NB(1),.ND(4),.DE(128),.SEED(3)) units(.complete(complete[2]));
 cal_weight_reduce_ppa_case #(.NS(32),.NA(8),.NM(2),.NB(1),.ND(1),.DE(3),.SEED(4)) slices(.complete(complete[3]));
 cal_weight_reduce_ppa_case #(.NS(3),.NA(2),.NM(2),.NB(1),.ND(2),.DE(4),.SEED(5)) partial(.complete(complete[4]));
 cal_weight_reduce_ppa_case #(.NS(32),.NA(32),.NM(1),.NB(1),.ND(1),.DE(2),.SEED(6)) active_max(.complete(complete[5]));
 cal_weight_reduce_ppa_case #(.NS(1),.NA(1),.NM(1),.NB(1),.ND(1),.DE(2),.SEED(7)) minimal(.complete(complete[6]));
 initial begin wait(&complete);$display("CAL_WEIGHT_REDUCE_PPA_COMPLETE");$finish;end
 initial begin #3000000;$fatal(1,"reducer miter watchdog");end
endmodule

// Physical-cell weighted correction terms. No quantizer truth, floating point,
// or fitted analog state is accessible here. Coefficients are supplied by the
// committed physical slice/unit store; caller snapshots these sums with a sample.
// Paper [00] specifies externally derived weights corrected on chip; this exact
// Q30/Q32 arithmetic realization is an engineering implementation (ADR 0014).
`include "rtl_params.vh"
module cal_weight_reduce_reference #(
    parameter int P_N_ACTIVE = int'(N_ACTIVE),
    parameter int P_N_MAIN = int'(N_UNIT_MAIN),
    parameter int P_N_SUB = int'(N_UNIT_SUB),
    parameter int P_N_SLICES = int'(N_SLICES),
    parameter int P_DIT_N = 2*int'(DITHER_UNITS_RANGE),
    parameter int P_DIT_END = int'(N_UNIT_TOTAL),
    parameter int SUM_BITS = 64,
    parameter int W_RAIL = SUM_BITS+2
) (
    input wire sampling_mask_en,
    input wire [P_N_ACTIVE-1:0][4:0] slice_id,
    input wire [P_N_ACTIVE-1:0][P_N_MAIN-1:0] main_on,
    input wire [P_N_ACTIVE-1:0][P_N_SUB-1:0] sub_on,
    input wire [P_DIT_N-1:0] dither_rail,
    input wire [P_N_SLICES-1:0][P_N_MAIN+P_N_SUB-1:0][W_BITS-1:0] w_rom,
    output wire [SUM_BITS-1:0] sum_W,
    output wire [SUM_BITS-1:0] sum_Wa,
    output wire signed [W_RAIL-1:0] rails,
    output logic invalid_slice
);
  localparam int N_U = P_N_MAIN+P_N_SUB;
  localparam int DIT_BEG = P_DIT_END-P_DIT_N;
  localparam int N_TERMS = P_N_SLICES * N_U;
  localparam int TREE_LEAVES = 1 << $clog2(N_TERMS);
  assign sum_W = g_node[1].total;
  assign sum_Wa = g_node[1].gain;
  wire [SUM_BITS-1:0] sum_Won = g_node[1].on_sum;
  wire signed [W_RAIL-1:0] sum_Wr = g_node[1].dither_sum;

  initial begin
    if (P_N_ACTIVE < 1 || P_N_MAIN < 1 || P_N_SUB < 1 ||
        P_N_SLICES < P_N_ACTIVE || P_N_SLICES > 32 || P_DIT_N < 1 || DIT_BEG < 0 ||
        SUM_BITS < int'(W_BITS)+$clog2(N_TERMS) || W_RAIL != SUM_BITS+2)
      $fatal(1, "cal_weight_reduce: unsupported dimensions");
  end
  always_comb begin
    invalid_slice = sampling_mask_en && (P_DIT_END > N_U);
    for (int a = 0; a < P_N_ACTIVE; a++)
      invalid_slice |= (int'(slice_id[a]) >= P_N_SLICES);
    for (int a = 0; a < P_N_ACTIVE; a++)
      for (int b = 0; b < a; b++)
        invalid_slice |= (slice_id[a] == slice_id[b]);
  end

  // Move the narrow switch mask to its physical row, rather than selecting
  // 48-bit coefficient buses through eight independent 18:1 crossbars.
  // Every coefficient is statically wired to its own physical unit. This trades
  // more zero-gated adder leaves for substantially narrower selection wiring;
  // mapped area/timing still require a target-library synthesis comparison.
  logic [P_N_SLICES-1:0] active;
  logic [P_N_SLICES-1:0][N_U-1:0] physical_on;
  always_comb begin
    active='0;physical_on='0;
    for(int s=0;s<P_N_SLICES;s++) begin
      for(int a=0;a<P_N_ACTIVE;a++) begin
        if(slice_id[a]==5'(s)) begin
          active[s]=1'b1;
          physical_on[s] |= {sub_on[a],main_on[a]};
        end
      end
    end
  end

  // Each generated node owns distinct nets: dependencies always point from
  // t to 2*t/2*t+1. Do not coalesce them into one unpacked array: older tools
  // conservatively report a loop on that aggregate despite this acyclic graph.
  for (genvar t = 1; t < 2*TREE_LEAVES; t++) begin : g_node
    wire [SUM_BITS-1:0] total, gain, on_sum;
    wire signed [W_RAIL-1:0] dither_sum;
    if (t < TREE_LEAVES) begin : g_branch
      assign total = g_node[2*t].total + g_node[2*t+1].total;
      assign gain = g_node[2*t].gain + g_node[2*t+1].gain;
      assign on_sum = g_node[2*t].on_sum + g_node[2*t+1].on_sum;
      assign dither_sum = g_node[2*t].dither_sum + g_node[2*t+1].dither_sum;
    end else if (t-TREE_LEAVES < N_TERMS) begin : g_leaf
      localparam int S = (t-TREE_LEAVES) / N_U;
      localparam int U = (t-TREE_LEAVES) % N_U;
      wire [W_BITS-1:0] weight = active[S] ? w_rom[S][U] : '0;
      wire [SUM_BITS-1:0] extended = {{(SUM_BITS-int'(W_BITS)){1'b0}}, weight};
      assign total = extended;
      assign on_sum = physical_on[S][U] ? extended : '0;
      if (U >= DIT_BEG && U < P_DIT_END) begin : g_dither
        wire signed [W_RAIL-1:0] signed_weight = $signed({{(W_RAIL-int'(W_BITS)){1'b0}}, weight});
        assign gain = sampling_mask_en ? '0 : extended;
        assign dither_sum = !sampling_mask_en ? '0 :
          (dither_rail[U-DIT_BEG] ? signed_weight : -signed_weight);
      end else begin : g_signal
        assign gain = extended;
        assign dither_sum = '0;
      end
    end else begin : g_padding
      assign total = '0;
      assign gain = '0;
      assign on_sum = '0;
      assign dither_sum = '0;
    end
  end
  assign rails = $signed({2'b0, sum_W}) - $signed({1'b0, sum_Won, 1'b0}) + sum_Wr;

endmodule
