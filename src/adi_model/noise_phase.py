"""noise_phase.py -- 逐相位噪声状态传递（v5.1 三轮审计 §五 -> stage16）。

把"采样噪声等效电容"从 [假设] 升级为**相位声明下的推导值**，
并回答审计的核心问题：KTC 观测电路实际看到的，是否就是进入残差的
那一份噪声组合？（"同一个随机数实现进两条通路"只是数值相关性，
不证明物理可观测。）

相位连接声明（与 charge_ref.py 一致，扩展到噪声）：
  * 采样相：主/子顶板钳位 V_cm，底板经开关接输入（dither 掩码单位接 ±V_FS）。
    开关断开瞬间每个电容保留独立噪声电荷，方差 kT·C
    （chi=2 = 差分两侧独立，与 config.chi 口径一致）。
  * 放大相：P 虚地、Q 浮置。噪声电荷状态向量 q = [q_M, q_S]：
      残差通路（输入等效）：n_path = (q_M + β·q_S)/C_sig
          -> a = [1, β]/C_sig   （由放大相节点方程直接导出）
      观测通路（输入等效）：n_obs = (q_M + γ·β·q_S)/C_sig + e_N
          -> b = [1, γβ]/C_sig  （γ = 观测网络对子阵列噪声的覆盖比例）
  * 数字相消后：n_res = (a − κ·b)ᵀq − κ·e_N
      σ²_res = (a − κb)ᵀ Σ_Q (a − κb) + κ²σ_eN²
      κ_opt  = (aᵀΣ_Q b)/(bᵀΣ_Q b)

三个直接推论（stage16 逐项验证）：
  1. γ=1（a≈b）：κ_opt=1，残差 = κ·σ_eN —— 观测电路拿到正确组合。
  2. γ=0（观测只接主阵列）：子阵列噪声 β·q_S 不可观消，
     残余 σ = β·sqrt(chi·kT·B)/C_sig —— 调 κ 救不回来（结构问题）。
  3. C_n,eq = C_sig²/(A+β²B)（sim_split 在用）= 1/(aᵀΣ_Q a·系数) ——
     主循环口径与相位推导一致；掩码不改变它（掩码单位噪声仍以 β 权重进入）。
单位契约：q=[q_M,q_S] 噪声状态 [C√(J) 量纲，逐相位节点方程口径]；
a/b 观测向量 [1/F]；输出 σ [V 折输入]。
参数来源分级：Σ_Q=kT·diag(A,B) 与 γ=1 为相位声明+乐观假设（[假设]）；
C_n,eq = C_sig²/(A+β²B) 为相位声明下的推导值（原 [假设] 升级）。

"""

from __future__ import annotations

import numpy as np

from .config import K_B, TEMP_K
from .dac_arch import SplitChip


def phase_noise_state(chip: SplitChip, gamma: float = 1.0, chi: float = 2.0) -> dict:
    """返回噪声状态模型：Σ_Q、残差通路 a、观测通路 b（输入等效口径）。

    gamma：观测网络对子阵列噪声的覆盖比例（γ=1 理想，γ=0 只看主阵列）。

    Args:
        chip:  SplitChip（提供 A/B、beta_true、c_sig_true）。
        gamma: 观测覆盖比例 γ [无量纲]（=1 理想，=0 子阵列噪声不可观消）。
        chi:   差分两侧独立系数 [无量纲]，=2 与 config.chi 口径一致（[推导]）。
    Returns:
        字典 {"Sigma_diag": Σ_Q 对角 [C²·J]，
              "a": 残差通路向量 [1/F]，
              "b": 观测通路向量 [1/F]，
              "beta": 子阵列权重 [无量纲]，
              "c_sig": 输入等效电容 C_sig [F]，
              "gamma": 回显 γ，"chi": 回显 chi}。
    Side effects: 无。
    """
    kTc = chi * K_B * TEMP_K
    beta = chip.beta_true()
    c_sig = chip.c_sig_true()
    sigma_q = np.array([np.sqrt(kTc * chip.A), np.sqrt(kTc * chip.B)])
    a = np.array([1.0, beta]) / c_sig
    b = np.array([1.0, gamma * beta]) / c_sig
    return {
        "Sigma_diag": sigma_q**2,
        "a": a,
        "b": b,
        "beta": beta,
        "c_sig": c_sig,
        "gamma": float(gamma),
        "chi": chi,
    }


