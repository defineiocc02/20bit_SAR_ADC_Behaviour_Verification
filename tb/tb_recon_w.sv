`timescale 1ns/1ps
module tb_recon_w;
    logic [43:0] wm,g; logic [11:0] a2; logic [5:0] w;
    logic [19:0] code; logic ovf;
    integer fd,r,n,err,ae,we,ce;
    longint wme,ge;
    recon_d5_w dut(.wm_q30(wm),.g_q30(g),.adc2_i(a2),.dither_i(w),.code_o(code),.overflow_o(ovf));
    initial begin
        err=0;n=0;fd=$fopen("wvec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d\n",wme,ge,ae,we,ce);
            if(r==5) begin
                wm=wme; g=ge; a2=ae[11:0]; w=we[5:0]; #1; n=n+1;
                if(code!==ce[19:0]) begin
                    err=err+1;
                    if(err<=4) $display("MISMATCH wm=%0d g=%0d adc=%0d w=%0d got=%0d exp=%0d",wme,ge,ae,we,code,ce);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
