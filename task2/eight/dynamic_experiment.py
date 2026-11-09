# =============================================================================
# 文件名     : dynamic_experiment.py
# 模块名     : task2.eight.dynamic_experiment
# 版本       : v1.0
# 功能       : 修复后时序、单位响应与条件输入模型的可复现实验。
# 设计说明   :
#   [边界] 不再猜串扰／参考绝对量；未绑定项报告UNBOUND，不生成全芯片性能数字。
#   [输入] 15×Nyquist是原文缩放基准，C比例来自已有制造池，固定R为显式假设。
#   [校验] 单位网络验证粗源／精确源分工，和实际ADC性能预测严格分开。
#   [统计] 局部128码使用20位整数命中，不冒充全幅百万码的原图复现。
# 变更记录   : 替换已撤回的任意4ns／±2%τ／1nF五臂估计。
# =============================================================================

"""原文架构关系与条件模型验证，不拟合论文性能曲线。"""

from __future__ import annotations

import argparse
import json
import platform
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from adi_model.capacitors import ExperimentConfig, cap_pool
from adi_model.hashing import sha256_file
from adi_model.reference_mux import ReferenceBinding, SwitchedReference
from adi_model.transients import DynamicConfig, RampDrive, SineDrive
from task1.experiments.adctoolbox_calibration import CalibrationConfig, _spectrum
from task1.experiments.full20_linearity_plots import chinese_style
from task1.experiments.residue_control_study import MapperTable
from task1.model.codebook import codebook
from task1.model.module1 import Module1
from task2.eight.dynamic import DynamicCapture
from task2.eight.model import native_step_v
from task2.model.dither_word_gen import DitherWordGenerator
from task_reconstruction.fixed import from_training, reconstruct

ROOT = Path(__file__).resolve().parents[2]
LSB20 = 10 / (1 << 20)
ARMS = {"ideal": ("静态基线", {}), "input": ("整周期＋标称15×Nyquist", {"acquisition": True})}
COLORS = ("#666666", "#0072B2")


# =============================================================================
# 1. 连续控制流、整数重构和有限命中统计
# =============================================================================
class ControlStream:
    """保持调度、DEM、扰动的独立随机状态，不因数据分块重新播种。

    调度与旧schedule_sequence使用同一初始事件；每步持续申请下一采集组。
    当前转换组与上一采集组的归属仍由Module1检查，slot次序保留。

    Attributes:
        controller: 持续运行的调度／DEM状态机，分块时不重置物理归属。
        generator: 独立的六位字PRNG；使用已有模型的接受事件语义。
        index: 下一笔转换ID，从0递增，与孔径的全局序号一致。
    """

    def __init__(self, experiment):
        """按既有种子建立连续18／8／8／2调度及PRBS31。"""
        self.controller = Module1()
        self.controller.step(
            seed_load=True, slice_seed=experiment.schedule_seed, dem_seed=experiment.dem_seed
        )
        self.controller.step()
        self.controller.step(advance=True, shuffle=True, dem_enable=True)
        self.controller.step(advance=True, shuffle=True, dem_enable=True)
        self.generator = DitherWordGenerator()
        self.generator.step(seed_load=True, seed=0x5A1709C3)
        self.index = 0

    def next(self, count):
        """返回一块(N,8)物理片号、两路相位和同笔六位字。"""
        plan = np.empty((count, 8), dtype=np.int8)
        h, v, word = (np.empty(count, dtype=np.uint8) for _ in range(3))
        for i in range(count):
            output = self.controller.step(advance=True, shuffle=True, dem_enable=True)
            if not output.schedule.conv_valid or output.schedule.conv_sample_id != self.index:
                raise ValueError("stream lost conversion ownership")
            plan[i] = output.schedule.conv_ids
            h[i], v[i] = (
                output.conversion_dem.horizontal_phase,
                output.conversion_dem.vertical_phase,
            )
            word[i] = self.generator.step(
                sample_accept=True, sample_id=self.index, dither_enable=True
            ).word
            self.index += 1
        return plan, (h, v), word


