`timescale 1ns/1ps
// Exercise the real structural controller's mask network by forcing only
// state/decision registers. Scheduling is intentionally tested separately.
module bridge_mask_audit_tb;
  logic clk=0,rst_n=1,enable=1;
  logic [8:0] sid_force=0,code_force=0;
  sar_structural_ctrl dut(
    .clk(clk),.rst_n(rst_n),.enable(enable),.dem_en(1'b1),.bridge_en(1'b1),
    .sampling_en(1'b0),.quantizer_en(1'b0),.flash_therm(7'd0),.flash_valid(1'b1),
    .coarse_cmp_valid(2'b11),.coarse_cmp_ge(2'b00),.fine_cmp_valid(1'b1),.fine_cmp_ge(1'b0),
    .injection_q(64'sd0),.analog_bad(1'b0),.recon_busy(1'b0),
    .phase(),.quiet_sample(),.tp_clock(),.ra_az(),.ra_amplify(),.ref_precharge(),.ref_accurate(),
    .acquiring_mask(),.converting_mask(),.aux_charge_enable(),.hold_low_enable(),
    .coarse_compare_enable(),.coarse_acquire_enable(),.flash_acquire_enable(),.fine_acquire_enable(),
    .coarse_trial(),.quantizer_dither(),.acquisition_dither_rails(),.fine_trial(),.fine_compare_enable(),.flash_sample(),
    .slice_sel(),.main_sw(),.sub_sw(),.dither_sw(),.sw_valid(),.recon_start(),.context_id(),.context_slices(),
    .context_main(),.context_sub(),.context_rails(),.context_sampling(),.context_analog_bad(),.context_injection(),
    .context_coarse(),.context_sid(),.context_dither(),.fine_code(),.error_code(),
      // Optional CAAZ ports are explicit; the legacy profile stays disabled.
      .caaz_az_capture_ok(1'b1),
      .caaz_input_chop_neg(),
      .caaz_az_capture_en(),
      .caaz_az_hold(),
      .context_chop_neg(),
      .context_az_pair_id(),
      .context_az_valid());
  integer fd,details,fail_cases=0,total_cases=0,total_changed_bits=0,nominal_fail=0;
  integer first_sid=-1,first_code=-1;
  integer order[63],sub_order[8];
  logic [62:0] ref_main[8];logic [7:0] ref_sub[8];
  integer sid,code,a,p,j,r,c,k,rank,sc,nmain,nsub,changed,ref_nom,dut_nom,n;
  longint signed dut_q,ref_q,total_c,w;
  real error_uv,max_error_uv=0,sum_error_sq=0;
  string outdir;
  initial begin
    if(!$value$plusargs("outdir=%s",outdir))outdir=".";
    fd=$fopen({outdir,"/bridge_dut_scan.csv"},"w");
    details=$fopen({outdir,"/bridge_minimal_masks.csv"},"w");
    $fwrite(fd,"sid,code,changed_bits,dut_nominal_sum,reference_nominal_sum,dut_minus_reference_uv\n");
    $fwrite(details,"sid,code,slice,dut_main_hex,reference_main_hex,dut_sub_hex,reference_sub_hex\n");
    force dut.bank=1'b0;
    total_c=0;
    for(a=0;a<8;a++)begin
      for(p=0;p<63;p++)total_c+=8000000+131*a+37*p;
      for(p=0;p<8;p++)total_c+=1000000+79*a+53*p;
    end
    for(sid=0;sid<512;sid++)begin
      sid_force=9'(sid);
      // Older Verilator releases evaluate a force RHS when the statement is
      // executed, so refresh the forced state for each new test vector.
      force dut.sid=sid_force;
      // Forward construction from physical 8x8 rotation; omit the absent cell63.
      n=0;
      for(r=0;r<8;r++)for(c=0;c<8;c++)begin
        p=((r+(sid/8)%8)%8)*8+(c+sid%8)%8;
        if(p<63)begin order[n]=p;n++;end
      end
      if(n!=63)$fatal(1,"independent main geometry invalid");
      for(j=0;j<8;j++)sub_order[j]=(j+sid/64)%8;
      for(code=0;code<512;code++)begin
        code_force=9'(code);
        force dut.resolved[0]=code_force;
        #1;
        if(dut.sid!==sid_force || dut.resolved[0]!==code_force)
          $fatal(1,"bridge stimulus did not reach DUT sid=%0d code=%0d",sid,code);
        changed=0;dut_q=0;ref_q=0;dut_nom=0;ref_nom=0;
        for(a=0;a<8;a++)begin
          rank=(a+8-sid%8)%8;
          sc=code;
          if(sid>=256 && code>0 && code<511)begin if(rank<4)sc++;else sc--;end
          nmain=sc/8;nsub=sc%8;ref_main[a]='0;ref_sub[a]='0;
          for(j=0;j<nmain;j++)ref_main[a][order[j]]=1'b1;
          for(j=0;j<nsub;j++)ref_sub[a][sub_order[j]]=1'b1;
          changed+=$countones(dut.main_on[a]^ref_main[a])+$countones(dut.sub_on[a]^ref_sub[a]);
          dut_nom+=$countones(dut.main_on[a])*8+$countones(dut.sub_on[a]);
          ref_nom+=$countones(ref_main[a])*8+$countones(ref_sub[a]);
          for(p=0;p<63;p++)begin
            w=8000000+131*a+37*p;
            if(dut.main_on[a][p])dut_q+=w;if(ref_main[a][p])ref_q+=w;
          end
          for(p=0;p<8;p++)begin
            w=1000000+79*a+53*p;
            if(dut.sub_on[a][p])dut_q+=w;if(ref_sub[a][p])ref_q+=w;
          end
        end
        error_uv=6.0e6*real'(dut_q-ref_q)/real'(total_c);
        if(error_uv<0)begin if(-error_uv>max_error_uv)max_error_uv=-error_uv;end
        else if(error_uv>max_error_uv)max_error_uv=error_uv;
        sum_error_sq+=error_uv*error_uv;total_cases++;total_changed_bits+=changed;
        if(dut_nom!=ref_nom)nominal_fail++;
        if(changed!=0)begin
          fail_cases++;
          if(first_sid<0)begin
            first_sid=sid;first_code=code;
            for(a=0;a<8;a++)$fwrite(details,"%0d,%0d,%0d,%016h,%016h,%02h,%02h\n",sid,code,a,dut.main_on[a],ref_main[a],dut.sub_on[a],ref_sub[a]);
          end
        end
        $fwrite(fd,"%0d,%0d,%0d,%0d,%0d,%.12g\n",sid,code,changed,dut_nom,ref_nom,error_uv);
      end
    end
    $fclose(fd);$fclose(details);
    // The independently constructed reference includes the explicit sid[8]
    // activation contract. Any physical mask difference is a real failure.
    if(total_cases!=262144 || fail_cases!=0 || nominal_fail!=0)
      $fatal(1,"bridge physical masks differ from independent reference");
    $display("BRIDGE_AUDIT_COMPLETE cases=%0d mismatch_cases=%0d changed_bits=%0d nominal_sum_mismatches=%0d first_sid=%0d first_code=%0d synthetic_weight_max_uv=%.9g synthetic_weight_rms_uv=%.9g",total_cases,fail_cases,total_changed_bits,nominal_fail,first_sid,first_code,max_error_uv,$sqrt(sum_error_sq/real'(total_cases)));
    $finish;
  end
endmodule
