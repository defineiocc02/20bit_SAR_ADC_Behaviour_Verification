"""事务序列：stall 不越队、reset 取消在途、计数恒等。

判据：accepted == retired + cancelled + pending 且 pending == 0。
不要用 accepted + stalled + resets 这种"每个列表位置归入某分支"的伪守恒。
"""

from __future__ import annotations

import pytest

from adi_model.digital_chain import Transaction, run_sequence


def _txs(n=3):
    return [Transaction(i, 281, 0, 1000, {}) for i in range(n)]


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"stall_window": (1, 2)},
        {"reset_at": 1},
        {"stall_window": (1, 2), "reset_at": 2},
    ],
)
def test_counts_are_conserved(kw):
    r = run_sequence(_txs(), **kw)
    assert r["pending"] >= 0
    assert r["pending"] == 0
    assert r["accepted"] == r["retired"] + r["cancelled"] + r["pending"]
    assert r["conserved"] is True


def test_reset_after_stall_actually_runs():
    """原缺陷：stall 分支 break 后，后面的 reset 永远不执行。"""
    r = run_sequence(_txs(), stall_window=(1, 2), reset_at=2)
    assert any(e["event"] == "reset" for e in r["log"])
    assert r["cancelled"] == 1          # held 中被取消的那笔
    assert r["retired"] == 2
