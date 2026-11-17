"""核组件入口的参数拒绝与学习数组隔离；真实 SV 运行另有 integration 测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _rtl_test_support import invoke, load_tool


ROOT = Path(__file__).resolve().parents[3]


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


@pytest.mark.parametrize("explicit_plant", [False, True], ids=["fixture", "plant-json"])
def test_valid_component_does_not_construct_training_rng_or_sine_arrays(
    tmp_path, monkeypatch, capsys, explicit_plant
):
    driver = load_tool("run_fig19_rtl_calibration", monkeypatch)
    fixture = ROOT / "flow/sim/fixtures/fig19_ideal_ports"
    nominal = driver.FixedPointReconstructor.from_dict(
        json.loads((fixture / "registers_nominal.json").read_text(encoding="utf-8"))
    )
    plant_args = []
    if explicit_plant:
        # 明确导出与 Fig19 映像同形状的实体。当前工程默认模型不是该夹具的来源。
        physical = driver.fixture_nominal_plant(fixture, nominal)
        plant = tmp_path / "plant.json"
        plant.write_text(json.dumps(physical))
        plant_args = ["--plant-json", plant]

    def forbidden_learning_array(*args, **kwargs):
        pytest.fail("component-only called an unused training RNG or holdout sine generator")

    boundary = []

    def stop_before_external_tool(command, **kwargs):
        boundary.append(command)
        raise OSError("TEST_STOP_BEFORE_EXTERNAL_TOOL")

    monkeypatch.setattr(driver, "default_model", forbidden_learning_array)
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
            "--fixture-dir",
            fixture,
            *plant_args,
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
    expected_source = (
        "EXPLICIT_FIXED_PLANT_JSON_NOT_LEARNER_INPUT"
        if explicit_plant
        else "DECLARED_FIG19_FIXTURE_NOMINAL_CAPACITANCE"
    )
    assert manifest["manufacturing"]["source"] == expected_source
    assert manifest["status"] == "FAIL", "the injected execution stop is not a completed RTL run"
    assert not (output / "frozen_fit.json").exists()
    assert not (output / "fitted_registers.json").exists()
