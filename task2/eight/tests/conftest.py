# =============================================================================
# 文件名     : conftest.py
# 模块名     : task2.eight.tests.conftest
# 版本       : v1.0
# 功能       : task2/eight 测试共用的名义网络与一次 _capture 调用。
#
# 设计说明   :
#   * 把 _capture 的导入集中在这里，而不是每个测试文件各导一次：它事实上被
#     task3 的两个工具与两个测试文件使用，本该是公有名；在改名之前至少不要让
#     私有导入告警按文件数增长。
#   * fixture 是 module 作用域：名义网络与失配抽样在整组测试里共用一份，
#     否则每个测试各建一次，几十条测试会白白多花十几秒。
#   * 噪声/参考都通过 capture() 的关键字传入，默认 None，与生产调用一致。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本：从 test_noise_injection.py 抽出，供 M4b 的参考注入测试共用。
# =============================================================================

"""task2/eight 测试共用的名义网络与一次 _capture 调用。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.capacitors import ExperimentConfig, cap_pool
from task1.experiments.adctoolbox_calibration import CalibrationConfig
from task1.experiments.residue_control_study import MapperTable
from task1.experiments.run_effects import schedule_sequence
from task1.model.codebook import codebook
from task2.eight.experiment import _capture
from task2.eight.model import ChargePool, native_step_v, words

SAMPLES = 4096
WORD_SEED = 0x5A17C0DE


@pytest.fixture(scope="module")
def rig():
    """构造一次完整 _capture 调用所需的名义网络，并给出绑定好的 capture()。

    返回的字典含 ``capture(noise=None, reference=None)``、``physical``、``plan``
    与 ``step_v``——后三项供需要把输入等效量折回残差域的测试使用。
    """
    experiment = ExperimentConfig(samples=SAMPLES)
    nominal, caps = cap_pool(experiment)
    physical = ChargePool.from_caps(caps, experiment)
    thresholds = np.r_[codebook(nominal, experiment), experiment.full_scale_peak_v]
    step_v = native_step_v(experiment)
    plan, _, phase = schedule_sequence(SAMPLES, experiment)
    word = words(np.ones(SAMPLES, dtype=bool), WORD_SEED)
    tone = experiment.tone_peak_v * np.sin(
        2 * np.pi * experiment.tone_bin * np.arange(SAMPLES) / SAMPLES
    )
    cfg = CalibrationConfig(adc2_half_range_v=0.100)
    table = MapperTable.build(bridge=True)

    def capture(noise=None, reference=None):
        return _capture(
            tone, word, plan, phase, thresholds, step_v, table, physical, cfg, noise, reference
        )

    return {"capture": capture, "physical": physical, "plan": plan, "step_v": step_v}
