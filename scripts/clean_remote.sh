#!/usr/bin/env bash
# 清理远端仿真机（默认 vm-meow）上的编译/综合临时产物。
#
#   bash scripts/clean_remote.sh            # dry-run：只列出将删除的路径
#   bash scripts/clean_remote.sh --apply    # 真正删除
#   REMOTE_HOST=other-host bash scripts/clean_remote.sh
#
# 白名单（远端）：/root/vcs/sim_*  /root/syn/work*  /root/timing
# 绝不删除：/root/vcs/*.sv、/root/vcs/*.txt、/root/syn/*.tcl、*.rpt、*.log 等源与报告
#
# 安全性：每条待删路径必须用 case 逐字匹配白名单前缀；匹配失败即跳过，
# 不存在任何"按目录整体 rm"的分支。远端 rm 只作用于单个已匹配路径。
set -uo pipefail

REMOTE_HOST="${REMOTE_HOST:-vm-meow}"
APPLY=0

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
    case "$arg" in
        --apply) APPLY=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "[error] 未知参数: $arg（支持 --apply / -h）" >&2; exit 2 ;;
    esac
done

echo "远端主机: ${REMOTE_HOST}"
if [ "$APPLY" -eq 1 ]; then
    echo "模式    : apply（删除中）"
else
    echo "模式    : dry-run（只列出，加 --apply 才删除）"
fi
echo

ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" "APPLY=$APPLY bash -s" <<'REMOTE_EOF'
set -uo pipefail

targets=""
append_target() {
    p="$1"
    # 硬性护栏：源文件与报告无论如何不进白名单
    case "$p" in
        *.sv|*.txt|*.tcl|*.rpt|*.log|*.sh) echo "[skip] 受保护源文件: $p"; return ;;
    esac
    if [ -e "$p" ] || [ -L "$p" ]; then
        targets="${targets}${p}
"
    fi
}

for p in /root/vcs/sim_*; do
    case "$p" in
        /root/vcs/sim_*) [ -d "$p" ] && append_target "$p" ;;
    esac
done
for p in /root/syn/work*; do
    case "$p" in
        /root/syn/work*) [ -d "$p" ] && append_target "$p" ;;
    esac
done
[ -d /root/timing ] && append_target /root/timing

if [ -z "$targets" ]; then
    echo "没有需要清理的远端临时产物。"
    exit 0
fi

count=0
total=0
echo "待清理路径："
while IFS= read -r p; do
    [ -n "$p" ] || continue
    # 二次校验：必须仍然命中白名单（防止拼接/变量污染）
    case "$p" in
        /root/vcs/sim_*|/root/syn/work*|/root/timing) ;;
        *) echo "  [REFUSE] 不在白名单内: $p"; continue ;;
    esac
    size=$(du -sk "$p" 2>/dev/null | awk '{print $1}')
    [ -n "$size" ] || size=0
    total=$((total + size))
    count=$((count + 1))
    printf "  [%8s]  %s\n" "$(du -sh "$p" 2>/dev/null | awk '{print $1}')" "$p"
done <<TARGET_LIST
$targets
TARGET_LIST

printf "\n合计: %d 项, 约 %s KiB\n" "$count" "$total"

if [ "${APPLY:-0}" != "1" ]; then
    echo
    echo "dry-run：未删除任何文件。核对无误后运行：bash scripts/clean_remote.sh --apply"
    exit 0
fi

deleted=0
failed=0
while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in
        /root/vcs/sim_*|/root/syn/work*|/root/timing)
            if rm -rf -- "$p"; then
                deleted=$((deleted + 1))
            else
                failed=$((failed + 1))
                echo "[error] 删除失败: $p" >&2
            fi
            ;;
        *) echo "[REFUSE] 不在白名单内: $p" >&2 ;;
    esac
done <<TARGET_LIST
$targets
TARGET_LIST

echo
echo "已删除 ${deleted} 项, 约 $(( total / 1024 )) MiB"
[ "$failed" -eq 0 ] || exit 1
REMOTE_EOF
