"""第二轮审查处置图（F13）：子阵列相位偏置的修复前后 + 可辨识性实验对照。

数据来源：
* 左图：本仓库 `dem3d.fig11_masks`，纵向周期 16（旧）vs 7（新），码 1；
* 右图：`mask_calibration.fit_element_weights` 的四种设计矩阵形式在本仓库全节点 plant 上的残差。

运行：
    PYTHONPATH=src python3 tools/run_review2_figure.py
"""

# ⚠️ 本工具依赖 **63+8 旧几何**（columns=63、n=63*9、tests.unit.test_dem3d._default_pool），
# 而主链已切换到专利的 17/5，且 `_default_pool` 已从测试模块移除。
# 因此它当前**跑不起来**（ImportError），也不应在 17/5 下强行适配：
# 它服务于 2026-09 那次针对 63+8 的审查，结论对当前几何不适用。
# 保留文件以保持审查可追溯；新的 DEM 使用均匀性证据见
# `docs/mechanisms/dem_fig11.md` 与 `tests/unit/test_dem3d.py`。


from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.unit.test_dem3d import _default_pool  # noqa: E402
from tools.run_p4_ablation import V_FS, _build_model, _ramp_fn, _run  # noqa: E402

from adi_model.calibration.mask_calibration import _mask_matrix  # noqa: E402
from adi_model.control.dem3d import (  # noqa: E402
    Dem3State,
    round_robin_plan,
    fig11_masks,
)


def _sub_counts(vertical: int) -> list[int]:
    pool = _default_pool()
    # 与审查一致：取联合周期 lcm(63x9, 16) = 9072，否则 16 相位的偏置会被截断掩盖
    n = 9072 if vertical == 16 else 63 * 9
    plan = round_robin_plan(n)
    states = Dem3State.sequence(n, columns=63, vertical=vertical)
    command = fig11_masks(pool.geometry, np.full(n, 1, np.int64), states, plan.conv)
    # 必须按**单个物理片**统计：聚合会把"某片只用 3 个单元"平均掉，
    # 正是审查指出的"聚合计数不能证明逐片覆盖"。
    counts = np.zeros(pool.geometry.n_sub, dtype=int)
    for sample in range(n):
        position = np.flatnonzero(plan.conv[sample] == 0)
        if position.size:
            counts += command.sub_on[sample, int(position[0]), :]
    return counts.tolist()


def _fit_variants() -> dict:
    model = _build_model(1e-3, True)
    n = 4096
    generator = np.random.default_rng(5)
    levels = generator.permutation(np.linspace(-2.95, 2.95, n))
    raw, _ = _run(model, _ramp_fn(levels), n, dem=False, uniform_plan=False)
    data = raw.digital_observation
    masks = _mask_matrix(data)
    fine = np.asarray(data.fine_v)
    voltages = levels[:, None] + V_FS - 2 * V_FS * masks
    fixed = np.ptp(masks, axis=0) == 0
    # 每片的主/子单元数取芯片实际列数（63+8 → 17/5），不写字面量。
    n_main_units, n_sub_units = model.chip.n_main, model.chip.n_sub
    per_slice = n_main_units + n_sub_units
    main_only = np.zeros(masks.shape[1], bool)
    for slice_index in range(18):
        main_only[slice_index * per_slice : slice_index * per_slice + n_main_units] = True

    variants = {}
    for label, design in (
        ("all columns + fixed merged", np.column_stack([voltages[:, ~fixed], voltages[:, fixed].sum(axis=1), np.ones(n)])),
        ("drop fixed columns", np.column_stack([voltages[:, ~fixed], np.ones(n)])),
        ("main array only", np.column_stack([voltages[:, main_only], np.ones(n)])),
    ):
        theta, _, rank, _ = np.linalg.lstsq(design, fine, rcond=None)
        variants[label] = {
            "residual_uv": float(np.sqrt(np.mean((fine - design @ theta) ** 2)) * 1e6),
            "rank": int(rank),
            "columns": int(design.shape[1]),
        }

    # 真权重 + 线性化 beta（不拟合）
    ids = np.asarray(data.slice_ids)
    chip = model.chip
    cf = float(np.asarray(chip.C_feedback_true))
    bridge = chip.C_bridge[ids]
    sub_sum = chip.C_sub[ids].sum(axis=-1)
    beta = bridge / (bridge + sub_sum + chip.c_p_sub[ids])
    weights = np.concatenate(
        # beta 是 [sample, active_slice]，C_sub[ids] 是 [sample, active_slice, unit]；
        # 必须沿最后的单元轴扩展。beta[:, None] 只在 sample 数恰好等于活动片数时
        # 才不报错，且会按 sample 索引——这正是审查指出的错误广播。
        [chip.C_main[ids] / cf, beta[:, :, None] * chip.C_sub[ids] / cf], axis=2
    )
    predicted = np.sum(
        weights * voltages.reshape(n, 18, model.chip.n_main + model.chip.n_sub)[
            np.arange(n)[:, None], ids
        ], axis=(1, 2)
    )
    variants["true weights + linearised beta"] = {
        "residual_uv": float(np.sqrt(np.mean((fine - predicted) ** 2)) * 1e6),
        "rank": None,
        "columns": None,
    }
    return variants


def main() -> int:
    out = Path("docs/reviews/20261113_p3p4/figures")
    out.mkdir(parents=True, exist_ok=True)
    old = _sub_counts(16)
    new = _sub_counts(7)
    variants = _fit_variants()

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(13, 4.4), constrained_layout=True)
    x = np.arange(8)
    ax0.bar(x - 0.2, old, 0.4, label="old: vertical period 16, then % 7", color="#c53030")
    ax0.bar(x + 0.2, new, 0.4, label="fixed: vertical period 7", color="#2b6cb0")
    ax0.set_xticks(x, [f"sub {i}" for i in range(8)])
    ax0.set_ylabel("selections in one phase cycle")
    ax0.set_title("F13a  code 1 sub-array usage (8th unit never connected)", fontsize=10)
    ax0.legend(fontsize=8)
    ax0.grid(alpha=0.3, axis="y")
    ax0.text(0.02, 0.95, f"old {old[0]}/{old[1]}/{old[2]}x5 (3:2)  ->  new {new[0]}x7",
             transform=ax0.transAxes, fontsize=8)

    labels = list(variants)
    values = [variants[k]["residual_uv"] for k in labels]
    colors = ["#2b6cb0", "#c53030", "#c53030", "#b7791f"]
    ax1.barh(labels, values, color=colors)
    ax1.set_xscale("log")
    ax1.set_xlabel("fit residual on the full-nodal plant [uV], log scale")
    ax1.set_title("F13b  residual-domain fit vs model form (signal is ~12 mV)", fontsize=10)
    for index, value in enumerate(values):
        ax1.text(value * 1.05, index, f"{value:.0f}", va="center", fontsize=8)
    ax1.grid(alpha=0.3, axis="x")

    fig.suptitle(
        "F13  second-review response: sub-array phase bias fixed; linearised beta is the remaining gap"
    )
    fig.savefig(out / "F13_review2.png", dpi=130)
    plt.close(fig)
    payload = {
        "figure": "F13_review2.png",
        "sub_counts_old_vertical16": old,
        "sub_counts_new_vertical7": new,
        "fit_variants": variants,
        "note": "left: repo masks only; right: repo full-nodal plant, 4096 samples, seed 20261105",
    }
    (out / "F13_review2.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
