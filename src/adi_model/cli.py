# =============================================================================
# 文件名     : cli.py
# 模块名     : 配置检查与数字 IP 事务模拟
# 功能       : 模块入口与安装的 adi-simulate 命令调用同一生产参考。
# 设计说明   : 先校验输入，再独占创建输出目录，最后写完成元数据。
# =============================================================================
"""公开数字 IP 命令行。配置验证与产生样本是独立操作。"""

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
from typing import Any, NoReturn

import numpy as np

from adi_model.behavioral.sampler import dc_input, sine_input
from adi_model.defaults import DEFAULT_SEED, DefaultParameters, DefaultRun, default_model
from adi_model.inventory_gate import _write_new_summary
from adi_model.simulation.config import Config, ConfigError

__all__ = ["load_config", "main_validate_config", "main_simulate"]


# 1. 解析与输入身份
# 错误也输出结构化记录，便于调用方区分拒绝配置、运行成功和写盘失败。
class _ConfigParser(argparse.ArgumentParser):
    """CLI错误以JSON输出，保持argparse的明确参数规则。"""

    error_status = "CONFIGURATION_REJECTED"

    def error(self, message: str) -> NoReturn:
        """把失败原因作为结构化 JSON 写到 stderr 并退出（码 2）。

        Args:
            message: 面向使用者的原因说明（人类可读，不含密钥或路径明文）。

        Returns:
            不返回。

        Raises:
            SystemExit: 恒以 2 退出，便于脚本判定"输入被拒绝"。

        Side effects: 写 stderr；**不产生任何输出目录或部分结果**
            （"拒绝时不写文件"是 CLI 的合同，避免留下半成品）。
        """
        diagnostic = {"status": self.error_status, "error": message, "model_run": False}
        self.exit(2, json.dumps(diagnostic, ensure_ascii=False, allow_nan=False) + "\n")


class _SimulationParser(_ConfigParser):
    """模拟命令的输入错误不冒充配置检查或成功运行。"""

    error_status = "SIMULATION_REJECTED"


# JSON 允许解析器覆盖重复键；这样一个表中的两种几何可能悄悄只剩后一种。
# 读配置时保留键的出现次序，在产生任何输出前拒绝这种歧义。
def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, value in pairs:
        if name in values:
            raise ValueError(f"duplicate JSON key: {name}")
        values[name] = value
    return values


# 指数溢出可使一个形式合法的 JSON 数字成为无穷。
# 物理电压、噪声和采样率均须为有限数值。
def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"non-finite JSON number: {value}")
    return number


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


# 用原字节计算 SHA-256，实际解释的值保留在输入清单。
# 因此即使两个文件有同名字段，也能识别本次读入的具体输入。
# 长度限制适用于整个文件，避免在确认文件可用前分配大缓冲。
def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise ValueError(f"configuration is not a regular file: {path}")
    if path.stat().st_size > 300 * 1024**2:
        raise ValueError("configuration exceeds the 300 MiB file limit")
    # 先读同一份原字节，再解析与求摘要。
    # 各步骤分别重新开文件会在并发修改时得到互不对应的值。
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


# 低层的显式组件 Config 与生产命令的参数文件有不同用途。
# 单独检查配置时不隐式生成测试模型，也不声称测试已经通过。
def load_config(path: Path) -> Config:
    """读取并检查指定JSON对象；不回填物理输入或执行模拟。"""
    values, _digest = _read_json_object(path)
    config = Config.from_dict(values)
    config.check_legal()
    return config


