"""对外部审查 A01–A04 的**修复后**回归：一条命令验证四个反例不再成立。

审查方的 `minimal_counterexamples.py` 复算的是当时那几行表达式；本脚本改成调用
当前模块的真实入口，因此能证明修复生效，而不是证明旧公式还算得对。

运行：
    PYTHONPATH=src python3 tools/check_audit_findings.py
"""

from __future__ import annotations

import sys

import numpy as np

from adi_model.behavioral.ra_az import OrdinaryAZConfig, apply_ordinary_az
from adi_model.control.dem3d import Dem3State, fig11_masks
from adi_model.control.track_aux import build_track_plan, causality_audit


def check_a01_low_code_coverage() -> dict:
    """码 8、固定物理片、一个相位周期后必须覆盖全部主列。

    2026-11-13：几何从 63+8 切到专利的 17/5 后，本检查仍用 63+8 的旧参数
    （C_sub 8 列、n=63*9、columns=63），fig11_masks 会直接拒绝 —— 脚本此前
    抛异常失败，但被管道掩盖成 exit 0，长期未被发现。
    现在按当前几何（主阵列 17 列 = 15×2C + B4 + S4；子阵列 5 位端口）重写。
    """
    from adi_model.control.dem3d import fig11_masks
    from adi_model.simulation.config import Config
    from adi_model.simulation.slice_pool import PhysicalSlicePool
    from adi_model.topology.dac_arch import NominalCodebook, SplitChip

    cap = 7.779886183991967e-14
    # 主阵列 17 列：15 个 2C + B4 1C + S4 1C；子阵列**装配 4 列**（第 5 位是
    # FIG.11 虚线占位，见 docs/mechanisms/p1_gaps_20261113.md §11）。
    chip = SplitChip(
        C_main=np.array([[2.0] * 15 + [1.0, 1.0]], dtype=float)[0][None, :] * np.ones((18, 1))
        * cap,
        C_sub=np.full((18, 4), cap),
        C_bridge=np.full(18, cap),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=640.625e-15,
    )
    # 必须显式声明**物理位宽** 17/5：器件只装配 4 列子阵列（第 5 位是虚线占位），
    # 而端口宽度仍是 5。不声明时 pool 会用装配列数建几何（4），fig11_masks 会拒绝。
    cfg = Config(dac_n_main=17, dac_n_sub=5)
    pool = PhysicalSlicePool(
        cfg,
        chip=chip,
        nominal_codebook=NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512),
        mask_binding=fig11_masks,
    )
    # 15 个 2C 列构成横向相位环，一个完整相位周期 = 15 笔
    n_columns = 15
    n = n_columns * 9
    ids = np.broadcast_to(np.arange(8), (n, 8))
    states = Dem3State.sequence(n, columns=n_columns)
    # 用**code 32** 而不是原来的 code 8：在 17/5 编码下 code 8 只选子阵列
    # （子掩码 [0]，主掩码为空），主列访问必然为 0，测不出"低码是否覆盖全部主列"。
    # code 32 是**会用到全部 15 个 2C 主列的最低码**（主码 2 → 1 个 2C）。
    command = fig11_masks(pool.geometry, np.full(n, 32, np.int64), states, ids)
    # 只看 15 个 2C 主列（索引 0..14）；B4(15) 与 S4(16) 不参与横向旋转
    visits = command.main_on[:, 7, :n_columns].sum(axis=0)
    covered = int(np.count_nonzero(visits))
    return {
        "finding": "A01 DEM phase coverage",
        "old": f"低码固定片时只访问部分主列",
        "now": f"{covered}/{n_columns} main 2C columns, range={int(visits.max() - visits.min())}",
        "pass": covered == n_columns and int(visits.max() - visits.min()) == 0,
    }


