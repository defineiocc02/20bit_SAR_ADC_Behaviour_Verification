#!/usr/bin/env bash
#=============================================================================
# synth/check_syntax.sh -- run_dc.tcl 的语法门禁（几秒钟，不起 DC）
#=============================================================================
# 为什么需要它：dc_shell 启动 ≈16 s，一次完整综合 ≈12 min。如果 run_dc.tcl
# 里有个拼错的括号，你要等 12 分钟才知道 —— 更糟的是 §7 坑 1 说的
# "Tcl 报错但退出码仍是 0"，会让你以为跑成功了。
#
# 做法：用任何一个独立 tclsh 把 run_dc.tcl **源进来**。DC 命令全部由
# 桩函数顶掉，所以只做解析与 proc 定义；proc body 里的语法错误会在
# 定义 proc 的那一刻暴露。
#
# 前提：必须有独立 tclsh。本 VM 上 tclsh 不在 PATH 里，Calibre 自带一个，
#       默认用它；也可用 TCLSH 环境变量覆盖。
#
# 用法：
#   ./check_syntax.sh [run_dc.tcl 的路径]
# 退出码：0 = 解析通过；1 = 有语法错误（原文打到 stderr）
#=============================================================================
set -euo pipefail

TARGET="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_dc.tcl}"
TCLSH="${TCLSH:-/opt/mentor/calibre2025/aok_cal_2025.1_16.10/bin/tclsh}"

if [[ ! -f "$TARGET" ]]; then echo "check_syntax.sh: no such file: $TARGET" >&2; exit 1; fi
if [[ ! -x "$TCLSH" ]]; then
    echo "check_syntax.sh: tclsh not found at '$TCLSH'; set TCLSH=<path to tclsh>" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 桩：把 DC 的顶层命令顶掉，让脚本能一路走到 proc 定义完成。
cat > "$TMP/gate.tcl" <<T
proc set_app_var {args} { return }
# dc_main 一定会在"缺参数"上 exit，那是预期的；我们只要"没有语法错误"。
source {$TARGET}
T

out="$("$TCLSH" "$TMP/gate.tcl" 2>&1 || true)"
if printf '%s\n' "$out" | grep -qiE 'syntax error|missing |extra characters|unmatched|invalid command name "set_app_var"'; then
    echo "check_syntax.sh: FAIL -- run_dc.tcl 有语法错误：" >&2
    printf '%s\n' "$out" >&2
    exit 1
fi
echo "check_syntax.sh: OK -- $TARGET 解析通过"
exit 0
