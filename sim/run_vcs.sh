#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# sim/run_vcs.sh —— 在远程 EDA 虚拟机上用 Synopsys VCS 编译并运行 SystemVerilog
#
# 设计目标：把「编译 + 运行 + 判定成败 + 落完整日志」封成一条可复用命令，让上层
# （本仓库的 tools/run_rtl_sim.py）只需要关心「传哪些文件、顶层是谁」。
#
# ---------------------------------------------------------------------------
# 所需环境（在 192.168.38.129 上已就绪，脚本不依赖交互式 shell）
#
#   VCS_HOME              = /opt/synopsys/vcs/W-2024.09-SP1
#   PATH                  需包含 $VCS_HOME/bin
#   SNPSLMD_LICENSE_FILE  = /opt/synopsys/scl/2025.03/admin/license/Synopsys.lic
#   LM_LICENSE_FILE       = 27080@localhost
#                         （license 由 systemd 服务 snpslmd.service 提供，
#                            `systemctl is-active snpslmd.service` == active）
#
# 实测：在该 VM 上 VCS 开箱可用 —— 即使 `ssh host '<cmd>'`（非交互、非登录、
# BASH_ENV 未设置）也能看到 VCS_HOME 与 $VCS_HOME/bin 已在 PATH 中，无需再
# `source` 任何环境脚本。为防换机器，本脚本仍做了「环境兜底」（ENV FALLBACK
# 段）：VCS_HOME 为空则取默认值，vcs 不在 PATH 则显式加上 $VCS_HOME/bin。
#
# 注意：VCS W-2024.09 已不再自带 DVE，本脚本不涉及任何 GUI 波形工具；
#       要看波形请用 Verdi（$VERDI_HOME/bin/verdi），或在 TB 里 $dumpvars。
# ---------------------------------------------------------------------------
# 用法：
#   sim/run_vcs.sh -top <TOP> [选项] <file.sv> [<file.sv> ...]
#
# 选项：
#   -top <name>       顶层模块名（必填）。以 `vcs -top <name>` 传入。
#   -f <filelist>     文件列表文件，每行一个路径，`#` 起始行为注释，空行忽略。
#                     可与命令行文件参数混用（追加在后面）。
#   -incdir <dir>     include 搜索目录（可重复），展开成 +incdir+<dir>
#   -define <X[=V]>   宏定义（可重复），展开成 +define+<X[=V]>
#   +<anything>       任意 `+` 前缀参数原样透传给 vcs（可重复）
#   -o <name>         生成的可执行文件名，默认 simv
#   -l <path>         合并日志文件（编译段 + 运行段 + 汇总），默认 <workdir>/vcs.log
#   -w <dir>          工作目录：编译产物、simv、覆盖率库都在这里，默认当前目录
#   -timescale <ts>   时间精度，默认 1ns/1ps
#   -cm <types>       覆盖率类型，例如 line+cond+fsm+tgl+branch（默认关闭）。
#                     开启时同时传给 vcs 与 simv。
#   -cm-dir <dir>     覆盖率数据库目录，默认 <workdir>/cov.vdb（仅 -cm 时有效）
#   -vcs-arg <arg>    额外透传给 vcs 的参数（可重复）
#   -sim-arg <arg>    额外透传给 simv 的参数（可重复）
#   -timeout <sec>    仿真运行超时（秒），默认 0 = 不限制；超时退出码 5
#   -no-run           只编译，不运行
#   -v | --verbose    把编译/运行的完整日志同时打到 stdout（默认只打尾部摘要）
#   -h | --help       显示本帮助
#
# 路径语义：<file.sv> / -f / -incdir / -l 里的相对路径，一律按「调用本脚本时的
# 当前目录」解析；解析完统一转成绝对路径后再 cd 到 -w 工作目录执行。
#
# ---------------------------------------------------------------------------
# 退出码（供上层脚本判定，不与 shell 常规约定冲突）
#   0  编译 + 运行均成功，且未观察到任何 error/fatal
#   1  用法或环境错误（缺 -top、vcs 不在 PATH、输入文件不存在……）
#   2  编译失败（vcs 非零退出）
#   3  运行期致命错误：$fatal / UVM_FATAL / 断言 fatal，或 simv 非零退出
#   4  运行期非致命错误：$error / UVM_ERROR / 断言失败，仿真本身跑完了
#   5  仿真超时（-timeout 触发）
#
# 实现说明（为什么这样判定，而不是只看 simv 退出码）：
#   VCS 的 simv 默认**无论 $error 还是 $fatal 都以 0 退出**（本机实测确认）。
#   官方让它反映错误的开关是运行期选项 `-exitstatus`：实测 0=干净、2=出现
#   $error、3=出现 $fatal。本脚本总是加 `-exitstatus`，并额外对运行日志做一次
#   正则统计作为兜底，覆盖 UVM report（UVM_ERROR/UVM_FATAL）与 SVA 失败这类
#   `-exitstatus` 之外的来源。
# =============================================================================

