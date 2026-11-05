"""检查只读配置 CLI 的严格输入及证据边界，不把配置有效视为模型运行成功。"""

import importlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from adi_model import __main__ as module_cli
from adi_model import cli

ROOT = Path(__file__).resolve().parents[2]


def _write_inputs(directory, cfg):
    path = directory / "explicit synthetic inputs.json"
    path.write_text(json.dumps(cfg.to_dict()), encoding="utf-8")
    return path


def test_pyproject_declares_only_the_current_config_command():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"(?ms)^\[project\.scripts\]\s*\n(.*?)(?=^\[|\Z)", text)
    assert section is not None
    scripts = dict(re.findall(r'^([A-Za-z][A-Za-z0-9_-]*)\s*=\s*"([^"]+)"', section[1], re.M))
    assert scripts["adi-validate-config"] == "adi_model.cli:main_validate_config"
    assert not ({"adi-run-all", "adi-make-report"} & scripts.keys())
    for target in scripts.values():
        module, name = target.split(":")
        assert callable(getattr(importlib.import_module(module), name))


@pytest.mark.parametrize("implicit", [False, True])
def test_module_dispatch_forwards_config_arguments_without_running_model(monkeypatch, implicit):
    calls = []
    monkeypatch.setattr(module_cli, "main_validate_config", lambda argv: calls.append(argv) or 0)
    argv = ["validate-config", "--config", "path with spaces.json"]
    if implicit:
        monkeypatch.setattr(sys, "argv", ["adi_model", *argv])
    assert module_cli.main(None if implicit else argv) == 0
    assert calls == [["--config", "path with spaces.json"]]


@pytest.mark.parametrize("argv", [[], ["run-all"], ["make-report"], ["validate-config"]])
def test_missing_or_retired_commands_are_structured_rejections(argv, capsys):
    with pytest.raises(SystemExit) as error:
        module_cli.main(argv)
    assert error.value.code == 2
    diagnostic = json.loads(capsys.readouterr().err)
    assert diagnostic["status"] == "CONFIGURATION_REJECTED"
    assert diagnostic["model_run"] is False


def test_help_shows_current_input_options(capsys):
    with pytest.raises(SystemExit) as error:
        module_cli.main(["validate-config", "--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--config" in help_text and "--results-dir" not in help_text


def test_valid_inputs_have_no_side_effects_or_performance_claim(
    configuration_inputs, tmp_path, capsys, monkeypatch
):
    path = _write_inputs(tmp_path, configuration_inputs)
    monkeypatch.setenv("ADC_TEST_CALLER_CONTEXT", "preserve-this")
    environment = dict(os.environ)
    assert cli.main_validate_config(["--config", str(path)]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert result["status"] == "CONFIG_INPUTS_VALID"
    assert result["model_run"] is False
    assert result["source_conformance"] == "NOT_ASSESSED"
    assert list(tmp_path.iterdir()) == [path]
    assert dict(os.environ) == environment
    assert not ({"PASS", "SNDR_dB", "ENOB", "INL", "DNL"} & result.keys())


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "null",
        "{",
        '{"fs": NaN}',
        '{"fs": Infinity}',
        '{"fs": -Infinity}',
        '{"fs": 1e999}',
        '{"fs": 40e6}',
        '{"fs":40000000,"fs":80000000}',
        '{"ktc_enable":true}',
        '{"dither_transfer_model":"dual_port"}',
    ],
)
def test_malformed_or_obsolete_json_is_not_repaired(payload, tmp_path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text(payload, encoding="utf-8")
    before = path.read_bytes()
    assert cli.main_validate_config(["--config", str(path)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    result = json.loads(captured.err)
    assert result["status"] == "CONFIGURATION_REJECTED"
    assert result["model_run"] is False and result["error"]
    assert path.read_bytes() == before


def test_absent_file_directory_and_invalid_utf8_fail_cleanly(tmp_path, capsys):
    invalid_utf8 = tmp_path / "invalid-encoding.json"
    invalid_utf8.write_bytes(b"\xff\xfe")
    for path in (tmp_path / "missing.json", tmp_path, invalid_utf8):
        assert cli.main_validate_config(["--config", str(path)]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        diagnostic = json.loads(captured.err)
        assert diagnostic["status"] == "CONFIGURATION_REJECTED"
        assert diagnostic["model_run"] is False


def test_oversized_config_is_rejected_before_any_read():
    # 用大小模拟值检查 300 MiB 限制，不创建或读取大文件。
    path = SimpleNamespace(
        is_file=lambda: True,
        stat=lambda: SimpleNamespace(st_size=300 * 1024**2 + 1),
        read_text=Mock(),
    )
    with pytest.raises(ValueError, match="300 MiB"):
        cli.load_config(path)
    path.read_text.assert_not_called()


def test_unknown_options_cannot_restore_legacy_report_execution(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main_validate_config(["--results-dir", "result"])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().err)["model_run"] is False
