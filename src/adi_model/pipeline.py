"""pipeline.py -- v6 整体 ADC 信号流：逐相位、有状态的完整链路模拟。

与 sim_split.py 的关系：
    sim_split 是**逐样本向量化**的吞吐导向模型（v1-v5 的主循环，全部验收
    基于它）；pipeline 是**逐相位状态机**导向的参考实现 —— slice 池里的
    每个 slice 是有记忆的物理对象（v_top 跨样本持久），独立量化器/SADC
    通路显式建模，数字侧算法全部经 digital_core.DigitalCore 走。

============================================================================
逐相位信号流（每样本一个周期；转换组/采集组按 2-phase 交替）
============================================================================

    周期 n（对应论文 Fig.9.8.1 的数据流）：
      [量化]  独立量化器 sDAC 对 x_S[n] 建立并 flash 出粗码 c[n]
              （量化器与 RDAC 完全分离，[09]；两个量化器按样本交替）
      [提交]  转换组 8 slice：本样本转换结束，顶板保持名义残差
              v_top[i] ← x_R[n] − v_D,0(k[n])
      [采集]  采集组 8 slice 各自一阶 RC 建立到输入 x_R[n]：
                x_saved(i,n) = x − eps_i·(x − v_top[i])
                eps_i = exp(−T_s/τ_i)，τ_i = τ0·(1+δ_i)·(1+ρ·码调制)
                v_top[i] 是**该 slice 自己**上一次转换（2 拍前）的残差状态
                —— 与 sim_split 的全局 v_prev[n]=r[n−1] 结构不同。
      [数字]  DigitalCore.switch_commands(c, dither, DEM) -> (k_eq, sid)
      [物理]  v_D,0 / v_D,true（SplitDAC + 参考建立 + 数字串扰）
              -> 残差 -> 共享 RA -> ADC2 -> fine
      [重构]  x̂ = (vD0 + fine/Ĝ − d_corr)/α   —— DigitalCore.reconstruct

============================================================================
适用域声明（勿夸大）
============================================================================
  * τ 口径 = **聚合单节点 RC**（τ0=(R_s+R_on)·C_load，与 sim_split/stage13
    同一口径，保守上界）。真实架构里每个 slice 有独立采样开关，
    τ_i = R·C_slice 小 ~8 倍（建立更快）—— 本口径偏保守，不偏乐观。
    slice 间带宽差异由 cfg.slice_bw_spread 刻画（交织杂散来源）。
  * 采集窗口 = dyn_t_sample_frac·Ts；论文"采集占满全周期"用 frac→0.9 近似。
  * 顶板状态的提交用**名义残差**（与 sim_split 的 v_prev 口径一致，
    失配部分已经含在 e_dac 里，不重复计）。
  * KTC / 噪声口径与 sim_split 完全一致（同一个 n_R 进两条通路）。
  * 退化等价：动态关 + 噪声关 + dither 关时与 sim_split 逐位一致
    （stage19 验收①）。
"""

from __future__ import annotations

import dataclasses

import numpy as np

from .adc2 import ADC2
from .config import Config
from .dac_arch import SplitDAC, build_split_chip
from .digital_core import DigitalCore
from .dynamics import apply_dynamics
from .ktc import KTCBranch
from .mapper import dither_transfer_code
from .ra import ResidueAmplifier
from .reconstruction import DigitalState
from .sadc import (
    build_first_stage_quantizer,
    units_per_first_stage_step,
)
from .sampler import capture, input_derivative
from .scheduler import Scheduler
from .sim import SimResult
from .sim_split import sampling_dither_injection, xtalk_profile


