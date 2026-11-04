//===========================================================================
// unit_therm.sv -- 温度计展开与采样 dither 的轨选择
//===========================================================================
// 职责一句话
//   把"每 slice 的单位计数 + 物理地址的逻辑位置"变成实际的开关掩码；
//   并给出采样态 dither 那 2D 个单位的底板轨极性。
//   本模块**不做**计数计算（swap_decode 的事）也不做地址置换（dem_addr_gen 的事）。
//
// 来源
//   adi_model/weight_calibration._terms()：
//       plus[..., order] = take,  take[i] = 1 iff i < count
//   即"掩码的物理地址 p 打开  <=>  逻辑位置 j(p) < count"。
//   采样 dither 的轨：`v_fs * (2*(i < D + d) - 1)`，故 rail[i] = (i < D + d)。
//
// 单位契约
//   无量纲整数。main_logical / sub_logical 来自 dem_addr_gen；
//   main_count ∈ [0, N_UNIT_MAIN]；bank_dither ∈ [-D, +D]。
//
// 参数来源分级
//   尺寸取头文件常量（[推导]）。本模块**没有**模块参数：同名参数会与
//   rtl_params.vh 自引用（见 swap_decode.sv 的同样说明）。
//
// 契约与不变量 / 适用域
//   * 结构性不变量（TB 断言）：`popcount(main_on[a]) == main_count[a]`，
//     `popcount(sub_on[a]) == sub_count[a]`。温度计展开若退化成"任意掩码"，
//     DAC 的名义守恒（契约 §6 A1）立刻失效。
//   * `main_count` 超出 [0, N_UNIT_MAIN] 时掩码饱和到全 1（比较即饱和），
//     这属于适用域之外；swap_decode 的桥接守卫保证不会发生。
//   * 纯组合，延迟 0。
//     没有 clk/reset/enable；调用方负责在 load 边沿冻结输出。地址表、计数
//     和 bank_dither 必须来自同一份样本上下文，不能混用下一样本的 DEM 状态。
//   * a 是活动通道号；p/k 是物理主/子单元 ID。main_on/sub_on 的位序直接
//     对齐权重存储的物理 ID，数组值 main_logical/sub_logical 才是逻辑顺序。
//   * dither_rail 是正/负底板轨的数字选择，每位覆盖一个采样 dither 单元；
//     它不是量化 dither 的幅值，也不代表模拟驱动已经无毛刺或满足非交叠。
//===========================================================================
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
    // i-D < d 等价于 i < D+d；先把 d 明确转成 signed，保留负 dither 的轨选择。
    for (i = 0; i < 2 * DITHER_UNITS_RANGE; i++) begin
      dither_rail[i] = (i - int'(DITHER_UNITS_RANGE)) < int'($signed(bank_dither));
    end
  end

endmodule
