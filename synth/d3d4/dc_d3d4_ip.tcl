# d3d4_ip 综合脚本：可在任意有 DC + TSMC28 库的机器上直接跑
#
# 用法：
#   export D3D4_PDK=/home/yian/PDK          # 或库所在前缀
#   export D3D4_WORK=$PWD                   # 仓库根
#   export D3D4_OUT=$PWD/synth/d3d4/out
#   dc_shell -f synth/d3d4/dc_d3d4_ip.tcl
#
# 与 synth/run_dc.tcl 的关系：那个脚本是"严格门禁 + 单时钟域断言"的通用包装，
# 要求所有输入以 dict 显式给出。本脚本是它的具体化版本，把路径拼装做掉，
# 但保留同样的硬断言：库/SDC 必须存在、必须恰好一个时钟、必须有时序路径。

# 断言写成"惰性求值"的形式：cond 是字符串表达式，在调用者的作用域里 eval。
# 直接写 proc need {cond msg} { if {![expr $cond]} ... } 会把 $lib/$sdc 这类
# 调用者局部变量当成 need 自己的作用域，导致 can't read "lib": no such variable。
proc die {msg} { puts stderr "DC_FATAL: $msg"; exit 1 }
proc need {cond msg} {
    if {![uplevel 1 [list expr $cond]]} { die $msg }
}

set pdk  $::env(D3D4_PDK)
set work $::env(D3D4_WORK)
set out  $::env(D3D4_OUT)

set lib   "$pdk/tsmc28/digital_technology/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db"
set sdc   "$work/synth/d3d4/sdc_d3d4_ip.sdc"
set rtl   [list "$work/rtl/dem_phase_rr.sv"  "$work/rtl/dem_phase_gen.sv" \
                "$work/rtl/sched18.sv"      "$work/rtl/follower_rt.sv" \
                "$work/rtl/dem_mask_v3.sv"  "$work/rtl/dither_pre.sv" \
                "$work/rtl/d3d4_ip.sv"]

need {[file isfile $lib]} "missing library: $lib"
need {[file isfile $sdc]} "missing SDC: $sdc"
foreach f $rtl { need {[file isfile $f]} "missing source: $f" }
need {![file exists $out]} "refusing to reuse existing output dir: $out"
file mkdir $out

set_app_var sh_continue_on_error false
set_app_var target_library [list $lib]
set_app_var link_library   [list * $lib]
set_app_var search_path    [list $work/rtl .]

# ---- 读入 ----
analyze -format sverilog $rtl
elaborate d3d4_ip
current_design d3d4_ip
need {[link]} "unresolved references"
need {[check_design]} "check_design rejected the design"

read_sdc $sdc
set clks [get_clocks -quiet *]
need {[sizeof_collection $clks] == 1} "exactly one clock domain required, got [sizeof_collection $clks]"

# 所有时序单元必须被这个时钟驱动（防止意外的无时钟寄存器）
set regs    [all_registers]
set clocked [all_registers -clock clk]
need {[sizeof_collection $regs] > 0} "no sequential cells found"
need {[sizeof_collection $regs] == [sizeof_collection $clocked]} "unclocked sequential cells present"

# ---- 综合 ----
# 用 -no_autoungroup 保留层次：compile_ultra 默认会把子模块 ungroup 掉，
# 那样 area.rpt 里只剩一个 d3d4_ip 行，无法看出面积花在哪个子块
# （dem_mask_v3 的 8x20 组合 vs dem_phase_gen 的取模树）。
# 保留层次对时序几乎无影响（模块间没插入额外逻辑），但对评审可读性影响很大。
need {[compile_ultra -no_autoungroup]} "compile_ultra failed"

# ---- 报告 ----
redirect "$out/area.rpt"        {report_area -hierarchy}
redirect "$out/qor.rpt"         {report_qor}
redirect "$out/check_design.rpt" {check_design}
# check_timing 只是诊断报告，不同 DC 版本对 -verbose 的支持不一致
# （W-2024.09-SP3 上带 -verbose 会在 redirect 里报错）。
# 它不参与任何门禁判断，因此失败不得阻断综合流程——用 catch 兜住并记下原因。
if {[catch {redirect "$out/check_timing.rpt" {check_timing -verbose}} _ct_err]} {
    set fhct [open "$out/check_timing_NOTE.txt" w]
    puts $fhct "check_timing -verbose 失败，已跳过（不参与门禁）：$_ct_err"
    close $fhct
    catch {redirect "$out/check_timing.rpt" {check_timing}}
}
redirect "$out/timing_max.rpt"  {report_timing -delay max -max_paths 20 -nworst 1}
redirect "$out/timing_min.rpt"  {report_timing -delay min -max_paths 20 -nworst 1}
report_power > "$out/power.rpt"

# ---- 门级网表（给门级等价仿真用）----
change_names -rules verilog -hierarchy
write -format verilog -hierarchy -output "$out/d3d4_ip_net.v"
write_sdc "$out/d3d4_ip.sdc"

# ---- 结论 ----
# 踩过的坑：get_timing_paths -max_paths 1 取到的**不一定是报告首条（最差）路径**。
# 实测 5 ns 时 timing_max.rpt 首条 slack=0.26，而 status.txt 写出 0.0016，
# 原因是那次还带着另一条更差的路径被单独取了出来。
# 正确做法：取 20 条再对所有 slack 求最小值，保证 status 与报告口径一致。
proc worst_slack {delay} {
    set paths [get_timing_paths -delay $delay -max_paths 200 -nworst 1]
    if {[sizeof_collection $paths] == 0} { return "" }
    set worst 1.0e30
    foreach_in_collection p $paths {
        set v [get_attribute $p slack]
        if {$v < $worst} { set worst $v }
    }
    return $worst
}
set smax [worst_slack max]
set smin [worst_slack min]
need {$smax ne ""} "no max timing path reported"
need {$smin ne ""} "no min timing path reported"

set fh [open "$out/status.txt" w]
puts $fh "STATUS=COMPILE_COMPLETE"
puts $fh "TOP=d3d4_ip"
puts $fh "LIBRARY=$lib"
puts $fh "SDC=$sdc"
# 周期从 SDC 里读回来，不要写死：之前写死 25.0，在 5 ns 那一组里
# status.txt 仍然写 25.0，会让下游把所有数字都解释错。
set _per [get_attribute [get_clocks clk] period]
puts $fh "CLOCK_PERIOD_NS=$_per"
puts $fh "MAX_SLACK_NS=$smax"
puts $fh "MIN_SLACK_NS=$smin"
# 先算成变量再写：在 double-quoted puts 里嵌套 expr 的三元表达式会对
# 引号做二次转义，DC 的 Tcl 会报 invalid character "\"。
if {$smax >= 0 && $smin >= 0} {
    set closure MET
} else {
    set closure VIOLATED
}
puts $fh "TIMING_CLOSURE=$closure"
puts $fh "SCOPE=BLOCK_LEVEL_SYNTH_WITH_DECLARED_CONSTRAINTS_NOT_SIGNOFF"
close $fh
puts "D3D4_SYNTH_DONE max=$smax min=$smin"
exit 0