# 两种 JSON 的用途在命令和回执中显式区分。保留 --config 的原有组件合同。
# --parameters 与 --config-overrides 则经过 simulate 使用的同一个生产构造器。
def main_validate_config(argv: Sequence[str] | None = None) -> int:
    """验证输入并返回其用途，退出 0 或 2。检查本身不产生样本或文件。"""
    parser = _ConfigParser(
        prog="adi-validate-config",
        allow_abbrev=False,
        description="检查生产数字模型参数，或显式检查低层组件 Config。",
    )
    parser.add_argument("--config", type=Path, metavar="FILE.json", help="低层组件的完整 Config")
    _add_production_inputs(parser)
    args = parser.parse_args(argv)
    # 空 JSON 对象是合法的生产默认值。按是否传入路径分组，不能按文件内容的真值猜类型。
    # 混用低层配置和两份生产输入时拒绝，以免其中一份文件被忽略却仍返回“有效”。
    production = args.parameters is not None or args.config_overrides is not None
    if (args.config is not None) == production:
        parser.error(
            "use --parameters / --config-overrides for the production model, or --config for a component"
        )
    result: dict[str, Any] = {
        "scope": "adc_digital_ip_v21" if production else "component_config",
        "model_run": False,
    }
    if args.config is not None:
        result["config"] = str(args.config)
    try:
        if production:
            parameters, overrides, inputs = _simulation_inputs(args)
            default_model(parameters=parameters, config_overrides=overrides)
            # 此处只验证目标接口与装表边界，不进入 simulate，也不创建运行输出目录。
            # 预检回执保留每份输入的角色与字节身份。完整权重数组留在调用者的文件内。
            result["input_files"] = [
                {key: item[key] for key in ("role", "path", "sha256")} for item in inputs
            ]
        else:
            load_config(args.config)
    except (OSError, UnicodeError, ValueError, TypeError, ConfigError) as exc:
        print(
            json.dumps(
                {**result, "status": "CONFIGURATION_REJECTED", "error": str(exc)},
                ensure_ascii=False,
                allow_nan=False,
            ),
            file=sys.stderr,
        )
        return 2
    # CONFIG_INPUTS_VALID 兼容旧调用者；scope 使一份组件校验回执不能冒充生产配置验收。
    result.update(status="CONFIG_INPUTS_VALID", source_conformance="NOT_ASSESSED")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


# 2. 数字合同与外部宏激励
# 参数文件和配置覆盖各自登记角色与原始摘要。二者最终仍交给同一个
# 生产构造器检查，以免一个入口宽容、一条路径却使用截断值。
# 可选的制造参数只进入外部宏，不改变数字权重种子或启动状态。
def _add_production_inputs(parser: argparse.ArgumentParser) -> None:
    """校验与模拟共用参数名和文件语义，低层组件另有 --config。"""
    parser.add_argument(
        "--parameters",
        type=Path,
        metavar="FILE.json",
        help="可选生产模型参数 JSON，包括器件与独立加载的 18×20 权重表",
    )
    parser.add_argument(
        "--config-overrides",
        type=Path,
        metavar="FILE.json",
        help="覆盖采样率、外部宏或控制配置；非法位宽/阵列拒绝",
    )


def _simulation_inputs(args) -> tuple[DefaultParameters | None, dict | None, list[dict]]:
    parameters = None
    overrides = None
    files = []
    if args.parameters is not None:
        values, digest = _read_json_object(args.parameters)
        parameters = DefaultParameters(**values)
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
        # 生产参数由同一个库构造器验证。不能让研究用 Config 悄悄转换或补齐字段。
        overrides = values
        files.append(
            {
                "role": "config_overrides",
                "path": str(args.config_overrides),
                "sha256": digest,
                "overrides": values,
            }
        )
    return parameters, overrides, files


# 输出路径可能是一个指向缺失目标的符号链接。
# 这种名称已被占用，必须在解析为实际目录前检查。
def _output_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    output = path.expanduser().absolute()
    # Checking the leaf before resolve also rejects dangling symbolic links.
    if output.exists() or output.is_symlink():
        raise ValueError(f"--output must be a new directory: {output}")
    return output


# 3. 逐笔数据与失败恢复
# 同一标签的开关、码和标志不可拆开发布。先在同目录完成临时文件，
# 再创建最终名称；若命名冲突，既有结果不被覆盖。
# 二位数组的第二维保存八个槽或八次事件，第一维仍须与样本数一致。
def _write_codes(path: Path, result: DefaultRun) -> None:
    """按样本号原子保存标志、码和开关；八槽位图与事件记录在第二维。"""
    arrays = result.arrays()
    count = len(result.codes.code)
    if any(value.ndim not in (1, 2) or len(value) != count for value in arrays.values()):
        raise ValueError("result record count differs between code and physical ports")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", dir=path.parent, prefix=".codes-", delete=False
        ) as handle:
            temporary = Path(handle.name)
            # 不传 allow_pickle 关键字：旧版 NumPy 会把它误存为一个数组。
            # 成员名由 DefaultRun 定义，所有成员均须是数值数组，兼容两种 savez 签名。
            if "allow_pickle" in arrays or any(a.dtype.kind not in "biuf" for a in arrays.values()):
                raise ValueError("unsupported archive member or dtype")
            archive: dict[str, Any] = arrays
            np.savez(handle, **archive)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# 批次元数据是完成标志，因此放在数值文件和假设表之后。
