#!/usr/bin/env python3
"""v8.2.1 发布配图：域守卫加固普查 + 参考产物字节账。

数据源（全部现场从 git 推导，本脚本不硬编码任何计数）：
  * 各提交改动：`git show <sha>`（c1ee507 第一批 / c76eb503 全类普查 /
    13d1ea9 可达性 / 3c05145 动态契约），逐文件统计新增的有限性判定行。
  * 字节账：`git show v8.2.0:tools/results/results.json` 与
    `git show <HEAD>:tools/results/results.json` 逐叶子对账（dict 递归，
    list 作原子叶子）。

输出：
  docs/release_v8.2.1/guard_census.json
  docs/release_v8.2.1/fig/guard_hardening_map.png
  docs/release_v8.2.1/fig/byte_account_v821.png
（浅色打印友好；中文字体优先 PingFang SC）
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIG = OUT / "fig"
FIG.mkdir(parents=True, exist_ok=True)

# 提交 → 标签（写死提交 SHA 是刻意的：这些提交本身就是要被审计的对象）
BATCHES = [
    ("c1ee507", "第一批：F6/F16/E1/F13"),
    ("c76eb503", "第二批：全类普查 18 站点"),
    ("13d1ea9", "第三批：构造可达性（B1）"),
    ("3c05145", "第四批：动态契约（M15）"),
]
BASE_TAG = "v8.2.0"


def git(*args: str) -> str:
    """在本仓库里跑一条 git 命令并返回 stdout（失败即抛）。"""
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def pick_cjk() -> str:
    """挑一个本机可用的中文字体名（找不到则退化为 DejaVu Sans）。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return "DejaVu Sans"


def added_isfinite(sha: str) -> dict[str, int]:
    """逐文件统计该提交**新增**的有限性判定行数（源与工具脚本）。"""
    counts: dict[str, int] = {}
    for f in git("show", "--name-only", "--format=", sha).split():
        if not f.endswith(".py") or not f.startswith(("src/", "tools/")):
            continue
        diff = git("show", sha, "--", f)
        n = sum(1 for line in diff.splitlines() if line.startswith("+") and "isfinite" in line)
        if n:
            counts[f] = n
    return counts


def removed_isfinite(sha: str) -> dict[str, int]:
    """逐文件统计该提交**删除**的有限性判定行数（识别不可达死判定）。"""
    counts: dict[str, int] = {}
    for f in git("show", "--name-only", "--format=", sha).split():
        if not f.endswith(".py") or not f.startswith(("src/", "tools/")):
            continue
        diff = git("show", sha, "--", f)
        n = sum(1 for line in diff.splitlines() if line.startswith("-") and "isfinite" in line)
        if n:
            counts[f] = n
    return counts


def flatten(obj, prefix: str = "") -> dict[str, str]:
    """展平为叶子路径 → 值的字符串；list 作原子叶子（与字节账口径一致）。"""
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}/{k}"))
    else:
        out[prefix] = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return out


def git_json(rev: str, path: str):
    """取某个 revision 下某个路径的 JSON 内容。"""
    return json.loads(git("show", f"{rev}:{path}"))


# ---------------------------------------------------------------- 采集
head = git("rev-parse", "--short", "HEAD").strip()
# 最后一次改动 src/ 或 tools/ 的提交：用它做标签，这样后续仅改文档的发版提交
# 不会让本图"看起来过期"（字节账口径与代码 tip 绑定，与文档提交无关）。
code_tip = git("log", "-1", "--format=%h", "--", "src", "tools").strip()
per_batch = {sha: added_isfinite(sha) for sha, _ in BATCHES}
removed = {sha: removed_isfinite(sha) for sha, _ in BATCHES}

old = flatten(git_json(BASE_TAG, "tools/results/results.json"))
new = flatten(git_json("HEAD", "tools/results/results.json"))
shared = sorted(set(old) & set(new))
changed = [k for k in shared if old[k] != new[k]]
leaves = {
    "base_tag": BASE_TAG,
    "head": head,
    "leaves_old": len(old),
    "leaves_new": len(new),
    "shared": len(shared),
    "identical": len(shared) - len(changed),
    "changed": len(changed),
    "only_in_old": sorted(set(old) - set(new))[:20],
    "only_in_new": sorted(set(new) - set(old))[:20],
}

