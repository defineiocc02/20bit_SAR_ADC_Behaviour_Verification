"""检查导出文件身份及独立的模拟异常归属。"""

import importlib
import os
from pathlib import Path

import pytest

from adi_model import Config

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("alias_kind", ["dotdot", "symlink", "hardlink"])
@pytest.mark.parametrize("output_name", ["--out-registers", "--out-manifest"])
def test_fitted_export_rejects_input_output_alias_before_writing(
    tmp_path, monkeypatch, capsys, alias_kind, output_name
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    exporter = importlib.import_module("export_fitted_rtl")
    frozen = tmp_path / "frozen.json"
    original = b"frozen input must remain intact"
    frozen.write_bytes(original)
    alias = tmp_path / "alias.json"
    if alias_kind == "dotdot":
        (tmp_path / "directory").mkdir()
        alias = tmp_path / "directory" / ".." / frozen.name
    elif alias_kind == "symlink":
        alias.symlink_to(frozen)
    else:
        os.link(frozen, alias)
    outputs = {
        "--out-registers": tmp_path / "registers.json",
        "--out-manifest": tmp_path / "manifest.json",
    }
    outputs[output_name] = alias
    args = ["--config", "paper_literal", "--frozen", str(frozen)]
    for flag, path in outputs.items():
        args.extend((flag, str(path)))
    with pytest.raises(SystemExit) as error:
        exporter.main(args)
    assert error.value.code == 2
    assert "paths must be distinct" in capsys.readouterr().err
    assert frozen.read_bytes() == original
    assert not (
        tmp_path / ("manifest.json" if output_name == "--out-registers" else "registers.json")
    ).exists()


def test_fitted_export_rejects_two_output_aliases(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    exporter = importlib.import_module("export_fitted_rtl")
    frozen = tmp_path / "frozen.json"
    frozen.write_bytes(b"input")
    target = tmp_path / "result.json"
    target.write_bytes(b"existing result")
    alias = tmp_path / "alias.json"
    alias.symlink_to(target)
    with pytest.raises(SystemExit) as error:
        exporter.main(
            [
                "--config",
                "paper_literal",
                "--frozen",
                str(frozen),
                "--out-registers",
                str(target),
                "--out-manifest",
                str(alias),
            ]
        )
    assert error.value.code == 2
    assert "paths must be distinct" in capsys.readouterr().err
    assert target.read_bytes() == b"existing result"


def test_ra_only_attribution_includes_real_saturation_without_other_faults(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    exporter = importlib.import_module("export_rtl_vectors")
    files, report, _ = exporter.build_p2_link(Config.paper_literal(ra_v_clip=0.2))
    # Recompute the exclusive event directly from the exported stimulus columns.
    rows = [
        line.split()
        for line in files["p2_link_stim.hex"].splitlines()
        if line and not line.startswith(("//", "#"))
    ]
    exclusive = sum(
        int(row[7], 16) == 1 and int(row[5], 16) == 0 and int(row[6], 16) == 0 for row in rows
    )
    assert exclusive > 0
    actual = report["analog_flag_attribution"]
    assert actual["only_ra_sat"] == exclusive
    assert actual["ra_sat_without_rdac_or_adc2"] == exclusive
