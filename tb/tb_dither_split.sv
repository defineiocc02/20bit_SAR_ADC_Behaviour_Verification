`timescale 1ns/1ps
module tb_dither_split;
    logic [5:0] word; logic [2:0] u; logic [4:0] l8; logic [5:0] t8;
    integer fd,r,n,err,ww,ue,le,te;
    dither_split dut(.word_i(word),.upper_o(u),.lower_q3_o(l8),.total_q3_o(t8));
    initial begin
        err=0;n=0;fd=$fopen("dith_vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d\n",ww,ue,le,te);
            if(r==4) begin
                word=ww[5:0]; #1; n=n+1;
                if(u!==ue[2:0]||l8!==le[4:0]||t8!==te[5:0]) begin
                    err=err+1;
                    if(err<=5) $display("MISMATCH w=%0d U=%0d/%0d L8=%0d/%0d T8=%0d/%0d",ww,u,ue,l8,le,t8,te);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