# 使用独占目录可避免并发实验把一个运行的假设配给另一个输出。
def _write_run(output: Path, result: DefaultRun, metadata: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)  # Atomic reservation; races never overwrite a user path.
    _write_codes(output / "codes.npz", result)
    _write_new_summary(output / "assumptions.json", metadata["inferences"])
    # Publish completion metadata last. Partial IO leaves no completed-run marker.
    _write_new_summary(output / "metadata.json", metadata)


# 4. 公共入口
# 正弦使用用户指定的实际频率，直流按固定差分电压采样。
# 通过测试针脚强制扰动字时仍逐笔消耗片内状态，和生产默认共用数据路径。
# 输出摘要给出 raw_invalid、retired 和 digital_errors，可定位输入或出码阶段。
# 当模型已运行但落盘失败时，回执不能写成输入非法。
# 参数来源用显式命令选项记录；以等号写参数和分开写参数应有相同身份。
def main_simulate(argv: Sequence[str] | None = None) -> int:
    """运行集中工程默认或显式覆盖，打印摘要JSON并可保存全部码/标志。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = _SimulationParser(
        prog="adi-simulate",
        description="运行 adc_digital_ip_v21 的事务级数字参考，使用显式的外部宏假设。",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--amplitude", type=float, default=2.7, help="差分正弦峰值[V]；保留超量程记录"
    )
    parser.add_argument(
        "--frequency", type=float, default=1e6, help="实际输入频率[Hz]；不调整为相干频率"
    )
    parser.add_argument("--dc", type=float, metavar="V", help="固定差分电压，替代正弦激励")
    parser.add_argument(
        "--dither-word",
        type=int,
        metavar="0..63",
        help="定向测试时逐笔强制此字；默认从 RTL 复位种子自动生字",
    )
    parser.add_argument("--samples", type=int, default=4096, help="保留的raw记录数，正uint32整数")
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="制造和宏噪声激励的根种子；片内字源遵循固定复位种子",
    )
    _add_production_inputs(parser)
    parser.add_argument(
        "--output",
        type=Path,
        metavar="NEW_DIR",
        help="新目录保存codes.npz/metadata.json/assumptions.json",
    )
    args = parser.parse_args(arguments)
    explicit_flags = {item.split("=", 1)[0] for item in arguments if item.startswith("--")}
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
        if args.dc is not None and (
            not math.isfinite(args.dc) or explicit_flags & {"--amplitude", "--frequency"}
        ):
            raise ValueError(
                "--dc requires a finite voltage and cannot be combined with sine options"
            )
        # 直流和正弦有不同的激励参数；不能把给定的固定输入悄悄替换成默认正弦。
        signal = (
            dc_input(args.dc) if args.dc is not None else sine_input(args.amplitude, args.frequency)
        )
        # 测试针脚仅替换本次受理的六位字。
        # 省略该选项时保留 RTL 复位字源，而非向模型传入一列零。
        forced = None
        if args.dither_word is not None:
            if not 0 <= args.dither_word <= 63:
                raise ValueError("--dither-word must be an integer in [0, 63]")
            forced = np.full(args.samples, args.dither_word, dtype=np.uint8)
        result = model.simulate(signal, args.samples, dither_words=forced)
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
    stimulus = {}
    waveform = (
        [("dc", args.dc, "V differential")]
        if args.dc is not None
        else [
            ("amplitude", args.amplitude, "V peak differential"),
            ("frequency", args.frequency, "Hz"),
        ]
    )
    for name, value, unit in waveform + [
        ("samples", args.samples, "records"),
        ("seed", args.seed, "integer"),
    ]:
        stimulus[name] = {
            "value": value,
            "unit": unit,
            "grade": "TEST_STIMULUS"
            if name in ("dc", "amplitude", "frequency")
            else "NUMERICAL_CONTROL",
            "origin": "USER_OVERRIDE" if "--" + name in explicit_flags else "CLI_DEMO_DEFAULT",
        }
    metadata = dict(result.metadata)
    metadata.update(
        {
            "status": "SIMULATION_COMPLETE",
            "model_run": True,
            "forced_dither_word": args.dither_word,
            "source_conformance": "NOT_ASSESSED",
            "records_filtered": 0,
            "stimulus": stimulus,
            "frequency_adjusted": False,
            "cli_input_mode": "PRODUCTION_DIGITAL_DEFAULTS"
            if not input_files
            else "EXPLICIT_PRODUCTION_INPUTS",
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
        "digital_contract",
        "forced_dither_word",
        "retired",
        "digital_errors",
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
    # 标准输出只给出摘要。
    # 数值与异常标志仍逐行保存在结果文件中。
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
