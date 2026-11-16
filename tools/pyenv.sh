#!/usr/bin/env bash
# =============================================================================
# 文件名     : pyenv.sh
# 版本       : v1.0
# 功能       : 为行为级 Python 模型准备一个可用的解释器与运行环境。
#
# 设计说明   :
#   * 为什么需要这个脚本：本项目 67 个 tools/*.py 里有 36 个 `import adi_model`，
#     它们要求：
#       ① Python >= 3.10   —— 因为用了 `typing.TypeGuard`
#       ② PYTHONPATH 含 src/ —— 否则 ModuleNotFoundError: adi_model
#       ③ numpy / scipy / matplotlib
#     本机默认 `python3` 是 3.9 且缺 scipy，直接跑会失败且报错信息不直观。
#
#   * 实测过的两个坑：
#       · python3.12 直接 pip install 会被 PEP 668 拦住（externally-managed）
#         -> 用 venv 绕过
#       · tools/run_review_figures.py 不传 --out 会**静默无输出**（不报错）
#
#   * 本脚本只负责"准备解释器"，不改变仓库状态；venv 建在 /tmp 下，
#     删掉即可回滚。
#
# 用法:
#   source tools/pyenv.sh          # 交互式：激活后用 $PY 跑脚本
#   bash tools/pyenv.sh --check    # CI：只检查环境是否可用，不安装
#   bash tools/pyenv.sh --run tools/run_review_figures.py --out /tmp/fig
# 变更记录   :
#   2026-11-16  初版。
# =============================================================================
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VENV=${PYENV_VENV:-/tmp/venv_adi}
REQ="$ROOT/requirements.txt"

# 找一个 >= 3.10 的解释器
find_py() {
    for c in python3.13 python3.12 python3.11 python3.10; do
        if command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi
    done
    # 兜底：检查 python3 版本
    if command -v python3 >/dev/null 2>&1; then
        v=$(python3 -c 'import sys; print("%d%d" % sys.version_info[:2])' 2>/dev/null)
        if [ "${v:-0}" -ge 310 ] 2>/dev/null; then echo python3; return 0; fi
    fi
    return 1
}

BASE=$(find_py) || {
    echo "FAIL: 找不到 Python >= 3.10（项目要求见 pyproject requires-python）" >&2
    echo "      本机 python3 版本: $(python3 --version 2>&1)" >&2
    exit 1
}

prepare() {
    if [ ! -x "$VENV/bin/python" ]; then
        echo "== 创建 venv（${BASE} -> ${VENV}）" >&2
        "$BASE" -m venv "$VENV" || return 1
        echo "== 安装依赖" >&2
        "$VENV/bin/pip" install -q -r "$REQ" || return 1
    fi
    export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
    export MPLBACKEND=Agg
    export PY="$VENV/bin/python"
    return 0
}

check() {
    [ -x "$VENV/bin/python" ] || { echo "FAIL: venv 不存在，先跑 bash tools/pyenv.sh" >&2; return 1; }
    PYTHONPATH="$ROOT/src" "$VENV/bin/python" - <<'PY'
import sys
assert sys.version_info >= (3, 10), f"需要 >=3.10，当前 {sys.version.split()[0]}"
import numpy, scipy, matplotlib
import adi_model
print(f"OK  python {sys.version.split()[0]}  numpy {numpy.__version__}  "
      f"scipy {scipy.__version__}  matplotlib {matplotlib.__version__}")
PY
}

case "${1:-}" in
    --check)
        check || exit 1
        ;;
    --run)
        shift
        prepare || exit 1
        cd "$ROOT" && "$PY" "$@"
        ;;
    *)
        prepare || exit 1
        echo "已准备：PY=${PY}" >&2
        echo "用法示例：PYTHONPATH=${ROOT}/src \${PY} tools/run_review_figures.py --out /tmp/fig" >&2
        ;;
esac
