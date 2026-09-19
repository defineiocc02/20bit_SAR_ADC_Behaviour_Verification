#=============================================================================
# synth/run_dc.tcl -- Design Compiler 综合流程（VM 侧执行）
#=============================================================================
# 调用方式（不要直接调本文件，用 run_synth.sh）：
#   dc_shell -no_gui -f run_dc.tcl
#   参数全部通过环境变量传入，见下。
#
# 环境变量（[必需] 的没给就直接退出码 2）：
#   TOP          [必需] 顶层模块名
#   FILES        [必需] 空格分隔的源文件列表（含 .sv / .v）
#   OUT_DIR      [必需] report / log 输出目录（会被创建）
#   CLK_PERIOD   [必需] 时钟周期，单位 ns（正数）
#   CLK_NAME     时钟端口名，默认 clk
#   INCDIRS      空格分隔的 `include 搜索目录
#   DEFINES      空格分隔的 +define+ 宏
#   PARAMS       顶层模块的 HDL 参数覆盖，形如 "P_STAGES=4"（多个用空格/逗号分隔）。
#                走 `elaborate -parameters`；空 = 用 RTL 里的默认值。
#                用途：同一份 RTL 扫参数（面积/时序旋钮），不必改源文件。
#   COMPILE_MODE ultra（默认）= compile_ultra -no_autoungroup；compile = 非 ultra，
#                快得多、结果差一些。**数字必须连着这行一起引用**（写进 status.txt）
#   DESIGN_RULE_FIX  1（默认）= 让 compile 做设计规则修复；0 = 加 `-no_design_rule`
#                关掉它（只对 COMPILE_MODE=compile 有效）。用途：把"DRC 修复在无界
#                加面积"这一项**单变量**地排除掉/定死。
#   LOAD_PF      输出负载 (pF)；0 = 输出不加载（分区块当顶层跑时**应当**用 0，
#                见 §5.1.1 的伪负载对照）
#   DC_CHECK_ONLY=1  只做到"参数一致性检查 + check_design"就退出（不起 compile），
#                用于改完头文件/脚本后几十秒内红绿一次
#   CORNER       工艺角，默认 tt0p9v25c（见 config/env.sh 的 SIGNOFF_DB_CORNERS）
#   LIB_DB       直接指定 .db 路径，覆盖 CORNER（调试用）
#   MAX_TRANS    最大转换时间 (ns)，默认取库的 default_max_transition
#   DRIVE_CELL   输入驱动单元名，默认 BUFFD2BWP7T40P140
#   LOAD_PF      输出负载 (pF)，默认 0.02
#   IN_DELAY     输入延迟 (ns)，默认 0.0
#   OUT_DELAY    输出延迟 (ns)，默认 0.0
#
# 退出码（与 run_synth.sh 一致，此处是"底层"语义）：
#   0  综合完成且 WNS >= 0
#   1  工具/流程失败（库读不进、analyze/elaborate/link/compile 失败、无设计）
#   2  参数错误
#   3  综合完成但时序未收敛（WNS < 0）
#   5  RTL 参数与冻结契约不一致（dc_check_rtl_params 判定），**拒绝综合**
#
# 产出（OUT_DIR 下固定文件名）：
#   area.rpt  timing.rpt  power.rpt  qor.rpt  check_design.rpt
#   dc.log（本文件不写，由 wrapper 重定向）
#   phase.log（**无缓冲**的阶段日志：dc.log 是块缓冲的，看不到进度，
#              "跑不完"和"卡住了"要靠它区分）
#   elaborate.log / ddc/（中间证据，便于排错）
#   status.txt  机器可读状态：DC_STATUS / WNS / TNS / AREA / CELLS /
#               TOP / CLK_PERIOD / PARAMS / COMPILE_MODE / RTL_PARAMS_SHA /
#               LIB_DB / PHASE / TIMESTAMP
#
# ⚠️ 一个必须知道的 dc_shell 行为
#   在 `-f script.tcl` 模式下，脚本里发生 **Tcl 层错误**时 dc_shell 只打印
#   "script stopped at line N due to error"，然后**回到交互提示符并以退出码 0
#   结束进程**。即"脚本炸了但 exit code 是 0"。
#   所以本文件把整个流程包在 proc dc_main 里，末尾用 catch 兜；
#   真正的致命错误用 dc_die -> exit <code>（Tcl 的 exit 不能被 catch 拦下，
#   会直接终止进程），未预期的 Tcl 错误则由 catch 转成退出码 1。
#=============================================================================

set_app_var sh_continue_on_error false

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

# 每个阶段往 phase.log 追加一行。为什么需要它（是真需求，不是装饰）：
# 顶层 `sar20_digital_core` 的 compile_ultra 在一次实跑里跑了一小时以上，
# 而 dc.log 是**块缓冲**的 —— 重定向到文件时几十 KB 才 flush 一次，于是
# "外部完全看不出它是在推进还是卡死了"。phase.log 用无缓冲写 + flush，
# 让"进度可见"，这也是 "跑不完" 与 "卡住了" 能被区分开的前提。
proc dc_phase {name} {
    global OUT_DIR
    if {![info exists OUT_DIR] || $OUT_DIR eq ""} { return }
    if {[catch {file mkdir $OUT_DIR}]} { return }
    set t [clock format [clock seconds] -format %Y-%m-%dT%H:%M:%S]
    if {![catch { set fh [open [file join $OUT_DIR phase.log] a] }]} {
        puts $fh "$t  $name"
        flush $fh
        close $fh
    }
    puts "DC_PHASE: $name"
    flush stdout
}

# 最后一行 PHASE 的镜像（status.txt 用）
proc dc_last_phase {} {
    global OUT_DIR
    if {![info exists OUT_DIR] || $OUT_DIR eq ""} { return "NA" }
    set f [file join $OUT_DIR phase.log]
    if {![file exists $f]} { return "NA" }
    if {[catch { set fh [open $f r]; set txt [read $fh]; close $fh }]} { return "NA" }
    set lines [split [string trim $txt] "\n"]
    if {[llength $lines] == 0} { return "NA" }
    set last [lindex $lines end]
    if {[regexp {^\S+\s+(.*)$} $last -> ph]} { return $ph }
    return $last
}

