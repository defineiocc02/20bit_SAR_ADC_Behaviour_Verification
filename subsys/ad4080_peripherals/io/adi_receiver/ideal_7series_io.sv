`timescale 1ps/1ps
`default_nettype none

// =============================================================================
// 文件名     : ideal_7series_io.sv
// 模块名     : ZedBoard 接收试验使用的理想 I/O primitive
// 版本       : v1.0
// 功能       : 在 Verilator 中替代冻结 ADI 源码所调用的 7 Series I/O 宏。
// 设计说明   :
//   * 本文件只模拟差分逻辑、无延时连线、DCO /2 和每 lane 1:4 捕获。
//     样本解包、轨道交错、位滑移、固定码、符号扩展均由原版 ADI 执行。
//   * Q1 是最近的下降沿数据，Q4 是最早的上升沿数据。CLKDIV 的首组
//     起点随 DCO 启动位置变化，模型不会引用 TX 帧或模式语义来调整相位。
//   * IDELAY 的物理步进、锁相与采样窗均不在此模型中。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

// =============================================================================
// 1. 差分`P/N`和返回时钟分频
// =============================================================================
// 正端为 1 且负端为 0 才表示高状态。这里的差分只用于检查逻辑极性。
module IBUFGDS(input wire I, IB, output wire O);
    assign O = I && !IB;
endmodule

module IBUFDS(input wire I, IB, output wire O);
    assign O = I && !IB;
endmodule

module BUFIO(input wire I, output wire O);
    assign O = I;
endmodule

module BUFR #(parameter BUFR_DIVIDE = "2") (
    input wire CLR, CE, I,                   // sync_n 清零分频器，恢复时重新定相
    output logic O = 1'b0                   // 固定 /2，产生 4 边沿一组的接收时钟
);
    initial begin
        if (BUFR_DIVIDE != "2") $fatal(1, "Only the AD408x Series-7 DCO divide by 2 is modeled");
    end
    always @(posedge I or posedge CLR) begin
        if (CLR) O <= 1'b0;
        else if (CE) O <= !O;
    end
endmodule

// =============================================================================
// 2. 透明 IDELAY 路径
// =============================================================================
// 保留控制引脚以链接 ADI 实例。只接受该实例选择的 IDATAIN / VAR_LOAD，
// 通过 up_clk 捕获寄存器值用于可读 tap。数据时延在逻辑验证中置零。
module IDELAYCTRL #(parameter SIM_DEVICE = "7SERIES") (
    input wire RST, REFCLK,                 // 不模拟 200 MHz 参考锁相序列
    output wire RDY
);
    assign RDY = !RST;
    initial if (SIM_DEVICE != "7SERIES") $fatal(1, "Unexpected IDELAYCTRL family");
endmodule

module IDELAYE2 #(
    parameter CINVCTRL_SEL = "FALSE", DELAY_SRC = "IDATAIN", HIGH_PERFORMANCE_MODE = "FALSE",
    parameter IDELAY_TYPE = "VAR_LOAD", IDELAY_VALUE = 0, REFCLK_FREQUENCY = 200,
    parameter PIPE_SEL = "FALSE", SIGNAL_PATTERN = "DATA"
) (
    input wire CE, INC, DATAIN, LDPIPEEN, CINVCTRL, REGRST, C,
    input wire IDATAIN, LD,                  // 已冻结 ADI 源码的实际输入端及 tap 装载
    input wire [4:0] CNTVALUEIN,
    output wire DATAOUT,
    output logic [4:0] CNTVALUEOUT = '0
);
    assign DATAOUT = IDATAIN;
    always @(posedge C) begin
        if (REGRST) CNTVALUEOUT <= 5'(IDELAY_VALUE);
        else if (LD) CNTVALUEOUT <= CNTVALUEIN;
    end
    initial begin
        if (DELAY_SRC != "IDATAIN" || IDELAY_TYPE != "VAR_LOAD")
            $fatal(1, "Undefined IDELAY mode in RX model");
    end
endmodule

// =============================================================================
// 3. 7 Series 1:4 DDR 捕获
// =============================================================================
// `BITSLIP` 引脚在实际 ADI 代码中绑 0。禁止通过理想原语执行补偿，
// 确保这次看到的旋转来自 ADI `ad408x_phy.shift_cnt`。
module ISERDESE2 #(
    parameter DATA_RATE = "DDR", DATA_WIDTH = 4, DYN_CLKDIV_INV_EN = "FALSE", DYN_CLK_INV_EN = "FALSE",
    parameter INIT_Q1 = 1'b0, INIT_Q2 = 1'b0, INIT_Q3 = 1'b0, INIT_Q4 = 1'b0,
    parameter INTERFACE_TYPE = "NETWORKING", IOBDELAY = "IFD", NUM_CE = 2,
    parameter OFB_USED = "FALSE", SERDES_MODE = "MASTER",
    parameter SRVAL_Q1 = 1'b0, SRVAL_Q2 = 1'b0, SRVAL_Q3 = 1'b0, SRVAL_Q4 = 1'b0
) (
    output wire O, Q1, Q2, Q3, Q4, Q5, Q6, Q7, Q8, SHIFTOUT1, SHIFTOUT2,
    input wire BITSLIP, CE1, CE2, CLKDIVP, CLK, CLKB, CLKDIV, OCLK,
    input wire DYNCLKDIVSEL, DYNCLKSEL, D, DDLY, OFB, OCLKB, RST, SHIFTIN1, SHIFTIN2
);
    logic [3:0] history_q = '0;         // 时序匹配后，最低位代表目前收到的最后一位
    logic [3:0] group_at_rise_q = '0;  // 上升沿之前已有完整 R/F/R/F 四采样
    logic [3:0] divided_pipe_q = '0;   // 让组在 CLKDIV 后以稳定四并行位交付
    logic [3:0] output_q = '0;

    // 双沿捕获按物理 CLK 和互补 CLKB。分频输出由原版 ADI 顶层选用的 BUFR 产生。
    always @(posedge CLK or posedge CLKB or posedge RST) begin
        if (RST) history_q <= '0;
        else if (CE1 && CE2) history_q <= {history_q[2:0], DDLY};
    end

    // 先记录本上升沿之前的历史，避免把当沿的新位误当上一个下降沿。
    always @(posedge CLK or posedge RST) begin
        if (RST) group_at_rise_q <= '0;
        else group_at_rise_q <= history_q;
    end

    // 两次 CLKDIV 采纳提供确定性流水。压根没有转换字长、样本模式或帧期知识。
    always @(posedge CLKDIV or posedge RST) begin
        if (RST) begin
            divided_pipe_q <= '0;
            output_q <= {SRVAL_Q4, SRVAL_Q3, SRVAL_Q2, SRVAL_Q1};
        end else begin
            if (BITSLIP) $fatal(1, "Physical BITSLIP is not implemented by the ideal model");
            divided_pipe_q <= group_at_rise_q;
            output_q <= divided_pipe_q;
        end
    end

    initial begin
        if (DATA_RATE != "DDR" || DATA_WIDTH != 4 || IOBDELAY != "IFD" ||
            INTERFACE_TYPE != "NETWORKING" || SERDES_MODE != "MASTER")
            $fatal(1, "Unexpected ISERDESE2 profile in the receiver test");
    end
    assign {Q4, Q3, Q2, Q1} = output_q;
    assign {Q8, Q7, Q6, Q5, SHIFTOUT2, SHIFTOUT1} = '0;
    assign O = DDLY;
endmodule

`default_nettype wire
