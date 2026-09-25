"""chip.py -- 物理真值：固定电容、反馈电容、失配。

三条底线之一：**失配每颗虚拟芯片只生成一次**，整段仿真固定。
采样热噪声则按采样事件更新（见 sampler.py）。

===========================================================================
失配模型 v2：三层空间分解
===========================================================================

v1 把每个单位电容的失配建模成独立同分布 N(0, sigma^2)。这在物理上过于乐观：
真实版图里失配不是纯随机的，而是**空间相关**的。空间相关的那一部分，DEM
能吃掉多少取决于相关长度——这正是 v1 无法回答的问题。

v2 把总失配方差拆成三个正交分量：

    eps_jk = a_g * eps_global + a_s * s_j + a_u * u_jk

    eps_global ~ N(0,1)   全阵列共模：反馈电容 C_fb 失配 + 整体工艺偏差
                          -> 表现为满幅增益误差。DEM 完全无效，只能校准。
    s_j       ~ N(0,1)    slice 级共模：版图梯度 / 刻蚀负载 / 密度效应
                          -> DEM 跨 slice 有效，slice 内无效。
    u_jk      ~ N(0,1)    unit 级独立随机：纯随机失配（Pelgrom 的 A/sqrt(WL)）
                          -> DEM 完全有效。

    a_g^2 + a_s^2 + a_u^2 = 1   （保证总 sigma 不变，便于与 v1 对标）

配合版图坐标还可以叠加显式线性梯度（mismatch_gradient）：

    eps += grad_x * (x - x_c) + grad_y * (y - y_c)

梯度是**确定性**误差，DEM 可以把它变成噪声，但如果 DEM 的随机数与梯度
同频（例如固定周期轮询），会变成杂散而不是噪声——这是必须实测的。

电容与缩放：

    C0_jk(s)  = s * C0_jk(1)
    sigma(s)  = sigma0 / sqrt(s)         面积缩放律
    C_true_jk = C0_jk(s) * (1 + eps_jk(s))

同一颗虚拟芯片比较不同 s 时保留同一组 (eps_global, s_j, u_jk)，
这样缩放本身的影响才看得清。

**数字侧任何算法都不得读取本对象。** 真值只用于模拟物理电路与事后评分。
单位契约：C_true / A / B / C_F [F]；eps [无量纲相对失配]；beta [无量纲]。
参数来源分级：c_unit0=40.04 fF [披露-推断]（20.5 pF/512，PPT p.23）；
失配空间分解 (global/slice/unit) [假设]（物理乐观度可调，(0,0,1) 为下界）。
契约与不变量：本模块全部真值**每颗虚拟芯片生成一次**（三条底线①），
生成后只读；任何"按样本重抽失配"的改动都是破坏物理固定的 bug。

"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config


@dataclass
class Chip:
    """一颗虚拟芯片的物理真值。数字侧任何算法都不得读取本对象。"""

    n_slices: int
    n_unit_per_slice: int
    cap_scale: float

    C_nominal: np.ndarray  # (n_slices, n_unit) 名义电容 [F]
    C_true: np.ndarray  # (n_slices, n_unit) 实际电容 [F]
    eps: np.ndarray  # (n_slices, n_unit) 相对失配（合成后）
    z: np.ndarray  # (n_slices, n_unit) unit 级独立抽样（跨 s 复用）
    s_slice: np.ndarray  # (n_slices,)        slice 级共模（跨 s 复用）
    eps_global: float  # 全阵列共模（跨 s 复用）
    C_feedback_true: float  # 反馈电容实际值 [F]（含自身失配）
    C_feedback_nominal: float  # 反馈电容名义值 [F]
    C_total_nominal: float  # 名义总电容 [F]
    C_total_true: float  # 实际总电容 [F]
    coords: np.ndarray  # (n_slices, n_unit, 2) 版图归一化坐标 [-0.5, 0.5]
    breakdown: dict = field(default_factory=dict)  # 各分量的实际 sigma（事后评分用）
    parasitics: dict = field(default_factory=dict)
    cf_z: float = 0.0  # 反馈电容独立失配抽样（跨 s 复用）

    # ---------------- 只能用于事后评分，不得进入算法 ----------------
    def gain_deviation(self) -> float:
        """总电容偏差 -> 满幅增益误差。DEM 消不掉，只能靠增益校准。

        Returns:
            总电容偏差导致的满幅增益误差（无量纲相对量），
            = C_total_true/C_total_nominal − 1。[派生] DEM 消不掉，只能靠增益校准。
        """
        return self.C_total_true / self.C_total_nominal - 1.0

    def feedback_deviation(self) -> float:
        """反馈电容自身失配。同样是 DEM 消不掉的全局项。

        Returns:
            反馈电容自身失配（无量纲相对量），
            = C_fb_true/C_fb_nominal − 1。[派生] 同样是 DEM 消不掉的全局项。
        """
        return self.C_feedback_true / self.C_feedback_nominal - 1.0

    def sigma_components(self) -> dict:
        """事后评分：把实际失配按三层分解回各自的 sigma。

        Returns:
            dict：三层失配分量各自的实际 sigma（无量纲相对误差），
            键见 build_chip 写入的 breakdown（global / slice / unit）。
            仅供事后评分，不得进入算法路径。
        """
        return dict(self.breakdown)


def build_chip(
    cfg: Config,
    mismatch_seed: int | None = None,
    draw: tuple | None = None,
    cap_scale: float | None = None,
) -> Chip:
    """每颗虚拟芯片只调用一次。

    draw = (eps_global, s_slice, z_unit) 可外部传入，
    用于在同一次缩放扫描 / Monte Carlo 里锁住同一组随机实现。

    Args:
        cfg: 模型配置（Config）。
        mismatch_seed: 失配随机种子（无量纲整数）。None 表示取 cfg.seed。
        draw: 外部给定的随机实现四元组 (eps_global, s_slice, z_unit, cf_z)，
            (无量纲)，用于在缩放扫描或 Monte Carlo 中锁定同一颗虚拟芯片。
            None 表示现场抽样。
        cap_scale: 电容缩放因子（无量纲，> 0）。None 表示取 cfg.cap_scale。

    Returns:
        Chip：一颗虚拟芯片的电容真值与标称值、三层失配实现与事后评分数据。
        每颗虚拟芯片只应调用一次；换缩放系数请用 rescale_chip。
    """
    s = cfg.cap_scale if cap_scale is None else cap_scale
    seed = cfg.seed if mismatch_seed is None else mismatch_seed
    shape = (cfg.n_slices, cfg.n_unit_per_slice)

    # ---- 三层随机实现（每颗芯片只抽一次，跨 s 复用）----
    # 第 4 个分量是反馈电容的**独立**失配抽样（v3 审计修正：旧版复用 eps_g，
    # 注释却说"独立"——共同工艺漂移与器件局部失配必须分开，不能一边说
    # 独立一边共用同一个随机变量）。
    if draw is None:
        rng = np.random.default_rng(seed)
        eps_g = float(rng.standard_normal())
        s_sl = rng.standard_normal(cfg.n_slices)
        z_u = rng.standard_normal(shape)
        cf_z = float(rng.standard_normal())
    else:
        if len(draw) == 3:  # 兼容旧版 3 元组
            draw = (*tuple(draw), float(np.random.default_rng(cfg.seed + 1).standard_normal()))
        eps_g, s_sl, z_u, cf_z = draw

    sigma = cfg.sigma_mismatch(cap_scale=s)
    ag, asl, au = cfg.mismatch_weights()

    # ---- 版图坐标：slice 沿 x，unit 沿 y，归一化到 [-0.5, 0.5] ----
    xs = (np.arange(cfg.n_slices) + 0.5) / cfg.n_slices - 0.5
    ys = (np.arange(cfg.n_unit_per_slice) + 0.5) / cfg.n_unit_per_slice - 0.5
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    coords = np.stack([X, Y], axis=-1)

    if not cfg.mismatch_enable:
        eps = np.zeros(shape)
    else:
        eps = sigma * (ag * eps_g + asl * s_sl[:, None] + au * z_u)
        # 显式梯度：**绝对**相对失配（单位 = 1 / 单位坐标），放在 sigma 缩放之后，
        # 不再被 sigma 悄悄缩放（v3 审计修正）。gy=1e-3 -> y 跨度上失配变 1e-3。
        if cfg.mismatch_gradient:
            gx, gy = cfg.mismatch_gradient
            eps = eps + gx * coords[..., 0] + gy * coords[..., 1]

    c_unit = cfg.c_unit0 * s
    C_nominal = np.full(shape, c_unit)
    C_true = C_nominal * (1.0 + eps)

    # ---- 反馈电容：独立物理电容，**独立失配**（与阵列随机变量不共享）----
    cf_nom = cfg.c_feedback0 * s
    cf_true = cf_nom * (1.0 + (sigma * cf_z if cfg.mismatch_enable else 0.0))

    # 非法物理状态在抽签处拒收（独立审查 2026-09-25）：负 c_total0 或过大的
    # 失配/梯度会把 (1+eps) 压成非正电容——静默放行会让下游在垃圾电容上
    # "正常"出结果。注意 np.isfinite 同时拦 NaN。
    if not np.all(np.isfinite(C_true)) or bool(np.any(C_true <= 0)):
        raise ValueError(
            f"物理单位电容非正/非有限（min={float(np.min(C_true)):.3e} F）："
            "c_total0 非正或失配/梯度幅度过大"
        )
    if not np.isfinite(cf_true) or cf_true <= 0:
        raise ValueError(
            f"反馈电容非正/非有限（{float(cf_true):.3e} F）：c_feedback0 非正或失配过大"
        )

    return Chip(
        n_slices=cfg.n_slices,
        n_unit_per_slice=cfg.n_unit_per_slice,
        cap_scale=s,
        C_nominal=C_nominal,
        C_true=C_true,
        eps=eps,
        z=z_u,
        s_slice=s_sl,
        eps_global=eps_g,
        cf_z=float(cf_z),
        C_feedback_true=float(cf_true),
        C_feedback_nominal=float(cf_nom),
        C_total_nominal=float(C_nominal.sum()),
        C_total_true=float(C_true.sum()),
        coords=coords,
        breakdown={
            "sigma_total": float(sigma),
            "sigma_global": float(sigma * ag),
            "sigma_slice": float(sigma * asl),
            "sigma_unit": float(sigma * au),
            "eps_global_actual": float(sigma * ag * eps_g) if cfg.mismatch_enable else 0.0,
            "eps_feedback_actual": float(sigma * cf_z) if cfg.mismatch_enable else 0.0,
            "gradient": tuple(cfg.mismatch_gradient),
        },
        parasitics={"bottom_plate": 0.0, "top_plate": 0.0, "routing": 0.0},
    )


def chip_draw(cfg: Config, seed: int | None = None) -> tuple:
    """先抽一组随机实现，供多个 s / 多颗芯片复用。4 元组含 C_fb 独立抽样。

    Args:
        cfg: 模型配置（Config）。
        seed: 随机种子（无量纲整数）。None 表示取 cfg.seed。

    Returns:
        四元组 (eps_global, s_slice, z_unit, cf_z)：全局项（无量纲标量）、
        slice 级实现（形状 (n_slices,)）、unit 级实现
        （形状 (n_slices, n_unit_per_slice)）、反馈电容独立抽样（无量纲标量）。
        供多个缩放系数或多颗芯片复用同一组随机实现。
    """
    rng = np.random.default_rng(cfg.seed if seed is None else seed)
    return (
        float(rng.standard_normal()),
        rng.standard_normal(cfg.n_slices),
        rng.standard_normal((cfg.n_slices, cfg.n_unit_per_slice)),
        float(rng.standard_normal()),
    )


def rescale_chip(cfg: Config, base: Chip, cap_scale: float) -> Chip:
    """同一颗虚拟芯片（同一组随机实现）在另一个缩放系数下的版本。

    Args:
        cfg: 模型配置（Config）。
        base: 基准芯片（Chip），提供要复用的随机实现。
        cap_scale: 目标电容缩放因子（无量纲，> 0）。

    Returns:
        Chip：同一颗虚拟芯片（同一组随机实现）在目标缩放系数下的版本。
        失配按 Pelgrom 律随单位电容面积变化，随机实现保持不变。
    """
    return build_chip(
        cfg, draw=(base.eps_global, base.s_slice, base.z, base.cf_z), cap_scale=cap_scale
    )


# ===========================================================================
# 事后分析：DEM 对不同失配分量的理论收益
# ===========================================================================
def dem_gain_analysis(cfg: Config, chip: Chip, n_sel: int | None = None) -> dict:
    """定量回答"DEM 能吃掉多少失配"。

    等权阵列选 n_sel 个单位，DAC 相对误差的 sigma 约为：

        sigma_e ≈ sigma_component / sqrt(n_sel_effective)

    其中 n_sel_effective 对三个分量不同：
        unit 级   -> n_sel（每个都是独立抽样，完全有效）
        slice 级  -> 选中的 slice 数（slice 内完全相关，跨 slice 有效）
        全局      -> 1（完全无效）

    返回值用于与仿真实测对照，检验 DEM 实现是否达到理论上限。

    Args:
        cfg: 模型配置（Config）。
        chip: 待分析的虚拟芯片（Chip）。
        n_sel: 本次选中的单位数（个）。None 表示取该拓扑的默认值。[派生]

    Returns:
        dict：各失配分量在 DEM 前后的 sigma（无量纲相对误差）与等效改善倍数
        （无量纲）。unit 级改善 sqrt(n_sel)，slice 级改善 sqrt(选中 slice 数)，
        全局项为 1（完全无效）。[派生] 一阶近似，忽略选择相关性。
    """
    n_sel = n_sel or cfg.n_active
    n_u_per_slice = chip.C_true.shape[1]
    b = chip.breakdown
    # 选中 n_sel 个 slice，每个 slice 用掉全部 unit（v1 的 bank 结构）
    n_slice_sel = min(n_sel, chip.C_true.shape[0])
    return {
        "n_slice_selected": n_slice_sel,
        "n_unit_selected": n_sel * n_u_per_slice,
        "sigma_unit_after_dem": b["sigma_unit"] / np.sqrt(n_sel * n_u_per_slice),
        "sigma_slice_after_dem": b["sigma_slice"] / np.sqrt(n_slice_sel),
        "sigma_global_after_dem": b["sigma_global"],  # 不降
        "sigma_total_after_dem": float(
            np.sqrt(
                (b["sigma_unit"] ** 2) / (n_sel * n_u_per_slice)
                + (b["sigma_slice"] ** 2) / n_slice_sel
                + b["sigma_global"] ** 2
            )
        ),
        "sigma_total_before_dem": b["sigma_total"],
        "note": "全局分量 DEM 无效，是 DEM 收益的天花板",
    }
