# =============================================================================
# 文件名     : experiment.py
# 模块名     : experiment
# 版本       : v1.0
# 功能       : 八片原生子阵列扰动的固定失配、LMS 和 20 位性能对照。
#
# 设计说明   :
#   * 承担 八片原生子阵列模型与对照实验 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""八片原生子阵列扰动的固定失配、LMS 和 20 位性能对照。"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

import numpy as np

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.hashing import sha256_file
from adi_model.noise import NoiseConfig, front_end_noise
from task1.experiments.adctoolbox_calibration import (
    CalibrationConfig,
    _fit_gain_from_flags,
    _spectrum,
    decode_switches,
    group_flags,
    quantize,
    range_report,
)
from task1.experiments.bridge_lms_comparison import _fit_observable
from task1.experiments.inl_dnl import (
    BRIDGE_SEEDS,
    OUTPUT_BITS,
    Intervals,
    _first_fine_code,
    _output_codes,
    code_mass,
)
from task1.experiments.residue_control_study import MapperTable
from task1.experiments.run_effects import ROOT, schedule_sequence
from task1.model.codebook import codebook
from task2.eight.model import (
    COARSE_CODES,
    FEATURES_PER_SLICE,
    ChargePool,
    coarse_decision,
    conversion_state,
    decode,
    fit_features,
    native_step_v,
    weighted_sum,
    words,
)
from task2.model.dither_word_gen import DEFAULT_DITHER_SEED

STATIC_INPUT_LIMIT_V = 3.671875
STATIC_OUTPUT_GUARD_V = 0.075
STATIC_WORD_SEED = 0x41425232
TRAIN_MASK_SEED = 0x14269
TRAIN_WORD_SEED = 0x16DE17E9
STATES_PER_SEED = 64
COARSE_BATCH = 8
N_CODES = 1 << OUTPUT_BITS
GROUPS = ("no_dem", "fig21_dem")
LMS_MODES = ("nominal", "lms")
SWITCHES = ("off", "on")

# 噪声序列编号。同一批样本被重跑多次（dither off/on、LMS shared/old_off），
# 这些对照必须看到同一份噪声，否则差异里混进的是噪声而不是阵列配置；
# 不同激励序列（评估/训练/静态边缘）之间必须错开，否则等于复用同一段噪声。
STREAM_TONE = 1
STREAM_TRAIN = 2
STREAM_EDGE = 3


# =========================================================================
# 1. 函数组：_capture / _calibrate_gain / _fit_weights
# =========================================================================
# ------------------------------------------------------------------------
def _capture(input_v, word, plan, phase, thresholds, step_v, table, physical, cfg, noise=None):
    """把一笔输入跑完前端，返回转换状态、粗码、残差与二级码。

    ``noise`` 为 ``FrontEndNoise`` 或 None。三个分量注在三个不同节点上：

    * ``sampling_v`` 加在输入上，粗码与残差看到同一份——kT/C 落在采样电容上
      之后被后续所有操作继承，不存在「判决看到、残差看不到」的分叉；
    * ``comparator_v`` 只加到 SADC 判决输入，**不进残差**，否则会被后级重构
      吸收，等于这个源不存在；
    * ``conversion_v`` 与 ``offset_v`` 只加到残差上，判决时它们还不存在。

    ``noise`` 为 None 或全零时整条噪声路径都不执行，返回与无噪声版本逐位相同。
    """
    dither = decode(word)
    active = noise is not None and not noise.is_quiet
    sampled_v = input_v + noise.sampling_v if active else input_v
    decision_v = sampled_v + noise.comparator_v if active else None
    coarse, command, valid = coarse_decision(
        sampled_v, dither, thresholds, step_v, decision_v=decision_v
    )
    if not valid.all():
        raise ValueError(f"front-end rejected {np.count_nonzero(~valid)} sample(s)")
    state = conversion_state(command, plan, phase, dither, thresholds, step_v, table)
    g, b = physical.affine(plan, state.features)
    residue = g * (sampled_v + thresholds[-1]) - b
    if active:
        residue = residue + noise.offset_v + noise.conversion_v
    code = quantize(residue, cfg.adc2_bits, cfg.adc2_half_range_v)
    return state, coarse, residue, code


