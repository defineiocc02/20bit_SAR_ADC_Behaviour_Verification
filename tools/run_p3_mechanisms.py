# =============================================================================
# 文件名     : run_p3_mechanisms.py
# 版本       : v1.0
# 功能       : 在现行 Fig.11 模型上检查 RDAC、DEM、扰动和预跟踪的单项机制。
# 设计说明   : 通过同一模型构造真实配置、片组计划和名义码本。
#              每项记录采用哪类输入和边界，便于把结论追溯到对应机制。
# =============================================================================
"""P3 机制证据：follower 因果、DEM 守恒、dither 范围/残余、TRACK 输入电荷。

各项实验使用 `adi_model` 的 Fig.11 17/5 电容阵列和 18 选 8 调度配置。

运行：
    run_dir="$(mktemp -d)"
    PYTHONPATH=src python3 tools/run_p3_mechanisms.py --out "$run_dir"
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

# 研究夹具随源码仓库提供；直接运行脚本时也使用同一份实验。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adi_model.control.dem3d import (
    Dem3State,
    FIG11_COLUMNS,
    ROUND_ROBIN_PERIOD,
    fig11_masks,
    nominal_conservation,
    round_robin_plan,
    usage_uniformity,
)
from adi_model.control.dither import FIG19_FRACTIONAL_WEIGHTS_Q, FIG19_STEP_Q, Fig19StaticSpec
from adi_model.control.dither_port import (
    attenuated_dither_bound,
    code_shift_residual,
    range_ledger,
)
from adi_model.control.follower import (
    ResolvedPrefixes,
    audit_causality,
    compare_reference_cost,
    run_direct_final,
    run_follower,
    run_trial_sar,
    switching_cost,
)
from adi_model.control.track_aux import (
    build_track_plan,
    causality_audit,
    consecutive_sample_charge,
    track_residual_charge,
)
from subsys.experiments.fig11 import Fig11Model, Fig11Parameters
from adi_model.simulation.slice_pool import PhysicalSlicePool

# =============================================================================
# 1. 共用输入与结果编码
# =============================================================================
FS_HZ = 40e6
V_FS = 3.0


def _jsonable(value):
    """把 numpy 标量/数组降为 JSON 原生类型；不改数值。"""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _model(mismatch: float = 1e-3) -> Fig11Model:
    # 两组 DEM 电容用同一种子，以隔离是否施加失配的作用。
    # 反馈电容的随机偏移固定为零，逐码比较只包含主、子和桥接阵列失配。
    return Fig11Model(
        seed=20261105,
        parameters=Fig11Parameters(
            unit_mismatch_std=mismatch, bridge_mismatch_std=mismatch, feedback_mismatch_std=0.0
        ),
        config_overrides=None,
    )


# =============================================================================
# 2. RDAC 跟随时序与参考轨电荷
# =============================================================================
# 从当前流水线拿粗码和实际转换片号。各 RDAC 策略在相同片号上比较。
# 已决前缀定义每步能使用哪些位。把最终码提前送入所有步骤是负对照。
def follower_evidence(model, n: int = 4096) -> dict:
    """用真实运行得到的 9 位码驱动 follower，给出因果审计与参考代价。"""
    run = model.simulate(lambda t: V_FS * 0.999 * np.sin(2 * np.pi * 1e6 * t), n)
    coarse = np.asarray(run.raw.coarse, dtype=np.int64)
    prefixes = ResolvedPrefixes.from_final(coarse)
    ids = run.raw.conv_slice_ids
    states = np.zeros(n, dtype=np.int64)
    pool = PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=fig11_masks,
        acquisition_binding=lambda geometry, sample_ids, slice_ids, control: __import__(
            "adi_model.defaults", fromlist=["_all_signal"]
        )._all_signal(geometry, sample_ids, slice_ids, control),
        schedule_binding=round_robin_plan(n),
    )
    follower = run_follower(
        pool, prefixes, states, slice_ids=ids, reference_high_v=V_FS, reference_low_v=-V_FS
    )
    direct = run_direct_final(
        pool, coarse, states, slice_ids=ids, reference_high_v=V_FS, reference_low_v=-V_FS
    )
    trial = run_trial_sar(
        pool, coarse, states, slice_ids=ids, reference_high_v=V_FS, reference_low_v=-V_FS
    )
    audit = audit_causality(prefixes, lambda values: values)
    peeking = audit_causality(
        prefixes, lambda values: np.repeat(values[:, -1:], values.shape[1], axis=1)
    )
    # 三条路径到达相同参考端点。累积开关电荷由沿途事件和方向决定。
    # 论文参考电流只用于同表对照，完整的瞬态负载还取决于驱动和相位。
    cost = compare_reference_cost(
        follower, direct, trial, fs_hz=FS_HZ, paper_reference_current_a=1.8e-3
    )
    return {
        "samples": n,
        "causality_audit": {
            "checked": audit.checked,
            "violations": audit.violations,
            "causal": audit.causal,
        },
        "peeking_control": {
            "violations": peeking.violations,
            "first": peeking.first,
            "caught": not peeking.causal,
        },
        "switching_cost_follower": switching_cost(follower),
        "cost": cost,
        "paper_reference_current_ma": 1.8,
        "_note": "final charge identical across the three paths; only the event sequence differs",
    }


# =============================================================================
# 3. DEM 名义电荷和实际片覆盖
# =============================================================================
# 保留模型的码本和电容表，单独指定相位审计所需的掩码及片组计划。
def _pool_for(model, binding, plan=None):
    return PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=binding,
        acquisition_binding=None,
        schedule_binding=plan,
    )


def dem_evidence(model, n: int = 360, codes: int = 64) -> dict:
    """名义守恒（零失配池）+ 失配下的状态离散度 + 使用均匀性（整周期）。

    两件事必须分开报：名义守恒查"DEM 是否改了权重"（零失配池应为 0）；
    失配离散度查"DEM 把固定失配摊成什么"（含失配池必然非零，这就是 DEM 代价）。
    """
    nominal = _model(mismatch=0.0)
    nominal_pool = _pool_for(nominal, fig11_masks, round_robin_plan(n))
    subset = np.unique(np.linspace(0, 511, codes).astype(np.int64))
    states = Dem3State.sequence(240)
    # 名义和失配池使用同一份粗码、相位和参考轨。均匀性另查物理片的实际覆盖。
    conservation = nominal_conservation(
        nominal_pool,
        fig11_masks,
        states,
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
        codes=subset,
    )
    mismatched_pool = _pool_for(model, fig11_masks, round_robin_plan(n))
    mismatched = nominal_conservation(
        mismatched_pool,
        fig11_masks,
        states,
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
        codes=subset,
    )
    # 每个片选位置只在每轮出现一次，15 个横向位置各需重复一个 9 笔片选周期。
    # 码 8 只测子支路，码 32 选第一个 2C。两者结合才能看清低码的选通范围。
    n_usage = FIG11_COLUMNS * ROUND_ROBIN_PERIOD
    plan = round_robin_plan(n_usage)
    usage = usage_uniformity(
        mismatched_pool,
        fig11_masks,
        plan,
        Dem3State.sequence(n_usage),
        codes=(8, 32, 255, 511),
    )
    return {
        "codes_checked": conservation["codes_checked"],
        "states_checked": conservation["states_checked"],
        "max_spread_c": conservation["max_spread_c"],
        "max_spread_lsb20": conservation["max_spread_lsb20"],
        "max_spread_v": conservation["max_spread_v"],
        "conserved": conservation["conserved"],
        "mismatched_spread_lsb20": mismatched["max_spread_lsb20"],
        "usage": {
            "samples": usage["samples"],
            "per_code": {
                code: {
                    "main_units_covered": row["main_units_covered"],
                    "main_chi2_per_dof": row["main_chi2_per_dof"],
                    "uniform": row["uniform"],
                }
                for code, row in usage["codes"].items()
            },
            "uniform": usage["uniform"],
        },
        "_note": "conservation is nominal-charge dispersion across DEM states; not INL",
    }


# =============================================================================
# 4. 扰动范围和码偏移残余
# =============================================================================
# 先用声明的注入规格做范围预算，再用同一实际码本计算偏移前后残余。
def dither_evidence(model) -> dict:
    """用当前 Fig.11 电容池检查扰动范围与码偏移残余。"""
    spec = Fig19StaticSpec(
        v_fs=V_FS,
        step_q=FIG19_STEP_Q,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="tools/run_p3_mechanisms.py explicit engineering binding",
    )
    ledger = range_ledger(spec, v_high=V_FS, v_low=-V_FS)
    pool = PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=fig11_masks,
        acquisition_binding=None,
        schedule_binding=round_robin_plan(8),
    )
    # U=5 时从 6 起扫，保留最低端以上的全部偏移测试点。
    # 电荷归一化电容由实际活动片一次求和，残余再折算到输入端。
    codes = np.arange(6, 512, dtype=np.int64)
    words = np.full(codes.size, 48, dtype=np.int64)  # D6=D4=1 -> U=5
    c_signal = float(pool.signal_capacitance(np.arange(8)[None, :])[0])
    residual = code_shift_residual(
        pool,
        codes,
        words,
        spec=spec,
        reference_high_v=V_FS,
        reference_low_v=-V_FS,
        c_signal_f=c_signal,
    )
    return {
        "range_ledger": ledger,
        "code_shift_residual_default_pool": {
            key: residual[key]
            for key in (
                "max_abs_residual_lsb20",
                "max_abs_residual_v",
                "worst_code",
                "illegal_shifted_codes",
                "code_shift_exact",
            )
        },
        "task_tree_bound_reconstruction": attenuated_dither_bound(5.0),
        "_note": "code shift residual evaluated with the current Fig.11 17/5 model codebook",
    }


# =============================================================================
# 5. 预跟踪的可用判决与输入电荷
# =============================================================================
# 孔径只能读取当时已经完成的判决。参考输入和实测残余都以同一孔径方式归一化。
def track_evidence(model, n: int = 4096) -> dict:
    """TRACK 预置后的输入电荷与论文 350 uA_pp 的口径核对。"""
    run = model.simulate(lambda t: V_FS * 0.999 * np.sin(2 * np.pi * 1e6 * t), n)
    codes = np.asarray(run.raw.coarse, dtype=np.int64)
    # 预测值必须由**数字码 + 名义码本**重建，不能读 plant 的 input_clean_v（外部审查 A03）。
    values = np.asarray(model.codebook.evaluate(codes), dtype=float)
    plan = round_robin_plan(n)
    pool = PhysicalSlicePool(
        model.cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=fig11_masks,
        acquisition_binding=None,
        schedule_binding=plan,
    )
    # 两相流水线下，上一笔判决要到本笔孔径才完成，因此最小合法回看是 2 笔。
    track = build_track_plan(
        plan, codes, values_v=values, fs_hz=FS_HZ, track_lead_s=5e-9, lookback=2
    )
    audit = causality_audit(track)
    charge = track_residual_charge(pool, track, values)
    # signal_capacitance 已按行求和（8 片合计），不能再乘 n_active
    total_cap = float(pool.signal_capacitance(plan.conv[0:1])[0])
    return {
        "audit": audit,
        "charge": charge,
        "total_sampling_cap_f": total_cap,
        "paper_input_current_ua_pp": 350.0,
        "at_1mhz": consecutive_sample_charge(total_cap, V_FS, 1e6, FS_HZ),
        "_note": (
            "论文 Fig.9.8.5 的输入电流横轴是直流差分输入电压，不是正弦频率；"
            "本表只给电荷吞吐，不做频率反解（外部审查 A05）"
        ),
    }


# =============================================================================
# 6. 结果记录
# =============================================================================
# 按机制逐项写入完整 JSON，终端摘要便于定位应继续查看的分支。
def main() -> int:
    parser = argparse.ArgumentParser(description="P3 mechanism evidence")
    parser.add_argument("--out", type=Path, help="结果目录；省略时在系统临时目录中新建")
    parser.add_argument("--samples", type=int, default=4096)
    args = parser.parse_args()
    # 无参数重跑时使用独立目录，避免半次或并发运行覆盖已发布的图和测量记录。
    out = args.out if args.out is not None else Path(tempfile.mkdtemp(prefix="sar-mechanism-study-"))
    out.mkdir(parents=True, exist_ok=True)
    if args.out is None:
        print(f"结果目录 {out}", file=sys.stderr)
    # 2026-11-13：这里用 exist_ok=True 是为了允许"输出到已有目录"，
    # 但会**静默覆盖**同名结果文件。打印一行提示，避免误当成新结果。
    if out.is_dir() and any(out.iterdir()) and not getattr(args, "force", False):
        print(f"note: output directory {out} already has files; existing results will be overwritten")
    model = _model()
    document = {
        "profile": "adi_isscc2024_40m_6vpp",
        "fs_hz": FS_HZ,
        "v_fs_v": V_FS,
        "evidence_level": "L1 behavioral (float); no RTL/post-layout/silicon",
        "follower": follower_evidence(model, args.samples),
        "dem": dem_evidence(model),
        "dither": dither_evidence(model),
        "track": track_evidence(model, args.samples),
    }
    (out / "p3_mechanisms.json").write_text(json.dumps(document, indent=2, ensure_ascii=False, default=_jsonable) + "\n")
    print(json.dumps(
        {
            "follower_causal": document["follower"]["causality_audit"]["causal"],
            "peeking_caught": document["follower"]["peeking_control"]["caught"],
            "trial_over_follower": document["follower"]["cost"]["trial_over_follower_charge_ratio"],
            "follower_ma": document["follower"]["cost"]["follower_reference_current_a"] * 1e3,
            "dem_uniform": document["dem"]["usage"]["uniform"],
            "dem_conserved": document["dem"]["conserved"],
            "dither_lower_bound_v": document["dither"]["range_ledger"]["sadc_lower_bound_v"],
            "track_reduction": document["track"]["charge"]["charge_reduction"],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
