`timescale 1ns/1ps
module tb_slice_pool;
    logic [7:0] s; logic [44:0] sid;
    integer fd,r,n,err,j,e0,e1,e2,e3,e4,e5,e6,e7;
    slice_pool dut(.sample_i(s),.sid_o(sid));
    initial begin
        err=0;n=0;fd=$fopen("sp_vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d %d %d %d %d\n",j,e0,e1,e2,e3,e4,e5,e6,e7);
            if(r==9) begin s=j[7:0]; #1; n=n+1;
                if(sid[0 +: 5]!==e0||sid[5 +: 5]!==e1||sid[10 +: 5]!==e2||sid[15 +: 5]!==e3||
                   sid[20 +: 5]!==e4||sid[25 +: 5]!==e5||sid[30 +: 5]!==e6||sid[35 +: 5]!==e7) begin
                    err=err+1;
                    if(err<=3) $display("MISMATCH s=%0d got %0d %0d.. exp %0d %0d..",s,sid[4:0],sid[39:35],e0,e7);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
