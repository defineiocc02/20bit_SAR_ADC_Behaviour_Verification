#!/usr/bin/env bash
#=============================================================================
# synth/run_synth.sh -- Design Compiler 综合 wrapper（VM 侧执行）
#=============================================================================
# 作用：把参数整理好 -> 调 dc_shell 跑 run_dc.tcl -> 把 dc_shell 的退出状态
#       和 run_dc.tcl 写的 status.txt 合起来，给出一个**含义明确**的退出码。
#
# 环境要求（脚本自己会 source PDK 的 env.sh，一般不用手动设）：
#   * dc_shell：/opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell
#     （可用 DC_SHELL 覆盖）
#   * License：systemd 的 snpslmd.service，端口 27080，即
#       LM_LICENSE_FILE=27080@localhost
#     本脚本在变量为空时自动补上。
#   * PDK：/home/yian/PDK/tsmc28hpcplus/config/env.sh
#     （可用 TSMC_ENV 覆盖；source 后会定义 TSMC28HPCPLUS_CCS_RVT 等）
#
# 用法：
#   ./run_synth.sh --top top_smoke --files "a.sv b.sv" --incdirs "/path/inc" \
#                  --clk-period 10 --out-dir ./out [--corner tt0p9v25c]
#
# 参数（也可以全部走同名大写环境变量）：
#   --top         TOP          顶层模块名                    [必需]
#   --files       FILES        空格分隔源文件列表            [必需]
#   --incdirs     INCDIRS      空格分隔 `include 目录
#   --defines     DEFINES      空格分隔 +define+ 宏
#   --params      PARAMS       顶层模块的 HDL 参数覆盖，如 "P_STAGES=4"
#                              （多个参数用空格分隔；空=用 RTL 默认值）
#   --compile-mode COMPILE_MODE ultra（默认）| compile（非 ultra，快但结果差）
#   --no-design-rule           关掉 compile 的设计规则修复（等价 DESIGN_RULE_FIX=0）
#                              只对 --compile-mode compile 有效，配错直接报参数错
#   --check-only               只做参数一致性检查 + check_design 就退出（不起 compile）
#   --clk-name    CLK_NAME     时钟端口名（默认 clk）
#   --clk-period  CLK_PERIOD   时钟周期 ns                   [必需]
#   --out-dir     OUT_DIR      报告输出目录                  [必需]
#   --corner      CORNER       工艺角（默认 tt0p9v25c）
#   --lib-db      LIB_DB       直接指定 .db（覆盖 --corner）
#   --max-trans   MAX_TRANS    覆盖库的 default_max_transition
#   --drive-cell  DRIVE_CELL   输入驱动单元（默认 BUFFD2BWP7T40P140）
#   --load        LOAD_PF      输出负载 pF（默认 0.02）
#   --in-delay    IN_DELAY     输入延迟 ns（默认 0.0）
#   --out-delay   OUT_DELAY    输出延迟 ns（默认 0.0）
#   --preflight                 只做库/license 体检（实测约 50 s），不综合；此时
#                               --top/--files/--clk-period 都可以不给
#
# 退出码：
#   0   综合完成，且 WNS >= 0（时序收敛）
#   1   工具/流程失败：库读不进、analyze/elaborate/link/compile 报错、
#       license 拿不到、dc_shell 非零退出、或事后取不到任何 timing path
#   2   参数错误：缺 --top/--files/--clk-period/--out-dir，或数值非法
#   3   综合**正常完成**，但时序未收敛（WNS < 0）—— 这是"有意义的结果"，
#       不是流程故障，报告仍然完整可用
#   4   综合结束但拿不到 status.txt / 状态字段不可解析（异常，需人工看 dc.log）
#   5   RTL 参数与冻结契约不一致（`DC_STATUS=PARAMS_MISMATCH`）——**有意拒绝综合**，
#       不是流程故障：说明有人改了 rtl_params.vh 或绕过了参数检查
#
# 产出：$OUT_DIR 下 area.rpt timing.rpt power.rpt qor.rpt
#       check_design.rpt status.txt dc.log elaborate.log
#       phase.log（无缓冲阶段日志，用来看进度）
#       run_dc.snapshot.tcl（本次实际执行的 run_dc.tcl 快照，可追溯）
#=============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DC_SHELL="${DC_SHELL:-/opt/synopsys/syn/W-2024.09-SP3/bin/dc_shell}"
TSMC_ENV="${TSMC_ENV:-/home/yian/PDK/tsmc28hpcplus/config/env.sh}"