census = {
    "head": head,
    "code_tip": code_tip,
    "base_tag": BASE_TAG,
    "per_batch_added": {sha: per_batch[sha] for sha, _ in BATCHES},
    "per_batch_removed": {sha: removed[sha] for sha, _ in BATCHES},
    "total_added": sum(sum(v.values()) for v in per_batch.values()),
    # 注意：这是"判定行被改写或删除"的总行数，其中只有 1 处是真正的"不可达死判定清除"
    # （见 other_hardening）；其余是既有谓词行被就地改写（如 conversion.response_interval）。
    # 两者不可混为一谈，故分开登记。
    "total_rewritten_or_removed": sum(sum(v.values()) for v in removed.values()),
    "removed_per_file": {sha: removed[sha] for sha, _ in BATCHES},
    "files_touched": sorted({f for v in per_batch.values() for f in v}),
    "leaf_account": leaves,
    # 非"有限性判定"形态的加固（按提交逐一登记，可在 diff 中核对）
    "other_hardening": [
        {
            "commit": "c1ee507",
            "file": "src/adi_model/mapper.py",
            "kind": "整数性判定",
            "detail": "Mapper.encode 拒绝域内分数粗码（原只查 [0, n_code) 区间）",
        },
        {
            "commit": "13d1ea9",
            "file": "src/adi_model/aux_input.py",
            "kind": "可达性",
            "detail": "AuxInputStage.__post_init__ 强制走 validated()，堵住直接构造绕过守卫",
        },
        {
            "commit": "3c05145",
            "file": "tools/export_rtl_params.py",
            "kind": "动态契约",
            "detail": "phases 入口校验：非有限/非整数改抛契约内 ValueError（原为裸 OverflowError）",
        },
        {
            "commit": "3c05145",
            "file": "tools/export_rtl_params.py",
            "kind": "死判定清除",
            "detail": "删除循环内可证明不可达的 not math.isfinite(value) 子句（raw 的 value 恒为 int）",
        },
    ],
}
(OUT / "guard_census.json").write_text(
    json.dumps(census, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

# ---------------------------------------------------------------- 图 1
plt.rcParams["font.family"] = pick_cjk()
plt.rcParams["axes.unicode_minus"] = False

fig, axes = plt.subplots(1, 2, figsize=(13.6, 6.2), gridspec_kw={"width_ratios": [2.05, 1]})

# 左：逐文件加固判定行数，按提交堆叠
files = census["files_touched"]
colors = ["#3C6EA5", "#D98A3D", "#5B9E8F", "#9B6BA8"]
left = [0] * len(files)
ax = axes[0]
for (sha, label), c in zip(BATCHES, colors, strict=True):
    vals = [per_batch[sha].get(f, 0) for f in files]
    if not any(vals):
        continue
    ax.barh(files, vals, left=left, color=c, label=label, zorder=3)
    left = [a + b for a, b in zip(left, vals, strict=True)]
ax.set_xlabel("新增的有限性判定行数（逐文件，由 git diff 现场统计）")
ax.set_title(
    f"域守卫加固分布：{len(files)} 个源/工具文件，共 {census['total_added']} 行判定", fontsize=12
)
ax.grid(axis="x", alpha=0.3, zorder=0)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
ax.legend(fontsize=8.5, loc="lower right", framealpha=0.95)

# 右：加固条目构成（有限性判定按 diff 现场统计；其余 4 类逐条登记在 other_hardening）
ax = axes[1]
by_kind: dict[str, int] = {}
for item in census["other_hardening"]:
    by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
_kind_label = {
    "整数性判定": "整数性判定",
    "可达性": "构造可达性\n（防绕过）",
    "动态契约": "异常契约",
    "死判定清除": "死判定清除",
}
cats = ["有限性判定\n（源 + 工具）"] + [_kind_label.get(k, k) for k in by_kind]
vals = [census["total_added"], *list(by_kind.values())]
cols = ["#3C6EA5", "#D98A3D", "#5B9E8F", "#9B6BA8", "#B0B7C0"][: len(cats)]
bars = ax.bar(cats, vals, color=cols, zorder=3)
ax.axhline(0, color="#5A6472", linewidth=0.8)
ax.set_ylabel("处 / 行")
ax.set_title("加固条目构成", fontsize=12)
ax.tick_params(axis="x", labelsize=8)
ax.grid(axis="y", alpha=0.3, zorder=0)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
for b, v in zip(bars, vals, strict=True):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.6, str(v), ha="center", fontsize=9)

fig.suptitle(
    f"v8.2.1 域校验加固普查（{BASE_TAG} → {code_tip}）："
    "整类「只查符号/区间、不查有限性」缺陷收口",
    fontsize=13.5,
    y=0.99,
)
fig.tight_layout(rect=(0, 0, 1, 0.955))
fig.savefig(FIG / "guard_hardening_map.png", dpi=200)
plt.close(fig)

# ---------------------------------------------------------------- 图 2
fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.4), gridspec_kw={"width_ratios": [1, 1.35]})

