"""dac_arch.py -- DAC 拓扑：纯等权 unary  vs  分段（主/子）+ 桥接电容 C_C。

============================================================================
为什么 v1-v4 的纯等权 unary 拓扑不足以指导设计
============================================================================

v1-v4 的 RDAC 是 N 个**等权**单位电容。模型里没问题，但它系统性掩盖了
高精度 CDAC 的三个真实约束：

1. **面积—失配的死结**：等权阵列要 L 个电平就要 L 个单位。总面积固定
   （= 采样电容，由 kT/C 锁定）时，单位数越多 -> 单位电容越小
   -> Pelgrom 失配 sigma_eps ∝ 1/sqrt(A_u) 越大。
   分段结构用 n_main x n_sub 个电平只花 n_main+n_sub 个单位，
   单位电容可以大得多 -> 失配直接小 sqrt(倍数)。这是分段结构的根本收益。

2. **桥接电容 C_C 的比例误差**（等权阵列根本没有这一项）：
   子阵列经 C_C 衰减后接入主阵列，衰减系数
        beta = C_C / (C_C + C_sub_total + C_p_sub)
   C_C 的相对误差 delta_C 直接变成**子阵列整体权重的比例误差**：
        delta_w_S / w_S ≈ (1 - beta) * delta_C
   这是一阶**系统性** INL：周期 = 主阵列一个 LSB 的锯齿，
   **DEM 完全无效**（DEM 只置换单位，不改变 C_C），只能校准或靠匹配硬扛。

3. **主/子边界残差**（专利 [11] 的核心难点）：
   主、子阵列各自 DEM 后各自逼近**自己的平均值**，两个平均值未必相等；
   再叠加 C_C 的比例误差，边界处留下 DEM 消不掉的台阶。
   本模块把它做成**可观测量**（boundary_error）。

============================================================================
传输关系（两浮动节点严格解，非近似）
============================================================================

单位底板驱动电压 ±V_FS。记

    A = sum(C_main)        B = sum(C_sub)
    S_M = V_FS * (2*sel_M - A)      （选中接 +V_FS，未选中接 -V_FS）
    S_S = V_FS * (2*sel_S - B)
    D   = C_C + B + C_p_sub
    beta   = C_C / D
    C_ser  = C_C * (B + C_p_sub) / D        （C_C 与子阵列总电容的串联）
    C_eff  = A + C_p_main + C_ser

两个浮动节点电荷守恒联立解得（严格，非近似）：

    V_top = ( S_M + beta * S_S ) / C_eff        （顶极板电压口径）

放大相逐相位节点方程（charge_ref.py 独立推导，P 虚地/Q 浮置）：

    C_F * v_R = (A + beta*B)*x - (S_M + beta*S_S) = C_sig*x - Q_D

**信号链统一用输入等效口径** v_D,in = Q_D/C_sig（v5 第二轮审计）：
残差 = x - v_D,in，RA 增益 = C_sig/C_F。顶板口径与输入等效口径
差 C_sig/C_eff ≈ 0.9972 —— 混用曾使模拟残差假性非零 ~143 mV
（数字端自洽重构掩盖了它，"理想重构正确"不能证明中间节点正确）。

C_C -> 无穷大或 n_sub = 0 时退化为等权 unary：V_top = V_FS*(2*sel/A - 1)，
与 rdac.py 的旧公式完全一致 —— unary 是本模型的一个特例，不是另一套代码。

名义设计条件（子阵列恰好覆盖主阵列一个 LSB）：
    n_sub * w_S = w_M  ->  beta * c_uS = c_uM / n_sub
    等单位电容时 beta = 1/n_sub  ->  C_C_nom = (n_sub*c_u + C_p_sub)/(n_sub - 1)
    即经典的"桥接电容 ≈ 单位电容"（n_sub 大时）。

名义值口径（数字侧唯一可见的公式）：

    vD0(k) = Q_D_nom(k)/C_sig_nom = -V_FS + k * Δ_nom,
    Δ_nom = 2*beta*c_u*V_FS/C_sig  （均匀；v_hi = -V_FS + (levels-1)·Δ_nom < +V_FS）

即输入等效口径下名义传输从 -V_FS 出发、以均匀步长 Δ_nom 铺开；
顶码到不了 +V_FS（k=levels-1 只选通 n_main-1 + n_sub-1 个单位），
这就是输入等效口径下的满幅收缩（≈1.7%，两端不对称）。
物理值与它的差就是全部 DAC 误差：单位失配（DEM 可平均）、
C_C 比例误差（锯齿 INL）、C_sig 增益误差（只能校准）。
单位契约：C_main/C_sub/C_C [F]；k_m/k_s [主/子单位当量]；v_D [V]。
C_F 名义必须定义在**信号电荷系数**上：(n_main+β·n_sub)·c_u/g0
（v5 审计修正：旧定义引入 -1.4% 系统增益误差）。
参数来源分级：dac_n_main/n_sub=64/8 [假设]（PPT 未给分段比）；
parasitic/bridge_mismatch [假设]；Pelgrom 面积律 [披露-物理规律]。

"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config

# 主/子用**不同的**轮转步长：这正是专利 [11] 描述的"各自 DEM"，
# 也是主/子平均值不等（边界残差）的来源之一。两步长与 512 互质且互异。
_A_MAIN = 331
_A_SUB = 173


# ==========================================================================
# 物理真值
# ==========================================================================
@dataclass
class SplitChip:
    """分段 DAC 的一颗虚拟芯片的真值（数字侧不得读取）。"""

    n_main: int
    n_sub: int
    c_unit_nom: float

    C_main: np.ndarray  # (n_main,) 实际主阵列单位电容 [F]
    C_sub: np.ndarray  # (n_sub,)  实际子阵列单位电容 [F]
    C_bridge: float  # 桥接电容实际值 [F]
    C_bridge_nom: float  # 桥接电容名义值 [F]
    c_p_main: float  # 主顶极板寄生（实际）
    c_p_sub: float  # 子顶极板寄生（实际）
    c_p_main_nom: float
    c_p_sub_nom: float
    C_feedback_true: float  # RA 反馈电容实际值 [F]
    C_feedback_nominal: float

    eps_main: np.ndarray | None = None
    eps_sub: np.ndarray | None = None
    breakdown: dict = field(default_factory=dict)

    # ---------------- 事后评分 ----------------
    @property
    def A(self) -> float:
        """主阵列总电容（论文记号 A）。

        Returns:
            主阵列总电容 A = sum(C_main) [F]。
        """
        return float(self.C_main.sum())

    @property
    def B(self) -> float:
        """子阵列总电容（论文记号 B）。

        Returns:
            子阵列总电容 B = sum(C_sub) [F]。
        """
        return float(self.C_sub.sum())

    def beta_true(self) -> float:
        """实测桥接衰减系数 C_C/(C_C + B + cp_sub)。

        Returns:
            实测桥接衰减系数 beta [无量纲]，范围 (0,1)；分母为 0 时返回 0.0。
        """
        d = self.C_bridge + self.B + self.c_p_sub
        return self.C_bridge / d if d > 0 else 0.0

    def beta_nom(self) -> float:
        """名义桥接衰减系数（设计值，算法不得使用实测值）。

        Returns:
            名义桥接衰减系数 beta_nom = C_bridge_nom/(C_bridge_nom + B_nom +
            c_p_sub_nom) [无量纲]。
        """
        bn = self.B_nominal()
        d = self.C_bridge_nom + bn + self.c_p_sub_nom
        return self.C_bridge_nom / d if d > 0 else 0.0

    def B_nominal(self) -> float:
        """子阵列名义总电容 n_sub·c_unit_nom。

        Returns:
            子阵列名义总电容 [F] = n_sub · c_unit_nom。
        """
        return self.n_sub * self.c_unit_nom

    def c_sig_true(self) -> float:
        """信号电荷系数（输入等效口径）：C_sig = A + beta*B。

        独立节点方程（charge_ref.py，放大相 P 虚地/Q 浮置）给出
            C_F * v_R = C_sig * x - Q_D,   Q_D = S_M + beta*S_S。
        这里的 beta 与顶板方程里的衰减系数是同一个（C_C/(C_C+B+cp_sub)）。

        Returns:
            信号电荷系数 C_sig_true = A + beta_true·B [F]（输入等效口径，
            非顶板等效电容，见 c_eff_true）。
        """
        return self.A + self.beta_true() * self.B

    def c_sig_nom(self) -> float:
        """名义信号电荷系数 C_sig = A + beta·B（输入等效口径）。

        Returns:
            名义信号电荷系数 C_sig_nom [F] = n_main·c_unit_nom + beta_nom·B_nom。
        """
        bn = self.B_nominal()
        d = self.C_bridge_nom + bn + self.c_p_sub_nom
        beta = self.C_bridge_nom / d if d > 0 else 0.0
        return self.n_main * self.c_unit_nom + beta * bn

    def c_eff_true(self) -> float:
        """实测顶板等效电容（仅供网络分析，不进信号链）。

        Returns:
            实测顶板等效电容 C_eff = A + c_p_main + C_C·(B+c_p_sub)/D [F]；
            D = C_bridge + B + c_p_sub。
        """
        b = self.B
        d = self.C_bridge + b + self.c_p_sub
        c_ser = self.C_bridge * (b + self.c_p_sub) / d if d > 0 else 0.0
        return self.A + self.c_p_main + c_ser

    def c_eff_nom(self) -> float:
        """名义顶板等效电容。

        Returns:
            名义顶板等效电容 C_eff_nom [F] = n_main·c_unit_nom + c_p_main_nom
            + c_ser（c_ser 为名义串联等效）。
        """
        bn = self.B_nominal()
        d = self.C_bridge_nom + bn + self.c_p_sub_nom
        c_ser = self.C_bridge_nom * (bn + self.c_p_sub_nom) / d if d > 0 else 0.0
        return self.n_main * self.c_unit_nom + self.c_p_main_nom + c_ser

    def bridge_relative_error(self) -> float:
        """C_C 的相对误差（事后真值，算法不得使用）。

        Returns:
            桥接电容相对误差 C_bridge/C_bridge_nom − 1 [无量纲]。
        """
        return self.C_bridge / self.C_bridge_nom - 1.0

    def sub_weight_relative_error(self) -> float:
        """子阵列**整体权重**的相对误差（输入等效口径）。

        (beta*B/C_sig) / (beta_nom*B_nom/C_sig_nom) - 1
        这是分段结构里 DEM 完全无效、只能靠匹配或校准的那一阶误差。

        Returns:
            子阵列整体权重相对误差 [无量纲]（DEM 不可平均，只能靠匹配/校准）。
        """
        w = self.beta_true() * self.B / self.c_sig_true()
        w0 = self.beta_nom() * self.B_nominal() / self.c_sig_nom()
        return w / w0 - 1.0

    def gain_error(self) -> float:
        """满幅增益误差（输入等效口径）：C_sig_true/C_sig_nom - 1。

        只能校准，DEM 无效。顶板口径的 c_eff 之比不再是增益误差 ——
        信号电荷系数是 C_sig（charge_ref.py 节点方程）。

        Returns:
            满幅增益误差 C_sig_true/C_sig_nom − 1 [无量纲]。
        """
        return self.c_sig_true() / self.c_sig_nom() - 1.0


def build_split_chip(cfg: Config, draw: tuple | None = None) -> SplitChip:
    """生成分段 DAC 的物理真值。总面积固定 = cfg.c_total0 * cap_scale。

    单位电容 c_u = C_total / (n_main + n_sub + 1)（桥接电容也占面积），
    失配按 Pelgrom 面积律随 c_u 自动缩放（sigma_mismatch_unit）——
    这让"分段结构单位更大 -> 失配更小"的收益真实生效。

    Args:
        cfg: 全局配置（提供 c_total0、cap_scale、dac_n_main/n_sub、seed、
            寄生与桥接失配比等）。[—] 配置对象。
        draw: 失配随机量 5 元组 (eps_g, s_grp, z_u, z_bridge, cf_z)；
            各分量均为标准正态分布抽样 [无量纲]。None 时按 cfg.seed+7000
            播种生成。[假设] 蒙特卡洛实现，无物理真值。

    Returns:
        SplitChip：一颗虚拟分段 DAC 的物理真值（含 C_main/C_sub/C_bridge
        单位 [F]、寄生与反馈电容名义/真值、失配分解 breakdown 字典）。
    """
    n_m = int(cfg.dac_n_main)
    n_s = int(cfg.dac_n_sub)
    if n_m <= 0 or n_s <= 0:
        raise ValueError("dac_n_main / dac_n_sub 必须为正")

    c_u = cfg.dac_unit_cap()  # 含桥接电容的面积分摊

    if draw is None:
        rng = np.random.default_rng(cfg.seed + 7000)
        eps_g = float(rng.standard_normal())
        n_grp = int(np.ceil((n_m + n_s) / 8))
        s_grp = rng.standard_normal(n_grp)
        z_u = rng.standard_normal(n_m + n_s)
        z_bridge = float(rng.standard_normal())
        cf_z = float(rng.standard_normal())
    else:
        eps_g, s_grp, z_u, z_bridge, cf_z = draw

    sigma = cfg.sigma_mismatch_unit(c_u)
    ag, asl, au = cfg.mismatch_weights()
    grp_id = np.arange(n_m + n_s) // 8
    eps = sigma * (ag * eps_g + asl * s_grp[grp_id] + au * z_u)
    if cfg.mismatch_gradient:
        gx, gy = cfg.mismatch_gradient
        xs = (np.arange(n_m + n_s) + 0.5) / (n_m + n_s) - 0.5
        eps = eps + gx * xs + gy * 0.0  # 一维阵列只保留 x 梯度

    C_main = (c_u * (1.0 + eps))[:n_m].copy()
    C_sub = (c_u * (1.0 + eps))[n_m:].copy()

    # ---- 桥接电容与寄生 ----
    # v5.1 三轮审计（KTC 尺度矩阵发现）："mismatch_enable=False" 必须关掉
    # **全部**失配源。旧代码只门控单位电容与 C_F，桥接电容与寄生的散布
    # 照常生成 —— 子权重残留 ~100 ppm 确定性误差（split 关 dither 也测得
    # 40 µV @2.5 MHz，corr(err,k)=-0.998），"理想芯片"名不副实。
    c_p_nom = cfg.dac_parasitic_ratio * c_u * n_s
    cc_nom = (n_s * c_u + c_p_nom) / (n_s - 1.0) if n_s > 1 else c_u
    if cfg.mismatch_enable:
        cc_true = cc_nom * (1.0 + cfg.dac_bridge_mismatch_sigma * z_bridge)
        cp_true = c_p_nom * (1.0 + cfg.dac_parasitic_spread * z_bridge)  # [假设] 散布
    else:
        cc_true, cp_true = cc_nom, c_p_nom

    # ---- 反馈电容：名义值必须按**实际采样电荷等效电容**定义 ----
    # v5 审计修正（电荷守恒口径）。两浮动节点的严格解给出传输式
    #     V_P = [ S_M + beta*S_S - x_s*(A + beta*B) ] / C_eff
    # 即**信号采样电荷的等效电容是 A + beta*B，不是 A + B** —— 子阵列的
    # 采样电荷要乘桥接衰减 beta≈1/n_sub 才到达主节点。
    # 原代码用 A+B（实测 20219 fF vs 正解 18253 fF，偏 +10.77%），
    # 后果是 kT/C 噪声被低估 5%（sigma ∝ 1/sqrt(C)），且 cf_nom 不自洽。
    # 记 c_sig_nom = (n_main + beta_nom*n_sub)*c_u ；cf_nom = c_sig_nom/g0
    # 保证名义增益恰为 g0。
    # 名义桥接衰减 beta_nom = C_C/(C_C + B_nom + c_p_sub_nom)（≈1/n_sub）
    b_sub_nom = float(n_s * c_u)
    beta_for_cf = cc_nom / (cc_nom + b_sub_nom + c_p_nom) if n_s > 1 else 1.0
    c_sig_nom = (n_m + beta_for_cf * n_s) * c_u
    cf_nom = c_sig_nom / cfg.g0 if cfg.split_feedback_cap_f is None else cfg.split_feedback_cap_f
    cf_true = cf_nom * (1.0 + (sigma * cf_z if cfg.mismatch_enable else 0.0))

    return SplitChip(
        n_main=n_m,
        n_sub=n_s,
        c_unit_nom=c_u,
        C_main=C_main,
        C_sub=C_sub,
        C_bridge=float(cc_true),
        C_bridge_nom=float(cc_nom),
        c_p_main=float(cp_true),
        c_p_sub=float(cp_true),
        c_p_main_nom=float(c_p_nom),
        c_p_sub_nom=float(c_p_nom),
        C_feedback_true=float(cf_true),
        C_feedback_nominal=float(cf_nom),
        eps_main=eps[:n_m].copy(),
        eps_sub=eps[n_m:].copy(),
        breakdown={
            "c_unit_fF": c_u * 1e15,
            "sigma_eps": float(sigma),
            "sigma_eps_ppm": float(sigma * 1e6),
            "n_units": n_m + n_s,
            "levels": n_m * n_s,
            "bridge_rel_err": float(cc_true / cc_nom - 1.0),
            "sub_weight_rel_err_theory": float((1.0 - 1.0 / n_s) * (cc_true / cc_nom - 1.0)),
        },
    )


def split_chip_draw(cfg: Config, seed: int | None = None) -> tuple:
    """抽取分段 DAC 的失配随机量（与 seed 一一对应，便于复现）。

    Args:
        cfg: 全局配置（提供 seed、dac_n_main/n_sub）。[—] 配置对象。
        seed: 随机种子覆盖；None 时用 cfg.seed。[假设] 仅复现用途，
            无物理意义。

    Returns:
        5 元组 (eps_g, s_grp, z_u, z_bridge, cf_z)，各分量为标准正态分布
        抽样 [无量纲]；与 build_split_chip 的 draw 一一对应。
    """
    rng = np.random.default_rng(cfg.seed if seed is None else seed)
    n_tot = int(cfg.dac_n_main) + int(cfg.dac_n_sub)
    return (
        float(rng.standard_normal()),
        rng.standard_normal(int(np.ceil(n_tot / 8))),
        rng.standard_normal(n_tot),
        float(rng.standard_normal()),
        float(rng.standard_normal()),
    )


# ==========================================================================
# DAC：LUT + 两个求值
# ==========================================================================
def _interp_cum(cum: np.ndarray, k: np.ndarray) -> np.ndarray:
    """一维前缀和插值：k 非整数时线性内插（小数权重的物理实现）。

    Args:
        cum: 单调前缀和数组 (m+1,)，单位 [F] 或 [单位当量]，cum[i] 为前 i 项累加。
        k: 查询点 [单位当量]，可为实数（小数权重的物理实现），超界钳位到端点。
    Returns:
        内插结果，与 cum 同量纲（[F] 或 [单位当量]）。
    """
    k = np.asarray(k, dtype=float)
    n_max = len(cum) - 1
    kc = np.clip(k, 0.0, float(n_max) - 1e-9)
    i0 = np.floor(kc).astype(np.int64)
    frac = kc - i0
    return cum[i0] + frac * (cum[i0 + 1] - cum[i0])


class SplitDAC:
    """分段（主/子）+ 桥接电容 DAC。"""

    def __init__(self, cfg: Config, chip: SplitChip):
        """绑定配置与芯片实例，预计算分段 DAC 的查表量。

        Args:
            cfg: 全局配置（提供 v_fs、dac_n_main/n_sub、dem_enable）。[—] 配置对象。
            chip: 分段 DAC 物理真值（SplitChip）；只读，失配固定。[假设] 物理真值。
        Side effects: 无（仅初始化自身属性 self.cfg/chip/n_m/n_s/levels/_cache）。
        """
        self.cfg = cfg
        self.chip = chip
        self.n_m = int(cfg.dac_n_main)
        self.n_s = int(cfg.dac_n_sub)
        self.levels = cfg.dac_levels
        self._cache: dict[int, tuple] = {}

    # ---------------- DEM 顺序 ----------------
    def order_for_state(self, sid: int) -> tuple[np.ndarray, np.ndarray]:
        """该 DEM 状态下主/子阵列的单位顺序（循环轮转，主/子不同步）。

        Args:
            sid: DEM 状态索引 [无量纲，整数]；dem_enable=False 时强制取 0。
        Returns:
            (om, os_) 二元组：长度 n_main 的主阵列单位序号排列、
            长度 n_sub 的子阵列单位序号排列（np.ndarray，单位索引 [无量纲]）；
            dem_enable=False 时返回恒等排列。
        """
        sid = int(sid) if self.cfg.dem_enable else 0
        key = sid
        hit = self._cache.get(key)
        if hit is not None:
            return hit[2], hit[3]
        om = np.roll(np.arange(self.n_m), (sid * _A_MAIN) % self.n_m)
        os_ = np.roll(np.arange(self.n_s), (sid * _A_SUB) % self.n_s)
        return om, os_

    def _row(self, sid: int):
        """该 DEM 状态下的 (主/子) 前缀和数组与单位顺序（带 1024 上限缓存）。

        Args:
            sid: DEM 状态编号（无量纲，0 ≤ sid < N_DEM_STATES）。

        Returns:
            四元组 (cum_m, cum_s, om, os_)：
            cum_m 为主阵列电容前缀和（F，长度 n_main+1），
            cum_s 为子阵列电容前缀和（F，长度 n_sub+1），
            om / os_ 为该状态下的主/子单位物理顺序（无量纲索引数组）。
            结果按状态缓存，上限 1024 条；DEM 关闭时一律用状态 0。
        """
        key = int(sid) if self.cfg.dem_enable else 0
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        om, os_ = self.order_for_state(key)
        cum_m = np.concatenate([[0.0], np.cumsum(self.chip.C_main[om])])
        cum_s = np.concatenate([[0.0], np.cumsum(self.chip.C_sub[os_])])
        row = (cum_m, cum_s, om, os_)
        if len(self._cache) < 1024:
            self._cache[key] = row
        return row

    def full_order(self, sid: int) -> np.ndarray:
        """主+子拼接的物理单位顺序（供串扰/可观测性分析用）。

        Args:
            sid: DEM 状态索引 [无量纲，整数]。
        Returns:
            拼接后的单位序号数组 [无量纲]：前 n_main 为主序，后 n_sub 为
            n_m + 子序（np.ndarray）。
        """
        om, os_ = self.order_for_state(sid)
        return np.concatenate([om, self.n_m + os_])

    # ---------------- 物理求值（真值 / 名义两套参数共用一个方程）----------------
    def _eval(self, k_eq, sid, true_vals: bool) -> np.ndarray:
        """返回**输入等效** DAC 电压 v_D,in = Q_D / C_sig。

        v5 第二轮审计统一口径：残差电荷 = C_sig*x - Q_D（charge_ref.py
        独立节点方程），所以 DAC 对残差的贡献必须用 Q_D/C_sig（输入等效），
        不能用顶板电压 Q_D/C_eff —— 两者差 C_sig/C_eff ≈ 0.9972，
        旧混用口径使模拟残差产生 ~143 mV 的假非零（数字端自洽掩盖了它）。
        顶板电压口径仍可经 evaluate_physical_topplate 获取（分析用）。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（可为数组，实数经小数权重插值）。
            sid: DEM 状态索引 [无量纲，整数]（数组；true_vals=False 时强制 0）。
            true_vals: True 用物理真值（C_true + DEM 置换），False 用名义全等
                电容（DEM 无效，按均匀比例）。
        Returns:
            v_D,in 输入等效 DAC 电压数组 [V] = (S_M + beta*S_S)/C_sig。
        """
        ch = self.chip
        k_eq = np.asarray(k_eq, dtype=float)
        sid = np.asarray(sid, dtype=np.int64)
        k_m, k_s = self.split_code(k_eq)
        out = np.empty(len(k_eq))
        v_fs = self.cfg.v_fs
        if true_vals:
            beta = ch.beta_true()
            A, B = ch.A, ch.B
            c_sig = ch.c_sig_true()
        else:
            beta = ch.beta_nom()
            A, B = float(ch.n_main * ch.c_unit_nom), ch.B_nominal()
            c_sig = ch.c_sig_nom()
        for s in np.unique(sid) if true_vals else [0]:
            m = np.ones(len(k_eq), dtype=bool) if not true_vals else (sid == s)
            if true_vals:
                cum_m, cum_s, _, _ = self._row(int(s))
                sel_m = _interp_cum(cum_m, k_m[m])
                sel_s = _interp_cum(cum_s, k_s[m])
            else:
                # 名义电容全等 -> DEM 无影响，直接按比例
                sel_m = A * k_m[m] / float(ch.n_main)
                sel_s = B * k_s[m] / float(ch.n_sub)
            S_M = v_fs * (2.0 * sel_m - A)
            S_S = v_fs * (2.0 * sel_s - B)
            out[m] = (S_M + beta * S_S) / c_sig
        return out

    def evaluate_physical(self, k_eq, sid) -> np.ndarray:
        """输入等效 DAC 电压 Q_D/C_sig（残差方程的唯一正确口径）。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（数组）。
            sid: DEM 状态索引 [无量纲，整数]（数组）。
        Returns:
            物理真值下的输入等效 DAC 电压 [V]（读 C_true + DEM 置换，
            仅进模拟/评分，禁入数字算法）。
        """
        return self._eval(k_eq, sid, True)

    def evaluate_physical_topplate(self, k_eq, sid) -> np.ndarray:
        """顶极板电压口径 Q_D/C_eff（仅供网络分析，不进信号链）。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（数组）。
            sid: DEM 状态索引 [无量纲，整数]（数组）。
        Returns:
            顶极板电压数组 [V] = (S_M + beta*S_S)/C_eff（仅网络分析用）。
        """
        ch = self.chip
        k_eq = np.asarray(k_eq, dtype=float)
        sid = np.asarray(sid, dtype=np.int64)
        k_m, k_s = self.split_code(k_eq)
        out = np.empty(len(k_eq))
        v_fs = self.cfg.v_fs
        beta, c_eff = ch.beta_true(), ch.c_eff_true()
        A, B = ch.A, ch.B
        for s in np.unique(sid):
            m = sid == s
            cum_m, cum_s, _, _ = self._row(int(s))
            sel_m = _interp_cum(cum_m, k_m[m])
            sel_s = _interp_cum(cum_s, k_s[m])
            S_M = v_fs * (2.0 * sel_m - A)
            S_S = v_fs * (2.0 * sel_s - B)
            out[m] = (S_M + beta * S_S) / c_eff
        return out

    # ---------------- 名义值（数字侧可见）----------------
    def _nominal_endpoints(self) -> tuple[float, float]:
        """k_eq=0 与 levels-1 在**名义参数**下的输入等效 DAC 电压。

        v_lo = -V_FS 是恒等式（k=0 全不选通：Q_D = -C_sig·V_FS）；
        v_hi = -V_FS + (levels-1)·Δ，Δ = 2β·c_u·V_FS/C_sig（均匀步长，
        设计条件保证）—— **到不了 +V_FS**（顶码只选通 63+7 个单位）。
        名义传输是精确仿射的（步长均匀），dac_error 里只剩失配类误差。

        Returns:
            (v_lo, v_hi) 二元组 [V]：k_eq=0 与 k_eq=levels-1 的名义输入等效电压；
            v_lo ≡ −V_FS。带 _ep_cache 缓存（首次计算后只读）。
        """
        hit = getattr(self, "_ep_cache", None)
        if hit is not None:
            return hit
        n = float(self.levels)
        v_lo = float(self._eval(np.array([0.0]), np.zeros(1, dtype=np.int64), False)[0])
        v_hi = float(self._eval(np.array([n - 1.0]), np.zeros(1, dtype=np.int64), False)[0])
        self._ep_cache = (v_lo, v_hi)
        return self._ep_cache

    def evaluate_nominal(self, k_eq) -> np.ndarray:
        """数字端可见的名义传输：名义端点仿射插值。与 DEM 状态、真值无关。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（数组）。
        Returns:
            名义输入等效 DAC 电压 [V]（仿射插值 v_lo + k·(v_hi−v_lo)/(levels−1)）；
            数字侧唯一合法口径，守恒且独立于 DEM/真值。
        """
        k_eq = np.asarray(k_eq, dtype=float)
        v_lo, v_hi = self._nominal_endpoints()
        return v_lo + k_eq * (v_hi - v_lo) / (self.levels - 1.0)

    def full_scale_shrinkage(self) -> float:
        """输入等效口径的满幅收缩 = 1 - (v_hi-v_lo)/(2V_FS)。

        v_lo = -V_FS 是恒等式（k=0 无单位选通）；但 k=levels-1 只选通
        n_main-1 个主 + n_sub-1 个子单位，**到不了** +V_FS —— 顶码亏缺
        (1+ (1+2β))·c_u·V_FS 的电荷。(64,8) 名义下 v_hi ≈ +2.896 V，
        收缩 ≈ 1.7%（旧顶板口径报 2.0% 且两端不对称被掩盖）。
        这才是分段拓扑的真实量程代价（仍不能用等比放大电容恢复）。

        Returns:
            满幅收缩系数 [无量纲] = 1 − (v_hi−v_lo)/(2·V_FS)，约 1.7%（(64,8)）。
        """
        v_lo, v_hi = self._nominal_endpoints()
        return 1.0 - (v_hi - v_lo) / (2.0 * self.cfg.v_fs)

    def sndr_penalty_db(self) -> float:
        """量程损失折算成的 SNDR 惩罚（dB，负值）。

        Returns:
            20·log10((v_hi−v_lo)/(2·V_FS)) [dB]（负值，等于满幅收缩的 dB 表达）。
        """
        import math

        v_lo, v_hi = self._nominal_endpoints()
        return 20.0 * math.log10((v_hi - v_lo) / (2.0 * self.cfg.v_fs))

    def split_code(self, k_eq):
        """逻辑电平码 -> (主码, 子码)。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（数组）。
        Returns:
            (k_m, k_s) 二元组 [单位当量]：k_m = floor(k_eq/n_sub) 为主码，
            k_s = k_eq − k_m·n_sub 为子码（np.ndarray）。
        """
        k_eq = np.clip(np.asarray(k_eq, dtype=float), 0.0, self.levels - 1.0)
        k_m = np.floor(k_eq / self.n_s)
        k_s = k_eq - k_m * self.n_s
        return k_m, k_s

    def dac_error(self, k_eq, sid) -> np.ndarray:
        """DAC 误差 = 实际输出 − 名义输出（输入等效口径）。

        Args:
            k_eq: 等价逻辑电平码 [单位当量]（数组）。
            sid: DEM 状态索引 [无量纲，整数]（数组）。
        Returns:
            DAC 误差数组 [V] = evaluate_physical − evaluate_nominal（评分/归因
            专用，禁入数字算法）。
        """
        return self.evaluate_physical(k_eq, sid) - self.evaluate_nominal(k_eq)

    # ---------------- 事后分析 ----------------
    def main_weight(self) -> float:
        """主阵列单位权重（输入等效口径）：2·V_FS·C̄_main/C_sig。

        Returns:
            主阵列单位权重（V，输入等效口径），
            = 2·V_FS·mean(C_main) / C_sig_true。[派生]
        """
        return 2.0 * self.cfg.v_fs * self.chip.C_main.mean() / self.chip.c_sig_true()

    def sub_weight(self) -> float:
        """子阵列单位权重（输入等效口径）：2·V_FS·β·C̄_sub/C_sig。

        Returns:
            子阵列单位权重（V，输入等效口径），
            = 2·V_FS·β_true·mean(C_sub) / C_sig_true。[派生]
            β 为桥接衰减因子：子阵列单位经桥接电容后对顶极板的贡献被衰减。
        """
        return (
            2.0
            * self.cfg.v_fs
            * self.chip.C_sub.mean()
            * self.chip.beta_true()
            / self.chip.c_sig_true()
        )

    def boundary_error(self) -> float:
        """主/子边界残差（V）：子阵列实际总权重与一个主 LSB 之差。

        专利 [11] 指出的难点：主、子各自 DEM 后各自逼近自己的平均值，
        两个平均值未必相等。DEM 只能平均掉其中的**随机**部分
        （子阵列单位失配的均值），C_C 比例误差消不掉。

        Returns:
            主/子边界残差（V），= sub_weight·n_sub − main_weight。[派生]
            该残差中的随机部分可被 DEM 平均，C_C 比例误差部分不可消。
        """
        return float(self.sub_weight() * self.chip.n_sub - self.main_weight())

    def inl_profile(self, n_points: int | None = None) -> dict:
        """扫全部电平码，返回 DAC 误差的 INL 结构（DEM 关、固定状态）。

        这是对设计最有用的一张图：能直接看到
          * C_C 比例误差的**锯齿**（周期 = n_sub 个码）
          * 主/子边界的**台阶**
          * 寄生引起的**满幅弓形**（增益误差）

        Args:
            n_points: 扫描电平数（个）。None 表示扫满 self.levels 个码。[派生]

        Returns:
            dict：键 'code' 为电平码（无量纲）、'err' 为对应 DAC 误差（V）、
            'inl' 为去掉最佳拟合直线后的积分非线性（V）。
            DEM 关闭、状态固定，用于观察锯齿/台阶/弓形三类结构。
        """
        n = n_points or self.levels
        k_eq = np.arange(n, dtype=float)
        sid = np.zeros(n, dtype=np.int64)
        e = self.dac_error(k_eq, sid)
        step = 2.0 * self.cfg.v_fs / float(self.levels)
        return {
            "code": k_eq,
            "error_V": e,
            "error_LSB_sub": e / step,
            "sawtooth_amplitude_LSB": float(np.std(e) / step),
            "max_abs_LSB": float(np.max(np.abs(e)) / step),
            "lsb_sub_V": step,
        }
