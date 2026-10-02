"""Keep the externally fitted 18-slice RTL fixture bound to its register image."""

import hashlib
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "sim/vectors"


def test_fitted_fixture_hashes_and_full_geometry(monkeypatch):
    manifest = json.loads((VECTORS / "fitted18_manifest.json").read_text())
    assert manifest["rank"] == 18 * 71 + 1
    assert manifest["holdout_samples"] == 128
    assert manifest["fitted_rms_v"] < 0.3 * manifest["nominal_rms_v"]
    for name, digest in manifest["artifacts_sha256"].items():
        assert hashlib.sha256((VECTORS / name).read_bytes()).hexdigest() == digest
    registers = json.loads((VECTORS / "fitted18_registers.json").read_text())
    assert len(registers["weights_q"]) == 18
    assert all(len(row) == 71 for row in registers["weights_q"])
    assert len((VECTORS / "fitted18_weights.hex").read_text().splitlines()) == 1278
    assert len((VECTORS / "fitted18_holdout.hex").read_text().splitlines()) == 128

    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    exporter = importlib.import_module("export_fitted_rtl")
    frozen_bytes = (VECTORS / "fitted18_frozen.json").read_bytes()
    image, sidecar = exporter.build_fitted_registers(
        "paper_literal",
        frozen_bytes,
        dither_mode="sampling",
        dem_enable=True,
        dem_mode="permute",
    )
    assert image.encode("utf-8") == (VECTORS / "fitted18_registers.json").read_bytes()
    assert json.loads(sidecar)["register_sha256"] == hashlib.sha256(image.encode()).hexdigest()


def test_fitted_export_rejects_wrong_voltage_scale(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    exporter = importlib.import_module("export_fitted_rtl")
    frozen = json.loads((VECTORS / "fitted18_frozen.json").read_text())
    frozen["v_fs"] *= 1.01
    with pytest.raises(ValueError, match="geometry or Vfs"):
        exporter.build_fitted_registers("paper_literal", json.dumps(frozen).encode())
