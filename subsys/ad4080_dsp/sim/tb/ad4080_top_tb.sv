`timescale 1ns/1ps
// =============================================================================
// 文件名     : ad4080_top_tb.sv
// 模块名     : ad4080_top_tb
// 版本       : v1.0
// 功能       : 验证旧并行 DSP、配置 SPI、FIFO 和已完成记录的软复位行为。
// 设计说明   :
//   * 主机驱动明确提供停止与排空确认，不从输入空拍推断 ADC 空闲。
//   * CS/SCLK/SDI 从真实配置引脚进入，状态在核心处理沿后检查。
//   * 旧路径只推进同域 valid。器件 CNV 拍点用独立 ad4080_cnv_phases_tb 验证。
//   * 完成记录允许经合法软复位保留。未完成的捕获和正在进行的读回禁止软复位。
// 变更记录   : 给新增的可选物理时基和配置交接端提供确定的旧路径绑值。
// =============================================================================
module ad4080_top_tb;
  localparam int DEPTH=32;
  logic clk=0,rst_n=0,cs_n=1,sclk=1,sdi=0,interface_ready=1,adc_idle=0;
  logic valid_in=0,filter_sync=0,ext_event=0,arm=0,rd_en=0;
  logic[19:0]x=0;logic[3:0]gpio_i=4'ha;
  wire sdo,sdo_oe,dout_valid,correction_clip_low,correction_clip_high,cfg_reject,processing_reject;
  wire[3:0]gpio_o,gpio_oe,cfg_reject_reason;wire[19:0]dout;wire[7:0]status;
  bit ascending=0;int checks=0,read_compares=0,resets=0,idle_assertions=0,rejections=0,route_checks=0;
  always #5 clk=~clk;
  // =========================================================================
  // 1. 保持独立数值用例的绑定
  // =========================================================================
  // 参数默认关闭全部物理适配，旧样本从 valid_in 直接推进；为 .* 补全新增端口。
  ad4080_top #(.FIFO_DEPTH(DEPTH),.P_MULTIBYTE_ORDER(0),.P_OFFSET_LSB_ADDR(37),.P_PRODUCT_ID_L(55),.P_SHORT_ADDR_BITS(7),.P_WRITE_SDO_DRIVE(0)) dut(
    // 独立数值或协议配置没有 AON 响应；真实功耗命令在产品台架验证。
    .pwr_cfg_full_allow_i(1'b1), .pwr_cfg_poll_allow_i(1'b0), // 该台架没有软初始化中的 AON 轮询窗口。
    .pwr_cmd_ready_i(1'b0), .pwr_cmd_valid_o(),
    .pwr_cmd_op_o(), .pwr_cmd_value_o(), .pwr_rsp_valid_i(1'b0),
    .pwr_rsp_accept_i(1'b0), .pwr_rsp_op_i(2'b00), .pwr_mode_i(2'b00),
    .pwr_ldo_pd_i(2'b00), .rom_crc_error_i(1'b0),

    .por_ana_event_i(1'b0),
    .cnv_tick_i(1'b0), .conversion_timing_error_i(1'b0), .cnv_data_timing_error_o(),
    .phy_fifo_full_i(1'b0), .phy_fifo_read_done_i(1'b0), .csr_fifo_full_i(1'b0), .csr_fifo_read_done_i(1'b0),
    .data_config_ready_i(1'b0), .sample_tag_i(32'd0), .data_intf_config_a_o(), .data_intf_config_b_o(), .data_intf_config_c_o(),
    .continuous_bypass_o(), .continuous_enable_o(), .continuous_flush_o(), .filter_config_o(), .continuous_word_valid_o(), .continuous_word_o(), .continuous_word_tag_o(), .*
  );
  task automatic require(input bit yes,input string why);checks++;if(!yes)$fatal(1,"DSP_TOP: %s",why);endtask
  always @(posedge clk)begin
    // SPI 接受、寄存器提交和错误各有脉冲。用累计数核对整条主机帧。
    #1;if(rst_n)begin if(dut.unused_sw_reset_pulse)resets++;if(cfg_reject)rejections++;end
  end
  task automatic byte_io(input logic[7:0]tx,output logic[7:0]rx,input int oe);
    for(int i=7;i>=0;i--)begin sdi=tx[i];#80;sclk=0;#80;sclk=1;rx[i]=sdo;if(oe>=0)require(sdo_oe==1'(oe),"SDO ownership");#80;end
  endtask
  task automatic frame_start;
    // 维持 CS 跨过输入同步器，避免把同步延迟误判为指令拒绝。
    @(negedge clk);cs_n=0;sclk=1;sdi=0;repeat(8)@(negedge clk);
  endtask
  task automatic frame_end;
    // CS 高等足够周期才观察 OE，验证下一次事务不会继承本次 SDO。
    cs_n=1;sclk=1;sdi=0;repeat(8)@(negedge clk);#1;require(!sdo_oe,"CS-high SPI release");
  endtask
  task automatic write1(input logic[14:0]a,input logic[7:0]v);
    logic[7:0]r;frame_start();byte_io({1'b0,a[14:8]},r,0);byte_io(a[7:0],r,0);byte_io(v,r,0);frame_end();
  endtask
  task automatic write2(input logic[14:0]base,input logic[15:0]v);
    // 严格模式的两个字节作为一个实体交付，首地址/字节序成对改变。
    logic[14:0]a;logic[7:0]r;a=ascending?base:base+15'd1;
    frame_start();byte_io({1'b0,a[14:8]},r,0);byte_io(a[7:0],r,0);
    byte_io(ascending?v[7:0]:v[15:8],r,0);byte_io(ascending?v[15:8]:v[7:0],r,0);frame_end();
  endtask
  task automatic read_reg(input logic[14:0]a,input logic[7:0]expected);
    // 真实 SDO 返回用固定期望比较，避免检查到内部寄存器但漏掉 SPI 接线。
    logic[7:0]r;frame_start();byte_io({1'b1,a[14:8]},r,0);byte_io(a[7:0],r,0);byte_io(0,r,1);
    require(r===expected,"known CSR readback");read_compares++;frame_end();
  endtask
  task automatic quiesce;
    @(negedge clk);valid_in=0;
    // 驱动先停止新工作，再等待 DSP 流水排空，最后才声明 adc_idle。
    repeat(8)@(negedge clk);require(!dut.chain_valid,"in-flight chain not drained");
    adc_idle=1;idle_assertions++;
  endtask
  task automatic push(input int first,input int n);
    adc_idle=0;
    for(int i=0;i<n;i++)begin @(negedge clk);valid_in=1;x=20'(first+i);end
    quiesce();
  endtask
  task automatic read_sample(input int expected);
    @(negedge clk);rd_en=1;@(posedge clk);#1;
    require(dout_valid&&dout===20'(expected),$sformatf("captured record data/order got=%0d expected=%0d valid=%0b",$signed(dout),expected,dout_valid));read_compares++;
    @(negedge clk);rd_en=0;@(posedge clk);#1;require(!dout_valid,"duplicate captured valid");
  endtask
  task automatic preserved_reset(input int expected_mode,input int expected_wm);
    int old_reads,old_rp,old_count,old_resets;
    old_reads=int'(dut.u_capture.read_count);old_rp=int'(dut.u_capture.u_fifo.rd_ptr);
    old_count=int'(dut.u_capture.u_fifo.count);old_resets=resets;
    require(dut.captured&&!dut.read_done&&old_reads>0,"reset fixture must be nonempty and partially read");
    write1(15'h000a,8'h5a);write1(15'h0014,8'h09);
    write1(15'h0000,8'h81);write1(15'h0000,8'h00);
    require(resets==old_resets+1,"two-write idle reset did not execute exactly once");
    require(dut.captured&&!dut.read_done,"reset lost completed-record identity");
    require(int'(dut.u_capture.read_count)==old_reads&&int'(dut.u_capture.u_fifo.rd_ptr)==old_rp&&int'(dut.u_capture.u_fifo.count)==old_count,"reset changed capture read pointer/count");
    require(int'(dut.u_capture.mode_r)==expected_mode&&int'(dut.u_capture.watermark_r)==expected_wm,"reset changed arm-latched mode/watermark");
    require(dut.fifo_mode==0&&dut.watermark==16384&&dut.addr_asc,"config reset defaults or retained ADDR_ASC");
    require(status[3]&&status[0],"software reset did not restore POR bits");read_reg(15'h000a,0);
  endtask
  // =========================================================================
  // 2. 停收、捕获记录、复位及路由
  // =========================================================================
  initial begin
    int reject_before,reset_before;
    repeat(4)@(negedge clk);rst_n=1;quiesce();
    require(gpio_oe[0]==0&&!sdo_oe,"default GPIO0 must release SPI SDO when CS high");read_reg(15'h0019,8'ha1);
    write1(15'h0000,8'h20);ascending=1;require(dut.addr_asc,"explicit ascending binding");
    // 即使未捕获且 valid_in 为低，只要 adc_idle 为假，仍不能接受软件复位。
    write1(15'h000a,8'ha5);adc_idle=0;reject_before=rejections;reset_before=resets;
    write1(15'h0000,8'h81);
    require(rejections==reject_before+1&&resets==reset_before&&dut.addr_asc&&!dut.captured&&int'(dut.u_capture.u_fifo.count)==0,"adc_idle0 reset changed state or was accepted");
    read_reg(15'h000a,8'ha5);quiesce();
    write2(15'h001d,16'd8);write1(15'h001c,8'h01);push(100,8);
    require(dut.captured&&status[7],"mode01 capture completion");
    read_sample(100);read_sample(101);preserved_reset(1,8);
    for(int i=102;i<108;i++)read_sample(i);
    require(dut.read_done&&status[6],"continued read did not finish retained record");
    // 显式 mode00 才执行清除；前面的软件复位须保留完成记录。
    write1(15'h001c,0);require(!dut.captured&&!dut.read_done,"explicit FIFO clear");
    write2(15'h001d,16'd4);write1(15'h001c,1);push(200,1);
    require(dut.capture_incomplete,"incomplete fixture");write1(15'h000a,8'h6c);
    reject_before=rejections;reset_before=resets;write1(15'h0000,8'h81);
    require(rejections==reject_before+1&&resets==reset_before&&dut.capture_incomplete&&int'(dut.u_capture.u_fifo.count)==1,"incomplete reset changed session");
    read_reg(15'h000a,8'h6c);push(201,3);for(int i=200;i<204;i++)read_sample(i);
    write1(15'h001c,0);write2(15'h001d,16'd4);write1(15'h001c,1);push(300,4);
    // 复位请求沿的读取仍属于原会话；拒绝复位不能丢弃这次合法读取。
    write1(15'h0000,8'h81);reject_before=rejections;reset_before=resets;
    fork
      write1(15'h0000,8'h00);
      begin
        wait(dut.we&&dut.addr==0);@(negedge clk);rd_en=1;
        @(posedge clk);#1;require(dout_valid&&dout==300,"concurrent rejected reset dropped legitimate read");read_compares++;
        @(negedge clk);rd_en=0;
      end
    join
    require(rejections==reject_before+1&&resets==reset_before&&int'(dut.u_capture.read_count)==1,"concurrent reset accepted or cleared record");
    for(int i=301;i<304;i++)read_sample(i);
    write1(15'h001c,0);write2(15'h001d,16'd4);write1(15'h001c,3);
    push(400,DEPTH+2);
    require(dut.u_capture.full&&!status[7]&&!dut.full_flag&&dut.capture_incomplete,"ring RAM capacity falsely reported completed record");
    @(negedge clk);ext_event=1;@(negedge clk);ext_event=0;
    push(700,4);require(dut.captured&&status[7],"mode11 completed record FULL");
    // 最后 DEPTH 个样本依次为预填索引 6..33 和四个事件后样本。
    read_sample(406);require(!status[7]&&dut.u_capture.full,"first snapshot read must clear protocol FULL despite full primitive RAM");
    read_sample(407);preserved_reset(3,4);
    for(int i=408;i<400+DEPTH+2;i++)read_sample(i);
    for(int i=700;i<704;i++)read_sample(i);
    require(dut.read_done&&status[6],"mode11 full-depth record did not finish");
    // 路由须同时控制 ALERT 电平、粘滞 STATUS 和事件触发捕获。
    write1(15'h001c,0);write2(15'h001d,16'd4);write2(15'h0021,16'd10);write2(15'h0023,16'h0ff6);
    write1(15'h001a,8'h06); // GPIO0 观测低有效 ALERT。
    write1(15'h001c,8'h82);push(6000,8);
    require(!dut.captured&&int'(dut.u_capture.u_fifo.count)==0&&!status[5]&&gpio_oe[0]&&gpio_o[0],"unrouted HI triggered STATUS/ALERT/FIFO");route_checks++;
    write1(15'h001c,0);write1(15'h0014,8'h30);write1(15'h001c,8'hc2);push(6000,8);
    require(dut.captured&&status[5]&&!gpio_o[0],"routed HI did not trigger STATUS/ALERT/FIFO");route_checks++;
    for(int i=0;i<4;i++)read_sample(6002+i);
    write1(15'h001c,0);write1(15'h0014,8'h30);write1(15'h001c,8'ha2);push(-6000,8);
    require(dut.captured&&status[4]&&!status[5]&&!gpio_o[0],"routed LO or hidden HI status gate");route_checks++;
    for(int i=0;i<4;i++)read_sample(-5998+i);
    require(route_checks==3,"event route coverage");
    require(resets==2&&rejections>=3&&read_compares>=50&&idle_assertions>=6,"nonzero retained-record checks");
    $display("AD4080_TOP_CONTRACT_COMPLETE retained_resets=%0d rejected_resets=%0d read_compares=%0d quiescent_assertions=%0d route_checks=%0d checks=%0d",resets,rejections,read_compares,idle_assertions,route_checks,checks);$finish;
  end
  initial begin #1000000;$fatal(1,"DSP top watchdog");end
endmodule
