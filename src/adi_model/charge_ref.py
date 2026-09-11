"""charge_ref.py -- 独立电荷参考求解器（v5 第二轮审计 §7-2）。

目的：**不调用** SplitDAC 的闭式解，用逐相位节点方程独立生成参考答案，
防止"某个公式局部替换后，另一模块仍用旧口径"的系统性风险。

相位连接声明（与 sim_split/dac_arch 引用的桥接方程一致）：
  * 采样相：主/子顶极板钳位到共模 V_cm（取 0），全部底板接输入 x；
  * 放大相：主顶极板 P 成为虚地（RA 理想），子顶极板 Q 浮置，
    桥接 C_C 保持连接，反馈电容 C_F 从复位态开始（常数偏移由数字吸收）。

节点方程（放大相）：
  Q 节点（浮置，电荷守恒自采样相）：
      V_Q*(C_C + B + C_p_sub) - S_S - C_C*V_P = -B*x
  P 节点（V_P = 0，电荷差经 C_F 流入输出）：
      C_F * v_R = A*x - S_M - C_C*V_Q
消去 V_Q（D = C_C + B + C_p_sub, beta = C_C/D）：
      C_F * v_R = (A + beta*B)*x - (S_M + beta*S_S)
                =  C_sig * x  -  Q_D
即：**残差电荷 = C_sig*x - Q_D**，输入等效 DAC 电压 = Q_D / C_sig。
这是全链路唯一的电荷真值来源；SplitDAC 与 sim_split 必须与它一致。
单位契约：电荷 [C]（fF/µV 级别交叉核对用）；节点电压 [V]。
适用域：独立参考求解器——**禁止**为通过验收而反向修改本模块去凑
主循环结果；两边不一致时先怀疑主循环口径（审计方法论）。

"""

from __future__ import annotations

import numpy as np

from .dac_arch import SplitChip


def ref_ra_charge(
    chip: SplitChip, v_fs: float, order_m: np.ndarray, order_s: np.ndarray, km, ks, x
) -> np.ndarray:
    """独立求解放大相输出电荷 C_F*v_R = C_sig*x - Q_D（不除 C_F）。

    km/ks 可为标量或数组（小数支持前缀和插值）；x 同形。

    Args:
        chip: 虚拟芯片（SplitChip），提供主/子/桥接/反馈电容真值。
        v_fs: 满幅电压 V_FS（V）。[披露]
        order_m: 主阵列 DEM 置换顺序（无量纲索引数组）。
        order_s: 子阵列 DEM 置换顺序（无量纲索引数组）。
        km: 主阵列选中单位数（个，标量或数组，支持小数插值）。
        ks: 子阵列选中单位数（个，标量或数组，支持小数插值）。
        x: 输入电压（V，标量或数组）。

    Returns:
        放大相输出电荷 C_F·v_R（C，库仑；数值上等于 C_F[F]·v_R[V]），形状与 x 同。
        口径为「输出电荷」而非「输出电压」，未除以反馈电容 C_F。[派生]
    """
    km = np.atleast_1d(np.asarray(km, dtype=float))
    ks = np.atleast_1d(np.asarray(ks, dtype=float))
    x = np.atleast_1d(np.asarray(x, dtype=float))

    def _sel(caps: np.ndarray, order: np.ndarray, k: np.ndarray) -> np.ndarray:
        """按 DEM 顺序取电容前缀和并做小数线性插值，得到「选中 k 个单位」的等效电容。

        Args:
            caps: 单位电容数组（F，长度 n）。
            order: DEM 置换后的物理顺序（无量纲索引数组，长度 n）。
            k: 选中的单位数（个，可为小数，会被裁剪到 [0, n)）。

        Returns:
            选中的等效电容（F，形状同 k）。整数 k 时等于前 k 个单位电容之和；
            小数部分在前缀和上线性插值，与数字端「小数码」的口径保持一致。

        Notes:
            这是独立于 dac_arch.py 的第二实现，专供电荷闭合交叉校验使用；
            修改本函数时必须同步复核 dac_arch.py 的对应路径。
        """
        cum = np.concatenate([[0.0], np.cumsum(caps[order])])
        out = np.empty(len(k))
        for i, kk in enumerate(k):
            kk = min(max(kk, 0.0), len(caps) - 1e-9)
            i0 = int(np.floor(kk))
            f = kk - i0
            out[i] = cum[i0] + f * (cum[i0 + 1] - cum[i0])
        return out

    selM = _sel(chip.C_main, order_m, km)
    selS = _sel(chip.C_sub, order_s, ks)
    A = float(chip.C_main.sum())
    B = float(chip.C_sub.sum())
    SM = v_fs * (2.0 * selM - A)
    SS = v_fs * (2.0 * selS - B)
    D = chip.C_bridge + B + chip.c_p_sub
    beta = chip.C_bridge / D
    Q_D = SM + beta * SS
    C_sig = A + beta * B
    return C_sig * x - Q_D


