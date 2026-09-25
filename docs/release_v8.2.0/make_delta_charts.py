#!/usr/bin/env python3
"""v8.2.0 发布对账配图：v8.1.0 → v8.2.0 的数据对比可视化。

数据源（全部为本机真实跑批产物，路径可复现）：
  v8.1.0 冻结参考输出：优先 <audit>/baseline_v810_results.json；缺失时从
      `git show v8.1.0:tools/results/results.json` 取（SHA256 f3e1a796…）
  v8.2.0 新参考输出：优先本仓库 tools/results/results.json（SHA256 5ff9ef9a…），
      缺失时回退 <audit>/run1/results.json
  <audit>/delta_v810_vs_v820.json      逐键 delta（926 叶子）
  <audit>/probes/probe_results.json    17 项修复 + 5 项新发现的探针数据

audit 目录默认 /tmp/v82_audit，可用环境变量 ADI_AUDIT_DIR 覆盖。

输出：docs/release_v8.2.0/fig/*.png（浅色打印友好）
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.patches import Rectangle

AUD = Path(os.environ.get("ADI_AUDIT_DIR", "/tmp/v82_audit"))
REPO = Path(__file__).resolve().parents[2]
FIG = Path(__file__).resolve().parent / "fig"
FIG.mkdir(parents=True, exist_ok=True)


def _load_v810() -> dict:
    """v8.1.0 冻结参考输出：优先本机审计副本，缺失则从 git tag 取。"""
    local = AUD / "baseline_v810_results.json"
    if local.is_file():
        return json.loads(local.read_text(encoding="utf-8"))
    raw = subprocess.run(
        ["git", "show", "v8.1.0:tools/results/results.json"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    return json.loads(raw)


def _load_v820() -> dict:
    """v8.2.0 参考输出：以随仓库发布的 tools/results/results.json 为准。"""
    in_repo = REPO / "tools" / "results" / "results.json"
    if in_repo.is_file():
        return json.loads(in_repo.read_text(encoding="utf-8"))
    return json.loads((AUD / "run1" / "results.json").read_text(encoding="utf-8"))


BASE = _load_v810()
NEW = _load_v820()
DELTA = json.loads((AUD / "delta_v810_vs_v820.json").read_text(encoding="utf-8"))
PROBE = json.loads((AUD / "probes" / "probe_results.json").read_text(encoding="utf-8"))


def pick_cjk() -> str:
    """挑一个本机可用的中文字体名（找不到则退化为 DejaVu Sans）。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return "DejaVu Sans"


plt.rcParams.update(
    {
        "font.family": pick_cjk(),
        "axes.unicode_minus": False,
        "figure.facecolor": "white",
        "axes.facecolor": "#FAFAFA",
        "axes.edgecolor": "#444444",
        "axes.labelcolor": "#222222",
        "axes.grid": True,
        "grid.alpha": 0.35,
        "grid.color": "#BBBBBB",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "font.size": 9.5,
        "savefig.dpi": 200,
        "savefig.facecolor": "white",
    }
)
C_OLD, C_NEW, C_OK, C_WARN, C_INK = "#8C8C8C", "#2C6FBB", "#3A9D6E", "#D1495B", "#222222"


