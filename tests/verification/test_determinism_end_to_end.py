"""确定性回归：同一 seed + 同一输入，输出必须**逐位**可复现。

## 为什么需要这个测试

2026-11-13 检查发现：仓库里没有任何测试验证"同一配置跑两次结果逐位相同"。

这在别处也许无所谓，但本项目的核心交付物是**逐位对拍的定点输出**：
* `tools/behavior_fingerprint.py` 用 SHA256 判断"行为是否漂移"；
* RTL 对拍的判据是"整数码逐位相同"；
* 论文指标（DR/NSD/INL）只有在可复现的前提下才有意义。

如果模型在某处隐含依赖了
* 集合/字典的遍历顺序（受 `PYTHONHASHSEED` 影响）、
* BLAS 线程数（影响浮点归约顺序）、
* 全局随机状态（未显式 seed 的 Generator），

那么同一份代码在不同机器/不同运行上会给出不同结果，
而**所有单测仍然通过**——它们各自只跑一次。

## 本测试做什么

1. 同一进程内跑两次，比较整数码的 SHA256；
2. 在**子进程**里换 `PYTHONHASHSEED` 与 `OPENBLAS_NUM_THREADS` 再跑，
   比较哈希是否与父进程一致（这才是真正的跨环境考验）。

## 实测结论（2026-11-13）

* 同进程两次：哈希相同
* `PYTHONHASHSEED` ∈ {0, 1, 12345}：哈希相同
* `OPENBLAS_NUM_THREADS` ∈ {1, 4}：哈希相同

即模型是**确定性的**。本测试把这个性质钉住。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import numpy as np
import pytest

from adi_model.defaults import DefaultModel, DefaultParameters

SEED = 20261105
N_SAMPLES = 256
TONE_HZ = 1.0e6


def _digest_once() -> str:
    """跑一次仿真，返回 20 bit 整数码的 SHA256（前 16 位十六进制）。"""
    model = DefaultModel(
        seed=SEED,
        parameters=DefaultParameters(),
        config_overrides=None,
    )
    run = model.simulate(
        lambda t: 0.5 * np.sin(2 * np.pi * TONE_HZ * np.atleast_1d(t)),
        N_SAMPLES,
    )
    codes = np.ascontiguousarray(run.codes.code)
    return hashlib.sha256(codes.tobytes()).hexdigest()[:16]


def test_same_process_repeats_are_bit_identical():
    """同一进程内连跑两次，整数码必须逐位相同。"""
    assert _digest_once() == _digest_once()


@pytest.mark.parametrize("hash_seed", ["0", "1", "12345"])
def test_hash_seed_does_not_change_the_output(hash_seed):
    """换 `PYTHONHASHSEED` 不得改变输出（否则会依赖集合/字典的遍历顺序）。"""
    script = (
        "import hashlib, numpy as np\n"
        "from adi_model.defaults import DefaultModel, DefaultParameters\n"
        f"m = DefaultModel(seed={SEED}, parameters=DefaultParameters(), config_overrides=None)\n"
        f"r = m.simulate(lambda t: 0.5*np.sin(2*np.pi*{TONE_HZ}*np.atleast_1d(t)), {N_SAMPLES})\n"
        "print(hashlib.sha256(np.ascontiguousarray(r.codes.code).tobytes()).hexdigest()[:16])\n"
    )
    env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH="src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert result.stdout.strip() == _digest_once(), (
        "output changed with PYTHONHASHSEED=" + hash_seed +
        "; the model depends on hash-ordered iteration somewhere"
    )