# ==========================================================================
# slice 池：每个 slice 是有记忆的物理对象
# ==========================================================================
class SlicePool:
    """18 个 RDAC slice + 2 个独立量化器 slice 的持久物理状态。

    每个 slice 记录：
      tau_rel  -- 时间常数相对偏差（slice 间带宽失配，cfg.slice_bw_spread）
      v_top    -- 顶极板当前保持的电压（输入等效口径 = 名义残差量级）
    转换组/采集组分配沿用 Scheduler（含 spare 轮换），采样时确定、不可更改。
    """

    N_QUANT = 2  # 独立量化器 sDAC 数（论文：<1ns return-to-acquisition 交替）

    def __init__(
        self, cfg: Config, chip, rng: np.random.Generator, sched: Scheduler, c_load_active: float
    ):
        """绑定配置、芯片、随机源与调度器，并预计算活跃负载电容。

        Args:
            cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
            chip: 虚拟芯片（build_split_chip 产出），含单位电容 C_main/C_sub
                与桥接比 ``beta_true``。
            rng: 随机源（np.random.Generator）；用于 slice 间带宽/偏斜/offset
                失配的一次性抽取（每颗芯片一次、跨样本持久）。
            sched: 调度器（Scheduler，含 spare 轮换），确定转换/采集组分配。
            c_load_active: 活跃负载电容 [F]，= chip.A + chip.B − c_mask；
                ``[推导]``（由芯片阵列面积派生，见 run_pipeline 头部注释）。

        Side effects:
            改写 self 的 ``tau_rel`` / ``t_skew`` / ``v_os`` / ``v_top`` 等数组。
        """
        self.cfg = cfg
        self.chip = chip
        self.sched = sched
        n = cfg.n_slices
        # 每 slice 的活跃电容：chip 只显式建模**活跃阵列**（8 slice 的主/子
        # 单位），池中其余 slice 是它的物理副本（同一批单位电容的复制，
        # ppm 级相对差异因此相同；slice 间额外差异由 slice_bw_spread 给出）。
        # 错误做法（v6 自查）：直接按 i*k 切 c_main —— 超出 64 单位的 slice
        # 切到空数组，电容≈0 -> eps≈0，偶数样本的采集误差被静默抹掉。
        c_main = np.asarray(chip.C_main, dtype=float)
        c_sub = np.asarray(chip.C_sub, dtype=float)
        n_act = cfg.n_active
        k = len(c_main) // n_act
        self.c_slice = np.array(
            [
                c_main[(i % n_act) * k : (i % n_act) * k + k].sum() + c_sub[i % len(c_sub)]
                for i in range(n)
            ]
        )
        # τ 归一基准 = 每 slice 名义电容（聚合口径 τ0=R·C_load 下，
        # 典型 slice 的 τ_i ≈ τ0 -> eps 与 sim_split 的聚合口径一致；
        # c_slice 的 ppm 级相对差异保留为物理真实）
        self.c_slice_nom = c_load_active / cfg.n_active
        # slice 间带宽失配（跨样本持久，每颗芯片一次）
        if cfg.slice_bw_spread > 0:
            self.tau_rel = 1.0 + rng.normal(0.0, cfg.slice_bw_spread, n)
        else:
            self.tau_rel = np.ones(n)
        # slice 间采样时刻偏移（论文交织误差四件套之 timing；每颗芯片一次）。
        # 误差机制与带宽失配正交：held ≈ x(t+δt) ≈ x + δt·dx/dt（信号斜率项），
        # 固定 A/B 两组交替 -> δ̄_bank 逐样本 (−1)^n 调制 -> f_S/2±f_IN 杂散；
        # 8/18 洗牌 -> δ̄ 随机 -> 打散进噪声底。
        if cfg.slice_timing_skew_s > 0:
            self.t_skew = rng.normal(0.0, cfg.slice_timing_skew_s, n)
        else:
            self.t_skew = np.zeros(n)
        # 逐 slice 输入参考 offset（交织四件套之 offset；每颗芯片一次）。
        # 误差机制与 skew 正交且与信号无关：x_hold = x + V_os,i ->
        # 样本级误差 = mean(V_os[acq])（常数型）-> 固定两组下被 (−1)^n
        # 调制成**常数** -> 杂散在 f_S/2（不随 f_IN 平移），这是与
        # skew/带宽/增益杂散（f_S/2±f_IN）的判别性指纹（stage20）。
        if cfg.slice_offset_sigma_v > 0:
            self.v_os = rng.normal(0.0, cfg.slice_offset_sigma_v, n)
        else:
            self.v_os = np.zeros(n)
        self.v_top = np.zeros(n)  # RDAC slice 顶板状态
        # 量化器 slice：独立 sDAC（论文：每个量化器由一片 sDAC 构成）
        self.c_quant = cfg.sadc_cap_ratio * c_load_active
        self.tau_rel_q = (
            1.0 + rng.normal(0.0, cfg.slice_bw_spread, self.N_QUANT)
            if cfg.slice_bw_spread > 0
            else np.ones(self.N_QUANT)
        )
        self.v_top_q = np.zeros(self.N_QUANT)

    # ------------------------------------------------------------------
    def acq_ids(self, n: int, alloc_slice_ids: np.ndarray) -> np.ndarray:
        """样本 n 的采集组 = 另一个 bank 的 8 个 slice（2-phase 交替）。

        Args:
            n: 样本索引 [无量纲]。
            alloc_slice_ids: ``(N, 8)`` int64，每 bank 的 slice 编号。

        Returns:
            ``(8,)`` int64：样本 n 的采集组（另一 bank）slice 编号。
        """
        m = alloc_slice_ids.shape[0]
        j = n + 1 if n + 1 < m else n - 1
        return alloc_slice_ids[j]

    def acquire(
        self,
        n: int,
        x_n: float,
        code_n: float,
        t_s: float,
        tau0: float,
        acq: np.ndarray,
        dxdt_n: float = 0.0,
    ) -> float:
        """采集组逐 slice 一阶 RC 建立，返回样本级平均误差（已更新 v_top）。

        x_saved(i) = x − eps_i·(x − v_top[i])，
        τ_i = τ0·(C_i/C̄)·(1+δ_i)·(1+ρ·码调制)。C_i/C̄ 的 ppm 级差异
        保留（物理真实），主导的带宽差异来自 δ_i。
        timing skew：每个 slice 在 t_n + δt_i 采样 ->
        x_hold(i) = x + δt_i·dx/dt；样本级平均误差含 mean(δt_i)·dx/dt 项。
        skew=0 时与旧口径逐位一致（加的是精确 0.0）。

        Args:
            n: 样本索引 [无量纲]。
            x_n: 输入电压 [V]。
            code_n: 当前样本粗码 k_n [单位当量]（用于码相关建立调制，
                ``dyn_ron_code_coeff``，[假设]）；线性化 τ = τ0·(1+ρ·(2k/N−1))。
            t_s: 采样相时长 [s]（= ``dyn_t_sample_frac / fs``）。
            tau0: 聚合时间常数基准 [s] = (R_s + R_on)·C_load。
            acq: ``(n_active,)`` int64，采集组 slice 编号。
            dxdt_n: 输入斜率 [V/s]，timing skew 的驱动量；0 = 关闭 skew
                （``slice_timing_skew_s`` 为 [假设]，一次性抽取）。

        Returns:
            float：样本级平均采集误差 [V]（已含 skew / offset 的线性项）。

        Side effects:
            改写 self.v_top[acq]（顶板状态跨样本持久）。
        """
        # 建立项必须由 dyn_input_settling 门控：offset/skew 是采样时钟与
        # 比较器属性，与建立正交 —— 若 offset/skew 单独开启时连带激活
        # eps=exp(−t_s/τ)，会混入 ~124ppm 的增益伪误差（fin 处 ~31 µV
        # 谱分量压过 offset 的 f_S/2 指纹，stage20 的实际教训）。
        if self.cfg.dyn_input_settling:
            tau = tau0 * (self.c_slice[acq] / self.c_slice_nom * self.tau_rel[acq])
            if self.cfg.dyn_ron_code_coeff != 0.0:
                nl = float(self.cfg.n_units_sig)
                tau = tau * (1.0 + self.cfg.dyn_ron_code_coeff * (2.0 * code_n / nl - 1.0))
            eps = np.exp(-t_s / np.maximum(tau, 1e-18))
        else:
            eps = np.zeros(len(acq))
        x_hold = x_n + self.t_skew[acq] * dxdt_n + self.v_os[acq]
        e = -eps * (x_hold - self.v_top[acq])
        self.v_top[acq] = x_hold + e
        # offset 项：保持值相对 x 的净误差 = mean(v_os)（eps 门控后无建立耦合）；
        # skew 项 = mean(δt_i)·dx/dt（采样时刻偏移的线性化误差）。
        return (
            float(np.mean(e))
            + float(np.mean(self.t_skew[acq])) * dxdt_n
            + float(np.mean(self.v_os[acq]))
        )

    def acquire_quantizer(self, n: int, x_s: float, t_s: float, tau0_q: float) -> float:
        """独立量化器 sDAC 的建立（两个量化器按样本交替，<1ns 恢复）。

        Args:
            n: 样本索引 [无量纲]；量化器按 ``n % N_QUANT`` 交替。
            x_s: 量化器输入电压 [V]。
            t_s: 采样相时长 [s]。
            tau0_q: 量化器通路时间常数基准 [s] = (R_s+R_on)·c_quant。

        Returns:
            float：量化器建立误差 [V]（进入粗码决策）。

        Side effects:
            改写 self.v_top_q 对应量化器的顶板状态。
        """
        q = n % self.N_QUANT
        tau = tau0_q * self.tau_rel_q[q]
        eps = float(np.exp(-t_s / max(tau, 1e-18)))
        e = -eps * (x_s - self.v_top_q[q])
        self.v_top_q[q] = x_s + e
        return e

    def commit_residue(self, conv: np.ndarray, r_nom: float) -> None:
        """转换相结束：转换组 slice 顶板保持名义残差（输入等效口径）。

        Args:
            conv: ``(n_active,)`` int64，转换组 slice 编号。
            r_nom: 名义残差 [V] = x_R − vD0（输入等效口径）；失配部分已含
                在 e_dac 里，不重复计。

        Side effects:
            改写 self.v_top[conv]。
        """
        self.v_top[conv] = r_nom

    def invariants(self, conv: np.ndarray, acq: np.ndarray) -> None:
        """逐样本硬检查：恰好 8+8、无重叠、编号合法（工业级自检）。

        Args:
            conv: ``(n_active,)`` int64，转换组 slice 编号。
            acq: ``(n_active,)`` int64，采集组 slice 编号。

        Raises:
            AssertionError: 当两组非各 8 个、存在重叠或编号越界时触发
                （工业级自检，绝不静默放行）。
        """
        assert len(conv) == self.cfg.n_active and len(acq) == self.cfg.n_active
        assert not set(conv.tolist()) & set(acq.tolist())
        assert conv.max() < self.cfg.n_slices and acq.max() < self.cfg.n_slices


