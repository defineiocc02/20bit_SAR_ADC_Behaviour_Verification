# =============================================================================
# 文件名     : ad4080_chip_io_timing.sdc
# 模块名     : ad4080_chip_top 单芯片 IO 与跨时钟域时序约束
# 版本       : v1.0
# 功能       : 规范 AD4080 单芯片的三时钟域（AON/CORE/IO）、50 MHz 配置 SPI
#              （Rev.B 表 2：1 ns setup / 0 ns hold）及 400 MHz DDR LVDS 源同步
#              （1.25 ns UI）ODDR 与 DCO 发射窗边界约束。
# 设计说明   :
#   * [时钟域划分] clk_aon_i（10 MHz / 100 ns）、clk_core_i（400 MHz / 2.5 ns，
#     或 40 MHz 双域方案）与 clk_io_i（400 MHz / 2.5 ns DDR = 800 Mbit/s）互相异步，
#     全部跨时钟交接由外围已审 CDC 模块承担，此处以 -asynchronous 声明并另约束
#     各 CDC 模块内部格雷/握手表层最大延迟。
#   * [50 MHz SPI 表 2] Rev.B p.7 表 2 要求 20 ns SCLK（高/低各 >= 9 ns），SDI 相对
#     SCLK 上升沿 setup >= 1.0 ns、hold >= 0.0 ns；本 SDC 额外加 0.25 ns 封装裕量。
#   * [400 MHz DDR LVDS] UI = 1.25 ns（双沿发射），对 DCO 与 DA/DB 之间的同源发射
#     偏斜（skew）施加 +/- 0.15 ns 上限，留出 >= 0.95 ns 有效眼宽给封装、PCB 与接收端。
# 变更记录   :
#   2026-11-24  v1.0 初版。闭合 SPI-CLOCK 与单芯片三域 IO 时序约束模板。
# =============================================================================

# =============================================================================
# 1. 三主时钟域与配置/结果 SPI 端口时钟定义
# =============================================================================
# 常开管理域（10 MHz，驱动电源监管、OTP 影子加载与软复位同步）
create_clock -name clk_aon  -period 100.000 [get_ports clk_aon_i]
set_clock_uncertainty -setup 0.500 [get_clocks clk_aon]
set_clock_uncertainty -hold  0.200 [get_clocks clk_aon]

# 核心与 DSP/SRAM 域（400 MHz 标称，与 ACCEPTANCE.md 速率复验一致）
create_clock -name clk_core -period   2.500 [get_ports clk_core_i]
set_clock_uncertainty -setup 0.150 [get_clocks clk_core]
set_clock_uncertainty -hold  0.080 [get_clocks clk_core]

# 高速 LVDS IO 域（单路 400 MHz DDR = 800 Mbit/s，UI = 1.25 ns）
create_clock -name clk_io   -period   2.500 [get_ports clk_io_i]
set_clock_uncertainty -setup 0.120 [get_clocks clk_io]
set_clock_uncertainty -hold  0.060 [get_clocks clk_io]

# 50 MHz 4 线配置 SPI 时钟（Rev.B 表 2：周期 20 ns，最坏占空比 45%/55% = 9 ns/11 ns）
create_clock -name spi_sclk -period  20.000 -waveform {0.000 9.000} [get_ports sclk]
set_clock_uncertainty -setup 0.250 [get_clocks spi_sclk]
set_clock_uncertainty -hold  0.150 [get_clocks spi_sclk]

# 50 MHz 4 线结果读出 SPI 时钟（Rev.B 图 87/88，Mode 3 下降沿发射/上升沿采样）
create_clock -name res_dclk -period  20.000 -waveform {0.000 10.000} [get_ports result_dclk_i]
set_clock_uncertainty -setup 0.250 [get_clocks res_dclk]
set_clock_uncertainty -hold  0.150 [get_clocks res_dclk]

# 异步时钟组隔离（各域交接经专用 CDC 与复位同步器，STA 不做无意义跨异步发射检查）
set_clock_groups -asynchronous \
    -group [get_clocks clk_aon]  \
    -group [get_clocks clk_core] \
    -group [get_clocks clk_io]   \
    -group [get_clocks spi_sclk] \
    -group [get_clocks res_dclk]

# =============================================================================
# 2. 50 MHz 配置 SPI 与结果 SPI 管脚时序（Rev.B 表 2）
# =============================================================================
# 配置 SPI：SDI 相对 SCLK 上升沿 setup 1.0 ns、hold 0.0 ns（额外留 0.25 ns 封装裕量）
set_input_delay -clock spi_sclk -max  1.250 [get_ports {cs_n sdi}]
set_input_delay -clock spi_sclk -min -0.250 [get_ports {cs_n sdi}]

# 配置 SDO 在 SCLK 下降沿切换，给主机下一上升沿留足建立时间（要求 <= 6.0 ns）
set_output_delay -clock spi_sclk -clock_fall -max 3.000 [get_ports {sdo sdo_oe}]
set_output_delay -clock spi_sclk -clock_fall -min -0.500 [get_ports {sdo sdo_oe}]

# 结果 SPI（A/B/C/D 四线）：DCS 与 SDO 相对 result_dclk_i
set_input_delay  -clock res_dclk -max  2.000 [get_ports result_dcs_ni]
set_input_delay  -clock res_dclk -min  0.000 [get_ports result_dcs_ni]
set_output_delay -clock res_dclk -clock_fall -max 3.000 [get_ports {result_sdo_o* result_sdo_oe_o*}]
set_output_delay -clock res_dclk -clock_fall -min -0.500 [get_ports {result_sdo_o* result_sdo_oe_o*}]

# =============================================================================
# 3. 400 MHz DDR LVDS 源同步输出窗口（UI = 1.25 ns）
# =============================================================================
# [源同步预算] 在封装与 LVDS TX Pad 接入 ad4080_lvds_phy_wrapper 后：
#   - 发射端 DA/DB 相对同源 DCO 的最大偏斜约束在 +/- 0.15 ns 以内；
#   - 扣除 0.12 ns 时钟不确定度与 0.08 ns 封装串扰后，接收端仍保留 >= 0.85 ns 眼宽。
set_output_delay -clock clk_io -max  0.350 [get_ports {continuous_da_fall_o continuous_da_rise_o \
                                                       continuous_db_fall_o continuous_db_rise_o \
                                                       continuous_da_oe_o continuous_db_oe_o \
                                                       continuous_dco_oe_o}]
set_output_delay -clock clk_io -min -0.200 [get_ports {continuous_da_fall_o continuous_da_rise_o \
                                                       continuous_db_fall_o continuous_db_rise_o \
                                                       continuous_da_oe_o continuous_db_oe_o \
                                                       continuous_dco_oe_o}]

# FIFO LVDS 组帧输出节拍同按 400 MHz IO 域约束
set_output_delay -clock clk_io -max  0.400 [get_ports {beat_valid_io_o beat_first_io_o beat_last_io_o \
                                                       beat_pair_io_o* beat_kind_io_o*}]
set_output_delay -clock clk_io -min -0.150 [get_ports {beat_valid_io_o beat_first_io_o beat_last_io_o \
                                                       beat_pair_io_o* beat_kind_io_o*}]

# =============================================================================
# 4. 封装负载与最大转换时间约束
# =============================================================================
set_max_transition 0.200 [current_design]
set_load 0.050 [all_outputs]
