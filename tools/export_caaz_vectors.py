#!/usr/bin/env python3
"""在新目录重新生成 CAAZ Python／RTL 配对合同向量。

首例运行完整样本域 ADC，产生 128 个原始码及 64 个配对码。独立标量状态参考
规定 15 个案例的整数算术、相位／自归零标签、编号连续及粘滞故障／复位行为，
不读取 RTL 仿真或已有夹具来制造期望值。

Wang 等 VLSI 2025 C32.1 图 6 提供偶数减奇数的配对机制；原始 40／配对
20 MS/s、RA 输出噪声幅度、向下取整及标签／故障协议为工程选择，不是论文
原型性能预测。随机种子固定；保留历史夹具名称，当前源字节与生成器另记摘要。
--out 指定尚不存在的输出目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import adi_model
from adi_model import Config, dc_input
from adi_model.chopping_az import ChoppingAZParameters, pair_readout, run_caaz_pipeline
from adi_model.fixed_point import CodeStream

M = 1 << 20
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


@dataclass
class _PairOracle:
    """独立于 SV 仿真与向量生成器的行为合同状态。"""

    fault: int = 0
    fault_code: int = 0
    pending: tuple | None = None
    last_id: int | None = None
    last_pair: int | None = None
    held: tuple = (0, 0, 0, 0, 0)

    def cycle(self, event):
        """返回本上升沿输入事件之后的全部寄存输出。"""
        if not event["rst_n"] or event["epoch_clear"]:
            self.fault = 0
            self.fault_code = 0
            self.pending = None
            self.last_id = None
            self.last_pair = None
            self.held = (0, 0, 0, 0, 0)
            return self.output(0, 0)
        valid = 0
        pulse = 0
        if event["in_valid"] and not self.fault:
            error = 0
            if event["in_flags"]:
                error = 1
            elif event["in_analog_bad"]:
                error = 2
            elif not event["in_az_valid"]:
                error = 3
            elif self.last_id is not None and event["in_sample_id"] != (self.last_id + 1) % 2**32:
                error = 4
            elif event["in_chop_neg"] != int(self.pending is not None):
                error = 5
            elif (
                self.pending is not None
                and event["in_az_pair_id"] != self.pending[2]
                or (
                    self.pending is None
                    and self.last_pair is not None
                    and event["in_az_pair_id"] != (self.last_pair + 1) % 2**32
                )
            ):
                error = 6
            if error:
                self.fault = 1
                self.fault_code = error
                self.pending = None
                pulse = 1
            elif self.pending is None:
                self.pending = (event["in_code"], event["in_sample_id"], event["in_az_pair_id"])
                self.last_id = event["in_sample_id"]
            else:
                even, first, generation = self.pending
                difference = int(even) - int(event["in_code"])
                word = (M + difference) // 2
                self.held = (word, event["in_sample_id"], first, generation, difference)
                self.last_id = event["in_sample_id"]
                self.last_pair = generation
                self.pending = None
                valid = 1
        return self.output(valid, pulse)

    def output(self, valid, pulse):
        """描述有效位、保持数据及粘滞故障，不推进状态。"""
        word, last, first, generation, difference = self.held
        return dict(
            zip(
                OUTPUTS,
                (
                    valid,
                    word,
                    last,
                    first,
                    generation,
                    0,
                    0,
                    difference,
                    valid,
                    self.fault,
                    pulse,
                    int(self.pending is not None),
                    self.fault_code,
                ),
                strict=True,
            )
        )


def event(**kw):
    value = dict(zip(INPUTS, (1, 0, 1, 0, 0, 0, 0, 1, 0, 0), strict=True))
    value.update(kw)
    return value


EXPECTED_FIXTURE_SHA256 = "549aea6310a6db7b5220098870382a60e93437b29ba39255d746cfda068a51a4"
HISTORICAL_SOURCE_SNAPSHOT = "86106fc+local_CAAZ"


def build_vectors() -> tuple[str, dict]:
    """在内存构建确定的夹具文本及来源记录。

    模型与标量参考比较全部 64 对仿真物理样本和 136 对无符号边界／随机案例。
    标准文本摘要变化即抛 ValueError；修改入库夹具前需审阅该变化。
    """
    cases = []

    def add_case(name, events):
        cases.append((name, [event(rst_n=0, in_valid=0), *events]))

    def pair_events(even, odd, first, generation):
        return [
            event(in_code=int(even), in_sample_id=first, in_az_pair_id=generation),
            event(
                in_code=int(odd),
                in_sample_id=(first + 1) % 2**32,
                in_chop_neg=1,
                in_az_pair_id=generation,
            ),
        ]

    # A real full-model capture: explicit source ownership comes from the generated
    # AZ memory, not from inferring it from a returned word or guessing valid flags.
    cfg = Config.paper_literal(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        seed=9001,
    )
    raw = run_caaz_pipeline(
        cfg,
        dc_input(0.7),
        128,
        az=ChoppingAZParameters(mode="chopping_assisted", az_noise_rms_v=0.002),
    )
    if raw.invalid.any():
        raise ValueError("reference model capture overflowed; review source drift")
    real = []
    for index, code in enumerate(raw.raw_words.code):
        real.append(
            event(
                in_code=int(code),
                in_sample_id=int(raw.raw.sample_id[index]),
                in_chop_neg=int(raw.chop_sign[index] < 0),
                in_az_pair_id=index // 2,
                in_az_valid=1,
                in_flags=0,
                in_analog_bad=int(raw.raw_words.analog_overflow[index]),
            )
        )
    add_case("physical_python_capture_dc_0p7V_seed9001", real)

    # Full integer extrema plus random pairs; check the public Python arithmetic
    # as an independent boundary over the same unsigned external-word interface.
    rng = np.random.default_rng(82133)
    pairs = [
        (0, 0),
        (M - 1, M - 1),
        (0, M - 1),
        (M - 1, 0),
        (0, 1),
        (1, 0),
        (M // 2, M // 2 + 1),
        (M // 2 + 1, M // 2),
    ]
    pairs += [tuple(map(int, v)) for v in rng.integers(0, M, size=(128, 2))]
    external = np.array(pairs, dtype=np.uint32).reshape(-1)
    zero = np.zeros(len(external), bool)
    words = CodeStream(external, zero, zero, zero, 3.0, 20, 80)
    expected_words = pair_readout(words, difference=True)[0]
    if not all(
        int(w) == (M + int(e) - int(o)) // 2
        for (e, o), w in zip(pairs, expected_words, strict=True)
    ):
        raise ValueError("public Python arithmetic differs from the independent integer oracle")
    normal = []
    for index, (even, odd) in enumerate(pairs):
        normal += pair_events(even, odd, 2 * index, 100 + index)
    add_case("unsigned_integer_boundaries_and_128_random_pairs", normal)

    add_case(
        "valid_idle_gaps",
        [
            *pair_events(10, 20, 2, 8)[:1],
            event(in_valid=0, in_sample_id=987),
            event(in_valid=0),
            *pair_events(10, 20, 2, 8)[1:],
        ],
    )
    add_case(
        "uint32_id_and_az_generation_wrap",
        pair_events(100, 102, 2**32 - 2, 2**32 - 1) + pair_events(900, 700, 0, 0),
    )
    # Each fault is sticky: the later apparently valid pair must not restart it.
    post = pair_events(10, 5, 20, 10)
    add_case(
        "missing_sample_id_then_sticky_fault",
        [
            event(in_code=1, in_sample_id=2, in_az_pair_id=0),
            event(in_code=2, in_sample_id=4, in_chop_neg=1, in_az_pair_id=0),
            *post,
        ],
    )
    add_case("wrong_first_phase_then_sticky_fault", [event(in_sample_id=2, in_chop_neg=1), *post])
    add_case("wrong_second_phase", [event(in_sample_id=2), event(in_sample_id=3), *post])
    add_case(
        "wrong_pair_az_generation",
        [
            event(in_sample_id=2, in_az_pair_id=8),
            event(in_sample_id=3, in_chop_neg=1, in_az_pair_id=9),
            *post,
        ],
    )
    add_case(
        "next_pair_generation_gap",
        [*pair_events(1, 2, 2, 8), event(in_sample_id=4, in_az_pair_id=10), *post],
    )
    for name, field, value in [
        ("raw_flags", "in_flags", 16),
        ("analog_bad", "in_analog_bad", 1),
        ("az_invalid", "in_az_valid", 0),
    ]:
        bad = event(in_code=11, in_sample_id=3, in_chop_neg=1, in_az_pair_id=4, **{field: value})
        add_case(name, [event(in_code=13, in_sample_id=2, in_az_pair_id=4), bad, *post])
    add_case(
        "epoch_clear_dominates_pending_odd_then_restarts",
        [
            event(in_code=77, in_sample_id=2, in_az_pair_id=3),
            event(epoch_clear=1, in_code=4, in_sample_id=3, in_chop_neg=1, in_az_pair_id=3),
            *pair_events(44, 21, 200, 99),
        ],
    )
    add_case(
        "epoch_clear_does_not_pair_stray_odd",
        [
            event(in_sample_id=2),
            event(epoch_clear=1, in_valid=0),
            event(in_sample_id=3, in_chop_neg=1),
            *post,
        ],
    )
    add_case(
        "clear_releases_sticky_fault",
        [
            event(in_sample_id=2, in_flags=1),
            event(epoch_clear=1, in_valid=0),
            *pair_events(67, 99, 8, 4),
        ],
    )

    rows = []
    summary = []
    for case_id, (name, events) in enumerate(cases):
        oracle = _PairOracle()
        for cycle, inputs in enumerate(events):
            expected = oracle.cycle(inputs)
            rows.append(
                {
                    "case_id": case_id,
                    "case_name": name,
                    "cycle": cycle,
                    "inputs": inputs,
                    "expected": expected,
                }
            )
        summary.append({"case_id": case_id, "name": name, "cycles": len(events)})
    first = [r for r in rows if r["case_id"] == 0 and r["expected"]["out_valid"]]
    if [r["expected"]["out_code"] for r in first] != raw.output_code.tolist():
        raise ValueError("full-model pair words differ from the independent event oracle")

    columns = ("case_id", "cycle", *INPUTS, *OUTPUTS)
    lines = ["# " + " ".join(columns)]
    for row in rows:
        lines.append(
            " ".join(str(row[k]) for k in columns[:2])
            + " "
            + " ".join(str(row["inputs"][k]) for k in INPUTS)
            + " "
            + " ".join(str(row["expected"][k]) for k in OUTPUTS)
        )
    fixture_text = "\n".join(lines) + "\n"
    fixture_sha = hashlib.sha256(fixture_text.encode("utf-8")).hexdigest()
    if fixture_sha != EXPECTED_FIXTURE_SHA256:
        raise ValueError(
            f"CAAZ contract fixture drift: expected {EXPECTED_FIXTURE_SHA256}, got {fixture_sha}"
        )
    package_dir = Path(adi_model.__file__).resolve().parent
    source_sha = {
        "src/adi_model/" + path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package_dir.glob("*.py"))
    }
    source_sha["tools/export_caaz_vectors.py"] = hashlib.sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    manifest = {
        "schema": "caaz-pair-v1",
        "evidence": "Independent Python contract oracle plus full-model capture; not an analog CAAZ silicon claim",
        "source": "C32.1 Fig6 even-minus-odd; IDs, fault/epoch and integer-floor are project interface choices",
        "source_commit": HISTORICAL_SOURCE_SNAPSHOT,
        "source_commit_note": "Historical fixture definition, not a claim about the current Git HEAD or cleanliness; current bytes are listed in source_sha256.",
        "raw_rate_hz": cfg.fs,
        "pair_rate_hz": cfg.fs / 2,
        "rate_grade": "ENGINEERING_CHOICE",
        "input_columns": list(INPUTS),
        "expected_columns": list(OUTPUTS),
        "cases": summary,
        "cycle_count": len(rows),
        "valid_output_count": sum(r["expected"]["out_valid"] for r in rows),
        "fixture_sha256": fixture_sha,
        "raw_model_pair_words": len(first),
        "raw_model_integer_equality": True,
        "unsigned_arithmetic_pairs": len(pairs),
        "fixed_seeds": {"model_config": 9001, "integer_pairs": 82133},
        "model_capture_config": cfg.to_dict(),
        "model_input_v": 0.7,
        "az_output_rms_v": 0.002,
        "source_sha256": source_sha,
        "runtime": {"python": platform.python_version(), "numpy": np.__version__},
        "generation_command": "PYTHONPATH=src python tools/export_caaz_vectors.py --out NEW_DIRECTORY",
        "output_policy": "The entire --out directory must be new; existing paths are never overwritten.",
    }
    return fixture_text, manifest


def main(argv: list[str] | None = None) -> int:
    """导出确定性向量，开始工作前即拒绝已有输出目录。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="new output directory (must not exist)"
    )
    args = parser.parse_args(argv)
    if args.out.exists() or args.out.is_symlink():
        parser.error("--out must be a new directory; refusing to overwrite an existing path")
    fixture, manifest = build_vectors()
    # mkdir is also an exclusive guard against another exporter creating the
    # same destination between the earlier validation and this file boundary.
    try:
        args.out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("--out was created concurrently; no existing files were overwritten")
    for filename, content in (
        ("caaz_pair_contract.txt", fixture),
        (
            "caaz_pair_contract_manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        ),
    ):
        with (args.out / filename).open("xb") as output:
            output.write(content.encode("utf-8"))
    print(
        json.dumps(
            {
                "fixture_sha256": manifest["fixture_sha256"],
                "cases": len(manifest["cases"]),
                "cycles": manifest["cycle_count"],
                "valid_outputs": manifest["valid_output_count"],
                "out": str(args.out),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