# ------------------------------------------------------------------------
def _calibrate_gain(physical, experiment, cfg, table, thresholds, step_v):
    plan, _, phase = schedule_sequence(
        cfg.gain_pairs, replace(experiment, schedule_seed=cfg.gain_schedule_seed)
    )
    reference_rng = np.random.default_rng(cfg.gain_reference_seed)
    midpoint = cfg.gain_reference_midpoint_span_v * reference_rng.uniform(-0.5, 0.5, len(plan))
    low_v = midpoint - 0.5 * cfg.gain_reference_span_v
    high_v = midpoint + 0.5 * cfg.gain_reference_span_v
    # 强制码和实际位图在两次前台读取之间保持不变，采样子阵列全部接低轨。
    state = conversion_state(
        np.full(len(plan), cfg.forced_gain_code),
        plan,
        phase,
        decode(np.zeros(len(plan), dtype=np.uint8)),
        thresholds,
        step_v,
        table,
    )
    g, b = physical.affine(plan, state.features)
    r_lo = g * (low_v + thresholds[-1]) - b
    r_hi = g * (high_v + thresholds[-1]) - b
    lo_code = quantize(r_lo, cfg.adc2_bits, cfg.adc2_half_range_v)
    hi_code = quantize(r_hi, cfg.adc2_bits, cfg.adc2_half_range_v)
    estimate, report = _fit_gain_from_flags(
        group_flags(plan), low_v, high_v, lo_code, hi_code, cfg
    )
    arrays = {
        "gain_plan": plan,
        "gain_horizontal": phase[0],
        "gain_vertical": phase[1],
        "gain_low_reference_v": low_v,
        "gain_high_reference_v": high_v,
        "gain_low_adc2_code": lo_code,
        "gain_high_adc2_code": hi_code,
        "frozen_gain": estimate,
    }
    report["low_residue_v"] = range_report(r_lo, cfg.adc2_half_range_v)
    report["high_residue_v"] = range_report(r_hi, cfg.adc2_half_range_v)
    return estimate, report, arrays