# 写 status.txt。即使中途失败也要留下证据，供 wrapper 判退出码。
proc dc_write_status {status {wns "NA"} {tns "NA"} {area "NA"} {cells "NA"}} {
    global OUT_DIR
    if {![info exists OUT_DIR] || $OUT_DIR eq ""} { return }
    if {[catch {file mkdir $OUT_DIR}]} { return }
    set f [open [file join $OUT_DIR status.txt] w]
    puts $f "DC_STATUS=$status"
    puts $f "WNS=$wns"
    puts $f "TNS=$tns"
    puts $f "TNS_KIND=sampled_path_negative_slack_sum"
    set tnw "NA"
    if {[info exists ::TNS_NWORST] && $::TNS_NWORST ne ""} { set tnw $::TNS_NWORST }
    puts $f "TNS_NWORST=$tnw"
    puts $f "AREA=$area"
    puts $f "CELLS=$cells"
    set clk_env "NA"
    if {[info exists ::env(CLK_PERIOD)]} { set clk_env $::env(CLK_PERIOD) }
    puts $f "TOP=$::TOP"
    puts $f "CLK_PERIOD=$clk_env"
    # 参数覆盖必须落到证据里：扫参数时"到底哪一版参数生效"不能靠回忆。
    set prm_env "(default)"
    if {[info exists ::env(PARAMS)] && $::env(PARAMS) ne ""} { set prm_env $::env(PARAMS) }
    puts $f "PARAMS=$prm_env"
    set cm "ultra"
    if {[info exists ::env(COMPILE_MODE)] && $::env(COMPILE_MODE) ne ""} { set cm $::env(COMPILE_MODE) }
    puts $f "COMPILE_MODE=$cm"
    set drf_env "1"
    if {[info exists ::env(DESIGN_RULE_FIX)] && $::env(DESIGN_RULE_FIX) ne ""} { set drf_env $::env(DESIGN_RULE_FIX) }
    puts $f "DESIGN_RULE_FIX=$drf_env"
    set lp "0.02"
    if {[info exists ::env(LOAD_PF)] && $::env(LOAD_PF) ne ""} { set lp $::env(LOAD_PF) }
    puts $f "LOAD_PF=$lp"
    set cu "1.0"
    if {[info exists ::env(LIB_CAP_UNIT_PF)]} { set cu $::env(LIB_CAP_UNIT_PF) }
    puts $f "LIB_CAP_UNIT_PF=$cu"
    # RTL 参数头文件的 payload sha：把"这份报告是哪一版参数产出的"钉死。
    # 头文件里自带 `payload sha : <hex>` 一行，由 tools/export_rtl_params.py 生成。
    set sha "NA"
    if {[info exists ::RTL_PARAMS_SHA] && $::RTL_PARAMS_SHA ne ""} { set sha $::RTL_PARAMS_SHA }
    puts $f "RTL_PARAMS_SHA=$sha"
    # 早期失败（源文件列表错等）时 LIB_DB_USED 还没赋值 —— 原来这里会直接抛错，
    # 被 dc_die 的 catch 吞掉，于是**最需要证据的那几种失败反而没有 status.txt**。
    set lib "NA"
    if {[info exists ::LIB_DB_USED] && $::LIB_DB_USED ne ""} { set lib $::LIB_DB_USED }
    puts $f "LIB_DB=$lib"
    puts $f "PHASE=[dc_last_phase]"
    puts $f "TIMESTAMP=[clock format [clock seconds] -format %Y-%m-%dT%H:%M:%S]"
    close $f
}

proc dc_die {code msg} {
    puts "DC_FATAL(exit $code): $msg"
    catch { dc_write_status FAILED }
    # Tcl 的 exit 不会被外层 catch 接住，直接终止 dc_shell，退出码可靠。
    exit $code
}

# 取最差 slack；取不到返回 NA。
# 注意 get_timing_paths **没有 -quiet** 选项（有会报 CMD-010）。
proc dc_slack {delay_type nworst {aggregate min}} {
    if {[catch { set paths [get_timing_paths -delay $delay_type -nworst $nworst -max_paths $nworst] } rc]} {
        puts "DC_WARN: get_timing_paths (-delay $delay_type) failed: $rc"
        return "NA"
    }
    if {[sizeof_collection $paths] == 0} { return "NA" }
    set vals {}
    foreach_in_collection p $paths {
        if {![catch { set s [get_attribute $p slack] } rc]} {
            if {[string is double -strict $s]} { lappend vals $s }
        }
    }
    if {[llength $vals] == 0} { return "NA" }
    if {$aggregate eq "sum_negative"} {
        set total 0.0
        foreach v $vals { if {$v < 0} { set total [expr {$total + $v}] } }
        return $total
    }
    set m [lindex $vals 0]
    foreach v $vals { if {$v < $m} { set m $v } }
    return $m
}

