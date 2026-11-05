"""用保留的结构轨迹检查已决 RDAC 参考事件合同。

七事件夹具来自基线 2cbd359 的结构控制器记录：3 位前缀后跟六个已决位，
目标粗码为 173。它是工程时序合同，不能说明论文唯一指定了该开关序列。
若 RTL 时序变化，需重新取得对应结构轨迹；本测试说明不表示本轮已执行该流程。
"""

import numpy as np

from adi_model.reference_charge import resolved_loading_codes


def test_reference_load_matches_recorded_structural_events():
    """由保留的 DUT 轨迹约束前缀及重复已决码的事件语义。"""
    modeled = resolved_loading_codes(coarse=173, bits=9, units=1, dither=0)
    structural = np.array([128, 160, 160, 168, 172, 172, 173], dtype=float)
    np.testing.assert_array_equal(modeled, structural)
