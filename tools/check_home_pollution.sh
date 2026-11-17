#!/usr/bin/env bash
# 远端 $HOME 污染自查。
#
# 为什么需要：EDA 工具默认把中间产物写到 CWD，而 ssh 远程执行的 CWD
# 就是 $HOME。已发生三次事故：
#   .ahdlSimDB × 55（Virtuoso AHDL）
#   d3d4* × 21（VCS）
#   *.svf × 44 + *.pvl/*.syn × 29（DC/Formality）
#
# 用法: bash tools/check_home_pollution.sh [host]
# 退出码: 0 = 干净, 1 = 有本次任务造成的污染
set -uo pipefail
HOST=${1:-vm-yian}
bad=0

# 明确的「非本任务」保留项——它们不算污染，不报。
# 依据：时间戳早于本任务，或属仓主既有整理结构。
KEEP_RE='verdiLog|fc_command\.log|fc_output\.txt|parasitics_command\.log|pt_shell_command\.log'

show() {
  local desc=$1 pat=$2
  local out n
  out=$(ssh "$HOST" "ls -d $pat 2>/dev/null" 2>/dev/null || true)
  # 过滤掉保留项
  out=$(printf '%s\n' "$out" | grep -Ev "^$" | grep -Ev "$KEEP_RE" || true)
  n=$(printf '%s\n' "$out" | grep -c . || true)
  if [ "${n:-0}" -eq 0 ]; then
    printf '  OK   %-34s 0\n' "$desc"
  else
    printf '  BAD  %-34s %s\n' "$desc" "$n"
    printf '%s\n' "$out" | head -5 | sed 's/^/         /'
    bad=1
  fi
}

echo "=== \$HOME 污染自查 ($HOST) ==="
show 'Virtuoso AHDL (.ahdlSimDB)'  '$HOME/*.ahdlSimDB'
show 'DC 综合 (.pvl/.syn/.mr)'     '$HOME/*.pvl $HOME/*.syn $HOME/*.mr'
show 'VCS 残留 (csrc/simv*)'       '$HOME/csrc $HOME/simv* $HOME/*.daidir'
show 'VCS/Voltus 残留'             '$HOME/vcst_rtdb.bak $HOME/vst_rtdb.bak'
show '本次任务日志'                '$HOME/fm_shell_command.log $HOME/lc_command.log $HOME/vcf.log'
show '临时实验目录'                '$HOME/d3d4* $HOME/fc_[a-z]* $HOME/v21w* $HOME/ip'

echo
if [ "$bad" -eq 0 ]; then
  echo "结果: 干净"
else
  echo "结果: **有污染**"
  echo "清理前请照 TOOL_WORKDIR_RULES.md §4：列清单 → 按时间戳区分 → 查进程占用"
fi
exit "$bad"
