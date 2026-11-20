# =============================================================================
# 文件名     : design_inputs.tcl
# 功能       : 从本次检出解析生产 filelist，绑定 DC 网表、SVF 与实际 RTL 字节。
# 设计说明   : 可直接用 tclsh 检查，读取源文件不需要 EDA 或 PDK。SHA-256 接收
#              文件原字节，收据只写仓库相对名，因此可在另一个工作目录复核。
# =============================================================================
namespace eval v21 {
    # 在 source 当下捕获脚本位置，后续切换工具工作目录不会重定向 RTL。
    variable root [file normalize [file join [file dirname [info script]] .. .. ..]]
    variable manifest flow/build/filelists/files_adc_v21.f
}

# 1. 路径。源码根由脚本自身确定；只有 PDK 和运行产物属于主机配置。
proc v21::location {key fallback} {
    if {[info exists ::env($key)] && $::env($key) ne ""} {
        return [file normalize $::env($key)]
    }
    return [file normalize $fallback]
}
proc v21::pdk {} { return [location V21_PDK_ROOT [file join ~ PDK tsmc28 digital_technology]] }
proc v21::dc_dir {} { return [location V21_DC_DIR [file join [pwd] dc]] }

proc v21::read_text {path} {
    # 文本读取仅用于清单/收据。RTL 与二进制 SVF 在 digest 中保持原字节。
    set channel [open $path r]
    set data [read $channel]
    close $channel
    return [string trimright $data "\r\n"]
}
proc v21::relative_sources {} {
    variable root
    variable manifest
    set entries {}
    foreach line [split [read_text [file join $root $manifest]] "\n"] {
        set name [string trim $line]
        if {$name eq "" || [string match {//*} $name]} { continue }
        # 生产清单只允许 RTL 路径。测试台、仿真宏和嵌套清单各有自己的作用域。
        if {![regexp {^rtl/([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.sv$} $name] ||
            ![file isfile [file join $root $name]] || [lsearch -exact $entries $name] >= 0} {
            error "invalid, missing or repeated production source: $name"
        }
        lappend entries $name
    }
    if {![llength $entries]} { error "empty production filelist: $manifest" }
    return $entries
}
proc v21::sources {} {
    variable root
    # analyze 与 read_sverilog 的调用形式不同，但直接消费相同的绝对路径序列。
    set paths {}
    foreach name [relative_sources] { lappend paths [file join $root $name] }
    return $paths
}

# 2. 收据。输入摘要能发现同名旧 RTL；成品摘要能发现另一次综合覆盖了网表或 SVF。
proc v21::digest {path} {
    if {![file isfile $path] || ![file size $path]} { error "missing or empty input: $path" }
    # 不经 shell 拼接路径。两种实现都从 stdin 哈希，收据与工作目录拼写无关。
    if {[auto_execok sha256sum] ne ""} { return [lindex [exec sha256sum < $path] 0] }
    return [lindex [exec shasum -a 256 < $path] 0]
}
proc v21::rtl_identity {} {
    variable manifest
    variable root
    # 清单本身也绑定。交换读取顺序或编辑来源标注都必须能与旧记录区分。
    set rows {}
    foreach name [linsert [relative_sources] 0 $manifest] {
        lappend rows "[digest [file join $root $name]]  $name"
    }
    return [join $rows "\n"]
}
# 同一次综合的网表与 SVF 是一对；仅验证 RTL 不足以识别被替换的实现端。
# 收据不宣称 PDK、约束选择或工具签核已通过，相关结果仍需原始运行日志。
proc v21::product_identity {dir design} {
    set rows {}
    foreach name [list ${design}_syn.v ${design}.svf] {
        lappend rows "[digest [file join $dir $name]]  $name"
    }
    return [join $rows "\n"]
}
proc v21::begin_dc {dir design} {
    file mkdir $dir
    # 独占标记还区分中断与完成。失败的目录保留原貌，重试可另选一个新目录。
    set input [rtl_identity]
    set pending [file join $dir .v21-inputs.pending]
    set channel [open $pending {WRONLY CREAT EXCL}]
    foreach name [list ${design}_syn.v ${design}.svf v21-inputs.sha256] {
        if {[file exists [file join $dir $name]]} {
            close $channel
            file delete $pending
            error "DC output already exists: $dir/$name"
        }
    }
    puts $channel $input
    close $channel
}
proc v21::finish_dc {dir design} {
    # 源码可能在一次长综合中发生变化。发布收据前重验启动时保存的身份。
    set pending [file join $dir .v21-inputs.pending]
    if {[read_text $pending] ne [rtl_identity]} { error "RTL inputs changed during synthesis" }
    set output [product_identity $dir $design]
    set channel [open $pending a]
    puts $channel $output
    close $channel
    # 完整收据以同文件系统 rename 发布，读者不会看到只写了一半的文件。
    file rename $pending [file join $dir v21-inputs.sha256]
}
proc v21::check_dc {dir design} {
    # 只比较文本数据，不将收据当作 Tcl 脚本执行。
    set expected "[rtl_identity]\n[product_identity $dir $design]"
    if {[read_text [file join $dir v21-inputs.sha256]] ne $expected} {
        error "DC receipt differs from this RTL, netlist or SVF: $dir"
    }
}

# 3. 静态入口。直接执行才打印；由 DC、FC、FM source 时只加载上面的定义。
if {[info exists argv0] && [file normalize [info script]] eq [file normalize $argv0]} {
    if {$argv eq {--list}} {
        puts [join [v21::relative_sources] "\n"]
    } elseif {[llength $argv] == 2 && [lindex $argv 0] eq "--check-dc"} {
        v21::check_dc [lindex $argv 1] adc_digital_ip_v21
        puts "DC inputs and products match the receipt"
    } else {
        puts stderr "usage: tclsh design_inputs.tcl --list | --check-dc DIR"
        exit 2
    }
}