def check_a02_lsb_units() -> dict:
    """1 LSB20 的电荷误差必须折算成 1.0 LSB。"""
    from types import SimpleNamespace

    from adi_model.control.dem import SplitSwitchGeometry
    from adi_model.control.dem3d import nominal_conservation

    c_norm_f, lsb20_v = 20.5e-12, 6.0 / 2**20
    charges = np.array([0.0, c_norm_f * lsb20_v])

    class _Stub:
        # 17/5 物理位宽（子阵列端口 5 位；器件可只装配 4 列）
        geometry = SplitSwitchGeometry(17, 5, 18, 8, 0, 511)

        def split_coefficients(self, ids):
            return {"c_signal": np.full(len(ids), c_norm_f)}

        def charge_from_masks(self, ids, command, **references):
            return SimpleNamespace(charge_c=charges.copy())

    report = nominal_conservation(
        _Stub(),
        fig11_masks,
        np.array([Dem3State(0, 0).to_int(), Dem3State(1, 0).to_int()]),
        reference_high_v=3.0,
        reference_low_v=-3.0,
        codes=np.array([8]),
    )
    return {
        "finding": "A02 charge to LSB units",
        "old": "把电荷直接除以电压 LSB，单位是法拉",
        "now": f"{report['max_spread_lsb20']:.12f} LSB20, {report['max_spread_v']:.3e} V",
        "pass": abs(report["max_spread_lsb20"] - 1.0) < 1e-9,
    }


def check_a03_dc_is_not_cheating() -> dict:
    """恒定直流输入下，合法预置不得被判偷看。"""
    from adi_model.control.dem3d import round_robin_plan

    plan = round_robin_plan(16)
    values = np.ones(16)
    codes = np.zeros(16, dtype=np.int64)
    track = build_track_plan(
        plan, codes, values_v=values, fs_hz=40e6, track_lead_s=5e-9, lookback=2
    )
    audit = causality_audit(track)
    return {
        "finding": "A03 TRACK causality",
        "old": "预测值等于目标值即判偷看（直流输入误报）",
        "now": f"future={audit['future_sources']} late={audit['late_sources']} causal={audit['causal']}",
        "pass": bool(audit["causal"]),
    }


def check_a04_az_hold_time() -> dict:
    """AZ 的 droop 必须按保持时间算，且噪声要能分块复现。"""
    # droop 用无噪声配置，否则 5 ns x 1 V/s = 5 nV 会被 0.1 mV 噪声盖住
    droop_cfg = OrdinaryAZConfig(enabled=True, droop_v_per_s=1.0)
    noise_cfg = OrdinaryAZConfig(enabled=True, noise_v=1e-4, seed=11)
    ids = np.arange(64, dtype=np.int64)
    zeros = np.zeros(64)
    refreshed = apply_ordinary_az(zeros, ids, droop_cfg, hold_time_s=np.zeros(64))
    held = apply_ordinary_az(zeros, ids, droop_cfg, hold_time_s=np.full(64, 5e-9))
    whole = apply_ordinary_az(zeros, ids, noise_cfg, hold_time_s=np.zeros(64))
    chunk_a = apply_ordinary_az(zeros[:32], ids[:32], noise_cfg, hold_time_s=np.zeros(32))
    chunk_b = apply_ordinary_az(zeros[32:], ids[32:], noise_cfg, hold_time_s=np.zeros(32))
    joined = np.concatenate([chunk_a.residual_v, chunk_b.residual_v])
    return {
        "finding": "A04 ordinary AZ",
        "old": "droop 用绝对记录时间；噪声每次调用重设种子",
        "now": (
            f"hold=0 -> {refreshed.residual_v.max():.3e} V; "
            f"hold=5ns -> {held.residual_v.max():.3e} V; "
            f"chunked==whole: {np.allclose(joined, whole.residual_v)}"
        ),
        "pass": bool(
            np.allclose(joined, whole.residual_v)
            and np.allclose(refreshed.residual_v, 0.0)
            and held.residual_v.max() > 0
        ),
    }


def main() -> int:
    checks = [
        check_a01_low_code_coverage(),
        check_a02_lsb_units(),
        check_a03_dc_is_not_cheating(),
        check_a04_az_hold_time(),
    ]
    failed = 0
    for row in checks:
        status = "PASS" if row["pass"] else "FAIL"
        print(f"[{status}] {row['finding']}: {row['now']}")
        if not row["pass"]:
            failed += 1
    print(f"\n{len(checks) - failed}/{len(checks)} audit findings resolved")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