def integer_reconstruct(coefficient, state, adc2):
    """向量化独立Q28整数公式，返回有符号码和有效位；不改变任何系数。

    先符号扩展权重再以int64累加，N*2^20也在int64内，避免32位溢出。
    ADC2中点分母为25，无半格tie；与标量fixed.reconstruct的用例另作对拍。
    非法原码或超出半开重构范围的样本保留无效位，不通过限幅冒充正常输出。

    Args:
        coefficient: 每片21个signed24权重及unsigned26增益，均为Q28。
        state: 当前样本的有序片号和主／子／逻辑注入特征，子特征允许为2。
        adc2: (N,)原始12位offset-binary码；-1仅是无效哨兵。
    Returns:
        signed20整数码和同形有效位，调用方还须合并模拟有效性。
    """
    weights = np.asarray(coefficient.weights, dtype=np.int64)
    gain = np.asarray(coefficient.gains, dtype=np.int64)[state.switches.plan].sum(axis=1)
    midpoint = np.rint((2 * adc2.astype(np.int64) + 1 - 4096) * (16384 / 25)).astype(np.int64)
    numerator = midpoint + np.sum(weights[state.switches.plan] * state.features, axis=(1, 2))
    valid = (adc2 >= 0) & (adc2 < 4096) & (numerator >= 0) & (numerator < gain)
    code = np.zeros(len(adc2), dtype=np.int64)
    code[valid] = (numerator[valid] * (1 << 20)) // gain[valid] - (1 << 19)
    return code, valid


def make_model(config, caps, experiment, drive):
    """本入口只运行已定义的输入缩放基准；未绑定串扰／参考会由模型拒绝。"""
    return DynamicCapture(config, caps, experiment, drive)


def tone_case(config, caps, experiment, thresholds, table, coefficient, cfg, count, warmup):
    """连续正弦预热后取相干N点；有拒绝即终止，不删除坏样本后计算漂亮频谱。

    波形时间使用全局样本序号乘period_s，避免除以fs的尾数差触发源一致性检查。
    所有臂的plan、phase、word由同种子产生；改变误差开关不改变控制序列。
    预热样本只稳定动态状态，不参与频谱；被截掉的长度明确写入摘要。
    有效段长度与相干频点配套，不能靠改频率元数据消除泄漏。
    带符号20位码先按固定LSB译回电压，再交给ADCToolbox。
    独立FFT使用相同DC／基波排除和Nyquist计权核对SNDR。
    保存内部节点及原码，便于追溯到底是哪条路径改变了性能。
    """
    drive = SineDrive(10 / 3, experiment.tone_bin * experiment.fs_hz / count)
    model = make_model(config, caps, experiment, drive)
    stream = ControlStream(experiment)
    plan, phase, word = stream.next(warmup + count)
    time_s = np.arange(warmup + count) * config.period_s
    state, _, residue, adc = model.capture(
        drive.value(time_s), word, plan, phase, thresholds, native_step_v(experiment), table, cfg
    )
    code, valid = integer_reconstruct(coefficient, state, adc)
    if not valid.all():
        raise ValueError("tone contains invalid reconstructed samples")
    # 标量大整数oracle不读取动态真值；抽检只验证向量加速没有改算术口径。
    for i in np.linspace(0, len(code) - 1, 16, dtype=int):
        values = (
            tuple(map(int, x))
            for x in (
                plan[i],
                state.switches.main[i],
                state.switches.sub[i],
                state.dither.sample_sub_high[i],
            )
        )
        expected, bad = reconstruct(coefficient, *values, int(adc[i]))
        if bad or expected != code[i]:
            raise ValueError("vector reconstruction differs from independent scalar oracle")
    output = (code[warmup:] + 0.5) * LSB20
    metrics = _spectrum(output, experiment)["metrics"]
    power = np.abs(np.fft.rfft(output)) ** 2
    signal = 2 * power[experiment.tone_bin]
    residual_power = power.copy()
    residual_power[[0, experiment.tone_bin]] = 0
    # 整数输出的FFT功率另算，与ADCToolbox定义交叉检查。
    sndr = 10 * np.log10(signal / (2 * residual_power[1:-1].sum() + residual_power[-1]))
    if abs(sndr - metrics["sndr_dbc"]) > 1e-5:
        raise ValueError("independent FFT SNDR disagrees")
    record = {k: v[warmup:].copy() for k, v in model.last_trace.items()}
    record.update(
        input_v=drive.value(time_s[warmup:]),
        output_v=output,
        adc2=adc[warmup:],
        residue_v=residue[warmup:],
        word=word[warmup:],
    )
    return {
        "sndr_db": float(metrics["sndr_dbc"]),
        "sfdr_dbc": float(metrics["sfdr_dbc"]),
        "enob": float(metrics["enob"]),
        "independent_fft_sndr_db": float(sndr),
        "invalid_samples": 0,
        "warmup_samples": warmup,
    }, record