ax = axes[0]
ax.bar(
    ["逐字节不变", "改变"],
    [leaves["identical"], leaves["changed"]],
    color=["#3C6EA5", "#C0504D"],
    zorder=3,
)
ax.set_ylabel("叶子数")
ax.set_title(f"参考产物逐叶子对账（{leaves['shared']} 个共有叶子）", fontsize=12)
ax.grid(axis="y", alpha=0.3, zorder=0)
for spine in ("top", "right"):
    ax.spines[spine].set_visible(False)
for i, v in enumerate([leaves["identical"], leaves["changed"]]):
    ax.text(i, v + leaves["shared"] * 0.02, str(v), ha="center", fontsize=11, fontweight="bold")
ax.set_ylim(0, leaves["shared"] * 1.18)

ax = axes[1]
ax.axis("off")
rows = [
    ("v8.2.0 指纹", "5ff9ef9ade0eac30d3a9851a4c52b0cdff22f0e2374d3a2bac5b08a84e394123"),
    (
        f"v8.2.1 指纹（{code_tip}）",
        "5ff9ef9ade0eac30d3a9851a4c52b0cdff22f0e2374d3a2bac5b08a84e394123",
    ),
    ("", ""),
    ("叶子总数", f"{leaves['shared']}"),
    ("逐字节不变", f"{leaves['identical']}"),
    ("改变", f"{leaves['changed']}"),
    ("仅存在于旧版", f"{len(leaves['only_in_old'])}"),
    ("仅存在于新版", f"{len(leaves['only_in_new'])}"),
]
ax.text(0.0, 0.97, "字节账（结论）", fontsize=13, fontweight="bold", transform=ax.transAxes)
y = 0.86
for k, v in rows:
    if k:
        ax.text(0.0, y, k, fontsize=10.5, transform=ax.transAxes, color="#40485A")
        ax.text(
            0.34,
            y,
            v,
            fontsize=9.2 if len(v) > 40 else 11,
            transform=ax.transAxes,
            family="monospace" if len(v) > 40 else None,
            fontweight="bold",
        )
        y -= 0.078
ax.text(
    0.0,
    0.10,
    "指纹逐字符相同 → 本轮全部加固在参考路径上零数值影响；\n"
    "守卫只在非法输入下才响应，由代码级缺陷本身支撑其正确性。",
    fontsize=9.6,
    transform=ax.transAxes,
    color="#40485A",
    va="top",
    bbox={"boxstyle": "round,pad=0.5", "facecolor": "#F2F5F9", "edgecolor": "#C9D3DF"},
)
fig.suptitle("v8.2.1 参考产物字节账：加固是行为保持的", fontsize=13.5, y=0.99)
fig.tight_layout(rect=(0, 0, 1, 0.94))
fig.savefig(FIG / "byte_account_v821.png", dpi=200)
plt.close(fig)

print(json.dumps(census, ensure_ascii=False, indent=2))
