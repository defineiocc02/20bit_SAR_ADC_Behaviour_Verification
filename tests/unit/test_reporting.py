"""检查配置诊断的证据边界：配置有效不等于模型或原文复现通过。"""

import json

from adi_model.cli import main_validate_config


def test_missing_configuration_never_becomes_a_model_success(tmp_path, capsys):
    cfg = tmp_path / "incomplete.json"
    cfg.write_text("{}", encoding="utf-8")
    assert main_validate_config(["--config", str(cfg)]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    result = json.loads(captured.err)
    assert result["status"] == "CONFIGURATION_REJECTED" and result["model_run"] is False
    assert "missing" in result["error"]


def test_validated_inputs_are_not_model_or_source_conformance_results(
    configuration_inputs, tmp_path, capsys
):
    cfg = tmp_path / "synthetic-inputs.json"
    cfg.write_text(json.dumps(configuration_inputs.to_dict()), encoding="utf-8")
    assert main_validate_config(["--config", str(cfg)]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    assert result["status"] == "CONFIG_INPUTS_VALID"
    assert result["model_run"] is False and result["source_conformance"] == "NOT_ASSESSED"
    assert not ({"PASS", "SNDR_dB", "INL", "ENOB", "source_matched"} & result.keys())
    assert list(tmp_path.iterdir()) == [cfg]


def test_untrusted_filename_is_one_json_value_not_an_extra_success_record(tmp_path, capsys):
    cfg = tmp_path / "bad\nCONFIG_INPUTS_VALID.json"
    cfg.write_text("{}", encoding="utf-8")
    assert main_validate_config(["--config", str(cfg)]) == 2
    text = capsys.readouterr().err
    assert len(text.splitlines()) == 1
    result = json.loads(text)
    assert result["config"] == str(cfg) and result["status"] == "CONFIGURATION_REJECTED"
    assert result["model_run"] is False