def local_linearity(
    config, caps, experiment, thresholds, table, coefficient, cfg, codes, hits, guard, block
):
    """用真正20位整数输出累计局部斜坡直方图，返回DNL和端点INL。

    codes是评价内码数，guard是两侧输入保护码数，hits是每输入码的激励数。
    不把“计划每码hits”当成“实测每输出码命中”；后者随DNL与无效样本变化，另报最小值。
    直方图按绝对20位输出码累计，DNL=count/hits-1，INL由DNL累积并去端点线。
    输出端保护窗没有计入评价内码，图注必须继续披露保护窗及拒绝样本。
    局部码范围越小，越不能代表全幅INL形状；没有命中的码不能补零误差。
    全幅百万码实验若前端拒绝或命中不足，也必须如实保留覆盖不足状态。
    模型、调度与PRNG跨块连续；空间复杂度由block和20位直方图决定。
    """
    if not 8 <= codes <= (1 << 20) or hits < 1 or block < 1 or guard < 1:
        raise ValueError("invalid histogram dimensions")
    center = 1 << 19
    first = center - codes // 2
    start = first - guard
    attempted = (codes + 2 * guard) * hits
    slope = LSB20 / (hits * config.period_s)
    # 首孔径位于输入格中心；连续线性源在预热后也保持相同斜率。
    warmup = 256
    drive = RampDrive(
        (start - center + 0.5 / hits) * LSB20 - warmup * slope * config.period_s, slope
    )
    model, stream = make_model(config, caps, experiment, drive), ControlStream(experiment)
    histogram = np.zeros(1 << 20, dtype=np.int64)
    rejected, processed = 0, 0
    while processed < warmup + attempted:
        n = min(block, warmup + attempted - processed)
        plan, phase, word = stream.next(n)
        t = np.arange(processed, processed + n) * config.period_s
        state, _, _, adc = model.capture(
            drive.value(t),
            word,
            plan,
            phase,
            thresholds,
            native_step_v(experiment),
            table,
            cfg,
            strict=False,
        )
        code, valid = integer_reconstruct(coefficient, state, adc)
        keep = np.arange(processed, processed + n) >= warmup
        good = valid & model.last_trace["valid"] & keep
        histogram += np.bincount(code[good] + center, minlength=1 << 20)
        rejected += int(np.count_nonzero(keep & ~good))
        processed += n
    counts = histogram[first : first + codes]
    dnl = counts / hits - 1.0
    raw = np.r_[0.0, np.cumsum(dnl)]
    inl = raw - np.linspace(0, raw[-1], len(raw))
    return {
        "scope": "finite-hit local code-density; not full-range chip INL/DNL",
        "first_signed_code": int(first - center),
        "code_count": codes,
        "planned_hits_per_input_code": hits,
        "minimum_measured_hits": int(counts.min()),
        "mean_measured_hits": float(counts.mean()),
        "maximum_measured_hits": int(counts.max()),
        "zero_hit_codes": int(np.count_nonzero(counts == 0)),
        "rejected_samples": rejected,
        "attempted_samples": attempted,
        "dnl_min_lsb20": float(dnl.min()),
        "dnl_max_lsb20": float(dnl.max()),
        "endpoint_inl_peak_lsb20": float(abs(inl).max()),
        "input_window_v": [(start - center) * LSB20, (start + codes + 2 * guard - center) * LSB20],
    }, histogram


