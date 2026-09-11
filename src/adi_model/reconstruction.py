"""reconstruction.py -- 粗细码合并、去 dither、校准。

核心重构式（内部全程电压单位，最后才编码成 20 bit）：

    x_hat = vD0 + v2 / G_hat - d_corr

G_hat 是数字端使用的增益（名义值或估计值），**不能默认等于真实增益**。

数字侧可见的只有：W_nominal、W_estimated、G_estimated。
**校准算法不得读取 chip.C_true**——真值只用于模拟物理电路与事后评分，
否则所谓"校准成功"只是代码直接拿到了答案。

单位契约：x_hat / vD0 / v2 / d_corr [V]；G_hat / alpha [无量纲]。
v2 是 ADC2 输出（RA 输出口径），除以 G_hat 折回残差口径再与 vD0 相加。

参数来源分级：
    G_hat 初值 = g0          [披露-推断]
    G_hat 估计（校准开启）    [拟合]（stage10 验收：回归量必须含 dither
                             名义项与 vd0，否则 33.6 -> 6.68 dB 的崩塌）
    alpha（dither 衰减）      [推导]（config.dither_alpha，由同一份掩码生成）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Config


@dataclass
class DigitalState:
    """数字域状态：增益估计、kappa、beta 与校准历史。"""

    estimated_gain: float
    kappa: float
    beta: float
    W_nominal: np.ndarray | None = None  # 名义权重（DAC 单位步长）
    W_estimated: np.ndarray | None = None  # 估计权重（v1 未使用，留接口）
    history: list | None = None


def initialize_state(cfg: Config) -> DigitalState:
    """按配置给出数字域的初始状态。

    Args:
        cfg: 模型配置（Config）。g0 / kappa_eff / beta_eff / rdac_step
             用于初始化估计增益、kappa、beta 与名义权重。
    Returns:
        DigitalState：estimated_gain=g0 [无量纲]、kappa=kappa_eff、
        beta=beta_eff [无量纲]、W_nominal=[rdac_step] [单位当量]、history=[]。
    """
    return DigitalState(
        estimated_gain=cfg.g0,
        kappa=cfg.kappa_eff(),
        beta=cfg.beta_eff(),
        W_nominal=np.array([cfg.rdac_step]),
        history=[],
    )


def reconstruct(
    vd_nominal: np.ndarray,
    fine_voltage: np.ndarray,
    g_hat: float,
    dither_correction: np.ndarray,
    alpha: float = 1.0,
) -> np.ndarray:
    """x̂ = ( vD0 + v2/Ĝ − d_corr ) / α

    α 是采样态 dither（专利 [10]）引入的**恒定**信号衰减 = (N−2D)/N。
    它必须先扣除已知 dither 再除，顺序不能颠倒：
        x̂_raw = α·x + d − e_D   →   x̂ = (x̂_raw − d)/α = x − e_D/α
    注意 α 会按 1/α 放大后端噪声与 RDAC 权重误差 —— 这就是该方案把
    "量程损失"换成"增益损失"之后真正的代价。输入注入模式 α ≡ 1。

    Args:
        vd_nominal:       粗码标称电压 vD0 [V]。
        fine_voltage:     ADC2 输出 v2 [V]（RA 输出口径，待除以 Ĝ）。
        g_hat:            数字端增益估计 Ĝ [无量纲]（名义值或校准值）。
        dither_correction: dither 名义扣除项 d_corr [V]。
        alpha:            采样态 dither 恒定衰减 α [无量纲]，默认 1。
    Returns:
        x̂ [V]，已折回输入口径。
    """
    return (vd_nominal + fine_voltage / g_hat - dither_correction) / alpha


class Calibrator:
    """前台校准（真实芯片用后台校准；v1 只验证接口与收益来源）。"""

    def __init__(self, cfg: Config, state: DigitalState):
        """绑定配置与数字状态（前台校准用）。

        Args:
            cfg:   模型配置（Config），提供 dither 相关口径与校准开关。
            state: 共享的 DigitalState；校准结果写回其 estimated_gain /
                   beta / kappa。
        Side effects: 无（仅绑定引用）。
        """
        self.cfg = cfg
        self.state = state

    def update_gain(
        self,
        x_known: np.ndarray,
        vd0: np.ndarray,
        fine: np.ndarray,
        d_known: np.ndarray | None = None,
        alpha: float | None = None,
    ) -> float:
        """最小二乘：fine 对 (alpha*x + d - vd0) 回归得到 G_actual。

            v_2 = G * (alpha*x + d_phys - vD_true) + noise
        数字端已知的自变量是 alpha*x + d_nom - vD0（d_nom = 名义 dither）。
        **v3 审计修正：d_known 必须传入。** 旧版回归量漏掉 d，dither 开启时
        dither 方差远大于 (x - vd0) 方差，斜率被严重拉低 —— 实测 analog dither
        下 G_hat = 6.68（真值 33.6），校准直接崩溃。

        **为什么不能拿 out 对 x 回归**（v1 的错误做法）：
            out = vD0 + v_2 / G_hat
        vD0 本身是 x 的阶梯函数，其平均斜率约等于 1，于是
            d(out)/d(x) = G/G_hat + (1 - G/G_hat) ≈ 1
        与 G 的真实偏差几乎无关。实测：G_real=32.16（+5e-3 误差）时
        该斜率只有 0.999962，校准量比正确值小 3 个数量级 —— 校准形同虚设。

        这个估计同时吸收级间增益误差与 RDAC 总电容偏差（满幅增益误差）。

        Args:
            x_known:   已知校准输入 x [V]（可直接获取）。
            vd0:       粗码标称电压 vD0 [V]。
            fine:      ADC2 输出 v2 [V]（RA 输出口径）。
            d_known:   可选 dither 名义项 d_nom [V]；缺失则退化为无 dither。
            alpha:     可选衰减 α [无量纲]；None = 1（仅乘信号项）。
        Returns:
            G_hat [无量纲]，更新后的增益估计（写入 state.estimated_gain）。
        Side effects: 写 state.estimated_gain（仅当 g>0）；denom<=0 时
            原样返回旧估计（不更新）。
        """
        x = np.asarray(x_known, dtype=float)
        # 回归量 = alpha*x + d_nom - vd0。**alpha 只乘信号项**：
        # 采样态 dither 是直接注入到保存电荷里的，不经过 alpha 衰减
        # （v3 自查：把 alpha 乘到整个 (x+d-vd0) 上，sampling 模式校准会偏 ~4%）。
        r = alpha * x if alpha is not None else x
        r = r - np.asarray(vd0, dtype=float)
        if d_known is not None:
            r = r + np.asarray(d_known, dtype=float)
        f = np.asarray(fine, dtype=float)
        denom = float(np.dot(r, r))
        if denom <= 0:
            return self.state.estimated_gain
        g = float(np.dot(f, r) / denom)
        if g > 0:
            self.state.estimated_gain = g
        return self.state.estimated_gain

    def update_beta(self, out: np.ndarray, x1: np.ndarray, dx: np.ndarray) -> float:
        """Beta 前台校准：对固定目标 x1 回归，再用 kappa_new = kappa/beta_hat 校正。

        x_hat = x1 + beta*dx + (1-beta)*n_R - e_D + ...
        beta_hat = <out - x1, dx> / <dx, dx>

        v3 审计修正（两条）：
        1. 旧版传入的 err 用 x_ref = x1 + beta_true*dx —— 参考已代入待估参数，
           估计结果恒为 1（实测 beta_true=1.2 时 beta_hat≈1.00013）。
           现在用**固定目标 x1**，噪声与 e_D 在多样本下平均掉。
        2. 校正规则改为 kappa_new = kappa_old / beta_hat：目标 beta=1，
           而 beta ∝ kappa，所以直接按比例缩。**不再读取 cfg.g_actual** ——
           数字侧不应知道真实模型参数。

        Args:
            out:   校准输出 x_hat [V]（已含噪声与 e_D）。
            x1:    固定目标 x1 = x(t1) [V]（禁用 x_ref 口径）。
            dx:    窗口内输入变化 x2−x1 [V]。
        Returns:
            beta_hat [无量纲]，当前实际 beta 的估计（写入 state.beta）。
        Side effects: 写 state.beta；按 kappa_new = kappa/beta_hat 写
            state.kappa（仅当 beta_hat>0）。
        """
        from .ktc import KTCBranch

        b = KTCBranch.estimate_beta(np.asarray(out) - np.asarray(x1), dx)
        self.state.beta = b  # 估计值（注意：这是"当前实际 beta"的估计）
        if b > 0:
            self.state.kappa = self.state.kappa / b
        return b