# ---- 参数解析：命令行优先，环境变量兜底 ------------------------------------
usage() {
    # 打印文件头部的注释块（从第 2 行起，直到第一个非注释行）。刻意不用
    # `sed -n '2,51p'` 这种**写死行号**的写法 —— 头部一加注释就会漂，
    # 表现为 usage 少打一行、或多打一行 `set -euo pipefail`。
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --top)        TOP="$2";        shift 2 ;;
        --files)      FILES="$2";      shift 2 ;;
        --incdirs)    INCDIRS="$2";    shift 2 ;;
        --defines)    DEFINES="$2";    shift 2 ;;
        --params)     PARAMS="$2";     shift 2 ;;
        --compile-mode) COMPILE_MODE="$2"; shift 2 ;;
        --no-design-rule) NO_DESIGN_RULE=1; shift 1 ;;
        --check-only) CHECK_ONLY=1;    shift 1 ;;
        --clk-name)   CLK_NAME="$2";   shift 2 ;;
        --clk-period) CLK_PERIOD="$2"; shift 2 ;;
        --out-dir)    OUT_DIR="$2";    shift 2 ;;
        --corner)     CORNER="$2";     shift 2 ;;
        --lib-db)     LIB_DB="$2";     shift 2 ;;
        --max-trans)  MAX_TRANS="$2";  shift 2 ;;
        --drive-cell) DRIVE_CELL="$2"; shift 2 ;;
        --load)       LOAD_PF="$2";    shift 2 ;;
        --in-delay)   IN_DELAY="$2";   shift 2 ;;
        --out-delay)  OUT_DELAY="$2";  shift 2 ;;
        --preflight)  PREFLIGHT=1;     shift 1 ;;
        -h|--help)    usage 0 ;;
        *) echo "run_synth.sh: unknown argument '$1'" >&2; usage 2 ;;
    esac
done

PREFLIGHT="${PREFLIGHT:-0}"

TOP="${TOP:-}"; FILES="${FILES:-}"; CLK_PERIOD="${CLK_PERIOD:-}"; OUT_DIR="${OUT_DIR:-}"
CLK_NAME="${CLK_NAME:-clk}"
INCDIRS="${INCDIRS:-}"; DEFINES="${DEFINES:-}"
PARAMS="${PARAMS:-}"
COMPILE_MODE="${COMPILE_MODE:-ultra}"
NO_DESIGN_RULE="${NO_DESIGN_RULE:-0}"
CHECK_ONLY="${CHECK_ONLY:-0}"
CORNER="${CORNER:-tt0p9v25c}"
LIB_DB="${LIB_DB:-}"; MAX_TRANS="${MAX_TRANS:-}"
DRIVE_CELL="${DRIVE_CELL:-BUFFD2BWP7T40P140}"
LOAD_PF="${LOAD_PF:-0.02}"
IN_DELAY="${IN_DELAY:-0.0}"; OUT_DELAY="${OUT_DELAY:-0.0}"

# ---- 参数校验（对应退出码 2）----------------------------------------------
REQ="TOP FILES CLK_PERIOD OUT_DIR"
[[ "$PREFLIGHT" == "1" ]] && REQ="OUT_DIR"
for k in $REQ; do
    if [[ -z "${!k:-}" ]]; then
        echo "run_synth.sh: missing required parameter $k" >&2; usage 2
    fi
done
if [[ "$PREFLIGHT" != "1" ]] && ! awk -v p="$CLK_PERIOD" 'BEGIN{exit !(p+0>0)}'; then
    echo "run_synth.sh: --clk-period must be a positive number, got '$CLK_PERIOD'" >&2
    exit 2
