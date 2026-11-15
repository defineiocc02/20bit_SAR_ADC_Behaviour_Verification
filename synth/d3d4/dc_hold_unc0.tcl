# hold uncertainty 对照实验：把 hold uncertainty 设为 0，看自环余量还剩多少
#
# 目的：区分"hold 真的快违例"和"hold 预算把余量吃掉了"。
# 用法（与 dc_d3d4_ip.tcl 相同的环境变量）：
#   export D3D4_PDK=/home/yian/PDK D3D4_WORK=$PWD
#   dc_shell -f synth/d3d4/dc_hold_unc0.tcl
#
# 结论解读见 docs/SYNTH_D3D4.md §4.2：uncertainty 0.1 -> slack +0.013，
# uncertainty 0 -> slack +0.113。两者差 0.1，正好等于 uncertainty 本身，
# 说明余量是被预算吃掉的，不是路径本身的问题。
proc die {msg} { puts stderr "DC_FATAL: $msg"; exit 1 }

set pdk  $::env(D3D4_PDK)
set work $::env(D3D4_WORK)

set lib "$pdk/tsmc28/digital_technology/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db"
set sdc "$work/synth/d3d4/sdc_d3d4_ip.sdc"
set rtl [list "$work/rtl/dem_phase_gen.sv" "$work/rtl/dem_mask_v3.sv" \
              "$work/rtl/dither_pre.sv" "$work/rtl/d3d4_ip.sv"]
if {![file isfile $lib]} { die "missing library: $lib" }
if {![file isfile $sdc]} { die "missing SDC: $sdc" }

set_app_var sh_continue_on_error false
set_app_var target_library [list $lib]
set_app_var link_library   [list * $lib]
set_app_var search_path    [list $work/rtl .]

analyze -format sverilog $rtl
elaborate d3d4_ip
current_design d3d4_ip
link
source $sdc
set_clock_uncertainty -hold 0.0 [get_clocks clk]
compile_ultra -no_autoungroup

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
puts "HOLD_UNC0 worst_hold_slack=[worst_slack min]  worst_setup_slack=[worst_slack max]"
exit 0
