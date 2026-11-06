#!/usr/bin/env python3
"""用独立规格向量核对模块一的 Python 模型与 Verilator 逐事件输出。"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task1.model.fig11_dem import map_fig11  # noqa: E402
from task1.model.module1 import Module1  # noqa: E402
from task1.model.scheduler import SliceScheduler  # noqa: E402
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

TOP = "task1_rtl_checks_tb"
MARKER = "TASK1_RTL_CHECKS_COMPLETE"
SOURCES = (
    "task1/rtl/slice_scheduler.sv",
    "task1/rtl/fig11_dem.sv",
    "task1/rtl/module1_top.sv",
    "task1/tb/task1_rtl_checks_tb.sv",
)
PYTHON_MODELS = (
    "task1/model/scheduler.py",
    "task1/model/fig11_dem.py",
    "task1/model/module1.py",
)
MODEL_SEED = 0xA11CE
DEFAULT_LFSR = 0x243F6A88
ALL_SLICES = (1 << 18) - 1
STEADY_SLOTS = 4096
STRICT_LINT = (
    "-Werror-WIDTH",
    "-Werror-LATCH",
    "-Werror-MULTIDRIVEN",
    "-Werror-UNOPTFLAT",
    "-Werror-CASEINCOMPLETE",
    "-Werror-PINMISSING",
    "-Werror-SELRANGE",
)


@dataclass(frozen=True)
class SchedulerStimulus:
    """在时钟上升沿前建立的同步输入。"""

    rst_n: bool = True
    clear: bool = False
    enable: bool = True
    advance: bool = False
    eligible: int = ALL_SLICES
    shuffle: bool = False
    seed_load: bool = False
    seed: int = 0


@dataclass(frozen=True)
class SchedulerObservation:
    """在上升沿后核对的组、归属、资格及单拍事件。"""

    acq_ids: tuple[int, ...]
    conv_ids: tuple[int, ...]
    acq_mask: int
    conv_mask: int
    acq_valid: bool
    conv_valid: bool
    acq_sample_id: int
    conv_sample_id: int
    slot_accepted: bool
    stall: bool
    seed_error: bool
    lfsr_state: int


def _mask(ids: tuple[int, ...] | list[int] | set[int]) -> int:
    return sum(1 << index for index in ids)


def _advance_lfsr(state: int) -> int:
    # 以 32 个独立位和反馈抽头表达规格，独立于生产模型的整数状态更新。
    bits = [(state >> position) & 1 for position in range(32)]
    for _ in range(8):
        feedback = bits[0]
        bits = bits[1:] + [0]
        for position in (0, 1, 21, 31):
            bits[position] ^= feedback
    return sum(bit << position for position, bit in enumerate(bits))


class ScheduleOracle:
    """按物理集合和环形顺序建立分组参考。未分配的组保留空集合。"""

    def __init__(self) -> None:
        """初始化一个空资源池和默认随机状态。"""
        self._acq: tuple[int, ...] = ()
        self._conv: tuple[int, ...] = ()
        self._acq_id = 0
        self._conv_id = 0
        self._next_id = 0
        self._state = DEFAULT_LFSR

    def step(self, command: SchedulerStimulus) -> SchedulerObservation:
        """处理一个时钟沿，并从独立状态生成所有可观察量。"""
        accepted = stall = seed_error = False
        if not command.rst_n or command.clear or not command.enable:
            self.__init__()
        elif command.seed_load:
            seed_error = not command.seed or command.advance
            if not seed_error:
                self._state = command.seed
        elif command.advance:
            eligible = {index for index in range(18) if command.eligible & (1 << index)}
            candidates = eligible.difference(self._acq)
            start = (self._state >> 24) % 18 if command.shuffle else 0
            order = tuple(range(start, 18)) + tuple(range(start))
            picked = tuple(index for index in order if index in candidates)
            if len(picked) < 8:
                stall = True
            else:
                self._conv = self._acq
                self._conv_id = self._acq_id
                self._acq = picked[:8]
                self._acq_id = self._next_id
                self._next_id = (self._next_id + 1) & 0xFFFF_FFFF
                accepted = True
                if command.shuffle:
                    self._state = _advance_lfsr(self._state)
        return SchedulerObservation(
            self._acq or (0,) * 8,
            self._conv or (0,) * 8,
            _mask(self._acq),
            _mask(self._conv),
            bool(self._acq),
            bool(self._conv),
            self._acq_id,
            self._conv_id,
            accepted,
            stall,
            seed_error,
            self._state,
        )


def _pack(fields: tuple[tuple[int, int], ...]) -> int:
    packed = 0
    for value, width in fields:
        if not 0 <= value < 1 << width:
            raise ValueError(f"value {value} does not fit in {width} bits")
        packed = (packed << width) | value
    return packed


def _pack_slots(masks: tuple[int, ...], width: int) -> int:
    return sum(mask << (slot * width) for slot, mask in enumerate(masks))


def _schedule_result_fields(expected: SchedulerObservation) -> tuple[tuple[int, int], ...]:
    return (
        (_pack_slots(expected.acq_ids, 5), 40),
        (_pack_slots(expected.conv_ids, 5), 40),
        (expected.acq_mask, 18),
        (expected.conv_mask, 18),
        (expected.acq_valid, 1),
        (expected.conv_valid, 1),
        (expected.acq_sample_id, 32),
        (expected.conv_sample_id, 32),
        (expected.slot_accepted, 1),
        (expected.stall, 1),
        (expected.seed_error, 1),
        (expected.lfsr_state, 32),
    )


def _schedule_word(command: SchedulerStimulus, expected: SchedulerObservation) -> int:
    return _pack(
        (
            (command.rst_n, 1),
            (command.clear, 1),
            (command.enable, 1),
            (command.advance, 1),
            (command.eligible, 18),
            (command.shuffle, 1),
            (command.seed_load, 1),
            (command.seed, 32),
            *_schedule_result_fields(expected),
        )
    )


def generate_scheduler_vectors(path: Path) -> dict[str, int]:
    """覆盖启动、配置、拒绝、恢复及 4096 个可接受的稳态帧。"""
    oracle = ScheduleOracle()
    model = SliceScheduler()
    rng = random.Random(MODEL_SEED)
    coverage = {
        "scheduler_cycles": 0,
        "scheduler_accepts": 0,
        "scheduler_conversions": 0,
        "scheduler_stalls": 0,
        "scheduler_seed_errors": 0,
        "scheduler_steady_slots": 0,
    }

    def put(command: SchedulerStimulus) -> SchedulerObservation:
        expected = oracle.step(command)
        actual = model.step(**asdict(command))
        for field, value in asdict(expected).items():
            actual_value = getattr(actual, field)
            if actual_value != value:
                raise RuntimeError(
                    f"Python scheduler mismatch at {coverage['scheduler_cycles']}: "
                    f"{field}={actual_value!r}, expected {value!r}; {command}"
                )
        stream.write(f"{_schedule_word(command, expected):069x}\n")
        coverage["scheduler_cycles"] += 1
        coverage["scheduler_accepts"] += expected.slot_accepted
        coverage["scheduler_conversions"] += expected.slot_accepted and expected.conv_valid
        coverage["scheduler_stalls"] += expected.stall
        coverage["scheduler_seed_errors"] += expected.seed_error
        return expected

    replay = SchedulerStimulus(advance=True, shuffle=True)
    with path.open("w", encoding="ascii") as stream:
        put(SchedulerStimulus(rst_n=False, advance=True, seed_load=True, seed=1))
        put(SchedulerStimulus())
        put(SchedulerStimulus(advance=True, eligible=0x7F, shuffle=True))
        put(SchedulerStimulus(seed_load=True, seed=0))
        put(SchedulerStimulus(seed_load=True, seed=0x80D3C99B))
        put(SchedulerStimulus(advance=True, seed_load=True, seed=0xADCAFEEF))
        initial = put(
            SchedulerStimulus(
                advance=True, eligible=_mask({0, 1, 2, 3, 4, 5, 10, 17}), shuffle=True
            )
        )
        put(SchedulerStimulus(advance=True, eligible=initial.acq_mask, shuffle=True))
        put(SchedulerStimulus())
        free = sorted(set(range(18)).difference(initial.acq_ids))
        put(SchedulerStimulus(advance=True, eligible=initial.acq_mask | _mask(free[:7])))
        converted = put(SchedulerStimulus(advance=True, eligible=_mask(free[:8]), shuffle=True))
        standby = ALL_SLICES ^ (converted.acq_mask | converted.conv_mask)
        put(SchedulerStimulus(advance=True, eligible=standby, shuffle=True))
        put(SchedulerStimulus(clear=True, advance=True, seed_load=True))
        put(SchedulerStimulus(enable=False, advance=True, seed_load=True))

        put(SchedulerStimulus(seed_load=True, seed=0xADCAFEEF))
        first_replay = [put(replay) for _ in range(64)]
        put(SchedulerStimulus(clear=True, advance=True))
        put(SchedulerStimulus(seed_load=True, seed=0xADCAFEEF))
        if first_replay != [put(replay) for _ in range(64)]:
            raise RuntimeError("fixed-seed replay diverged")
        put(SchedulerStimulus(rst_n=False, advance=True))
        previous = put(replay)
        for index in range(STEADY_SLOTS):
            free = sorted(set(range(18)).difference(previous.acq_ids))
            if index % 31 == 0:
                put(
                    SchedulerStimulus(
                        advance=True,
                        eligible=previous.acq_mask | _mask(rng.sample(free, 7)),
                        shuffle=True,
                    )
                )
            if index % 17 == 0:
                put(SchedulerStimulus())
            released = rng.sample(free, 8 + index % 3)
            eligible = _mask(released) | (previous.acq_mask if index % 2 else 0)
            result = put(
                SchedulerStimulus(advance=True, eligible=eligible, shuffle=index % 23 != 0)
            )
            if not (result.slot_accepted and result.conv_valid):
                raise RuntimeError(f"expected steady conversion at slot {index}")
            if result.conv_ids != previous.acq_ids:
                raise RuntimeError(f"ordered acquisition group lost at slot {index}")
            previous = result
            coverage["scheduler_steady_slots"] += 1
    return coverage


def _dem_oracle(
    code: int, enable: bool, horizontal: int, vertical: int
) -> tuple[tuple[int, ...], tuple[int, ...], bool]:
    if enable and horizontal == 15:
        return (0,) * 8, (0,) * 8, False
    coarse_count, tail = divmod(code, 32)
    selected_vertical = {(vertical + offset) % 16 for offset in range(8)}
    main = []
    for slot in range(8):
        start = horizontal + slot if enable else 0
        selected = {(start + offset) % 15 for offset in range(coarse_count)}
        if tail >= 16:
            if enable:
                selected.update(
                    15 + unit for unit in (0, 1) if 2 * slot + unit in selected_vertical
                )
            else:
                selected.add(15)
        main.append(_mask(selected))
    sub_bits = sum(1 << unit for unit in range(4) if tail & (8 >> unit))
    return tuple(main), (sub_bits,) * 8, True


def generate_dem_vectors(path: Path) -> dict[str, int]:
    """穷举码、两相位和使能，分别检查掩码与名义转换电荷。"""
    coverage = {"dem_active": 0, "dem_bypass": 0, "dem_invalid": 0}
    with path.open("w", encoding="ascii") as stream:
        for enable in (False, True):
            for code in range(512):
                for horizontal in range(16):
                    for vertical in range(16):
                        main, sub, valid = _dem_oracle(code, enable, horizontal, vertical)
                        model = map_fig11(code, enable, horizontal, vertical)
                        if (model.main, model.sub, model.valid) != (main, sub, valid):
                            raise RuntimeError(
                                "Python DEM mismatch for "
                                f"code={code}, enable={enable}, h={horizontal}, v={vertical}"
                            )
                        if valid:
                            # 分开检查主、子节点，避免两个节点的错误电荷相消。
                            main_c = sum(
                                2 * (row & 0x7FFF).bit_count() + (row >> 15).bit_count()
                                for row in main
                            )
                            sub_c = sum(
                                sum(
                                    weight
                                    for unit, weight in enumerate((8, 4, 2, 1, 1))
                                    if row & (1 << unit)
                                )
                                for row in sub
                            )
                            expected_charge = (
                                16 * (code >> 5) + 8 * ((code >> 4) & 1),
                                8 * (code & 15),
                            )
                            if (main_c, sub_c) != expected_charge:
                                raise RuntimeError(f"nominal charge not preserved for code {code}")
                        category = (
                            "dem_invalid" if not valid else "dem_active" if enable else "dem_bypass"
                        )
                        coverage[category] += 1
                        packed = _pack(
                            (
                                (code, 9),
                                (enable, 1),
                                (horizontal, 4),
                                (vertical, 4),
                                (_pack_slots(main, 17), 136),
                                (_pack_slots(sub, 5), 40),
                                (valid, 1),
                            )
                        )
                        stream.write(f"{packed:049x}\n")
    return coverage


@dataclass(frozen=True)
class TopStimulus:
    """集成顶层的同步请求和沿后使用的组合前缀输入。"""

    rst_n: bool = True
    clear: bool = False
    enable: bool = True
    advance: bool = False
    eligible: int = ALL_SLICES
    shuffle: bool = False
    dem_enable: bool = False
    seed_load: bool = False
    slice_seed: int = 0
    dem_seed: int = 0
    code_valid: bool = False
    code: int = 0
    code_sample_id: int = 0


@dataclass(frozen=True)
class TopObservation:
    """集成顶层发布的同笔上下文及当前前缀掩码。"""

    schedule: SchedulerObservation
    dem_state: int
    acquisition: tuple[bool, int, int]
    conversion: tuple[bool, int, int]
    main: tuple[int, ...]
    sub: tuple[int, ...]
    mask_valid: bool
    code_error: bool


def _next_dem_state(state: int) -> int:
    bits = [(state >> position) & 1 for position in range(32)]
    for shift in (13, -17, 5):
        bits = [
            bit ^ (bits[position - shift] if 0 <= position - shift < 32 else 0)
            for position, bit in enumerate(bits)
        ]
    return sum(bit << position for position, bit in enumerate(bits))


class TopOracle:
    """把调度事务排入一拍发布槽，并按样本冻结独立 DEM 状态。"""

    def __init__(self) -> None:
        """清空在途事务并恢复两条独立随机流。"""
        self._scheduler = ScheduleOracle()
        self._pending = self._scheduler.step(SchedulerStimulus())
        self._pending_enable = False
        self._pending_seed: int | None = None
        self._pending_error = False
        self._state = 0x9E3779B9
        self._acq = (False, 0, 0)
        self._conv = (False, 0, 0)

    def step(self, command: TopStimulus) -> TopObservation:
        """发布旧事务，受理本拍请求，再以已发布 ID 检查组合前缀。"""
        active = command.rst_n and not command.clear and command.enable
        if not active:
            self.__init__()
        published = replace(self._pending, seed_error=self._pending_error)
        if active:
            if self._pending_seed is not None:
                self._state = self._pending_seed
            elif published.slot_accepted:
                self._conv = self._acq
                self._acq = (
                    self._pending_enable,
                    (self._state >> 24) % 15,
                    (self._state >> 16) & 15,
                )
                self._state = _next_dem_state(self._state)

            good_seed = (
                command.seed_load
                and not command.advance
                and command.slice_seed != 0
                and command.dem_seed != 0
            )
            self._pending_error = command.seed_load and not good_seed
            self._pending_enable = command.dem_enable
            self._pending_seed = command.dem_seed if good_seed else None
            self._pending = self._scheduler.step(
                SchedulerStimulus(
                    advance=command.advance and not command.seed_load,
                    eligible=command.eligible,
                    shuffle=command.shuffle,
                    seed_load=good_seed,
                    seed=command.slice_seed,
                )
            )
        request_code = active and command.code_valid
        valid = (
            request_code
            and published.conv_valid
            and command.code_sample_id == published.conv_sample_id
        )
        main, sub = (0,) * 8, (0,) * 8
        if valid:
            main, sub, valid = _dem_oracle(command.code, *self._conv)
        return TopObservation(
            published,
            self._state,
            self._acq,
            self._conv,
            main,
            sub,
            valid,
            request_code and not valid,
        )


def _top_word(command: TopStimulus, expected: TopObservation) -> int:
    return _pack(
        (
            (command.rst_n, 1),
            (command.clear, 1),
            (command.enable, 1),
            (command.advance, 1),
            (command.eligible, 18),
            (command.shuffle, 1),
            (command.dem_enable, 1),
            (command.seed_load, 1),
            (command.slice_seed, 32),
            (command.dem_seed, 32),
            (command.code_valid, 1),
            (command.code, 9),
            (command.code_sample_id, 32),
            *_schedule_result_fields(expected.schedule),
            (expected.dem_state, 32),
            (expected.acquisition[0], 1),
            (expected.conversion[0], 1),
            (expected.acquisition[1], 4),
            (expected.acquisition[2], 4),
            (expected.conversion[1], 4),
            (expected.conversion[2], 4),
            (_pack_slots(expected.main, 17), 136),
            (_pack_slots(expected.sub, 5), 40),
            (expected.mask_valid, 1),
            (expected.code_error, 1),
        )
    )


def generate_top_vectors(path: Path) -> dict[str, int]:
    """核对同笔前缀资格、原子配置、取消与一拍发布，最后持续派发。"""
    oracle = TopOracle()
    model = Module1()
    coverage = {
        "top_cycles": 0,
        "top_accepts": 0,
        "top_conversions": 0,
        "top_stalls": 0,
        "top_seed_errors": 0,
        "top_active_prefixes": 0,
        "top_bypass_prefixes": 0,
        "top_code_errors": 0,
        "top_steady_slots": 0,
    }

    def put(command: TopStimulus) -> TopObservation:
        expected = oracle.step(command)
        actual = model.step(**asdict(command))
        normalized = TopObservation(
            SchedulerObservation(**asdict(actual.schedule)),
            actual.dem_rng_state,
            (
                actual.acquisition_dem.enabled,
                actual.acquisition_dem.horizontal_phase,
                actual.acquisition_dem.vertical_phase,
            ),
            (
                actual.conversion_dem.enabled,
                actual.conversion_dem.horizontal_phase,
                actual.conversion_dem.vertical_phase,
            ),
            actual.masks.main,
            actual.masks.sub,
            actual.masks.valid,
            actual.code_error,
        )
        if normalized != expected:
            raise RuntimeError(
                f"Python module1_top mismatch at {coverage['top_cycles']}: "
                f"{command}; expected {expected}, got {normalized}"
            )
        stream.write(f"{_top_word(command, expected):0144x}\n")
        coverage["top_cycles"] += 1
        coverage["top_accepts"] += expected.schedule.slot_accepted
        coverage["top_conversions"] += (
            expected.schedule.slot_accepted and expected.schedule.conv_valid
        )
        coverage["top_stalls"] += expected.schedule.stall
        coverage["top_seed_errors"] += expected.schedule.seed_error
        coverage["top_active_prefixes"] += expected.mask_valid and expected.conversion[0]
        coverage["top_bypass_prefixes"] += expected.mask_valid and not expected.conversion[0]
        coverage["top_code_errors"] += expected.code_error
        return expected

    with path.open("w", encoding="ascii") as stream:
        put(TopStimulus(rst_n=False, advance=True, seed_load=True, code_valid=True))
        put(TopStimulus(code_valid=True))
        put(TopStimulus(seed_load=True, slice_seed=0, dem_seed=0x70000001))
        put(TopStimulus(seed_load=True, slice_seed=0x81AC1172, dem_seed=0))
        put(TopStimulus(advance=True, seed_load=True, slice_seed=0xFEDCBA98, dem_seed=0x70000001))
        put(TopStimulus(seed_load=True, slice_seed=0x81AC1172, dem_seed=0xFEDCBA98))
        put(TopStimulus(advance=True, shuffle=True, dem_enable=True))
        put(TopStimulus(advance=True, shuffle=True, dem_enable=False))
        first = put(TopStimulus(code_valid=True, code=0x111))
        put(TopStimulus(code_valid=True, code=0x0F3))
        put(TopStimulus(code_valid=True, code=0x0F3, code_sample_id=1))
        put(TopStimulus(advance=True, eligible=first.schedule.acq_mask, dem_enable=True))
        put(TopStimulus(code_valid=True, code=0x1F0))
        put(TopStimulus(advance=True, shuffle=True))
        put(TopStimulus(clear=True, advance=True, code_valid=True))
        put(TopStimulus(advance=True, dem_enable=True))
        put(TopStimulus(enable=False, code_valid=True))
        put(TopStimulus(advance=True, shuffle=True, dem_enable=True))
        put(TopStimulus(advance=True, shuffle=True, dem_enable=False))
        for index in range(STEADY_SLOTS):
            result = put(
                TopStimulus(
                    advance=True,
                    shuffle=index % 17 != 0,
                    dem_enable=index % 2 == 0,
                    code_valid=True,
                    code=index % 512,
                    code_sample_id=index,
                )
            )
            if not (result.schedule.slot_accepted and result.mask_valid):
                raise RuntimeError(f"integrated steady conversion missing at slot {index}")
            coverage["top_steady_slots"] += 1
        put(TopStimulus(rst_n=False, code_valid=True))
    return coverage


def run(out: Path, command: list[str], timeout: int) -> dict:
    """在 ASCII 临时构建树编译并执行向量，将结果证据写入明确输出路径。"""
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="task1_rtl_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*command, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        hashes = {
            str(Path(__file__).resolve().relative_to(ROOT)): digest(Path(__file__)),
            "tools/run_open_rtl.py": digest(ROOT / "tools/run_open_rtl.py"),
        }
        for source in SOURCES:
            staged = work / source
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, staged)
            hashes[source] = digest(staged)
        for source in PYTHON_MODELS:
            hashes[source] = digest(ROOT / source)
        scheduler_vectors = work / "scheduler.hex"
        dem_vectors = work / "dem.hex"
        expected = generate_scheduler_vectors(scheduler_vectors)
        expected.update(generate_dem_vectors(dem_vectors))
        top_vectors = work / "top.hex"
        expected.update(generate_top_vectors(top_vectors))
        lint_tops = (
            ("task1_slice_scheduler", SOURCES[:1]),
            ("fig11_dem", SOURCES[1:2]),
            ("module1_top", SOURCES[:3]),
        )
        for module, sources in lint_tops:
            checked_run(
                [*command, "--lint-only", "--top-module", module, *STRICT_LINT, *sources],
                cwd=work,
                env=env,
                log=out / f"{module}.lint.log",
                timeout=timeout,
            )
        checked_run(
            [
                *command,
                "--binary",
                "--timing",
                "--assert",
                "--timescale",
                "1ns/1ps",
                "-j",
                "1",
                *STRICT_LINT,
                *verilator_compiler_args(),
                "--top-module",
                TOP,
                "--Mdir",
                str(work / "build"),
                *SOURCES,
            ],
            cwd=work,
            env=env,
            log=out / "compile.log",
            timeout=timeout,
        )
        binary = work / "build" / f"V{TOP}"
        if not binary.is_file() or not binary.stat().st_size:
            raise RuntimeError("Verilator did not produce a fresh executable")
        log = checked_run(
            [
                str(binary),
                f"+scheduler_vectors={scheduler_vectors}",
                f"+dem_vectors={dem_vectors}",
                f"+top_vectors={top_vectors}",
            ],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        expected_counts = {
            name: count for name, count in expected.items() if not name.endswith("_steady_slots")
        }
        counts = completion(log, MARKER, tuple(expected_counts))
        if counts != expected_counts:
            raise RuntimeError(f"incomplete RTL coverage: {counts}; expected {expected_counts}")
        reported_version = re.search(r"R e p o r t: (Verilator [^\r\n]+)", log)
        result = {
            "schema_version": 1,
            "status": "PASS",
            "scope": "TASK1_DIGITAL_MODULE1",
            "simulator": (
                reported_version.group(1).strip()
                if reported_version
                else version.splitlines()[0].strip()
            ),
            "scheduler_fixture_seed": MODEL_SEED,
            "counts": {
                **counts,
                "scheduler_steady_slots": expected["scheduler_steady_slots"],
                "top_steady_slots": expected["top_steady_slots"],
            },
            "mismatches": 0,
            "source_sha256": hashes,
            "log_sha256": {file.name: digest(file) for file in sorted(out.glob("*.log"))},
            "vector_sha256": {
                "scheduler.hex": digest(scheduler_vectors),
                "dem.hex": digest(dem_vectors),
                "top.hex": digest(top_vectors),
            },
        }
        write_json(out / "result.json", result)
        return result


def main() -> int:
    """解析 Verilator 路径与输出位置并运行自检。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="仓库外尚不存在的输出目录")
    parser.add_argument("--verilator", type=Path, help="现有 Verilator 可执行文件")
    parser.add_argument("--timeout", type=int, default=120, help="每个工具步骤的超时（秒）")
    args = parser.parse_args()
    try:
        if args.timeout <= 0:
            raise ValueError("--timeout must be positive")
        command = (
            [str(args.verilator.expanduser().resolve(strict=True))]
            if args.verilator is not None
            else command_for("verilator")
        )
        out = new_output(args.out)
        result = run(out, command, args.timeout)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"TASK1_RTL_CHECKS_FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{MARKER}: {result['counts']}; receipt={out / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
