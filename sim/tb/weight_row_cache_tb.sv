`timescale 1ns/1ps
// Exact loader/cache invariant, independently recomputed from retained words.
// Includes the production geometry and both address maxima simultaneously.
module weight_row_cache_case #(parameter int NS=18, NU=71, SEED=1)(output logic done=0);
  logic clk=0,rst_n=0,ready=0,clear_load=0,wr_en=0;
  always #1 clk=~clk;
  logic [4:0] wr_slice=0;
  logic [6:0] wr_unit=0;
  logic [47:0] wr_data=0;
  wire [NS-1:0][NU-1:0][47:0] weights;
  wire [NS-1:0][63:0] row_total;
  wire [47:0] selected;
  wire complete,err_write;
  logic [NS-1:0][NU-1:0][47:0] model='0;
  logic [NS-1:0][NU-1:0] written='0;
  logic [31:0] rng=32'(SEED);
  int checks=0,accepted=0,rejected=0,clears=0;
  weight_store #(.P_N_SLICES(NS),.P_N_UNITS(NU)) dut(
    .clk(clk),.rst_n(rst_n),.cfg_ready(ready),.clear_load(clear_load),
    .wr_en(wr_en),.wr_slice(wr_slice),.wr_unit(wr_unit),.wr_data(wr_data),
    .load_complete(complete),.err_write(err_write),.w_q(weights),
    .row_total(row_total),.selected_weight(selected));
  function automatic logic [31:0] random_word();
    rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;
  endfunction
  function automatic bit check_rows(
    input logic [NS-1:0][NU-1:0][47:0] actual_words,expected_words,
    input logic [NS-1:0][63:0] actual_rows,input int step_index
  );
    /* verilator no_inline_task */
    logic [63:0] expected_row;
    for(int physical=0;physical<NS;physical++)begin
      expected_row=0;
      for(int unit_index=0;unit_index<NU;unit_index++)begin
        if(actual_words[physical][unit_index]!==expected_words[physical][unit_index])
          $fatal(1,"cache word NS=%0d NU=%0d step=%0d slice=%0d unit=%0d",NS,NU,step_index,physical,unit_index);
        expected_row+=64'(expected_words[physical][unit_index]);
      end
      if(actual_rows[physical]!==expected_row)
        $fatal(1,"cache invariant NS=%0d NU=%0d step=%0d slice=%0d got=%h expected=%h",NS,NU,step_index,physical,actual_rows[physical],expected_row);
    end
    return 1;
  endfunction
  task automatic step(input bit reset_ok,clr,lock,wr,input int s,u,input logic[47:0] data);
    bit idx_ok,accept;
    @(negedge clk);
    rst_n=reset_ok;clear_load=clr;ready=lock;wr_en=wr;
    wr_slice=5'(s);wr_unit=7'(u);wr_data=data;
    idx_ok=(s<NS)&&(u<NU);
    accept=wr&&!clr&&!lock&&idx_ok&&(data!=0)&&(data<48'h800000000000);
    #0.1;
    if(err_write!==(wr&&!accept))$fatal(1,"cache reject NS=%0d NU=%0d step=%0d",NS,NU,checks);
    @(posedge clk);
    if(!reset_ok)begin model='0;written='0;end
    else if(clr)begin written='0;clears++;end
    else if(accept)begin model[s][u]=data;written[s][u]=1;accepted++;end
    if(reset_ok&&wr&&!accept)rejected++;
    #0.1;checks++;
    if(!check_rows(weights,model,row_total,checks))$fatal(1,"cache state comparison failed");
    if(complete!==(&written))$fatal(1,"cache bitmap mismatch");
    if(selected!==(idx_ok?model[s][u]:48'd0))$fatal(1,"cache readback mismatch");
  endtask
  initial begin
    step(0,0,0,0,0,0,0);
    for(int s=0;s<NS;s++)for(int u=0;u<NU;u++)step(1,0,0,1,s,u,48'h7fffffffffff);
    if(!complete)$fatal(1,"cache full load missing");
    $display("CACHE_MAXIMUM_LOADED slices=%0d units=%0d words=%0d row_last=%h",NS,NU,NS*NU,row_total[NS-1]);
    step(1,0,1,1,0,0,1);                         // ready rejects
    step(1,0,0,1,0,0,0);                         // zero rejects
    step(1,0,0,1,0,0,48'h800000000000);           // sign bit rejects
    step(1,0,0,1,0,0,48'hffffffffffff);           // full word rejects
    step(1,0,0,1,(NS<32?NS:31),(NU<128?NU:127),7); // address endpoints
    step(1,0,0,0,0,0,1);                         // no write
    step(1,1,0,0,0,0,0);                         // clear retains data/cache
    step(1,0,0,1,0,0,1);                         // subtract retained maximum
    $display("CACHE_POST_CLEAR_REPLACEMENT slices=%0d units=%0d row0=%h",NS,NU,row_total[0]);
    step(1,1,0,1,0,0,48'h7fffffffffff);           // clear wins over write
    for(int s=0;s<NS;s++)for(int u=0;u<NU;u++)step(1,0,0,1,s,u,1);
    if(!complete)$fatal(1,"cache reload missing");
    step(1,0,0,1,0,0,48'h7fffffffffff);           // replace upwards
    step(1,0,0,1,0,0,1);                         // replace downwards
    step(1,1,1,0,0,0,0);                         // clear still acts while locked
    step(1,0,0,0,0,0,0);                         // read retained value
    step(1,0,0,1,0,0,48'h7fffffffffff);           // first post-clear replacement
    step(0,0,0,0,0,0,0);                         // reset clears cache too
    for(int n=0;n<1000;n++)begin
      automatic logic [31:0] control=random_word();
      automatic int s=int'(random_word()%32);
      automatic int u=int'(random_word()%128);
      automatic logic [47:0] data={16'(random_word()),random_word()};
      step(n%127!=0,n%37==0,control[0],control[1],s,u,data);
    end
    if(checks!=2*NS*NU+1016)$fatal(1,"cache coverage count got=%0d",checks);
    $display("WEIGHT_ROW_CACHE_CASE_PASS slices=%0d units=%0d steps=%0d accepted=%0d rejected=%0d clears=%0d",NS,NU,checks,accepted,rejected,clears);
    done=1;
  end
endmodule
module weight_row_cache_tb;
  wire [4:0] done;
  weight_row_cache_case #(.NS(18),.NU(71),.SEED(20260930)) production(.done(done[0]));
  weight_row_cache_case #(.NS(32),.NU(128),.SEED(2)) maximum(.done(done[1]));
  weight_row_cache_case #(.NS(32),.NU(1),.SEED(3)) slices(.done(done[2]));
  weight_row_cache_case #(.NS(1),.NU(128),.SEED(4)) units(.done(done[3]));
  weight_row_cache_case #(.NS(1),.NU(1),.SEED(5)) minimum(.done(done[4]));
  initial begin wait(&done);$display("WEIGHT_ROW_CACHE_COMPLETE geometries=5 steps=16150");$finish;end
  initial begin #30000;$fatal(1,"weight row cache watchdog");end
endmodule
