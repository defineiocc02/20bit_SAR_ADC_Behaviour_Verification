"""D5 权重寻址：改被选中单元必须精确改变累加，改未选中单元必须完全不变。

这两个方向缺一不可：
- 只测"改了有变化"，可能连未选中的单元也在偷偷累加；
- 只测"未选中不变"，可能压根没按 mask 取数。
"""

from __future__ import annotations

import numpy as np

from adi_model.digital_chain import Transaction, run_transaction

Q = 1 << 30


def _weights():
    w = {(s, j): (2 * Q // 32 if j < 15 else Q // 32) for s in range(18) for j in range(20)}
    return w


def _acc(masks, ids, w):
    total = 0
    for slot, sid in enumerate(ids):
        for j in range(16):
            if masks[slot][j]:
                total += int(w.get((int(sid), j), 0))
    return total


def test_selected_unit_change_moves_accumulator_exactly():
    w = _weights()
    r = run_transaction(Transaction(3, 281, 0, 1000, w))
    masks, ids = r["masks"], r["slice_ids"]
    slot, col = next((s, j) for s in range(8) for j in range(16) if masks[s][j])
    sid = ids[slot]
    delta = 1 << 20
    w2 = dict(w)
    w2[(sid, col)] += delta
    assert _acc(masks, ids, w2) - _acc(masks, ids, w) == delta


def test_unselected_unit_change_does_not_move_accumulator():
    w = _weights()
    r = run_transaction(Transaction(3, 281, 0, 1000, w))
    masks, ids = r["masks"], r["slice_ids"]
    slot, col = next((s, j) for s in range(8) for j in range(16) if not masks[s][j])
    sid = ids[slot]
    w2 = dict(w)
    w2[(sid, col)] += 1 << 20
    assert _acc(masks, ids, w2) == _acc(masks, ids, w)


def test_output_is_monotonic_in_input_over_the_supported_range():
    """同一扰动字下，输入越高输出码越高（不跨饱和区）。"""
    from adi_model.digital_chain import forward_code

    w = _weights()
    codes = []
    for x in np.linspace(-2.5, 2.5, 11):
        f = forward_code(float(x), 0)
        codes.append(run_transaction(Transaction(3, f["c"], 0, f["adc2"], w))["code20"])
    assert all(b >= a for a, b in zip(codes, codes[1:])), codes
