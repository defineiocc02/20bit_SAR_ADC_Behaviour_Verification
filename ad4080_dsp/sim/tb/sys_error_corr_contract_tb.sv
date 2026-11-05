`timescale 1ns/1ps
// KG/SHIFT 为显式数值测试参数，不代表 AD4080 厂家增益映射。
module sys_error_corr_contract_tb;
  localparam int KG=3,SH=13;
  logic clk=0,rst_n=0,valid_in=0,cancel=0;
  logic[19:0]x=0;logic signed[11:0]offset_code=0;logic[9:0]gain_code=512;
  wire[19:0]y,y_unbound;wire valid_out,clip_low,clip_high,processing_reject;
  wire uv,ul,uh,ur;
  int checks=0,compares=0,rejects=0,cancels=0;
  always #5 clk=~clk;
  sys_error_corr #(.P_GAIN_MAP_VALID(1),.K_GAIN(KG),.SHIFT(SH)) dut(.*);
  sys_error_corr missing(.clk(clk),.rst_n(rst_n),.valid_in(valid_in),.cancel(cancel),.x(x),.offset_code(offset_code),.gain_code(gain_code),.y(y_unbound),.valid_out(uv),.clip_low(ul),.clip_high(uh),.processing_reject(ur));
  function automatic longint signed floor_div(input longint signed n,input longint signed d);
    return n>=0?n/d:-((-n+d-1)/d);
  endfunction
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"CORR: %s",why);endtask
  task automatic sample(input int xi,input int gain,input int off);
    longint signed raw,expected,unity;bit low,high;
    raw=longint'(xi)+longint'(off)+
        floor_div(longint'(xi)*(longint'(gain)-64'sd512)*KG+(64'sd1<<(SH-1)),64'sd1<<SH);
    low=raw< -524288;high=raw>524287;expected=low?-524288:(high?524287:raw);
    unity=longint'(xi)+longint'(off);
    @(negedge clk);x=20'(xi);gain_code=10'(gain);offset_code=12'(off);valid_in=1;
    @(posedge clk);#1;
    require(processing_reject==(gain==0),"bound unsupported zero endpoint");
    require(ur==(gain!=512),"unbound nonunity did not reject");if(gain!=512)rejects++;
    @(negedge clk);valid_in=0;
    @(posedge clk);#1;require(!valid_out&&!uv,"latency shorter than two intervals");
    @(posedge clk);#1;
    require(valid_out==(gain!=0),"valid/reject outcome");
    if(gain!=0)begin require(longint'($signed(y))==expected,"signed rational oracle");require(clip_low==low&&clip_high==high,"saturation flags");compares++;end
    require(uv==(gain==512),"missing-map output valid");
    if(gain==512)begin
      require(longint'($signed(y_unbound))==(unity< -524288?-524288:(unity>524287?524287:unity)),"unity offset oracle");compares++;
    end
    @(posedge clk);#1;require(!valid_out&&!uv,"extra output valid");
  endtask
  initial begin
    repeat(3)@(negedge clk);rst_n=1;
    sample(0,512,0);sample(-524288,512,-2048);sample(524287,512,2047);
    sample(-500001,1023,-700);sample(500001,1023,700);sample(-3,513,0);sample(3,511,0);sample(42,0,1);
    for(int i=0;i<32;i++)sample((i%2==0?1:-1)*(100+i*713),1+(i*31)%1023,(i*97)%4096-2048);
    @(negedge clk);valid_in=1;x=20'd123;gain_code=512;
    @(posedge clk);#1;@(negedge clk);cancel=1;
    @(posedge clk);#1;require(!valid_out&&!uv&&!processing_reject&&!ur,"cancel priority");
    @(negedge clk);cancel=0;valid_in=0;repeat(4)begin @(posedge clk);#1;require(!valid_out&&!uv,"cancel leaked pending output");end;cancels++;
    require(compares>30&&rejects>20&&cancels==1,"vacuous correction checks");
    $display("SYS_ERROR_CORR_CONTRACT_COMPLETE comparisons=%0d unbound_rejects=%0d cancels=%0d checks=%0d",compares,rejects,cancels,checks);$finish;
  end
  initial begin #100000;$fatal(1,"CORR watchdog");end
endmodule
