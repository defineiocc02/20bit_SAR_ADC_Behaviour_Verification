# =============================================================================
# 文件名     : test_standalone_reference_models.py
# 模块名     : test_standalone_reference_models
# 版本       : v1.0
# 功能       : 核验独立数字参考中的选片交接和实际片组增益
# 设计说明   :
#   * 旧软件参考仍可生成对照值；用已知整数和片组条件检查两处边界。
#   * 这里只调用 Python 参考，不要求 RTL 工具或由真实器件推断指标。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================

from adi_model.dynamic_g import compute_g, deff, reciprocal_exact
from adi_model.scheduler18 import Scheduler18


# =============================================================================
# 1. 实际片组增益
# =============================================================================

def test_reconstruction_gain_uses_selected_slices_and_preserves_q24_scale():
    # 各活动片每列权重为 1/4（Q30 整数），8 片 × 16 列的精确增益为 32。
    # 未选片权重故意不同，误把它们计入会改变增益而无法侥幸通过。
    selected = tuple(range(1, 17, 2))
    gain = 32 << 30
    weights = {
        (sid, column): (1 if sid in selected else 3) << 28
        for sid in range(18)
        for column in range(16)
    }
    assert compute_g(weights, selected) == gain

    # 当 Wm=G/4 时，6 Wm/G - 3 = -1.5。该值在 Q24 中精确，
    # 可以直接发现倒数或乘积的移位错一位，无须照抄近似倒数算法。
    assert deff(gain // 4, gain, reciprocal_exact(gain)) == -(3 << 23)


# =============================================================================
# 2. 跨周期片组交接
# =============================================================================

def test_acquisition_set_becomes_next_conversion_set():
    scheduler = Scheduler18(seed=2026)
    scheduler.start()
    for _ in range(32):
        # 保存推进前的采集归属。只查推进后的集合大小不能发现采错上一笔。
        previous_acquisition = set(scheduler.acq)
        state = scheduler.step()
        acq, conv, idle = (set(state[name]) for name in ("acq", "conv", "idle"))
        assert conv == previous_acquisition
        assert (len(acq), len(conv), len(idle)) == (8, 8, 2)
        assert acq | conv | idle == set(range(18))
    assert scheduler.violations == []
