`timescale 1ns/1ps
// 检查未绑定参数的真实寄存器堆：受影响功能须明确拒绝，无关 CSR 仍可访问。
module ad4080_regs_rejection_tb;
  logic clk=0,rst_n=0,we=0;logic[15:0]addr=0,wdata=0;
  wire[15:0]rdata;wire[1:0]entity_bytes;wire address_valid,read_only,access_supported;
  wire[3:0]access_reject_reason,cfg_reject_reason;wire cfg_reject,short_instr;
  wire[9:0]gain_code;wire[1:0]filter_sel,fifo_mode;wire[14:0]fifo_watermark;
  int checks=0,rejects=0,metadata_rejects=0,preserved=0;
  always #5 clk=~clk;
  ad4080_regs dut(.clk(clk),.rst_n(rst_n),.addr(addr),.wdata(wdata),.write_bytes(2'd1),.we(we),
    .rdata(rdata),.entity_bytes(entity_bytes),.address_valid(address_valid),.read_only(read_only),.access_supported(access_supported),.access_reject_reason(access_reject_reason),
    .adc_idle(1'b1),.gpio_levels(4'd0),.capture_active(1'b0),.capture_incomplete(1'b0),.capture_io_active(1'b0),
    .st_fifo_full(1'b0),.st_fifo_read_done(1'b0),.st_hi_flag(1'b0),.st_lo_flag(1'b0),
    .crc_err_pulse(1'b0),.clk_cnt_err_pulse(1'b0),.partial_err_pulse(1'b0),.addr_err_pulse(1'b0),.readonly_err_pulse(1'b0),.not_ready_err_pulse(1'b0),
    .hi_clr(),.lo_clr(),.offset_code(),.gain_code(gain_code),.filter_sel(filter_sel),.dec_sel(),.fsync_pulse(),.fifo_mode(fifo_mode),.fifo_watermark(fifo_watermark),.fifo_arm(),.fifo_clear(),
    .wm_wb_valid(1'b0),.wm_wb_data(15'd0),.event_hyst(),.event_hi(),.event_lo(),.event_en(),.hi_route(),.lo_route(),.gpio_dir(),.gpio_reg(),.gpio_sel(),
    .crc_en(),.send_status(),.addr_asc(),.single_inst(),.short_instr(short_instr),.strict_access(),.sw_reset_pulse(),.status(),.interface_status(),.status_long(),.status_short(),.cfg_reject(cfg_reject),.cfg_reject_reason(cfg_reject_reason));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"REG_REJECT: %s",why);endtask
  task automatic meta_unbound(input logic[15:0]a);
    @(negedge clk);addr=a;we=0;#1;require(address_valid&&!access_supported&&access_reject_reason==1,"unbound metadata looked usable");metadata_rejects++;
  endtask
  task automatic write_checked(input logic[15:0]a,input logic[7:0]v,input int reason);
    @(negedge clk);addr=a;wdata={8'd0,v};we=1;@(posedge clk);#1;
    require(cfg_reject==(reason!=0),"bank write rejection flag");
    if(reason!=0)begin require(cfg_reject_reason==4'(reason),"named rejection reason");rejects++;end
    @(negedge clk);we=0;
  endtask
  task automatic read_checked(input logic[15:0]a,input logic[7:0]v);
    @(negedge clk);addr=a;#1;require(access_supported&&rdata[7:0]==v,"known preserved CSR value");preserved++;
  endtask
  initial begin
    repeat(3)@(negedge clk);rst_n=1;
    meta_unbound(16'h0004);meta_unbound(16'h0026);meta_unbound(16'h0028);
    write_checked(16'h000a,8'h5a,0);read_checked(16'h000a,8'h5a);
    write_checked(16'h0001,8'h08,1);require(!short_instr,"unbound short format stored");
    // 显式选择普通字节访问，检查其合法性；不将它当作严格访问失败后的默认替代。
    write_checked(16'h0010,8'h00,0);
    meta_unbound(16'h0025); // 普通模式仍须拒绝未绑定 OFFSET 地址的访问。
    write_checked(16'h0027,8'h01,1);require(gain_code==512,"missing gain map silently applied");preserved++;
    write_checked(16'h0028,8'h00,1);require(gain_code==512,"unbound gain0 endpoint accepted");preserved++;
    write_checked(16'h0029,8'h03,1);require(filter_sel==0,"missing COMP coefficients became identity");preserved++;
    write_checked(16'h001a,8'h03,2);read_checked(16'h001a,0);
    write_checked(16'h0002,8'h01,2);read_checked(16'h0002,0);
    write_checked(16'h001e,8'h00,0);write_checked(16'h001d,8'h03,0);require(fifo_watermark==3,"explicit invalid mode11 watermark fixture");
    write_checked(16'h001c,8'h03,9);require(fifo_mode==0,"bad mode11 WM changed mode");preserved++;
    require(rejects==7&&metadata_rejects==4&&preserved>=7,"nonzero rejection/preservation coverage");
    $display("AD4080_REG_REJECTION_COMPLETE rejected_writes=%0d metadata_rejects=%0d preserved_compares=%0d checks=%0d",rejects,metadata_rejects,preserved,checks);$finish;
  end
  initial begin #10000;$fatal(1,"register rejection watchdog");end
endmodule