fi
# 参数互锁：`-no_design_rule` 只有 compile（非 ultra）才有。放在**参数校验区**
# 而不是后面的 export 处 —— 这样它是纯参数检查，本地（没有 dc_shell 的机器）也能自测，
# 不会被 "dc_shell not executable" 提前挡掉而变成一段测不到的代码。
if [[ "${NO_DESIGN_RULE:-0}" == "1" && "$COMPILE_MODE" != "compile" ]]; then
    echo "run_synth.sh: --no-design-rule requires --compile-mode compile (got '$COMPILE_MODE')" >&2
    exit 2
fi
# preflight 不给 --out-dir 时，落到 tmp（仍然只在自己的会话内活动）
if [[ -z "$OUT_DIR" ]]; then OUT_DIR="/tmp/dc_preflight_$$"; fi
if [[ ! -x "$DC_SHELL" ]]; then
    echo "run_synth.sh: dc_shell not executable: $DC_SHELL" >&2
    exit 1
fi

# ---- 环境 ----------------------------------------------------------------
# PDK 的 env.sh 定义了 TSMC28HPCPLUS_CCS_RVT / SIGNOFF_DB_CORNERS 等；
# source 它 = 用 PDK 自己声明的库路径，而不是我们自己写死的字符串。
if [[ -f "$TSMC_ENV" ]]; then
    # shellcheck disable=SC1090
    source "$TSMC_ENV"
else
    echo "run_synth.sh: WARNING: PDK env not found at $TSMC_ENV, falling back to built-in paths" >&2
fi
# license：SSH 会话里通常已设好；没有的话补默认值
export LM_LICENSE_FILE="${LM_LICENSE_FILE:-27080@localhost}"

export TOP FILES OUT_DIR CLK_PERIOD CLK_NAME INCDIRS DEFINES CORNER
# PARAMS 只在非空时导出：空串导出会让 run_dc.tcl 认为"给了参数"。
[[ -n "$PARAMS" ]] && export PARAMS
export COMPILE_MODE
# 互锁已在参数校验区做过，这里只管导出
[[ "${NO_DESIGN_RULE:-0}" == "1" ]] && export DESIGN_RULE_FIX=0
[[ "$CHECK_ONLY" == "1" ]] && export DC_CHECK_ONLY=1
export DRIVE_CELL LOAD_PF IN_DELAY OUT_DELAY
[[ -n "$LIB_DB" ]]    && export LIB_DB
[[ -n "$MAX_TRANS" ]] && export MAX_TRANS
[[ "$PREFLIGHT" == "1" ]] && export DC_PREFLIGHT=1

mkdir -p "$OUT_DIR"
DC_LOG="$OUT_DIR/dc.log"
rm -f "$OUT_DIR/status.txt"

# ---- 给 run_dc.tcl 拍个快照 ------------------------------------------------
# 为什么必须这么做（真事故）：bash 是**按字节偏移增量读**脚本的，dc_shell 启动时
# 也把 -f 的 tcl 整份读进来。如果在一次综合还在跑的时候去覆盖这两个文件
# （例如 rsync/scp 同步仓库的新版本），正在跑的那一次会读到"被撕开"的内容。
# 实测后果：20 ns 那一次明明综合成功、status.txt 数字都对，wrapper 却返回了
# **假的 rc=2**（读到了 usage 分支的残片）。这种故障最难查，因为它看起来像
# 参数错误。
# 对策：把 tcl 拷到 OUT_DIR 再执行 —— 之后谁去改仓库里的 run_dc.tcl 都影响不到
# 正在跑的这一次；同时这份快照也是"这份报告到底是哪版脚本产出的"的证据。
DC_SCRIPT_SNAPSHOT="$OUT_DIR/run_dc.snapshot.tcl"
cp "$SCRIPT_DIR/run_dc.tcl" "$DC_SCRIPT_SNAPSHOT"