def kappa_optimal(st: dict) -> float:
    """κ_opt = aᵀΣb / bᵀΣb（令 n_res = aᵀq − κ·bᵀq 的方差最小的 κ）。

    Args:
        st: phase_noise_state 返回的噪声状态字典（含 Sigma_diag/a/b）。
    Returns:
        κ_opt [无量纲]，使残差方差最小的最优相消系数。
    """
    S = np.diag(st["Sigma_diag"])
    a, b = st["a"], st["b"]
    return float(a @ S @ b / (b @ S @ b))


def sigma_res_analytic(st: dict, kappa: float, sigma_eN: float = 0.0) -> float:
    """σ_res = sqrt((a−κb)ᵀΣ(a−κb) + κ²σ_eN²)（输入等效，V）。

    Args:
        st:       噪声状态字典（Sigma_diag/a/b）。
        kappa:    实际使用的相消系数 κ [无量纲]。
        sigma_eN: 观测通路自身噪声 RMS σ_eN [V]，默认 0。
    Returns:
        σ_res [V]（折输入等效），数字相消后的残余采样噪声。
    """
    v = st["a"] - kappa * st["b"]
    return float(np.sqrt(np.dot(st["Sigma_diag"], v**2) + (kappa * sigma_eN) ** 2))


def sigma_total_sampling(st: dict) -> float:
    """无相消时的总采样噪声 σ = sqrt(aᵀΣa)（= sqrt(chi·kT/C_n,eq)，主循环口径）。

    Args:
        st: 噪声状态字典（Sigma_diag/a）。
    Returns:
        σ [V]（折输入），未施加 KTC 相消时的总采样噪声。
    """
    return float(np.sqrt(np.dot(st["Sigma_diag"], st["a"] ** 2)))


def sigma_uncancellable(st: dict) -> float:
    """γ<1 时结构性残余的下界：sqrt(aᵀΣa − (aᵀΣb)²/(bᵀΣb))。

    几何解释：a 在 b 方向上的投影可被 κ 消掉，正交分量永远留下。

    Args:
        st: 噪声状态字典（Sigma_diag/a/b）。
    Returns:
        σ_unc [V]（折输入），γ<1 时即使 κ=κ_opt 也必残留的下界。
    """
    S = np.diag(st["Sigma_diag"])
    a, b = st["a"], st["b"]
    atSa = a @ S @ a
    atSb = a @ S @ b
    btSb = b @ S @ b
    return float(np.sqrt(max(atSa - atSb**2 / btSb, 0.0)))


def monte_carlo_residual(
    st: dict,
    kappa: float,
    sigma_eN: float,
    n: int = 1 << 20,
    rng: np.random.Generator | None = None,
) -> dict:
    """Monte Carlo：抽 q、e_N，测 n_res 的 σ，并与解析式对账。

    同时做一次**回归式 κ 估计**（κ̂ = ⟨n_path, n_obs⟩/⟨n_obs,n_obs⟩，
    即校准能做的事），验证 κ̂ ≈ κ_opt —— 这就是"观测电路是否拿到
    正确组合"的可操作判据。

    Args:
        st:        噪声状态字典（Sigma_diag/a/b）。
        kappa:     实际相消系数 κ [无量纲]。
        sigma_eN:  观测通路噪声 RMS σ_eN [V]。
        n:         抽样数 [无量纲]，默认 2^20。
        rng:       可选随机源（np.random.Generator）；None = 固定种子 1234。
    Returns:
        字典 {"sigma_mc": 蒙特卡洛 σ_res [V]，
              "kappa_hat": 回归 κ̂ [无量纲]（应 ≈ κ_opt），
              "corr_path_obs": n_path 与 n_obs 皮尔逊相关系数 [无量纲]}。
    Side effects: 推进 rng（若提供）。
    """
    rng = rng or np.random.default_rng(1234)
    sd = np.sqrt(st["Sigma_diag"])
    qM = rng.normal(0.0, sd[0], n)
    qS = rng.normal(0.0, sd[1], n)
    eN = rng.normal(0.0, sigma_eN, n) if sigma_eN > 0 else np.zeros(n)
    n_path = st["a"][0] * qM + st["a"][1] * qS
    n_obs = st["b"][0] * qM + st["b"][1] * qS + eN
    n_res = n_path - kappa * n_obs
    kappa_hat = float(np.dot(n_path, n_obs) / np.dot(n_obs, n_obs))
    return {
        "sigma_mc": float(np.std(n_res)),
        "kappa_hat": kappa_hat,
        "corr_path_obs": float(np.corrcoef(n_path, n_obs)[0, 1]),
    }
