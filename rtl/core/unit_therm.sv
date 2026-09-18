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
//===========================================================================
`include "rtl_params.vh"

module unit_therm (
    input  logic [N_UNIT_MAIN-1:0][5:0]        main_logical,
    input  logic [N_UNIT_SUB-1:0][2:0]         sub_logical,
    input  logic [N_ACTIVE-1:0][6:0]           main_count,
    input  logic [N_ACTIVE-1:0][2:0]           sub_count,
    input  logic signed [7:0]                  bank_dither,
    output logic [N_ACTIVE-1:0][N_UNIT_MAIN-1:0] main_on,
    output logic [N_ACTIVE-1:0][N_UNIT_SUB-1:0]  sub_on,
    output logic [2*DITHER_UNITS_RANGE-1:0]      dither_rail
);

  integer a, p, k, i;

  always_comb begin
    for (a = 0; a < N_ACTIVE; a++) begin
      for (p = 0; p < N_UNIT_MAIN; p++) begin
        main_on[a][p] = (9'(main_logical[p]) < 9'(main_count[a]));
      end
      for (k = 0; k < N_UNIT_SUB; k++) begin
        sub_on[a][k] = (4'(sub_logical[k]) < 4'(sub_count[a]));
      end
    end
    for (i = 0; i < 2 * DITHER_UNITS_RANGE; i++) begin
      dither_rail[i] = ($signed(i) - DITHER_UNITS_RANGE) < $signed(bank_dither);
    end
  end

endmodule
