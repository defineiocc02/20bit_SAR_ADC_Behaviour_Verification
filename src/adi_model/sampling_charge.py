"""Sampling charge and bank/RDAC unit conversion for the split topology.

The physical path uses fabricated capacitor values. Command conversion and
nominal digital subtraction use only design values. Both runners share this
module; neither runner imports physical helpers from the other runner.
"""

from __future__ import annotations

import numpy as np

from .config import Config
from .dac_arch import SplitChip
from .sampler import SampleBatch


def sampling_dither_injection(
    cfg: Config, chip: SplitChip, sample: SampleBatch, step0: float, c_sig: float
):
    """由同一采样掩码计算真实信号/dither 电荷并规范化码单位。

    必须在 RDAC 命令和动态计算之前调用。sampler 的 split 原始码表示
    bank 掩码增量；本函数将其保存为 dither_bank_code，并将 dither_code
    转换为名义 RDAC 单位。数字换算只使用名义参数，物理信号和注入使用
    真实电容。重复调用幂等，不会重复衰减或再次换算码单位。

    Args:
        cfg: 仿真配置（Config）；相关字段来源分级见 config.PARAM_GRADES。
        chip: 虚拟芯片（提供 C_main/C_sub 与 beta_true，用于掩码电容）。
        sample: SampleBatch（**会被改写**：x_rdac 与 dither 同步重标定）。
        step0: 本拓扑名义 RDAC 单位步长 [V]。
        c_sig: 信号电荷系数 [F] = chip.A + beta_true·chip.B（注入量归一化）。

    Returns:
        ``(N,)`` float64：重标定后的注入电压 [V]。

    Side effects:
        同步改写 x_rdac、dither、dither_code、dither_bank_code、signal_alpha。
    """
    d_old = np.asarray(sample.dither, dtype=float).copy()
    d_code = np.asarray(
        sample.dither_code if sample.dither_bank_code is None else sample.dither_bank_code,
        dtype=float,
    ).copy()
    nd = cfg.dither_units_total
    bank_arr = chip.C_sub if cfg.dither_split_bank == "sub" else chip.C_main
    mask = bank_arr[-nd:] if nd else bank_arr[:0]
    w_bank = chip.beta_true() if cfg.dither_split_bank == "sub" else 1.0
    beta_nom = chip.beta_nom()
    w_nom = beta_nom if cfg.dither_split_bank == "sub" else 1.0
    c_sig_nom = chip.c_sig_nom()
    bank_step_nom = 2 * cfg.v_fs * chip.c_unit_nom * w_nom / c_sig_nom
    alpha_true = 1.0 - w_bank * float(mask.sum()) / c_sig
    if getattr(cfg, "dither_discrete", False) and nd > 0:
        D_rng = int(round(cfg.dither_units_range))
        lut = {}
        for du in range(-D_rng, D_rng + 1):
            s = np.full(len(mask), -1.0)
            s[: len(mask) // 2 + du] = 1.0
            lut[du] = w_bank * cfg.v_fs * float(np.dot(mask, s)) / c_sig
        d_new = np.array([lut[int(round(d))] for d in d_code])
    else:
        # Continuous interpolation is a mathematical baseline with the same
        # bank charge scale; it is not an independently realizable switch mask.
        cap_mean = float(mask.mean()) if nd else chip.c_unit_nom
        d_new = d_code * 2 * cfg.v_fs * w_bank * cap_mean / c_sig
    old_alpha = cfg.dither_alpha if sample.dither_bank_code is None else sample.signal_alpha
    sample.x_rdac = sample.x_rdac - d_old + d_new + (alpha_true - old_alpha) * sample.x1
    sample.dither = d_new
    sample.dither_bank_code = d_code
    unit_ratio = cfg.dac_n_sub if cfg.dither_split_bank == "main" else 1
    if not np.isclose(bank_step_nom, unit_ratio * step0, rtol=1e-12, atol=0):
        raise ValueError("bank charge and nominal RDAC grid use inconsistent units")
    sample.dither_code = d_code * unit_ratio
    sample.signal_alpha = alpha_true
    return d_new
