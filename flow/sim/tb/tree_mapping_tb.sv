`timescale 1ns/1ps
// COMPONENT_SYNTHETIC_DIGITAL. Independent scalar reference for flat-mask RTL or an explicitly
// supplied mapped netlist of tree_mapping_dut. No PPA claim.
module tree_mapping_tb;
  logic[9:0] ids=0;
  logic[3:0] main_on=0;
  logic[1:0] sub_on=0;
  logic[431:0] weights=0;
  wire[63:0] total,gain;
  wire signed[65:0] rails;
  wire invalid;
  int checks=0,valid_cases=0,invalid_cases=0,trace_fd,f;
  string trace_path;
  logic[31:0] rng=32'h1949;
  tree_mapping_dut dut(.*);
  function automatic logic[31:0] random_word();rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;endfunction
  task automatic check();
    longint unsigned want_total,weight;
    longint signed want_rails;
    bit active,on,bad,ok;
    #1;want_total=0;want_rails=0;
    bad=(ids[4:0]>=3)||(ids[9:5]>=3)||(ids[4:0]==ids[9:5]);
    for(int row=0;row<3;row++)begin
      active=(int'(ids[4:0])==row)||(int'(ids[9:5])==row);
      for(int term=0;term<3;term++)begin
        weight=64'(weights[(row*3+term)*48+:48]);on=0;
        for(int slot=0;slot<2;slot++)if(int'(ids[slot*5+:5])==row)
          on|=(term<2)?main_on[slot*2+term]:sub_on[slot];
        if(active)begin want_total+=weight;want_rails+=on?-$signed(weight):$signed(weight);end
      end
    end
    checks++;if(bad)invalid_cases++;else valid_cases++;
    ok=(total===want_total)&&(gain===want_total)&&(rails===66'(want_rails))&&(invalid===bad);
    $fdisplay(trace_fd,"%0d,%h,%h,%h,%h,%h,%h,%h,%h,%0d,%0d,%0d",checks,ids,main_on,sub_on,weights,total,want_total,rails,66'(want_rails),invalid,bad,ok);
    if(!ok)begin $fflush(trace_fd);$fclose(trace_fd);$fatal(1,"TREE_MAPPING_REDUCE_FAIL check=%0d",checks);end
  endtask
  initial begin
    if(!$value$plusargs("mapping_trace=%s",trace_path))trace_path="tree_mapping.csv";
    trace_fd=$fopen(trace_path,"w");if(!trace_fd)$fatal(1,"cannot create tree trace");
    $fdisplay(trace_fd,"check,ids_hex,main_hex,sub_hex,weights_hex,total_hex,want_total_hex,rails_hex,want_rails_hex,invalid,want_invalid,pass");
    #200; // Allows a mapped-netlist global startup interval without claiming one was run.
    for(int row=0;row<3;row++)for(int term=0;term<3;term++)weights[(row*3+term)*48+:48]=48'((row+1)*100+term+1);
    for(int a=0;a<3;a++)for(int b=0;b<3;b++)if(a!=b)begin
      ids={5'(b),5'(a)};
      for(int m=0;m<16;m++)for(int sub=0;sub<4;sub++)begin main_on=4'(m);sub_on=2'(sub);check();end
    end
    for(int a=0;a<32;a++)for(int b=0;b<32;b++)begin
      ids={5'(b),5'(a)};
      for(int m=0;m<16;m++)for(int sub=0;sub<4;sub++)begin main_on=4'(m);sub_on=2'(sub);check();end
    end
    for(int n=0;n<1000;n++)begin
      ids=10'(random_word());main_on=4'(random_word());sub_on=2'(random_word());
      for(int term=0;term<9;term++)weights[term*48+:48]={16'(random_word()),random_word()};
      check();
    end
    if(checks!=66920 || valid_cases==0 || invalid_cases==0)$fatal(1,"tree coverage changed");
    $fflush(trace_fd);$fclose(trace_fd);
    $display("TREE_MAPPING_COMPLETE checks=%0d valid_cases=%0d invalid_cases=%0d",checks,valid_cases,invalid_cases);
    f=$fopen("tree_mapping.pass","w");if(!f)$fatal(1,"cannot write tree marker");
    $fdisplay(f,"TREE_MAPPING_COMPLETE checks=%0d",checks);$fclose(f);$finish;
  end
  initial begin #100000;$fatal(1,"TREE_MAPPING_WATCHDOG");end
endmodule
