"""split采样mask的真实电荷与数字码单位换算。

aggregate实现用实际C/beta生成RDAC保存节点的输入等效注入[V]；数字扣除只用
名义栅格。生产物理pool还会按实际selected slice同步重算信号与注入，不能把
aggregate归一化直接当作18片共享同一物理电容。
"""

from __future__ import annotations

import numpy as np

from .config import Config
from .dac_arch import SplitChip
from .sampler import SampleBatch


def sampling_dither_injection(
    cfg: Config, chip: SplitChip, sample: SampleBatch, step0: float, c_sig: float
):
    """在RDAC命令生成前同步换算采样mask的电荷与码单位。

    Args:
        cfg: split配置；2D个固定末尾bank单元作为注入mask。
        chip: aggregate物理电容/beta实现，仅用于plant注入。
        sample: 可变SampleBatch，长度N；首次保存原bank码，之后复用该码换算。
        step0: 名义RDAC细栅格步长[输入等效V]。
        c_sig: aggregate总信号电荷归一化C=A+beta*B[F]，尚未减去mask。

    Returns:
        (N,)重算的RDAC保存节点注入[输入等效V]。

    Side effects:
        同步改x_rdac、dither、dither_code、dither_bank_code、signal_alpha。
        未被其他模块改写期间重复调用幂等；只用名义bank_step/step0转换数字码，
        main-bank码需乘n_sub，sub-bank码不变。真实mask失配不会被名义扣除自动消除。
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