# =============================================================================
# 2. 结构／单位响应图，不输出未绑定误差的微伏预测
# =============================================================================
def normalized_reference_response():
    """单位R/C网络仅验证时序、电荷分工，所有坐标归一，禁止当作芯片电容规格。

    两次SAR电荷事件都发生在粗预充阶段；只有切准参考后才有精确源的负载电流。
    参数1 F、1 s等用于构造数学单位例，不是论文或180 nm设计参数。
    """
    cfg = DynamicConfig(period_s=10.0)
    binding = ReferenceBinding(
        1.0,
        1.0,
        3.0,
        0.2,
        0.1,
        0.3,
        1.0,
        (0.0, 0.0),
        "dimensionless validation only; not ADC parameters",
        "independent",
        "zero_reset",
    )
    events = [(1.0, np.array([0.5, 0.0])), (2.0, np.array([0.5, 0.0]))]
    t = np.linspace(0, cfg.period_s, 301)
    internal = np.zeros_like(t)
    pin = np.zeros_like(t)
    for i, time in enumerate(t):
        if time < cfg.ra_start_s:
            internal[i] = sum(
                q[0] * np.exp(-(time - at) / binding.coarse_tau_s) for at, q in events if time >= at
            )
        else:
            # 保持粗预充事件不变，仅截取不同长度的RA区间读取真实ODE状态。
            class Phase:
                command_s = cfg.command_s
                ra_start_s = cfg.ra_start_s
                ra_s = time - cfg.ra_start_s

            _, obs = SwitchedReference(binding).cycle(events, Phase(), 0.0, np.array([1.0, 0.0]))
            internal[i], pin[i] = obs["internal_end_v"][0], obs["pin_end_v"][0]
    _, obs = SwitchedReference(binding).cycle(events, cfg, 0.0, np.array([1.0, 0.0]))
    supplied = [
        obs["coarse_supplied_charge_c"][0],
        obs["fine_source_charge_c"][0],
        binding.internal_cap_f * obs["internal_end_v"][0]
        + binding.fine_pin_cap_f * obs["pin_end_v"][0],
    ]
    if not np.isclose(sum(supplied), 1.0, atol=1e-12):
        raise ValueError("unit charge balance failed")
    return t / cfg.period_s, internal, pin, np.asarray(supplied), asdict(binding)