# ---------------------------------------------------------------------------
# RTL 参数一致性检查（TL-3 补丁）
# ---------------------------------------------------------------------------
# 背景（实测，不是猜测）
#   rtl/top/sar20_digital_core.sv:110-128 原本用 17 条 `$fatal` 在 initial 块里
#   做参数一致性自检。综合时 DC 把 initial 块**整块丢掉**，只留一句 Warning：
#       Warning: .../sar20_digital_core.sv:111: The statements in initial blocks
#                are ignored. (VER-281)
#   于是这套防线**只对仿真有效、对综合完全无效**：参数漂了会安静地综合出
#   一块面积/时序都不同、但报告看起来完全正常的网表。
#   所以必须把检查搬进流程。这与 README 坑 1（`dc_shell -f` 下 Tcl 报错仍返回 0）
#   是同一族缺陷：**没有报错 != 检查执行过了**。
#
# 做法：三个独立来源两两比对。只做其中一边都不够。
#   A) `rtl_params.vh`（在 INCDIRS 里找得到的那一份）解析出来的值。
#   B) **冻结的契约值**（docs/rtl/RTL_ARITHMETIC_CONTRACT.md §4，与顶层
#      initial 块里那 17 条断言逐条对应）。这是"外部参照"：A 被改就变红。
#   C) elaborate 之后设计上**可观测的位宽**（slice_sel 18 位、main_sw 18*63 位…）。
#      这一份挡的是"参数被 --params 覆盖掉"或"顶层例化端口接错"——
#      那时 A 和 B 都没变，只有实际的电路变了。
#
# ⚠️ 本段所有 `puts` **一律写 ASCII**（真需求，不是风格偏好）：
#    dc_shell 的 stdout 会把非 ASCII 字符逐个打成 `?`。实测（同一台 VM、同一版 DC）：
#        puts "DC_PARAM_FAIL: $bad 条不一致"            ->  DC_PARAM_FAIL: 2 ????
#        puts "DC_PARAM_MISMATCH: 端口 $port 位宽 = ..." ->  DC_PARAM_MISMATCH: ?? dout ?? = ...
#    报错里的关键内容（哪个常量、哪个端口）会整块丢掉 —— "能变红但看不出红在哪"
#    等于半个门禁。中文解释一律留在注释里（注释不进 log，不会被打坏）。
proc dc_port_bits {name} {
    # 返回端口/总线的位宽。DC 里总线通常被展开成逐位端点，也可能是一个总线对象。
    #
    # ⚠️ 两个实测踩到的点，都写在这里免得下次再踩：
    # 1) **不能用 `${name}*` 直接数个数**：`dout*` 会把 1 位的 `dout_valid`
    #    也算进来，于是 dout 报 21 而不是 20 —— 绿例第一次跑就是被这个判红的
    #    （"检查自己写错" 与 "设计真的错了" 在输出上长得一样，所以必须让检查
    #    的判据本身可复核：这里只数 `名字[数字]` 形式的展开端点）。
    # 2) 取不到就返回 NA，**不允许把 NA 当成通过**（见调用处）。
    if {[catch { set cands [get_ports -quiet ${name}*] }]} { return "NA" }
    if {[sizeof_collection $cands] == 0} { return "ABSENT" }
    set nbits 0
    foreach_in_collection p $cands {
        # 只认 `x[3]` / `x[17][62]` 这种展开端点，排除 `dout_valid` 这类同前缀标量
        if {[regexp {\[[0-9]+\]$} [get_object_name $p]]} { incr nbits }
    }
    if {$nbits > 0} { return $nbits }
    if {[sizeof_collection $cands] == 1} {
        foreach attr {width bit_width bus_width} {
            if {![catch { set v [get_attribute $cands $attr] }]} {
                if {[string is integer -strict $v]} { return $v }
            }
        }
    }
    return "NA"
}

