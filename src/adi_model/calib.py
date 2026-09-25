"""calib.py -- 单位权重校准的**可观测性**与 rank-aware 估计。

============================================================================
为什么这是"架构能否成立"的判据，而不是可选优化
============================================================================
失配预算分析已证明：PDK 估算的单位失配 sigma ≈ 1117 ppm，
而达到 SNDR >= 93 dB 需要 sigma <= 约 300 ppm。差 3.7 倍。
靠加面积补要 14 倍电容（sigma ∝ 1/sqrt(C)），不可行；DEM 在大失配下
甚至让 SNDR 变差（v2 结论）。所以**只能估计出每个单位的真实权重并在数字端扣除**。

但"能不能估出来"取决于**使用矩阵 U 的秩**：

    U[n, u] = 第 n 次转换里第 u 个单位是否被选中（实数时为权重）

    err[n] = -sum_u w_u * U[n, u] + noise       （w_u = 该单位的权重偏差）

若 rank(U) = r < N_units，则只有 r 个线性组合是可辨识的，
其余 N_units - r 个方向**无论给多少数据都估不出来**。
v4 实测当前交织映射下 rank = 64 / 512 —— 这不是数据量问题，是**结构问题**。

本模块三件事：
  1. observability()      —— 量化"能估出多少"
  2. ridge_fit()          —— 在**可辨识子空间**上做最小二乘（不假装能估全部）
  3. required_samples()   —— 反推"要估到目标精度需要多少次观测"

============================================================================
为什么 rank 会退化（必须让设计者看到的机制）
============================================================================
交织映射下，码值 +1 是"给下一个 slice 加一个单位"，于是
8 个 slice 的**同位置单位永远同时切换**。观测只能看到它们的和，
永远分不开 —— rank 被压到 64（= 每个 slice 的单位数 x ... 见实测）。
打破退化的手段：
  * DEM / 随机置换：让同一码值对应不同单位集合（同一个 k，不同 sid）
  * 专门的校准模式：随机选单位而不是按固定顺序取前 k 个
代价：都需要额外的激励与数据量。这是设计上的**真实成本**，必须量化。
单位契约：权重 w [V/单位当量]；U 观测矩阵 [无量纲/V]；残差 [V]。
适用域（勿夸大）：单位权重估计实验，非 split 校准闭环；
必须 out-of-sample + 整数/分数码双口径（in-sample 自欺）；
required_samples 是设计裕量公式，非物理硬下界。

"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config


# ==========================================================================
# 使用矩阵
# ==========================================================================
def use_matrix(
    k_float: np.ndarray, sid: np.ndarray, perm_fn, n_units: int, n_states: int
) -> np.ndarray:
    """U[n, u] = 第 n 次转换中物理单位 u 的选中权重（0/1，边界可为小数）。

    k_float 是**实数**码（dither 后）：前 floor(k) 个单位全选，
    第 floor(k) 个单位按小数部分部分选中。

    Args:
        k_float: 实数码数组 [单位当量]（dither 后可为小数）。
        sid: 逐样本 slice 标签数组 [int]。
        perm_fn: 可调用 perm_fn(slice_id) -> 该 slice 的单位排列（DEM）。
        n_units: 物理单位总数 [无量纲]。
        n_states: DEM 状态数 [无量纲]（用于 perm_fn 定义域，不参与计算）。

    Returns:
        U [n x n_units，无量纲]：第 n 次转换中单位 u 的选中权重
        （0/1，边界码为小数）。U 是权重校准的可观测性来源矩阵。
    Side effects: 无（纯函数）。
    """
    k = np.clip(np.asarray(k_float, dtype=float), 0.0, float(n_units))
    sid = np.asarray(sid, dtype=np.int64)
    n = len(k)
    U = np.zeros((n, n_units))
    i0 = np.floor(k).astype(np.int64)
    frac = k - i0
    i0 = np.clip(i0, 0, n_units)
    for s in np.unique(sid):
        m = sid == s
        if not m.any():
            continue
        order = perm_fn(int(s))
        ii = np.clip(i0[m], 0, n_units)
        rows = np.where(m)[0]
        # 全选部分：对每行把 order[:ii] 置 1 —— 用累积方式避免大循环
        # 逐行赋值（n_cal 通常 <= 8192，可接受）
        for r, nsel, fr in zip(rows, ii, frac[m], strict=False):
            if nsel > 0:
                U[r, order[:nsel]] = 1.0
            if nsel < n_units and fr > 0:
                U[r, order[nsel]] = fr
    return U


# ==========================================================================
# 可观测性
# ==========================================================================
@dataclass
class Observability:
    """校准问题的可观测性指标：单位数、秩与有效秩。"""

    n_units: int
    rank: int
    eff_rank: float
    cond: float
    svals: np.ndarray | None = field(repr=False, default=None)
    identifiable_fraction: float = 0.0

    def as_dict(self) -> dict:
        """转成可序列化字典（写 results.json 用）。

        Returns:
            字典，字段与单位：
                n_units [无量纲]      物理单位总数；
                rank [无量纲]         矩阵秩（可辨识方向数）；
                eff_rank [无量纲]     奇异值能量参与比（有效秩）；
                cond [无量纲]         条件数 s_max/s_min；
                identifiable_fraction [0..1]  rank/n_units；
                n_null_directions [无量纲]   不可辨识方向数 = n_units - rank。
        """
        return {
            "n_units": int(self.n_units),
            "rank": int(self.rank),
            "eff_rank": float(self.eff_rank),
            "cond": float(self.cond),
            "identifiable_fraction": float(self.identifiable_fraction),
            "n_null_directions": int(self.n_units - self.rank),
        }


def observability(U: np.ndarray, tol: float | None = None) -> Observability:
    """SVD 秩分析。eff_rank 用奇异值能量的参与比（participation ratio）。

    Args:
        U: 观测矩阵 [n x n_units，无量纲]（来自 use_matrix）。
        tol: 可选秩阈值 [无量纲]；None 时取 max(shape)·eps·s[0]。

    Returns:
        Observability（见其字段单位）。空矩阵返回 rank=0、cond=inf。
    Side effects: 无（纯函数）。
    """
    if U.size == 0:
        return Observability(U.shape[1], 0, 0.0, float("inf"), np.zeros(0))
    s = np.linalg.svd(U, compute_uv=False)
    s = s[np.isfinite(s)]
    if tol is None:
        tol = max(U.shape) * np.finfo(float).eps * (s[0] if s.size else 1.0)
    rank = int((s > max(tol, 1e-9)).sum())
    e = s**2
    eff = float(e.sum() ** 2 / (e**2).sum()) if e.sum() > 0 else 0.0
    cond = float(s[0] / s[-1]) if s[-1] > 0 else float("inf")
    n_u = U.shape[1]
    return Observability(n_u, rank, eff, cond, s, rank / n_u if n_u else 0.0)


def group_aggregate(U: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """把列按 group 求和 -> U_g (n x n_groups)。只能在**组内不可分辨**时这么干。

    Args:
        U: 观测矩阵 [n x n_units，无量纲]。
        groups: 列分组标签数组 [int]，值 ∈ [0, n_groups)。

    Returns:
        U_g [n x n_groups，无量纲]：每个 group 内单位列求和。
    Side effects: 无（纯函数）。

    Notes:
        仅在同组单位**物理不可分辨**（同一耦合/失配）时用；否则会把
        可分离的方向错误合并，人为抬高可观测性（勿回退）。
    """
    g = np.asarray(groups, dtype=np.int64)
    n_g = int(g.max()) + 1
    out = np.zeros((U.shape[0], n_g))
    for j in range(n_g):
        out[:, j] = U[:, g == j].sum(axis=1)
    return out


# ==========================================================================
# 估计
# ==========================================================================
def ridge_fit(U: np.ndarray, y: np.ndarray, lam: float = 0.0):
    """在可辨识子空间上做岭回归。返回 (w_hat, used_rank)。

    不用 np.linalg.lstsq 直接解：那样在秩亏时会给出**任意**一个特解
    （最小范数解），看起来像估出来了，实际把不可辨识方向也填了数 ——
    换一批数据验证就会崩。这里显式截断到可辨识子空间。

    Args:
        U: 观测矩阵 [n x n_units，无量纲]（来自 use_matrix）。
        y: 目标残差数组 [V] = -err（err = out - x_known）。
        lam: 岭正则强度 [无量纲缩放]；默认 0（无偏最小二乘）。

    Returns:
        (w_hat, used_rank)：估计权重 [V/单位当量] 与所用工况秩 [int]。
        欠定（n < n_units）时走对偶形式（核技巧），等价于最小范数岭解；
        秩亏时零空间方向被岭项压到 0（前提是 y 在该方向无分量）。
    Side effects: 无（纯函数）。

    Notes:
        数值会失效：rank(U) < n_units 时只能估可辨识子空间，零空间方向
        不可信（换数据即崩）—— 不要把它当"全部估出"（勿回退）。
    """
    U = np.asarray(U, dtype=float)
    y = np.asarray(y, dtype=float)
    if U.shape[0] < U.shape[1]:
        # 欠定：用对偶形式（核技巧），等价于最小范数岭解
        K = U @ U.T
        if lam == 0.0:
            # lam=0 且秩亏时 solve 对近奇异静默放大——直接用最小范数解
            a = np.linalg.lstsq(K, y, rcond=None)[0]
        else:
            a = np.linalg.solve(K + lam * np.eye(U.shape[0]), y)
        return U.T @ a, int(np.linalg.matrix_rank(U))
    UtU = U.T @ U
    Uty = U.T @ y
    s = np.linalg.svd(UtU, compute_uv=False)
    tol = max(s) * 1e-10 if s.size else 0.0
    # 直接加岭并求解；岭项自动把零空间压到 0（前提是 y 在这些方向上无分量）
    if lam == 0.0:
        # 同上：lam=0 时直接取最小范数解，避免近奇异静默放大
        w = np.linalg.lstsq(UtU, Uty, rcond=None)[0]
    else:
        A = UtU + lam * np.eye(U.shape[1])
        try:
            w = np.linalg.solve(A, Uty)
        except np.linalg.LinAlgError:
            w = np.linalg.lstsq(A, Uty, rcond=None)[0]
    used = int(np.linalg.matrix_rank(U, tol=tol if tol > 0 else None))
    return w, used


def null_space_projection_error(U: np.ndarray, w_true: np.ndarray) -> float:
    """真实权重向量里有多少**落在不可观测的零空间**里（比例）。

    这个值 = 无论给多少数据都消不掉的那部分失配。是本模块最重要的输出。

    Args:
        U: 观测矩阵 [n x n_units，无量纲]。
        w_true: 真实单位权重偏差向量 [V/单位当量]。

    Returns:
        落在不可观测零空间的比例 [0..1，无量纲]；0 表示全可辨识，
        1 表示完全不可观测。空矩阵返回 1。
    Side effects: 无（纯函数）。

    Notes:
        数值会失效：若真实权重本身接近 0（||w||→0），比例分母无定义、
        结果不可信——需配合非零权重幅度解读（勿回退）。
    """
    if U.size == 0:
        return 1.0
    # U 的零空间正交基
    _, s, Vt = np.linalg.svd(U, full_matrices=True)
    n_u = U.shape[1]
    rank = int((s > max(s) * 1e-10).sum()) if s.size else 0
    if rank >= n_u:
        return 0.0
    N = Vt[rank:].T  # (n_u, n_u - rank)
    w = np.asarray(w_true, dtype=float)
    proj = N @ (N.T @ w)
    denom = np.linalg.norm(w)
    return float(np.linalg.norm(proj) / denom) if denom > 0 else 0.0


# ==========================================================================
# 样本量反推（设计指导）
# ==========================================================================
def required_samples(
    cfg: Config,
    U: np.ndarray,
    sigma_noise_v: float,
    step_v: float,
    sigma_target_ppm: float,
    oversample: float = 4.0,
) -> dict:
    """把每个单位权重估到 sigma_target_ppm，需要多少次观测？

    两条独立约束，**必须取 max**（旧版只算了第 1 条，
    给出"65 次观测估 512 个未知量"这个物理上不可能的答案）：

      1. 噪声约束：最小二乘协方差 σ²(UᵀU)⁻¹ 的迹要够小。
         Var(ŵ) 按 ±1 奇异值分解写成 Σ Var_j = σ² Σ 1/s_i²；
         要求平均 Var_j ≤ tgt²。把设计矩阵整体复制 k 倍时 s_i → √k·s_i，
         所以 Σ Var_j 按 1/k 缩小，解出 k 即得噪声界。
      2. 观测冗余（**设计裕量，非物理硬下界**）：rank(U)=N 只保证
         "理论上可解"；是否需要 2N/4N 取决于噪声、条件数与目标精度
         （零噪声下 U=I_N 时 N 次观测即可）。这里取 oversample×N
         （默认 4×）作为工程裕量 —— 标注清楚，不要包装成必然。

    若 rank(U) < N（如交织映射），返回不可行 —— 再多数据也没用，
    这是**结构约束**，不是数据量约束。但注意：秩不足 ≠ 运行态校正无效。
    真正的判据是 Null(U_cal) ⊆ Null(U_run)：校准分辨不了的方向
    若在运行映射下也不产生误差，就无需识别（v5 判据文本已注明）。

    Args:
        cfg: Config（取 fs [披露] 40 MS/s，用于换算观测时间）。
        U: 校准观测矩阵 [n x n_units，无量纲]。
        sigma_noise_v: 单样本噪声 RMS [V]。
        step_v: 单位步电压 [V]（= 一个单位当量对应的输入电压）。
        sigma_target_ppm: 目标权重精度 [ppm，无量纲]（如 300 ppm）。
        oversample: 观测冗余倍数 [无量纲]，默认 4（设计裕量，非硬下界）。

    Returns:
        字典，字段与单位：
            feasible [bool]；rank / n_units [无量纲]；
            n_rows_given [无量纲]；target_V [V]；
            cond_amp_variance [无量纲]   方差放大倍率（相对理想设计）；
            n_noise_bound / n_dof_bound [无量纲]   两条约束各自所需样本；
            binding ["噪声" | "观测冗余"]；oversample_assumed [无量纲]；
            n_samples [无量纲]   max 后的总需求；
            at_fs_seconds [s]   以全 fs 连续观测的时长；
            at_fs_40MHz_seconds [s]   同上（显式标 40 MHz）；
            note [str]   方法说明。
        rank < n_units 时 feasible=False 并返回 null_directions 等结构信息。
    Side effects: 无（纯函数）。

    Notes:
        这是**设计裕量公式，非物理硬下界**；rank 不足时返回不可行，
        表明问题在结构（映射）而非数据量——不要幻想靠加样本突破（勿回退）。
    """
    U = np.asarray(U, dtype=float)
    n_rows, n_units = U.shape
    tgt = sigma_target_ppm * 1e-6 * step_v
    s = np.linalg.svd(U, compute_uv=False)
    if s.size == 0:
        return {"feasible": False, "n_samples": float("inf"), "note": "U 为空"}
    rank = int((s > s[0] * 1e-10).sum())
    s_kept = s[:rank]
    if tgt <= 0:
        return {"feasible": False, "n_samples": float("inf"), "note": "目标精度为 0，不可能"}
    # 方差放大倍率（相对"各单位独立、等观测次数"的理想设计）：
    #   理想 Var_j = σ²/n_eff_j，n_eff_j = (UᵀU)_jj = 该单位被选中次数
    #   实际 Var_j = σ²[(UᵀU)⁻¹]_jj  —— 放大即二者之比，对 j 取均值
    UtU = U.T @ U
    n_eff = np.clip(np.diag(UtU), 1e-12, None)
    try:
        inv_diag = np.diag(np.linalg.inv(UtU))
        amp = float(np.mean(n_eff * inv_diag))
    except np.linalg.LinAlgError:
        amp = float("inf")
    if rank < n_units:
        return {
            "feasible": False,
            "rank": rank,
            "n_units": n_units,
            "null_directions": int(n_units - rank),
            "n_samples": float("inf"),
            "at_fs_seconds": float("inf"),
            "note": (
                "秩不足：不可辨识方向与数据量无关。必须先改映射"
                "（每状态独立随机置换 / 专用校准模式）再谈样本量。"
            ),
        }
    # Σ_j Var(ŵ_j) = σ²·tr((UᵀU)⁻¹) = σ²·Σ 1/s_i²
    var_sum = float(sigma_noise_v**2 * np.sum(1.0 / s_kept**2))
    k_need = var_sum / (n_units * tgt**2)  # 复制倍数
    n_noise_bound = max(k_need, 1e-9) * n_rows
    n_dof_bound = oversample * n_units
    n_total = max(n_noise_bound, n_dof_bound)
    return {
        "feasible": True,
        "rank": rank,
        "n_units": n_units,
        "n_rows_given": n_rows,
        "target_V": float(tgt),
        "cond_amp_variance": float(amp),
        "n_noise_bound": float(n_noise_bound),
        "n_dof_bound": float(n_dof_bound),
        "binding": "噪声" if n_noise_bound >= n_dof_bound else "观测冗余",
        "oversample_assumed": float(oversample),
        "n_samples": float(n_total),
        "at_fs_seconds": float(n_total / cfg.fs),
        "at_fs_40MHz_seconds": float(n_total / cfg.fs),
        "note": (
            "两条约束取 max：① 噪声界 σ²·Σ1/s_i² 复制到平均 Var ≤ tgt²；"
            "② 观测冗余 oversample×N（默认 4×，**设计裕量而非物理硬下界**）。"
            "秩不足时返回不可行 —— 结构问题不能靠数据量解决。"
            "时间口径：at_fs_seconds 假设专用校准模式以全 fs 连续观测；"
            "若在正常 A/B 交织下只观测 bank0，实际时间 ×2。"
        ),
    }


# ==========================================================================
# 端到端：估计 -> 扣除 -> 评分
# ==========================================================================
def calibrate_unit_weights(U_cal: np.ndarray, err_cal: np.ndarray, lam: float = 0.0) -> np.ndarray:
    """从校准数据估单位权重偏差。

    err = out - x_known = -sum_u w_u U[n,u] + noise
    ->  目标量 y = -err = sum_u w_u U[n,u] - noise

    Args:
        U_cal: 校准观测矩阵 [n x n_units，无量纲]。
        err_cal: 校准残差数组 [V] = out - x_known。
        lam: 岭正则强度 [无量纲缩放]，默认 0。

    Returns:
        单位权重偏差估计 w [V/单位当量]（n_units,）。仅可辨识子空间可信；
        零空间方向由 ridge_fit 压到 0（见其数值失效说明）。
    Side effects: 无（纯函数）。
    """
    y = -np.asarray(err_cal, dtype=float)
    w, _ = ridge_fit(U_cal, y, lam)
    return w


def apply_weight_correction(U: np.ndarray, w: np.ndarray) -> np.ndarray:
    """数字端扣除量（V）：+ sum_u w_u U[n,u]。

    Args:
        U: 运行态观测矩阵 [n x n_units，无量纲]（与校准同口径）。
        w: 单位权重偏差 [V/单位当量]（来自 calibrate_unit_weights）。

    Returns:
        数字端扣除量数组 [V] = U @ w，从残差里减去即可抵消权重失配。
    Side effects: 无（纯函数）。
    """
    return U @ w
