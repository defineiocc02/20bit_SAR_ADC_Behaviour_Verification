#!/usr/bin/env python3
# =============================================================================
# 文件名     : export_rtl_params.py
# 版本       : v1.0
# 功能       : 校验显式源控制合同，导出整数 RTL 组件输入包。
# 设计说明   : 组件的信号折叠以同一份掩码、参考电压和 Q 格式为依据。
#              下游可通过元数据和哈希核对每个输入的来历。
# =============================================================================
"""从显式 Config、整数格式和源控制绑定生成 RTL 输入包。

当前只支持声明为全信号采样、对称参考轨的折叠子集。
本工具不提供器件默认值、物理选通映射、扰动实现、校准系数或模拟／时序验证结果。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from adi_model.calibration.weight_calibration import CalibrationSpec
from adi_model.contracts.fixed_point import FixedPointFormat
from adi_model.control.dem import SplitSwitchGeometry
from adi_model.simulation.config import Config


# =============================================================================
# 1. 输入语法与标量校验
# =============================================================================
# 生成的 JSON 用于字节比对。规定排序、行末和有限数值可使这些检查重跑一致。
def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(path: Path) -> dict:
    # 拒绝重复字段，防止同一文件在另一解析器中选取不同连接参数。
    def unique_object(pairs):
        result = {}
        for name, item in pairs:
            if name in result:
                raise ValueError(f"duplicate JSON field: {name}")
            result[name] = item
        return result

    def reject_constant(name):
        raise ValueError(f"nonfinite JSON constant: {name}")

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path}: an explicit JSON object is required")
    return value


# 消费者名随错误返回。配置与物理绑定可能使用相同字段名，留出定位上下文。
def _required(mapping: dict, name: str, consumer: str):
    if name not in mapping or mapping[name] is None:
        raise ValueError(f"missing {name}; consumed by {consumer}")
    return mapping[name]


def _integer(value: object, name: str, low: int, high: int) -> int:
    # Python 的 bool 是 int 子类。位宽、片号和时钟配置只接受实际整数。
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an explicit integer in [{low}, {high}]")
    return value


# 只接受该接口认识的附加字段，避免拼写错误的可选项落入通用默认分支。
def _known_keys(value: dict, allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {name} fields: {', '.join(sorted(unknown))}")


# =============================================================================
# 2. 检查组件可表达的几何、位宽和连接
# =============================================================================
# 先验证所有消费字段再生成输入。这样接口版本不一致会在编译 RTL 前被发现。
def load_contract(config_path: Path, format_path: Path, binding_path: Path) -> dict:
    """在写文件前校验全部消费字段及逐单元折叠条件。

    Args:
        config_path: 显式 Config JSON 路径，包含所消费的量化与目标规格。
        format_path: 显式整数位宽及小数位 JSON 路径。
        binding_path: 几何、时序、调度、参考连接及源控制模块的绑定 JSON 路径。

    Returns:
        经校验的配置、格式、规格、几何、源控制字节及输入摘要。

    Raises:
        ValueError: 输入缺项、越出工具支持域、绑定不匹配或逐单元折叠条件不成立。
    """
    cfg = Config.from_dict(_object(config_path))
    raw_format = _object(format_path)
    binding = _object(binding_path)
    for name in (
        "weight_fraction_bits",
        "voltage_fraction_bits",
        "voltage_bits",
        "coefficient_bits",
        "accumulator_bits",
        "output_bits",
    ):
        _required(raw_format, name, "RTL integer arithmetic")
    # 折叠运算的中间项、权重和电压各有固定二进制小数点，必须成套匹配。
    # 即使总位数相同，更改小数位也会改变最终截断和饱和发生的位置。
    fmt = FixedPointFormat(**raw_format)
    supported = (30, 32, 64, 48, 96, 20)
    selected = (
        fmt.weight_fraction_bits,
        fmt.voltage_fraction_bits,
        fmt.voltage_bits,
        fmt.coefficient_bits,
        fmt.accumulator_bits,
        fmt.output_bits,
    )
    if selected != supported:
        raise ValueError("UNSUPPORTED RTL format; no implicit Q/width substitution")
    # 绑定版本与输出报文的版本独立。前者描述工程输入声明，后者描述数据解析。
    if binding.get("schema_version") != 1:
        raise ValueError("binding.schema_version must explicitly be 1")
    _known_keys(
        binding,
        {
            "schema_version",
            "geometry",
            "control_word_bits",
            "state_bits",
            "sample_id_bits",
            "timing",
            "scheduler",
            "divider_stages",
            "source_control",
            "reconstruction_fold",
            "provenance",
            "acquisition_control",
            "dither_contract",
        },
        "binding",
    )
    raw_geometry = _required(binding, "geometry", "physical interface")
    if not isinstance(raw_geometry, dict):
        raise ValueError("binding.geometry must be an object")
    for name in ("n_main", "n_sub", "n_slices", "n_active"):
        _required(raw_geometry, name, "physical geometry")
    # 该接口以实际掩码参与运算。独立码本的边界可显式为空。
    # This consumer records actual masks, not an independent RDAC k codebook.
    # Retain explicit unknown endpoints without inventing a domain from coarse9.
    for name in ("code_min", "code_max"):
        if name not in raw_geometry:
            raise ValueError(f"missing {name}; declare the independent codebook or explicit null")
    geo = SplitSwitchGeometry(**raw_geometry)
    # 先把配置和几何对齐，再从同一对象导出片组与位宽，避免两份定义混用。
    if (geo.n_slices, geo.n_active, cfg.n_slices, cfg.n_active, cfg.b1, cfg.n_bits_target) != (
        18,
        8,
        18,
        8,
        9,
        20,
    ):
        raise ValueError("source target requires 18 slices, 8 active, 9 coarse and 20 output bits")
    # 单片的数组寻址由支持的组件尺寸限定；超域情况需新的组件合同。
    if geo.n_main + geo.n_sub > 128:
        raise ValueError("UNSUPPORTED physical unit-address dimension")
    # 采样率用于与纳秒节拍做量纲检查，不允许布尔字段被当作一赫兹。
    if (
        isinstance(cfg.fs, bool)
        or not isinstance(cfg.fs, int | float)
        or not math.isfinite(cfg.fs)
        or cfg.fs <= 0
    ):
        raise ValueError("Config.fs must be finite and positive [Hz]")
    if type(cfg.dem_enable) is not bool or type(cfg.dither_enable) is not bool:
        raise ValueError("DEM and dither requests must be explicit Boolean values")
    # 可接收的 ADC2 判决数取决于组件自身的相位预算。
    # 组件输入包只面向下方校验的固定帧调度。
    spec = CalibrationSpec.from_config(cfg, geometry=geo)
    if spec.adc2_n_bits > 12:
        raise ValueError("UNSUPPORTED ADC2 decisions in the bound 16-tick engine")
    cw = _integer(
        _required(binding, "control_word_bits", "sample context"), "control_word_bits", 1, 64
    )
    sw = _integer(_required(binding, "state_bits", "sample context"), "state_bits", 1, 64)
    acquisition = _required(binding, "acquisition_control", "two actual acquisition nodes")
    # 量化器和电容阵列是两个节点。显式连接声明约定两个端口各自加到哪里。
    # 端口使用有符号定点值，路由方向和单位都属于绑定 ABI。
    expected_acquisition = {
        "kind": "sv_module",
        "sv_module_name": "source_acquisition_binding",
        "voltage_unit": "normalized_signed_q",
        "sadc_input_shift": "add_to_sadc_sampled_input",
        "rdac_input_shift": "add_to_all_rdac_sampled_units",
    }
    if not isinstance(acquisition, dict) or any(
        acquisition.get(name) != value for name, value in expected_acquisition.items()
    ):
        raise ValueError(
            "explicit source_acquisition_binding and both additive node meanings required"
        )
    _known_keys(
        acquisition,
        {*expected_acquisition, "connection_contract", "sv_path", "sha256"},
        "acquisition_control",
    )
    if (
        not isinstance(acquisition.get("connection_contract"), str)
        or not acquisition["connection_contract"].strip()
    ):
        raise ValueError("acquisition_control.connection_contract must describe supplied nodes")
    # 组件折叠只接受明确声明的无扰动能力，回读端核对同一约定。
    dither = _required(binding, "dither_contract", "source dither capability")
    if (
        not isinstance(dither, dict)
        or not isinstance(dither.get("provenance"), str)
        or not dither["provenance"].strip()
    ):
        raise ValueError("explicit dither capability kind and provenance required")
    # 普通数字组件使用明确的双节点接口，不从旧夹具推断电压或开关几何。
    # Python 的扰动研究是独立入口。导出时收到未知契约就停止，不回退到名义绑定。
    if dither.get("kind") != "none":
        raise ValueError("UNSUPPORTED source dither capability; no range/4x/scalar fallback")
    _known_keys(dither, {"kind", "provenance"}, "dither_contract")
    if cfg.dither_enable:
        raise ValueError("UNSUPPORTED source lower/upper dither without a bound two-node profile")
    # 样本编号按固定宽度交换。解码端使用这一宽度判断自然回绕和反向跳号。
    if binding.get("sample_id_bits") != 32:
        raise ValueError("explicit sample_id_bits=32 engineering ABI required")
    timing = _required(binding, "timing", "macro-control schedule")
    if not isinstance(timing, dict):
        raise ValueError("binding.timing must be an object")
    _known_keys(
        timing,
        {
            "phases",
            "residue_capture_phase",
            "reference_on_phase",
            "fine_decision_deadline_phase",
            "tick_period_ns",
        },
        "timing",
    )
    # 阶段索引与帧长度成套使用，让捕获、参考切换、末级判决有固定先后。
    # 物理采样率由完整帧决定。帧含若干个以 ns 表示的逻辑节拍。
    phases = {
        "phases": 16,
        "residue_capture_phase": 9,
        "reference_on_phase": 10,
        "fine_decision_deadline_phase": 14,
    }
    if any(type(timing.get(k)) is not int or timing[k] != v for k, v in phases.items()):
        raise ValueError("UNSUPPORTED schedule; every supported phase must be explicit")
    tick = _required(timing, "tick_period_ns", "digital clock budget")
    if (
        isinstance(tick, bool)
        or not isinstance(tick, int | float)
        or not math.isfinite(tick)
        or tick <= 0
    ):
        raise ValueError("timing.tick_period_ns must be finite and positive [ns]")
    if not math.isclose(tick * timing["phases"] * cfg.fs, 1e9, rel_tol=1e-10):
        raise ValueError("explicit tick/frame period disagrees with Config.fs")
    # 片组随机顺序负责资源所有权，独立于在已选片组内轮换等权电容的 DEM。
    scheduler = _required(binding, "scheduler", "causal slice ownership")
    if not isinstance(scheduler, dict) or scheduler.get("kind") != "causal_pool_scan":
        raise ValueError("explicit engineering scheduler.kind=causal_pool_scan required")
    _known_keys(scheduler, {"kind", "shuffle_enable", "seed"}, "scheduler")
    if type(scheduler.get("shuffle_enable")) is not bool:
        raise ValueError("scheduler.shuffle_enable must be explicit and separate from DEM")
    seed = _integer(_required(scheduler, "seed", "slice allocator"), "scheduler.seed", 1, 2**32 - 1)
    # 检查除法器的计算步和进入、退出延迟都能放入一帧。
    stages = _integer(
        _required(binding, "divider_stages", "integer latency"), "divider_stages", 1, 63
    )
    if math.ceil((fmt.accumulator_bits - fmt.voltage_fraction_bits - 1) / stages) + 2 > 16:
        raise ValueError("divider latency exceeds the explicit frame budget")
    # 逐单元证明依赖每个物理单元采相同输入，且正、负参考幅值对称。
    # 对未参与本笔的片也核对连接行，以保证分配器换片后仍可用同一公式。
    fold = _required(binding, "reconstruction_fold", "weight-independent per-unit proof")
    if not isinstance(fold, dict) or fold.get("kind") != "all_signal_symmetric_common_v1":
        raise ValueError("UNSUPPORTED connection fold")
    _known_keys(
        fold,
        {
            "kind",
            "sampling_main",
            "sampling_sub",
            "reference_low_v",
            "reference_high_v",
            "common_input_unit",
            "term_quantizer",
        },
        "fold",
    )
    # 主、子矩阵的物理列数可不同，二者均按完整物理片组逐行声明。
    for name, units in (("sampling_main", geo.n_main), ("sampling_sub", geo.n_sub)):
        rows = _required(fold, name, "actual acquisition connections")
        if (
            not isinstance(rows, list)
            or len(rows) != geo.n_slices
            or any(
                not isinstance(row, list)
                or len(row) != units
                or any(type(item) is not int or item != 0 for item in row)
                for row in rows
            )
        ):
            raise ValueError(f"UNSUPPORTED {name}: every physical unit must explicitly be SIGNAL=0")
    if fold.get("reference_low_v") != -spec.v_fs or fold.get("reference_high_v") != spec.v_fs:
        raise ValueError("this fold requires explicit exact -Vfs/+Vfs references [V]")
    # 在单元层按最近偶数舍入，才能与跟踪每个单元的连接记录进行逐项比对。
    if (
        fold.get("common_input_unit") != "normalized_signed_q"
        or fold.get("term_quantizer") != "ties_even_per_unit"
    ):
        raise ValueError("explicit normalized_signed_q and ties_even_per_unit contracts required")
# =============================================================================
# 3. 绑定的实现身份
# =============================================================================
    source = _required(binding, "source_control", "actual physical mask implementation")
    if not isinstance(source, dict) or source.get("kind") != "sv_module":
        raise ValueError("UNSUPPORTED source-control kind; supply a synthesizable SV module")
    _known_keys(
        source,
        {"kind", "sv_module_name", "sv_path", "sha256", "connection_contract"},
        "source_control",
    )
    if source.get("sv_module_name") != "source_control_binding":
        raise ValueError("binding module must implement source_control_binding")
    module_name = _required(source, "sv_path", "source-control implementation")
    if not isinstance(module_name, str) or not module_name:
        raise ValueError("source_control.sv_path must be a nonempty path string")
    # 相对路径以绑定 JSON 为根，使同一目录可被移入临时目录或干净克隆。
    module_path = Path(module_name)
    if not module_path.is_absolute():
        module_path = binding_path.resolve().parent / module_path
    if not module_path.is_file() or module_path.stat().st_size >= 300 * 1024 * 1024:
        raise ValueError("binding SV must be readable and smaller than 300 MB")
    # 哈希针对实际字节，拷贝进导出包时也使用此处已经校验的内容。
    # 忽略注释后确认模块声明，可在调用编译器之前捕获误绑。
    module_data = module_path.read_bytes()
    if source.get("sha256") != _digest(module_data):
        raise ValueError("source-control module digest mismatch")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", " ", module_data.decode("utf-8"), flags=re.S)
    if re.findall(r"(?m)^\s*module\s+(\w+)", code) != ["source_control_binding"]:
        raise ValueError("source control SV must declare exactly its same-named module")
    # 独立文件保存采样节点的实现身份；它与开关掩码编排各自核验。
    acquisition_name = _required(acquisition, "sv_path", "source acquisition implementation")
    if not isinstance(acquisition_name, str) or not acquisition_name:
        raise ValueError("acquisition_control.sv_path must be a nonempty path")
    acquisition_path = Path(acquisition_name)
    if not acquisition_path.is_absolute():
        acquisition_path = binding_path.resolve().parent / acquisition_path
    if not acquisition_path.is_file() or acquisition_path.stat().st_size >= 300 * 1024 * 1024:
        raise ValueError("source acquisition SV must be readable and smaller than300MB")
    acquisition_data = acquisition_path.read_bytes()
    if acquisition.get("sha256") != _digest(acquisition_data):
        raise ValueError("source acquisition module digest mismatch")
    # 文件中的注释示例不计为模块声明；包内只能有一个采样端模块定义。
    acquisition_code = re.sub(
        r"/\*.*?\*/|//[^\n]*", " ", acquisition_data.decode("utf-8"), flags=re.S
    )
    if re.findall(r"(?m)^\s*module\s+(\w+)", acquisition_code) != ["source_acquisition_binding"]:
        raise ValueError("source acquisition SV must declare exactly its same-named module")
    if not source.get("connection_contract") or not binding.get("provenance"):
        raise ValueError(
            "connection_contract and provenance required; golden output is not a binding"
        )
    # 输入原件随输出包保留，便于消费者复查生成模式和各文件摘要。
    return {
        "config": cfg,
        "format": fmt,
        "spec": spec,
        "geometry": geo,
        "binding": binding,
        "word_bits": cw,
        "state_bits": sw,
        "seed": seed,
        "stages": stages,
        "module_data": module_data,
        "acquisition_data": acquisition_data,
        "input_bytes": {
            "config.json": config_path.read_bytes(),
            "format.json": format_path.read_bytes(),
            "binding.json": binding_path.read_bytes(),
        },
        "input_sha256": {
            "config_json": _digest(config_path.read_bytes()),
            "format_json": _digest(format_path.read_bytes()),
            "binding_json": _digest(binding_path.read_bytes()),
            "source_sv": _digest(module_data),
            "acquisition_sv": _digest(acquisition_data),
        },
    }


# =============================================================================
# 4. 生成编译参数、包布局和索引
# =============================================================================
# 同一次构造提供 Verilog 参数和 Python 回读模式，使不同消费者共享字段顺序。
def build(config_path: Path, format_path: Path, binding_path: Path) -> dict[str, str | bytes]:
    """返回完整且确定的输入包内容，不推断物理器件配置。"""
    data = load_contract(config_path, format_path, binding_path)
    cfg, fmt, spec, geo, binding = (
        data[k] for k in ("config", "format", "spec", "geometry", "binding")
    )
    # 控制模块要同时看到片组几何和包字段宽度，避免参数表与标签拆分独立漂移。
    params = {
        "W_FRAC": fmt.weight_fraction_bits,
        "W_BITS": fmt.coefficient_bits,
        "V_FRAC": fmt.voltage_fraction_bits,
        "V_BITS": fmt.voltage_bits,
        "ACC_BITS": fmt.accumulator_bits,
        "OUT_BITS": fmt.output_bits,
        "N_SLICES": geo.n_slices,
        "N_ACTIVE": geo.n_active,
        "N_UNIT_MAIN": geo.n_main,
        "N_UNIT_SUB": geo.n_sub,
        "N_UNIT_TOTAL": geo.n_main + geo.n_sub,
        "B1": cfg.b1,
        "ADC2_BITS": spec.adc2_n_bits,
        "PHASES": binding["timing"]["phases"],
        "CONTROL_WORD_BITS": data["word_bits"],
        "STATE_BITS": data["state_bits"],
        "RECON_UNROLL": data["stages"],
        "POOL_SHUFFLE_SEED": data["seed"],
        "POOL_SHUFFLE_ENABLE": int(binding["scheduler"]["shuffle_enable"]),
        "SOURCE_DEM_REQUEST": int(cfg.dem_enable),
        "SOURCE_DITHER_REQUEST": int(cfg.dither_enable),
    }
    # 报文总体按高位到低位列出。同一个掩码字段内部则从 slot 0 的最低位起。
    # known_common_q 有独立位宽，回读端将其解释为有符号定点数。
    fields = [
        ("sample_id", 32),
        ("coarse", 9),
        ("state", data["state_bits"]),
        ("control_word", data["word_bits"]),
        ("adc2_code", spec.adc2_n_bits),
        ("known_common_q", fmt.voltage_bits),
        ("slice_ids", geo.n_active * 5),
        ("main_masks", geo.n_active * geo.n_main),
        ("sub_masks", geo.n_active * geo.n_sub),
        ("analog_bad", 1),
    ]
    params["CAL_OBS_BITS"] = sum(width for _name, width in fields)
    lines = [
        "// Explicit Config/format/binding inputs. No device or physical-mapper defaults.",
        "// Source macro/timing correspondence is not certified by this header.",
        "`ifndef RTL_PARAMS_VH",
        "`define RTL_PARAMS_VH",
    ]
    # 显式无符号 32 位常量，避免源语言默认整型的符号扩展影响移位。
    lines += [f"localparam int unsigned {name} = 32'd{value};" for name, value in params.items()]
    lines += ["`endif", ""]
    # 包布局复述固定格式、几何和身份；脱离绑定原件的解析不能用于校准。
    packet = {
        "schema_version": 2,
        "field_order_msb_to_lsb": [name for name, _width in fields],
        "field_widths": dict(fields),
        "packet_bits": params["CAL_OBS_BITS"],
        "geometry": asdict(geo),
        "fixed_point_format": asdict(fmt),
        "reconstruction_fold": binding["reconstruction_fold"],
        "source_binding_sha256": data["input_sha256"]["binding_json"],
        "payload_encoding": "hex_msb_first",
        "packed_index_order": "slot0_lsb_unit0_lsb",
        "input_sha256": data["input_sha256"],
        "connection_binding_required": True,
        "scope": binding["provenance"],
    }
    # 整数输入包的结果索引覆盖推导口径和原件身份，结果只代表数字适配边界。
    manifest = {
        "schema_version": 1,
        "status": "EXPLICIT_RTL_INPUTS_GENERATED",
        "scope": "DIGITAL_BINDING_NOT_ANALOG_OR_TIMING_SIGNOFF",
        "config": cfg.to_dict(),
        "format": asdict(fmt),
        "calibration_spec": asdict(spec),
        "parameters": params,
        "input_sha256": data["input_sha256"],
        "fold": "a=1; term_q=known_common_q+(1-2*actual_mask)*2^V_FRAC per unit",
        "source_dither": binding["dither_contract"],
        "acquisition_control": binding["acquisition_control"],
        "binding_interface": "RTL_BINDING_ABI_2_COMMON_ACQUISITION",
    }
    files: dict[str, str | bytes] = {
        "rtl_params.vh": "\n".join(lines),
        "source_control_binding.sv": data["module_data"],
        "source_acquisition_binding.sv": data["acquisition_data"],
        "packet_schema.json": _json_text(packet),
    }
    files.update(data["input_bytes"])
    # 用写入时的 UTF-8 编码计算哈希，消费者可直接对已生成的字节核验。
    manifest["artifact_sha256"] = {
        name: _digest(content if isinstance(content, bytes) else content.encode("utf-8"))
        for name, content in files.items()
    }
    # 先计算其余文件的摘要，再加入索引文件，避免索引对自身摘要形成循环。
    files["rtl_input_manifest.json"] = _json_text(manifest)
    return files


# =============================================================================
# 5. 文件写入或字节核验
# =============================================================================
# 只读检查比较每份输入制品。生成入口要求新目录，避免覆盖他人的运行。
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--format", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = build(args.config, args.format, args.binding)
    # 浮点参数和源码同时受字节约束，检查重跑时生成的输入与磁盘原件一致。
    if args.check:
        for name, content in files.items():
            expected = content if isinstance(content, bytes) else content.encode("utf-8")
            target = args.out / name
            if not target.is_file() or target.read_bytes() != expected:
                raise SystemExit(f"explicit RTL input drift: {target}")
    else:
        # 写入中途失败时整批撤销，避免下游误把半份输入包当成完整契约。
        args.out.mkdir(parents=True, exist_ok=False)
        try:
            for name, content in files.items():
                (args.out / name).write_bytes(
                    content if isinstance(content, bytes) else content.encode("utf-8")
                )
        except OSError:
            shutil.rmtree(args.out)  # 只清理本次调用新建的目录。
            raise
    manifest = json.loads(files["rtl_input_manifest.json"])
    print(f"EXPLICIT_RTL_INPUTS_COMPLETE files=8 source_dither={manifest['source_dither']['kind']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