proc dc_check_rtl_params {hdr} {
    # B) 冻结的契约值。来源：docs/rtl/RTL_ARITHMETIC_CONTRACT.md §4 与
    #    rtl/top/sar20_digital_core.sv 的 initial 自检块（逐条对应）。
    set expect [dict create \
        W_FRAC 30 W_BITS 48 V_FRAC 32 V_BITS 64 ACC_BITS 96 OUT_BITS 20 \
        N_SLICES 18 N_ACTIVE 8 N_UNIT_MAIN 63 N_UNIT_SUB 8 N_UNIT_TOTAL 71 \
        DAC_LEVELS 512 B1 9 ADC2_BITS 12 PHASES 16]
    # C) 常量 -> (端口名, 乘数)。端口不存在就跳过（不同顶层端口不同）；
    #    端口存在但**位宽测不出来**则算失败 —— 否则这就是个"没人检查的检查"。
    set width_map [dict create \
        N_SLICES     {slice_sel 1} \
        OUT_BITS     {dout 1} \
        V_BITS       {inj_q 1} \
        ADC2_BITS    {adc2_code 1} \
        N_UNIT_MAIN  {main_sw 18} \
        N_UNIT_SUB   {sub_sw 18}]

    puts "DC_INFO: rtl params check: header = $hdr"
    if {![file exists $hdr]} {
        puts "DC_FATAL(exit 5): rtl params header not found: $hdr"
        return 0
    }

    set fh [open $hdr r]; set txt [read $fh]; close $fh

    # 头文件自带的 payload sha（tools/export_rtl_params.py 生成）——把它记进
    # status.txt，"这份报告是哪一版参数产出的"就有了不可辩驳的锚点。
    set ::RTL_PARAMS_SHA "NA"
    if {[regexp {payload sha\s*:\s*([0-9a-fA-F]+)} $txt -> m]} { set ::RTL_PARAMS_SHA $m }

    # A) 解析 `localparam [<range>] NAME = <w>'d<value>;`（本文件的实际写法）
    set got [dict create]
    foreach line [split $txt "\n"] {
        if {[regexp {localparam\s*\[[^\]]*\]\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[0-9]*'d([0-9]+)} $line -> nm val]} {
            dict set got $nm $val
        }
    }
    if {[dict size $got] == 0} {
        puts "DC_FATAL(exit 5): parsed 0 localparam from $hdr (header syntax changed? fix the parser)"
        return 0
    }
    puts "DC_INFO: rtl params payload_sha=$::RTL_PARAMS_SHA parsed=[dict size $got] constants"

    set bad 0
    # ---- A vs B ----
    dict for {nm exp} $expect {
        if {![dict exists $got $nm]} {
            puts "DC_PARAM_MISMATCH: $nm not found in $hdr (expected $exp)"; incr bad
            continue
        }
        set a [dict get $got $nm]
        if {$a != $exp} {
            puts "DC_PARAM_MISMATCH: $nm = $a (header) != $exp (frozen contract section 4)"; incr bad
        }
    }
    # 派生恒等式（顶层 initial 块里那两条）
    if {[dict exists $got B1]} {
        set d [expr {(1 << [dict get $got B1]) - 1}]
        if {$d != 511} { puts "DC_PARAM_MISMATCH: (1<<B1)-1 = $d != 511 (SADC thermometer width)"; incr bad }
    }
    if {[dict exists $got N_UNIT_MAIN] && [dict exists $got N_UNIT_SUB] && [dict exists $got N_UNIT_TOTAL]} {
        set s [expr {[dict get $got N_UNIT_MAIN] + [dict get $got N_UNIT_SUB]}]
        if {$s != [dict get $got N_UNIT_TOTAL]} {
            puts "DC_PARAM_MISMATCH: N_UNIT_MAIN+N_UNIT_SUB = $s != N_UNIT_TOTAL = [dict get $got N_UNIT_TOTAL]"
            incr bad
        }
    }
    # ---- A vs C（只看实际电路）----
    dict for {nm spec} $width_map {
        if {![dict exists $got $nm]} { continue }
        lassign $spec port mul
        set want [expr {[dict get $got $nm] * $mul}]
        set have [dc_port_bits $port]
        if {$have eq "ABSENT"} { continue }
        if {$have eq "NA"} {
            puts "DC_PARAM_MISMATCH: port '$port' exists but its width is unreadable; cannot verify $nm == $want (NA is NOT a pass)"
            incr bad
        } elseif {$have != $want} {
            # ⚠️ 这里必须写 ${nm}：写成 `$nm(...)` 会被 Tcl 当成**数组元素引用**
            # （`nm(20)`），报 "can't read \"nm(20)\": variable isn't array"。
            # 实测踩过：绿例第一次跑就是死在这一行，被顶层 catch 转成 exit 1。
            puts "DC_PARAM_MISMATCH: port $port width = $have != ${nm}([dict get $got $nm]) * $mul = $want"
            incr bad
        } else {
            puts "DC_INFO: width ok: $port = $have (== $nm * $mul)"
        }
    }

    if {$bad > 0} {
        puts "DC_PARAM_FAIL: $bad mismatch(es) found"
    } else {
        puts "DC_PARAM_OK: rtl params consistent (sha=$::RTL_PARAMS_SHA, header=$hdr)"
    }
    flush stdout
    return [expr {$bad == 0}]
}

# 在 INCDIRS / search_path 里找一个叫 rtl_params.vh 的文件。找到就返回路径。
proc dc_find_rtl_params {incdirs} {
    foreach d [concat $incdirs [list "."]] {
        if {$d eq ""} { continue }
        set p [file join $d rtl_params.vh]
        if {[file exists $p]} { return $p }
    }
    return ""
}

# 诊断：顶层扇出最大的网 + 时序单元数。
# 为什么需要（实测动机）：顶层 `sar20_digital_core` 的 `compile` **发散了** ——
# 面积以 ~4000 um^2/s 单调膨胀到 118 万（recon_core 单独才 31.5 万）、
# `WORST NEG SLACK` 卡在 3.77e6 ns 不动、`DESIGN RULE COST` 1.4e10，
# 同时**空闲内存被吃到只剩 1 GB**。这种"只涨面积不涨时序"的形状，
# 常见成因是某个网扇出到上万个时序单元（典型是 `clk` / 异步复位 `rst_n`），
# 而本流程又对整个 design 下了 `set_max_transition`，于是 DC 去造一棵巨大的
# 缓冲树 —— 而时钟树/复位树本来该由 APR 做，不该在 RTL 综合里烧。
# 这一步只在 `DC_CHECK_ONLY` 下跑（它只是几个 collection 查询、秒级），
# 用来把这个"假说"变成可看的数字，而不是靠猜。
proc dc_fanout_diag {} {
    set ff_cnt "NA"
    if {![catch { set ffs [get_cells -hierarchical -filter "is_sequential==true"] }]} {
        set ff_cnt [sizeof_collection $ffs]
    }
    puts "DC_DIAG: sequential_cells=$ff_cnt"
    # ⚠️ 不要用 `fanout_load` 来数扇出：实测在**未映射**（elaborate 之后、compile 之前）
    # 的网表上这个属性是**空的** —— 全阈值都是 0，而且直接打 clk/rst_n 的
    # `fanout_load` / `total_capacitance` 出来是两个空串。用 0 当"没有大扇出网"
    # 是**把取不到值当成了测到 0**（和 §6 坑里"NA 不能当通过"是同一条纪律）。
    # 改成直接数连在网上的 pin 个数 —— 这个在 elaborate 之后就有。
    foreach nm {clk rst_n cfg_ready sw_valid wr_en inj_q[0] dout[0]} {
        if {[catch { set c [get_nets -quiet $nm] }]} { continue }
        if {[sizeof_collection $c] == 0} { puts "DC_DIAG: net '$nm' ABSENT"; continue }
        set pc "NA"
        if {![catch { set p [get_pins -quiet -of $c] }]} { set pc [sizeof_collection $p] }
        puts "DC_DIAG: net '$nm' connected_pins=$pc"
    }
    flush stdout
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
proc dc_main {} {

# ---- 0) 读参数 -----------------------------------------------------------
# DC_PREFLIGHT=1 时只做"库能不能读进来"的快速体检（约 40 s），不综合。
# 用途：换 corner / 换库时先验证路径与 license，不必等一次完整综合。
set PREFLIGHT [expr {[info exists ::env(DC_PREFLIGHT)] && $::env(DC_PREFLIGHT) ne "" && $::env(DC_PREFLIGHT) ne "0"}]

set required {OUT_DIR}
if {!$PREFLIGHT} { set required {TOP FILES OUT_DIR CLK_PERIOD} }
foreach v $required {
    if {![info exists ::env($v)] || $::env($v) eq ""} {
        puts "DC_FATAL(exit 2): required environment variable $v is not set"
        exit 2
    }
}

set ::TOP        [expr {[info exists ::env(TOP)] ? $::env(TOP) : "preflight"}]
set FILES        [expr {[info exists ::env(FILES)] ? $::env(FILES) : ""}]
set ::OUT_DIR    $::env(OUT_DIR)
set CLK_PERIOD   [expr {[info exists ::env(CLK_PERIOD)] && $::env(CLK_PERIOD) ne "" ? $::env(CLK_PERIOD) : "10"}]
set CLK_NAME   [expr {[info exists ::env(CLK_NAME)] && $::env(CLK_NAME) ne "" ? $::env(CLK_NAME) : "clk"}]
set INCDIRS    [expr {[info exists ::env(INCDIRS)] ? $::env(INCDIRS) : ""}]
set DEFINES    [expr {[info exists ::env(DEFINES)] ? $::env(DEFINES) : ""}]
set ::PARAMS   [expr {[info exists ::env(PARAMS)] ? $::env(PARAMS) : ""}]
# COMPILE_MODE: ultra（默认，compile_ultra -no_autoungroup）| compile（快得多、
# 结果差一些）。存在的理由：顶层 sar20_digital_core 的 compile_ultra 实测跑了一小时
# 以上还没出报告，"先要一份能看的 area/timing" 是有价值的中间结论 —— 但**必须标清楚**，
# 所以它会被写进 status.txt 的 COMPILE_MODE= 一行。
set COMPILE_MODE [expr {[info exists ::env(COMPILE_MODE)] && $::env(COMPILE_MODE) ne "" ? $::env(COMPILE_MODE) : "ultra"}]
# DC_CHECK_ONLY=1：只做到"参数检查 + check_design"就收工（不起 compile）。
# 用途：改完头文件/脚本后几秒~几十秒就能红绿一次，不用等一次完整综合。
set CHECK_ONLY [expr {[info exists ::env(DC_CHECK_ONLY)] && $::env(DC_CHECK_ONLY) ne "" && $::env(DC_CHECK_ONLY) ne "0"}]
# 早期失败（源文件列表错等）也要能写 status.txt，所以这里先给默认值
set ::RTL_PARAMS_SHA "NA"
set ::LIB_DB_USED ""
set CORNER     [expr {[info exists ::env(CORNER)] && $::env(CORNER) ne "" ? $::env(CORNER) : "tt0p9v25c"}]
set DRIVE_CELL [expr {[info exists ::env(DRIVE_CELL)] && $::env(DRIVE_CELL) ne "" ? $::env(DRIVE_CELL) : "BUFFD2BWP7T40P140"}]
# The documented TSMC library uses 1 pF per capacitance unit. Override for other libraries.
set LIB_CAP_UNIT_PF [expr {[info exists ::env(LIB_CAP_UNIT_PF)] ? $::env(LIB_CAP_UNIT_PF) : "1.0"}]
if {![string is double -strict $LIB_CAP_UNIT_PF] || !($LIB_CAP_UNIT_PF > 0 && $LIB_CAP_UNIT_PF < Inf)} {
    dc_die 2 "LIB_CAP_UNIT_PF must be finite and positive"
}
set LOAD_PF    [expr {[info exists ::env(LOAD_PF)] && $::env(LOAD_PF) ne "" ? $::env(LOAD_PF) : "0.02"}]
set IN_DELAY   [expr {[info exists ::env(IN_DELAY)] && $::env(IN_DELAY) ne "" ? $::env(IN_DELAY) : "0.0"}]
set OUT_DELAY  [expr {[info exists ::env(OUT_DELAY)] && $::env(OUT_DELAY) ne "" ? $::env(OUT_DELAY) : "0.0"}]

if {![string is double -strict $CLK_PERIOD] || $CLK_PERIOD <= 0} {
    puts "DC_FATAL(exit 2): CLK_PERIOD must be a positive number, got '$CLK_PERIOD'"
    exit 2
}
if {!$PREFLIGHT} {
    if {[llength $FILES] == 0} {
        puts "DC_FATAL(exit 2): FILES is empty"
        exit 2
    }
    foreach f $FILES {
        if {![file exists $f]} {
            puts "DC_FATAL(exit 1): source file not found: $f"
            exit 1
        }
    }
}

file mkdir $::OUT_DIR
# phase.log 每次运行**截断重来**：dc_phase 是 append 的，同一个 OUT_DIR 上重跑
# （比如绿例改完脚本又跑一次）会把两次运行的阶段混在一起，看起来像"analyze 跑了
# 两遍"。status.txt 由 wrapper 删，phase.log 在这里删，各自只删自己的。
catch { file delete [file join $::OUT_DIR phase.log] }

# ---- 1) 解析库路径 -------------------------------------------------------
# 首选 PDK 自带的 config/env.sh 定义的 CCS 目录（run_synth.sh 会 source 它）；
# 否则回退到硬编码的 RVT 目录。两者都是"列出来、验证过"的路径。
set ::LIB_DB_USED ""
if {[info exists ::env(LIB_DB)] && $::env(LIB_DB) ne ""} {
    set ::LIB_DB_USED $::env(LIB_DB)
} else {
    if {[info exists ::env(TSMC28HPCPLUS_CCS_RVT)] && $::env(TSMC28HPCPLUS_CCS_RVT) ne ""} {
        set ccs_root $::env(TSMC28HPCPLUS_CCS_RVT)
    } else {
        set ccs_root "/home/yian/PDK/tsmc28hpcplus/TSMCHOME/digital/Front_End/timing_power_noise/CCS/tcbn28hpcplusbwp7t40p140_180a"
    }
    # PDK 的 corner 命名有两种，且**同一颗角在两种命名下名字不同**：
    #   SIGNOFF_LIB_CORNERS（.lib / 卡名字）: ssg0p81v0p81v125c   tt0p9v0p9v25c
    #   SIGNOFF_DB_CORNERS （.db 文件名）    : ssg0p81v125c       tt0p9v25c
    # 差别就是电压段重复了一次。这里做一次归一化，让 --corner 传哪种都能命中，
    # 免得使用者照抄 env.sh 里的 SIGNOFF_LIB_CORNERS 却找不到文件。
    set CORNER_NORM $CORNER
    if {[regexp {^([a-z]+[0-9]+p[0-9]+v)([0-9]+p[0-9]+v)(.+)$} $CORNER -> g1 g2 g3]} {
        set CORNER_NORM "${g1}${g3}"
    }
    set cands [list \
        [file join $ccs_root "tcbn28hpcplusbwp7t40p140${CORNER}_ccs.db"] \
        [file join $ccs_root "tcbn28hpcplusbwp7t40p140${CORNER_NORM}_ccs.db"] ]
    foreach c $cands {
        if {[file exists $c]} { set ::LIB_DB_USED $c ; break }
    }
    if {$::LIB_DB_USED eq ""} {
        puts "DC_FATAL(exit 1): no .db found for corner '$CORNER' under $ccs_root"
        puts "  tried: $cands"
        puts "  available .db in that directory:"
        foreach f [lsort [glob -nocomplain [file join $ccs_root *.db]]] { puts "    [file tail $f]" }
        exit 1
    }
}
if {![file exists $::LIB_DB_USED]} {
    puts "DC_FATAL(exit 1): LIB_DB does not exist: $::LIB_DB_USED"
    exit 1
}
puts "DC_INFO: using library DB = $::LIB_DB_USED"

if {[catch { read_db $::LIB_DB_USED } rc]} {
    puts "DC_FATAL(exit 1): read_db failed: $rc"
    exit 1
}

# 找出刚读进来的技术库（排除 DC 内置的 gtech / standard.sldb）
set TECH_LIBS {}
foreach_in_collection l [get_libs *] {
    set n [get_object_name $l]
    if {$n ne "gtech" && $n ne "standard.sldb"} { lappend TECH_LIBS $n }
}
if {[llength $TECH_LIBS] == 0} {
    puts "DC_FATAL(exit 1): read_db succeeded but no technology library is present"
    exit 1
}
set LIB_NAME [lindex $TECH_LIBS 0]
puts "DC_INFO: technology library = $LIB_NAME"

if {[info exists ::env(MAX_TRANS)] && $::env(MAX_TRANS) ne ""} {
    set MAX_TRANS $::env(MAX_TRANS)
} else {
    set MAX_TRANS [get_attribute [get_libs $LIB_NAME] default_max_transition]
}
puts "DC_INFO: default_max_transition = $MAX_TRANS ns"

# ⚠️ 这里有两个必须踩过才知道的坑，都做过对照实验：
#
# 坑 1（作用域）：把 `set target_library ...` 写在 proc 里只会创建 **proc 局部
#   变量**，DC 的应用变量根本没被赋值 -> compile 报 "No target library found.
#   (OPT-1312)"，而且会长时间空转，最后所有 report 都是 NA。必须用 set_app_var。
#
# 坑 2（取值）：`target_library` / `link_library` 里**必须写 .db 的完整路径**，
#   写库名（如 tcbn28hpcplusbwp7t40p140tt0p9v25c_ccs）会失败：
#       Error: Could not read the following target libraries: <name> (UIO-3)
#       Error: No target library found. (OPT-1312)
#   实测对照（同一个 8-bit counter，DC W-2024.09-SP3）：
#       写库名 -> Total cell area 0.000000（完全没映射，CELLS=20 全是 GTECH 门）
#       写路径 -> Total cell area 28.126000（正确映射到 TSMC 单元，CELLS=26）
#   注意 read_db 并不会让"写库名"这种做法变可用 —— 两种写法都带 read_db。
set_app_var target_library [list $::LIB_DB_USED]
set_app_var link_library   [list * $::LIB_DB_USED]
if {[catch { set tl_chk [get_app_var target_library] } rc]} { set tl_chk "" }
puts "DC_INFO: target_library = $tl_chk"
if {[llength $tl_chk] == 0} {
    puts "DC_FATAL(exit 1): target_library is empty after assignment"
    exit 1
}
if {[lsearch -exact $tl_chk $::LIB_DB_USED] < 0} {
    puts "DC_FATAL(exit 1): target_library does not contain '$::LIB_DB_USED' (got '$tl_chk')"
    exit 1
}

# ---- 1b) 预检模式到此为止 -------------------------------------------------
if {$PREFLIGHT} {
    set ncell [sizeof_collection [get_lib_cells -quiet ${LIB_NAME}/*]]
    puts "DC_PREFLIGHT_OK: lib=$LIB_NAME db=$::LIB_DB_USED cells=$ncell max_transition=$MAX_TRANS"
    puts "DC_PREFLIGHT_OK: corner_requested=$CORNER corner_normalized=$CORNER_NORM"
    dc_write_status PREFLIGHT_OK
    exit 0
}

# ---- 2) 读 RTL（analyze -> elaborate -> link）---------------------------
dc_phase "analyze begin"
set sv_opts ""
foreach d $INCDIRS {
    if {$d ne ""} { append sv_opts "+incdir+$d " }
}
foreach d $DEFINES {
    if {$d ne ""} { append sv_opts "+define+$d " }
}
if {$INCDIRS ne ""} { set_app_var search_path ". [join $INCDIRS { }]" }

puts "DC_INFO: analyze [llength $FILES] files, sv_opts='$sv_opts'"
foreach f $FILES {
    if {[catch { analyze -format sverilog -library WORK -vcs $sv_opts $f } rc]} {
        puts "DC_FATAL(exit 1): analyze failed on $f: $rc"
        exit 1
    }
}

# elaborate + link 输出重定向到文件，稍后用它抓 "unresolved reference"。
# DC 对"例化了但文件列表里没有的模块"只发 Warning 然后建黑盒，不报 Error；
# 不主动检查就会静默产出残缺网表。
#
# PARAMS 非空时走 `elaborate -parameters`（顶层 HDL 参数覆盖）。刻意写成
# 两个分支而不是拼字符串：`-parameters` 的取值必须整体作为**一个** Tcl 参数，
# 拼字符串一旦带了引号就会被拆开（这类"参数被拆成两半"的错，DC 只会
# 报一个看不懂的 usage 错，排查成本高）。
set elab_log [file join $::OUT_DIR elaborate.log]
dc_phase "elaborate begin"
puts "DC_INFO: elaborate $::TOP params='$::PARAMS'"
redirect $elab_log {
    if {$::PARAMS ne ""} {
        elaborate $::TOP -library WORK -architecture verilog -parameters $::PARAMS
    } else {
        elaborate $::TOP -library WORK -architecture verilog
    }
    current_design $::TOP
    link
    uniquify -force
}
if {[catch { current_design $::TOP } rc]} {
    puts "DC_FATAL(exit 1): current_design $::TOP failed (elaborate/link did not produce a design?)"
    exit 1
}

set elab_txt ""
if {![catch { set fh [open $elab_log r]; set elab_txt [read $fh]; close $fh } rc]} {
    foreach pat {"Unresolved reference" "Can't find the design" "Cannot find the design"} {
        if {[string first $pat $elab_txt] >= 0} {
            puts "DC_FATAL(exit 1): '$pat' found in $elab_log -- source file list is incomplete"
            exit 1
        }
    }
} else {
    puts "DC_WARN: cannot read $elab_log ($rc)"
}

# ---- 2b) 参数生效性指纹 ---------------------------------------------------
# `-parameters` 失效是**静默**的（吃不吃掉都不会报错，只是数字悄悄变成默认参数
# 那一版）。所以留一个随参数变化的规模指纹：同一次扫描里两个不同参数点如果
# 指纹与面积一模一样，就是"参数没生效"的红灯，而不是"参数不敏感"。
set hier_cnt "NA"
if {[catch { set hier_cnt [sizeof_collection [get_cells -hierarchical]] } rc]} {
    puts "DC_WARN: cannot count hierarchical cells: $rc"
}
puts "DC_INFO: design fingerprint: TOP=$::TOP PARAMS='$::PARAMS' hierarchical_cells=$hier_cnt"

# ---- 2c) RTL 参数一致性检查 ----------------------------------------------
# 见文件上方 dc_check_rtl_params 的注释：这一步补的是"initial 块被 VER-281
# 整块丢掉"留下的空洞。找不到头文件就跳过（有些顶层本来就不依赖它）。
dc_phase "params_check"
set hdr [dc_find_rtl_params $INCDIRS]
if {$hdr ne ""} {
    if {![dc_check_rtl_params $hdr]} {
        dc_write_status PARAMS_MISMATCH
        puts "DC_FATAL(exit 5): RTL params inconsistent with the frozen contract; refusing to synthesize (see DC_PARAM_MISMATCH lines above)"
        exit 5
    }
} else {
    puts "DC_WARN: no rtl_params.vh found under INCDIRS='$INCDIRS' -- RTL params check SKIPPED"
}

# ---- 3) check_design（elaborate 之后）------------------------------------
dc_phase "check_design"
set cd_pre [file join $::OUT_DIR check_design.rpt]
redirect $cd_pre { check_design -multiple_designs }
puts "DC_INFO: pre-compile check_design -> $cd_pre"

# ---- 3b) DC_CHECK_ONLY：只到"参数检查 + check_design"就收工 ----------------
# 改完头文件/脚本后要能"几十秒内红绿一次"，不必等一次完整综合（顶层要一小时+）。
if {$CHECK_ONLY} {
    dc_phase "fanout diag"
    catch { dc_fanout_diag }
    dc_phase "check_only done"
    dc_write_status CHECK_ONLY_OK
    puts "DC_CHECK_ONLY_OK: params + check_design passed (no compile); phase log = $::OUT_DIR/phase.log"
    exit 0
}

# ---- 4) 约束 -------------------------------------------------------------
dc_phase "constraints"
create_clock -name $CLK_NAME -period $CLK_PERIOD [get_ports $CLK_NAME]

set ports_in  [remove_from_collection [all_inputs] [get_ports -quiet $CLK_NAME]]
set ports_out [all_outputs]

set clk_tran [expr {$CLK_PERIOD * 0.05}]
set_input_transition $clk_tran [get_ports $CLK_NAME]
set_clock_uncertainty [expr {$CLK_PERIOD * 0.02}] [get_clocks $CLK_NAME]
set_clock_transition  $clk_tran [get_clocks $CLK_NAME]

if {[sizeof_collection $ports_in] > 0} {
    set_driving_cell -lib_cell $DRIVE_CELL -pin Z $ports_in
}
if {[sizeof_collection $ports_out] > 0} {
    set_load [expr {$LOAD_PF / $LIB_CAP_UNIT_PF}] $ports_out
}
# Zero is an explicit timing budget, not a request to omit the constraint.
if {[sizeof_collection $ports_in] > 0} {
    set_input_delay  $IN_DELAY  -clock $CLK_NAME $ports_in
}
if {[sizeof_collection $ports_out] > 0} {
    set_output_delay $OUT_DELAY -clock $CLK_NAME $ports_out
}
set_max_transition $MAX_TRANS [current_design]

puts "DC_INFO: constraints = clk $CLK_NAME @ ${CLK_PERIOD}ns, drive=$DRIVE_CELL, load=${LOAD_PF}pF (library unit=${LIB_CAP_UNIT_PF}pF), max_trans=$MAX_TRANS"

# ---- 5) compile ----------------------------------------------------------
# COMPILE_MODE=compile 时走非 ultra 的 compile：快得多，面积/时序都差一些，
# 但**能出报告**。status.txt 里会记 COMPILE_MODE=，引用数字时必须一起引。
#
# DESIGN_RULE_FIX=0 时给 `compile` 加 `-no_design_rule`（关掉设计规则修复）。
# 为什么需要这个开关（实测动机）：顶层 `sar20_digital_core` 与 `weight_store`
# 都出现过"面积单调膨胀、slack 一动不动、DESIGN RULE COST 到 1e9~1e10"的发散形状。
# 这类形状的一个可能成因是 DRC 修复在无界加缓冲/加粗单元。这个开关就是用来
# **单变量**地把这一项排除掉或定死。它只对 COMPILE_MODE=compile 有意义
# （compile_ultra 没有这个选项），所以配错时直接报参数错，不静默忽略。
set DRF "1"
if {[info exists ::env(DESIGN_RULE_FIX)] && $::env(DESIGN_RULE_FIX) ne ""} { set DRF $::env(DESIGN_RULE_FIX) }
if {$DRF ne "0" && $DRF ne "1"} {
    puts "DC_FATAL(exit 2): DESIGN_RULE_FIX must be 0 or 1, got '$DRF'"
    exit 2
}
if {$DRF eq "0" && $COMPILE_MODE ne "compile"} {
    puts "DC_FATAL(exit 2): DESIGN_RULE_FIX=0 requires COMPILE_MODE=compile (compile_ultra has no -no_design_rule)"
    exit 2
}
if {$COMPILE_MODE ni {compile ultra}} {
    puts "DC_FATAL(exit 2): unsupported COMPILE_MODE=$COMPILE_MODE"
    exit 2
}
set comp_tool compile_ultra
set comp_args [list -no_autoungroup]
if {$COMPILE_MODE eq "compile"} {
    set comp_tool compile
    set comp_args [list]
    if {$DRF eq "0"} { set comp_args [list -no_design_rule] }
}
dc_phase "compile begin (mode=$COMPILE_MODE design_rule_fix=$DRF)"
puts "DC_INFO: compile command = $comp_tool; args = '$comp_args'"
if {[catch { $comp_tool {*}$comp_args } rc]} {
    puts "DC_FATAL(exit 1): COMPILE_MODE=$COMPILE_MODE args='$comp_args' failed: $rc"
    exit 1
}
dc_phase "compile done"

# ---- 6) 报告 -------------------------------------------------------------
dc_phase "reports"
set r_area  [file join $::OUT_DIR area.rpt]
set r_time  [file join $::OUT_DIR timing.rpt]
set r_power [file join $::OUT_DIR power.rpt]
set r_qor   [file join $::OUT_DIR qor.rpt]
set r_cd    [file join $::OUT_DIR check_design.rpt]

redirect $r_area {
    puts "================ report_area (hierarchical) ================"
    report_area -hierarchy
    puts ""
    puts "================ report_area (flat) ========================"
    report_area
}
redirect $r_time {
    puts "================ max delay (setup) ========================="
    report_timing -max_paths 10 -nworst 10 -delay max -input_pins
    puts ""
    puts "================ min delay (hold) =========================="
    report_timing -max_paths 5 -nworst 5 -delay min
    puts ""
    puts "================ clock ====================================="
    report_clock
    puts ""
    puts "================ constraints (violators) ==================="
    report_constraint -all_violators
}
redirect $r_power { report_power }
redirect $r_qor   { report_qor }
redirect -append $r_cd {
    puts ""
    puts "================ post-compile check_design ================="
    check_design -multiple_designs
}

# ---- 7) 提取数字 -> status.txt -> 退出码 ---------------------------------
set wns [dc_slack max 1]
# ⚠️ TNS 的路径条数**必须封顶**（实测踩过，代价 13 分钟 + 一次作业作废）：
#   原来这里写 1000000。在 recon_core（leaf cell 492504、以组合逻辑为主）上，
#   `get_timing_paths -nworst 1000000 -max_paths 1000000` 跑了 13 分钟以上还没返回，
#   把整条 P_STAGES 扫描卡死在第一个参数点上。
#   注意此时**所有报告都已经写完了**（area/timing/power/qor/check_design 都在盘上），
#   卡住的只有 status.txt —— 也就是"把已经拿到的结果锁死在一个无关紧要的数字后面"。
#   封顶后 TNS = 返回路径中负 slack 之和（非完整 endpoint TNS），对 WNS / 面积 / cell 数**无影响**；
#   同一 endpoint 可能返回多条路径，不能把本字段当作完整 endpoint TNS；签核另读 STA 汇总。
#   封顶值记进 status.txt 的 TNS_NWORST=，引用 TNS 时必须连着它一起引。
set ::TNS_NWORST 2000
set tns [dc_slack max $::TNS_NWORST sum_negative]

set area_val "NA"
# `get_attribute [current_design] area` 在这个版本上返回空串（实测），
# 所以改成把 report_area 的输出抓成字符串再正则提数 —— 这条路径是可靠的。
set area_raw [file join $::OUT_DIR .area_raw.rpt]
if {![catch { redirect $area_raw { report_area } ; set fh [open $area_raw r] ; set atxt [read $fh] ; close $fh } rc]} {
    if {[regexp {Total cell area:\s*([0-9eE.+-]+)} $atxt -> m]} { set area_val $m }
}
set cell_cnt "NA"
if {[catch { set cell_cnt [sizeof_collection [get_cells -hierarchical -filter "is_hierarchical==false"]] } rc]} {
    puts "DC_WARN: cannot count cells: $rc"
}

puts "DC_RESULT: WNS=$wns TNS=$tns AREA=$area_val CELLS=$cell_cnt CLK_PERIOD=$CLK_PERIOD COMPILE_MODE=$COMPILE_MODE"
dc_phase "done (wns=$wns area=$area_val)"

if {$wns eq "NA"} {
    # 报不出来 = 约束没生效或没有路径，属于流程问题，不能当成功
    puts "DC_FATAL(exit 1): could not extract any timing path slack (constraints not applied?)"
    dc_write_status NO_TIMING NA NA $area_val $cell_cnt
    exit 1
}

if {$wns < 0} {
    dc_write_status TIMING_NOT_MET $wns $tns $area_val $cell_cnt
    puts "DC_DONE: compiled but TIMING NOT MET (WNS = $wns ns < 0)"
    exit 3
}

dc_write_status OK $wns $tns $area_val $cell_cnt
puts "DC_DONE: OK (WNS = $wns ns)"
exit 0

}  ;# end proc dc_main

# ---------------------------------------------------------------------------
# 顶层兜底：把任何未预期的 Tcl 错误转成退出码 1
# ---------------------------------------------------------------------------
if {[catch { dc_main } rc]} {
    puts "DC_FATAL(exit 1): uncaught error in flow: $rc"
    catch { dc_write_status FAILED }
    exit 1
}
# 正常路径下 dc_main 内部一定已经 exit，不会走到这里；万一走到也算失败。
puts "DC_FATAL(exit 1): flow ended without an explicit exit code"
catch { dc_write_status FAILED }
exit 1