# 注意：`set -euo pipefail` 已经放在文件最开头（第 2 行），这里不再重复。

# ---------------------------------------------------------------------------
# ENV FALLBACK —— 换机器时的兜底；当前 VM 上这些值本来就在环境里
# ---------------------------------------------------------------------------
: "${VCS_HOME:=/opt/synopsys/vcs/W-2024.09-SP1}"
case ":$PATH:" in
    *":$VCS_HOME/bin:"*) ;;
    *) PATH="$VCS_HOME/bin:$PATH" ;;
esac
export PATH
: "${SNPSLMD_LICENSE_FILE:=/opt/synopsys/scl/2025.03/admin/license/Synopsys.lic}"
: "${LM_LICENSE_FILE:=27080@localhost}"
export SNPSLMD_LICENSE_FILE LM_LICENSE_FILE

# --- 退出码常量 ------------------------------------------------------------
EXIT_OK=0
EXIT_USAGE=1
EXIT_COMPILE=2
EXIT_RUNTIME_FATAL=3
EXIT_RUNTIME_ERROR=4
EXIT_TIMEOUT=5

SCRIPT_NAME="$(basename "$0")"
die() { printf '%s: ERROR: %s\n' "$SCRIPT_NAME" "$*" >&2; exit "$EXIT_USAGE"; }
info() { printf '[%s] %s\n' "$SCRIPT_NAME" "$*"; }
usage() { sed -n '/^# 用法：/,/^# 实现说明/p' "$0" | sed 's/^# \{0,1\}//'; }

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
TOP=""
WORKDIR="$PWD"
LOG=""
SIMV_NAME="simv"
TIMESCALE="1ns/1ps"
CM=""
CM_DIR=""
TIMEOUT=0
DO_RUN=1
VERBOSE=0

ARGV_FILES=()
ARGV_INCDIRS=()
ARGV_DEFINES=()
ARGV_PLUSARGS=()
ARGV_EXTRA_VCS=()
ARGV_EXTRA_SIM=()

