"""提供 adi_model 测试共享的随机源、默认配置及相干正弦夹具。"""

from __future__ import annotations

import os

# --------------------------------------------------------------------- paths
# Ensure `import adi_model` works without setting PYTHONPATH manually.
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))


# --------------------------------------------------------------------- rng
@pytest.fixture
def rng() -> np.random.Generator:
    """用固定种子创建 default_rng，供确定性测试使用。"""
    return np.random.default_rng(20260910)


@pytest.fixture
def cfg():
    """返回主线默认 Config() 实例。"""
    from adi_model import Config

    return Config()


# --------------------------------------------------------------------- inputs
@pytest.fixture
def sine_input_fn(cfg):
    """返回 N=2**14 记录下位于相干频点的单音正弦。"""
    from adi_model import sine_input

    n = 2**14
    return sine_input(0.7 * cfg.v_fs, cfg.fs * 1021 / n)


@pytest.fixture
def small_n() -> int:
    """返回快速测试所用 N；须与选定输入频率相干。"""
    return 2**14