def plot(out, reports, arrays, unit):
    """图上分开标原文约束、工程缩放条件和未绑定的单位响应。

    Args:
        out: 本次独立输出目录，不能覆盖既有静态结果。
        reports: 两档固定失配、两条输入路径的统计及有效性记录。
        arrays: 同笔输出波形和完整整数命中表；不插值补造频谱或码宽。
        unit: 单位参考网络的时间、电压缺额及电荷分配，不是芯片参数。

    Returns:
        实际写出的PNG／SVG／PDF文件名与SHA256，供结果摘要追溯。
    """
    chinese_style()
    plt.rcParams["pdf.fonttype"] = 3
    produced = {}

    def save(fig, name):
        for ext in ["png", "svg", "pdf"]:
            p = out / f"{name}.{ext}"
            meta = (
                {"CreationDate": None, "ModDate": None}
                if ext == "pdf"
                else {"Date": None}
                if ext == "svg"
                else None
            )
            fig.savefig(p, dpi=220, metadata=meta)
            produced[p.name] = sha256_file(p)
        plt.close(fig)

    fig, ax = plt.subplots(2, 2, figsize=(11, 7), layout="constrained")
    fig.suptitle("修复依据：整周期、共享孔径、预充与精确参考分工")
    ax[0, 0].broken_barh([(0, 100)], (2.2, 0.5), facecolors="#0072B2")
    ax[0, 0].broken_barh([(0, 50)], (1.2, 0.5), facecolors="#E69F00")
    ax[0, 0].broken_barh([(50, 50)], (0.2, 0.5), facecolors="#009E73")
    for t in (0, 100):
        ax[0, 0].axvline(t, color="#333333", ls="--", lw=0.8)
    ax[0, 0].set(
        yticks=[0.45, 1.45, 2.45],
        yticklabels=["REF_IN＋RA(n)", "粗预充(n)", "采集(n+1)"],
        xlabel="时间（ns，10 MS/s缩放示例）",
        title="边界共用输入／ADC2孔径；内部50%分配为工程预算",
        xlim=(-3, 103),
    )
    x = np.linspace(0, 10, 300)
    ax[0, 1].plot(x, np.exp(-x), color="#D55E00")
    ax[0, 1].set(
        xlabel="距孔径的静默时间／恢复τ",
        ylabel="采样误差／(Q/C)",
        title="串扰只给归一响应，不猜0.02 fF或微伏幅度",
    )
    t, internal, pin, supplied, _ = unit
    ax[1, 0].plot(t, internal, label="内部预充节点", color="#0072B2")
    ax[1, 0].plot(t, pin, label="精确参考引脚", color="#009E73")
    ax[1, 0].axvline(0.5, color="#333333", ls="--", lw=0.8)
    ax[1, 0].set(
        xlabel="周期归一时间t/T",
        ylabel="缺额／(总SAR电荷/C内部)",
        title="SAR先由粗驱动供电，0.5T后切精确参考",
    )
    ax[1, 0].legend(fontsize=8)
    ax[1, 1].bar(
        ["粗源已补电荷", "精确源补残差", "终点剩余缺额"],
        supplied,
        color=["#E69F00", "#009E73", "#777777"],
    )
    ax[1, 1].set(ylabel="电荷／总SAR电荷", title="同一单位例的电荷守恒，不是芯片电流预测")
    for a in ax.flat:
        a.grid(alpha=0.2)
    save(fig, "aligned_mechanisms")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8), layout="constrained")
    fig.suptitle("两臂均已做LMS静态校准，DEM／dither开启；固定R，τ随已有C变化")
    for col, sigma in enumerate((2, 3)):
        for (arm, (label, _)), color in zip(ARMS.items(), COLORS, strict=True):
            y = arrays[f"sigma_{sigma}pct"][arm]["output_v"]
            f = np.fft.rfftfreq(len(y), 1e-7)
            amp = np.abs(np.fft.rfft(y - y.mean())) * 2 / len(y) / 5
            axes[col].plot(
                f / 1e6,
                20 * np.log10(np.maximum(amp, 1e-16)),
                color=color,
                lw=0.7,
                label=f"{label}＋LMS: {reports[f'sigma_{sigma}pct'][arm]['tone']['sndr_db']:.2f} dB",
            )
        axes[col].set(
            title=f"{sigma}%电容失配；同一冻结LMS",
            xlabel="频率（MHz）",
            ylabel="幅度（dBFS）",
            ylim=(-155, 0),
        )
        axes[col].legend(fontsize=8)
        axes[col].grid(alpha=0.2)
    save(fig, "aligned_input_spectra")
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.5), layout="constrained")
    fig.suptitle("两臂均已做LMS静态校准；局部20位命中，曲线重合处蓝线覆盖灰线")
    for col, sigma in enumerate((2, 3)):
        for (arm, (label, _)), color in zip(ARMS.items(), COLORS, strict=True):
            r = reports[f"sigma_{sigma}pct"][arm]["linearity"]
            start = r["first_signed_code"]
            n = r["code_count"]
            h = arrays[f"sigma_{sigma}pct"][arm]["histogram"][
                start + (1 << 19) : start + (1 << 19) + n
            ]
            d = h / r["planned_hits_per_input_code"] - 1.0
            # 有限随机状态的直方图有统计起伏，不能拿局部峰值替代全幅静态积分。
            # 两臂共用相同激励预算；不按输出命中数重新归一以抹平真实DNL。
            v = np.r_[0.0, np.cumsum(d)]
            v -= np.linspace(0, v[-1], len(v))
            axes[0, col].step(
                np.arange(n) + start, d, where="mid", color=color, label=label, lw=0.7
            )
            axes[1, col].plot(np.arange(n + 1) + start, v, color=color, lw=0.8)
        axes[0, col].set(title=f"{sigma}%固定电容失配", ylabel="DNL（LSB20）")
        axes[1, col].set(xlabel="有符号20位码（仅局部）", ylabel="端点INL（LSB20）")
        axes[0, col].legend(fontsize=8)
    for a in axes.flat:
        a.grid(alpha=0.2)
    save(fig, "aligned_local_linearity")
    return produced


