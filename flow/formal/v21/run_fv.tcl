# =============================================================================
# 文件名     : run_fv.tcl
# 版本       : v3.0
# 功能       : 用 VC Formal 的 FPV 模式证明 recon_rnd_equiv 的等价性断言。
# 设计说明   :
#   * VC Formal 不是 DC/PT 那套命令：
#       · 默认 `get_fml_appmode` 已是 **FPV**，不需要 set_fml_appmode
#         （实测 `-property` 非法，会抛 "called a copy of a compiled script"）
#       · 没有 `prove` 命令；执行入口是 **run_active_scenarios**
#       · 结果用 report_property / report_fml_jobs 读
#   * 每次运行前必须清掉 ~/vcst_rtdb 下的 session.lock，
#     否则报 "Another session is already running"（实测锁里存的 PID 已不存在）。
# 变更记录   :
#   2026-11-16  v1.0 set_fml_appmode -property 非法。
#   2026-11-16  v2.0 去掉 appmode；prove 不存在。
#   2026-11-16  v3.0 改 run_active_scenarios + report_property。
# =============================================================================

set DESIGN recon_rnd_equiv
set WORK   /tmp/fvwork

# =============================================================================
# 1. 选择属性文件
# =============================================================================
# 先在本脚本旁查找属性文件。须在切换工作目录前解析绝对路径。
# 旧远端部署可能只上传 Tcl，沿用 /tmp/formal 下的已暂存属性文件。
set FORMAL_SOURCE [file join [file dirname [file normalize [info script]]] recon_rnd_equiv.sv]
if {![file isfile $FORMAL_SOURCE]} {
    set FORMAL_SOURCE /tmp/formal/recon_rnd_equiv.sv
}
if {![file readable $FORMAL_SOURCE]} {
    error "无法读入 VC Formal 属性文件：$FORMAL_SOURCE"
}

file mkdir $WORK
cd $WORK

analyze -format sverilog $FORMAL_SOURCE
elaborate $DESIGN
current_design $DESIGN

create_clock -name clk -period 25 [get_ports clk]

# ---- 执行证明 ----
# run_active_scenarios 依赖 set_host_options：VC Formal 把证明作业当"任务"
# 派给 host 跑，没设 host 会报 HOST_SETTING_NOT_EXIST（实测）。
# VC Formal 把证明当分布式作业：set_host_options 必须给
# -submit_command（提交命令）与 -protocol（通信协议），否则报
# "Required argument '-submit_command' was not found"（实测）。
# 合法协议只有 cmc/lsf/netbatch/rsh/rtda/ssh/uge（没有 local）。
# 本机没有 LSF/SGE，用 ssh 连 localhost 最省事。
# ssh/rsh 协议下 -num_processes 与 -host_names 是**必需**选项
# （报 "options (-num_processes and -host_names) are mandatory"）；
# -max_cores 不受支持会被忽略。
set_host_options -protocol ssh -num_processes 4 -host_names localhost \
                 -submit_command "ssh localhost"

# run_active_scenarios 要求**已有 active scenario**，否则报
# "no active scenario is present"（实测）。所以先建一个场景并激活。
create_scenario -name fv_all
set_active_scenarios fv_all
run_active_scenarios

# ---- 结果 ----
report_property -all
report_fml_jobs
exit
