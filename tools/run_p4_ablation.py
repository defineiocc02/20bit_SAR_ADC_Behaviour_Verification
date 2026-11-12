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
from dataclasses import replace
from pathlib import Path

import numpy as np

from adi_model import metrics
from adi_model.calibration.mask_calibration import fit_code_correction
from adi_model.control.dem3d import Dem3State, round_robin_plan, unary_rotation_masks
from adi_model.control.dither import FIG19_FRACTIONAL_WEIGHTS_Q, Fig19StaticSpec
from adi_model.control.dither_port import DitherPort
from adi_model.defaults import DefaultModel, DefaultParameters, _schedule
from adi_model.simulation.pipeline import run_pipeline
from adi_model.simulation.slice_pool import PhysicalSlicePool

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


def _ramp_fn(values):
    """把逐笔电平序列变成**逐笔恒定**的 input_fn（零阶保持）。

    不能用线性插值：孔径时刻不保证落在样本格点上，插值会让"训练参考电平"与
    实际采样值差最多一个斜坡步长，拟合残差被这个系统误差吃掉
    （实测残差 833 uV，其中大部分不是器件误差）。零阶保持使第 i 笔的采样值
    严格等于 values[i]，训练参考才是真正的已知输入。
    """
    table = np.asarray(values, dtype=float)
    last = table.size - 1
    # 用 rint 而不是 floor：孔径时刻是 i/fs，浮点乘回来可能是 6334.99999999，
    # floor 会取到前一笔——对**乱序**电平表来说就是取到一个完全无关的电压
    # （实测造成 5.5 V 级误差，且只在部分样本上出现）。
    def fn(t):
        index = np.rint(np.asarray(t, dtype=float) * FS_HZ).astype(np.int64)
        return table[np.clip(index, 0, last)]

    return fn


def _dither_port(model, cfg, words) -> DitherPort:
    """按名义全 Signal 归一化电容构造独立电荷口（v2）。

    `c_lsb_f = C_norm/512` 是工程绑定：它让输入等效步长与 `spec.coarse_step_v`
    自洽；逐片失配由逐笔 C_norm 承担，不用标称值掩盖。
    """
    chip = model.chip
    active = np.arange(chip.C_main.shape[0], dtype=np.int64)
    main, sub = chip.C_main[active], chip.C_sub[active]
    beta = chip.C_bridge[active] / (
        chip.C_bridge[active] + sub.sum(axis=-1) + chip.c_p_sub[active]
    )
    c_norm = float(np.mean((main.sum(axis=-1) + beta * sub.sum(axis=-1)).sum(axis=-1)))
    spec = Fig19StaticSpec(
        v_fs=cfg.v_fs,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="P4 ablation: independent charge port at the sampling node (ASSUMED)",
    )
    return DitherPort.from_words(
        words, spec=spec, c_lsb_f=c_norm / 512.0, reference_swing_v=2.0 * cfg.v_fs
    )


def _run(model, input_fn, n_samples: int, *, dem: bool, uniform_plan: bool, calibration=None,
         dither_words=None):
    """执行一次显式配置；DEM 打开时用旋转掩码，否则复用默认分段掩码。

    calibration 给定时用它替换名义解码（`FrozenCalibration.reconstruct`），
    返回的电压是校准后的浮点重构，不是 20bit 整数码。

    dither_words 给定时走 v2 独立电荷口：RDAC 码保持真实判决，dither 电容
    在采样节点注入 T=L+U*Δ，数字侧扣回 -T。与 v1 的 k=c-U 不是同一条拓扑。
    """
    from adi_model.defaults import _all_signal, _segmented_mask

    cfg = replace(model.cfg, dem_enable=dem, dither_enable=dither_words is not None)
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
    dither_port = None if dither_words is None else _dither_port(model, cfg, dither_words)
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
        dither_port=dither_port,
    )
    codes = model.nominal_decoder.reconstruct(raw.digital_observation)
    voltage = np.asarray(codes.voltage, dtype=float)
    if calibration is not None:
        voltage = calibration.apply(np.asarray(raw.coarse, dtype=np.int64), voltage)
        raw.out = voltage
        raw.err = voltage - np.asarray(raw.x_ref, dtype=float)
    return raw, voltage


