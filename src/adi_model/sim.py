"""sim.py -- 主循环。

顺序严格保持：

    chip = build_chip(...)                # 每颗芯片只执行一次
    allocation = scheduler.reserve(...)   # 采样时确定 slice 归属，之后不可更改
    C_active[n] = sum(chip.C_true[S[n]])  # 同一组物理电容决定采样噪声与 RA 增益
    for n:
        sample = sampler.capture(...)     # slice 与采样噪声都绑定到这个样本
        coarse = sadc.convert(sample.x_sadc)
        cmd    = mapper.encode(coarse, allocation.bank, sid, d_code)
        vd0    = rdac.evaluate_nominal(cmd)
        vdt    = rdac.evaluate_physical(cmd)
        vra    = ra.evaluate(x_R - vdt, g = C_active[n]/C_F)   # 电荷一致增益
        vnc    = ktc.observe(...)
        fine   = adc2.quantize(vra - kappa*vnc)
        out    = vd0 + fine/G_hat - d_corr) / alpha

**哪个物理量改变了、哪条通路受到影响、哪个指标因此变化** —— 三者必须严格对应：
同一组 C_active[n] 同时决定 (1) 采样噪声 kT/C (2) RA 增益 (3) KTC 的逐样本 beta。

单位契约：全程 [V] / [F] / [s]；SimResult 字段单位见其 docstring。
适用域：unary 等权阵列口径（ADI 拓扑用 sim_split / pipeline）；
退化口径下（动态/噪声/dither/失配关）与 pipeline 逐位等价是 stage19 验收①。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .adc2 import ADC2
from .chip import Chip, build_chip
from .config import Config
from .ktc import KTCBranch
from .mapper import DitherState, Mapper, dem_state_sequence, dither_transfer_code, make_dither_state
from .ra import ResidueAmplifier
from .rdac import RDAC
from .reconstruction import Calibrator, DigitalState, initialize_state, reconstruct
from .sadc import SADC
from .sampler import SampleBatch, capture
from .scheduler import Scheduler, make_scheduler


@dataclass
class SimResult:
    """一次仿真的全部输出，含三套误差口径与逐样本物理量记录。

    字段单位契约：电压量全为 ``(N,)`` float64 [V]，电容为 [F]，码为 int64，
    除非特别说明。三套误差口径定义不同，勿互相冒充（v3 审计修正）：

    Attributes:
        out: ``(N,)`` [V] 重构输出 x_hat。
        err: ``(N,)`` [V] = out − 设计目标 x_ref（KTC 关→对齐 x1，
            KTC 开→对齐 x1+dx，即 beta=1 的设计目标）。
        x_ref: ``(N,)`` [V] 上述设计目标（数字端已知，非物理真值）。
        sample: SampleBatch（采样事件级实现；字段单位见其 docstring）。
        coarse: ``(N,)`` int64 粗码 c[n]。
        k: ``(N,)`` int64 RDAC 电平码 k_eq。
        vd0: ``(N,)`` [V] 名义 DAC 电压（理想权重）。
        vd_true: ``(N,)`` [V] 物理 DAC 电压（含失配/动态误差）。
        e_dac: ``(N,)`` [V] = vd_true − vd0（DAC 误差）。
        residue: ``(N,)`` [V] 残差 = x_R − vd_true。
        vra: ``(N,)`` [V] RA 输出。
        vnc: ``(N,)`` [V] KTC 观测支路输出。
        fine: ``(N,)`` [V] ADC2 量化后的精细项（折输入）。
        ra_sat: ``(N,)`` RA 饱和标志。
        adc2_over: ``(N,)`` ADC2 溢出标志。
        ktc_sat: ``(N,)`` KTC 饱和标志。
        bank: ``(N,)`` 采样 bank 归属（交织通道）。
        sid: ``(N,)`` int DEM 状态。
        cfg: Config（仿真所用配置，来源分级见 config.PARAM_GRADES）。
        chip: Chip（每颗芯片只生成一次的物理实现）。
        state: DigitalState（数字域状态，含 kappa/estimated_gain 等）。
        err_to_x1: ``(N,)`` [V] = out − x(t1)，对齐**第一级输入**的固定参考
            （校准/评价常用）；未计算时为 None。
        err_to_x2: ``(N,)`` [V] = out − x(t2)，对齐**第二级输入**的固定参考
            （beta=1 的设计目标）；未计算时为 None。
        err_vs_clean: ``(N,)`` [V] = out − x1_clean，对齐**驱动器噪声加入之前**
            的干净输入的整链误差（A07.6）；driver_noise_rms=0 时与 err_to_x1
            逐位相同，否则才暴露驱动器噪声伪影；未计算时为 None。
        g_vec: ``(N,)`` 逐样本 RA 增益（charge 模式 = C_active/C_F）。
        c_active: ``(N,)`` [F] 逐样本活跃采样电容（kT/C 噪声与增益共用）。
        calibration_applied: tuple 实际执行过的校准步骤（run_with_calibration 填充）。
    """

    out: np.ndarray
    err: np.ndarray
    x_ref: np.ndarray
    sample: SampleBatch
    coarse: np.ndarray
    k: np.ndarray
    vd0: np.ndarray
    vd_true: np.ndarray
    e_dac: np.ndarray
    residue: np.ndarray
    vra: np.ndarray
    vnc: np.ndarray
    fine: np.ndarray
    ra_sat: np.ndarray
    adc2_over: np.ndarray
    ktc_sat: np.ndarray
    bank: np.ndarray
    sid: np.ndarray
    cfg: Config
    chip: Chip
    state: DigitalState
    # ---- v3 审计修正：三套误差参考 + 物理量记录 ----
    err_to_x1: np.ndarray | None = None  # x_hat - x(t1)   （固定目标，校准/评价用）
    err_to_x2: np.ndarray | None = None  # x_hat - x(t2)   （固定目标，beta=1 的设计目标）
    # 相对**驱动器噪声加入之前**的输入的误差（外部审计 A07.6）。
    # 与 err_to_x1 的差别只在 driver_noise_rms > 0 时可见：err_to_x1 把 nd
    # 消掉了（驱动的 x1 本身含 nd），所以 1 mV 的驱动器噪声在 err_to_x1 里
    # 看起来只有 1 µV。整链误差必须看这一项。
    err_vs_clean: np.ndarray | None = None
    g_vec: np.ndarray | None = None  # 逐样本 RA 增益（charge 模式）
    c_active: np.ndarray | None = None  # 逐样本活跃采样电容
    calibration_applied: tuple = ()  # 实际执行过的校准步骤
    runner: str = "run_sim"

    @property
    def rdac_over(self) -> np.ndarray:
        """Return the per-sample command overflow flags before physical clipping.

        Returns:
            Boolean array: requested code is outside the realizable DAC range.
        """
        upper = self.cfg.dac_levels - 1 if self.cfg.dac_arch == "split" else self.cfg.n_units_sig
        return (self.k < 0) | (self.k > upper) | ~np.isfinite(self.k)

    @property
    def effective_config(self) -> dict:
        """Return serializable requested settings and the actual run contract.

        Physical diagnostics are for inspection only; no digital algorithm may
        consume this property. Calibration requested and applied are separate.

        Returns:
            Configuration snapshot, topology-specific capacitance, gain range,
            pending calibration and overrides inactive at this entry point.
        """
        inactive = {}
        default = Config()
        if self.cfg.dac_arch == "split" and self.cfg.c_feedback0 != default.c_feedback0:
            inactive["c_feedback0"] = "unary-only; use split_feedback_cap_f"
        if self.cfg.dac_arch == "unary" and self.cfg.split_feedback_cap_f is not None:
            inactive["split_feedback_cap_f"] = "split-only"
        return {
            "runner": self.runner,
            "requested": asdict(self.cfg),
            "feedback_cap_f": float(self.chip.C_feedback_true),
            "gain_min": float(np.min(self.g_vec))
            if self.g_vec is not None and self.g_vec.size
            else None,
            "gain_max": float(np.max(self.g_vec))
            if self.g_vec is not None and self.g_vec.size
            else None,
            "calibration_requested": self.cfg.calibration,
            "calibration_applied": list(self.calibration_applied),
            "calibration_pending": self.cfg.calibration != "none" and not self.calibration_applied,
            "inactive_overrides": inactive,
            "rdac_code_min": 0,
            "rdac_code_max": self.cfg.dac_levels - 1,
            "rdac_overflow_count": int(np.count_nonzero(self.rdac_over)),
        }


def run_sim(
    cfg: Config,
    input_fn,
    n_samples: int,
    chip: Chip | None = None,
    state: DigitalState | None = None,
    rng: np.random.Generator | None = None,
    scheduler: Scheduler | None = None,
    sadc: SADC | None = None,
) -> SimResult:
    """主信号链仿真（unary DAC 口径），返回 SimResult。

    与 sim_split.run_sim_split 的对偶关系：两者共享 RA/KTC/ADC2/重构/校准
    状态机，仅前端 DAC 求值不同（unary 等权 vs split 分段）；结果应互校，
    退化等价下逐位一致（stage19 验收①）。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
        input_fn: 可调用 ``t -> x``（t [s]，返回 [V]）。
        n_samples: 样本数 [无量纲]。
        chip: 虚拟芯片（默认 build_chip(cfg)，每颗芯片只生成一次）。
        state: 外部注入的数字状态（DigitalState），用于校准接力；None=新建。
        rng: 随机源；None=``np.random.default_rng(cfg.seed)``。
        scheduler: 调度器；None=``Scheduler(cfg)``。
        sadc: 量化器（默认 SADC(cfg)）。

    Returns:
        SimResult（字段单位与三套误差口径见 SimResult 的 docstring）。

    Raises:
        ConfigError: 配置不是可仿真的（枚举取值拼错、尺寸无意义等），入口直接
            拒绝（外部复核 2026-09-11）。
    """
    cfg.check_legal()
    if rng is None:
        rng = np.random.default_rng(cfg.seed)
    if chip is None:
        chip = build_chip(cfg)
    if state is None:
        state = initialize_state(cfg)

    sched = make_scheduler(cfg, rng, scheduler)
    allocation = sched.reserve(n_samples)

    sadc = sadc or SADC(cfg)
    mapper = Mapper(cfg)
    rdac = RDAC(cfg, chip, np.stack([sched.bank_a, sched.bank_b]))
    ra = ResidueAmplifier(cfg)
    ktc = KTCBranch(cfg)
    adc2 = ADC2(cfg)

    # ---- 同一组物理电容决定三件事：采样噪声、RA 增益、KTC beta ----
    # C_slice 一行求和，再按样本的 slice 集合索引 —— 备用 slice 不参与。
    C_slice = chip.C_true.sum(axis=1)  # (n_slices,)
    C_active = C_slice[allocation.slice_ids].sum(axis=1)  # (N,) 逐样本
    if cfg.ra_gain_model == "charge":
        g_vec = C_active / chip.C_feedback_true  # 逐样本增益
    else:
        g_vec = np.full(n_samples, cfg.g_actual)

    # ---- 采样：slice 与噪声都在这里绑定到样本，后续不能偷换 ----
    sample = capture(cfg, input_fn, n_samples, rng, chip=chip, c_active=C_active)

    # ---- 粗量化 ----
    coarse = sadc.convert(sample.x_sadc)

    # ---- DEM 状态（每样本确定；按 bank 内序号推进） ----
    sid = dem_state_sequence(n_samples, cfg, allocation.bank)

    # ---- 数字映射（sampling 模式下 dither 以码域配对量进入）----
    d_code = dither_transfer_code(
        cfg,
        sample.dither,
        step_rdac=cfg.rdac_step,
        step_coarse=cfg.delta1,
        dither_code_sampling=sample.dither_code,
    )
    cmd = mapper.encode(coarse, allocation.bank, sid, d_code)

    # ---- 两套权重下的 DAC 求值 ----
    vd0 = rdac.evaluate_nominal(cmd)
    vd_true = rdac.evaluate_physical(cmd)

    # ---- 残差 / 放大（各自独立检查饱和）----
    residue = sample.x_rdac - vd_true
    vra, ra_sat = ra.evaluate(residue, rng, g=g_vec)

    # ---- KTC 噪声观测支路（v1 可整体关闭）----
    # v5.1 第三轮审计 §二：观测的是**衰减后采样域**的节点（与 RDAC 同一电容
    # 网络），输入变化是 α·Δx 而非 Δx。旧代码漏乘 α，dither 开时数字端又除以
    # α，产生 (1/α-1)·Δx ≈ 42.7 µV @5MHz 的确定性尺度残差（unary 复现
    # 42.683 µV，与旧公式逐数值吻合；split 分支已修，此处补齐统一口径）。
    dx_obs = cfg.dither_alpha * sample.dx
    vnc, ktc_sat = ktc.observe(sample.n_R, dx_obs, rng)

    # ---- 后端量化：校正量在**数字域**扣除（见 adc2.quantize_with_correction）----
    # 不能写成 quantize(vra - kappa*vnc)：那样 κ·v_N 会占用 ADC2 的模拟量程，
    # 近 Nyquist 时把余量吃穿（docs/adr/0006）。
    fine, adc2_over = adc2.quantize_with_correction(vra, state.kappa * vnc)

    # ---- 去 dither / 重构（α 为采样态 dither 的恒定衰减）----
    dither = make_dither_state(cfg, sample.dither)
    if cfg.dither_mode == "sampling":
        dither.digital_correction = sample.dither_code * cfg.rdac_step
    elif cfg.dither_mode == "quantizer":
        dither = DitherState(sample.dither, d_code * cfg.rdac_step, np.asarray(sample.rdac_dither))
    out = reconstruct(vd0, fine, state.estimated_gain, dither.digital_correction, cfg.dither_alpha)

    # ---- 三套误差参考（v3 审计修正：不能互相冒充）----
    # 主误差 = out - 设计目标。目标插值权重 beta_target 是**数字端已知的设计量**：
    #   KTC 开（校准目标 beta=1）-> 目标 x2；KTC 关 -> 目标 x1。
    # 它不依赖任何物理真值；校准的任务就是让实际 beta 收敛到这个目标。
    # err_to_x1 / err_to_x2 是两个固定时刻的原始参考。
    beta_target = 1.0 if cfg.ktc_enable else 0.0
    err_x1 = out - sample.x1
    err_x2 = out - sample.x2
    x_ref = sample.x1 + beta_target * sample.dx
    err_target = out - x_ref
    # 整链误差：相对驱动器噪声加入之前的输入（A07.6）。driver_noise_rms=0
    # 时与 err_x1 逐位相同，所以不会给默认路径引入任何差异。
    x1_clean = sample.x1_clean if sample.x1_clean is not None else sample.x1
    err_clean = out - x1_clean

    return SimResult(
        out=out,
        err=err_target,
        x_ref=x_ref,
        sample=sample,
        coarse=coarse,
        k=cmd.k + cmd.dither_code,
        vd0=vd0,
        vd_true=vd_true,
        e_dac=vd_true - vd0,
        residue=residue,
        vra=vra,
        vnc=vnc,
        fine=fine,
        ra_sat=ra_sat,
        adc2_over=adc2_over,
        ktc_sat=ktc_sat,
        bank=allocation.bank,
        sid=sid,
        cfg=cfg,
        chip=chip,
        state=state,
        err_to_x1=err_x1,
        err_to_x2=err_x2,
        err_vs_clean=err_clean,
        g_vec=g_vec,
        c_active=C_active,
        calibration_applied=(),
    )


def run_with_calibration(
    cfg: Config,
    input_fn,
    n_samples: int,
    chip: Chip | None = None,
    rng: np.random.Generator | None = None,
    levels: tuple[float, float] = (-0.9, 0.9),
    n_cal: int = 4096,
) -> SimResult:
    """两遍式前台校准：先估增益（+beta），再用估计值跑正式仿真。

    **校准模式约定（v3 审计修正）**：增益校准使用已知直流输入 + 关闭 dither
    是最稳的第一步。若保留 dither，回归自变量必须包含数字端已知的
    d_nom = d_corr（update_gain 的 d_known 参数），否则估计器会被
    dither 方差淹没（实测 G_hat 33.6 -> 6.68）。

    Args:
        cfg: 仿真配置（Config）；校准模式由 cfg.calibration 决定
            （"none"/"gain"/"gain_beta"）。
        input_fn: 正式仿真的输入波形 ``t -> x``（t [s]，返回 [V]）。
        n_samples: 正式仿真样本数 [无量纲]。
        chip: 虚拟芯片（默认 build_chip(cfg)，与校准共用同一颗）。
        rng: 随机源；None=``np.random.default_rng(cfg.seed)``。
        levels: 增益校准用的直流电平范围 [V]（按 v_fs 缩放前的标称区间）。
        n_cal: 每个校准电平的样本数 [无量纲]（默认 4096）。

    Returns:
        SimResult：``calibration_applied`` 记录实际执行过的步骤
        （"gain" 和/或 "beta"），其余字段同 run_sim。
    """
    from .sampler import dc_input

    rng = rng or np.random.default_rng(cfg.seed)
    chip = chip or build_chip(cfg)
    state = initialize_state(cfg)
    applied = []

    if cfg.calibration in ("gain", "gain_beta"):
        # --- 增益：对 (alpha*x + d_nom - vd0) 回归 fine ---
        # 校准期固定 DEM 状态（dem_enable=False 的旁路副本）、关闭采样噪声，
        # 消除无关方差源；dither 若开启则用已知 d_nom 进入回归量。
        cal_cfg = cfg
        if cfg.dem_enable or cfg.enable_sampling_noise:
            from dataclasses import replace

            cal_cfg = replace(cfg, dem_enable=False, enable_sampling_noise=False)
        lv_list = np.linspace(levels[0], levels[1], 9) * cfg.v_fs
        xs, vds, fines, ds = [], [], [], []
        for lv in lv_list:
            r = run_sim(
                cal_cfg, dc_input(lv), n_cal, chip=chip, state=initialize_state(cfg), rng=rng
            )
            xs.append(np.full(n_cal, lv))
            vds.append(r.vd0)
            fines.append(r.fine)
            # 数字端**已知**的名义 dither：analog = 注入电压本身；
            # sampling = 码域配对量 × 名义步长。不得使用含失配的物理注入值。
            if cal_cfg.dither_mode == "sampling":
                ds.append(r.sample.dither_code * cal_cfg.rdac_step)
            else:
                ds.append(r.sample.dither)
        Calibrator(cfg, state).update_gain(
            np.concatenate(xs),
            np.concatenate(vds),
            np.concatenate(fines),
            d_known=np.concatenate(ds) if cfg.dither_mode != "off" else None,
            alpha=cfg.dither_alpha,
        )
        applied.append("gain")

    if cfg.calibration == "gain_beta" and cfg.ktc_enable:
        # --- beta：正弦前台，对固定目标 x1 回归（v3：不再用模型参考）---
        fin = cfg.fs * 1024 / n_samples
        from .sampler import sine_input

        r1 = run_sim(
            cfg, sine_input(0.8 * cfg.v_fs, fin), n_samples, chip=chip, state=state, rng=rng
        )
        Calibrator(cfg, state).update_beta(r1.out, r1.sample.x1, r1.sample.dx)
        applied.append("beta")

    res = run_sim(cfg, input_fn, n_samples, chip=chip, state=state, rng=rng)
    res.calibration_applied = tuple(applied)
    return res
