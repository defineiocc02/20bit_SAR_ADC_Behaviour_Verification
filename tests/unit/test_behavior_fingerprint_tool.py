"""The behaviour-fingerprint tool must fail loudly, not just write a file.

G3 requires: read the baseline, compare, report the first difference, exit
non-zero, and never overwrite the baseline by default. A tool that only writes a
JSON file passes no verification at all.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/behavior_fingerprint.py"), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("fp")
    result = _run("--out", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    return out / "behavior_fingerprint.json"


def test_matching_baseline_exits_zero(baseline):
    result = _run("--check", str(baseline))
    assert result.returncode == 0, result.stdout + result.stderr


def test_drifted_baseline_exits_non_zero_and_names_the_first_field(baseline, tmp_path):
    document = json.loads(baseline.read_text())
    document["entries"]["static_dem_on"]["codes_voltage_sha256"] = "0" * 64
    corrupted = tmp_path / "corrupt.json"
    corrupted.write_text(json.dumps(document))

    result = _run("--check", str(corrupted))
    assert result.returncode == 1
    assert "First mismatch" in result.stdout
    assert "codes_voltage_sha256" in result.stdout


def test_existing_baseline_is_not_overwritten(baseline):
    before = baseline.read_bytes()
    result = _run("--out", str(baseline.parent))
    assert result.returncode == 2, "overwriting the baseline must be refused"
    assert "refusing to overwrite" in result.stdout
    assert baseline.read_bytes() == before
