"""端到端指标回归：模型必须仍然给出与论文 [00] 吻合的 DR 与 NSD。

## 为什么需要这个测试

2026-11-13 检查发现：**没有任何测试真正跑模型再断言"DR ≈ 94.2 dB"**。

既有的 `test_readme_commands.py` 只校验"README 的数字 == 归档 JSON 的数字"，
但归档 JSON 是**冻结的数据文件**——即使模型退化了，那个测试仍然通过。

于是存在一个空档：
* 边角行为（掩码、定点、dither）有大量单测；
* **但"模型整体是否还能给出论文量级的 DR/NSD"没人管**。

本测试补这个空档。它跑一次真实仿真（seeded，确定性），
断言 DR 与 NSD 落在论文值附近。

## 为什么用宽松容差

论文 DR = 94.2 dB、NSD = 9.3 nV/√Hz。本模型给 94.33 dB / 8.55 nV/√Hz
（σ = 1e-4 从这两个指标反解，见 `docs/mechanisms/paper_metrics_calibration.md`）。

容差取 ±1.5 dB / ±20%，只用来抓**量级退化**（例如错接、参数漂移、单位错误），
**不用来声称"复现了论文"**——关于"吻合 ≠ 复现"的限定见 README §1。

## 运行成本

动态仿真 2048 点，约 3–5 s。放在 `tests/verification` 而不是 unit，
因为它属于"端到端验收"而非"单元行为"。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.defaults import DefaultModel, DefaultParameters
from adi_model.metrics.metrics import sine_fit_metrics

FS_HZ = 40e6
V_FS = 3.0
TONE_BIN = 101          # 相干
N_SAMPLES = 2048


def _run_dynamic(seed: int = 20261105) -> tuple[float, float]:
    """跑失配+噪声的动态仿真，返回 (DR_dB, NSD_V_per_rtHz)。

    DR 的定义（与 `tools/run_p4_ablation.py` 一致）：
    用**小幅**正弦（满量程的 1e-3）测出噪声 RMS，再与满量程正弦功率相比：
        DR = 20·log10( (V_FS/√2) / noise_rms )
    NSD 取**大幅**正弦那次的全 Nyquist 白噪声等效密度。
    """
    model = DefaultModel(
        seed=seed,
        parameters=DefaultParameters(),      # 默认 σ=1e-4、噪声启用
        config_overrides=None,
    )
    fin_hz = TONE_BIN * FS_HZ / N_SAMPLES
    # 与 tools/run_p4_ablation.py 一致：满幅 = V_FS × 0.999
    # （V_FS 已是差分等效峰值，不要再乘 0.5）
    full_amplitude = V_FS * 0.999
    full = model.simulate(
        lambda t: full_amplitude * np.sin(2 * np.pi * fin_hz * np.atleast_1d(t)),
        N_SAMPLES,
    )
    small = model.simulate(
        lambda t: (V_FS * 1e-3) * np.sin(2 * np.pi * fin_hz * np.atleast_1d(t)),
        N_SAMPLES,
    )
    # 必须测**重构后的输出**（`codes.voltage`），不是 RA 节点 `raw.vra`。
    # 与 tools/run_p4_ablation.py 的 `_dynamic` 保持一致：
    #   voltage = decoder.reconstruct(digital_observation, physical_gain=estimated_gain).voltage
    # 用 raw.vra 会把二级量化与重构路径排除在外，测出的 DR 只有 63 dB。
    full_metrics = sine_fit_metrics(np.asarray(full.codes.voltage, dtype=float), FS_HZ, fin_hz)
    small_metrics = sine_fit_metrics(np.asarray(small.codes.voltage, dtype=float), FS_HZ, fin_hz)
    noise_rms = max(float(small_metrics["noise_rms"]), 1e-30)
    dr_db = float(20 * np.log10((V_FS / np.sqrt(2)) / noise_rms))
    return dr_db, float(full_metrics["NSD_V_rtHz"])


@pytest.fixture(scope="module")
def dynamic_result():
    return _run_dynamic()


def test_dynamic_range_is_still_at_the_paper_level(dynamic_result):
    """DR 必须仍在 94.2 dB 附近（±1.5 dB 只抓量级退化）。"""
    dr_db, _ = dynamic_result
    assert dr_db == pytest.approx(94.2, abs=1.5), (
        f"DR degraded to {dr_db:.2f} dB (paper 94.2); "
        "check for a parameter/unit regression"
    )


def test_noise_spectral_density_is_still_at_the_paper_level(dynamic_result):
    """NSD（全 Nyquist 口径）必须仍在 9.3 nV/√Hz 附近（±20% 只抓量级退化）。"""
    _, nsd = dynamic_result
    assert nsd * 1e9 == pytest.approx(9.3, rel=0.20), (
        f"NSD degraded to {nsd * 1e9:.2f} nV/rtHz (paper 9.3); "
        "check the noise budget or the normalization"
    )
