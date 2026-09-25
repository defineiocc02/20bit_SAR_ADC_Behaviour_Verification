"""ktc.py -- 噪声观测、保持、校正（**我们准备研究的扩展，不是 ADI 已公开实现的模块**）。

记 x1 = x(t1)、x2 = x(t2)、dx = x2 - x1。RDAC 保存 x_R = x1 + n_R。
按正向残差约定建立两条观测：

    v_R = G_R (x1 + n_R - vD_true) + e_R
    v_N = G_N (n_R - dx)           + e_N
    v_2 = Q_2( v_R - kappa * v_N )

**同一个 n_R 必须进入两条观测**——这是仿真器对物理相关性的建模，
不是允许数字算法直接读取真实噪声。

推导结果（beta = kappa * G_N / G_R）：

    x_hat = x2 + (1 - beta)(n_R - dx) - e_D + (e_R - kappa e_N + q2) / G_R

| 项目                 | 输出中的表现              |
|----------------------|---------------------------|
| 原采样噪声           | (1 - beta) n_R            |
| 提取窗口内输入变化   | -(1 - beta) dx            |
| RDAC 失配            | -e_D，KTC 不会自动消除    |
| 新增观测噪声         | -kappa e_N / G_R          |

两点必须落实：
1. 两条通路**分别**检查摆幅，再相减；
2. 固定系数、固定时间间隔下 (1-beta)dx 首先是**线性传递函数变化**（ quadrature 项），
   系数随信号/码型/状态变化才引入额外非线性。
"""

from __future__ import annotations

import numpy as np

from .config import Config


