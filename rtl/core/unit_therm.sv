// 模块：unit_therm；逻辑位置/计数到物理温度计mask与采样rail。
// 职责：main_on[a][p]=(main_logical[p]<main_count[a])，sub同理；rail[i]=(i-D<bank_dither)。
// 端口/单位：地址数组下标为物理unit ID，值为逻辑顺序；bank_dither为8位signed整数。
// 时序：纯组合，无时钟/复位；调用方在load时冻结来自同一样本的地址、计数和dither。
// 适用域：main_count=0..N_MAIN、sub_count<N_SUB、bank_dither=-D..D；超范围计数会自然饱和mask。
// 边界：输出只是数字选择，不代表模拟电压幅值、无毛刺驱动或非交叠已满足。
// a是活动slot、p/k是物理unit；slot对应哪颗slice由上层冻结的slice_id决定。
// 同一列rail供所有活动slice使用，补偿仍须使用各slice实际有效权重，不能只扣名义dither。
// 来源：数字物理mask合同、weight_calibration特征定义与参数头。
`include "rtl_params.vh"

module unit_therm (
    input  logic [N_UNIT_MAIN - 1:0][5:0]          main_logical,
    input  logic [N_UNIT_SUB - 1:0][2:0]           sub_logical,
    input  logic [N_ACTIVE - 1:0][6:0]             main_count,
    input  logic [N_ACTIVE - 1:0][2:0]             sub_count,
    input  logic signed [7:0]                    bank_dither,
    output logic [N_ACTIVE - 1:0][N_UNIT_MAIN - 1:0] main_on,
    output logic [N_ACTIVE - 1:0][N_UNIT_SUB - 1:0]  sub_on,
    output logic [2 * DITHER_UNITS_RANGE - 1:0]      dither_rail
);

  integer a, p, k, i;

  // 全部输出在每次组合执行中赋值；各 unit 比较器在综合时并行展开，
  // for 循环不产生逐 unit 扫描状态机，也没有未赋值路径推断锁存器。
  always_comb begin
    for (a = 0; a < N_ACTIVE; a++) begin
      for (p = 0; p < N_UNIT_MAIN; p++) begin
        main_on[a][p] = (9'(main_logical[p]) < 9'(main_count[a]));
      end
      for (k = 0; k < N_UNIT_SUB; k++) begin
        sub_on[a][k] = (4'(sub_logical[k]) < 4'(sub_count[a]));
      end
    end
    // 1/0表示参考正/负轨，不能把0当作零伏。D=2、d=0时rail=0011，
    // “dither关闭”须由上层sampling门控解释，不能用rail是否全0来判断。
    // i-D < d等价于i < D+d；明确signed后才能保留负扰动的轨选择。
    for (i = 0; i < 2 * DITHER_UNITS_RANGE; i++) begin
      dither_rail[i] = (i - int'(DITHER_UNITS_RANGE)) < int'($signed(bank_dither));
    end
  end

endmodule
