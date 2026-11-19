#!/usr/bin/env bash
# =============================================================================
# 文件名     : check_formal.sh
# 功能       : 将当前生产清单暂存到独占目录，核对 DC 收据后运行 Formality。
# 设计说明   : V21_DC_DIR 指向执行端的 DC 输出。远端用 V21_RUNNER 指定 SSH
#              别名；原 D3D4_RUNNER 和 D3D4_WORKDIR 调用仍可选主机和工作路径。
#              退出 0 表示本次工具明确报告成功，1 表示失败，2 表示未取得结论。
# =============================================================================
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
RUNNER=${V21_RUNNER:-${D3D4_RUNNER:-}}
RUNNER=${RUNNER#ssh }
REQUESTED=${V21_WORKDIR:-${D3D4_WORKDIR:-}}
DC_DIR=${V21_DC_DIR:-}
TMO=${1:-2700}
if [[ $# -gt 1 || ! $TMO =~ ^[1-9][0-9]*$ ]]; then
    printf 'Usage: V21_DC_DIR=/path/to/dc bash tools/check_formal.sh [timeout_seconds]\n' >&2
    exit 2
fi

# 1. 参数以独立 argv 传给执行端 Bash。远端不拼接未经转义的主机路径，
# tar 以标准输入传整份 filelist 及它列出的文件，本地和 SSH 使用同一暂存过程。
on_host() {
    if [[ -z $RUNNER ]]; then
        bash -c "$1" v21-formal "${@:2}"
    else
        local command
        printf -v command '%q ' bash -c "$1" v21-formal "${@:2}"
        ssh -- "$RUNNER" "$command"
    fi
}
# 默认从执行端启动目录选择 dc/。显式指定的绝对路径可用于读取已完成的另一运行。
# 预检先按退出码判断文件存在；错误消息即使含有“.svf”也不表示文件可读。
WORK=$(on_host 'set -eu
    dc=${1:-$PWD/dc}
    [[ $dc == /* ]] || { echo "V21_DC_DIR must be absolute" >&2; exit 2; }
    for f in adc_digital_ip_v21_syn.v adc_digital_ip_v21.svf v21-inputs.sha256; do
        [[ -s $dc/$f ]] || { echo "missing DC artifact: $dc/$f" >&2; exit 1; }
    done
    umask 077
    if [[ -n $2 ]]; then
        [[ $2 == /* ]] || { echo "V21_WORKDIR must be absolute" >&2; exit 2; }
        mkdir -- "$2" && (cd "$2" && pwd -P)
    else
        mktemp -d "${TMPDIR:-/tmp}/v21-fm.XXXXXXXX"
    fi' "$DC_DIR" "$REQUESTED")
printf 'Formality work directory: %s\n' "$WORK"
manifest=flow/build/filelists/files_adc_v21.f
helper=flow/synth/v21/design_inputs.tcl
inputs=$(tclsh "$ROOT/$helper" --list)
files=("$manifest" "$helper" flow/synth/v21/fm_equiv.tcl)
while IFS= read -r name; do files+=("$name"); done <<< "$inputs"
tar -C "$ROOT" -cf - "${files[@]}" | on_host 'tar -C "$1" -xf -' "$WORK"

# 2. 在新目录运行；PDK 和许可证可沿用执行端已有环境。收据在真正读 RTL
# 之前由 fm_equiv.tcl 逐项比较，错网表、错 SVF 和同名旧 RTL 都不能出通过结论。
rc=0
on_host 'set -eu
    export V21_DC_DIR=${2:-$PWD/dc}
    if [[ -n $4 ]]; then export V21_PDK_ROOT="$4"; fi
    cd "$1"
    command -v fm_shell >/dev/null || export PATH="/opt/synopsys/fm/W-2024.09-SP3/bin:$PATH"
    export SNPSLMD_LICENSE_FILE=${SNPSLMD_LICENSE_FILE:-27080@localhost}
    export LM_LICENSE_FILE=${LM_LICENSE_FILE:-$SNPSLMD_LICENSE_FILE}
    timeout "$3" fm_shell -f flow/synth/v21/fm_equiv.tcl > fm.log 2>&1' "$WORK" "$DC_DIR" "$TMO" "${V21_PDK_ROOT:-}" || rc=$?
# 超时或 SSH 错误均保留日志。只有过程结束且原始判定唯一为 SUCCEEDED 才报 PASS。
on_host 'set -eu
    cd "$1"; log=fm.log
    [[ -r $log ]] || exit 1
    grep -E "^STEP_|Verification (SUCCEEDED|FAILED|INCONCLUSIVE)" "$log" | tail -16 || true
    if grep -Eq "Verification FAILED" "$log"; then echo RESULT: FORMAL_EQUIV_FAIL; exit 1; fi
    case $2 in 0) ;; 124) echo RESULT: FORMAL_INCONCLUSIVE; exit 2;; *) exit 1;; esac
    if grep -Fxq STEP_INPUTS_OK "$log" && grep -Fxq STEP_VERIFY_DONE "$log" &&
       grep -Eq "Verification SUCCEEDED" "$log" && ! grep -Eq "Verification INCONCLUSIVE" "$log"; then
        echo RESULT: FORMAL_EQUIV_PASS
    else
        echo RESULT: FORMAL_INCONCLUSIVE; exit 2
    fi' "$WORK" "$rc"