class KTCBranch:
    """KTC 观测支路：噪声/斜率双通路观测、独立饱和、beta 前台校准。

    [研究扩展] 本支路为本仓库的**原创研究扩展**，不是 [00] 或专利
    [09]–[14] 披露的特性，**不得归属于 Analog Devices**（见仓库根目录
    NOTICE）。ktc_* 全部参数在 provenance.PARAM_GRADES 中分级为
    [研究扩展]，不得当作 ADI 已公开实现。

    Attributes:
        g_n:     观测通路增益 G_N·(1+beta_error) —— beta 失配**必须落在
                 物理通路上**（见 __init__ 内注释；写进公式是假仿真）。
        eta_n/x: 阶跃/斜坡建立系数（v4 修正，二者不等，见下）。
        kappa:   数字侧使用的标称值（数字侧**不知道** beta_error）。
        noise_n: 观测通路自身输出噪声 RMS [V]。

    单位契约：n_R / dx / v_N [V]；g_r [无量纲]。
    Side effects（构造时）：从 cfg 读取全部系数一次并固化——本类无逐样本
    状态，噪声按事件经 rng 生成。
    """

    def __init__(self, cfg: Config):
        """构造 KTC 观测支路；beta 失配落在物理通路上而非事后公式里。

        Args:
            cfg: 模型配置（Config）。KTC 全部系数自此读取一次并固化；
                 相关 ktc_* 参数来源分级均为 [研究扩展]，非 ADI 披露特性。
        Side effects: 从 cfg 读取并固化 g_n/eta_n/eta_x/kappa/noise_n。
        """
        self.cfg = cfg
        # **beta 失配必须落在物理通路上**：v1 只把它写进 x_ref 的公式，
        # 于是 ktc_beta_error 从 0 改到 0.2 时输出频谱纹丝不动 —— 那是假仿真。
        # 物理来源：噪声观测通路的增益 G_N 偏离标称（或 kappa 由电容实现时的失配）。
        # 数字侧仍按标称 kappa 扣除，二者的乘积偏差就是 beta 误差。
        self.g_n = cfg.ktc_gain_n * (1.0 + cfg.ktc_beta_error)
        # v4 审计修正：**阶跃与斜坡的建立系数不同**。
        #   n_R  : 窗口开始就存在的阶跃 -> eta_n = 1 - exp(-T_w/tau_a)
        #   dx   : 窗口内线性积累的斜坡 -> eta_x = 1 - (tau_a/T_w)(1 - exp(-T_w/tau_a))
        # 旧版把同一个 (1-eps) 同时乘两项，隐含"输入变化也是 t1 时刻的阶跃"。
        # 输出由此分解为两个独立的 beta：
        #   beta_n = kappa*G_N*eta_n / G_R  （决定噪声抵消）
        #   beta_x = kappa*G_N*eta_x / G_R  （决定信号时刻响应）
        # 把噪声校准到相消（beta_n=1）不必然把信号恢复到 x2（beta_x=1），反之亦然。
        self.eta_n = cfg.ktc_eta_n()
        self.eta_x = cfg.ktc_eta_x()
        self.kappa = cfg.kappa_eff()  # 数字侧使用的标称值
        self.noise_n = cfg.ktc_noise_n

    def beta_n_of(self, g_r) -> np.ndarray:
        """逐样本噪声抵消 beta_n = kappa * G_N * eta_n / G_R[n]。

        Args:
            g_r: 逐样本 RA 增益 G_R[n] [无量纲]；任意形状，内部转 float。
        Returns:
            beta_n [无量纲]，shape 同 g_r；决定噪声抵消深度
            （beta_n=1 时 (1-beta_n)*n_R 相消）。

        适用边界（独立审查 2026-09-25，B2）：入参须为正有限（由上方守卫保证）。
        但**输出有限**的额外前提是 g_r 不低到让商 kappa*G_N*eta_n/g_r 溢出——
        量级约 1e-300 以下（如 1e-308、nextafter(0,1)=5e-324）虽经守卫接受，
        商会溢出为 inf。这是数值现实而非守卫漏洞，故**不拒绝极小正值**
        （拒绝会误伤合法输入）。
        """
        g_r = np.asarray(g_r, dtype=float)
        if np.any(~np.isfinite(g_r)) or np.any(g_r <= 0):
            raise ValueError(
                "g_r 必须为正且有限（RA 增益）；非正值或 nan/inf 会使 beta_n 发散为 inf"
                "（独立审查 2026-09-25）"
            )
        return self.kappa * self.g_n * self.eta_n / g_r

    def beta_x_of(self, g_r) -> np.ndarray:
        """逐样本信号响应 beta_x = kappa * G_N * eta_x / G_R[n]。

        Args:
            g_r: 逐样本 RA 增益 G_R[n] [无量纲]。
        Returns:
            beta_x [无量纲]，shape 同 g_r；决定信号时刻响应
            （beta_x=1 时输出恢复到 x2，与噪声抵消相互独立）。

        适用边界（独立审查 2026-09-25，B2）：入参须为正有限（由上方守卫保证）。
        但**输出有限**的额外前提是 g_r 不低到让商 kappa*G_N*eta_x/g_r 溢出——
        量级约 1e-300 以下（如 1e-308、nextafter(0,1)=5e-324）虽经守卫接受，
        商会溢出为 inf。这是数值现实而非守卫漏洞，故**不拒绝极小正值**
        （拒绝会误伤合法输入）。
        """
        g_r = np.asarray(g_r, dtype=float)
        if np.any(~np.isfinite(g_r)) or np.any(g_r <= 0):
            raise ValueError(
                "g_r 必须为正且有限（RA 增益）；非正值或 nan/inf 会使 beta_x 发散为 inf"
                "（独立审查 2026-09-25）"
            )
        return self.kappa * self.g_n * self.eta_x / g_r

    def beta_of(self, g_r) -> np.ndarray:
        """兼容接口：旧单一 beta 现在指**噪声**侧 beta_n（校准噪声抵消时用）。

        Args:
            g_r: 逐样本 RA 增益 G_R[n] [无量纲]。
        Returns:
            beta_n [无量纲]，shape 同 g_r（等价 beta_n_of）。
        """
        return self.beta_n_of(g_r)

    def observe(
        self, n_R: np.ndarray, dx: np.ndarray, rng: np.random.Generator
    ) -> tuple[np.ndarray, np.ndarray]:
        """观测通路求值。返回 (v_N, sat)。KTC 关闭时固定返回 (0, False)。

        v_N = G_N * (eta_n * n_R  -  eta_x * dx) + e_N

        Args:
            n_R: 采样噪声实现 [V]——**必须与残差通路是同一份**
                 （三条底线③：相关性真实；传另一实现即是假仿真）。
            dx:  提取窗口内输入变化 x2-x1 [V]；sim_split/pipeline 传
                 dither_alpha·dx（KTC 观察衰减后采样域的口径，v5.1）。
            rng: e_N 生成器。
        Returns:
            (v_N [V] 已按 ra_v_clip 钳位, sat 逐样本布尔)。
        Side effects: 推进 rng。
        """
        n = n_R.shape[0]
        if not self.cfg.ktc_enable:
            return np.zeros(n), np.zeros(n, dtype=bool)

        v = self.g_n * (self.eta_n * n_R - self.eta_x * dx)
        if self.noise_n > 0:
            v = v + rng.normal(0.0, self.noise_n, n)
        sat = np.abs(v) >= self.cfg.ra_v_clip
        v = np.clip(v, -self.cfg.ra_v_clip, self.cfg.ra_v_clip)
        return v, sat

    # ---------------- beta 前台校准 ----------------
    @staticmethod
    def estimate_beta(out_minus_x1: np.ndarray, dx: np.ndarray) -> float:
        """对 dx 回归 (out - x1) 得到 beta。

        x_hat = x1 + beta*dx + (1-beta)*n_R - e_D + ...
        beta_hat = <out - x1, dx> / <dx, dx>

        v3 审计修正：旧版传入的 err 用的是 x_ref = x1 + beta_true*dx ——
        参考本身已经代入待估参数，估计出来的永远是 1。现在必须用
        **固定目标 x1**（t1 时刻的已知输入）构造回归量，
        噪声与 e_D 项在足够多样本下平均掉。

        Args:
            out_minus_x1: 输出减固定目标 x1 [V]（**禁用** x_ref 口径）。
            dx:           窗口内输入变化 [V]；Var(dx)=0 时返回 1.0
                          （不可观测的保守退化，避免除零）。
        Returns:
            beta_hat [无量纲]。
        Side effects: 无。
        """
        dx = np.asarray(dx, dtype=float)
        if np.var(dx) == 0:
            return 1.0
        y = np.asarray(out_minus_x1, dtype=float)
        return float(np.dot(y, dx) / np.dot(dx, dx))
