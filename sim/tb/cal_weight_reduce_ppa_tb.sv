`timescale 1ns/1ps
`include "rtl_params.vh"
// COMPONENT_SYNTHETIC_DIGITAL. Compare direct and exact-row-cache reductions to an independent
// scalar fold over supplied row IDs and actual binary masks. This verifies arithmetic/structure
// only; no physical switching map or PPA is inferred.
module cal_weight_reduce_ppa_case #(
  parameter int NS=3,NA=2,NM=2,NB=1,SEED=1
)(output logic complete=0);
  logic [NA-1:0][4:0] ids;
  logic [NA-1:0][NM-1:0] main_on;
  logic [NA-1:0][NB-1:0] sub_on;
  logic [NS-1:0][NM+NB-1:0][47:0] weights;
  logic sample_clk=0,sample_valid=0;
  logic [NA-1:0][4:0] captured_ids;
  logic [NA-1:0][NM-1:0] captured_main;
  logic [NA-1:0][NB-1:0] captured_sub;
  logic [NS-1:0][NM+NB-1:0][47:0] captured_weights;
  logic [NS-1:0][63:0] captured_rows;
  wire [63:0] direct_total,direct_gain,cached_total,cached_gain;
  wire signed [65:0] direct_rails,cached_rails;
  wire direct_invalid,cached_invalid;
  int checks=0,invalid_cases=0;
  logic [31:0] rng=32'(SEED);
  always @(posedge sample_clk)begin
    sample_valid<=1;
    captured_ids<=ids;captured_main<=main_on;captured_sub<=sub_on;
    captured_weights<=weights;
    for(int row=0;row<NS;row++)begin
      automatic logic[63:0] sum=0;
      for(int unit_index=0;unit_index<NM+NB;unit_index++)sum+=64'(weights[row][unit_index]);
      captured_rows[row]<=sum;
    end
  end
  cal_weight_reduce #(.P_N_SLICES(NS),.P_N_ACTIVE(NA),.P_N_MAIN(NM),.P_N_SUB(NB),.P_USE_ROW_TOTALS(0)) direct(
    .slice_id(captured_ids),.main_on(captured_main),.sub_on(captured_sub),.w_rom(captured_weights),
    .row_total('0),.sum_W(direct_total),.sum_Wa(direct_gain),.rails(direct_rails),.invalid_slice(direct_invalid));
  cal_weight_reduce #(.P_N_SLICES(NS),.P_N_ACTIVE(NA),.P_N_MAIN(NM),.P_N_SUB(NB),.P_USE_ROW_TOTALS(1)) cached(
    .slice_id(captured_ids),.main_on(captured_main),.sub_on(captured_sub),.w_rom(captured_weights),
    .row_total(captured_rows),.sum_W(cached_total),.sum_Wa(cached_gain),.rails(cached_rails),.invalid_slice(cached_invalid));
  function automatic logic[31:0] random_word();
    rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;
  endfunction
  // No generated tree, row cache, or DUT intermediate is reused by this oracle.
  function automatic logic[194:0] scalar_result(
    input logic[NA-1:0][4:0] ids_arg,
    input logic[NA-1:0][NM-1:0] main_arg,
    input logic[NA-1:0][NB-1:0] sub_arg,
    input logic[NS-1:0][NM+NB-1:0][47:0] weights_arg
  );
    /* verilator no_inline_task */
    logic[63:0] total;
    logic signed[65:0] rails,weight;
    bit active,on,bad;
    total=0;rails=0;bad=0;
    for(int a=0;a<NA;a++)begin
      bad|=int'(ids_arg[a])>=NS;
      for(int b=0;b<a;b++)bad|=ids_arg[a]==ids_arg[b];
    end
    for(int row=0;row<NS;row++)begin
      active=0;
      for(int slot=0;slot<NA;slot++)active|=int'(ids_arg[slot])==row;
      if(active)for(int term=0;term<NM+NB;term++)begin
        on=0;
        for(int slot=0;slot<NA;slot++)if(int'(ids_arg[slot])==row)
          on|=(term<NM)?main_arg[slot][term]:sub_arg[slot][term-NM];
        weight=$signed({18'd0,weights_arg[row][term]});
        total+=64'(weight);rails+=on?-weight:weight;
      end
    end
    return {total,total,rails,bad};
  endfunction
  task automatic check();sample_clk=1;#0.5;sample_clk=0;#0.5;endtask
  task automatic valid_ids();for(int slot=0;slot<NA;slot++)ids[slot]=5'(slot);endtask
  always @(negedge sample_clk)begin
    logic[194:0] want;
    if(sample_valid)begin
      want=scalar_result(ids,main_on,sub_on,weights);checks++;if(want[0])invalid_cases++;
      if({direct_total,direct_gain,direct_rails,direct_invalid}!==want)
        $fatal(1,"direct numerical reduction mismatch NS=%0d NA=%0d NM=%0d NB=%0d check=%0d",NS,NA,NM,NB,checks);
      if({cached_total,cached_gain,cached_rails,cached_invalid}!==want)
        $fatal(1,"cached numerical reduction mismatch NS=%0d NA=%0d NM=%0d NB=%0d check=%0d",NS,NA,NM,NB,checks);
    end
  end
  initial begin
    if(W_BITS!=48)$fatal(1,"explicit 48-bit mathematical coefficient fixture required");
    #0.5;valid_ids();main_on='0;sub_on='0;
    for(int pattern=0;pattern<6;pattern++)begin
      for(int row=0;row<NS;row++)for(int term=0;term<NM+NB;term++)begin
        case(pattern)
          0:weights[row][term]=0;
          1:weights[row][term]=1;
          2:weights[row][term]=48'h7fffffffffff;
          3:weights[row][term]=48'h800000000000;
          4:weights[row][term]=48'hffffffffffff;
          default:weights[row][term]=(term%2!=0)?48'h555555555555:48'haaaaaaaaaaaa;
        endcase
      end
      main_on='0;sub_on='0;check();main_on='1;sub_on='1;check();
    end
    for(int slot=0;slot<NA;slot++)for(int address=0;address<32;address++)begin
      valid_ids();ids[slot]=5'(address);check();
    end
    for(int n=0;n<1000;n++)begin
      for(int row=0;row<NS;row++)for(int term=0;term<NM+NB;term++)
        weights[row][term]={16'(random_word()),random_word()};
      for(int slot=0;slot<NA;slot++)begin
        ids[slot]=(n%2==0)?5'((n+slot)%NS):5'(random_word());
        for(int term=0;term<NM;term++)main_on[slot][term]=1'(random_word());
        for(int term=0;term<NB;term++)sub_on[slot][term]=1'(random_word());
      end
      check();
    end
    if(checks!=1012+NA*32 || invalid_cases==0)$fatal(1,"numerical reducer coverage changed");
    $display("REDUCE_NUMERIC_CASE_PASS rows=%0d active=%0d terms=%0d checks=%0d invalid_cases=%0d",NS,NA,NM+NB,checks,invalid_cases);
    complete=1;
  end
endmodule
module cal_weight_reduce_ppa_tb;
  wire[3:0] complete;
  cal_weight_reduce_ppa_case #(.NS(3),.NA(2),.NM(2),.NB(1),.SEED(1)) small_numeric(.complete(complete[0]));
  cal_weight_reduce_ppa_case #(.NS(1),.NA(1),.NM(7),.NB(1),.SEED(2)) row_width(.complete(complete[1]));
  cal_weight_reduce_ppa_case #(.NS(32),.NA(8),.NM(1),.NB(1),.SEED(3)) selection(.complete(complete[2]));
  cal_weight_reduce_ppa_case #(.NS(1),.NA(1),.NM(1),.NB(1),.SEED(4)) minimum(.complete(complete[3]));
  initial begin
    wait(&complete);$display("CAL_WEIGHT_REDUCE_PPA_COMPLETE geometries=4 checks=4432");$finish;
  end
  initial begin #20000;$fatal(1,"numeric reducer watchdog");end
endmodule
