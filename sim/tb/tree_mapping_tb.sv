`timescale 1ns/1ps
// Independent scalar reference: never traverses the DUT's generated tree.
// Reuse unchanged against write_verilog -mode funcsim and the RTL witness.
module tree_mapping_tb;
    logic [6:0] flash3='0;
    logic [510:0] flash9='0;
    logic sampling=0;
    logic [9:0] ids='0;
    logic [3:0] main_on='0;
    logic [1:0] sub_on='0, dither_rail='0;
    logic [431:0] weights='0;
    wire [2:0] code3;
    wire [8:0] code9;
    wire [63:0] total,gain;
    wire signed [65:0] rails;
    wire invalid;
    integer checks=0, f, trace_fd;
    string trace_path;
    bit reducer_only;
    logic [31:0] rng=32'h20260930;
    tree_mapping_dut dut(.*);
    function automatic logic [31:0] random_word();
        rng^=rng<<13; rng^=rng>>17; rng^=rng<<5; return rng;
    endfunction
    task automatic check();
        longint unsigned want_total,want_gain,weight;
        longint signed want_rails;
        bit active,on,bad,ok;
        int count3,count9;
        #1;
        count3=0;count9=0;want_total=0;want_gain=0;want_rails=0;
        for(int i=0;i<7;i++)count3+=int'(flash3[i]);
        for(int i=0;i<511;i++)count9+=int'(flash9[i]);
        bad=(ids[4:0]>=3)||(ids[9:5]>=3)||(ids[4:0]==ids[9:5]);
        for(int s=0;s<3;s++)begin
            active=(int'(ids[4:0])==s)||(int'(ids[9:5])==s);
            for(int u=0;u<3;u++)begin
                weight=64'(weights[(s*3+u)*48+:48]);
                on=0;
                for(int a=0;a<2;a++)if(int'(ids[a*5+:5])==s)
                    on|=(u<2)?main_on[a*2+u]:sub_on[a];
                if(active)begin
                    want_total+=weight;
                    if(!sampling||u==0)want_gain+=weight;
                    want_rails+=on?-$signed(weight):$signed(weight);
                    if(sampling&&u>=1)want_rails+=dither_rail[u-1]?$signed(weight):-$signed(weight);
                end
            end
        end
        checks++;
        ok=(reducer_only||((code3===3'(count3))&&(code9===9'(count9))))&&
            (total===want_total)&&(gain===want_gain)&&(rails===66'(want_rails))&&(invalid===bad);
        $fdisplay(trace_fd,"%0d,%h,%h,%0d,%0d,%0d,%0d,%0b,%h,%h,%h,%h,%h,%h,%h,%h,%h,%h,%0b,%0b,%0b",
            checks,flash3,flash9,code3,count3,code9,count9,sampling,ids,main_on,sub_on,dither_rail,
            total,want_total,gain,want_gain,rails,66'(want_rails),invalid,bad,ok);
        // Preserve the actual failing row even if the simulator exits on $fatal.
        if(!ok)begin $fflush(trace_fd);$fclose(trace_fd);end
        if(!reducer_only&&(code3!==3'(count3)||code9!==9'(count9)))
            $fatal(1,"TREE_MAPPING_FLASH_FAIL check=%0d got3=%0d want3=%0d got9=%0d want9=%0d",checks,code3,count3,code9,count9);
        if(total!==want_total||gain!==want_gain||rails!==66'(want_rails)||invalid!==bad)
            $fatal(1,"TREE_MAPPING_REDUCE_FAIL check=%0d got_total=%0h want_total=%0h got_gain=%0h want_gain=%0h got_rails=%0h want_rails=%0h got_invalid=%0b want_invalid=%0b",checks,total,want_total,gain,want_gain,rails,66'(want_rails),invalid,bad);
    endtask
    initial begin
        reducer_only=$test$plusargs("reducer_only");
        if(!$value$plusargs("mapping_trace=%s",trace_path))trace_path="tree_mapping.csv";
        trace_fd=$fopen(trace_path,"w");
        if(trace_fd==0)$fatal(1,"could not create mapped witness trace");
        $fdisplay(trace_fd,"check,flash3_hex,flash9_hex,code3,want3,code9,want9,sampling,ids_hex,main_hex,sub_hex,dither_hex,total_hex,want_total_hex,gain_hex,want_gain_hex,rails_hex,want_rails_hex,invalid,want_invalid,pass");
        #200; // Also tolerates the vendor glbl startup interval.
        if(!reducer_only)begin
            for(int i=0;i<128;i++)begin flash3=7'(i);check();end
            flash3='0;
            for(int n=0;n<=511;n++)begin
                for(int i=0;i<511;i++)flash9[i]=(i<n);
                check();
            end
        end
        flash3='0;flash9='0;
        for(int s=0;s<3;s++)for(int u=0;u<3;u++)
            weights[(s*3+u)*48+:48]=48'((s+1)*100+u+1);
        for(int a=0;a<3;a++)for(int b=0;b<3;b++)if(a!=b)begin
            ids={5'(b),5'(a)};
            for(int m=0;m<16;m++)for(int sub=0;sub<4;sub++)
                for(int d=0;d<4;d++)for(int mode=0;mode<2;mode++)begin
                    main_on=4'(m);sub_on=2'(sub);dither_rail=2'(d);sampling=1'(mode);check();
                end
        end
        // All address encodings, including duplicates and out-of-range slices.
        for(int a=0;a<32;a++)for(int b=0;b<32;b++)begin
            ids={5'(b),5'(a)};main_on=4'(a);sub_on=2'(b);
            for(int d=0;d<4;d++)for(int mode=0;mode<2;mode++)begin
                dither_rail=2'(d);sampling=1'(mode);check();
            end
        end
        // Full 48-bit coefficient bit patterns and non-thermometer Flash codes.
        for(int n=0;n<1000;n++)begin
            flash3=7'(random_word());
            for(int i=0;i<511;i++)flash9[i]=1'(random_word());
            ids=10'(random_word());main_on=4'(random_word());sub_on=2'(random_word());
            dither_rail=2'(random_word());sampling=1'(random_word());
            for(int k=0;k<9;k++)weights[k*48+:48]={16'(random_word()),random_word()};
            check();
        end
        $fflush(trace_fd);$fclose(trace_fd);
        $display("TREE_MAPPING_COMPLETE checks=%0d",checks);
        f=$fopen("tree_mapping.pass","w");
        if(f==0)$fatal(1,"could not create witness pass marker");
        $fdisplay(f,"TREE_MAPPING_COMPLETE checks=%0d",checks);$fclose(f);
        $finish;
    end
    initial begin #100000;$fatal(1,"TREE_MAPPING_WATCHDOG");end
endmodule
