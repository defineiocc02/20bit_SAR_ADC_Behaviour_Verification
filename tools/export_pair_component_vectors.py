#!/usr/bin/env python3
"""从显式数字事件导出配对组件向量，不运行 ADC 或生成模拟真值。

输入为 pair-component-input-v1 JSON。binding 给出组件的 20/32/32/5 位接口、
偏移二进制码中心及向下取整规则；expected 声明非零记录、案例和有效输出数。
records 逐行提供 case_id、cycle 及全部 10 个输入；每个案例从 cycle=0 的显式复位开始。

标量参考独立于 SV DUT，只核对整数半差、样本编号、极性、自归零代号及清除／故障
规则。输入是合成数字夹具，不形成 CBIAS、H/K、噪声或论文性能证据。
输出 25 列供共享测试台读取，不生成物理配置、扰动模式、随机误差或默认模拟参数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

INPUTS = (
    "rst_n",
    "epoch_clear",
    "in_valid",
    "in_code",
    "in_sample_id",
    "in_chop_neg",
    "in_az_pair_id",
    "in_az_valid",
    "in_flags",
    "in_analog_bad",
)
OUTPUTS = (
    "out_valid",
    "out_code",
    "out_sample_id",
    "out_first_sample_id",
    "out_az_pair_id",
    "out_flags",
    "out_analog_bad",
    "pair_difference",
    "pair_difference_valid",
    "fault",
    "fault_pulse",
    "pair_pending",
    "fault_code",
)
COLUMNS = ("case_id", "cycle", *INPUTS, *OUTPUTS)
SUPPORTED_BINDING = {
    "code_bits": 20,
    "sample_id_bits": 32,
    "az_id_bits": 32,
    "flags_bits": 5,
    "code_format": "offset_binary_bin_center",
    "output_quantizer": "floor",
}
MAX_RECORDS = 100_000
MAX_INPUT_BYTES = 300_000_000


def _integer(value, name: str, limit: int) -> int:
    if type(value) is not int or not 0 <= value < limit:
        raise ValueError(f"{name} must be an integer in [0,{limit})")
    return value


def _keys(value, expected, name: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{name} requires exactly these fields: {', '.join(expected)}")
    return value


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"duplicate JSON field: {name}")
        result[name] = value
    return result


def validate_document(document: dict) -> dict:
    """校验完整数字夹具，不补复位、位宽、记录、状态或期望计数。"""
    _keys(document, ("schema", "binding", "expected", "records"), "input document")
    if document["schema"] != "pair-component-input-v1":
        raise ValueError("unsupported pair-component input schema")
    binding = _keys(document["binding"], SUPPORTED_BINDING, "binding")
    for name in ("code_bits", "sample_id_bits", "az_id_bits", "flags_bits"):
        if type(binding[name]) is not int:
            raise ValueError(f"binding.{name} must be an explicit integer")
    if binding != SUPPORTED_BINDING:
        raise ValueError("binding must explicitly match the supported 20/32/32/5 pair interface")
    expected = _keys(
        document["expected"], ("record_count", "case_count", "valid_output_count"), "expected"
    )
    for name, value in expected.items():
        _integer(value, "expected." + name, MAX_RECORDS + 1)
        if value == 0:
            raise ValueError(f"expected.{name} must be nonzero")
    records = document["records"]
    if not isinstance(records, list) or len(records) != expected["record_count"]:
        raise ValueError("records must match expected.record_count exactly")
    if expected["case_count"] > len(records) or expected["valid_output_count"] > len(records) // 2:
        raise ValueError("expected case/output counts cannot exceed the supplied record capacity")
    widths = {
        "in_code": binding["code_bits"],
        "in_sample_id": binding["sample_id_bits"],
        "in_az_pair_id": binding["az_id_bits"],
        "in_flags": binding["flags_bits"],
    }
    normalized = []
    previous_case, previous_cycle = -1, -1
    for index, row in enumerate(records):
        _keys(row, ("case_id", "cycle", "inputs"), f"record {index}")
        case_id = _integer(row["case_id"], f"record {index}.case_id", expected["case_count"])
        cycle = _integer(row["cycle"], f"record {index}.cycle", MAX_RECORDS)
        inputs = _keys(row["inputs"], INPUTS, f"record {index}.inputs")
        inputs = {
            name: _integer(inputs[name], f"record {index}.{name}", 1 << widths.get(name, 1))
            for name in INPUTS
        }
        if case_id != previous_case:
            if case_id != previous_case + 1 or cycle != 0 or inputs["rst_n"] != 0:
                raise ValueError(
                    "cases must be ordered from 0 and start cycle0 with explicit rst_n=0"
                )
        elif cycle != previous_cycle + 1:
            raise ValueError(f"record {index}: case cycles must be contiguous")
        normalized.append({"case_id": case_id, "cycle": cycle, "inputs": inputs})
        previous_case, previous_cycle = case_id, cycle
    if previous_case + 1 != expected["case_count"]:
        raise ValueError("records do not contain every declared case")
    return {
        "schema": document["schema"],
        "binding": dict(binding),
        "expected": dict(expected),
        "records": normalized,
    }


def load_input(path: Path) -> tuple[dict, str]:
    """一次读取显式输入字节，校验结构并返回同一字节序列的 SHA256。"""
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("input must be a regular JSON file no larger than 300 MB")
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    return validate_document(document), hashlib.sha256(raw).hexdigest()


class PairReference:
    """显式配对数字接口的标量参考，不读取 DUT 输出或预填的期望结果。"""

    def __init__(self, binding: dict):
        """使用已校验的接口尺寸创建状态；每个实际案例仍须显式提供复位事件。"""
        self.count = 1 << binding["code_bits"]
        self.sample_mask = (1 << binding["sample_id_bits"]) - 1
        self.az_mask = (1 << binding["az_id_bits"]) - 1
        self._reset()

    def _reset(self):
        self.fault = 0
        self.reason = 0
        self.pending = None
        self.last_id = None
        self.last_pair = None
        self.held = (0, 0, 0, 0, 0)

    def step(self, event: dict) -> dict:
        """返回本上升沿后的输出；复位或清除优先，故障保持至下一次复位或清除。"""
        if not event["rst_n"] or event["epoch_clear"]:
            self._reset()
            return self.output(0, 0)
        valid, pulse = 0, 0
        if event["in_valid"] and not self.fault:
            reason = self._reject(event)
            if reason:
                self.fault, self.reason = 1, reason
                self.pending = None
                pulse = 1
            elif self.pending is None:
                self.pending = (event["in_code"], event["in_sample_id"], event["in_az_pair_id"])
                self.last_id = event["in_sample_id"]
            else:
                code, first_id, generation = self.pending
                difference = code - event["in_code"]
                self.held = (
                    (self.count + difference) // 2,
                    event["in_sample_id"],
                    first_id,
                    generation,
                    difference,
                )
                self.last_id = event["in_sample_id"]
                self.last_pair = generation
                self.pending = None
                valid = 1
        return self.output(valid, pulse)

    def _reject(self, event: dict) -> int:
        if event["in_flags"]:
            return 1
        if event["in_analog_bad"]:
            return 2
        if not event["in_az_valid"]:
            return 3
        if self.last_id is not None and event["in_sample_id"] != (
            (self.last_id + 1) & self.sample_mask
        ):
            return 4
        if event["in_chop_neg"] != int(self.pending is not None):
            return 5
        if self.pending is not None:
            if event["in_az_pair_id"] != self.pending[2]:
                return 6
        elif self.last_pair is not None and event["in_az_pair_id"] != (
            (self.last_pair + 1) & self.az_mask
        ):
            return 6
        return 0

    def output(self, valid: int, pulse: int) -> dict:
        """分别输出保持数据、待配对状态和粘滞故障，不按 valid 删除记录。"""
        code, last_id, first_id, generation, difference = self.held
        values = (
            valid,
            code,
            last_id,
            first_id,
            generation,
            0,
            0,
            difference,
            valid,
            self.fault,
            pulse,
            int(self.pending is not None),
            self.reason,
        )
        return dict(zip(OUTPUTS, values, strict=True))


def evaluate_records(document: dict) -> tuple[dict, list[dict]]:
    """从给定输入逐行复算期望结果，并核对显式声明的非零输出数。"""
    data = validate_document(document)
    reference = PairReference(data["binding"])
    rows = [dict(row, expected=reference.step(row["inputs"])) for row in data["records"]]
    actual_outputs = sum(row["expected"]["out_valid"] for row in rows)
    if actual_outputs != data["expected"]["valid_output_count"]:
        raise ValueError("reference valid outputs do not match expected.valid_output_count")
    return data, rows


def build_vectors(document: dict, input_sha256: str) -> tuple[str, dict]:
    """导出完整 25 列及组件来源记录，不生成 ADC、模拟误差或随机事件。"""
    data, rows = evaluate_records(document)
    lines = ["# " + " ".join(COLUMNS)]
    for row in rows:
        values = (
            row["case_id"],
            row["cycle"],
            *(row["inputs"][name] for name in INPUTS),
            *(row["expected"][name] for name in OUTPUTS),
        )
        lines.append(" ".join(map(str, values)))
    text = "\n".join(lines) + "\n"
    manifest = {
        "schema": "pair-component-vectors-v1",
        "evidence_scope": "COMPONENT_SYNTHETIC_DIGITAL",
        "scope": "Pair arithmetic/ID/polarity/AZ generation/clear/fault only; no analog or literature-performance evidence",
        "binding": data["binding"],
        **data["expected"],
        "columns": list(COLUMNS),
        "input_record_sha256": input_sha256,
        "reference_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "vectors_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "analog_model": "NONE",
    }
    return text, manifest


def write_export(input_path: Path, output_path: Path) -> dict:
    """仅向新目录写向量和清单，拒绝已有路径及符号链接。"""
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("--out must be a new directory, not an existing path or symlink")
    data, input_sha = load_input(input_path)
    text, manifest = build_vectors(data, input_sha)
    output_path.mkdir(parents=True, exist_ok=False)
    for name, payload in (
        ("pair_component_vectors.txt", text),
        ("pair_component_manifest.json", json.dumps(manifest, indent=2) + "\n"),
    ):
        with (output_path / name).open("xb") as output:
            output.write(payload.encode("utf-8"))
    return manifest


def main() -> int:
    """从显式 JSON 事件构建数字组件向量；缺项或覆盖请求均以非零状态退出。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="Explicit pair-component input JSON"
    )
    parser.add_argument("--out", required=True, type=Path, help="New output directory")
    args = parser.parse_args()
    try:
        manifest = write_export(args.input, args.out)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                name: manifest[name]
                for name in (
                    "evidence_scope",
                    "record_count",
                    "case_count",
                    "valid_output_count",
                    "vectors_sha256",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
