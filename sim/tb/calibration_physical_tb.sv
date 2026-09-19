`timescale 1ns/1ps
// Independent wide-integer oracle for calibration across all 18 physical slices.
// No DUT partial sums, gains, numerator or divider values feed this oracle.
module calibration_physical_tb;
  logic clk=0, rst_n=0, ready=0, start=0;
  always #1 clk=~clk;
  logic [7:0][4:0] ids;
  logic [7:0][6:0] main_on;
  logic [7:0][0:0] sub_on;
  logic [1:0] rails;
  logic sampling;
  logic [17:0][7:0][47:0] weights;
  logic [11:0] adc2;
  logic signed [63:0] offset_q, injection;
  logic [31:0] sample_id, result_id;
  logic [19:0] dout;
  logic [4:0] flags;
  logic valid, busy;
  logic [31:0] rng=32'h759a320d;
  bit [17:0] exercised='0;
  int checks=0;
  recon_core #(.P_N_ACTIVE(8),.P_N_MAIN(7),.P_N_SUB(1),.P_N_SLICES(18),
               .P_DIT_N(2),.P_DIT_END(8)) dut(
    .clk(clk),.rst_n(rst_n),.cfg_ready(ready),.start(start),.sample_id(sample_id),
    .clr_ovf(1'b0),.sampling_mask_en(sampling),.slice_id(ids),
    .main_on(main_on),.sub_on(sub_on),.dither_rail(rails),.adc2_code(adc2),
    .inj_q(injection),.w_rom(weights),.offset_q(offset_q),
    .adc2_min_q(64'sd0),.adc2_max_q(64'sd8589934592),
    .dout(dout),.dout_valid(valid),.clip_low(),.clip_high(),.acc_ovf(),.gain_err(),
    .adc2_ovf(),.busy(busy),.result_sample_id(result_id),.result_flags(flags));
  function automatic logic [31:0] random_word();
    rng ^= rng << 13; rng ^= rng >> 17; rng ^= rng << 5; return rng;
  endfunction
  task automatic oracle(output logic [19:0] expected,output logic [4:0] expected_flags);
    logic signed [255:0] total,gain,rail_sum,w,f,n,d,q;
    bit on_bit;
    total=0;gain=0;rail_sum=0;
    for(int a=0;a<8;a++) for(int u=0;u<8;u++) begin
      w=256'(weights[ids[a]][u]); total+=w;
      on_bit=(u<7)?main_on[a][u]:sub_on[a][0];
      rail_sum+=(on_bit?-w:w);
      if(sampling && u>=6) rail_sum+=(rails[u-6]?w:-w);
      else gain+=w;
    end
    f=(2*256'(adc2)+1)*256'sd1048576;
    n=(((f-offset_q)<<<30)-(rail_sum<<<32)-total*injection+(gain<<<32))<<<20;
    d=gain<<<33; q=n/d;
    if(n<0 && n%d!=0) q--;
    expected_flags='0;
    if(q<0) begin expected=0;expected_flags[3]=1;end
    else if(q>=1048576) begin expected='1;expected_flags[4]=1;end
    else expected=q[19:0];
  endtask
  initial begin
    logic [19:0] expected;
    logic [4:0] expected_flags;
    int latency;
    for(int s=0;s<18;s++) for(int u=0;u<8;u++)
      weights[s][u]=48'd16777216+48'(s*907+u*2371)+48'(random_word()%1000000);
    ids='0;main_on='0;sub_on='0;rails='0;sampling=0;adc2=0;
    offset_q=0;injection=0;sample_id=0;
    repeat(2) @(negedge clk);rst_n=1;ready=1;
    for(int transaction=1;transaction<=2048;transaction++) begin
      @(negedge clk);
      if(busy) $fatal(1,"calibration did not become idle");
      for(int a=0;a<8;a++) begin
        ids[a]=5'((transaction+7*a)%18); // eight distinct physical slices
        exercised[ids[a]]=1;
        main_on[a]=7'(random_word()); sub_on[a][0]=1'(random_word());
      end
      adc2=12'(random_word());rails=2'(random_word());sampling=1'(transaction);
      offset_q=64'(int'(random_word()&32'hfffffff))-64'sd134217728;
      injection=64'(int'(random_word()&32'h7ffffff))-64'sd67108864;
      sample_id=32'(transaction);oracle(expected,expected_flags);start=1;
      @(negedge clk);start=0;latency=0;
      // Disturb all external sample fields after acceptance, including identity.
      ids='1;main_on='1;sub_on='1;adc2='1;injection='1;sample_id='1;
      while(!valid) begin
        @(negedge clk);latency++;
        if(latency>12) $fatal(1,"calibration liveness");
      end
      checks++;
      if(latency!=11 || result_id!=32'(transaction) || dout!==expected || flags!==expected_flags)
        $fatal(1,"physical calibration transaction=%0d id=%0d latency=%0d got=%0d exp=%0d flags=%b expflags=%b",
          transaction,result_id,latency,dout,expected,flags,expected_flags);
    end
    @(negedge clk);ids='0;main_on='0;sub_on='0;sample_id=32'd3000;start=1;
    @(negedge clk);start=0;
    while(!valid) @(negedge clk);
    if(!flags[1] || result_id!=3000) $fatal(1,"duplicate physical IDs accepted");
    if(exercised!='1) $fatal(1,"not all physical slice coefficient tables were exercised");
    $display("CALIBRATION_PHYSICAL_COMPLETE samples=%0d physical_slices=18",checks);$finish;
  end
  initial begin #100000; $fatal(1,"physical calibration watchdog");end
endmodule