# ==========================================================================
# 主循环
# ==========================================================================
def run_pipeline(
    cfg: Config,
    input_fn,
    n_samples: int,
    chip=None,
    state: DigitalState | None = None,
    rng: np.random.Generator | None = None,
    scheduler: Scheduler | None = None,
) -> SimResult:
    """逐相位整体信号流模拟。返回与 run_sim_split 同构的 SimResult。

    与 sim_split.py 的对偶关系：sim_split 是逐样本向量化吞吐模型（验收基准），
    pipeline 是逐相位状态机参考实现（slice 池的有记忆物理对象），两者结果应互校。
    skew 斜率必须用 input_derivative（解析/谱导数）；**勿回退**到中心差分实现
    （此前 v6.1 用的梯度函数近 Nyquist 低估约 25.6 dB，审计 A07.3，详见 ADR 0008）。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
        input_fn: 可调用 ``t -> x``（t 标量或 ndarray [s]，返回 [V]）；
            建议用 sine_input/dc_input（自带精确 ``.derivative``，供 skew 使用）。
        n_samples: 样本数 [无量纲]。
        chip: 虚拟芯片（默认 build_split_chip(cfg)，每颗芯片只生成一次）。
        state: 外部注入的数字状态（DigitalState），用于校准接力；None=新建。
        rng: 随机源；None=``np.random.default_rng(cfg.seed)``。
        scheduler: 调度器；None=``Scheduler(cfg)``。

    Returns:
        SimResult（字段单位见 adi_model.sim.SimResult 的 docstring），含：
        ``out`` [V]、``err``（= out − 设计目标 x_ref）[V]、
        ``err_to_x1`` / ``err_to_x2`` / ``err_vs_clean`` 三套误差口径 [V]
        （定义与单位见 SimResult；err_vs_clean 在 driver_noise_rms=0 时
        与 err_to_x1 逐位相同，否则为相对干净输入的整链误差）。

    Raises:
        ConfigError: 配置不是可仿真的（枚举取值拼错、尺寸无意义等），入口直接
            拒绝（外部复核 2026-09-11）。
    """
    cfg.check_legal()
    if rng is None:
        rng = np.random.default_rng(cfg.seed)
    if chip is None:
        chip = build_split_chip(cfg)

    core = DigitalCore(cfg)
    if state is not None:
        core.state.digital = state  # 允许外部注入数字状态（校准接力）
    sched = scheduler or Scheduler(cfg)
    allocation = sched.reserve(n_samples)
    conv_arr, acq_arr = sched.reserve_dual(n_samples, rng)

    dac = SplitDAC(cfg, chip)
    ra = ResidueAmplifier(cfg)
    ktc = KTCBranch(cfg)
    adc2 = ADC2(cfg)

    # ---- 电容口径（与 sim_split 同一套定义，单一事实来源见该文件注释）----
    c_mask = 0.0
    if cfg.dither_mode == "sampling" and cfg.dither_units_total > 0:
        _bank = chip.C_sub if cfg.dither_split_bank == "sub" else chip.C_main
        c_mask = float(_bank[-cfg.dither_units_total :].sum())
    c_sig = chip.A + chip.beta_true() * chip.B
    c_load = chip.A + chip.B - c_mask
    c_noise_eq = c_sig * c_sig / (chip.A + chip.beta_true() ** 2 * chip.B)
    # RA 增益经 ra.gain_vector 求值（同 sim_split 的理由）：cfg.ra_gain_model
    # 的 "fixed" 对照口径此前被本行的硬编码电容比静默忽略。默认 "charge"
    # 下 gain_vector 返回 c_sig/C_F，与原值逐位相同。
    g_vec = ra.gain_vector(np.full(n_samples, c_sig), chip.C_feedback_true)
    c_noise_vec = np.full(n_samples, c_noise_eq)

    pool = SlicePool(cfg, chip, rng, sched, c_load_active=c_load)

    # ---- 名义栅格与 SADC 阈值（**唯一真相源**：sadc.build_first_stage_quantizer）----
    # 历史问题（外部审计 B16）：本行曾写死 `d1_eff = cfg.dac_n_sub * step0`，
    # 而 sim_split 写死 `cfg.delta1`。b1=6 时两者数值巧合相等，掩盖了分歧；
    # 按论文读到 9b 后相差 8 倍，退化等价测试立刻失败。现在两条主循环
    # 统一调用共享构造器，步长由 units_per_first_stage_step() 单点定义。
    v_lo, v_hi = dac._nominal_endpoints()
    step0 = (v_hi - v_lo) / (dac.levels - 1)
    units_per_d1 = units_per_first_stage_step(cfg, dac)
    d1_eff = units_per_d1 * step0  # 一个第一级判决步的电压宽度
    sadc = build_first_stage_quantizer(cfg, dac)
    # 量化器通路缺陷显式施加在阈值上（SADC 构造器对显式阈值跳过失配参数，
    # pipeline 在这里统一处理）。语义：量化器 sDAC 的增益失配绕**其量程中点**
    # 缩放（sDAC 的零点在底板），失调为输入参考：
    #   thr_q = mid + ( (thr − mid) − offset ) / (1 + gm)
    # 注意 cfg.sadc_rdac_gain_mismatch 默认 1e-4 非零 —— 量化器决策在阈值
    # 附近 ±0.15 mV 内可能翻转（物理真实）。退化等价测试需显式置零。
    if cfg.sadc_offset != 0.0 or cfg.sadc_rdac_gain_mismatch != 0.0:
        mid = 0.5 * (v_lo + v_hi)
        sadc.thresholds = mid + (
            (sadc.thresholds - mid - cfg.sadc_offset) / (1.0 + cfg.sadc_rdac_gain_mismatch)
        )

    # ---- 采样（kT/C 噪声绑噪声等效电容；n_R 同一实现进两条通路）----
    sample = capture(cfg, input_fn, n_samples, rng, chip=None, c_active=c_noise_vec)

    # ---- sampling dither 注入重标定（与 sim_split 共用同一实现）----
    if cfg.dither_mode == "sampling":
        sampling_dither_injection(cfg, chip, sample, step0, c_sig)
    # 码域配对量：sampling = 掩码码（sampler 生成）；quantizer = 数字侧按
    # cfg.dither_quant_transfer 粒度取整转移（与 sim_split 同一实现口径）；
    # 其余模式 = 0。
    d_code = dither_transfer_code(
        cfg,
        np.asarray(sample.dither, dtype=float),
        step_rdac=step0,
        step_coarse=d1_eff,
        dither_code_sampling=sample.dither_code,
    )

    # ---- 逐相位循环：量化 -> 转换组提交 -> 采集组建立 ----
    t_s = cfg.dyn_t_sample_frac / cfg.fs
    tau0 = (cfg.dyn_r_source + cfg.dyn_r_on) * c_load
    tau0_q = (cfg.dyn_r_source + cfg.dyn_r_on) * pool.c_quant
    dyn_on = cfg.dyn_input_settling
    # timing skew 是采样时钟属性，与建立误差正交：只要开启就进入逐相位循环。
    # dx/dt 必须用**精确/谱**导数，不能用 np.gradient：中心差分的幅度响应是
    # sin(2πfin/fs)/(2πfin/fs)，在 19 MHz / 40 MHz 处只有 0.0524（低估约
    # 25.6 dB），会把近 Nyquist 的 skew 效应悄悄压掉（外部审计 A07.3）。
    # 见 sampler.input_derivative 与其回归测试。
    skew_on = cfg.slice_timing_skew_s > 0
    # 逐 slice offset 同理：只要开启就进入逐相位循环（与建立/ skew 正交）。
    offset_on = cfg.slice_offset_sigma_v > 0
    if skew_on:
        t_grid = np.arange(n_samples) / cfg.fs
        dxdt = input_derivative(input_fn, t_grid)
    else:
        dxdt = np.zeros(n_samples)

    x_sadc = np.asarray(sample.x_sadc, dtype=float)
    x_rdac = np.asarray(sample.x_rdac, dtype=float)
    coarse = np.zeros(n_samples, dtype=np.int64)
    e_input = np.zeros(n_samples)
    e_sadc = np.zeros(n_samples)
    if dyn_on or skew_on or offset_on:
        for n in range(n_samples):
            conv = conv_arr[n]
            acq = acq_arr[n]
            pool.invariants(conv, acq)
            # (1) 独立量化器建立 + flash 粗码（误差进入粗码决策）
            e_sadc[n] = pool.acquire_quantizer(n, float(x_sadc[n]), t_s, tau0_q)
            c_n = int(sadc.convert(np.array([x_sadc[n] + e_sadc[n]]))[0])
            coarse[n] = c_n
            k_n = c_n * cfg.units_per_lsb1 + float(d_code[n])
            # (2) 转换组提交：本样本转换结束，顶板保持名义残差
            vdn = float(dac.evaluate_nominal(np.array([k_n]))[0])
            pool.commit_residue(conv, x_rdac[n] - vdn)
            # (3) 采集组建立（v_top 来自这些 slice 上一次转换的提交）
            e_input[n] = pool.acquire(n, x_rdac[n], k_n, t_s, tau0, acq, dxdt_n=float(dxdt[n]))
    else:
        coarse = sadc.convert(x_sadc)

    # ---- 数字核：DEM + dither -> 开关指令（bank 内序号推进，512 状态全遍历）----
    cmd = core.switch_commands(coarse, allocation.bank, d_code)
    sid = cmd.sid
    k_eq = cmd.k + d_code

    # ---- 动态误差（参考建立 + 数字串扰；输入建立已由池逐 slice 计算）----
    v_nom = dac.evaluate_nominal(k_eq)
    dyn = apply_dynamics(
        dataclasses.replace(cfg, dyn_input_settling=False),
        x=x_rdac,
        v_prev=np.zeros(n_samples),
        v_nominal=v_nom,
        code=k_eq,
        sid=sid,
        c_active=np.full(n_samples, c_load),
        n_levels=dac.levels,
        n_units=cfg.dac_n_units,
        xtalk_profile=xtalk_profile(cfg, cfg.dac_n_units, rng),
        perm_fn=dac.full_order,
        c_xtalk_out=c_sig,
        c_load_ref=c_load,
    )

    # ---- DAC 两套求值 + 残差 + RA ----
    vd0 = v_nom
    vd_true = dac.evaluate_physical(k_eq, sid) + dyn.e_dac
    residue = (x_rdac + e_input) - vd_true
    vra, ra_sat = ra.evaluate(residue, rng, g=g_vec)

    # ---- KTC / ADC2 / 重构（与 sim_split 同一口径）----
    dx_obs = cfg.dither_alpha * sample.dx
    vnc, ktc_sat = ktc.observe(sample.n_R, dx_obs, rng)
    # 校正量在数字域扣除，不占用 ADC2 模拟量程（docs/adr/0006）
    fine, adc2_over = adc2.quantize_with_correction(vra, core.state.digital.kappa * vnc)

    from .mapper import DitherState, make_dither_state

    if cfg.dither_mode == "sampling":
        dither = DitherState(
            analog_injection=np.asarray(sample.dither, float),
            code_modification=np.zeros(n_samples),
            digital_correction=d_code * step0,
        )
    elif cfg.dither_mode == "quantizer":
        # 码修正已在 k_n 内完成（逐相位循环里 k_n = c·units_per_lsb1 + d_code），
        # 理想链路 vD0 + fine/Ĝ = x -> d_corr = 0；余项经 G 进 ADC2（stage21）。
        dither = DitherState(
            analog_injection=np.asarray(sample.dither, float),
            code_modification=d_code * step0,
            digital_correction=np.zeros(n_samples),
        )
    else:
        dither = make_dither_state(cfg, sample.dither)
    out = core.reconstruct(vd0, fine, dither.digital_correction, cfg.dither_alpha)

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
        state=core.state.digital,
        err_to_x1=err_x1,
        err_to_x2=err_x2,
        err_vs_clean=err_clean,
        g_vec=g_vec,
        c_active=c_noise_vec,
        calibration_applied=(),
    )
