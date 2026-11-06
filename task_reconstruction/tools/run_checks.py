#!/usr/bin/env python3
"""用独立整数参考、归档八片数据和边界向量运行 Verilator。"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from itertools import zip_longest
from pathlib import Path
from typing import BinaryIO

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from task_reconstruction.fixed import (  # noqa: E402
    FULL_SCALE_CODE,
    MIN_GAIN,
    Coefficients,
    decode_midpoint,
    from_training,
    reconstruct,
)
from tools.run_open_rtl import (  # noqa: E402
    alias_runtime,
    checked_run,
    command_for,
    completion,
    digest,
    new_output,
    verilator_compiler_args,
    write_json,
)

RTL = "task_reconstruction/rtl/sar20_lms_recon.sv"
TB = "task_reconstruction/tb/sar20_lms_recon_tb.sv"
DATA = ROOT / "task2/evidence/eight_native"
ARCHIVE = tuple(DATA / f"sigma_{sigma}pct/data.npz" for sigma in (2, 3))
FIELDS = ("cycles", "accepted", "retired", "bad_retired", "stalls", "words", "midpoint")
EMPTY = (0,) * 8
ORDERED = tuple(range(8))
RESULT_LATENCY = 22
STIMULUS_BITS = 333
OBSERVATION_BITS = 59
POST_EDGE_BITS = 56


def _pack(fields: tuple[tuple[int, int], ...]) -> int:
    value = 0
    for field, width in fields:
        if not 0 <= field < (1 << width):
            raise ValueError(f"vector field {field} is out of range for {width} bits")
        value = (value << width) | field
    return value


def _slots(values: tuple[int, ...], width: int) -> int:
    return _pack(tuple((i, width) for i in reversed(values)))


class Trace:
    """保存手工规定的接口事件和独立整数结果到每沿向量。"""

    def __init__(self, stream: BinaryIO) -> None:
        """初始化期望事件、覆盖计数与输出流。"""
        self.stream = stream
        self.cycle = 0
        self.counts = dict.fromkeys(FIELDS, 0)
        self.configured = False
        self.error = False
        self.due: dict[int, tuple[int, int, bool]] = {}
        self.visible: tuple[int, int, bool] | None = None

    def tick(
        self,
        *,
        rst: bool = True,
        clear: bool = False,
        start: bool = False,
        cfg: int | None = None,
        sample: tuple | None = None,
        accepted: bool = False,
        ready: bool = True,
        start_accept: bool = False,
        word_accept: bool = False,
    ) -> None:
        """推进一个上升沿，打包握手和指定的输出事务。"""
        if not rst or clear:
            if not rst:
                self.configured = False
            self.error = False
            self.due.clear()
            self.visible = None
        if self.visible is not None:
            if ready:
                self.counts["retired"] += 1
                self.counts["bad_retired"] += self.visible[2]
                self.visible = None
            else:
                self.counts["stalls"] += 1
        if self.cycle in self.due:
            if self.visible is not None:
                raise ValueError("trace schedules a new result before prior output handshake")
            self.visible = self.due.pop(self.cycle)
        self.counts["words"] += word_accept
        self.counts["accepted"] += accepted
        sid, ids, main, conv, samp, adc, bad = sample or (0, EMPTY, EMPTY, EMPTY, EMPTY, 0, 0)
        out_id, code, out_bad = self.visible or (0, 0, False)
        row = _pack(
            (
                (rst, 1),
                (clear, 1),
                (start, 1),
                (cfg is not None, 1),
                (cfg or 0, 26),
                (sample is not None, 1),
                (sid, 32),
                (bad, 1),
                (_slots(ids, 5), 40),
                (_slots(main, 17), 136),
                (_slots(conv, 5), 40),
                (_slots(samp, 5), 40),
                (adc, 12),
                (ready, 1),
                (start_accept, 1),
                (word_accept, 1),
                (accepted, 1),
                (self.configured, 1),
                (self.error, 1),
                (self.visible is not None, 1),
                (out_id, 32),
                (code & (FULL_SCALE_CODE - 1), 20),
                (out_bad, 1),
            )
        )
        stimulus, expected = divmod(row, 1 << OBSERVATION_BITS)
        self.stream.write(f"{stimulus:x} {expected:x}\n".encode())
        self.cycle += 1
        self.counts["cycles"] += 1

    def idle(self, cycles: int, *, ready: bool = True) -> None:
        """插入指定数量的空沿，保持给定反压条件。"""
        for _ in range(cycles):
            self.tick(ready=ready)

    def load(self, coef: Coefficients) -> None:
        """生成完整顺序装载，标记最后一字后开放采样。"""
        self.configured = False
        self.error = False
        self.tick(start=True, start_accept=True)
        for i, word in enumerate(coef.words()):
            self.configured = i == 395
            self.tick(cfg=word, word_accept=True)

    def submit(
        self,
        coef: Coefficients,
        sample: tuple,
        *,
        ready: bool = True,
        output_cycle: int | None = None,
    ) -> tuple[int, bool]:
        """在当前沿提交一笔并安排独立算术的期望结果。"""
        result = reconstruct(coef, *sample[1:])
        if output_cycle is None:
            output_cycle = self.cycle + (12 if result[1] else RESULT_LATENCY)
        if output_cycle in self.due:
            raise ValueError("multiple expected outputs on one cycle")
        self.due[output_cycle] = (sample[0], *result)
        self.tick(sample=sample, accepted=True, ready=ready)
        return result


def _edge_cases(trace: Trace) -> None:
    max_w = (1 << 23) - 1
    weights = [[0] * 21 for _ in range(18)]
    weights[0][0], weights[0][1] = -655, -656
    weights[0][17] = -(1 << 23)
    weights[1][:16] = [max_w] * 16
    weights[1][16], weights[2][16] = -640, -639
    coef = Coefficients(tuple(tuple(x) for x in weights), (MIN_GAIN,) * 18)

    trace.tick(rst=False)
    trace.tick(start=True, start_accept=True)
    trace.error = True
    trace.tick(cfg=1 << 24, word_accept=True)  # 权重符号扩展不合法。
    trace.tick(cfg=0)
    trace.error = False
    trace.tick(start=True, start_accept=True)
    trace.tick(cfg=0, word_accept=True)
    trace.tick(clear=True, cfg=0)
    trace.tick(start=True, start_accept=True)
    for _ in range(21):
        trace.tick(cfg=0, word_accept=True)
    trace.error = True
    trace.tick(cfg=0, word_accept=True)  # 零增益不合法。
    trace.load(coef)

    def sample(sid: int, *, ids=ORDERED, main=EMPTY, conv=EMPTY, samp=EMPTY, bad=False) -> tuple:
        return sid, ids, main, conv, samp, 2048, bad

    minimum = sample(0xFFFF_FFFE, main=(1, *EMPTY[1:]))
    under = sample(0xFFFF_FFFF, main=(2, *EMPTY[1:]))
    maximum = sample(0, main=(0, 0x1FFFF, *EMPTY[2:]))
    over = sample(1, main=(0, 0x0FFFF, 0x10000, *EMPTY[3:]))
    signed_sub = sample(2, samp=(1, *EMPTY[1:]))
    checks = (
        (minimum, (-524288, False)),
        (under, (0, True)),
        (maximum, (524287, False)),
        (over, (0, True)),
        (signed_sub, (-458747, False)),
        (sample(3, ids=(18, *ORDERED[1:])), (0, True)),
        (sample(4, ids=(1, *ORDERED[1:])), (0, True)),
        (sample(5, conv=(16, *EMPTY[1:])), (0, True)),
        (sample(6, samp=(16, *EMPTY[1:])), (0, True)),
        (sample(7, bad=True), (0, True)),
    )
    for row, expected in checks:
        if trace.submit(coef, row) != expected:
            raise ValueError(f"directed expected result changed for sample {row[0]}")
        trace.idle(24)

    # 清空丢弃在途结果，但下一笔仍可用完整配置。
    trace.submit(coef, minimum)
    trace.idle(3)
    trace.tick(clear=True)
    trace.idle(24)

    # 前一结果反压至 E35，后一和保持等待，第三笔不能覆盖。
    start = trace.cycle
    trace.submit(coef, minimum, ready=False)
    trace.idle(15, ready=False)
    trace.submit(coef, maximum, ready=False, output_cycle=start + 45)
    trace.idle(15, ready=False)
    trace.tick(sample=signed_sub, ready=False)
    trace.idle(2, ready=False)
    trace.idle(12)

    # 尝试在间隔 12 的沿受理同笔请求，保持到间隔 13 握手。
    burst = [sample(0x100 + i, main=(1, *EMPTY[1:])) for i in range(16)]
    trace.submit(coef, burst[0])
    for row in burst[1:]:
        trace.idle(11)
        trace.tick(sample=row)
        trace.submit(coef, row)
    trace.idle(24)

    # 除法中的同步复位取消结果，重新装载前不开放采样。
    trace.submit(coef, maximum)
    trace.idle(17)
    trace.tick(rst=False)
    trace.idle(24)
    trace.tick(sample=minimum)
    trace.load(coef)

    # 新配置开始与采样同沿，只有配置有效。
    trace.configured = False
    trace.tick(start=True, start_accept=True, sample=minimum)
    trace.tick(clear=True)
    trace.tick(sample=minimum)
    trace.tick(rst=False)


def _write_vectors(path: Path, archives: tuple[Path, ...], out: Path) -> tuple[dict, dict]:
    reports = {}
    with path.open("wb") as stream:
        trace = Trace(stream)
        _edge_cases(trace)
        for sigma, archive in zip((2, 3), archives, strict=True):
            coef = from_training(archive)
            trace.load(coef)
            write_json(
                out / f"coefficients_{sigma}pct.json",
                {
                    "format": "signed24_weight_and_unsigned26_gain_q28",
                    "physical_slice_count": 18,
                    "data_words": list(coef.words()),
                    "source_sha256": digest(archive),
                },
            )
            with np.load(archive) as data:
                plan = data["fig21_dem_tone_plan"]
                for mode in ("off", "on"):
                    base = f"fig21_dem_{mode}"
                    main = data[f"{base}_tone_main"]
                    conv = data[f"{base}_tone_sub"]
                    sample = data[f"{base}_tone_sample_sub"]
                    adc = data[f"{base}_tone_adc2_code"]
                    float_codes = data[f"{base}_lms_output_code"].astype(np.int64)
                    offsets = Counter()
                    for i, arrays in enumerate(zip(plan, main, conv, sample, adc, strict=True)):
                        ids, m, c, s = (tuple(map(int, a)) for a in arrays[:-1])
                        value, bad = trace.submit(
                            coef,
                            (
                                i | (sigma << 24) | ((mode == "on") << 23),
                                ids,
                                m,
                                c,
                                s,
                                int(arrays[-1]),
                                False,
                            ),
                        )
                        if bad:
                            raise ValueError("archive tone has an invalid fixed-point result")
                        offsets[value + FULL_SCALE_CODE // 2 - int(float_codes[i])] += 1
                        trace.idle(15)
                    trace.idle(24)
                    if max(map(abs, offsets)) > 1:
                        raise ValueError(
                            "fixed-point versus archived float code differs by >1 LSB20"
                        )
                    reports[f"sigma_{sigma}pct_{mode}"] = {
                        "samples": len(adc),
                        "code_delta_fixed_minus_float_lsb20_counts": dict(sorted(offsets.items())),
                    }
    trace.counts["midpoint"] = 4096
    return trace.counts, reports


def _compare_observation(vectors: Path, observed: Path, midpoint_count: int) -> dict[str, int]:
    """以逐沿真实输出核对期望轨迹、输入受理及反压行为。"""
    counts = dict.fromkeys(FIELDS, 0)
    counts["midpoint"] = midpoint_count
    previous = 0
    with vectors.open() as stimulus, observed.open() as observation:
        for index, (source, result) in enumerate(zip_longest(stimulus, observation)):
            if source is None or result is None:
                raise RuntimeError("observation and stimulus have different lengths")
            v, expected = (int(x, 16) for x in source.split())
            pre, post = (int(x, 16) for x in result.split())
            actual = (pre << POST_EDGE_BITS) | post
            if actual != expected:
                raise RuntimeError(f"cycle {index}: expected {expected:015x}, got {actual:015x}")
            counts["cycles"] += 1
            counts["accepted"] += pre & 1
            counts["words"] += (pre >> 1) & 1
            if previous & (1 << 53) and (v >> (STIMULUS_BITS - 2)) == 2:
                if v & 1:
                    counts["retired"] += 1
                    counts["bad_retired"] += previous & 1
                else:
                    counts["stalls"] += 1
            previous = post
    return counts


def run(out: Path, command: list[str], archives: tuple[Path, ...], timeout: int) -> dict:
    """编译和执行真实工具，核对非零完成计数和输入字节。"""
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="adc20_recon_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*command, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        for src in (RTL, TB):
            shutil.copyfile(ROOT / src, work / Path(src).name)
        vectors, midpoint, observation = (
            work / name for name in ("vectors.hex", "midpoint.hex", "observation.hex")
        )
        counts, report = _write_vectors(vectors, archives, out)
        midpoint.write_text(
            "".join(
                f"{(code << 32) | (decode_midpoint(code) & 0xFFFFFFFF):011x}\n"
                for code in range(4096)
            )
        )
        checked_run(
            [*command, "--lint-only", "-Wall", "--assert", Path(RTL).name],
            cwd=work,
            env=env,
            log=out / "lint.log",
            timeout=timeout,
        )
        checked_run(
            [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "-Wall",
                "-j",
                "1",
                *verilator_compiler_args(),
                "--top-module",
                "sar20_lms_recon_tb",
                Path(RTL).name,
                Path(TB).name,
            ],
            cwd=work,
            env=env,
            log=out / "compile.log",
            timeout=timeout,
        )
        log = checked_run(
            [
                str(work / "obj_dir" / "Vsar20_lms_recon_tb"),
                f"+vectors={vectors}",
                f"+midpoint={midpoint}",
                f"+observation={observation}",
            ],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        finished = completion(log, "RECON_COMPLETE", ("cycles", "midpoint"))
        observed = _compare_observation(vectors, observation, finished["midpoint"])
        if observed != counts or finished["cycles"] != counts["cycles"]:
            raise RuntimeError(f"completion differs from expected: {observed} vs {counts}")
        for file in out.glob("*.log"):
            value = file.read_text(encoding="utf-8")
            value = re.sub(r"(?m)(VERILATOR_ROOT\s*=\s*)/[^\n]+", r"\1<tool-root>", value)
            value = value.replace(temporary, "<build-dir>")
            file.write_text(
                "\n".join(line.rstrip() for line in value.splitlines()) + "\n", encoding="utf-8"
            )
        inputs = (
            RTL,
            TB,
            "task_reconstruction/fixed.py",
            "tools/run_open_rtl.py",
            str(Path(__file__).resolve().relative_to(ROOT)),
        )
        version_match = re.search(r"\bVerilator \d[^\r\n]*", log)
        result = {
            "schema": "sar20-runtime-recon-verilator-v1",
            "status": "PASS",
            "scope": "DIGITAL_BEHAVIOR_ONLY",
            "verilator": version_match.group(0)
            if version_match
            else version.splitlines()[0].strip(),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "mismatches": 0,
            "counts": observed,
            "verified_normal_latency_cycles": RESULT_LATENCY,
            "verified_sustained_sample_spacing_cycles": 16,
            "verified_minimum_sample_spacing_cycles": 13,
            "float_comparison": report,
            "source_sha256": {src: digest(ROOT / src) for src in inputs},
            "archive_sha256": {
                f"sigma_{s}pct": digest(a) for s, a in zip((2, 3), archives, strict=True)
            },
            "vector_sha256": {name.name: digest(name) for name in (vectors, midpoint, observation)},
            "log_sha256": {file.name: digest(file) for file in sorted(out.glob("*.log"))},
            "log_normalization": "local paths replaced; line-end whitespace trimmed",
        }
    write_json(out / "result.json", result)
    return result


def main() -> None:
    """在仓库外新目录生成可以重放的证据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    result = run(new_output(args.out), command_for("verilator"), ARCHIVE, args.timeout)
    print(result["counts"], result["float_comparison"])


if __name__ == "__main__":
    main()
