"""P4：adi_40m 行为级消融矩阵与论文锚点对照。

口径先说清楚，避免把不同证据等级混在一起：

* L1 行为级：本脚本的全部 INL/DNL/SNR/SNDR/SFDR/DR/NSD 数字都来自
  `adi_model` 的浮点行为模型，**不是** RTL、后仿或硅测。
* 每个数字都带 `evidence_level` 与 `assumption` 字段；论文值单列，不参与拟合。
* 缺一条机制（校准系数、环内 dither、动态带宽）时该配置写 `NOT_RUN` 与阻塞原因，
  不用"接近就行"的替代量填表。

运行：
    PYTHONPATH=src python3 tools/run_p4_ablation.py --out docs/reviews/20261113_p3p4/data
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from adi_model import metrics
from adi_model.defaults import DefaultModel, DefaultParameters, _schedule
from adi_model.dem3d import Dem3State, round_robin_plan, unary_rotation_masks
from adi_model.pipeline import run_pipeline
from adi_model.slice_pool import PhysicalSlicePool

FS_HZ = 40e6
V_FS = 3.0
LSB20 = 2 * V_FS / 2**20
PAPER = {
    "source": "ISSCC 2024 Session 9.8 digest p.3 and slides p.36-37/p.42",
    "process_nm": 40,
    "supply": "3.3 V first stage (0.4 um devices), 1.1 V digital for level shifters",
    "input_range_vpp_diff": 6.0,
    "inl_ppm": "+2.2 / -1.8",
    "dnl_ppm": 0.27,
    "snr_db": 93.7,
    "dr_db": 94.2,
    "dr_db_slides": 94.6,
    "nsd_v_rthz": 9.3e-9,
    "nsd_v_rthz_slides": 8.8e-9,
    "sfdr_db": 119.0,
    "thd_db_at_1mhz": -107.0,
    "input_current_ua_pp": 350.0,
    "reference_current_ma": 1.8,
    "power_core_mw": 50.0,
    "power_system_mw": 69.0,
}


def _noise_cap_f(model, ids: np.ndarray) -> np.ndarray:
    """与 DefaultModel.simulate 同式的采样噪声等效电容。"""
    chip = model.chip
    main, sub = chip.C_main[ids], chip.C_sub[ids]
    beta = chip.C_bridge[ids] / (chip.C_bridge[ids] + sub.sum(axis=-1) + chip.c_p_sub[ids])
    norm = (main.sum(axis=-1) + beta * sub.sum(axis=-1)).sum(axis=-1)
    factor = (main.sum(axis=-1) + beta**2 * sub.sum(axis=-1)).sum(axis=-1)
    return norm**2 / factor


def _run(model, input_fn, n_samples: int, *, dem: bool, uniform_plan: bool):
    """执行一次显式配置；DEM 打开时用旋转掩码，否则复用默认分段掩码。"""
    from adi_model.defaults import _all_signal, _segmented_mask

    cfg = replace(model.cfg, dem_enable=dem)
    ids_seed = model._child_seed(1, 0)
    schedule = (
        round_robin_plan(n_samples)
        if uniform_plan
        else _schedule(ids_seed)
    )
    pool = PhysicalSlicePool(
        cfg,
        chip=model.chip,
        nominal_codebook=model.codebook,
        mask_binding=unary_rotation_masks if dem else _segmented_mask,
        acquisition_binding=_all_signal,
        schedule_binding=schedule,
    )
    states = Dem3State.sequence(n_samples) if dem else np.zeros(n_samples, dtype=np.int64)
    plan = pool.plan(n_samples)
    noise_cap = _noise_cap_f(model, plan.conv) if cfg.enable_sampling_noise else None
    raw = run_pipeline(
        cfg,
        input_fn,
        n_samples,
        pool=pool,
        mapper=model.mapper,
        sample_ids=np.arange(n_samples, dtype=np.int64),
        dem_states=states,
        sampled_top_v=0.0,
        sampled_subnode_v=0.0,
        reference_high_v=cfg.v_fs,
        reference_low_v=-cfg.v_fs,
        nominal_reference_high_v=cfg.v_fs,
        nominal_reference_low_v=-cfg.v_fs,
        static_charge_domain=True,
        rdac_noise_cap_f=noise_cap,
    )
    codes = model.nominal_decoder.reconstruct(raw.digital_observation)
    return raw, np.asarray(codes.voltage, dtype=float)


def _dynamic(model, *, dem: bool, uniform_plan: bool, n: int = 8192, fin: float = 1.0e6):
    """相干正弦与 -60 dBFS 小信号，给出 SNDR/SNR/SFDR/THD/DR/NSD。"""
    tone_bin = int(round(fin / (FS_HZ / n)))
    fin_coherent = tone_bin * FS_HZ / n
    t = np.arange(n) / FS_HZ
    amplitude = V_FS * 0.999
    _, full = _run(
        model,
        lambda _t: amplitude * np.sin(2 * np.pi * fin_coherent * _t),
        n,
        dem=dem,
        uniform_plan=uniform_plan,
    )
    _, small = _run(
        model,
        lambda _t: (V_FS * 1e-3) * np.sin(2 * np.pi * fin_coherent * _t),
        n,
        dem=dem,
        uniform_plan=uniform_plan,
    )
    full_metrics = metrics.sine_fit_metrics(full, FS_HZ, fin_coherent)
    small_metrics = metrics.sine_fit_metrics(small, FS_HZ, fin_coherent)
    noise_rms = float(small_metrics["noise_rms"])
    dr_db = float(20 * np.log10((V_FS / np.sqrt(2)) / max(noise_rms, 1e-30)))
    return {
        "fin_hz": float(fin_coherent),
        "n_samples": int(n),
        "sndr_db": float(full_metrics["SNDR_dB"]),
        "snr_db": float(full_metrics["SNR_dB"]),
        "sfdr_db": float(full_metrics["SFDR_dB"]),
        "thd_db": float(full_metrics["THD_dB"]),
        "enob": float(full_metrics["ENOB"]),
        "nsd_v_rthz_full": float(full_metrics["NSD_V_rtHz"]),
        "dr_db": dr_db,
        "nsd_v_rthz_dr": float(noise_rms / np.sqrt(FS_HZ / 2)),
        "noise_rms_small_v": noise_rms,
        "coherent": bool(full_metrics["coherent"]),
        "harmonics_reliable": bool(full_metrics["harmonics_reliable"]),
    }


def _static(model, *, dem: bool, uniform_plan: bool, points: int = 1024, repeats: int = 8):
    """慢斜坡：采样式 INL（端点拟合）+ 256 LSB20 分箱 DNL。"""
    levels = np.linspace(-0.995 * V_FS, 0.995 * V_FS, points)
    step = levels[1] - levels[0]
    mu = np.zeros(points)
    sd = np.zeros(points)
    for index, level in enumerate(levels):
        def run_fn(_level, count):
            return _run(
                model,
                lambda _t, value=float(_level): np.full_like(np.asarray(_t, dtype=float), value),
                count,
                dem=dem,
                uniform_plan=uniform_plan,
            )[1]

        out = run_fn(level, repeats)
        mu[index] = out.mean() - level
        sd[index] = out.std(ddof=1)
    inl = metrics.inl_from_mean_error(levels, mu, LSB20)
    steps = np.diff(mu + levels)
    dnl_lsb20 = (steps - step) / LSB20
    bins = np.floor(step / LSB20).astype(int)
    return {
        "points": int(points),
        "repeats": int(repeats),
        "lsb20_v": LSB20,
        "inl_max_lsb20": float(inl["inl_max_lsb"]),
        "inl_max_ppm_fs": float(inl["inl_max_lsb"] * LSB20 / (2 * V_FS) * 1e6),
        "offset_v": float(inl["offset_v"]),
        "gain_err": float(inl["gain_err"]),
        "sigma_e_mean_lsb20": float(np.mean(sd) / LSB20),
        "dnl_bin_lsb20_max": float(np.max(np.abs(dnl_lsb20))),
        "dnl_bin_span_lsb20": int(bins),
        "dnl_bin_note": "differential metric at the ramp step granularity, not 20b code-density DNL",
    }


def _configs() -> list[dict]:
    """消融矩阵定义；每条都写明改了什么、没改什么。"""
    return [
        {
            "id": "A0_ideal",
            "label": "理想：零失配、零噪声",
            "mismatch_std": 0.0,
            "noise": False,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A1_mismatch",
            "label": "仅失配：sigma=1e-3，无噪声、无 DEM",
            "mismatch_std": 1e-3,
            "noise": False,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A2_mismatch_noise",
            "label": "失配 + 热噪声（kT/C + RA）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A3_dem_rotation",
            "label": "A2 + 三维 DEM 掩码（横向按物理 ID、纵向按组内位置）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "status": "RUN",
        },
        {
            "id": "A4_dem_uniform_plan",
            "label": "A3 + 18 选 8 连续窗轮转（片选维度）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": True,
            "uniform_plan": True,
            "status": "RUN",
        },
        {
            "id": "A5_calibration",
            "label": "A2 + 冻结权值校准",
            "status": "NOT_RUN",
            "blocking_reason": (
                "需要独立静态训练集与 mode_id 冻结/重载五步流程；本轮未构造训练/留出对，"
                "不用名义权重冒充已校准"
            ),
        },
        {
            "id": "A6_dither_in_loop",
            "label": "A2 + 环内 dither（六位字）",
            "status": "NOT_RUN",
            "blocking_reason": (
                "旧 Fig19 路径要求 uniform512 码本与 identity 解码，与 63+8 分段池冲突；"
                "电荷口版本已在 dither_port 建立，但尚未接进主 pipeline 的逐笔循环"
            ),
        },
        {
            "id": "A7_full_dynamic",
            "label": "全动态：参考建立 + RA 单极点 + ADC2 宽/窄带",
            "status": "NOT_RUN",
            "blocking_reason": (
                "ConversionEngine 已具备三者，但逐事件参考程序要求全部事件时刻与"
                "初始节点电压绑定；本轮未冻结该组物理参数，不用默认值猜"
            ),
        },
    ]


def _build_model(mismatch_std: float, noise: bool) -> DefaultModel:
    """按配置构造同一 seed 的制造实现；噪声开关走 Config 覆盖。"""
    parameters = DefaultParameters(
        unit_mismatch_std=mismatch_std,
        bridge_mismatch_std=mismatch_std,
        feedback_mismatch_std=0.0,
    )
    overrides = {}
    if not noise:
        overrides = {
            "ra_enable_noise": False,
            "ra_out_noise_rms": 0.0,
            "enable_sampling_noise": False,
        }
    return DefaultModel(seed=20261105, parameters=parameters, config_overrides=overrides or None)


def main() -> int:
    parser = argparse.ArgumentParser(description="P4 ablation matrix")
    parser.add_argument("--out", default="docs/reviews/20261113_p3p4/data")
    parser.add_argument("--dynamic-n", type=int, default=8192)
    parser.add_argument("--static-points", type=int, default=512)
    parser.add_argument("--static-repeats", type=int, default=8)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    for spec in _configs():
        row = dict(spec)
        if spec["status"] != "RUN":
            rows.append(row)
            continue
        started = time.time()
        model = _build_model(spec["mismatch_std"], spec["noise"])
        row["static"] = _static(
            model,
            dem=spec["dem"],
            uniform_plan=spec["uniform_plan"],
            points=args.static_points,
            repeats=args.static_repeats,
        )
        row["dynamic"] = _dynamic(
            model,
            dem=spec["dem"],
            uniform_plan=spec["uniform_plan"],
            n=args.dynamic_n,
        )
        row["runtime_s"] = round(time.time() - started, 2)
        row["evidence_level"] = "L1 behavioral (float), no RTL/post-layout/silicon"
        rows.append(row)
        print(
            f"{row['id']:<22} INL={row['static']['inl_max_lsb20']:8.1f} LSB20 "
            f"SNDR={row['dynamic']['sndr_db']:6.2f} dB DR={row['dynamic']['dr_db']:6.2f} dB "
            f"({row['runtime_s']}s)"
        )

    document = {
        "profile": "adi_isscc2024_40m_6vpp",
        "fs_hz": FS_HZ,
        "v_fs_v": V_FS,
        "lsb20_v": LSB20,
        "evidence_level": "L1 behavioral; not RTL, not post-layout, not silicon",
        "paper_anchor": PAPER,
        "configs": rows,
        "notes": [
            "所有模型数字来自浮点行为模型，未经过 RTL 或后仿",
            "校准、环内 dither、全动态三条未运行，见 blocking_reason",
            "论文值与模型值不混排：paper_anchor 是独立字典",
        ],
    }
    (out / "p4_ablation.json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")

    header = ["config", "status", "inl_lsb20", "dnl_bin_lsb20", "sndr_db", "snr_db", "sfdr_db", "dr_db", "nsd_nv"]
    lines = [",".join(header)]
    for row in rows:
        if row["status"] != "RUN":
            lines.append(f"{row['id']},NOT_RUN,,,,,,,")
            continue
        dynamic = row["dynamic"]
        lines.append(
            ",".join(
                [
                    row["id"],
                    "RUN",
                    f"{row['static']['inl_max_lsb20']:.2f}",
                    f"{row['static']['dnl_bin_lsb20_max']:.2f}",
                    f"{dynamic['sndr_db']:.2f}",
                    f"{dynamic['snr_db']:.2f}",
                    f"{dynamic['sfdr_db']:.2f}",
                    f"{dynamic['dr_db']:.2f}",
                    f"{dynamic['nsd_v_rthz_dr'] * 1e9:.2f}",
                ]
            )
        )
    (out / "p4_ablation.csv").write_text("\n".join(lines) + "\n")
    print(f"\nwritten: {out/'p4_ablation.json'} and p4_ablation.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
