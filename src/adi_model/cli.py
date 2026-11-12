"""配置校验与可运行工程默认入口。

validate-config只检查用户提供的低层Config，不运行模型。simulate直接调用真实
默认模型，允许可审阅的参数覆盖，返回未校准名义20bit码和完整标志；推断表
不赋予器件复现或20bit ENOB结论。文件输出排他创建，不覆盖已有结果。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn, TypedDict

import numpy as np

from adi_model.config import Config, ConfigError
from adi_model.defaults import DEFAULT_SEED, DefaultParameters, DefaultRun, default_model
from adi_model.fixed_point import FixedPointFormat
from adi_model.inventory_gate import _write_new_summary
from adi_model.sampler import sine_input

__all__ = ["load_config", "main_validate_config", "main_simulate"]


class _ConfigParser(argparse.ArgumentParser):
    """CLI错误以JSON输出，保持argparse的明确参数规则。"""

    error_status = "CONFIGURATION_REJECTED"

    def error(self, message: str) -> NoReturn:
        diagnostic = {"status": self.error_status, "error": message, "model_run": False}
        self.exit(2, json.dumps(diagnostic, ensure_ascii=False, allow_nan=False) + "\n")


class _SimulationParser(_ConfigParser):
    """模拟命令的输入错误不冒充配置检查或成功运行。"""

    error_status = "SIMULATION_REJECTED"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, value in pairs:
        if name in values:
            raise ValueError(f"duplicate JSON key: {name}")
        values[name] = value
    return values


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ValueError(f"configuration is not a regular file: {path}")
    if path.stat().st_size > 300 * 1024**2:
        raise ValueError("configuration exceeds the 300 MiB file limit")
    data = path.read_bytes()
    values = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_float=_finite_float,
        parse_constant=_reject_constant,
    )
    if not isinstance(values, dict):
        raise ValueError("configuration JSON must be an object")
    return values, hashlib.sha256(data).hexdigest()


def load_config(path: Path) -> Config:
    """读取并检查指定JSON对象；不回填物理输入或执行模拟。"""
    values, _digest = _read_json_object(path)
    config = Config.from_dict(values)
    config.check_legal()
    return config


def main_validate_config(argv: Sequence[str] | None = None) -> int:
    """检查必填配置，错误退出2；成功仅表示配置输入检查通过。"""
    parser = _ConfigParser(
        prog="adi-validate-config", description="检查明确提供的JSON配置；不运行仿真。"
    )
    parser.add_argument("--config", type=Path, required=True, metavar="FILE.json")
    args = parser.parse_args(argv)
    try:
        load_config(args.config)
    except (OSError, UnicodeError, ValueError, TypeError, ConfigError) as exc:
        print(
            json.dumps(
                {
                    "status": "CONFIGURATION_REJECTED",
                    "config": str(args.config),
                    "error": str(exc),
                    "model_run": False,
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": "CONFIG_INPUTS_VALID",
                "config": str(args.config),
                "model_run": False,
                "source_conformance": "NOT_ASSESSED",
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


def _simulation_inputs(args) -> tuple[DefaultParameters | None, dict | None, list[dict]]:
    parameters = None
    overrides = None
    files = []
    if args.parameters is not None:
        values, digest = _read_json_object(args.parameters)
        fields = dict(values)
        if isinstance(fields.get("format"), dict):
            fields["format"] = FixedPointFormat(**fields["format"])
        parameters = DefaultParameters(**fields)
        files.append(
            {
                "role": "parameters",
                "path": str(args.parameters),
                "sha256": digest,
                "overrides": values,
            }
        )
    if args.config_overrides is not None:
        values, digest = _read_json_object(args.config_overrides)
        # Convert only supplied nested Config fields; do not fill a missing field
        # from this incomplete Config object. The centralized model supplies its
        # visible baseline and consumes these exact overrides, including None.
        supplied = Config.from_dict(values)
        overrides = {name: getattr(supplied, name) for name in values}
        files.append(
            {
                "role": "config_overrides",
                "path": str(args.config_overrides),
                "sha256": digest,
                "overrides": values,
            }
        )
    return parameters, overrides, files


def _output_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    output = path.expanduser().absolute()
    # Checking the leaf before resolve also rejects dangling symbolic links.
    if output.exists() or output.is_symlink():
        raise ValueError(f"--output must be a new directory: {output}")
    return output


class _CodeArchiveArrays(TypedDict):
    """固定 NPZ 成员名，供 NumPy 的关键字参数检查使用。"""

    code: np.ndarray
    sample_id: np.ndarray
    clipped_low: np.ndarray
    clipped_high: np.ndarray
    analog_overflow: np.ndarray
    sadc_over: np.ndarray
    ra_sat: np.ndarray
    adc2_over: np.ndarray
    rdac_over: np.ndarray
    input_clean_v: np.ndarray
    input_v: np.ndarray


def _write_codes(path: Path, result: DefaultRun) -> None:
    arrays: _CodeArchiveArrays = {
        "code": result.codes.code,
        "sample_id": result.raw.sample_id,
        "clipped_low": result.codes.clipped_low,
        "clipped_high": result.codes.clipped_high,
        "analog_overflow": result.codes.analog_overflow,
        "sadc_over": result.raw.sadc_over,
        "ra_sat": result.raw.ra_sat,
        "adc2_over": result.raw.adc2_over,
        "rdac_over": result.raw.rdac_over,
        "input_clean_v": result.raw.sample.input_clean_v,
        "input_v": result.raw.sample.input_v,
    }
    count = len(result.codes.code)
    if any(np.asarray(value).shape != (count,) for value in arrays.values()):
        raise ValueError("result records/flags have inconsistent one-dimensional shapes")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", dir=path.parent, prefix=".codes-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            np.savez(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_run(output: Path, result: DefaultRun, metadata: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)  # Atomic reservation; races never overwrite a user path.
    _write_codes(output / "codes.npz", result)
    _write_new_summary(output / "assumptions.json", metadata["inferences"])
    # Publish completion metadata last. Partial IO leaves no completed-run marker.
    _write_new_summary(output / "metadata.json", metadata)


def main_simulate(argv: Sequence[str] | None = None) -> int:
    """运行集中工程默认或显式覆盖，打印摘要JSON并可保存全部码/标志。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _SimulationParser(
        prog="adi-simulate",
        description="实际运行推断默认模型；输出为未校准名义20bit码，非器件复现。",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--amplitude", type=float, default=2.7, help="差分正弦峰值[V]；保留超量程记录"
    )
    parser.add_argument(
        "--frequency", type=float, default=1e6, help="实际输入频率[Hz]；不调整为相干频率"
    )
    parser.add_argument("--samples", type=int, default=4096, help="保留的raw记录数，正uint32整数")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="独立数值流的可重放根种子")
    parser.add_argument(
        "--parameters",
        type=Path,
        metavar="FILE.json",
        help="可选DefaultParameters字段覆盖；format需完整格式对象",
    )
    parser.add_argument(
        "--config-overrides",
        type=Path,
        metavar="FILE.json",
        help="可选Config字段覆盖；未知/缺失激活输入拒绝",
    )
    parser.add_argument(
        "--output",
        type=Path,
        metavar="NEW_DIR",
        help="新目录保存codes.npz/metadata.json/assumptions.json",
    )
    args = parser.parse_args(arguments)
    try:
        if not math.isfinite(args.amplitude) or not math.isfinite(args.frequency):
            raise ValueError("amplitude and frequency must be finite")
        if not 1 <= args.samples <= 0xFFFF_FFFF:
            raise ValueError("samples must be a positive uint32-range integer")
        if args.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        output = _output_path(args.output)
        parameters, overrides, input_files = _simulation_inputs(args)
        model = default_model(seed=args.seed, parameters=parameters, config_overrides=overrides)
        result = model.simulate(sine_input(args.amplitude, args.frequency), args.samples)
    except (OSError, UnicodeError, ValueError, TypeError, ConfigError) as exc:
        print(
            json.dumps(
                {
                    "status": "SIMULATION_REJECTED",
                    "error": str(exc),
                    "model_run": False,
                    "source_conformance": "NOT_ASSESSED",
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2
    explicit_flags = {item.split("=", 1)[0] for item in arguments if item.startswith("--")}
    stimulus = {}
    for name, value, unit in (
        ("amplitude", args.amplitude, "V peak differential"),
        ("frequency", args.frequency, "Hz"),
        ("samples", args.samples, "raw records"),
        ("seed", args.seed, "integer"),
    ):
        stimulus[name] = {
            "value": value,
            "unit": unit,
            "grade": "TEST_STIMULUS" if name in ("amplitude", "frequency") else "NUMERICAL_CONTROL",
            "origin": "USER_OVERRIDE" if "--" + name in explicit_flags else "CLI_DEMO_DEFAULT",
        }
    metadata = dict(result.metadata)
    metadata.update(
        {
            "status": "SIMULATION_COMPLETE",
            "model_run": True,
            "result_role": "UNCALIBRATED_NOMINAL_20BIT",
            "source_conformance": "NOT_ASSESSED",
            "records_filtered": 0,
            "stimulus": stimulus,
            "frequency_adjusted": False,
            "cli_input_mode": "INFERRED_DEFAULTS"
            if not input_files
            else "EXPLICIT_OVERRIDES_ON_INFERRED_DEFAULTS",
            "cli_input_files": input_files,
        }
    )
    try:
        if output is not None:
            _write_run(output, result, metadata)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "SIMULATION_OUTPUT_ERROR",
                    "model_run": True,
                    "output_complete": False,
                    "error": str(exc),
                    "source_conformance": "NOT_ASSESSED",
                },
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2
    # Compact dataset summary; simulated vectors are only in the requested file.
    summary_keys = (
        "status",
        "model_run",
        "result_role",
        "source_conformance",
        "source_reproduction",
        "digital_coefficients",
        "precision_claim",
        "profile",
        "amendment_sha256",
        "output_bits",
        "n_samples",
        "seed",
        "run_id",
        "records_filtered",
        "stimulus",
        "frequency_adjusted",
        "cli_input_mode",
        "raw_invalid",
        "final_clip_low",
        "final_clip_high",
        "enabled",
        "unmodeled",
        "inferences",
    )
    summary = {name: metadata[name] for name in summary_keys}
    summary["n_codes"] = len(result.codes.code)
    summary["input_files"] = [
        {
            "role": item["role"],
            "path": item["path"],
            "sha256": item["sha256"],
            "override_fields": sorted(item["overrides"]),
        }
        for item in input_files
    ]
    summary["output_directory"] = str(output) if output is not None else None
    print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
    return 0
