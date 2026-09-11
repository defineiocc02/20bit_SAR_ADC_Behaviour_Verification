"""sim_split.py -- 分段 DAC（主/子 + 桥接电容）拓扑的信号链主循环。

与 sim.py 的关系：**下游完全复用**（RA / KTC / ADC2 / 重构 / 校准状态机），
只替换前端：DAC 求值（两套拓扑的物理方程不同）+ v5 新增的三项动态误差。

信号链（v5）：

    chip   = build_split_chip(...)        # 每颗芯片一次
    sample = capture(...)                 # 采样噪声绑定 C_samp = A + B
    x_R'   = x_R + e_input_settling       # (a) 输入建立（含 tau(k) 码相关）
    vD0    = dac.evaluate_nominal(k_eq)   # 数字可见
    vDt    = dac.evaluate_physical(k_eq, sid) + e_ref + e_xtalk
                                           # (b) 参考建立 (c) 数字串扰
    r      = x_R' - vDt
    vra    = RA(r, g = C_samp/C_F)        # 电荷一致增益（与 unary 同口径）
    fine   = ADC2(vra - kappa*vnc)
    out    = (vd0 + fine/G_hat - d_corr)/alpha

动态误差的进入位置**必须**与物理一致：
  * 输入建立改的是**采样值**（进 SADC/RDAC 两条路，这里只建 RDAC 路，
    SADC 电容小 20 倍、建立快，粗码误差二阶小量，已在注释声明）；
  * 参考建立与数字串扰改的是 **DAC 输出**（进残差，被 G 放大）。
单位契约：与 sim.py 一致（V/F/s/单位当量）；c_mask/c_sig/c_load/c_noise_eq
四口径定义见 run_sim_split 头部注释块（单一事实来源）。
适用域：分段主/子拓扑主循环；退化场景与 pipeline 逐位等价（stage19①）。
共享函数 sampling_dither_injection 改动后必须全链路回归（d_new 事故）。

"""

from __future__ import annotations

import numpy as np

from .adc2 import ADC2
from .config import Config
from .dac_arch import SplitDAC, build_split_chip
from .dynamics import apply_dynamics
from .ktc import KTCBranch
from .mapper import DitherState, dither_transfer_code, make_dither_state
from .ra import ResidueAmplifier
from .reconstruction import DigitalState, initialize_state, reconstruct
from .sadc import (
    build_first_stage_quantizer,
    units_per_first_stage_step,
)
from .sampler import SampleBatch, capture
from .scheduler import Scheduler, make_scheduler
from .sim import SimResult


def xtalk_profile(cfg: Config, n_units: int, rng: np.random.Generator) -> np.ndarray:
    """每单位数字耦合电容的**确定性**空间分布（版图梯度 [假设]）。

    DEM 置换单位即置换这份分布 -> 单位串扰分量可被 DEM 随机化；
    共模分量（dyn_c_xtalk_common）不在此列，DEM 无效。

    Args:
        cfg: 仿真配置；相关字段来源分级见 config.PARAM_GRADES
            （``dyn_c_xtalk_unit`` 为 [假设]，``dyn_c_xtalk_common`` 为 [假设]）。
        n_units: 参与切换的单位数 [无量纲]（= cfg.dac_n_units）。
        rng: 随机源（本函数目前为确定性分布，未消耗 rng）。

    Returns:
        ``(n_units,)`` float64：每单位的耦合电容 [F]
        （= ``dyn_c_xtalk_unit · (1 + 0.5·梯度)``，梯度 ∈ [−0.5, 0.5]）。
    """
    j = np.arange(n_units)
    grad = j / max(n_units - 1, 1) - 0.5  # [-0.5, 0.5]
    return cfg.dyn_c_xtalk_unit * (1.0 + 0.5 * grad)


