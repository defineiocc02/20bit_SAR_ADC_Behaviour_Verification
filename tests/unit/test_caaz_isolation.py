"""CAAZ 隔离门禁的自检：主路径不得残留实现符号。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_caaz_isolation_gate_passes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools/check_caaz_isolation.py")],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CAAZ_ISOLATION_PASS" in r.stdout
