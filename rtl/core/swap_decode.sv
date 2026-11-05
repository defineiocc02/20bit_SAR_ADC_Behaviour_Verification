// 模块：swap_decode；粗码与dither到每slice主/子单位计数的组合译码。
// 职责：有符号命令k=coarse*UNITS_PER_LSB1+K0+dither，随后越界检测、裁剪、bridge交换及divmod。
// 端口/单位：coarse为9位码；dither_code为16位signed RDAC单位当量；输出为非负单元计数。
// 时序：无时钟、复位或握手；所有输入须属于同一个冻结样本。
// 桥接：dem_en&&bridge_en时按sid_low进行4正4负零和交换，端点幅度0，内部码幅度1。
// 生产调用方已将bridge_en与sid[8]相与；本叶没有完整sid，不能在这里重复猜测激活条件。
// 名义main=8、sub=1时八slot总量为8*k；带失配的有效权重不相等时，实际电荷不必零和。
// 异常：rdac_over使用裁剪前k，合法mask使用裁剪后码；名义总和守恒不证明失配下的物理等价。
// 适用域：当前8活动slice、63main/8sub、512级；几何来自参数头，顶层另作一致性检查。
// 来源：mapper.encode、dem.split_switch_command、RTL算术合同；不生成地址或温度计mask。
// 这是ADR0021统一的工程开关合同，不是原芯片binary/unary桥接电路的完整真值表。
`include "rtl_params.vh"

module swap_decode (
    input  logic [8:0]               coarse,
    input  logic signed [15:0]       dither_code,
    input  logic                     dem_en,
    input  logic                     bridge_en,
    input  logic [2:0]               sid_low,
    output logic [N_ACTIVE - 1:0][6:0] main_count,
    output logic [N_ACTIVE - 1:0][2:0] sub_count,
    output logic                     rdac_over
);

  localparam int N_ACT = int'(N_ACTIVE);
  localparam int LV    = int'(DAC_LEVELS);
  localparam int HALF  = N_ACT / 2;

  // 命令及边界常量均声明为signed；负dither必须先符号扩展再参与命令运算。
  localparam signed [17:0] UL1  = 18'(UNITS_PER_LSB1);
  localparam signed [17:0] KLO  = 18'(K0);
  localparam signed [17:0] LMAX = 18'(int'(DAC_LEVELS) - 1);
  localparam signed [17:0] ZERO = 18'sd0;

  // coarse是量化器判决码，k_cmd才是RDAC子单位命令；dither_code已由上层按 sampling加、
  // quantizer减完成单位换算。RDAC合法不等于RA摆幅或ADC2窗口合法。
  logic signed [17:0] k_cmd;
  assign k_cmd = $signed({9'b0, coarse}) * UL1 + KLO + 18'($signed(dither_code));

  assign rdac_over = (k_cmd < ZERO) || (k_cmd > LMAX);

  // ---- 2) 裁剪到可实现的 DAC 电平 ----
  logic [8:0] code;
  assign code = (k_cmd < ZERO) ? 9'd0
    : (k_cmd > LMAX) ? 9'(LV - 1)
    : k_cmd[8:0];

  // ---- 3) 桥接零和交换的幅度：min(1, min(code, LV-1-code)) ----
  logic [8:0] head, tail, amount;
  assign head   = code;
  assign tail   = 9'(LV - 1) - code;
  assign amount = (head < tail) ? ((head < 9'd1) ? 9'd0 : 9'd1)
    : ((tail < 9'd1) ? 9'd0 : 9'd1);

  // ---- 4) 逐 slice：符号 + 拆分 ----
  genvar a;
  generate
    for (a = 0; a < N_ACT; a++) begin : g_slice
      logic [2:0]         rank;
      logic               sign_pos, sign_neg;
      logic signed [10:0] delta, slice_code;

      // a是8个活动slot的编号，稍后才由rdac_drv映射到物理slice ID； rank只旋转4正4负的slot分组。
      // 操作数先扩32位，避免常量8被3位截成0。
      assign rank     = 3'((32'(a) - 32'(sid_low)) % 32'(N_ACT));
      assign sign_pos = (32'(rank) < 32'(HALF));
      assign sign_neg = !sign_pos && (32'(rank) < 32'(N_ACT));

      // amount 扩为有符号 11 位后再取负；先截位再符号转换会改变边界值。
      assign delta = (dem_en && bridge_en)
        ? (sign_pos ? $signed({2'b0, amount})
          : (sign_neg ? -$signed({2'b0, amount}) : 11'sd0))
        : 11'sd0;

      assign slice_code = $signed({2'b0, code}) + delta;

      assign main_count[a] = slice_code[9:3];   // 非负前提下的 floor(x/8)
      assign sub_count[a]  = slice_code[2:0];   // 非负前提下的 x mod 8
    end
  endgenerate

endmodule
