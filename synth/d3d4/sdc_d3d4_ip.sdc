# d3d4_ip 的单时钟域约束
#
# 本 IP 在一个时钟周期内完成：
#   xorshift32 -> %1920 -> 三轴分解 -> 8×20 DEM 组合 -> 8×4 dither 组合 -> 寄存
# 纯组合长链，无流水。
#
# 周期取值（2026-11-15 实测后修订）
# --------------------------------
# 本 IP 的规格约束来自 ADC 采样率 40 MS/s -> 25 ns。
#
# 但必须同时记下一个事实：这个实现**没有流水线**，一整个
#   xorshift32 -> %1920 -> 三轴分解 -> 8x20 DEM -> 8x4 dither
# 都在一拍内完成。实测（DC W-2024.09-SP3 + tcbn28hpcplus ss）：
#   * 在极宽松约束（100 ns）下，综合器的自然实现：
#       146 级逻辑，组合延迟 7.91 ns，1312 cells，978.6 um2
#   * 在 25 ns 约束下：setup slack +16.77 ns（约束宽松，非性能指标）
#   * 在 5 ns 约束下：综合器被迫重优化，slack 仍收敛到约 0
#     （说明 5 ns 已在能力边界，且此时的面积/延迟不再是"自然实现"）
#
# 结论：**真实能力约 126 MHz（7.91 ns）**，相对 25 ns 规格有约 3 倍余量。
# 因此主约束按规格取 25 ns；7.91 ns 作为"自然实现延迟"单独记录在
# docs/SYNTH_D3D4.md，不写进 status.txt 的 slack 里（那是约束相对值）。
#
# 若将来需要更高频率：在 xorshift 与 %1920 之间插一级流水，
# 输出延迟会从 1 拍变 2 拍，接口合同需同步修改。
create_clock -name clk -period 25.0 [get_ports clk]

# 输入/输出延迟：本 IP 的上游/下游都在同一时钟域内直连，
# 这里按"外部组合逻辑占 2 ns"的常见假设给出，属于工程假设而非接口预算。
# 端口显式列举：本 IP 的端口集合是固定的，直接写名字最稳。
# 踩过的坑（都不该再用）：
#   * remove_from_collection —— read_sdc 阶段未加载，unknown command
#   * all_inputs -no_clocks  —— 当前版本不支持该选项
# 显式列出的代价是加端口时要同步改两处，因此下面用变量集中定义。
set _data_in [get_ports {valid_i k_i[*] dither_i[*] sample_adv_i}]
set_input_delay  -max 2.0 -clock clk $_data_in
set_input_delay  -min 0.5 -clock clk -add_delay $_data_in
set_output_delay -max 2.0 -clock clk [all_outputs]
set_output_delay -min 0.5 -clock clk -add_delay [all_outputs]

# 驱动与负载：与仓库既有 28nm 脚本同口径，便于横向比较
set_driving_cell -lib_cell BUFFD1BWP7T40P140 $_data_in
set_load 0.05 [all_outputs]

# 时钟不确定性：两个值都是**保守工程假设**，不是从时钟树推出来的。
#
# setup 0.3 ns：留给 PLL 抖动 + 时钟树偏斜 + OCV。对一个还没做 CTS 的
#   块级综合，这个量级是常见做法。
# hold 0.1 ns：注意这一项直接决定了自环 hold 的结论。实测（见
#   docs/SYNTH_D3D4.md §4.2）：同一份网表，hold uncertainty 0.1 时
#   最差 slack = +0.013 ns，改成 0 时 = +0.113 ns。也就是说这 0.1 ns
#   占掉了全部余量的绝大部分，13 ps 不是"电路快到期"。
#
# 保持 0.1 是因为：在没做 CTS 之前，用偏保守的 uncertainty 更安全；
# 若为了"好看"把它调小，得到的正 slack 没有物理依据。
# 需要看去掉 uncertainty 后的数字，用 synth/d3d4/dc_hold_unc0.tcl 对照。
set_clock_uncertainty -setup 0.3 [get_clocks clk]
set_clock_uncertainty -hold  0.1 [get_clocks clk]