# ------------------------------------------------------------------------
def _fit_weights(input_v, state, raw_code, gain, holdout, cfg, peak):
    physical_bits = fit_features(state.switches, state.features)
    known_target_v = gain[state.switches.plan].sum(axis=1) * (input_v + peak) - (
        state.nominal_base_v + decode_switches(raw_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    )
    weights, rank, learning = _fit_observable(physical_bits, holdout, known_target_v, cfg)
    observed = np.any(physical_bits != 0, axis=0)
    weights[~observed] = 0.0
    return weights.reshape(-1, FEATURES_PER_SLICE), {"design": rank, "learning": learning}, observed


# =========================================================================
# 2. 函数组：_output / calibration_metrics / _tones
# =========================================================================
# ------------------------------------------------------------------------
def _output(state, raw_code, cfg, peak, gain=None, weight=None):
    base = state.nominal_base_v + decode_switches(raw_code, cfg.adc2_bits, cfg.adc2_half_range_v)
    if gain is not None:
        plan = state.switches.plan
        base = (base + weighted_sum(plan, state.features, weight)) / gain[plan].sum(axis=1)
    v = base - peak
    code = _output_codes(v, peak)
    if np.any((code < 0) | (code >= N_CODES)):
        raise ValueError("output rejected on independent tone; a clipped FFT is inadmissible")
    midpoint_v = (code + 0.5) * (2 * peak / N_CODES) - peak
    return code, midpoint_v


# ------------------------------------------------------------------------
def calibration_metrics(input_v, output_v, experiment):
    """用 adctoolbox 频谱与自算 FFT 功率互校，返回 SNDR/SFDR/主杂散与输出误差。"""
    toolbox = _spectrum(output_v, experiment)
    power = np.abs(np.fft.rfft(output_v)) ** 2
    tone_bin = experiment.tone_bin
    tone_power = power[tone_bin]
    power[0] = power[tone_bin] = 0.0
    power[-1] *= 0.5
    sndr = 10 * np.log10(tone_power / power.sum())
    sfdr = 10 * np.log10(tone_power / power.max())
    t_metrics = toolbox["metrics"]
    np.testing.assert_allclose(
        [sndr, sfdr], [t_metrics["sndr_dbc"], t_metrics["sfdr_dbc"]], atol=1e-5, rtol=0
    )
    return {
        "sndr_db": float(sndr),
        "sfdr_dbc": float(sfdr),
        "spur_hz": float(np.argmax(power) * experiment.fs_hz / experiment.samples),
        "output_error_rms_v": float(np.sqrt(np.mean((output_v - input_v) ** 2))),
    }, toolbox["plot_data"]["power_spectrum_db_plot"]


# ------------------------------------------------------------------------
def _tones(physical, table, experiment, cfg, thresholds, step_v, gain, arrays):
    peak = thresholds[-1]
    train_cfg = replace(
        experiment, schedule_seed=cfg.training_schedule_seed, dem_seed=cfg.training_dem_seed
    )
    train_plan, _, train_phase = schedule_sequence(cfg.training_samples, train_cfg)
    train_input = cfg.training_tone_peak_v * np.sin(
        2 * np.pi * cfg.training_tone_bin * np.arange(len(train_plan)) / len(train_plan)
        + cfg.training_tone_phase_rad
    )
    train_enable = np.random.default_rng(TRAIN_MASK_SEED).permutation(len(train_plan)) % 2 == 1
    train_word = words(train_enable, TRAIN_WORD_SEED)
    plan, _, phase = schedule_sequence(experiment.samples, experiment)
    tone_input = experiment.tone_peak_v * np.sin(
        2 * np.pi * experiment.tone_bin * np.arange(len(plan)) / len(plan)
        + cfg.evaluation_tone_phase_rad
    )
    on_word = words(np.ones(len(plan), dtype=bool), DEFAULT_DITHER_SEED)
    captures = {}
    arrays.update(
        tone_reference_v=tone_input,
        tone_plan=plan,
        tone_horizontal=phase[0],
        tone_vertical=phase[1],
        train_reference_v=train_input,
        train_plan=train_plan,
        train_horizontal=train_phase[0],
        train_vertical=train_phase[1],
        train_dither_enable=train_enable,
    )
    tone_noise = front_end_noise(experiment.noise, len(tone_input), stream=STREAM_TONE)
    for label, word in zip(SWITCHES, (np.zeros_like(on_word), on_word), strict=True):
        captures[label] = _capture(
            tone_input, word, plan, phase, thresholds, step_v, table, physical, cfg, tone_noise
        )
    hold_features = {
        label: fit_features(row[0].switches, row[0].features) for label, row in captures.items()
    }
    fits, fit_reports = {}, {}
    train_noise = front_end_noise(experiment.noise, len(train_input), stream=STREAM_TRAIN)
    for label, train_words, hold in (
        ("shared", train_word, np.concatenate(tuple(hold_features.values()))),
        ("old_off", np.zeros_like(train_word), hold_features["off"]),
    ):
        train, coarse, residue, raw = _capture(
            train_input,
            train_words,
            train_plan,
            train_phase,
            thresholds,
            step_v,
            table,
            physical,
            cfg,
            train_noise,
        )
        weights, fit, observed = _fit_weights(train_input, train, raw, gain, hold, cfg, peak)
        fits[label] = weights
        fit_reports[label] = {
            **fit,
            "training_observed_physical_columns": int(observed.sum()),
            "dither_enabled_training_samples": int(train_enable.sum()) if label == "shared" else 0,
            "training_adc2": range_report(residue, cfg.adc2_half_range_v),
        }
        if label == "old_off":
            # 旧校准未辨识的采样位沿用零修正，单列诊断其独立留出。
            new_columns = np.any(hold_features["on"] != 0, axis=0) & ~observed
            fit_reports[label]["unobserved_on_holdout_physical_columns"] = int(new_columns.sum())
        arrays.update(
            {
                f"{label}_frozen_weight_v": weights,
                f"{label}_train_word": train_words,
                f"{label}_train_command": train.switches.codes,
                f"{label}_train_coarse": coarse,
                f"{label}_train_main": train.switches.main,
                f"{label}_train_sub": train.switches.sub,
                f"{label}_train_sample_sub": train.dither.sample_sub_high,
                f"{label}_train_adc2_code": raw,
            }
        )
    report = {}
    for label, (state, coarse, residue, raw_code) in captures.items():
        results = {}
        for cal in (*LMS_MODES, "old_off"):
            fitted = cal != "nominal"
            weight = fits["shared" if cal == "lms" else "old_off"] if fitted else None
            code, output_v = _output(state, raw_code, cfg, peak, gain if fitted else None, weight)
            results[cal], spectrum = calibration_metrics(tone_input, output_v, experiment)
            arrays[f"{label}_{cal}_output_code"] = code.astype(np.uint32)
            arrays[f"{label}_{cal}_fft_dbfs"] = spectrum
        report[label] = {
            **results,
            "adc2": range_report(residue, cfg.adc2_half_range_v),
            "changed_rdac_command": int(np.count_nonzero(state.switches.codes != coarse)),
            "maximum_slot_setting": int(state.dither.setting.max()),
            "slot_setting_eight_count": int(np.count_nonzero(state.dither.setting == 8)),
        }
        arrays.update(
            {
                f"{label}_tone_word": state.dither.word,
                f"{label}_tone_coarse": coarse,
                f"{label}_tone_command": state.switches.codes,
                f"{label}_tone_main": state.switches.main,
                f"{label}_tone_sub": state.switches.sub,
                f"{label}_tone_sample_sub": state.dither.sample_sub_high,
                f"{label}_tone_adc2_code": raw_code,
                f"{label}_tone_adc2_residue_v": residue,
            }
        )
    return report, fits["shared"], fit_reports


# =========================================================================
# 3. 函数组：_static_states / _span_outside / _static
# =========================================================================
# ------------------------------------------------------------------------
def _static_states(experiment):
    plan, horizontal, vertical, word = [], [], [], []
    rng = np.random.default_rng(STATIC_WORD_SEED)
    for schedule_seed, dem_seed in BRIDGE_SEEDS:
        cfg = replace(experiment, schedule_seed=schedule_seed, dem_seed=dem_seed)
        ids, _, (h, v) = schedule_sequence(STATES_PER_SEED, cfg)
        plan.append(ids)
        horizontal.append(h)
        vertical.append(v)
        # 每组同一词域的 64 个字恰好一次，独立打乱后与该组状态配对。
        word.append(rng.permutation(64).astype(np.uint8))
    return tuple(np.concatenate(rows) for rows in (plan, horizontal, vertical, word))


# ------------------------------------------------------------------------
def _span_outside(lo, hi, slope, allowed_lo, allowed_hi):
    return (
        float(np.sum(np.maximum(0, np.minimum(hi, allowed_lo) - lo) / slope)),
        float(np.sum(np.maximum(0, hi - np.maximum(lo, allowed_hi)) / slope)),
    )


# ------------------------------------------------------------------------
def _static(table, physical, experiment, cfg, thresholds, step_v, gain, weight, states, enabled):
    plan0, h0, v0, word_on = states
    word0 = word_on if enabled else np.zeros_like(word_on)
    d0 = decode(word0)
    n = len(plan0)
    peak, half = thresholds[-1], cfg.adc2_half_range_v
    selected_v = STATIC_INPUT_LIMIT_V
    widths = {label: np.zeros(N_CODES) for label in LMS_MODES}
    adc2_full = np.zeros(2)
    output_full = {label: np.zeros(2) for label in LMS_MODES}
    front_width = main_front_width = 0.0
    common_lo, common_hi = float("inf"), float("-inf")
    for start in range(0, COARSE_CODES, COARSE_BATCH):
        coarse = np.repeat(np.arange(start, min(start + COARSE_BATCH, COARSE_CODES)), n)
        i = np.arange(len(coarse)) % n
        lower_shift = d0.lower_eighth[i] * (step_v / 8)
        # 负注入（C9）：SADC 判的是 x−L，所以粗码 c 的输入区间整段右移 +L；
        # 命令再去掉高路 U，c≥U 才合法，c<U 的下溢样本在这一层就被挡掉。
        low = np.maximum(-peak, thresholds[coarse] + lower_shift)
        high = np.minimum(peak, thresholds[coarse + 1] + lower_shift)
        command = coarse - d0.upper[i]
        legal = (command >= 0) & (high > low)
        i, command, low, high = (value[legal] for value in (i, command, low, high))
        plan = plan0[i]
        state = conversion_state(
            command, plan, (h0[i], v0[i]), decode(word0[i]), thresholds, step_v, table
        )
        g_act, b_act = physical.affine(plan, state.features)
        front_width += float(np.sum(high - low)) / n
        full_low = g_act * (low + peak) - b_act
        full_high = g_act * (high + peak) - b_act
        adc2_full += np.asarray(_span_outside(full_low, full_high, g_act, -half, half)) / n
        clip_low, clip_high = np.maximum(low, -selected_v), np.minimum(high, selected_v)
        use = clip_high > clip_low
        lo, hi = clip_low[use], clip_high[use]
        r_lo = g_act[use] * (lo + peak) - b_act[use]
        r_hi = g_act[use] * (hi + peak) - b_act[use]
        main_front_width += float(np.sum(hi - lo)) / n
        common_lo = min(common_lo, float(r_lo.min(initial=np.inf)))
        common_hi = max(common_hi, float(r_hi.max(initial=-np.inf)))
        for mode in LMS_MODES:
            base = state.nominal_base_v
            gain_fit = np.ones(len(plan))
            if mode == "lms":
                base = base + weighted_sum(plan, state.features, weight)
                gain_fit = gain[plan].sum(axis=1)
            # ADC2 已合法的部分按输出边界反解，单列全幅输出越界。
            adc_lo, adc_hi = np.clip(full_low, -half, half), np.clip(full_high, -half, half)
            adc_step = 2 * half / (1 << cfg.adc2_bits)
            fine_bounds = [
                np.clip(
                    _first_fine_code(
                        np.full(len(plan), edge), base, gain_fit, cfg.adc2_bits, half, peak
                    ),
                    0,
                    1 << cfg.adc2_bits,
                )
                * adc_step
                - half
                for edge in (0, N_CODES)
            ]
            output_full[mode] += np.asarray(_span_outside(adc_lo, adc_hi, g_act, *fine_bounds)) / n
            if not np.any(use):
                continue
            iv = Intervals(lo, hi, r_lo, r_hi, base[use], gain_fit[use])
            mass = code_mass(iv, cfg.adc2_bits, half, peak)
            if (
                max(mass.adc2_below_v, mass.adc2_above_v, mass.output_below_v, mass.output_above_v)
                > 1e-14
            ):
                raise ValueError(f"common comparison window rejected ADC2 or output, mode {mode}")
            widths[mode] += np.bincount(mass.code, weights=mass.width_v, minlength=N_CODES) / n
    if not np.isclose(main_front_width, 2 * selected_v, atol=2e-12, rtol=0):
        raise ValueError("common static window contains rejected front-end intervals")
    step = 2 * peak / N_CODES
    trim = STATIC_OUTPUT_GUARD_V
    first, stop = np.ceil((np.array([-selected_v + trim, selected_v - trim]) + peak) / step).astype(
        int
    )
    edge = np.tile([-selected_v, np.nextafter(selected_v, -np.inf)], n)
    edge_plan = np.repeat(plan0, 2, axis=0)
    edge_phase = np.repeat(h0, 2), np.repeat(v0, 2)
    edge_state, _, _, edge_raw = _capture(
        edge,
        np.repeat(word0, 2),
        edge_plan,
        edge_phase,
        thresholds,
        step_v,
        table,
        physical,
        cfg,
        front_end_noise(experiment.noise, len(edge), stream=STREAM_EDGE),
    )
    report = {}
    for mode in LMS_MODES:
        width = widths[mode]
        err = float(width.sum() - 2 * selected_v)
        if abs(err) > 1e-11:
            raise ValueError(f"ramp mass did not close: {err:g} V")
        fitted = mode == "lms"
        edge_codes, _ = _output(edge_state, edge_raw, cfg, peak, gain if fitted else None, weight)
        edge_below_max, edge_above_min = edge_codes[::2].max(), edge_codes[1::2].min()
        if not edge_below_max < first < stop <= edge_above_min:
            raise ValueError("an evaluated output code falls outside an endpoint guard")
        dnl, inl = linearity(width, first, stop, step)
        report[mode] = {
            "evaluated_first_unsigned_code": int(first),
            "evaluated_stop_unsigned_code": int(stop),
            "evaluated_code_count": int(stop - first),
            "max_lower_edge_code": int(edge_below_max),
            "min_upper_edge_code": int(edge_above_min),
            "dnl_rms_lsb20": float(np.sqrt(np.mean(dnl**2))),
            "dnl_min_lsb20": float(dnl.min()),
            "dnl_max_lsb20": float(dnl.max()),
            "endpoint_inl_peak_lsb20": float(np.abs(inl).max()),
            "zero_width_codes": int(np.count_nonzero(width[first:stop] <= 1e-15)),
            "mass_balance_error_v": err,
            "full_scale_front_end_rejected_width_v": max(0.0, 2 * peak - front_width),
            "full_scale_adc2_rejected_width_v": adc2_full.tolist(),
            "full_scale_output_rejected_width_v": output_full[mode].tolist(),
            "common_adc2_residue_bounds_v": [common_lo, common_hi],
        }
    return report, widths


# =========================================================================
# 4. 函数组：linearity / run / main
# =========================================================================
# -------------------------------------------------------------------------
# 逐个输出码按名义 20 位 LSB 归一，INL 扣除端点直线。
# 运行一个固定制造样本的两组 DEM、LMS 和 dither 交叉实验。
# 运行指定 σ 的实验并写入独立目录。
# ------------------------------------------------------------------------
def linearity(width_v, first, stop, step):
    """逐个输出码按名义 20 位 LSB 归一，INL 扣除端点直线。"""
    dnl = width_v[first:stop] / step - 1
    cumulative = np.r_[0.0, np.cumsum(dnl)]
    return dnl, cumulative - np.linspace(0.0, cumulative[-1], len(cumulative))


# ------------------------------------------------------------------------
def run(output, sigma, seed, noise=None):
    """运行一个固定制造样本的两组 DEM、LMS 和 dither 交叉实验。

    ``noise`` 取 ``NoiseConfig`` 或 None。None 用默认配置，即所有噪声源关闭，
    结果与 M3 之前的版本逐位相同；传入配置才能看到噪声对 DR/SNR 的影响。
    """
    output.mkdir(parents=True, exist_ok=False)
    experiment = replace(
        ExperimentConfig(),
        mismatch_sigma_unit=sigma,
        manufacturing_seed=seed,
        noise=NoiseConfig() if noise is None else noise,
    )
    cfg = replace(CalibrationConfig(), adc2_half_range_v=0.100, training_tone_peak_v=4.75)
    nominal, caps = cap_pool(experiment)
    physical = ChargePool.from_caps(caps, experiment)
    thresholds = np.r_[codebook(nominal, experiment), experiment.full_scale_peak_v]
    step_v = native_step_v(experiment)
    tables = {label: MapperTable.build(bridge=label == "fig21_dem") for label in GROUPS}
    gain, gain_report, arrays = _calibrate_gain(
        physical, experiment, cfg, tables["fig21_dem"], thresholds, step_v
    )
    states = _static_states(experiment)
    arrays.update(
        static_plan=states[0],
        static_horizontal=states[1],
        static_vertical=states[2],
        static_on_word=states[3],
        nominal_threshold_v=thresholds,
        actual_main_cap_f=caps.main,
        actual_sub_cap_f=caps.sub,
        actual_bridge_cap_f=caps.bridge,
    )
    comparisons = {}
    for label, table in tables.items():
        group_arrays = {}
        tone, weight, fit = _tones(
            physical, table, experiment, cfg, thresholds, step_v, gain, group_arrays
        )
        statics = {}
        for key in SWITCHES:
            statics[key], widths = _static(
                table,
                physical,
                experiment,
                cfg,
                thresholds,
                step_v,
                gain,
                weight,
                states,
                enabled=key == "on",
            )
            group_arrays.update({f"{key}_{mode}_width_v": width for mode, width in widths.items()})
        arrays.update({f"{label}_{key}": val for key, val in group_arrays.items()})
        comparisons[label] = {"fit": fit, "tone": tone, "linearity": statics}
    data_path = output / "data.npz"
    np.savez_compressed(data_path, **arrays)
    sources = (
        __file__,
        Path(__file__).with_name("model.py"),
        "task2/model/dither_word_gen.py",
        "task1/experiments/adctoolbox_calibration.py",
        "task1/experiments/bridge_lms_comparison.py",
        "task1/experiments/inl_dnl.py",
        "task1/experiments/residue_control_study.py",
        "task1/experiments/run_effects.py",
        "task1/model/fig21_bridge.py",
        "src/adi_model/charge_ref.py",
    )
    summary = {
        "schema": "eight_slice_native_dither_1",
        "scope": "behavioral two-node RDAC with fixed physical mismatch and ideal SADC assist and ADC2",
        "experiment": asdict(experiment),
        "calibration": asdict(cfg),
        "native_dither_step_v": step_v,
        "output_lsb20_v": 2 * thresholds[-1] / N_CODES,
        "static_half_window_v": STATIC_INPUT_LIMIT_V,
        "static_output_guard_v": STATIC_OUTPUT_GUARD_V,
        "static_paired_states": len(states[0]),
        "static_schedule_dem_seeds": BRIDGE_SEEDS,
        "static_dither_assignment_seed": STATIC_WORD_SEED,
        "training_dither_mask_seed": TRAIN_MASK_SEED,
        "training_dither_word_seed": TRAIN_WORD_SEED,
        "tone_dither_word_seed": DEFAULT_DITHER_SEED,
        "gain_calibration": gain_report,
        "groups": comparisons,
        "source_sha256": {
            str((ROOT / name).relative_to(ROOT)): sha256_file(ROOT / name) for name in sources
        },
        "artifact_sha256": {data_path.name: sha256_file(data_path)},
        "python": platform.python_version(),
        "dependencies": {n: version(n) for n in ("numpy", "adctoolbox")},
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


# ------------------------------------------------------------------------
def main():
    """运行指定 σ 的实验并写入独立目录。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sigma", type=float, choices=(0.02, 0.03), required=True)
    parser.add_argument("--seed", type=int, default=100)
    args = parser.parse_args()
    summary = run(args.output, args.sigma, args.seed)
    result = summary["groups"]["fig21_dem"]
    print(
        json.dumps(
            {
                key: {arm: result[key][arm]["lms"] for arm in SWITCHES}
                for key in ("linearity", "tone")
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

