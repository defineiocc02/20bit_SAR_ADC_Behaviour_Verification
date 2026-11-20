#!/usr/bin/env bash
# =============================================================================
# 文件名     : clean_remote.sh
# 功能       : 通过 SSH 在明确指定的 Git 检出中复用本机清理规则，默认预览。
# 设计说明   : 发送当前 clean.py 的字节，远端另一个分支的旧脚本不会改变候选。
#              目标工单以外的临时目录不在此工具的扫描范围内。
# =============================================================================
set -euo pipefail
usage() {
    echo 'Usage: REMOTE_HOST=host bash flow/scripts/clean_remote.sh --root /absolute/checkout [--apply]'
}
root=''
mode=()
while (( $# )); do
    case "$1" in
        --root) [[ $# -ge 2 && -z $root ]] || { usage >&2; exit 2; }; root=$2; shift 2;;
        --apply) mode=(--apply); shift;;
        -h|--help) usage; exit 0;;
        *) usage >&2; exit 2;;
    esac
done
if [[ -z ${REMOTE_HOST:-} || $root != /* ]]; then usage >&2; exit 2; fi
# SSH 的远端命令由登录 shell 解释。按 POSIX 单个参数编码，保留空格和引号。
args=(python3 - --root "$root")
if [[ ${mode[0]:-} == --apply ]]; then args+=(--apply); fi
remote_cmd=$(python3 -c 'import shlex, sys; print(" ".join(map(shlex.quote, sys.argv[1:])))' "${args[@]}")
script_dir=$(cd "$(dirname "$0")" && pwd -P)
exec ssh -o BatchMode=yes -o ConnectTimeout=10 -- "$REMOTE_HOST" "$remote_cmd" < "$script_dir/clean.py"
