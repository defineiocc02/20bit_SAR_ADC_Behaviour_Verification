`timescale 1ns/1ps
module tb_recon_pipe;
    logic clk=0, rstn=0;
    logic signed [8:0] k; logic [11:0] a2; logic [5:0] w;
    logic [19:0] code; logic ovf;
    integer fd,r,n,err,kk,aa,ww,ee;
    recon_d5_pipe dut(.clk(clk),.reset_n(rstn),.k_i(k),.adc2_i(a2),
                      .dither_i(w),.code_o(code),.overflow_o(ovf));
    always #5 clk=~clk;
    initial begin
        err=0;n=0;rstn=0; #12 rstn=1;
        fd=$fopen("vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d\n",kk,aa,ww,ee);
            if(r==4) begin
                @(negedge clk); k=kk[8:0]; a2=aa[11:0]; w=ww[5:0];
                @(negedge clk); @(negedge clk);
                n=n+1;
                if(code!==ee[19:0]) begin
                    err=err+1;
                    if(err<=4) $display("MISMATCH k=%0d a=%0d w=%0d got=%0d exp=%0d",kk,aa,ww,code,ee);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