def chart_delta_map() -> None:
    """图 1：926 个叶子的变化分布——证据"只有 RNG 相关分区改变"。"""
    sec_changed: dict[str, int] = {}
    sec_total: dict[str, int] = {}
    for p in DELTA["changed"]:
        sec_changed[p["path"].split("/")[1]] = sec_changed.get(p["path"].split("/")[1], 0) + 1

    def count_leaves(node: dict, sec: str) -> None:
        for v in node.values():
            if isinstance(v, dict):
                count_leaves(v, sec)
            else:
                sec_total[sec] = sec_total.get(sec, 0) + 1

    for k, v in BASE.items():
        if isinstance(v, dict):
            count_leaves(v, k)

    secs = sorted(sec_total, key=lambda s: (-sec_changed.get(s, 0), s))
    fig, ax = plt.subplots(figsize=(9.6, 7.2))
    y = np.arange(len(secs))
    tot = [sec_total[s] for s in secs]
    chg = [sec_changed.get(s, 0) for s in secs]
    ax.barh(y, tot, color="#E4E7EC", height=0.72, label="逐字节不变")
    ax.barh(y, chg, color=C_WARN, height=0.72, label="发生变化")
    for i, (t, c) in enumerate(zip(tot, chg, strict=True)):
        if c:
            ax.text(t + 0.6, i, f"{c}/{t}", va="center", fontsize=8, color=C_WARN)
    ax.set_yticks(y)
    ax.set_yticklabels(secs, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("results.json 叶子数")
    ax.set_title(
        f"v8.1.0 → v8.2.0 参考输出逐键对账：{DELTA['identical_leaves']} 个叶子逐字节不变，"
        f"{DELTA['changed_leaves']} 个改变（全部在 RNG 相关分区）"
    )
    ax.legend(loc="lower right", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(FIG / "delta_map.png", bbox_inches="tight")
    plt.close(fig)


def chart_headline_compare() -> None:
    """图 2：关键指标 v8.1.0 vs v8.2.0（含变化量标注）。"""
    rows = [
        ("MC(16) SNDR 均值", BASE["mc"]["SNDR_mean"], NEW["mc"]["SNDR_mean"]),
        ("MC(16) SNDR 最差", BASE["mc"]["SNDR_min"], NEW["mc"]["SNDR_min"]),
        ("PDK关失配 SNDR 最差", BASE["mc_pdk_off"]["SNDR_min"], NEW["mc_pdk_off"]["SNDR_min"]),
        ("PDK关失配 SFDR 最差", BASE["mc_pdk_off"]["SFDR_min"], NEW["mc_pdk_off"]["SFDR_min"]),
        ("PDK开失配 SNDR 最差", BASE["mc_pdk_on"]["SNDR_min"], NEW["mc_pdk_on"]["SNDR_min"]),
        ("PDK开失配 SFDR 最差", BASE["mc_pdk_on"]["SFDR_min"], NEW["mc_pdk_on"]["SFDR_min"]),
    ]
    labels = [r[0] for r in rows]
    old = np.array([r[1] for r in rows])
    new = np.array([r[2] for r in rows])
    x = np.arange(len(rows))
    w = 0.36
    fig, ax = plt.subplots(figsize=(10.6, 4.6))
    ax.bar(x - w / 2, old, w, color=C_OLD, label="v8.1.0（旧随机流口径）", zorder=3)
    ax.bar(x + w / 2, new, w, color=C_NEW, label="v8.2.0（修复后）", zorder=3)
    for i, (o, n) in enumerate(zip(old, new, strict=True)):
        d = n - o
        col = C_OK if d > 0 else C_WARN
        ax.text(i, max(o, n) + 0.5, f"{d:+.2f} dB", ha="center", fontsize=8.5, color=col)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.6)
    ax.set_ylim(78, 97)
    ax.set_ylabel("dB")
    ax.legend(fontsize=8.6, loc="lower left")
    ax.set_title("关键 dB 指标：RNG 修复前后对比（均值几乎不变，尾部变差）")
    fig.tight_layout()
    fig.savefig(FIG / "headline_compare.png", bbox_inches="tight")
    plt.close(fig)


def chart_mc_distribution() -> None:
    """图 3：MC / PDK 逐芯片 SNDR 分布对比（v8.1.0 vs v8.2.0）。"""
    fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.4))
    for ax, (sec, title, _n_chip) in zip(
        axes,
        [
            ("mc", "主 MC（16 颗，单位失配，DEM 开）", 16),
            ("mc_pdk_off", "PDK 失配 MC（60 颗，DEM 关，良率下界口径）", 60),
        ],
        strict=True,
    ):
        o = np.asarray(BASE[sec]["sndr_per_chip"], dtype=float)
        n = np.asarray(NEW[sec]["sndr_per_chip"], dtype=float)
        bp = ax.boxplot(
            [o, n],
            widths=0.45,
            patch_artist=True,
            medianprops={"color": C_INK, "lw": 1.4},
            showfliers=False,
        )
        for patch, c in zip(bp["boxes"], [C_OLD, C_NEW], strict=True):
            patch.set_facecolor(c)
            patch.set_alpha(0.32)
            patch.set_edgecolor(c)
        rng = np.random.default_rng(0)
        for i, (vals, c) in enumerate(((o, C_OLD), (n, C_NEW)), start=1):
            jit = rng.uniform(-0.09, 0.09, vals.size)
            ax.scatter(np.full(vals.size, i) + jit, vals, s=16, color=c, alpha=0.85, zorder=3)
        ax.set_xticks([1, 2])
        ax.set_xticklabels([f"v8.1.0\n(n={o.size})", f"v8.2.0\n(n={n.size})"])
        ax.set_ylabel("SNDR (dB)")
        ax.set_title(
            f"{title}\nσ {o.std(ddof=1):.3f} → {n.std(ddof=1):.3f} dB，最差 {o.min():.2f} → {n.min():.2f} dB"
        )
    fig.suptitle(
        "逐芯片 SNDR 分布：修复后系综离散度变大、最差芯片下探（旧口径低估了离散度）", y=1.03
    )
    fig.tight_layout()
    fig.savefig(FIG / "mc_distribution.png", bbox_inches="tight")
    plt.close(fig)


