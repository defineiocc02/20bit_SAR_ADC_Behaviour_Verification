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
from adi_model.control.dem3d import Dem3State, unary_rotation_masks
from adi_model.control.track_aux import build_track_plan, causality_audit


def check_a01_low_code_coverage() -> dict:
    """码 8、固定物理片、一个相位周期后必须覆盖全部 63 列。"""
    from adi_model.simulation.config import Config
    from adi_model.simulation.slice_pool import PhysicalSlicePool
    from adi_model.topology.dac_arch import NominalCodebook, SplitChip

    cap = 4.00390625e-14
    chip = SplitChip(
        C_main=np.full((18, 63), cap),
        C_sub=np.full((18, 8), cap),
        C_bridge=np.full(18, 8 * cap / 7),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=640.625e-15,
    )
    pool = PhysicalSlicePool(
        Config(),
        chip=chip,
        nominal_codebook=NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512),
        mask_binding=unary_rotation_masks,
    )
    n = 63 * 9
    ids = np.broadcast_to(np.arange(8), (n, 8))
    states = Dem3State.sequence(n, columns=63)
    command = unary_rotation_masks(pool.geometry, np.full(n, 8, np.int64), states, ids)
    visits = command.main_on[:, 7, :].sum(axis=0)
    covered = int(np.count_nonzero(visits))
    return {
        "finding": "A01 DEM phase coverage",
        "old": "63 列里只访问 15 个（固定片、低码）",
        "now": f"{covered}/63 main units, range={int(visits.max() - visits.min())}",
        "pass": covered == 63 and int(visits.max() - visits.min()) == 0,
    }


def check_a02_lsb_units() -> dict:
    """1 LSB20 的电荷误差必须折算成 1.0 LSB。"""
    from types import SimpleNamespace

    from adi_model.control.dem import SplitSwitchGeometry
    from adi_model.control.dem3d import nominal_conservation

    c_norm_f, lsb20_v = 20.5e-12, 6.0 / 2**20
    charges = np.array([0.0, c_norm_f * lsb20_v])

    class _Stub:
        geometry = SplitSwitchGeometry(63, 8, 18, 8, 0, 511)

        def split_coefficients(self, ids):
            return {"c_signal": np.full(len(ids), c_norm_f)}

        def charge_from_masks(self, ids, command, **references):
            return SimpleNamespace(charge_c=charges.copy())

    report = nominal_conservation(
        _Stub(),
        unary_rotation_masks,
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