def ref_ra_charge_nodal(
    chip: SplitChip, v_fs: float, order_m: np.ndarray, order_s: np.ndarray, km, ks, x
) -> np.ndarray:
    """真·节点矩阵求解器（v5.1 第三轮审计 §一）。

    不写任何闭式解：按**电容连接**直接组装放大相的 2x2 线性系统并数值求解。

        [ C_C+B+C_pS    0  ] [ V_Q ]   [ S_S - B*x ]
        [ C_C          C_F ] [ v_R ] = [ A*x - S_M ]

    （Q 节点浮置电荷守恒；P 节点虚地，电荷差经 C_F 流出。）
    闭式解 ref_ra_charge 只是它的解析消元结果 —— 两者的最大偏差是
    stage12 电荷闭合验收的一部分，防止两份代码同时抄错同一个代数式。

    Args:
        chip: 虚拟芯片（SplitChip），提供主/子/桥接/反馈电容真值。
        v_fs: 满幅电压 V_FS（V）。[披露]
        order_m: 主阵列 DEM 置换顺序（无量纲索引数组）。
        order_s: 子阵列 DEM 置换顺序（无量纲索引数组）。
        km: 主阵列选中单位数（个，标量或数组，支持小数插值）。
        ks: 子阵列选中单位数（个，标量或数组，支持小数插值）。
        x: 输入电压（V，标量或数组）。

    Returns:
        放大相输出电荷 C_F·v_R（C，库仑），与 ref_ra_charge 同口径。
        [派生] 由 2x2 节点方程数值求解得到，不写闭式解，用于交叉校验。
    """
    km = np.atleast_1d(np.asarray(km, dtype=float))
    ks = np.atleast_1d(np.asarray(ks, dtype=float))
    x = np.atleast_1d(np.asarray(x, dtype=float))

    def _sel(caps: np.ndarray, order: np.ndarray, k: np.ndarray) -> np.ndarray:
        """按 DEM 顺序取电容前缀和并做小数线性插值，得到「选中 k 个单位」的等效电容。

        Args:
            caps: 单位电容数组（F，长度 n）。
            order: DEM 置换后的物理顺序（无量纲索引数组，长度 n）。
            k: 选中的单位数（个，可为小数，会被裁剪到 [0, n)）。

        Returns:
            选中的等效电容（F，形状同 k）。整数 k 时等于前 k 个单位电容之和；
            小数部分在前缀和上线性插值，与数字端「小数码」的口径保持一致。

        Notes:
            这是独立于 dac_arch.py 的第二实现，专供电荷闭合交叉校验使用；
            修改本函数时必须同步复核 dac_arch.py 的对应路径。
        """
        cum = np.concatenate([[0.0], np.cumsum(caps[order])])
        out = np.empty(len(k))
        for i, kk in enumerate(k):
            kk = min(max(kk, 0.0), len(caps) - 1e-9)
            i0 = int(np.floor(kk))
            f = kk - i0
            out[i] = cum[i0] + f * (cum[i0 + 1] - cum[i0])
        return out

    selM = _sel(chip.C_main, order_m, km)
    selS = _sel(chip.C_sub, order_s, ks)
    A = float(chip.C_main.sum())
    B = float(chip.C_sub.sum())
    SM = v_fs * (2.0 * selM - A)
    SS = v_fs * (2.0 * selS - B)
    n = len(x)
    M = np.zeros((n, 2, 2))
    M[:, 0, 0] = chip.C_bridge + B + chip.c_p_sub
    M[:, 1, 0] = chip.C_bridge
    M[:, 1, 1] = chip.C_feedback_true
    rhs = np.stack([SS - B * x, A * x - SM], axis=1)[:, :, None]  # (n,2,1) 列向量
    sol = np.linalg.solve(M, rhs)  # (n, 2, 1) -> [V_Q, v_R]
    return chip.C_feedback_true * sol[:, 1, 0]  # C_F*v_R（与 ref_ra_charge 同口径）