def _dynamic(model, *, dem: bool, uniform_plan: bool, n: int = 8192, fin: float = 1.0e6,
             calibration=None, dither_seed: int | None = None, dither_headroom_v: float = 0.0):
    """相干正弦与 -60 dBFS 小信号，给出 SNDR/SNR/SFDR/THD/DR/NSD。

    满幅幅度扣除 dither 占用的范围，避免把 SADC 削顶误读成失真。
    """
    tone_bin = int(round(fin / (FS_HZ / n)))
    fin_coherent = tone_bin * FS_HZ / n
    t = np.arange(n) / FS_HZ
    amplitude = (V_FS - dither_headroom_v) * 0.999
    # dither 字用**独立** Generator，不改变噪声序列；同一组字同时用于
    # 满幅与小信号，保证两条记录的 dither 条件一致。
    words = None
    if dither_seed is not None:
        words = np.random.default_rng(int(dither_seed)).integers(0, 64, n, dtype=np.int64)
    _, full = _run(
        model,
        lambda _t: amplitude * np.sin(2 * np.pi * fin_coherent * _t),
        n,
        dem=dem,
        uniform_plan=uniform_plan,
        calibration=calibration,
        dither_words=words,
    )
    _, small = _run(
        model,
        lambda _t: (V_FS * 1e-3) * np.sin(2 * np.pi * fin_coherent * _t),
        n,
        dem=dem,
        uniform_plan=uniform_plan,
        calibration=calibration,
        dither_words=words,
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


def _dither_max_v(cfg) -> float:
    """64 个字里最大的 |T| [V]：dither 占掉的输入范围。"""
    words = np.arange(64, dtype=np.int64)
    spec = Fig19StaticSpec(
        v_fs=cfg.v_fs,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="range ledger for the P4 ablation",
    )
    from adi_model.control.dither import Fig19DitherProgram

    return float(Fig19DitherProgram.from_words(words, spec=spec).total_v.max())


def _static(model, *, dem: bool, uniform_plan: bool, points: int = 1024, repeats: int = 8,
            calibration=None, offset_v: float = 0.0, dither_seed: int | None = None,
            dither_headroom_v: float = 0.0):
    """慢斜坡：采样式 INL（端点拟合）+ 256 LSB20 分箱 DNL。

    dither_seed 给定时每个电平用**独立**字序列（专门的 Generator，
    不碰噪声流），因此同一电平的 repeats 笔覆盖不同 dither 字。

    dither_headroom_v 是 dither 占掉的输入范围 [V]：SADC 看 x−T，所以
    可用输入必须收缩这么多，否则边界电平会削顶——这是技术的真实代价，
    不是可以忽略的数值细节，因此显式从扫描范围里扣掉并记录。
    """
    span = 0.985 * V_FS - dither_headroom_v
    levels = np.linspace(-span, span, points) + offset_v
    step = levels[1] - levels[0]
    mu = np.zeros(points)
    sd = np.zeros(points)
    for index, level in enumerate(levels):
        words = None
        if dither_seed is not None:
            words = np.random.default_rng(
                np.random.SeedSequence([int(dither_seed), int(index)])
            ).integers(0, 64, repeats, dtype=np.int64)

        def run_fn(_level, count, _words=words):
            return _run(
                model,
                lambda _t, value=float(_level): np.full_like(np.asarray(_t, dtype=float), value),
                count,
                dem=dem,
                uniform_plan=uniform_plan,
                calibration=calibration,
                dither_words=_words,
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
        "input_span_v": float(2 * span),
        "input_span_loss_v": float(2 * dither_headroom_v),
        "dither_active": dither_seed is not None,
    }


def _train_calibration(model, *, points: int = 8200):
    """用独立已知斜坡拟合逐码修正表；返回 (CodeCorrection, 训练诊断)。

    这是论文里 "coefficients derived externally" 的那一步在输出域的实现：
    激励是已知输入，拟合量是 512 个粗码各自的平均输出误差。

    不做逐单元权重：残差域里 G*C*x 与 G*Q_DAC 各约 96 V、相减后只剩 0.1 V，
    逐单元权值在这种抵消下不可辨识（见 mask_calibration 模块 docstring 与负结果）。
    """
    # 必须覆盖全部 512 个粗码：±0.99 FS 会漏掉 0/1/510/511 四个端点码，
    # 而逐码表不允许对未访问码插值或补零。
    grid = np.linspace(-0.9995 * V_FS, 0.9995 * V_FS, points)
    # 打乱顺序避免"码"与"片选序列"共线；叠加已知小偏置打散 ADC2 量化锯齿。
    generator = np.random.default_rng(20261113)
    levels = generator.permutation(grid) + generator.uniform(-3e-4, 3e-4, points)
    raw, estimated = _run(model, _ramp_fn(levels), points, dem=False, uniform_plan=False)
    correction = fit_code_correction(
        np.asarray(raw.coarse, dtype=np.int64), estimated, levels
    )
    return correction, {
        "training_samples": correction.n_samples,
        "covered_codes": correction.covered_codes,
        "min_samples_per_code": correction.min_count,
        "residual_rms_uv": correction.residual_rms_v * 1e6,
        "training_levels": int(points),
        "identifiable": "per-code output correction; per-element weights are NOT identifiable",
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
            "label": "A2 + 冻结有效权重校准（外部已知斜坡拟合，同一条留出记录上前后对比）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "calibration": True,
            "status": "RUN",
        },
        {
            "id": "A6_dither_in_loop",
            "label": "A2 + 独立电荷口 dither（六位字，采样节点注入，码不偏移）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": False,
            "uniform_plan": False,
            "dither": True,
            "status": "RUN",
        },
        {
            "id": "C1_dem_cal",
            "label": "A2 + 三维 DEM + 逐码校准",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "calibration": True,
            "status": "RUN",
        },
        {
            "id": "C2_dem_dither",
            "label": "A2 + 三维 DEM + dither（电荷口）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "dither": True,
            "status": "RUN",
        },
        {
            "id": "C3_all",
            "label": "A2 + DEM + dither + 逐码校准（全组合）",
            "mismatch_std": 1e-3,
            "noise": True,
            "dem": True,
            "uniform_plan": False,
            "dither": True,
            "calibration": True,
            "status": "RUN",
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


def _build_model(mismatch_std: float, noise: bool, *, seed: int = 20261105) -> DefaultModel:
    """按配置构造同一 seed 的制造实现；噪声开关走 Config 覆盖。

    seed 决定制造池与噪声相位；同一 seed 下所有配置共用同一颗"芯片"。
    """
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
    return DefaultModel(seed=seed, parameters=parameters, config_overrides=overrides or None)


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
        # dither 字流与控制/噪声流分开：固定 20261113，只被 A6 消费。
        dither_seed = 20261113 if spec.get("dither") else None
        headroom_v = _dither_max_v(model.cfg) if dither_seed is not None else 0.0
        calibration = None
        held_out_offset = 0.0
        if spec.get("calibration"):
            calibration, training = _train_calibration(model)
            row["training"] = training
            step = 2 * 0.985 * V_FS / max(args.static_points - 1, 1)
            held_out_offset = 0.37 * step
            row["static_before"] = _static(
                model,
                dem=spec["dem"],
                uniform_plan=spec["uniform_plan"],
                points=args.static_points,
                repeats=args.static_repeats,
                offset_v=held_out_offset,
                dither_seed=dither_seed,
                dither_headroom_v=headroom_v,
            )
            row["dynamic_before"] = _dynamic(
                model,
                dem=spec["dem"],
                uniform_plan=spec["uniform_plan"],
                n=args.dynamic_n,
                dither_seed=dither_seed,
                dither_headroom_v=headroom_v,
            )
        row["static"] = _static(
            model,
            dem=spec["dem"],
            uniform_plan=spec["uniform_plan"],
            points=args.static_points,
            repeats=args.static_repeats,
            calibration=calibration,
            offset_v=held_out_offset,
            dither_seed=dither_seed,
            dither_headroom_v=headroom_v,
        )
        row["dynamic"] = _dynamic(
            model,
            dem=spec["dem"],
            uniform_plan=spec["uniform_plan"],
            n=args.dynamic_n,
            calibration=calibration,
            dither_seed=dither_seed,
            dither_headroom_v=headroom_v,
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
            "A7 全动态未运行，见 blocking_reason；A6 只覆盖静态连接下的电荷口 dither",
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