while (( $# > 0 )); do
    case "$1" in
        -top)        [[ $# -ge 2 ]] || die "-top 需要参数"; TOP="$2"; shift 2 ;;
        -f)          [[ $# -ge 2 ]] || die "-f 需要参数"; ARGV_FILES+=("@filelist:$2"); shift 2 ;;
        -incdir)     [[ $# -ge 2 ]] || die "-incdir 需要参数"; ARGV_INCDIRS+=("$2"); shift 2 ;;
        -define)     [[ $# -ge 2 ]] || die "-define 需要参数"; ARGV_DEFINES+=("$2"); shift 2 ;;
        -o)          [[ $# -ge 2 ]] || die "-o 需要参数"; SIMV_NAME="$2"; shift 2 ;;
        -l)          [[ $# -ge 2 ]] || die "-l 需要参数"; LOG="$2"; shift 2 ;;
        -w)          [[ $# -ge 2 ]] || die "-w 需要参数"; WORKDIR="$2"; shift 2 ;;
        -timescale)  [[ $# -ge 2 ]] || die "-timescale 需要参数"; TIMESCALE="$2"; shift 2 ;;
        -cm)         [[ $# -ge 2 ]] || die "-cm 需要参数"; CM="$2"; shift 2 ;;
        -cm-dir)     [[ $# -ge 2 ]] || die "-cm-dir 需要参数"; CM_DIR="$2"; shift 2 ;;
        -timeout)    [[ $# -ge 2 ]] || die "-timeout 需要参数"; TIMEOUT="$2"; shift 2 ;;
        -vcs-arg)    [[ $# -ge 2 ]] || die "-vcs-arg 需要参数"; ARGV_EXTRA_VCS+=("$2"); shift 2 ;;
        -sim-arg)    [[ $# -ge 2 ]] || die "-sim-arg 需要参数"; ARGV_EXTRA_SIM+=("$2"); shift 2 ;;
        -no-run)     DO_RUN=0; shift ;;
        -v|--verbose) VERBOSE=1; shift ;;
        -h|--help)   usage; exit "$EXIT_OK" ;;
        +*)          ARGV_PLUSARGS+=("$1"); shift ;;
        -*)          die "未知选项 '$1'（用 -h 查看用法）" ;;
        *)           ARGV_FILES+=("$1"); shift ;;
    esac
done

[[ -n "$TOP" ]] || die "必须用 -top 指定顶层模块名"
command -v vcs >/dev/null 2>&1 || die "vcs 不在 PATH 上（VCS_HOME=$VCS_HOME）"

# ---------------------------------------------------------------------------
# 展开文件列表 + 归一化成绝对路径
# ---------------------------------------------------------------------------
FILES=()
if (( ${#ARGV_FILES[@]} > 0 )); then
    for item in "${ARGV_FILES[@]}"; do
        if [[ "$item" == @filelist:* ]]; then
            fl="${item#@filelist:}"
            [[ -r "$fl" ]] || die "文件列表 '$fl' 不存在或不可读"
            while IFS= read -r line || [[ -n "$line" ]]; do
                line="${line%%#*}"                          # 去注释
                line="${line//$'\r'/}"                      # 容忍 CRLF
                line="$(printf '%s' "$line" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
                [[ -z "$line" ]] && continue
                FILES+=("$line")
            done < "$fl"
        else
            FILES+=("$item")
        fi
    done
fi

(( ${#FILES[@]} > 0 )) || die "没有给定任何源文件（命令行文件参数或 -f 都没有）"

ABS_FILES=()
for f in "${FILES[@]}"; do
    [[ -r "$f" ]] || die "源文件不存在或不可读：$f"
    ABS_FILES+=("$(cd "$(dirname "$f")" && printf '%s/%s' "$PWD" "$(basename "$f")")")
done

# 工作目录
[[ -d "$WORKDIR" ]] || mkdir -p "$WORKDIR" || die "无法创建 -w 工作目录：$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"

# 日志（统一落到绝对路径，再 cd 之后仍可写）
if [[ -z "$LOG" ]]; then LOG="$WORKDIR/vcs.log"; fi
if [[ "$LOG" != /* ]]; then LOG="$PWD/$LOG"; fi
mkdir -p "$(dirname "$LOG")"
: > "$LOG"

COMPILE_LOG="$WORKDIR/compile.log"
RUN_LOG="$WORKDIR/run.log"
SIMV_STDOUT="$WORKDIR/simv.stdout"
if [[ -z "$CM_DIR" ]]; then CM_DIR="$WORKDIR/cov.vdb"; fi

# ---------------------------------------------------------------------------
# 拼装 vcs / simv 参数
# ---------------------------------------------------------------------------
VCS_ARGS=( -sverilog -full64 "-timescale=$TIMESCALE" -top "$TOP" -o "$SIMV_NAME" )
if (( ${#ARGV_INCDIRS[@]} > 0 )); then
    for d in "${ARGV_INCDIRS[@]}"; do VCS_ARGS+=("+incdir+$d"); done
fi
if (( ${#ARGV_DEFINES[@]} > 0 )); then
    for d in "${ARGV_DEFINES[@]}"; do VCS_ARGS+=("+define+$d"); done
fi
if (( ${#ARGV_PLUSARGS[@]} > 0 )); then
    for p in "${ARGV_PLUSARGS[@]}"; do VCS_ARGS+=("$p"); done
fi
if (( ${#ARGV_EXTRA_VCS[@]} > 0 )); then
    for a in "${ARGV_EXTRA_VCS[@]}"; do VCS_ARGS+=("$a"); done
fi
if [[ -n "$CM" ]]; then VCS_ARGS+=( -cm "$CM" -cm_dir "$CM_DIR" ); fi

SIM_ARGS=( -exitstatus )
if [[ -n "$CM" ]]; then SIM_ARGS+=( -cm "$CM" -cm_dir "$CM_DIR" ); fi
if (( ${#ARGV_EXTRA_SIM[@]} > 0 )); then
    for a in "${ARGV_EXTRA_SIM[@]}"; do SIM_ARGS+=("$a"); done
fi

# ---------------------------------------------------------------------------
# 日志头
# ---------------------------------------------------------------------------
{
    echo "==================================================================="
    echo " run_vcs.sh — VCS SystemVerilog 仿真"
    echo " 时间(VM)     : $(date '+%Y-%m-%d %H:%M:%S')"
    echo " 主机         : $(hostname)"
    echo " 工作目录     : $WORKDIR"
    echo " 顶层模块     : $TOP"
    echo " 可执行名     : $SIMV_NAME"
    echo " 时间精度     : $TIMESCALE"
    echo " 覆盖率       : ${CM:-<关闭>}"
    if [[ -n "$CM" ]]; then echo " 覆盖率库     : $CM_DIR"; fi
    echo " VCS_HOME     : $VCS_HOME"
    echo " SIMV 参数    : ${SIM_ARGS[*]}"
    echo " 源文件 (${#ABS_FILES[@]}):"
    printf '   %s\n' "${ABS_FILES[@]}"
    echo "==================================================================="
} >> "$LOG"

info "顶层=$TOP，源文件 ${#ABS_FILES[@]} 个，工作目录 $WORKDIR"
info "vcs ${VCS_ARGS[*]}"
info "完整日志：$LOG"

# ---------------------------------------------------------------------------
# 1) 编译
# ---------------------------------------------------------------------------
{
    echo ""
    echo "----- [1/2] COMPILE -----"
    echo "cmd: vcs ${VCS_ARGS[*]} \\"
    printf '         %s\n' "${ABS_FILES[@]}"
} >> "$LOG"

set +e
( cd "$WORKDIR" && vcs "${VCS_ARGS[@]}" "${ABS_FILES[@]}" ) > "$COMPILE_LOG" 2>&1
compile_rc=$?
set -e

cat "$COMPILE_LOG" >> "$LOG"

if (( compile_rc != 0 )); then
    {
        echo ""
        echo "----- RESULT -----"
        echo "verdict        : compile_failed"
        echo "exit_code      : $EXIT_COMPILE"
        echo "vcs_exit       : $compile_rc"
        echo "log            : $LOG"
        echo "==================================================================="
    } >> "$LOG"
    info "编译失败（vcs exit=$compile_rc）。日志尾部：" >&2
    tail -n 25 "$COMPILE_LOG" >&2
    info "完整日志：$LOG" >&2
    exit "$EXIT_COMPILE"
fi

if (( VERBOSE == 1 )); then cat "$COMPILE_LOG"; else tail -n 6 "$COMPILE_LOG"; fi
info "编译成功"

if (( DO_RUN == 0 )); then
    {
        echo ""
        echo "----- RESULT -----"
        echo "verdict        : compile_only_ok"
        echo "exit_code      : $EXIT_OK"
        echo "note           : 按 -no-run 未运行仿真"
        echo "==================================================================="
    } >> "$LOG"
    info "已按 -no-run 结束"
    exit "$EXIT_OK"
fi

# ---------------------------------------------------------------------------
# 2) 运行
# ---------------------------------------------------------------------------
SIMV_PATH="$WORKDIR/$SIMV_NAME"
[[ -x "$SIMV_PATH" ]] || die "编译声称成功，但可执行文件不存在：$SIMV_PATH"

{
    echo ""
    echo "----- [2/2] RUN -----"
    echo "cmd: $SIMV_PATH ${SIM_ARGS[*]} -l $RUN_LOG"
} >> "$LOG"

set +e
if (( TIMEOUT > 0 )); then
    ( cd "$WORKDIR" && timeout "$TIMEOUT" "$SIMV_PATH" "${SIM_ARGS[@]}" -l "$RUN_LOG" ) > "$SIMV_STDOUT" 2>&1
    run_rc=$?
else
    ( cd "$WORKDIR" && "$SIMV_PATH" "${SIM_ARGS[@]}" -l "$RUN_LOG" ) > "$SIMV_STDOUT" 2>&1
    run_rc=$?
fi
set -e

if [[ -s "$RUN_LOG" ]]; then cat "$RUN_LOG" >> "$LOG"; else cat "$SIMV_STDOUT" >> "$LOG"; fi

# --- 运行日志正则兜底统计 ---------------------------------------------------
# ⚠️ 这里是**次要防线**，主防线是 `-exitstatus`（裸 simv 对 $fatal 也返回 0，实测）。
# 已知残余限制（如实登记，不声称已验证）：
#   1. 这些正则是**按日志文本**判断的，覆盖不到自定义措辞的致命信息；
#   2. 兜底本身**没有独立的反例测试** —— 去掉 -exitstatus 的副本之所以仍能报错，
#      是因为日志里同时有 "Fatal:" 字样，不是兜底被单独验证过。
#   3. 要覆盖断言失败，靠的是上面新加的 `Assertion .* failed`；仍可能有变体漏网。
# 结论：判定以 `-exitstatus` 退出码为准，正则只用来把失败**分类**（fatal vs error）。
# 说明：`-exitstatus` 已能覆盖 $error/$fatal；这里再扫一遍是为了覆盖 UVM report
# 与 SVA 失败等来源，并让日志里出现计数，便于人工核对。
count_re() {
    local n
    n="$(grep -cE "$1" "$RUN_LOG" 2>/dev/null || true)"
    printf '%s' "${n:-0}"
}
# 允许前导空白：VCS 有时会给出行首缩进；早先的 `^` 锚定太紧。
# 断言失败也归入 fatal —— 独立复核（2026-09-18）指出这一类比 $fatal 更容易漏。
FATAL_CNT=$(count_re '^[[:space:]]*(Fatal:|Error-\[.*(FATAL|Fatal))|^[[:space:]]*\*\* Fatal|^[[:space:]]*UVM_FATAL @|Assertion .* failed')
ERROR_CNT=$(count_re '^[[:space:]]*(Error:|Error-\[)|^[[:space:]]*\*\* Error|^[[:space:]]*UVM_ERROR @')

{
    echo ""
    echo "----- 运行日志错误统计（正则扫描 run.log）-----"
    echo "run.log        : $RUN_LOG"
    echo "fatal 匹配行数 : $FATAL_CNT"
    echo "error 匹配行数 : $ERROR_CNT"
    echo "simv 退出码    : $run_rc"
} >> "$LOG"

VERDICT="ok"
EXIT_CODE=$EXIT_OK
if (( run_rc == 124 )); then
    VERDICT="timeout";       EXIT_CODE=$EXIT_TIMEOUT
elif (( FATAL_CNT > 0 )) || (( run_rc == 3 )); then
    VERDICT="fatal";         EXIT_CODE=$EXIT_RUNTIME_FATAL
elif (( ERROR_CNT > 0 )) || (( run_rc == 2 )); then
    VERDICT="error";         EXIT_CODE=$EXIT_RUNTIME_ERROR
elif (( run_rc != 0 )); then
    # simv 非零但与 -exitstatus 约定不符：保守判为运行期致命错误
    VERDICT="fatal";         EXIT_CODE=$EXIT_RUNTIME_FATAL
fi

{
    echo ""
    echo "----- RESULT -----"
    echo "verdict        : $VERDICT"
    echo "exit_code      : $EXIT_CODE"
    echo "fatal_lines    : $FATAL_CNT"
    echo "error_lines    : $ERROR_CNT"
    echo "simv_exit      : $run_rc"
    echo "log            : $LOG"
    if [[ -n "$CM" ]]; then echo "cov_db         : $CM_DIR"; fi
    echo "==================================================================="
} >> "$LOG"

if (( VERBOSE == 1 )); then cat "$RUN_LOG"; else tail -n 12 "$RUN_LOG"; fi

case "$VERDICT" in
    ok)      info "仿真通过（无 error/fatal）" ;;
    timeout) info "仿真超时（> ${TIMEOUT}s）" ;;
    error)   info "仿真有 \$error（非致命），命中 $ERROR_CNT 行" ;;
    fatal)   info "仿真有 \$fatal / 非零退出，命中 $FATAL_CNT 行" ;;
esac
info "verdict=$VERDICT exit=$EXIT_CODE  日志：$LOG"
exit "$EXIT_CODE"