def ref_input_equiv_dac(chip: SplitChip, v_fs: float, k: float, levels: int) -> float:
    """名义参数下码 k 的输入等效 DAC 电压 = Q_D_nom / C_sig_nom。

    用名义参数（等电容、名义 C_C/寄生）独立算出 —— 数字端的名义栅格
    必须由同一关系生成，不得用另一口径（顶板电压）拼凑。

    Args:
        chip: 虚拟芯片（SplitChip），只用其名义参数（等电容、名义桥接/寄生）。
        v_fs: 满幅电压 V_FS（V）。[披露]
        k: DAC 码字（个，无量纲）。
        levels: 总电平数（个，无量纲）。

    Returns:
        码 k 的输入等效 DAC 电压（V），= Q_D_nom / C_sig_nom。[派生]
        数字端名义栅格必须由同一关系生成，不得用顶极板电压口径拼凑。
    """
    # 名义芯片：等电容 + 名义桥接/寄生
    c_u = chip.c_unit_nom
    A = chip.n_main * c_u
    B = chip.n_sub * c_u
    D = chip.C_bridge_nom + B + chip.c_p_sub_nom
    beta = chip.C_bridge_nom / D
    C_sig = A + beta * B
    km = int(np.floor(k / chip.n_sub))
    ks = k - km * chip.n_sub
    SM = v_fs * (2.0 * km * c_u - A)
    SS = v_fs * (2.0 * ks * c_u - B)
    return (SM + beta * SS) / C_sig


# ===========================================================================
# 采样 dither 掩码（v5.1 第三轮审计 §四 -> stage18）
# ===========================================================================
# 相位连接声明（在文件头的基础上扩展 dither）：
#   * 采样相：**掩码固定为 bank 阵列末尾 2D 个单位**，它们不接输入，改接
#     ±V_FS —— 其中 D+d_u 个接 +V_FS、D−d_u 个接 −V_FS（d_u 为整数 dither 码）；
#     其余单位底板接输入 x。
#   * 放大相：全部单位（含掩码单位）按码 k 切换到 ±V_FS。
#
# 逐单位推广（w=采样相底板电压，b=放大相底板电压）：
#     Q 节点：D_n*V_Q = Σ_sub C_j*(b_j − w_j)
#     P 节点：C_F*v_R = Σ_main C_i*(w_i − b_i) − C_C*V_Q
# 消元得恒等式（对任意掩码/任意电容成立）：
#     C_F*v_R = C_sig,sample*x + w_bank*Q_mask − Q_D(k)
# 其中 C_sig,sample = C_sig − w_bank*C_mask（掩码单位移出信号电荷）、
# Q_mask = V_FS*Σ_mask C_j*s_j（dither 电荷）、w_bank = β（子掩码）或 1（主掩码）。
# α = C_sig,sample/C_sig 与注入尺度 w_bank*Q_mask/C_sig 必须由**同一份掩码**生成。
# ===========================================================================


def dither_mask_charge(
    chip: SplitChip, v_fs: float, d_units, bank: str = "sub", n_mask: int | None = None
) -> tuple:
    """返回 (Q_mask, C_mask)：掩码单位的 dither 电荷与总电容。

    掩码 = bank 阵列**末尾** n_mask=2D 个单位（与 sampler/sim_split 的
    eps_d 取法一致）；s_j = +1 for 前 D+d_u 个、−1 for 其余。
    d_units 可为标量或逐样本数组（返回同形 Q_mask）。

    Args:
        chip: 虚拟芯片（SplitChip）。
        v_fs: 满幅电压 V_FS（V）。[披露]
        d_units: dither 单位偏移量 D（个，标量或逐样本数组）。
        bank: 掩码取自哪个阵列，'sub'（子阵列，权重 β）或 'main'（主阵列，权重 1）。
        n_mask: 掩码单位数（个）。None 表示取整列长度。[派生]

    Returns:
        元组 (Q_mask, C_mask)：Q_mask 为掩码注入的 dither 电荷（C，库仑），
        标量输入时是 float、数组输入时是同形 ndarray；C_mask 为掩码总电容（F）。
        [派生] 掩码取 bank 阵列末尾 n_mask 个单位，与 sampler/sim_split 的 eps_d 一致。
    """
    arr = chip.C_sub if bank == "sub" else chip.C_main
    nm = int(n_mask if n_mask is not None else len(arr))
    nm = min(nm, len(arr))
    mask = arr[-nm:]
    d = np.atleast_1d(np.asarray(d_units, dtype=float))
    D = nm // 2
    j = np.arange(nm)[:, None]  # (nm, 1)
    s = np.where(j < (D + d)[None, :], 1.0, -1.0)  # (nm, n)
    Q = v_fs * (s * mask[:, None]).sum(axis=0)
    scalar = np.ndim(d_units) == 0
    return (float(Q[0]) if scalar else Q), float(mask.sum())


