`timescale 1ns/1ps
`include "rtl_params.vh"
module review_leaf_tb;
  logic clk=0, rst_n=0;
  always #1 clk=~clk;
  int checks=0;
  logic signed [7:0] d=0;
  logic [3:0] rails;
  unit_therm therm(.main_logical('0),.sub_logical('0),.main_count('0),.sub_count('0),
    .bank_dither(d),.main_on(),.sub_on(),.dither_rail(rails));
  logic [14:0] cmp4=0;
  logic [3:0] count4;
  logic [6:0] cmp7=0;
  logic [8:0] count7;
  sadc_enc #(.P_B1(4)) enc4(.cmp_raw(cmp4),.sadc_code(count4));
  sadc_enc #(.P_B1(9),.P_N_CMP(7)) enc7(.cmp_raw(cmp7),.sadc_code(count7));

  logic wr=0;
  logic [31:0][0:0][47:0] w32;
  logic [0:0][127:0][47:0] w128;
  logic err32,err128;
  weight_store #(.P_N_SLICES(32),.P_N_UNITS(1)) ws32(
    .clk(clk),.rst_n(rst_n),.cfg_ready(1'b0),.clear_load(1'b0),.load_complete(),
    .wr_en(wr),.wr_slice(5'd31),.wr_unit(7'd0),.wr_data(48'd19),.err_write(err32),.w_q(w32));
  weight_store #(.P_N_SLICES(1),.P_N_UNITS(128)) ws128(
    .clk(clk),.rst_n(rst_n),.cfg_ready(1'b0),.clear_load(1'b0),.load_complete(),
    .wr_en(wr),.wr_slice(5'd0),.wr_unit(7'd127),.wr_data(48'd23),.err_write(err128),.w_q(w128));

  logic sample_en=0, conv_valid;
  logic [31:0] sample_idx;
  slice_alloc alloc(.clk(clk),.rst_n(rst_n),.cfg_ready(1'b1),.sample_en(sample_en),
    .acq_slices(),.conv_slices(),.conv_valid(conv_valid),.bank(),.sample_idx(sample_idx),.group());

  logic start=0;
  logic signed [5:0] numerator=0;
  logic [4:0] denominator=1;
  wire [3:0][5:0] q;
  wire [3:0] done,err,busy;
  for(genvar k=0;k<4;k++) begin:g_div
    localparam int UNROLL=(k==3)?7:k+1;
    div_floor #(.P_W_A(6),.P_W_D(5),.P_STAGES(UNROLL)) div(
      .clk(clk),.rst_n(rst_n),.start(start),.a(numerator),.d(denominator),
      .q(q[k]),.err(err[k]),.busy(busy[k]),.done(done[k]));
  end
  task automatic check(input bit condition,input string message);
    checks++;
    if(!condition) $fatal(1,"%s",message);
  endtask
  initial begin
    int expected,n;
    logic [3:0] seen;
    check($bits(enc4.cmp_raw)==15,"P_N_CMP must derive from P_B1");
    for(int v=-128;v<128;v++) begin
      d=8'(v); #1;
      expected=2+v; if(expected<0) expected=0; if(expected>4) expected=4;
      check(int'($countones(rails))==expected,$sformatf("signed dither rail count d=%0d rails=%b",v,rails));
      for(int j=1;j<4;j++) check(!(rails[j]&&!rails[j-1]),"rail thermometer order");
    end
    for(int v=0;v<32768;v++) begin
      cmp4=15'(v); cmp7=7'(v); #1;
      check(int'(count4)==$countones(cmp4),"4-bit parameterized popcount");
      check(int'(count7)==$countones(cmp7),"zero-extended non-power-of-two popcount");
    end
    repeat(2) @(negedge clk); rst_n=1;
    check(!conv_valid,"allocator warmup before first sample");
    sample_en=1; @(negedge clk); sample_en=0;
    check(conv_valid&&sample_idx==1,"allocator becomes valid after first sample");
    // Advance directly to the wrap boundary, then release to exercise real next-state logic.
    force alloc.n=32'hffffffff;
    @(negedge clk); release alloc.n; sample_en=1;
    @(negedge clk); sample_en=0;
    check(conv_valid&&sample_idx==0,"counter wrap must not re-enter warmup");
    wr=1; #0.1; check(!err32&&!err128,"maximum addressable geometry must accept writes");
    @(negedge clk); wr=0;
    check(w32[31][0]==19 && w128[0][127]==23,"maximum geometry stored last address");
    for(int a=-32;a<32;a++) for(int b=0;b<32;b++) begin
      expected=(b==0)?0:a/b;
      if(b!=0 && a<0 && (a%b)!=0) expected--;
      @(negedge clk); numerator=6'(a); denominator=5'(b); start=1;
      @(negedge clk); // already busy: this held request and changed inputs must be ignored
      numerator=6'sd17; denominator=5'd3;
      seen='0; n=0;
      while(seen!=4'hf) begin
        @(negedge clk); start=0; n++;
        for(int j=0;j<4;j++) if(done[j]) begin
          check(!seen[j],"divider duplicate completion"); seen[j]=1;
          check(int'($signed(q[j]))==expected,$sformatf("floor a=%0d d=%0d unroll=%0d got=%0d exp=%0d",a,b,j,$signed(q[j]),expected));
          check(err[j]==(b==0),"divide-by-zero flag");
        end
        if(n>10) $fatal(1,"divider liveness");
      end
    end
    $display("REVIEW_LEAF_COMPLETE checks=%0d",checks); $finish;
  end
  initial begin #500000; $fatal(1,"leaf regression timeout"); end
endmodule
