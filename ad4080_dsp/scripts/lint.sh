#!/usr/bin/env bash
# =============================================================================
# lint.sh -- AD4080 RTL 交付前门禁
# -----------------------------------------------------------------------------
# 依次执行：
#   1. sv_lint.py    : pyslang 语法/elaboration + 文本风格规则
#   2. check_headers : 文件头字段完整性（sv_lint 也查，这里给出独立报告）
#   3. verible       : 若本机可执行则加跑（Linux CI 推荐；macOS 官方包有误）
#
# 用法：
#   bash scripts/lint.sh              # 常规门禁
#   STRICT=1 bash scripts/lint.sh     # 把 pyslang 警告也当失败
#   VERIBLE_BIN=/path/bin bash scripts/lint.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 优先用带 pyslang 的隔离解释器；找不到再退回系统 python3。
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$HOME/.workbuddy/binaries/python/envs/default/bin/python" ]]; then
    PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
  else
    PY="python3"
  fi
fi
STRICT_FLAG=""
[[ "${STRICT:-0}" == "1" ]] && STRICT_FLAG="--strict"

echo "== [1/3] pyslang + style =="
"$PY" "$ROOT/scripts/sv_lint.py" $STRICT_FLAG "$ROOT"/rtl/*.sv

echo
echo "== [2/3] file headers =="
"$PY" "$ROOT/scripts/check_headers.py" "$ROOT"/rtl/*.sv

echo
echo "== [3/3] verible (optional) =="
VB="${VERIBLE_BIN:-}"
if [[ -z "$VB" ]]; then
  VB="$(command -v verible-verilog-lint || true)"
fi
if [[ -n "$VB" && -x "$VB" ]]; then
  "$VB" --rules_config="$ROOT/.verible.rules" "$ROOT"/rtl/*.sv
  echo "verible: PASS"
else
  echo "verible: SKIP (未找到可执行文件)"
  echo "  说明：官方 macOS 资产实际是 Linux ELF，本机无法运行；"
  echo "        Linux CI 上请安装 verible 后重跑本脚本。"
fi

echo
echo "ALL GATES PASSED"