def ref_ra_charge_dither_closed(
    chip: SplitChip,
    v_fs: float,
    order_m: np.ndarray,
    order_s: np.ndarray,
    km,
    ks,
    x,
    d_units: int | np.ndarray,
    bank: str = "sub",
    n_mask: int | None = None,
) -> np.ndarray | float:
    """闭式解：C_F*v_R = C_sig,sample*x + w_bank*Q_mask − Q_D(k)。

    返回类型随 ``d_units`` 的形状走：标量输入给标量，数组输入给数组。

    Args:
        chip: 虚拟芯片（SplitChip）。
        v_fs: 满幅电压 V_FS（V）。[披露]
        order_m: 主阵列 DEM 置换顺序（无量纲索引数组）。
        order_s: 子阵列 DEM 置换顺序（无量纲索引数组）。
        km: 主阵列选中单位数（个，标量或数组，支持小数插值）。
        ks: 子阵列选中单位数（个，标量或数组，支持小数插值）。
        x: 输入电压（V，标量或数组）。
        d_units: dither 单位偏移量 D（个，标量或逐样本数组）。
        bank: 掩码所在阵列，'sub'（权重 β）或 'main'（权重 1）。
        n_mask: 掩码单位数（个）。None 表示取整列长度。[派生]

    Returns:
        放大相输出电荷 C_F·v_R（C，库仑）：标量 d_units 返回 float，
        数组 d_units 返回同形 ndarray。[派生]
        口径 = C_sig,sample·x + w_bank·Q_mask − Q_D(k)，α 与注入尺度由同一份掩码生成。
    """
    km = np.atleast_1d(np.asarray(km, dtype=float))
    ks = np.atleast_1d(np.asarray(ks, dtype=float))
    x = np.atleast_1d(np.asarray(x, dtype=float))

    def _sel(caps: np.ndarray, order: np.ndarray, k: np.ndarray) -> np.ndarray:
        """按 DEM 顺序取电容前缀和并做小数线性插值，得到「选中 k 个单位」的等效电容。

        Args:
            caps: 单位电容数组（F，长度 n）。
            order: DEM 置换后的物理顺序（无量纲索引数组，长度 n）。
            k: 选中的单位数（个，可为小数，会被裁剪到 [0, n)）。

        Returns:
            选中的等效电容（F，形状同 k）。整数 k 时等于前 k 个单位电容之和；
            小数部分在前缀和上线性插值，与数字端「小数码」的口径保持一致。

        Notes:
            这是独立于 dac_arch.py 的第二实现，专供电荷闭合交叉校验使用；
            修改本函数时必须同步复核 dac_arch.py 的对应路径。
        """
        cum = np.concatenate([[0.0], np.cumsum(caps[order])])
        out = np.empty(len(k))
        for i, kk in enumerate(k):
            kk = min(max(kk, 0.0), len(caps) - 1e-9)
            i0 = int(np.floor(kk))
            f = kk - i0
            out[i] = cum[i0] + f * (cum[i0 + 1] - cum[i0])
        return out

    selM = _sel(chip.C_main, order_m, km)
    selS = _sel(chip.C_sub, order_s, ks)
    A = float(chip.C_main.sum())
    B = float(chip.C_sub.sum())
    SM = v_fs * (2.0 * selM - A)
    SS = v_fs * (2.0 * selS - B)
    D = chip.C_bridge + B + chip.c_p_sub
    beta = chip.C_bridge / D
    Q_D = SM + beta * SS
    d_arr = np.atleast_1d(np.asarray(d_units, dtype=float))
    Q_mask, C_mask = dither_mask_charge(chip, v_fs, d_arr, bank, n_mask)
    w = beta if bank == "sub" else 1.0
    C_sig_sample = (A + beta * B) - w * C_mask
    out = C_sig_sample * x + w * Q_mask - Q_D
    return out if np.ndim(d_units) else float(out[0])