def sampling_dither_injection(cfg: Config, chip, sample: SampleBatch, step0: float, c_sig: float):
    """Sampling dither 的注入电压重标定（sim_split / pipeline 共用，单一实现）。

    必须在残差计算**之前**调用：sampler 用的是 unary 口径的 cfg.rdac_step，
    与本拓扑 step0 差 ~1.8%；若不先换算，dither 会在残差里残留
    d_code*(rdac_step-step0) 的巨大误差。
    离散掩码口径（stage18）：注入量不用 step0·(1+mean eps) 近似，而是由
    **实际掩码电容**逐 d_u 精确生成 LUT —— 物理注入 = w_bank·Q_mask(d_u)/C_sig
    （charge_ref.dither_mask_charge 同一掩码），掩码单位的失配由此真实进入。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
        chip: 虚拟芯片（提供 C_main/C_sub 与 beta_true，用于掩码电容）。
        sample: SampleBatch（**会被改写**：x_rdac 与 dither 同步重标定）。
        step0: 本拓扑 RDAC 单位步长 [V]（用于连续口径换算）。
        c_sig: 信号电荷系数 [F] = chip.A + beta_true·chip.B（注入量归一化）。

    Returns:
        ``(N,)`` float64：重标定后的注入电压 [V]。

    Side effects:
        改写 sample.x_rdac 与 sample.dither（两者必须同步，否则残差与
        数字扣除失配）；仅此函数有权修改 SampleBatch。
    """
    d_old = np.asarray(sample.dither, dtype=float).copy()
    d_code = np.asarray(sample.dither_code, dtype=float)
    nd = cfg.dither_units_total
    if getattr(cfg, "dither_discrete", False) and nd > 0:
        bank_arr = chip.C_sub if cfg.dither_split_bank == "sub" else chip.C_main
        mask = bank_arr[-min(nd, len(bank_arr)) :]
        w_bank = chip.beta_true() if cfg.dither_split_bank == "sub" else 1.0
        D_rng = int(round(cfg.dither_units_range))
        lut = {}
        for du in range(-D_rng, D_rng + 1):
            s = np.full(len(mask), -1.0)
            s[: len(mask) // 2 + du] = 1.0
            lut[du] = w_bank * cfg.v_fs * float(np.dot(mask, s)) / c_sig
        d_new = np.array([lut[int(round(d))] for d in d_code])
    else:
        eps_d = (
            float(np.mean(chip.eps_sub[-nd:]))
            if (cfg.mismatch_enable and nd > 0 and chip.n_sub >= nd)
            else 0.0
        )
        d_new = d_code * step0 * (1.0 + eps_d)
    sample.x_rdac = sample.x_rdac - d_old + d_new
    sample.dither = d_new
    return d_new


def run_sim_split(
    cfg: Config,
    input_fn,
    n_samples: int,
    chip=None,
    state: DigitalState | None = None,
    rng: np.random.Generator | None = None,
    scheduler: Scheduler | None = None,
) -> SimResult:
    """分段 DAC（主/子 + 桥接电容）信号链仿真，返回 SimResult。

    与 sim.run_sim 的对偶关系：两者**下游完全复用**（RA/KTC/ADC2/重构/校准
    状态机），仅替换前端——DAC 求值（split 拓扑物理方程）与 v5 三项动态误差
    （输入建立/参考建立/数字串扰）。退化等价下与 pipeline 逐位一致（stage19①）。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
        input_fn: 可调用 ``t -> x``（t [s]，返回 [V]）。
        n_samples: 样本数 [无量纲]。
        chip: 虚拟芯片（默认 build_split_chip(cfg)，每颗芯片只生成一次）。
        state: 外部注入的数字状态（DigitalState），用于校准接力；None=新建。
        rng: 随机源；None=``np.random.default_rng(cfg.seed)``。
        scheduler: 调度器；None=``Scheduler(cfg)``。

    Returns:
        SimResult（字段单位与三套误差口径见 adi_model.sim.SimResult 的 docstring）。

    Raises:
        ConfigError: 配置不是可仿真的（枚举取值拼错、尺寸无意义等）。入口直接
            拒绝，而不是静默退化成默认分支再跑出一份看起来正常的结果
            （外部复核 2026-09-11）。
    """
    cfg.check_legal()
    if rng is None:
        rng = np.random.default_rng(cfg.seed)
    if chip is None:
        chip = build_split_chip(cfg)
    if state is None:
        state = initialize_state(cfg)

    sched = make_scheduler(cfg, rng, scheduler)
    allocation = sched.reserve(n_samples)

    dac = SplitDAC(cfg, chip)
    ra = ResidueAmplifier(cfg)
    ktc = KTCBranch(cfg)
    adc2 = ADC2(cfg)

    # ---- 分段拓扑的可用输入范围由 DAC 名义端点决定（设计时已知）----
    # v_lo 名义 = DAC 物理最小输出；一个第一级判决步 = units_per_d1 个子步长。
    # SADC 阈值**必须与 DAC 名义栅格严格对齐**（起点 v_lo、步长
    # units_per_d1*step0），否则 511 个码累积的偏差会把 residue 推出 ADC2
    # 窗口（历史实测 err 跳到 3 mV）。
    #
    # 外部审计 B16：两条主循环**不得各自解释** SADC 阈值。这里的步长、
    # 端点、单位换算全部来自 sadc.py 的共享构造器，pipeline 调用同一个函数。
    v_lo, v_hi = dac._nominal_endpoints()
    step0 = (v_hi - v_lo) / (dac.levels - 1)
    units_per_d1 = units_per_first_stage_step(cfg, dac)
    d1_eff = units_per_d1 * step0  # 一个第一级判决步的电压宽度
    sadc = build_first_stage_quantizer(cfg, dac)

    # ---- 四个电容口径严格分离（v5 第二轮审计 §3 / v5.1 stage18 掩码化）----
    # 一个电容数不能同时代表：制造面积、输入负载、信号电荷系数、噪声等效。
    #   C_sig   = A + beta*B        信号电荷系数（RA 增益分子，charge_ref.py）
    #   C_load  = A + B − C_mask    采样相输入驱动要充放电的负载（建立 tau）：
    #                               dither 掩码单位采样相接 ±V_FS，不加载输入
    #   C_n,eq  = C_sig^2/(A+beta^2·B)  噪声等效电容：
    #           状态向量 q=[q_M,q_S]（方差 kT·diag(A,B)）经残差通路
    #           (q_M+β·q_S)/C_sig -> σ²=kT(A+β²B)/C_sig²。
    #           stage16 已从相位连接推导此式（noise_phase.py），原 [假设]
    #           升级为"相位声明下的推导值"；剩余假设 = 阵列间独立。
    #           注意：dither 掩码**不改变**噪声 —— 掩码单位仍贡献 kT/C，
    #           且以同一 β 权重进入残差；掩码只衰减信号（α<1 -> −20log10(α) dB）。
    c_mask = 0.0
    if cfg.dither_mode == "sampling" and cfg.dither_units_total > 0:
        _bank = chip.C_sub if cfg.dither_split_bank == "sub" else chip.C_main
        c_mask = float(_bank[-cfg.dither_units_total :].sum())
    c_sig = chip.A + chip.beta_true() * chip.B
    c_load = chip.A + chip.B - c_mask
    c_load_vec = np.full(n_samples, c_load)
    c_noise_eq = c_sig * c_sig / (chip.A + chip.beta_true() ** 2 * chip.B)
    c_noise_vec = np.full(n_samples, c_noise_eq)

    # ---- RA 增益（输入等效口径，与 charge_ref.py 一致）----
    # 必须经 ra.gain_vector 求值，不得在此硬编码电容比：cfg.ra_gain_model
    # 声明了 "charge"（G=C_sig/C_F）与 "fixed"（G=g0·(1+gain_error)）两种
    # 对照口径，硬编码会把 "fixed" 静默忽略 —— 一条只在 runner 里生效的配置
    # 契约，必须在 runner 里被遵守或显式拒绝（外部复核 2026-09-11）。
    # 默认 "charge" 下 gain_vector 返回的正是 c_sig/C_F，与本行原值逐位相同。
    g_vec = ra.gain_vector(np.full(n_samples, c_sig), chip.C_feedback_true)

    # ---- 采样（kT/C 用噪声等效电容；gain/建立分别用各自口径）----
    sample = capture(cfg, input_fn, n_samples, rng, chip=None, c_active=c_noise_vec)

    # ---- 粗码 -> 电平码：直接用名义栅格（与 SADC 阈值严格对齐）----
    coarse = sadc.convert(sample.x_sadc)
    # units_per_d1 已在构造 SADC 时由共享构造器算出（同一真相源）。
    # sampling dither 的码域配对量在这里并入电平码（与 unary 的 cmd.k+dither 口径一致）；
    # quantizer dither（stage21）：数字侧把 d_Q 按 cfg.dither_quant_transfer
    # 粒度取整后**转移给 RDAC 码**（d_u = −round(d_Q/gran)），残差回到名义 bin，
    # 余项 d_Q − round(d_Q) 落进残差由 ADC2 窗口吸收 —— 这就是"转移到 RDAC
    # 增强 log2(units_per_d1) bit"的机制载体。取整必须在主循环做：
    # 粒度 gran 依赖本拓扑的 step0，sampler 不知道拓扑。
    d_code = dither_transfer_code(
        cfg,
        np.asarray(sample.dither, dtype=float),
        step_rdac=step0,
        step_coarse=d1_eff,
        dither_code_sampling=sample.dither_code,
    )
    k_eq = coarse * units_per_d1 + d_code

    # ---- DEM 状态（沿用 bank 内推进口径）----
    from .mapper import dem_state_sequence

    sid = dem_state_sequence(n_samples, cfg, allocation.bank)

    # ---- 动态误差（三项，任何一项可独立关闭）----
    # 采样相开始时，slice 顶极板保持的是**上一次转换的残差电压**（0~Δ1，接近共模），
    # 不是上一个 DAC 输出（那是 -x 量级）。V_prev 取上一拍的名义残差：
    #     V_prev[n] = x_R[n-1] - vd0[n-1]  （即残差 r[n-1] 的名义值）
    # 若误用 vd0[n-1]，误差变成 -eps*(x[n]+x[n-1])，增益虚减 eps 倍、且
    # eps 的码调制被放大 2x —— 曾导致 SNDR 崩到 36 dB 的假象。
    v_nom = dac.evaluate_nominal(k_eq)
    v_prev = np.empty(n_samples)
    v_prev[0] = 0.0
    v_prev[1:] = sample.x_rdac[:-1] - v_nom[:-1]

    dyn = apply_dynamics(
        cfg,
        x=sample.x_rdac,
        v_prev=v_prev,
        v_nominal=v_nom,
        code=k_eq,
        sid=sid,
        c_active=c_load_vec,
        n_levels=dac.levels,
        n_units=cfg.dac_n_units,
        xtalk_profile=xtalk_profile(cfg, cfg.dac_n_units, rng),
        perm_fn=dac.full_order,
        c_xtalk_out=c_sig,  # 串扰电荷折输入等效电压除 C_sig（不是 c_eff/c_total）
        c_load_ref=c_load,
    )  # 参考建立被开关电容 = 实际驱动负载 A+B

    # ---- dither：sampling 模式的注入电压尺度必须用本拓扑的 step0 ----
    # （实现在 sampling_dither_injection，sim_split / pipeline 共用同一份代码）
    if cfg.dither_mode == "sampling":
        d_new = sampling_dither_injection(cfg, chip, sample, step0, c_sig)

    # ---- DAC 两套求值 + 动态误差注入 ----
    vd0 = v_nom
    vd_true = dac.evaluate_physical(k_eq, sid) + dyn.e_dac

    # ---- 残差 / 放大 ----
    residue = (sample.x_rdac + dyn.e_input) - vd_true
    vra, ra_sat = ra.evaluate(residue, rng, g=g_vec)

    # ---- KTC / 后端 / 重构（与 unary 完全相同）----
    # v5 第二轮审计 §5：KTC 观察的是**衰减后采样域**的节点（与 RDAC 同一
    # 电容网络），它看到的输入变化是 α·Δx 而非 Δx。旧代码直接用 Δx，
    # 数字端又除以 α，产生 (1/α-1)·Δx ≈ 42.5 µV @5MHz 的确定性尺度残差
    # （实测复现 42.502 vs 预测 42.704 µV）。α=1（无 sampling dither）不变。
    dx_obs = cfg.dither_alpha * sample.dx
    vnc, ktc_sat = ktc.observe(sample.n_R, dx_obs, rng)
    # 校正量在数字域扣除，不占用 ADC2 模拟量程（docs/adr/0006）
    fine, adc2_over = adc2.quantize_with_correction(vra, state.kappa * vnc)

    if cfg.dither_mode == "sampling":
        # 数字端扣除**名义**值：dither 单位的失配必须留在输出里（这才是它的代价），
        # 不能用含失配的物理值去扣（那会把要研究的误差悄悄消掉）。
        dither = DitherState(
            analog_injection=d_new,
            code_modification=np.zeros(n_samples),
            digital_correction=np.asarray(sample.dither_code, dtype=float) * step0,
        )
    elif cfg.dither_mode == "quantizer":
        # 码修正已在 k_eq 内完成（d_u = −round(d_Q/gran)）；理想链路下
        # vD0 + fine/Ĝ = x（dither 在残差与码修正中成对相消），故 d_corr = 0。
        # 余项（取整误差）经 G 放大进 ADC2 —— 粒度越细余项越小，即"转移增强"。
        dither = DitherState(
            analog_injection=np.asarray(sample.dither, dtype=float),
            code_modification=d_code * step0,
            digital_correction=np.zeros(n_samples),
        )
    else:
        dither = make_dither_state(cfg, sample.dither)
    out = reconstruct(vd0, fine, state.estimated_gain, dither.digital_correction, cfg.dither_alpha)

    beta_target = 1.0 if cfg.ktc_enable else 0.0
    err_x1 = out - sample.x1
    err_x2 = out - sample.x2
    x_ref = sample.x1 + beta_target * sample.dx
    err_target = out - x_ref
    # 整链误差：相对驱动器噪声加入之前的输入（A07.6）。
    x1_clean = sample.x1_clean if sample.x1_clean is not None else sample.x1
    err_clean = out - x1_clean

    return SimResult(
        out=out,
        err=err_target,
        x_ref=x_ref,
        sample=sample,
        coarse=coarse,
        k=k_eq,
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
        c_active=c_noise_vec,
        calibration_applied=(),
    )
