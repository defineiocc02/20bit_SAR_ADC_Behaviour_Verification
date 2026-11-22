# =============================================================================
# 文件名     : test_simulate_ordinary_az.py
# 模块名     : Fig.11 余差放大器自归零
# 功能       : 验证显式 AZ 对样本数和码值的影响。
# 设计说明   : 相同种子的基准用来隔离 AZ 开关，数字事务回归另外驱动生产顶层。
# =============================================================================
"""Fig.11 电荷夹具的普通 AZ 实验。检查样本数、逐位一致性及偏置的作用。"""

from __future__ import annotations

import numpy as np

from adi_model.behavioral.ra_az import OrdinaryAZConfig
from adi_model.behavioral.sampler import sine_input
from experiments.fig11 import fig11_model


# 普通 AZ 是显式 Fig.11 外部模拟实验，在生产数字端没有隐式入口。
# 不启用该实验时，两次同种子的码序列须一致。
def test_az_none_matches_baseline():
    m = fig11_model()
    a = m.simulate(sine_input(2.7, 1e6), 32)
    b = m.simulate(sine_input(2.7, 1e6), 32, ordinary_az=None)
    assert np.array_equal(a.codes.code, b.codes.code)


# 零误差仍走一次真实 AZ 调用，更容易发现错误的使能副作用。
# 输出数必须保持与输入相同，避免把研究支路误当作两样本斩波。
def test_az_zero_error_is_bit_identical_and_full_rate():
    m = fig11_model()
    base = m.simulate(sine_input(2.7, 1e6), 32)
    az = m.simulate(
        sine_input(2.7, 1e6), 32,
        ordinary_az=OrdinaryAZConfig(enabled=True, offset_v=0.0, noise_v=0.0),
    )
    assert az.codes.code.shape == base.codes.code.shape
    np.testing.assert_array_equal(az.codes.code, base.codes.code)


# 显式偏置应影响码值，样本编号及采样数继续保持一一对应。
def test_az_offset_changes_codes_but_keeps_n_to_n():
    m = fig11_model()
    base = m.simulate(sine_input(2.7, 1e6), 32)
    az = m.simulate(
        sine_input(2.7, 1e6), 32,
        ordinary_az=OrdinaryAZConfig(enabled=True, offset_v=1e-3),
    )
    assert az.codes.code.shape == (32,)
    assert int(np.sum(az.codes.code != base.codes.code)) > 0
    assert az.metadata["ordinary_az"]["pairing"].startswith("none")