def ref_ra_charge_dither_nodal(
    chip: SplitChip,
    v_fs: float,
    order_m: np.ndarray,
    order_s: np.ndarray,
    km,
    ks,
    x,
    d_units,
    bank: str = "sub",
    n_mask: int | None = None,
) -> np.ndarray:
    """真·节点矩阵版（不写闭式）：逐单位 w/b 组装节点方程数值求解。

    与 ref_ra_charge_dither_closed 的最大偏差进 stage18 验收 ——
    防止"闭式恒等式"本身被抄错而没有第二意见。
    全向量化：b 矩阵按码前缀选择，w 矩阵按采样掩码赋 ±V_FS。

    Args:
        chip: 虚拟芯片（SplitChip）。
        v_fs: 满幅电压 V_FS（V）。[披露]
        order_m: 主阵列 DEM 置换顺序（无量纲索引数组）。
        order_s: 子阵列 DEM 置换顺序（无量纲索引数组）。
        km: 主阵列选中单位数（个，标量或数组，支持小数插值）。
        ks: 子阵列选中单位数（个，标量或数组，支持小数插值）。
        x: 输入电压（V，标量或数组）。
        d_units: dither 单位偏移量 D（个，标量或逐样本数组）。
        bank: 掩码所在阵列，'sub'（权重 β）或 'main'（权重 1）。
        n_mask: 掩码单位数（个）。None 表示取整列长度。[派生]

    Returns:
        放大相输出电荷 C_F·v_R 的 ndarray（C，库仑），与
        ref_ra_charge_dither_closed 同口径。[派生] 逐单位组装节点方程数值求解，
        不写闭式恒等式，用于 stage18 电荷闭合验收的第二意见。
    """
    km = np.atleast_1d(np.asarray(km, dtype=float))
    ks = np.atleast_1d(np.asarray(ks, dtype=float))
    x = np.atleast_1d(np.asarray(x, dtype=float))
    d = np.atleast_1d(np.asarray(d_units, dtype=float))
    nm = int(n_mask if n_mask is not None else 0)
    nd_half = nm // 2

    # 采样相底板电压 w（(units, n)）：信号单位 = x；掩码单位 = ±V_FS
    #   s[j, i] = +1 if j < D + d_i else -1（掩码内第 j 个单位，逐样本）
    jm = np.arange(nm)[:, None]
    s_mask = np.where(jm < (nd_half + d)[None, :], 1.0, -1.0)  # (nm, n)
    wM = np.tile(x, (chip.n_main, 1))
    wS = np.tile(x, (chip.n_sub, 1))
    if bank == "sub":
        wS[-nm:, :] = s_mask * v_fs
    else:
        wM[-nm:, :] = s_mask * v_fs

    # 放大相底板电压 b（(units, n)）：码前缀选择 +V_FS / −V_FS
    bM = np.where(np.arange(chip.n_main)[:, None] < km[None, :], v_fs, -v_fs)
    bS = np.where(np.arange(chip.n_sub)[:, None] < ks[None, :], v_fs, -v_fs)

    # 节点方程（Q 浮置 / P 虚地）：
    #   D_n*V_Q = Σ_sub C_j*(b_j − w_j)
    #   C_F*v_R = Σ_main C_i*(w_i − b_i) − C_C*V_Q
    B = float(chip.C_sub.sum())
    Dn = chip.C_bridge + B + chip.c_p_sub
    rhs_Q = chip.C_sub @ (bS - wS)
    rhs_P = chip.C_main @ (wM - bM)
    VQ = rhs_Q / Dn
    vR = (rhs_P - chip.C_bridge * VQ) / chip.C_feedback_true
    return chip.C_feedback_true * vR  # C_F*v_R（与闭式同口径）
