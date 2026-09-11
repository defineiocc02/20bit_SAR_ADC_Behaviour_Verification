"""dynamics.py -- 动态误差：输入建立 / 参考建立 / 数字串扰。

[00_1]ISSCC2024_ppt.pdf p.32 明确点名这三项是限制 INL 的非理想源。
v1-v4 完全没有建模它们，所以模型算出的 |INL| = 0.07 LSB，
而论文实测 2.2 LSB —— 差 30 倍的缺口**主要就在这里**，而不是失配参数不对。

三项的共同特征（也是它们危险的原因）：

    **都与码相关（code-dependent）**，因此产生确定性的 INL 结构；
    **都不是静态失配**，因此既不能靠增大单位电容改善，也不能被 DEM 平均掉
    （共模部分）—— 必须靠建立时间预算 / 去耦电容 / 版图隔离 / 校准。

============================================================================
(a) 输入建立
============================================================================
采样相一阶 RC：tau = (R_source + R_on) * C_active，采样时长 T_s。
顶极板从上一次转换的残留电压 V_prev 出发向 x 建立，未建立完就断开：

    x_saved = x - eps * (x - V_prev),   eps = exp(-T_s / tau)

若 tau 与码无关，这只是一阶 IIR（对正弦是群延迟 + 微小衰减），
**不产生 INL**。真正的 INL 来自 tau 的码相关性：开关导通电阻随信号电平变
（V_GS 随输入摆），

    tau(k) = tau0 * (1 + rho * (2k/N - 1))

于是 eps(k) 随码变，误差 -eps(k)*(x - V_prev) 成为码相关的 INL。

============================================================================
(b) 参考建立
============================================================================
每次位试验都要从 V_ref 抽取电荷。片上去耦 C_dec 有限、参考缓冲恢复时间
常数 tau_ref 有限，于是参考电压在一次转换内被拉低且来不及完全恢复：

    静态分量（DC 负载）  ∝ (2k/N - 1)        -> 关于中码奇对称
    动态分量（码跳变）   ∝ (k[n] - k[n-1])/N -> 与输入斜率相关

恢复因子 eta_ref = 1 - exp(-T_conv/tau_ref)：转换相结束时残留的比例。
DAC 输出正比于实际参考电压，于是

    e_ref = -v_nominal * gamma * (静态 + r_dyn * 动态)

静态分量使 DAC 输出含 k^2 项 -> **抛物线形 INL**（经典 bow），
这是高精度 SAR 最常见的 INL 形状之一，也是"为什么 INL 图是弓形"的答案。

============================================================================
(c) 数字串扰
============================================================================
数字走线经耦合电容往顶极板注入电荷。单调切换下一次转换的单位翻转数为

    A(k) = 2 * min(k, N - k)      （三角形，中码最大）

注入电荷 = c_xtalk * V_digital * A(k)，折成电压 /C_eff。
分两部分：
    共模部分 c_xtalk_common * A(k)      -> DEM **无效**（只取决于翻转个数）
    单位部分 sum_{翻转的单位} c_x_unit  -> DEM **有效**（置换单位即置换注入量）

这正是"为什么 DEM 开/关时 INL 形状会变"的物理来源。
单位契约：三项误差均以 [V] 折入相应通路（e_input 进残差信号侧、
e_dac 进 DAC 物理侧、参考/串扰按电荷口径折 C_sig）。
参数来源分级：dyn_r_source/r_on/ron_code_coeff/c_xtalk_* 全部 [假设]
（[00_1] p.32 只点名机理不给数值；只能做灵敏度排序，不能判良率）。
适用域：一阶 RC 聚合口径。注意"聚合式偏保守"只在支路开关阻抗主导时成立；
公共源阻抗项 R_s·C_load 不随 slice 划分缩小（星形网络公共模式
τ = R_s·C_total + R_on·C_slice，见 pipeline.py 头部适用域声明与
docs/review_response_2026-09-11d.md §5.2）；确定性 INL 协议见 stage13。

"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import Config


# ==========================================================================
# (a) 输入建立
# ==========================================================================
def input_settling_eps(
    cfg: Config,
    c_active: np.ndarray | float,
    code: np.ndarray | None = None,
    n_levels: int | None = None,
) -> np.ndarray:
    """返回逐样本的建立残差 eps（0 = 完全建立）。

    tau = (R_source + R_on) * C_active；若给了 code 且 rho>0，则 tau 随码调制。

    Args:
        cfg: Config；取 dyn_r_source / dyn_r_on / dyn_t_sample_frac /
            dyn_ron_code_coeff（均 [假设]，无公开数值）。
        c_active: 输入建立的**驱动负载**电容口径 [F]；split 用 A+B。
        code: 可选码数组 [单位当量]；给出且 rho≠0 时 tau 随码调制，
            None 表示 tau 与码无关（不产生 INL）。
        n_levels: 可选总电平数 N [无量纲]；默认 cfg.dac_levels。

    Returns:
        逐样本建立残差 eps [无量纲] = exp(-T_s/tau)；0 = 完全建立，
        越接近 1 表示采样相结束时残留越多。
    Side effects: 无（纯函数）。
    """
    t_s = cfg.dyn_t_sample_frac / cfg.fs
    r = cfg.dyn_r_source + cfg.dyn_r_on
    c = np.asarray(c_active, dtype=float)
    tau = r * c
    if code is not None and cfg.dyn_ron_code_coeff != 0.0:
        k = np.asarray(code, dtype=float)
        n = float(n_levels or cfg.dac_levels)
        tau = tau * (1.0 + cfg.dyn_ron_code_coeff * (2.0 * k / n - 1.0))
    tau = np.maximum(tau, 1e-18)
    return np.exp(-t_s / tau)


def input_settling_error(
    cfg: Config, x: np.ndarray, v_prev: np.ndarray, c_active, code=None, n_levels=None
) -> np.ndarray:
    """采样值上残留的误差：x_saved - x = -eps * (x - V_prev)。

    Args:
        cfg: Config（取 dyn_input_settling 开关与 input_settling_eps 所需
            参数，均 [假设]）。
        x: 本样本输入电压 [V]。
        v_prev: 上一次转换残留到采样电容顶极板的电压 [V]。
        c_active: 驱动负载电容口径 [F]；split 用 A+B。
        code: 可选码数组 [单位当量]；用于 tau 的码调制。
        n_levels: 可选总电平数 N [无量纲]。

    Returns:
        采样值残留误差数组 [V] = -eps*(x - v_prev)；dyn_input_settling
        关闭时返回全 0。
    Side effects: 无（纯函数）。
    """
    if not cfg.dyn_input_settling:
        return np.zeros_like(np.asarray(x, dtype=float))
    eps = input_settling_eps(cfg, c_active, code, n_levels)
    return -eps * (np.asarray(x, dtype=float) - np.asarray(v_prev, dtype=float))


# ==========================================================================
# (b) 参考建立
# ==========================================================================
def ref_recovery_factor(cfg: Config) -> float:
    """转换相结束时参考还没恢复回来的比例 eta_ref（0 = 完全恢复）。

    Args:
        cfg: Config；取 dyn_tau_ref（参考缓冲恢复时间常数 [s]，[假设]）
            与 dyn_t_conv_frac（转换相时长占比，[假设]）。

    Returns:
        残余压降比例 eta_ref [无量纲] = exp(-T_conv/tau_ref)；
        dyn_tau_ref ≤ 0（理想无限带宽参考）时返回 0（完全恢复）。
    Side effects: 无（纯函数）。
    """
    t_conv = cfg.dyn_t_conv_frac / cfg.fs
    if cfg.dyn_tau_ref <= 0:
        return 0.0
    return math.exp(-t_conv / cfg.dyn_tau_ref)


def bitwise_eta_dyn(cfg: Config) -> float:
    """逐位装载下**动态分量**的残余压降因子（相对单次抽取的改善由此而来）。

    论文："conversion results are loaded as they develop" —— 跟随器逐位
    装载：SADC 结果分 B 位步写入 RDAC，第 i 步抽取总跳变电荷的 w_i 份
    （SAR 次序 -> w_i = 2^-i，MSB 最大），到转换结束恢复了
    exp(−(B−i)/B·T_conv/τ)。等效残余因子：

        eta_dyn = Σ_i w_i·exp(−(B−i)/B·T_conv/τ)   vs   单次抽取 exp(−T_conv/τ)

    早抽取的位有更多恢复时间 -> eta_dyn >> eta_single（τ_ref >> T_conv 时
    改善 ~ln2·B/... 量级）。    **静态分量不受影响**（DC 负载贯穿整个转换相，
    与装载时序无关）—— 口径：只建终态；转换中途瞬态不在行为级预测范围。

    Args:
        cfg: Config；取 dyn_tau_ref [s]、dyn_t_conv_frac、rdac_bitwise_bits
            B（均 [假设]）。

    Returns:
        动态分量残余压降因子 [无量纲] = Σ_i w_i·exp(-(B-i)/B·T_conv/τ)；
        tau_ref ≤ 0 时返回 0。逐位装载下远小于单次抽取的 exp(-T_conv/τ)。
    Side effects: 无（纯函数）。
    """
    t_conv = cfg.dyn_t_conv_frac / cfg.fs
    if cfg.dyn_tau_ref <= 0:
        return 0.0
    B = max(int(cfg.rdac_bitwise_bits), 1)
    i = np.arange(1, B + 1)
    w = 2.0 ** (-i)
    w = w / w.sum()
    return float(np.sum(w * np.exp(-(B - i) / B * t_conv / cfg.dyn_tau_ref)))


def bitwise_peak_ratio(cfg: Config) -> float:
    """逐位装载的**峰值瞬时电荷需求 / 单次装载**（参考缓冲裕量指标）。

    单次装载峰值 = 总跳变电荷；逐位装载峰值 = 最大位步 w_1 ≈ 0.5。

    Args:
        cfg: Config；取 rdac_bitwise_bits B（[假设]，假定等于第一级位数）。

    Returns:
        峰值瞬时电荷需求 / 单次装载 的比值 [无量纲] ≈ 0.5（B 位步时）；
        参考缓冲裕量指标，越小越好。
    Side effects: 无（纯函数）。
    """
    B = max(int(cfg.rdac_bitwise_bits), 1)
    w = 2.0 ** (-np.arange(1, B + 1))
    return float(w.max() / w.sum())


def ref_settling_error(
    cfg: Config,
    v_nominal: np.ndarray,
    code: np.ndarray,
    n_levels: int | None = None,
    c_load_ref: float | None = None,
) -> np.ndarray:
    """参考未恢复造成的 DAC 输出误差（V，输入等效口径）。

    gamma = C_drawn / C_dec * eta_ref —— 单位电荷抽取造成的相对压降。
    v5 第二轮审计：C_drawn 是实际被开关的电容（split 用 A+B），
    不能与信号电荷系数 C_sig 混用；缺省退回 c_total0 代理（unary 口径）。
    静态分量用 (2k/N - 1)（对中码奇对称），动态分量用码差分（对斜率敏感）。
    逐位装载（rdac_bitwise_loading）：动态分量的抽取分 B 位步进行、每步在
    剩余时间恢复 -> gamma_dyn 用 bitwise_eta_dyn；静态分量不变（DC 负载
    贯穿转换相，与装载时序无关）。终态码相同 -> 只改动态分量。

    Args:
        cfg: Config；取 dyn_c_decouple [F]、dyn_ref_dynamic_ratio、
            dyn_tau_ref、rdac_bitwise_loading / rdac_bitwise_bits（均 [假设]）。
        v_nominal: 名义 DAC 输出电压 [V]。
        code: 码数组 [单位当量]。
        n_levels: 可选总电平数 N [无量纲]；默认 cfg.dac_levels。
        c_load_ref: 可选被开关电容口径 [F]；缺省退回 c_total0·cap_scale
            （unary 口径，v5 第二轮审计）。

    Returns:
        参考未恢复造成的 DAC 输出误差 [V]（输入等效口径）；
        dyn_ref_settling 关闭时返回全 0。
    Side effects: 无（纯函数）。
    """
    if not cfg.dyn_ref_settling:
        return np.zeros_like(np.asarray(v_nominal, dtype=float))
    n = float(n_levels or cfg.dac_levels)
    k = np.asarray(code, dtype=float)
    eta = ref_recovery_factor(cfg)
    c_eff = c_load_ref if c_load_ref is not None else cfg.c_total0 * cfg.cap_scale

    static = 2.0 * k / n - 1.0
    dk = np.diff(k, prepend=k[0])
    dynamic = dk / n
    # 静态分量：DC 负载贯穿转换相，装载时序无关 -> 恒用 eta；
    # 动态分量：逐位装载时早抽取的位恢复更充分 -> 用 bitwise_eta_dyn。
    # 极限自洽：tau_ref→∞ 或 t_conv→0 时 eta 与 bitwise_eta_dyn 同时 →1。
    eta_dyn = bitwise_eta_dyn(cfg) if cfg.rdac_bitwise_loading else eta
    g_st = (c_eff / cfg.dyn_c_decouple) * eta
    g_dy = (c_eff / cfg.dyn_c_decouple) * eta_dyn
    load = g_st * static + g_dy * cfg.dyn_ref_dynamic_ratio * dynamic
    return -np.asarray(v_nominal, dtype=float) * load


# ==========================================================================
# (c) 数字串扰
# ==========================================================================
def switching_activity(cfg: Config, code: np.ndarray, n_levels: int | None = None) -> np.ndarray:
    """单调切换下每次转换的单位翻转数 A(k) = 2*min(k, N-k)。

    Args:
        cfg: Config；取 dac_levels / n_units_sig 作默认 N（[推导]）。
        code: 码数组 [单位当量]；单调切换下前 k 个单位置位、后 N-k 清零。
        n_levels: 可选总电平数 N [无量纲]；默认 cfg.dac_levels。

    Returns:
        逐样本单位翻转数 A(k) [无量纲]（三角形，中码最大）；串扰共模分量
        的驱动项，DEM 无效（只取决于翻转个数）。
    Side effects: 无（纯函数）。
    """
    n = float(n_levels or cfg.dac_levels)
    k = np.clip(np.asarray(code, dtype=float), 0.0, n)
    return 2.0 * np.minimum(k, n - k)


def crosstalk_error(
    cfg: Config,
    code: np.ndarray,
    sid: np.ndarray,
    n_levels: int | None = None,
    n_units: int | None = None,
    unit_xtalk_profile: np.ndarray | None = None,
    perm_fn=None,
    c_out: float | None = None,
) -> np.ndarray:
    """数字串扰注入折成的 DAC 输出误差（V，输入等效口径）。

    注入电荷 = c_x * V_digital * 翻转单位耦合电容之和，折成电压应除
    **信号电荷系数 C_sig**（输入等效口径，v5 第二轮审计）——
    旧代码除 c_total0 代理，把顶板电压口径与输入等效口径混用。
    缺省退回 c_total0·cap_scale（unary 兼容）。
    共模部分 ∝ A(k)                —— DEM 无效
    单位部分 ∝ 被翻转单位的耦合电容之和 —— DEM 有效（置换即改变注入量）

    Args:
        cfg: Config；取 dyn_c_xtalk_common / dyn_c_xtalk_unit [F]、
            dyn_v_digital [V]、dyn_crosstalk 开关（均 [假设]）。
        code: 码数组 [单位当量]。
        sid: 逐样本 slice 标签数组（决定 DEM 排列）。
        n_levels: 可选总电平数 N [无量纲]。
        n_units: 可选单位总数 [无量纲]。
        unit_xtalk_profile: 每单位耦合电容空间分布 [F]（DEM 随机化来源）。
        perm_fn: 可调用 perm_fn(slice_id) -> 该 slice 的单位排列。
        c_out: 可选折电压口径 [F]；缺省 c_total0·cap_scale（输入等效口径）。

    Returns:
        数字串扰注入折成的 DAC 输出误差 [V]（输入等效口径，除 C_sig）；
        dyn_crosstalk 关闭时返回全 0。
    Side effects: 无（纯函数）。
    """
    if not cfg.dyn_crosstalk:
        return np.zeros_like(np.asarray(code, dtype=float))
    c_ref = c_out if c_out is not None else cfg.c_total0 * cfg.cap_scale
    a_k = switching_activity(cfg, code, n_levels)

    e_common = cfg.dyn_c_xtalk_common * cfg.dyn_v_digital * a_k

    e_unit = np.zeros_like(a_k)
    if cfg.dyn_c_xtalk_unit > 0 and unit_xtalk_profile is not None and perm_fn is not None:
        # 翻转的是"前 A(k)/2 个单位"（单调切换：先置位再清零），
        # DEM 决定它们是哪些物理单位 -> 注入量随之改变。
        prof = np.asarray(unit_xtalk_profile, dtype=float)
        sid = np.asarray(sid, dtype=np.int64)
        # v5 审计修正（量纲 + 口径双重错误）：
        # 1) unit_xtalk_profile 已经是**每单位耦合电容的空间分布**
        #    dyn_c_xtalk_unit*(1+0.5*grad)，单位 [F]。原实现又乘了一次
        #    dyn_c_xtalk_unit，使 e_unit 的量纲变成 [F^2 * V]，除以 c_eff[F]
        #    后仍余一个法拉 —— 数值被 5e-19 压掉约 18 个数量级，
        #    等于把"单位串扰可被 DEM 随机化"这一物理效应整体抹除。
        # 2) 原实现用"总均值 x 翻转数"近似；正确的注入量是被翻转单位
        #    的耦合电容**之和** units_sum = Σ_{j<=A(k)} C_x,perm(i,j)，
        #    它由 DEM 排列决定，因此天然携带 DEM 随机化效果。
        for s in np.unique(sid):
            m = sid == s
            if not m.any():
                continue
            order = np.asarray(perm_fn(int(s)), dtype=np.int64)
            cum = np.concatenate([[0.0], np.cumsum(prof[order])])
            n_sel = np.clip(np.round(a_k[m]).astype(np.int64), 0, prof.size)
            e_unit[m] = cfg.dyn_v_digital * cum[n_sel]
    return (e_common + e_unit) / c_ref


def _dem_fluctuation(prof: np.ndarray, sid: np.ndarray, frac: np.ndarray, perm_fn) -> np.ndarray:
    """DEM 造成的单位耦合电容抽样波动：抽 frac 比例的单位的均值 - 总均值。

    Args:
        prof: 每单位耦合电容空间分布 [F]。
        sid: 逐样本 slice 标签数组。
        frac: 逐样本抽样比例 [0..1，无量纲]（如 A(k)/2 / n_units）。
        perm_fn: 可调用 perm_fn(slice_id) -> 该 slice 的单位排列。

    Returns:
        逐样本 DEM 波动量 [F] = 抽中单位的均值耦合电容 - 总均值；
        反映单位串扰被 DEM 随机化的幅度（共模部分恒为 0）。
    Side effects: 无（纯函数）。
    """
    if prof.size == 0:
        return np.zeros(len(sid))
    out = np.zeros(len(sid))
    uq = np.unique(sid)
    for s in uq:
        m = sid == s
        if not m.any():
            continue
        order = perm_fn(int(s))
        p = prof[order]
        cum = np.concatenate([[0.0], np.cumsum(p)])
        nk = np.clip(frac[m] * prof.size, 0.0, float(prof.size) - 1e-9)
        i0 = np.floor(nk).astype(np.int64)
        f = nk - i0
        sel_mean = (cum[i0] + f * (cum[i0 + 1] - cum[i0])) / np.maximum(nk, 1e-9)
        out[m] = sel_mean - float(prof.mean())
    return out


# ==========================================================================
# 三项的合成接口（sim.py 用）
# ==========================================================================
@dataclass
class DynamicsResult:
    """动态误差的三个分量：采样端、参考端与数字串扰。"""

    e_input: np.ndarray  # 加在采样值上（V）
    e_ref: np.ndarray  # 加在 DAC 输出上（V）
    e_xtalk: np.ndarray  # 加在 DAC 输出上（V）
    eps_settle: np.ndarray  # 输入建立残差（诊断用）
    activity: np.ndarray  # 单位翻转数（诊断用）

    @property
    def e_dac(self) -> np.ndarray:
        """加在 DAC 输出上的动态误差总和 = 参考未恢复 + 串扰。

        Returns:
            e_ref + e_xtalk 数组 [V]，即 DAC 物理侧承受的全部动态误差
            （输入建立误差 e_input 折在采样信号侧，不计入本量）。
        """
        return self.e_ref + self.e_xtalk


def apply_dynamics(
    cfg: Config,
    x: np.ndarray,
    v_prev: np.ndarray,
    v_nominal: np.ndarray,
    code: np.ndarray,
    sid: np.ndarray,
    c_active,
    n_levels=None,
    n_units=None,
    xtalk_profile=None,
    perm_fn=None,
    c_xtalk_out: float | None = None,
    c_load_ref: float | None = None,
) -> DynamicsResult:
    """三项一次算齐。任何一项关闭时返回 0 向量，不产生副作用。

    c_active：输入建立的**驱动负载**口径（split 用 A+B）；
    c_xtalk_out：串扰电荷折电压的口径（split 用 C_sig，输入等效）；
    c_load_ref：参考建立被开关电容口径（split 用 A+B）。
    三个口径物理上不同，不得共用一个数（v5 第二轮审计 §3）。

    Args:
        cfg: Config（各动态开关与参数，均 [假设]）。
        x: 输入电压数组 [V]。
        v_prev: 上次转换残留电压数组 [V]。
        v_nominal: 名义 DAC 输出电压数组 [V]。
        code: 码数组 [单位当量]。
        sid: 逐样本 slice 标签数组。
        c_active: 输入建立驱动负载电容口径 [F]；split 用 A+B。
        n_levels: 可选总电平数 N [无量纲]。
        n_units: 可选单位总数 [无量纲]。
        xtalk_profile: 单位耦合电容分布 [F]（串扰用）。
        perm_fn: DEM 排列函数。
        c_xtalk_out: 串扰折电压口径 [F]；split 用 C_sig（输入等效）。
        c_load_ref: 参考建立被开关电容口径 [F]；split 用 A+B。

    Returns:
        DynamicsResult，含 e_input [V]、e_ref [V]、e_xtalk [V]、eps_settle
        [无量纲]、activity [无量纲]。任一开关关闭时对应分量返回 0 向量。
    Side effects: 无（纯函数，不改任何传入对象）。
    """
    eps = (
        input_settling_eps(cfg, c_active, code, n_levels)
        if cfg.dyn_input_settling
        else np.zeros_like(np.asarray(x, dtype=float))
    )
    e_in = input_settling_error(cfg, x, v_prev, c_active, code, n_levels)
    e_ref = ref_settling_error(cfg, v_nominal, code, n_levels, c_load_ref)
    e_xt = crosstalk_error(
        cfg, code, sid, n_levels, n_units, xtalk_profile, perm_fn, c_out=c_xtalk_out
    )
    act = switching_activity(cfg, code, n_levels) if cfg.dyn_crosstalk else np.zeros_like(e_in)
    return DynamicsResult(e_input=e_in, e_ref=e_ref, e_xtalk=e_xt, eps_settle=eps, activity=act)
