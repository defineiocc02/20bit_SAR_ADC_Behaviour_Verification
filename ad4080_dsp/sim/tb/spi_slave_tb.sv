`timescale 1ns/1ps
// 检查真实寄存器堆的实体访问协议；显式绑定参数属于测试输入。
module spi_slave_tb;
  logic clk=0,rst_n=0,cs_n=1,sclk=1,sdi=0,interface_ready=1,adc_idle=1;
  wire sdo,sdo_oe,we;wire[15:0]addr,wdata,rdata,status_long;
  wire[1:0]write_bytes,entity_bytes;
  wire address_valid,read_only,access_supported;wire[3:0]access_reject_reason;
  wire crc_en,send_status,single_inst,short_instr,addr_asc,strict_access;
  wire crc_err_pulse,clk_cnt_err_pulse,partial_err_pulse,addr_err_pulse,readonly_err_pulse,not_ready_err_pulse;
  wire spi_reject,bank_reject;wire[3:0]spi_reason,bank_reason;
  wire[7:0]status,status_short,interface_status;wire signed[11:0]offset_code;wire[9:0]gain_code;
  wire[1:0]filter_sel,fifo_mode;wire[3:0]dec_sel;wire[14:0]fifo_watermark;
  wire fsync_pulse,fifo_arm,fifo_clear,hi_clr,lo_clr,sw_reset_pulse;
  logic st_fifo_full=0,st_fifo_read_done=0,st_hi_flag=0,st_lo_flag=0;
  int checks=0,requests=0,read_compares=0,crc_compares=0,status_compares=0;
  int crc_errors=0,clock_errors=0,partial_errors=0,notready_errors=0,prepare_end_cases=0;
  int reason_count[16];bit frame_crc,frame_status;
  logic[15:0]last_instruction_status;
  always #5 clk=~clk;
  spi_slave #(.P_SHORT_ADDR_BITS(7),.P_WRITE_SDO_DRIVE(0)) spi(
    .clk(clk),.rst_n(rst_n),.cs_n(cs_n),.sclk(sclk),.sdi(sdi),.sdo(sdo),.sdo_oe(sdo_oe),
    .interface_ready(interface_ready),.crc_en(crc_en),.send_status(send_status),.single_inst(single_inst),
    .short_instr(short_instr),.addr_asc(addr_asc),.strict_access(strict_access),.status_long(status_long),
    .status_short(status_short),.addr(addr),.wdata(wdata),.write_bytes(write_bytes),.we(we),.rdata(rdata),
    .entity_bytes(entity_bytes),.address_valid(address_valid),.read_only(read_only),.access_supported(access_supported),
    .access_reject_reason(access_reject_reason),.crc_err_pulse(crc_err_pulse),.clk_cnt_err_pulse(clk_cnt_err_pulse),
    .partial_err_pulse(partial_err_pulse),.addr_err_pulse(addr_err_pulse),.readonly_err_pulse(readonly_err_pulse),
    .not_ready_err_pulse(not_ready_err_pulse),.cfg_reject(spi_reject),.cfg_reject_reason(spi_reason));
  ad4080_regs #(.P_MULTIBYTE_ORDER(0),.P_OFFSET_LSB_ADDR(37),.P_PRODUCT_ID_L(55),.P_SHORT_ADDR_BITS(7)) bank(
    .clk(clk),.rst_n(rst_n),.addr(addr),.wdata(wdata),.write_bytes(write_bytes),.we(we),.rdata(rdata),
    .entity_bytes(entity_bytes),.address_valid(address_valid),.read_only(read_only),.access_supported(access_supported),
    .access_reject_reason(access_reject_reason),.adc_idle(adc_idle),.gpio_levels(4'ha),
    .capture_active(1'b0),.capture_incomplete(1'b0),.capture_io_active(1'b0),
    .st_fifo_full(st_fifo_full),.st_fifo_read_done(st_fifo_read_done),.st_hi_flag(st_hi_flag),.st_lo_flag(st_lo_flag),
    .crc_err_pulse(crc_err_pulse),.clk_cnt_err_pulse(clk_cnt_err_pulse),.partial_err_pulse(partial_err_pulse),
    .addr_err_pulse(addr_err_pulse),.readonly_err_pulse(readonly_err_pulse),.not_ready_err_pulse(not_ready_err_pulse),
    .hi_clr(hi_clr),.lo_clr(lo_clr),.offset_code(offset_code),.gain_code(gain_code),.filter_sel(filter_sel),.dec_sel(dec_sel),
    .fsync_pulse(fsync_pulse),.fifo_mode(fifo_mode),.fifo_watermark(fifo_watermark),.fifo_arm(fifo_arm),.fifo_clear(fifo_clear),
    .wm_wb_valid(1'b0),.wm_wb_data(15'd0),.event_hyst(),.event_hi(),.event_lo(),.event_en(),.hi_route(),.lo_route(),
    .gpio_dir(),.gpio_reg(),.gpio_sel(),.crc_en(crc_en),.send_status(send_status),.addr_asc(addr_asc),
    .single_inst(single_inst),.short_instr(short_instr),.strict_access(strict_access),.sw_reset_pulse(sw_reset_pulse),
    .status(status),.interface_status(interface_status),.status_long(status_long),.status_short(status_short),
    .cfg_reject(bank_reject),.cfg_reject_reason(bank_reason));
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"SPI_ENTITY: %s",why);endtask
  always @(posedge clk) begin
    if(rst_n&&we)begin requests++;require(write_bytes==1||write_bytes==2,"invalid entity write size");end
    if(rst_n&&spi.cs_rise&&spi.optional_entity&&spi.phase==3'd2)prepare_end_cases++;
    #1;
    if(rst_n)begin
      if(crc_err_pulse)crc_errors++;
      if(clk_cnt_err_pulse)clock_errors++;
      if(partial_err_pulse)partial_errors++;
      if(not_ready_err_pulse)notready_errors++;
      if(spi_reject)reason_count[spi_reason]++;
      if(bank_reject)reason_count[bank_reason]++;
    end
  end
  function automatic[7:0]crc_byte(input logic[7:0]state,input logic[7:0]data);
    logic[7:0]c;c=state^data;
    for(int i=0;i<8;i++)c=c[7]?((c<<1)^8'h07):(c<<1);
    return c;
  endfunction
  task automatic begin_frame;
    @(negedge clk);sclk=1;sdi=0;frame_crc=crc_en;frame_status=send_status;cs_n=0;
    repeat(8)@(negedge clk);
  endtask
  task automatic end_frame;
    cs_n=1;sclk=1;sdi=0;repeat(8)@(negedge clk);#1;require(!sdo_oe,"CS high must release SDO");
  endtask
  task automatic bit_io(input bit tx,output bit rx,input int expected_oe);
    sdi=tx;#80;sclk=0;#80;sclk=1;rx=sdo;
    if(expected_oe>=0)require(sdo_oe==1'(expected_oe),"wire SDO output-enable ownership");#80;
  endtask
  task automatic byte_io(input logic[7:0]tx,output logic[7:0]rx,input int expected_oe);
    bit b;for(int i=7;i>=0;i--)begin bit_io(tx[i],b,expected_oe);rx[i]=b;end
  endtask
  task automatic instruction(input logic[15:0]word);
    logic[7:0]a,b;byte_io(word[15:8],a,frame_status?1:0);byte_io(word[7:0],b,frame_status?1:0);last_instruction_status={a,b};
  endtask
  function automatic[7:0]instruction_crc(input logic[15:0]word);
    return crc_byte(crc_byte(8'ha5,word[15:8]),word[7:0]);
  endfunction
  task automatic finish_crc(input logic[7:0]host,input logic[7:0]device,input bit good);
    logic[7:0]r;byte_io(good?host:(host^8'h01),r,1);require(r===device,"device CRC differs from independent byte oracle");crc_compares++;
  endtask
  task automatic crc_then_prepare_end(input logic[7:0]host,input logic[7:0]device);
    logic[7:0]r;bit b;
    for(int i=7;i>0;i--)begin bit_io(host[i],b,1);r[i]=b;end
    sdi=host[0];#80;sclk=0;#80;sclk=1;r[0]=sdo;require(sdo_oe,"last CRC output ownership");
    // 在同步采样完成前保持 SDI 稳定，仅提前改变 CS。
    #20;cs_n=1;repeat(8)@(negedge clk);
    require(r===device&&!sdo_oe,"first CRC entity early CS end");crc_compares++;
  endtask
  task automatic write1(input logic[14:0]address,input logic[7:0]value);
    logic[7:0]r,c;logic[15:0]word;word={1'b0,address};begin_frame();instruction(word);byte_io(value,r,0);
    if(frame_crc)begin c=crc_byte(instruction_crc(word),value);finish_crc(c,c,1);end
    end_frame();
  endtask
  task automatic read1(input logic[14:0]address,input logic[7:0]expected);
    logic[7:0]r,h,d;logic[15:0]word;word={1'b1,address};begin_frame();instruction(word);byte_io(8'd0,r,1);
    require(r===expected,"nonzero/known byte readback");read_compares++;
    if(frame_crc)begin h=crc_byte(instruction_crc(word),8'd0);d=crc_byte(instruction_crc(word),expected);finish_crc(h,d,1);end
    end_frame();
  endtask
  task automatic write2_desc(input logic[14:0]high_addr,input logic[15:0]value);
    logic[7:0]r;begin_frame();require(!frame_crc,"fixture write2 expects CRC off");instruction({1'b0,high_addr});
    byte_io(value[15:8],r,0);byte_io(value[7:0],r,0);end_frame();
  endtask
  initial begin
    logic[7:0]r,c,h,d;int before_requests,before_errors,before_partial,before_reason;
    for(int i=0;i<16;i++)reason_count[i]=0;
    repeat(4)@(negedge clk);rst_n=1;repeat(8)@(negedge clk);
    require(strict_access&&!crc_en&&gain_code==512,"reset metadata/control");
    write1(15'h000a,8'h5a);read1(15'h000a,8'h5a);
    // 严格两字节实体须缓存第一个线上字节，完整后仅提交一次。
    before_requests=requests;write2_desc(15'h0026,16'h0f85);
    require(requests==before_requests+1&&$signed(offset_code)==-123,"strict entity atomic signed OFFSET");
    begin_frame();instruction(16'h8026);byte_io(0,r,1);require(r==8'h0f,"strict read first high byte");read_compares++;
    byte_io(0,r,1);require(r==8'h85,"strict read second low byte");read_compares++;end_frame();
    before_requests=requests;before_partial=partial_errors;
    begin_frame();instruction(16'h0026);byte_io(8'h01,r,0);end_frame();
    require(requests==before_requests&&offset_code==12'hf85&&partial_errors==before_partial+1,"partial strict entity committed or unreported");
    before_requests=requests;before_partial=partial_errors;
    begin_frame();instruction(16'h0025);byte_io(8'h01,r,-1);end_frame();
    require(requests==before_requests&&offset_code==12'hf85&&partial_errors==before_partial+1,"wrong strict start byte accepted");
    // SINGLE_INST 为下一实体重新接收指令，不能把它解释为相邻寄存器字节。
    write1(15'h0001,8'h80);require(single_inst,"single instruction enable");
    before_requests=requests;begin_frame();instruction(16'h000a);byte_io(8'h3c,r,0);instruction(16'h0019);byte_io(8'ha5,r,0);end_frame();
    require(requests==before_requests+2,"two noncontiguous entities under one CS");read1(15'h000a,8'h3c);read1(15'h0019,8'ha5);
    // 不完整指令不得生成数据载荷提交或寄存器写请求。
    before_requests=requests;before_errors=clock_errors;begin_frame();byte_io(8'h00,r,0);end_frame();
    require(requests==before_requests&&clock_errors==before_errors+1,"instruction abort");
    // 只读或非法地址被拒绝时，不得向寄存器堆发出写请求。
    before_requests=requests;begin_frame();instruction(16'h0003);byte_io(8'hff,r,-1);end_frame();
    require(requests==before_requests&&reason_count[4]>0,"read-only write");
    begin_frame();instruction(16'h9234);byte_io(8'd0,r,-1);end_frame();require(reason_count[3]>0,"invalid address");
    // 首实体读 CRC 分开覆盖主机填充字节与实际返回数据，避免相同输入掩盖错误。
    write1(15'h0010,8'h62);require(crc_en,"CRC framing enable");
    read1(15'h000a,8'h3c);write1(15'h000a,8'ha7);read1(15'h000a,8'ha7);
    h=crc_byte(instruction_crc(16'h800a),8'd0);d=crc_byte(instruction_crc(16'h800a),8'ha7);require(h!=d,"read CRC comparison would be vacuous");
    before_requests=requests;before_errors=crc_errors;
    begin_frame();instruction(16'h000a);byte_io(8'h55,r,0);c=crc_byte(instruction_crc(16'h000a),8'h55);finish_crc(c,c,0);
    // 当前帧发生 CRC 错误后，后续正确指令和 CRC 也不能恢复本帧写入。
    byte_io(8'h00,r,0);byte_io(8'h0a,r,0);byte_io(8'h66,r,0);c=crc_byte(instruction_crc(16'h000a),8'h66);byte_io(c,r,0);end_frame();
    require(requests==before_requests&&crc_errors==before_errors+1,"CRC poison allowed later write");read1(15'h000a,8'ha7);
    // 首个 CRC 实体之后的流式访问未绑定，须明确拒绝。
    write1(15'h0001,8'h00);require(!single_inst,"stream enable");
    before_errors=clock_errors;
    begin_frame();instruction(16'h8028);byte_io(0,r,1);require(r==2,"GAIN first byte");byte_io(0,r,1);require(r==0,"GAIN second byte");
    h=crc_byte(crc_byte(instruction_crc(16'h8028),0),0);d=crc_byte(crc_byte(instruction_crc(16'h8028),2),0);finish_crc(h,d,1);end_frame();
    require(clock_errors==before_errors,"legal first CRC stream end got phantom CLOCK");
    before_errors=clock_errors;
    begin_frame();instruction(16'h8028);byte_io(0,r,1);require(r==2,"prepare-end GAIN high");byte_io(0,r,1);require(r==0,"prepare-end GAIN low");read_compares+=2;
    crc_then_prepare_end(h,d);
    require(clock_errors==before_errors&&prepare_end_cases>0,"optional PREPARE CS end got CLOCK or was not exercised");
    before_reason=reason_count[1];begin_frame();instruction(16'h8028);byte_io(0,r,1);byte_io(0,r,1);finish_crc(h,d,1);byte_io(0,r,-1);end_frame();
    require(reason_count[1]==before_reason+1,"unbound second CRC entity not rejected");
    // 使用旧 CRC 帧规则关闭 CRC，再独立设置状态输入。
    write1(15'h0010,8'h30);require(!crc_en&&send_status,"status framing switch");
    write1(15'h0011,8'hff);write1(15'h0014,8'h09);
    st_fifo_full=1;st_hi_flag=1;interface_ready=0;before_requests=requests;before_errors=notready_errors;
    begin_frame();byte_io(8'h80,r,-1);end_frame();require(requests==before_requests&&notready_errors==before_errors+1,"not-ready frame");interface_ready=1;
    repeat(8)@(negedge clk);
    require(status==8'ha0&&interface_status==8'h80,"independent status register packing");
    require(status_long==16'h2820&&status_short==8'h20,"Table16/17 packing, no repeated-byte golden");status_compares+=2;
    read1(15'h000a,8'ha7);require(last_instruction_status==16'h2820,"long instruction status serialization");status_compares++;
    // 短格式显式绑定为七位地址；状态按 Table 17 输出一个字节。
    write1(15'h0001,8'h88);require(short_instr&&single_inst,"bound short format enable");
    begin_frame();byte_io(8'h8a,r,1);require(r==8'h20,"short status packing");status_compares++;
    byte_io(0,r,1);require(r==8'ha7,"short instruction data");read_compares++;end_frame();
    require(requests>=10&&read_compares>=9&&crc_compares>=7&&status_compares==4&&partial_errors>0&&crc_errors>0&&notready_errors>0&&prepare_end_cases>0,"nonzero protocol comparisons");
    $display("SPI_ENTITY_CONTRACT_COMPLETE requests=%0d read_compares=%0d crc_compares=%0d status_compares=%0d partial_errors=%0d crc_errors=%0d notready_errors=%0d prepare_end_cases=%0d checks=%0d",requests,read_compares,crc_compares,status_compares,partial_errors,crc_errors,notready_errors,prepare_end_cases,checks);$finish;
  end
  initial begin #1000000;$fatal(1,"SPI entity watchdog");end
endmodule
