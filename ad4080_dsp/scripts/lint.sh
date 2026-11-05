#!/usr/bin/env bash
# =============================================================================
# lint.sh -- 显式文件集合的语法与文本检查
# -----------------------------------------------------------------------------
# 依次执行：
#   1. sv_lint.py    : pyslang 语法/elaboration + 文本风格规则
#   2. check_headers : 文件头字段完整性（sv_lint 也查，这里给出独立报告）
#   3. verible       : 若本机可执行则加跑（Linux CI 推荐；macOS 官方包有误）
#
# 用法：bash scripts/lint.sh path/to/module.sv [dependency.sv ...]
# STRICT=1 将 pyslang 警告也当失败；不隐式扫描 RTL 或提供外部参数包。
# 完整核的仿真/展开应使用 tools/run_open_rtl.py 的显式组件与输入包。
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$#" -eq 0 ]]; then
  printf '%s\n' 'usage: lint.sh <file.sv> [dependency.sv ...]; no default suite' >&2
  exit 2
fi
PY="${PYTHON:-python3}"

echo "== [1/3] pyslang + style =="
if [[ "${STRICT:-0}" == "1" ]]; then
  "$PY" "$ROOT/scripts/sv_lint.py" --strict "$@"
else
  "$PY" "$ROOT/scripts/sv_lint.py" "$@"
fi

echo
echo "== [2/3] file headers =="
"$PY" "$ROOT/scripts/check_headers.py" "$@"

echo
echo "== [3/3] verible (optional) =="
VB="${VERIBLE_BIN:-}"
if [[ -z "$VB" ]]; then
  VB="$(command -v verible-verilog-lint || true)"
fi
if [[ -n "$VB" && -x "$VB" ]]; then
  "$VB" "$@"
  echo "verible: PASS"
else
  echo "verible: NOT_RUN (未找到可执行文件)"
fi

echo
echo "SELECTED SYNTAX/TEXT CHECKS PASSED; simulation/formal/CDC/STA not assessed"