def run(out, samples=4096, codes=128, hits=512, guard=64, block=4096):
    """运行标称输入条件与单位响应；未绑定的串扰／参考不能冒充项目预测。

    输出目录必须新建。频谱遇无效数据即失败；局部直方图单列拒绝数。
    本次不重新训练LMS，不改变原始静态数据，也不选择参数去凑100 dB。
    15倍Nyquist用于用户10 MS/s目标只是缩放验证点，真实180 nm带宽仍需模拟绑定。
    """
    if samples < 1024 or samples % 2:
        raise ValueError("use even samples>=1024")
    out.mkdir(parents=True, exist_ok=False)
    reports = {}
    arrays = {}
    experiments = {}
    # [可比性] 各档都复用既有留出系数；本实验不接触训练真值或重新挑选种子。
    for sigma in (2, 3):
        exp = replace(ExperimentConfig(), mismatch_sigma_unit=sigma / 100, samples=samples)
        nom, caps = cap_pool(exp)
        th = np.r_[codebook(nom, exp), 5.0]
        table = MapperTable.build(bridge=True)
        coef = from_training(ROOT / f"task2/evidence/eight_negative/sigma_{sigma}pct/data.npz")
        cfg = replace(CalibrationConfig(), adc2_half_range_v=0.1)
        key = f"sigma_{sigma}pct"
        reports[key] = {}
        arrays[key] = {}
        experiments[key] = asdict(exp)
        for arm, (_, flags) in ARMS.items():
            model_cfg = DynamicConfig(**flags)
            tone, wave = tone_case(model_cfg, caps, exp, th, table, coef, cfg, samples, 256)
            linearity, hist = local_linearity(
                model_cfg, caps, exp, th, table, coef, cfg, codes, hits, guard, block
            )
            reports[key][arm] = {
                "config": asdict(model_cfg),
                "tone": tone,
                "linearity": linearity,
                "tau_multiplier": (caps.main.sum(axis=1) / (32 * exp.unit_cap_f)).tolist(),
            }
            arrays[key][arm] = {**wave, "histogram": hist}
            print(key, arm, tone["sndr_db"], flush=True)
    unit = normalized_reference_response()
    files = plot(out, reports, arrays, unit)
    raw = out / "data.npz"
    np.savez_compressed(
        raw,
        **{
            f"{s}_{a}_{k}": v for s, d in arrays.items() for a, w in d.items() for k, v in w.items()
        },
    )
    files[raw.name] = sha256_file(raw)
    sources = [
        "src/adi_model/transients.py",
        "src/adi_model/reference_mux.py",
        "task2/eight/dynamic.py",
        "task2/eight/dynamic_experiment.py",
        "task2/eight/experiment.py",
        "task_reconstruction/fixed.py",
        "src/adi_model/capacitors.py",
        "task1/model/module1.py",
        "task1/model/scheduler.py",
    ]
    summary = {
        "schema": "source-aligned-dynamics-v2",
        "scope": "conditional model validation, not measured chip prediction",
        "readiness": {
            "timing": "VALIDATED",
            "input_nominal": "CONDITIONAL_FIXED_R_C_MODEL",
            "digital_absolute_error": "UNBOUND_NO_PREDICTION",
            "reference_absolute_error": "UNBOUND_NO_PREDICTION",
            "full_range_250_hits": "NOT_RUN",
        },
        "results": reports,
        "experiments": experiments,
        "normalized_unit_network": unit[4],
        "artifact_sha256": files,
        "source_sha256": {n: sha256_file(ROOT / n) for n in sources},
        "frozen_training_sha256": {
            str(s): sha256_file(ROOT / f"task2/evidence/eight_negative/sigma_{s}pct/data.npz")
            for s in (2, 3)
        },
        "environment": {
            "python": platform.python_version(),
            **{n: version(n) for n in ["numpy", "scipy", "matplotlib", "adctoolbox"]},
        },
        "stimulus": {
            "fs_hz": 10_000_000,
            "tone_bin": 409,
            "samples": samples,
            "tone_peak_v": 10 / 3,
            "codes": codes,
            "hits_per_input_code": hits,
            "guard_codes": guard,
            "block": block,
        },
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


def main():
    """以显式预算运行修复后验证，不再接受已撤回的exploratory五臂预测。"""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    for name, value in [
        ("samples", 4096),
        ("codes", 128),
        ("hits", 512),
        ("guard", 64),
        ("block", 4096),
    ]:
        p.add_argument("--" + name, type=int, default=value)
    args = p.parse_args()
    run(args.out, args.samples, args.codes, args.hits, args.guard, args.block)


if __name__ == "__main__":
    main()