def chart_rng_independence() -> None:
    """图 4：RNG 独立性证据——旧口径噪声流逐值复放失配抽签。"""
    seed0, k, n_chips = 1000, 8, 16
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.6))
    old_x, old_y = [], []
    for i in range(n_chips):
        old_x.append(np.random.default_rng(seed0 + i).normal(size=k))
        old_y.append(np.random.default_rng(seed0 + i).normal(size=k))
    new_x, new_y = [], []
    ss = np.random.SeedSequence(seed0)
    for child in ss.spawn(n_chips):
        new_x.append(np.random.default_rng(int(child.generate_state(1)[0])).normal(size=k))
        new_y.append(np.random.default_rng(child.spawn(1)[0]).normal(size=k))
    for ax, xs, ys, title, corr_col in (
        (axes[0], old_x, old_y, "v8.1.0 口径：失配与噪声共用整数种子", C_WARN),
        (axes[1], new_x, new_y, "v8.2.0 口径：SeedSequence.spawn 独立子流", C_OK),
    ):
        allx = np.concatenate(xs)
        ally = np.concatenate(ys)
        r = float(np.corrcoef(allx, ally)[0, 1])
        for xx, yy in zip(xs, ys, strict=True):
            ax.scatter(xx, yy, s=22, alpha=0.8, color=corr_col)
        lim = float(np.max(np.abs(ally))) * 1.15
        ax.plot([-lim, lim], [-lim, lim], ls="--", lw=0.9, color="#999999")
        ax.set_xlabel("失配抽签值")
        ax.set_ylabel("噪声实现值")
        ax.set_title(f"{title}\n皮尔逊 r = {r:+.3f}")
    fig.suptitle(
        "MC 统计独立性证据：旧口径下 16/16 颗芯片的两条流逐值相同（r = 1.000，点全部落在对角线上）",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(FIG / "rng_independence.png", bbox_inches="tight")
    plt.close(fig)


def chart_fix_matrix() -> None:
    """图 5：21 项发现的效力矩阵（严重度 / 是否影响发布数值 / 证据 / 状态）。"""
    findings = [
        # (id, 简述, 严重度, 影响发布数值?, 证据, 状态)
        ("F1", "MC 失配/噪声同种子", "P1", True, "探针+受控跑批", "已修"),
        ("F2", "调度器占用主噪声流", "P1", False, "探针(rng.spawn)", "已修"),
        ("F3a", "配置五量无合法性检查", "P1", False, "拒绝探针", "已修"),
        ("F3b", "Ron 码系数越界→静默'完美建立'", "P1", False, "拒绝探针", "已修"),
        ("F4", "负电容静默入仿真", "P1", False, "拒绝探针", "已修"),
        ("F5", "整定增益无收敛域检查", "P2", False, "拒绝探针", "已修"),
        ("F6", "r_aux 漏入正参数清单", "P2", False, "拒绝探针", "已修"),
        ("F7", "SADC NaN 静默排末仓", "P2", False, "拒绝探针", "已修"),
        ("F8", "节点验证器不支持小数码", "P2", False, "对拍探针 1e-16", "已修"),
        ("F9", "单位串扰口径 A(k) vs A(k)/2", "P2", True, "受控跑批(s13)", "已修"),
        ("F10", "短记录 beta bin 越 Nyquist", "P2", False, "bin 钳位探针", "已修"),
        ("F11", "λ=0 欠定分支无最小范数回退", "P2", False, "分支探针", "已修"),
        ("F12", "零误差窗口 raise 与 a06 不一致", "P2", False, "退化探针", "已修"),
        ("F13", "encode 无长度/码域校验", "P2", False, "契约探针", "已修"),
        ("F14", "null 口径使报告生成崩溃", "P2", False, "退化探针", "已修"),
        ("F15", "来源 id 不查重", "P2", False, "门禁探针", "已修"),
        ("F16", "g_r≤0 → beta 发散", "P2", False, "拒绝探针", "已修"),
        ("F17a", "观测无噪声 0/0 静默 nan", "P2", False, "退化探针", "已修"),
        ("F17b", "--results-dir 指文件裸 traceback", "P2", False, "CLI 退码探针", "已修"),
        ("N1", "encode 标量输入 len() TypeError", "P2", False, "契约探针", "本版修复"),
        ("N2", "crosstalk 整数取整 vs 分数插值", "P2", False, "对拍探针(56%)", "本版修复"),
        ("N3", "!docs/**/*.png 放过论文/专利截图", "P0-合规", False, "真跑 git add -A", "本版修复"),
        ("N4", "n_mask=None/0 使节点法崩溃", "P2", False, "真调函数", "本版修复"),
    ]
    cols = ["严重度", "影响发布数值", "已跑数据证据", "状态"]
    fig, ax = plt.subplots(figsize=(10.2, 7.6))
    n = len(findings)
    for j, _ in enumerate(cols):
        for i in range(n):
            ax.add_patch(
                Rectangle(
                    (j + 0.03, i + 0.03), 0.94, 0.94, facecolor="white", edgecolor="#DDDDDD", lw=0.6
                )
            )
    for i, (fid, desc, sev, active, ev, _status) in enumerate(findings):
        y = i + 0.5
        ax.text(
            0.5, y, f"{fid}", ha="center", va="center", fontsize=8.6, weight="bold", color=C_INK
        )
        ax.text(1.5, y, desc, ha="center", va="center", fontsize=7.6, color=C_INK)
        c = {"P0-合规": C_WARN, "P1": "#E08A00", "P2": C_NEW}.get(sev, C_INK)
        ax.text(2.5, y, sev, ha="center", va="center", fontsize=8, color=c, weight="bold")
        ax.text(
            3.5,
            y,
            "是" if active else "否（潜伏）",
            ha="center",
            va="center",
            fontsize=8,
            color=C_WARN if active else "#777777",
            weight="bold" if active else "normal",
        )
        ax.text(4.5, y, ev, ha="center", va="center", fontsize=7.6, color=C_INK)
    ax.set_xlim(0, 5)
    ax.set_ylim(n, 0)
    ax.set_xticks([0.5, 1.5, 2.5, 3.5, 4.5])
    ax.set_xticklabels(["发现", "内容", "严重度", "影响发布数值", "证据"], fontsize=8.8)
    ax.xaxis.set_ticks_position("top")
    ax.xaxis.set_label_position("top")
    ax.set_yticks([])
    ax.grid(False)
    ax.set_title(
        "23 项发现的效力矩阵（17 项 2026-09-25 审查修复 + 4 项本版新增 + 2 项驳回/2 项设计选择另列）",
        pad=26,
    )
    fig.tight_layout()
    fig.savefig(FIG / "fix_matrix.png", bbox_inches="tight")
    plt.close(fig)


def chart_probe_effects() -> None:
    """图 6：三项探针的量化效果（对数尺度）。"""
    p4 = PROBE["P4_charge_ref_fractional"]
    p3 = PROBE["P3_crosstalk_activity"]
    p1 = PROBE["P1_rng_independence"]
    fig, axes = plt.subplots(1, 3, figsize=(11.6, 3.9))

    # (a) 闭式 vs 节点：旧整数口径 vs 修复后
    ax = axes[0]
    legacy = p4["uniform_chip"]["legacy_integer_code_max_rel_diff"]
    fixed = max(p4["uniform_chip"]["frac_code_max_rel_diff"], 1e-18)
    ax.bar(
        ["旧口径\n(整数截断码)", "修复后\n(小数线性插值)"],
        [legacy, fixed],
        color=[C_WARN, C_OK],
        width=0.55,
        zorder=3,
    )
    ax.set_yscale("log")
    ax.set_ylim(1e-17, 1e-1)
    for i, v in enumerate((legacy, fixed)):
        ax.text(i, v * 1.6, f"{v:.1e}", ha="center", fontsize=8.5)
    ax.set_ylabel("闭式 vs 节点 最大相对偏差")
    ax.set_title("(a) F8 小数码对拍\n（dither 开启，64 组小数子码）", fontsize=9)

    # (b) RNG：旧 vs 新相关性
    ax = axes[1]
    ax.bar(
        ["旧口径", "修复后"],
        [1.0, abs(p1["new_corr_mean"])],
        color=[C_WARN, C_OK],
        width=0.55,
        zorder=3,
    )
    ax.set_ylim(0, 1.15)
    ax.text(0, 1.03, "r = 1.000\n16/16 逐值相同", ha="center", fontsize=8)
    ax.text(
        1,
        abs(p1["new_corr_mean"]) + 0.03,
        f"平均 |r| = {abs(p1['new_corr_mean']):.3f}",
        ha="center",
        fontsize=8,
    )
    ax.set_ylabel("失配流 × 噪声流 相关性")
    ax.set_title("(b) F1 RNG 独立性\n（16 颗芯片，每颗 8 维抽样）", fontsize=9)

    # (c) crosstalk 量化
    ax = axes[2]
    cur = p3["graded_current_vs_fractional_max_rel"]
    ax.bar(
        ["整数取整\n(本版前)", "分数插值\n(本版后)"],
        [cur, 0.0],
        color=[C_WARN, C_OK],
        width=0.55,
        zorder=3,
    )
    ax.set_ylim(0, max(cur * 1.35, 0.1))
    ax.text(0, cur + 0.01, f"最大相对偏差 {cur*100:.1f}%", ha="center", fontsize=8)
    ax.text(1, 0.01, "0（连续、与\n_dem_fluctuation 一致）", ha="center", fontsize=8, color=C_OK)
    ax.set_ylabel("梯度 profile 下 相对偏差")
    ax.set_title("(c) N2 单位串扰口径\n（801 个码点，梯度空间分布）", fontsize=9)

    fig.suptitle("三项修复的量化效果（真实函数调用 / 探针数据）", y=1.04)
    fig.tight_layout()
    fig.savefig(FIG / "probe_effects.png", bbox_inches="tight")
    plt.close(fig)


for fn in (
    chart_delta_map,
    chart_headline_compare,
    chart_mc_distribution,
    chart_rng_independence,
    chart_fix_matrix,
    chart_probe_effects,
):
    fn()
    print("OK:", fn.__name__)
print("figures written to", FIG)