# ---- 跑 dc_shell ---------------------------------------------------------
# dc_shell 自己可能有 60s 级别的启动开销；超时上限给足（RAM 只有 22G，
# 但 smoke/子模块规模很小）。DC_TIMEOUT 可覆盖。
DC_TIMEOUT="${DC_TIMEOUT:-3600}"

set +e
timeout "$DC_TIMEOUT" "$DC_SHELL" -no_gui -f "$DC_SCRIPT_SNAPSHOT" > "$DC_LOG" 2>&1
dc_rc=$?
set -e

echo "run_synth.sh: dc_shell exit code = $dc_rc  (log: $DC_LOG)"

# ---- 判定退出码 -----------------------------------------------------------
# dc_shell 的退出码不完全可靠（某些内部错误也返回 0），所以以 run_dc.tcl
# 写的 status.txt 为准；两者取"更严重"的那个。
dc_status=""; wns=""
if [[ -f "$OUT_DIR/status.txt" ]]; then
    dc_status="$(awk -F= '/^DC_STATUS=/{print $2}'  "$OUT_DIR/status.txt" | head -1)"
    wns="$(awk -F= '/^WNS=/{print $2}'             "$OUT_DIR/status.txt" | head -1)"
fi

if [[ "$dc_rc" -eq 124 ]]; then
    echo "run_synth.sh: TIMEOUT after ${DC_TIMEOUT}s" >&2
    exit 1
fi

# A success marker cannot overrule a process error, crash or killed process.
if [[ "$dc_rc" -ne 0 && ( "$dc_status" == "OK" || "$dc_status" == "PREFLIGHT_OK" || "$dc_status" == "CHECK_ONLY_OK" ) ]]; then
    echo "run_synth.sh: FLOW FAILED: success marker conflicts with dc_shell rc=$dc_rc" >&2
    exit 1
fi

case "$dc_status" in
    PREFLIGHT_OK)
        echo "run_synth.sh: PREFLIGHT OK"
        grep -E '^DC_PREFLIGHT_OK' "$DC_LOG" || true
        exit 0
        ;;
    OK)
        # 再独立复核一次 WNS，避免 tcl 侧判定被绕过
        if [[ ! "$wns" =~ ^[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)([eE][+-]?[0-9]+)?$ ]]; then
            echo "run_synth.sh: FLOW FAILED: invalid WNS='$wns' despite status=OK" >&2
            exit 1
        fi
        if awk -v value="$wns" 'BEGIN { exit !(value + 0 < 0) }'; then
            echo "run_synth.sh: TIMING NOT MET (WNS = $wns ns although status=OK)" >&2
            exit 3
        fi
        echo "run_synth.sh: OK (WNS = $wns ns)"
        exit 0
        ;;
    CHECK_ONLY_OK)
        echo "run_synth.sh: CHECK ONLY OK (params + check_design passed; no compile)"
        grep -E '^DC_CHECK_ONLY_OK|^DC_PARAM_OK' "$DC_LOG" || true
        exit 0
        ;;
    PARAMS_MISMATCH)
        echo "run_synth.sh: RTL PARAMS MISMATCH -- 有意拒绝综合；不一致明细见 $DC_LOG" >&2
        grep -E '^DC_PARAM_(MISMATCH|FAIL)' "$DC_LOG" || true
        exit 5
        ;;
    TIMING_NOT_MET)
        echo "run_synth.sh: TIMING NOT MET (WNS = $wns ns)" >&2
        exit 3
        ;;
    FAILED)
        echo "run_synth.sh: FLOW FAILED -- see $DC_LOG" >&2
        exit 1
        ;;
    NO_TIMING)
        echo "run_synth.sh: FLOW FAILED (no timing paths extractable) -- see $DC_LOG" >&2
        exit 1
        ;;
    "")
        echo "run_synth.sh: no status.txt produced (dc_shell rc=$dc_rc) -- see $DC_LOG" >&2
        if [[ "$dc_rc" -ne 0 ]]; then exit 1; else exit 4; fi
        ;;
    *)
        echo "run_synth.sh: unrecognized DC_STATUS='$dc_status' -- see $DC_LOG" >&2
        exit 4
        ;;
esac
