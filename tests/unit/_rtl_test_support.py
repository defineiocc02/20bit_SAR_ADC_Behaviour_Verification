"""为 RTL 导出测试共享显式数值夹具。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "sim" / "fixtures" / "source_control_component"
BUNDLE_FILES = {
    "rtl_params.vh",
    "source_control_binding.sv",
    "source_acquisition_binding.sv",
    "packet_schema.json",
    "config.json",
    "format.json",
    "binding.json",
    "rtl_input_manifest.json",
}


def blob(value: str | bytes) -> bytes:
    """返回导出器的精确字节，不归一化换行。"""
    return value.encode("utf-8") if isinstance(value, str) else value


def sha(value: str | bytes) -> str:
    """独立计算字节摘要，不复用被测导出器的摘要函数。"""
    return hashlib.sha256(blob(value)).hexdigest()


def read_json(path: Path):
    """读取显式测试输入或生成的 JSON 产物。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    """只写测试拥有的输入，不推断生产参数。"""
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def copy_inputs(tmp_path: Path):
    """复制已标注范围的数字夹具，不运行物理 ADC 或训练。"""
    folder = tmp_path / "explicit_inputs"
    folder.mkdir()
    for name in (
        "config.json",
        "format.json",
        "binding.json",
        "source_control_binding.sv",
        "source_acquisition_binding.sv",
        "registers.json",
    ):
        shutil.copyfile(FIXTURE / name, folder / name)
    cfg = read_json(folder / "config.json")
    cfg.update(fs=40e6, n_bits_target=20, v_fs=3.0, b1=9, g0=32.0, n_slices=18, n_active=8)
    write_json(folder / "config.json", cfg)
    return SimpleNamespace(
        folder=folder,
        config=folder / "config.json",
        format=folder / "format.json",
        binding=folder / "binding.json",
        source=folder / "source_control_binding.sv",
        acquisition=folder / "source_acquisition_binding.sv",
        registers=folder / "registers.json",
    )


def load_tool(name: str, monkeypatch):
    """导入实际当前工具，并显式解析其同目录依赖。"""
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def invoke(module, arguments, monkeypatch):
    """直接调用真实 argparse 入口，不另启进程。"""
    monkeypatch.setattr(sys, "argv", [str(module.__file__), *map(str, arguments)])
    return module.main()


def input_args(inputs) -> list[str]:
    """提供配置、整数格式与绑定三项显式输入路径。"""
    return [
        "--config",
        str(inputs.config),
        "--format",
        str(inputs.format),
        "--binding",
        str(inputs.binding),
    ]


def snapshot_inputs(inputs) -> dict[str, bytes]:
    """保存输入字节，用于检查拒绝路径是否破坏输入。"""
    return {p.name: p.read_bytes() for p in inputs.folder.iterdir() if p.is_file()}


@lru_cache(maxsize=1)
def fitted_math_bytes() -> bytes:
    """求解显式 256×55 静态线性回归，不运行物理 ADC。"""
    import numpy as np

    from adi_model.calibration.weight_calibration import CalibrationSpec, DigitalObservation, fit_unit_weights

    spec = CalibrationSpec(**read_json(FIXTURE / "registers.json")["spec"])
    generator = np.random.default_rng(407)
    count = 256
    ids = np.stack([generator.choice(18, 8, replace=False) for _ in range(count)])
    main = generator.integers(0, 2, (count, 8, 2))
    sub = generator.integers(0, 2, (count, 8, 1))
    terms = (1 - 2 * np.concatenate((main, sub), axis=2)) * 3.0
    signal = np.ones((count, 8, 3), dtype=np.int64)
    reference = generator.uniform(-2.0, 2.0, count)
    coefficients = 0.002 + np.arange(54).reshape(18, 3) * 0.0000007
    linear = np.sum(coefficients[ids] * (reference[:, None, None] + terms), axis=(1, 2)) + 0.001
    codes = np.floor((linear - spec.adc2_v_min) / spec.adc2_step_v).astype(np.int64)
    assert np.all((codes >= 0) & (codes < 64))
    data = DigitalObservation(
        spec=spec,
        slice_ids=ids,
        adc2_code=codes,
        main_masks=main,
        sub_masks=sub,
        signal_mask=signal,
        known_voltage_term_v=terms,
        overflow=np.zeros(count, bool),
        sample_ids=np.arange(count),
        static_charge_domain=True,
    )
    frozen = fit_unit_weights(data, reference)
    assert frozen.rank == 55 and frozen.training_samples == count
    return (json.dumps(frozen.to_dict(), sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def write_frozen(inputs) -> Path:
    """写入实际求解的数学拟合，不虚构拟合诊断值。"""
    path = inputs.folder / "synthetic_frozen.json"
    path.write_bytes(fitted_math_bytes())
    return path
