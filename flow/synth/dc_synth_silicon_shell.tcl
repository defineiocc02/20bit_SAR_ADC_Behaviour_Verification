# =============================================================================
# 文件名     : dc_synth_silicon_shell.tcl
# 模块名     : AD4080 外围流片边界外壳综合脚本（TSMC 28HPC+）
# 版本       : v1.0
# 功能       : 在 TSMC 28HPC+（SS 0.81V -40C）下依次综合全部四项外围流片边界模块：
#              1) ad4080_crc24_service        @ 400 MHz (2.50 ns)
#              2) ad4080_otp_macro_adapter    @ 100 MHz (10.0 ns，对 10 MHz AON 留 10x 裕量)
#              3) ad4080_lvds_phy_wrapper     @ 400 MHz DDR (2.50 ns，含 90° 正交时钟)
#              4) ad4080_result_store_foundry @ 400 MHz (2.50 ns，4 银行译码控制壳+黑盒 SRAM 桩)
# 设计说明   :
#   * [工艺基线] 使用 TSMC 28HPC+ tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db；
#     每模块分别导出 _syn.v、_syn.sdc、.svf、qor.rpt、area.rpt、sta.rpt、viol.rpt，
#     供下游 Formality 形式等价、PrimeTime 四角 STA 与 VCS 门级网表仿真闭环消费。
#   * [SRAM 硬宏边界] ad4080_result_store_foundry 使用 -define USE_FOUNDRY_SRAM_MACRO
#     综合真实 4 银行译码与低功耗钳位外壳，避免把 327,680 bit 行为存储展开为触发器阵。
# 变更记录   :
#   2026-11-24  v1.0 初版。四项外围流片边界模块 TSMC 28HPC+ 综合入口。
# =============================================================================

set ROOT   [file normalize [file join [file dirname [info script]] .. ..]]
set PDK    [expr {[info exists ::env(V21_PDK_ROOT)] ? $::env(V21_PDK_ROOT) : [file normalize [file join ~ PDK tsmc28 digital_technology]]}]
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set OUTTOP [expr {[info exists ::env(SHELL_OUT_DIR)] ? $::env(SHELL_OUT_DIR) : [file join [pwd] dc_shell_out]}]
file mkdir $OUTTOP

set_host_options -max_cores 8
set_app_var target_library [list $DB]
set_app_var link_library   [list "*" $DB]

# =============================================================================
# 1. 通用单模块综合过程（含驱动核验、SVF、未时钟寄存器检查与全量报告）
# =============================================================================
proc synth_shell_block {design srcs period clk_ports extra_defines sdc_extra} {
    global OUTTOP DB
    set out [file join $OUTTOP $design]
    file mkdir $out

    remove_design -all
    set_svf $out/${design}.svf

    foreach f $srcs {
        if {[llength $extra_defines] > 0} {
            analyze -format sverilog -define $extra_defines $f
        } else {
            analyze -format sverilog $f
        }
    }
    elaborate $design
    current_design $design
    link

    # 主时钟与输入/输出边界延迟预算
    set primary_clk [lindex $clk_ports 0]
    create_clock -name clk_main -period $period [get_ports $primary_clk]
    set_clock_uncertainty -setup 0.120 [get_clocks clk_main]
    set_clock_uncertainty -hold  0.060 [get_clocks clk_main]
    set_clock_transition         0.060 [get_clocks clk_main]

    # 若存在额外时钟或特殊约束（如 90° 正交时钟），通过回调脚本追加
    if {$sdc_extra ne ""} {
        eval $sdc_extra
    }

    set _in_nonclk [remove_from_collection [all_inputs] [get_ports $clk_ports]]
    set_driving_cell -lib_cell BUFFD2BWP7T40P140 -pin Z $_in_nonclk
    set _io_dly [expr {$period * 0.15}]
    set_input_delay  $_io_dly -clock clk_main $_in_nonclk
    set_output_delay $_io_dly -clock clk_main [all_outputs]
    set_load 0.050 [all_outputs]

    compile -map_effort medium

    write -format verilog -hierarchy -output $out/${design}_syn.v
    write_sdc $out/${design}_syn.sdc
    report_qor                        > $out/qor.rpt
    report_area -hierarchy            > $out/area.rpt
    report_power -hierarchy           > $out/power.rpt
    report_timing -max_paths 10       > $out/sta.rpt
    report_constraint -all_violators  > $out/viol.rpt

    set _all_r [all_registers]
    set _clk_r [all_registers -clock clk_main]
    set _unclk [remove_from_collection $_all_r $_clk_r]
    puts "SHELL_SYNTH_OK design=$design period=$period regs=[sizeof_collection $_all_r] unclocked=[sizeof_collection $_unclk]"
    set_svf -off
}

# =============================================================================
# 2. 依次综合四项外围流片边界模块
# =============================================================================
# 2.1 可编程 2-bit/beat DDR CRC-24 服务 @ 400 MHz (2.50 ns)
synth_shell_block ad4080_crc24_service \
    [list $ROOT/rtl/top/ad4080_crc24_service.sv] \
    2.500 {clk_io_i} {} {}

# 2.2 工艺厂 OTP 宏适配 + 硬件 CRC-24 镜像校验器 @ 100 MHz (10.0 ns，10x AON 裕量)
synth_shell_block ad4080_otp_macro_adapter \
    [list $ROOT/rtl/top/ad4080_otp_macro_adapter.sv] \
    10.000 {clk_aon_i} {} {}

# 2.3 400 MHz DDR LVDS ODDR + 0°/90°/180° DCO 相位封装 @ 400 MHz (2.50 ns)
synth_shell_block ad4080_lvds_phy_wrapper \
    [list $ROOT/rtl/top/ad4080_lvds_phy_wrapper.sv] \
    2.500 {clk_io_i clk_io_90_i} {} {
        create_clock -name clk_io_90 -period 2.500 -waveform {0.625 1.875} [get_ports clk_io_90_i]
        set_clock_uncertainty -setup 0.120 [get_clocks clk_io_90]
        set_clock_uncertainty -hold  0.060 [get_clocks clk_io_90]
        set_clock_transition         0.060 [get_clocks clk_io_90]
    }

# 2.4 四银行 16K×20 工艺厂 SRAM 控制壳 @ 400 MHz（黑盒硬宏接缝，核验译码与睡眠钳位）
# 创建最小黑盒声明文件至输出目录，供 DC link 工艺厂 4096×20 1RW 硬宏引脚
set _bb_file [file join $OUTTOP foundry_sram_1rw_4096x20_bb.sv]
set _fh [open $_bb_file w]
puts $_fh {
module foundry_sram_1rw_4096x20 (
    input  logic        CLK,
    input  logic        CEB,
    input  logic        WEB,
    input  logic [11:0] A,
    input  logic [19:0] D,
    input  logic [19:0] BWEB,
    input  logic        SLP,
    input  logic        SD,
    input  logic [1:0]  RTSEL,
    input  logic [1:0]  WTSEL,
    output logic [19:0] Q
);
endmodule
}
close $_fh

synth_shell_block ad4080_result_store_foundry \
    [list $_bb_file $ROOT/rtl/top/ad4080_sram_bank_foundry_wrap.sv $ROOT/rtl/top/ad4080_result_store_foundry.sv] \
    2.500 {clk_i} {USE_FOUNDRY_SRAM_MACRO} {}

puts "SHELL_ALL_SYNTH_DONE"
exit 0
