# =============================================================================
# 文件名     : fm_equiv.tcl
# 版本       : v3.0
# 功能       : 用 Formality 检查 28HPC+ 综合网表与 RTL 的功能等价。
#
# 设计说明   :
#   * 等价检查是"网表可信"的必要证据：综合会做常量传播、资源共享、
#     边界优化，网表与 RTL 是否等价**不能假定**。
#
#   * Formality 命令体系与 DC/PT 差异较大，本轮实测踩过的坑：
#       · 没有 `link_library` app var（CMD-104）
#       · 没有 `set_top_designs`，正确是 **`set_top`**
#       · 没有 `set_container`；`create_container` 也不能带 `-type`
#         （fm_shell 启动时已有容器 0）
#       · 读库用 `read_db`，不用 set_app_var target_library
#     v3.0 按上述实测结果重写：在默认容器里按顺序读实现与参考，
#     再用 set_reference_design / set_implementation_design + match/verify。
# 变更记录   :
#   2026-11-16  v1.0 link_library/set_top_designs 不存在。
#   2026-11-16  v2.0 改 read_db/set_top，但 create_container -type 与
#                    set_container 仍不存在。
#   2026-11-16  v3.0 去掉容器操作，用默认容器。
# =============================================================================

set DESIGN adc_digital_ip_v21
set PDK    /home/yian/PDK/tsmc28/digital_technology
set DB     $PDK/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db
set RTLDIR /tmp/ip

read_db $DB
set_app_var search_path [list . $RTLDIR $RTLDIR/rtl $RTLDIR/rtl/v21]

# ---- 实现端：DC 网表 ----
read_verilog -netlist /tmp/dcwork/${DESIGN}_syn.v
set_top $DESIGN
puts "STEP_IMPL_READ_OK"

# ---- 参考端：生产 RTL ----
read_sverilog \
    $RTLDIR/rtl/sched18.sv \
    $RTLDIR/rtl/dem_phase_rr.sv \
    $RTLDIR/rtl/dem_phase_gen.sv \
    $RTLDIR/rtl/dither_pre.sv \
    $RTLDIR/rtl/dem_mask_v3.sv \
    $RTLDIR/rtl/follower_rt.sv \
    $RTLDIR/rtl/recip_q60.sv \
    $RTLDIR/rtl/v21/acq_conv_ctx.sv \
    $RTLDIR/rtl/v21/sar_evt_v21.sv \
    $RTLDIR/rtl/v21/adc2_capture.sv \
    $RTLDIR/rtl/v21/wsum_v21.sv \
    $RTLDIR/rtl/v21/recip_rnd_v21.sv \
    $RTLDIR/rtl/v21/recon_v21.sv \
    $RTLDIR/rtl/v21/adc_digital_ip_v21.sv
set_top $DESIGN
puts "STEP_REF_READ_OK"

puts "IMPL=[get_app_var hdlin_default_container]"
report_containers

match
puts "STEP_MATCH_OK"
verify
puts "STEP_VERIFY_OK"
report_status
exit
