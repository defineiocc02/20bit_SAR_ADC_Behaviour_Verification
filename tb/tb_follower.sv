`timescale 1ns/1ps
module tb_follower;
    logic [8:0] c; logic [2:0] u; logic [62:0] kev;
    integer fd,r,n,err,cc,uu,k0,k1,k2,k3,k4,k5,k6;
    follower dut(.c_i(c),.upper_i(u),.k_ev_o(kev));
    initial begin
        err=0;n=0;fd=$fopen("fo_vec.txt","r");
        if(fd==0) begin $display("FATAL"); $finish; end
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d %d %d %d %d\n",cc,uu,k0,k1,k2,k3,k4,k5,k6);
            if(r==9) begin
                c=cc[8:0]; u=uu[2:0]; #1; n=n+1;
                if(kev[0 +: 9]!==k0[8:0]||kev[9 +: 9]!==k1[8:0]||kev[18 +: 9]!==k2[8:0]||kev[27 +: 9]!==k3[8:0]||
                   kev[36 +: 9]!==k4[8:0]||kev[45 +: 9]!==k5[8:0]||kev[54 +: 9]!==k6[8:0]) begin
                    err=err+1;
                    if(err<=3) $display("FOLLOW MISMATCH c=%0d u=%0d got %0d,%0d exp %0d,%0d",
                                        cc,uu,kev[8:0],kev[62:54],k0,k6);
                end
            end
        end
        $display("RESULT: %0d vectors, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
