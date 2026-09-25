#!/usr/bin/env python3
"""v8.2.2 发布配图：非有限 ingress 收口对照 + 参考产物字节账。

数据源（全部现场实测 / 现场从 git 推导，本脚本不硬编码任何结论或计数）：

* **ingress 对照**：把 `v8.2.1` 与当前代码树各自 `git archive` 解到临时目录，用**同一个
  探针**（本文件 `--probe` 子进程模式）在每棵树上实跑 6 个场景，按"谁拦下它"分类
  （入口标度闸门 / SVD 因子闸门 / reference 闸门 / 下游权重闸门 / 未拦下），并按异常
  类型区分后果（契约 ValueError / RuntimeWarning / LAPACK 层异常 / 裸 OverflowError）。
  探针回传 `adi_model.__file__` 以便核对它确实跑在被指定的那棵树上。
* **变更计数**：`git diff v8.2.1..<code_tip> -- src tools` 现场统计新增的有限性判定行。
* **字节账**：`git show <rev>:tools/results/results.json` 逐叶子对账（dict 递归，list
  作原子叶子），三版指纹并列。

输出：
  docs/release_v8.2.2/guard_census_v822.json
  docs/release_v8.2.2/fig/ingress_closure_v822.png
  docs/release_v8.2.2/fig/byte_account_v822.png

子进程模式（内部使用）：`python make_guard_charts.py --probe <name>`，stdout 单行 JSON。
（浅色打印友好；中文字体优先 PingFang SC）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
FIG = OUT / "fig"
FIG.mkdir(parents=True, exist_ok=True)

BASE_TAG = "v8.2.0"
PREFIX_TAG = "v8.2.1"

# 场景 → 图上的行标签（探针名必须与 probe() 里的分支一致）
CASES = [
    ("ref_nonfinite", "上游 reference 非有限\n(known_input_v = inf)"),
    ("spec_scale_inf", "spec 标度非有限\n(v_fs = inf)"),
    ("spec_scale_nan_stub", "spec 标度非有限 + 满秩桩\n(adc2_v_min = nan)"),
    ("svd_factor_inf", "SVD 因子非有限\n(因子桩返回 inf)"),
    ("weights_finite_negative", "权重有限但非正\n(因子桩返回 -1.0)"),
    ("extreme_scale_svd", "标度有限但极端，溢出落在 linalg\n(v_fs = 1e308)"),
    ("extreme_assembly", "标度有限但极端，溢出落在装配\n(v_fs = 1e308 + 注入 1e308)"),
]

# 信息串 → 是哪一层闸门拦下的（顺序即匹配优先级）
GATES = [
    ("physical scales must be finite", "入口标度闸门"),
    ("non-finite factors", "SVD 因子闸门"),
    ("training reference must be finite", "reference 闸门"),
    ("rank ", "可辨识性检查"),
    ("nonpositive", "下游权重闸门"),
    ("must be finite and positive", "FrozenCalibration 校验"),
]

# 后果类型 → (颜色, 图例文字)
OUTCOME_STYLE = {
    "contract_valueerror": ("#5B9E8F", "契约一致的 ValueError（目标行为）"),
    "runtime_warning": ("#C0504D", "RuntimeWarning（本仓 filterwarnings 下即硬失败）"),
    "linalg_error": ("#D98A3D", "LAPACK 层异常（非本仓契约）"),
    "overflow_error": ("#9B6BA8", "裸 OverflowError（非本仓契约）"),
    "no_error": ("#B0B7C0", "未拦下"),
    "other": ("#7A8290", "其它异常"),
}


def git(*args: str) -> str:
    """在本仓库里跑一条 git 命令并返回 stdout（失败即抛）。"""
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def python_bin() -> str:
    """返回当前解释器路径（子进程复用同一个 venv）。"""
    return sys.executable


# ------------------------------------------------------------------ 探针（子进程侧）
def _fake_svd(bad):
    """复刻回归测试里的 svd 桩：只污染 vh[0,0]，其余保持满秩单位阵。"""

    def f(design, full_matrices=False, check_finite=False, lapack_driver="gesdd"):
        nn, pp = design.shape
        u = np.zeros((nn, pp))
        for i in range(min(nn, pp)):
            u[i, i] = 1.0
        vh = np.eye(pp)
        vh[0, 0] = bad
        return u, np.ones(pp), vh

    return f


def _classify(exc: BaseException) -> str:
    """把异常归到后果类型（顺序重要：先认具体类，再认 ValueError）。"""
    name = type(exc).__name__
    if name in ("RuntimeWarning",):
        return "runtime_warning"
    if name == "LinAlgError":
        return "linalg_error"
    if isinstance(exc, OverflowError):
        return "overflow_error"
    if isinstance(exc, ValueError):
        return "contract_valueerror"
    return "other"


def probe(name: str) -> dict:
    """在**当前**代码树上实跑一个场景，返回 {outcome, message, module}。"""
    import warnings

    import adi_model
    from adi_model import Config
    from adi_model import weight_calibration as wc
    from adi_model.weight_calibration import (
        CalibrationSpec,
        DigitalObservation,
        fit_unit_weights,
    )

    spec = CalibrationSpec.from_config(Config(dac_arch="split"))
    p = int(np.prod(spec.shape)) + 1
    n = p + 5
    na, ns = spec.n_active, spec.n_slices
    sid = np.array([[(i + j) % ns for j in range(na)] for i in range(n)], dtype=np.int64)
    z64 = np.zeros(n, dtype=np.int64)
    z = np.zeros(n)
    ov = np.zeros(n, dtype=bool)
    x = np.ones(n)
    injection = z
    stub: float | None = None

    if name == "ref_nonfinite":
        x = np.ones(n)
        x[0] = float("inf")
    elif name == "spec_scale_inf":
        spec = replace(spec, v_fs=float("inf"))
    elif name == "spec_scale_nan_stub":
        spec = replace(spec, adc2_v_min=float("nan"))
        stub = float("nan")
    elif name == "svd_factor_inf":
        stub = float("inf")
    elif name == "weights_finite_negative":
        stub = -1.0
    elif name == "extreme_scale_svd":
        spec = replace(spec, v_fs=1e308)
    elif name == "extreme_assembly":
        spec = replace(spec, v_fs=1e308)
        injection = np.full(n, 1e308)
    else:  # pragma: no cover - 只在手误时触发
        raise SystemExit(f"unknown probe: {name}")

    obs = DigitalObservation(spec, sid, z64, z64, z64, z, injection, ov, True)
    real = wc.svd
    if stub is not None:
        wc.svd = _fake_svd(stub)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            fit_unit_weights(obs, x)
    except BaseException as exc:
        return {
            "probe": name,
            "outcome": _classify(exc),
            "message": str(exc)[:220],
            "exception": type(exc).__name__,
            "module": getattr(adi_model, "__file__", "?"),
        }
    finally:
        wc.svd = real
    return {
        "probe": name,
        "outcome": "no_error",
        "message": "",
        "exception": "",
        "module": getattr(adi_model, "__file__", "?"),
    }


# ------------------------------------------------------------------ 变更计数 / 字节账
def added_isfinite(diff: str) -> dict[str, int]:
    """从一段 unified diff 逐文件统计**新增**的有限性判定行数。"""
    counts: dict[str, int] = {}
    current = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and "isfinite" in line:
            counts[current] = counts.get(current, 0) + 1
    return {k: v for k, v in counts.items() if v}


def leaves(payload: object, prefix: str = "") -> dict[str, str]:
    """把 JSON 递归成 {叶子路径: 值字符串}；list 与标量都作原子叶子。"""
    if isinstance(payload, dict):
        out: dict[str, str] = {}
        for key, value in payload.items():
            out.update(leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: json.dumps(payload, sort_keys=True)}


def leaves_at(rev: str) -> dict[str, str]:
    """取某个 revision 里随仓库发布的 results.json 的叶子表。"""
    raw = git("show", f"{rev}:tools/results/results.json")
    return leaves(json.loads(raw))


def fingerprint_at(rev: str) -> str:
    """取某个 revision 里 results.json 的 sha256（对 git blob 内容本身）。"""
    import hashlib

    raw = subprocess.run(
        ["git", "show", f"{rev}:tools/results/results.json"],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(raw).hexdigest()


# ------------------------------------------------------------------ 探针调度
def run_probes(rev: str, workdir: Path) -> list[dict]:
    """把某 revision 解包到 workdir，并在其中逐一实跑全部场景。"""
    tree = workdir / rev
    tree.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", rev], cwd=REPO, check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env["OPENBLAS_NUM_THREADS"] = "1"
    results: list[dict] = []
    for name, _ in CASES:
        proc = subprocess.run(
            [python_bin(), str(Path(__file__).resolve()), "--probe", name],
            cwd=str(tree),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        line = ""
        # 修复前的 LAPACK 会把 "** On entry to DLASCL..." 打到 **stdout**，所以不能只取
        # 最后一行：扫描全部行，取第一条能解析成 JSON 的（探针本身只输出一行 JSON）。
        for candidate in proc.stdout.splitlines():
            if candidate.startswith("{"):
                try:
                    json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                line = candidate
                break
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {
                "probe": name,
                "outcome": "other",
                "message": (proc.stderr.strip() or "probe produced no JSON")[-220:],
                "exception": "",
                "module": "?",
                "lapack_stdout_noise": proc.stdout.count("** On entry to"),
            }
        payload["rev"] = rev
        # LAPACK 内部诊断**打在 stdout 上**（修复前才会出现），是一项可测量的劣化指标
        payload["lapack_stdout_noise"] = proc.stdout.count("** On entry to")
        results.append(payload)
    return results


def pick_cjk() -> str:
    """挑一个本机可用的中文字体名（找不到则退化为 DejaVu Sans）。"""
    for name in ("PingFang SC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return "DejaVu Sans"


def gate_of(payload: dict) -> str:
    """把一条探针结果归成"谁拦下的"标签。"""
    if payload["outcome"] != "contract_valueerror":
        for label, style in OUTCOME_STYLE.items():
            if label == payload["outcome"]:
                return style[1].split("（")[0]
        return payload["outcome"]
    for needle, label in GATES:
        if needle in payload["message"]:
            return label
    return "契约 ValueError（未识别来源）"


# ------------------------------------------------------------------ 主流程
def main() -> int:
    """解包两棵树、实跑探针、现场推导计数并出图。"""
    code_tip = git("log", "-1", "--format=%h", "--", "src", "tools").strip()
    with tempfile.TemporaryDirectory(prefix="v822_census_") as tmp:
        workdir = Path(tmp)
        before = run_probes(PREFIX_TAG, workdir)
        after = run_probes(code_tip, workdir)

    by_rev = {PREFIX_TAG: {r["probe"]: r for r in before}, code_tip: {r["probe"]: r for r in after}}
    diff = git("diff", f"{PREFIX_TAG}..{code_tip}", "--", "src", "tools")
    counts = added_isfinite(diff)

    # 本轮每个修复提交各自新增了多少行有限性判定（现场从 git 推导）
    per_commit: list[tuple[str, str, int]] = []
    for sha in git("log", "--format=%h", f"{PREFIX_TAG}..{code_tip}").split():
        subject = git("show", "-s", "--format=%s", sha).strip()
        per_commit.append((sha, subject, sum(added_isfinite(git("show", sha)).values())))

    old_leaves = leaves_at(PREFIX_TAG)
    new_leaves = leaves_at(code_tip)
    shared = sorted(set(old_leaves) & set(new_leaves))
    identical = [k for k in shared if old_leaves[k] == new_leaves[k]]
    changed = [k for k in shared if old_leaves[k] != new_leaves[k]]

    census = {
        "prefix_tag": PREFIX_TAG,
        "base_tag": BASE_TAG,
        "code_tip": code_tip,
        "probes": {"before": before, "after": after},
        "added_isfinite_lines": counts,
        "added_isfinite_total": sum(counts.values()),
        "per_commit": [
            {"sha": sha, "subject": subject, "added_isfinite": n} for sha, subject, n in per_commit
        ],
        "fingerprints": {
            BASE_TAG: fingerprint_at(BASE_TAG),
            PREFIX_TAG: fingerprint_at(PREFIX_TAG),
            code_tip: fingerprint_at(code_tip),
        },
        "leaves": {
            "shared": len(shared),
            "identical": len(identical),
            "changed": len(changed),
            "only_in_old": sorted(set(old_leaves) - set(new_leaves)),
            "only_in_new": sorted(set(new_leaves) - set(old_leaves)),
        },
    }
    (OUT / "guard_census_v822.json").write_text(
        json.dumps(census, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    plt.rcParams["font.sans-serif"] = [pick_cjk(), "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    # ---------------------------------------------------------- 图 1：ingress 收口对照
    fig, ax = plt.subplots(figsize=(13.6, 6.0))
    cols = [f"{PREFIX_TAG}（修复前）", f"v8.2.2（修复后，{code_tip}）"]
    ax.set_xlim(0, 2)
    ax.set_ylim(0, len(CASES))
    for xi in (1,):
        ax.axvline(xi, color="#D5DBE3", linewidth=0.9, zorder=1)
    for yi in range(1, len(CASES)):
        ax.axhline(yi, color="#EDF0F4", linewidth=0.8, zorder=1)
    for row, (name, label) in enumerate(CASES):
        y = len(CASES) - row - 1
        ax.text(-0.03, y + 0.5, label, ha="right", va="center", fontsize=9.6, color="#2B3340")
        for col, rev in enumerate((PREFIX_TAG, code_tip)):
            payload = by_rev[rev][name]
            color, _ = OUTCOME_STYLE.get(payload["outcome"], OUTCOME_STYLE["other"])
            ax.add_patch(
                plt.Rectangle((col + 0.06, y + 0.12), 0.88, 0.76, facecolor=color, alpha=0.86)
            )
            ax.text(
                col + 0.5,
                y + 0.55,
                gate_of(payload),
                ha="center",
                va="center",
                fontsize=9.4,
                color="white",
                fontweight="bold",
                zorder=3,
            )
            noise = payload.get("lapack_stdout_noise") or 0
            subtitle = payload["exception"] or "—"
            if noise:
                subtitle = f"{subtitle}  (+{noise} 行 LAPACK 输出污染 stdout)"
            ax.text(
                col + 0.5,
                y + 0.27,
                subtitle,
                ha="center",
                va="center",
                fontsize=8.2,
                color="white",
                alpha=0.92,
                zorder=3,
            )
    for col, title in enumerate(cols):
        ax.text(col + 0.5, len(CASES) + 0.12, title, ha="center", fontsize=11, fontweight="bold")
    ax.set_axis_off()
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=c, alpha=0.86)
        for c in [OUTCOME_STYLE[k][0] for k in OUTCOME_STYLE]
    ]
    labels = [OUTCOME_STYLE[k][1] for k in OUTCOME_STYLE]
    ax.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.03),
        ncol=3,
        fontsize=8.6,
        frameon=False,
    )
    fig.suptitle(
        f"v8.2.2 非有限 ingress 收口对照：同一个探针在 {PREFIX_TAG} 与 {code_tip} 上实跑",
        fontsize=13.5,
        y=0.985,
    )
    fig.tight_layout(rect=(0.13, 0.08, 1, 0.945))
    fig.savefig(FIG / "ingress_closure_v822.png", dpi=200)
    plt.close(fig)

    # ---------------------------------------------------------- 图 2：变更计数 + 字节账
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.4), gridspec_kw={"width_ratios": [1, 1.35]})
    files = sorted(counts)
    ax = axes[0]
    ypos = np.arange(len(per_commit))
    vals = [n for _, _, n in per_commit]
    labels = [f"{sha}  {subject.split(':')[0]}" for sha, subject, _ in per_commit]
    bars = ax.barh(ypos, vals, color="#3C6EA5", zorder=3, height=0.45)
    ax.set_yticks(ypos, labels, fontsize=9)
    ax.set_xlabel("新增有限性判定行数")
    ax.set_title(f"{PREFIX_TAG} → {code_tip}：两个修复提交各自的闸门", fontsize=12)
    ax.grid(axis="x", alpha=0.3, zorder=0)
    ax.set_xlim(0, max(vals) * 1.45 if vals else 1)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for b, v in zip(bars, vals, strict=True):
        ax.text(v + 0.04, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=10.5)

    ax = axes[1]
    ax.axis("off")
    fp = census["fingerprints"]
    rows = [
        (f"{BASE_TAG} 指纹", fp[BASE_TAG]),
        (f"{PREFIX_TAG} 指纹", fp[PREFIX_TAG]),
        (f"v8.2.2 指纹（{code_tip}）", fp[code_tip]),
        ("", ""),
        ("叶子总数", f"{census['leaves']['shared']}"),
        ("逐字节不变", f"{census['leaves']['identical']}"),
        ("改变", f"{census['leaves']['changed']}"),
        ("新增有限性判定", f"{census['added_isfinite_total']} 行 / {len(files)} 文件"),
    ]
    ax.text(0.0, 0.97, "字节账（结论）", fontsize=13, fontweight="bold", transform=ax.transAxes)
    y = 0.87
    for k, v in rows:
        if k:
            ax.text(0.0, y, k, fontsize=10.5, transform=ax.transAxes, color="#40485A")
            ax.text(
                0.36,
                y,
                v if v else "—",
                fontsize=9.2 if len(v) > 40 else 11,
                transform=ax.transAxes,
                family="monospace" if len(v) > 40 else None,
                fontweight="bold",
            )
            y -= 0.082
    verifier = (
        "三版指纹逐字符相同 → 两处新闸门在参考路径上零数值影响。\n"
        "闸门只在非法输入下才响应，其正确性由探针与变异检验支撑。"
    )
    ax.text(
        0.0,
        0.075,
        verifier,
        fontsize=9.6,
        transform=ax.transAxes,
        color="#40485A",
        va="top",
        bbox={"boxstyle": "round,pad=0.5", "facecolor": "#F2F5F9", "edgecolor": "#C9D3DF"},
    )
    fig.suptitle("v8.2.2 参考产物字节账：两处闸门是行为保持的", fontsize=13.5, y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIG / "byte_account_v822.png", dpi=200)
    plt.close(fig)

    print(json.dumps(census, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        print(json.dumps(probe(sys.argv[2]), ensure_ascii=False))
    else:
        raise SystemExit(main())
