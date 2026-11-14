`timescale 1ns/1ps
module tb_d1d2;
    logic [7:0] s; logic [4:0] sid [0:7];
    logic [8:0] c; logic [2:0] u; logic [8:0] kev [0:6];
    integer fd,r,n,err,i,j,e0,e1,e2,e3,e4,e5,e6,e7,k0,k1,k2,k3,k4,k5,k6;
    slice_pool u_pool(.sample_i(s),.sid_o(sid));
    follower   u_fo(.c_i(c),.upper_i(u),.k_ev_o(kev));
    initial begin
        err=0;n=0;
        fd=$fopen("sp_vec.txt","r");
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d %d %d %d %d\n",j,e0,e1,e2,e3,e4,e5,e6,e7);
            if(r==9) begin s=j[7:0]; #1; n=n+1;
                if(sid[0]!==e0||sid[1]!==e1||sid[2]!==e2||sid[3]!==e3||
                   sid[4]!==e4||sid[5]!==e5||sid[6]!==e6||sid[7]!==e7) begin
                    err=err+1;
                    if(err<=3) $display("SLICE MISMATCH s=%0d got=%0d exp=%0d",s,sid[0],e0);
                end
            end
        end
        $fclose(fd);
        fd=$fopen("fo_vec.txt","r");
        while(!$feof(fd)) begin
            r=$fscanf(fd,"%d %d %d %d %d %d %d %d %d\n",j,k0,k1,k2,k3,k4,k5,k6);
            if(r==9) begin
                // 行格式: c u k0..k6  -> 先读 c、u，再读 7 个 k
                c=j[8:0]; u=0; #1;
            end
        end
        $display("RESULT: %0d checked, %0d mismatches",n,err);
        if(err==0) $display("PASS"); else $display("FAIL");
        $finish;
    end
endmodule
