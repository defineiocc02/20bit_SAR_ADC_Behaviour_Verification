"""核组件入口的参数拒绝与学习数组隔离；真实 SV 运行另有 integration 测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _rtl_test_support import invoke, load_tool

from adi_model.defaults import default_model

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("field", ["--train", "--holdout"])
@pytest.mark.parametrize("value", ["0", "-1", "8193", str(1 << 64)])
def test_component_rejects_invalid_counts_before_any_output_or_tool_call(tmp_path, field, value):
    marker = tmp_path / "unexpected_tool_call"
    fake_tool = tmp_path / "explicit_tool"
    fake_tool.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(99)\n"
    )
    fake_tool.chmod(0o700)
    output = tmp_path / "rejected"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/run_fig19_rtl_calibration.py"),
            "--component-only",
            field,
            value,
            "--out",
            str(output),
        ],
        cwd=tmp_path,
        env=dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(ROOT / "src"),
            VERILATOR=str(fake_tool),
            OPENBLAS_NUM_THREADS="1",
        ),
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert "FAIL" in result.stderr and "train/holdout" in result.stderr
    assert "1..8192" in result.stderr
    assert not output.exists(), result.stdout + result.stderr
    assert not marker.exists(), "invalid counts reached the declared external tool"


def test_valid_component_does_not_construct_training_rng_or_sine_arrays(
    tmp_path, monkeypatch, capsys
):
    driver = load_tool("run_fig19_rtl_calibration", monkeypatch)
    # 实际 C 仅是这一输入边界测试的显式 plant，不交给任何拟合器。
    chip = default_model(seed=17).chip
    physical = {
        name: getattr(chip, name).tolist()
        for name in ("C_main", "C_sub", "C_bridge", "c_p_main", "c_p_sub")
    }
    physical["C_feedback_true"] = chip.require_feedback()
    plant = tmp_path / "plant.json"
    plant.write_text(json.dumps(physical))

    def forbidden_learning_array(*args, **kwargs):
        pytest.fail("component-only called an unused training RNG or holdout sine generator")

    boundary = []

    def stop_before_external_tool(command, **kwargs):
        boundary.append(command)
        raise OSError("TEST_STOP_BEFORE_EXTERNAL_TOOL")

    monkeypatch.setattr(driver.np.random, "Generator", forbidden_learning_array)
    monkeypatch.setattr(driver.np, "sin", forbidden_learning_array)
    monkeypatch.setattr(driver, "command_for", lambda simulator: ["explicit_test_tool"])
    monkeypatch.setattr(driver, "checked_run", stop_before_external_tool)
    output = tmp_path / "component_boundary"
    result = invoke(
        driver,
        [
            "--component-only",
            "--train",
            "4096",
            "--holdout",
            "512",
            "--plant-json",
            plant,
            "--out",
            output,
        ],
        monkeypatch,
    )
    assert result == 1
    assert boundary == [["explicit_test_tool", "-V"]]
    assert "TEST_STOP_BEFORE_EXTERNAL_TOOL" in capsys.readouterr().err
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["execution_mode"] == "COMPONENT_ONLY_NO_TRAINING"
    assert manifest["status"] == "FAIL", "the injected execution stop is not a completed RTL run"
    assert not (output / "frozen_fit.json").exists()
    assert not (output / "fitted_registers.json").exists()
