#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_checks.py
# 模块名     : run_checks
# 版本       : v1.0
# 功能       : 编译八片 RTL，用已验证的调度参考和独立扰动事务逐沿核对。
#
# 设计说明   :
#   * 承担 20b10mtask 数字交付 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""编译八片 RTL，用已验证的调度参考和独立扰动事务逐沿核对。"""

from __future__ import annotations

import argparse
import os
import random
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

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

from task1.tools.run_bridge_checks import (  # noqa: E402
    RTL_SOURCES as MODULE1_RTL,
)
from task1.tools.run_bridge_checks import (  # noqa: E402
    BridgeOracle,
    BridgeStimulus,
    _reference_bridge,
)
from task1.tools.run_rtl_checks import STRICT_LINT, _dem_oracle, _pack, _pack_slots  # noqa: E402
from task2.tools.run_dither_word_checks import _serial_word  # noqa: E402

RTL = (*MODULE1_RTL, "task2/rtl/dither_word_gen.sv", "task2/eight_rtl/rtl/dither8_top.sv")
TB = "task2/eight_rtl/tb/dither8_checks_tb.sv"
SUPPORT = (
    "task1/tools/run_bridge_checks.py",
    "task1/tools/run_rtl_checks.py",
    "task2/tools/run_dither_word_checks.py",
    "tools/run_open_rtl.py",
)
MARKER = "TASK2_DITHER8_COMPLETE"
TEST_SEED = 0xD817
DEFAULT_DITHER_SEED = 0x5A1709C3


# =========================================================================
# 1. 函数组：Stimulus / Descriptor / Oracle
# =========================================================================
# -------------------------------------------------------------------------
# 一沿前的调度、种子写入和最终粗判决。
# 独立期望的同笔物理片序与扰动字段。
# 按发布时间排队并按样本号查询，避免复刻 RTL 的两项查找结构。
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Stimulus(BridgeStimulus):
    """一沿前的调度、种子写入和最终粗判决。"""

    dither_enable: bool = False
    dither_seed: int = 0

    def fields(self) -> tuple[tuple[int, int], ...]:
        """按 testbench 的打包顺序导出输入及位宽。"""
        return (
            (self.rst_n, 1),
            (self.clear, 1),
            (self.enable, 1),
            (self.advance, 1),
            (self.eligible, 18),
            (self.shuffle, 1),
            (self.dem_enable, 1),
            (self.bridge_enable, 1),
            (self.dither_enable, 1),
            (self.seed_load, 1),
            (self.slice_seed, 32),
            (self.dem_seed, 32),
            (self.dither_seed, 31),
            (self.code_valid, 1),
            (self.code, 9),
            (self.code_sample_id, 32),
        )


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class Descriptor:
    """独立期望的同笔物理片序与扰动字段。"""

    sample_id: int = 0
    ids: tuple[int, ...] = (0,) * 8
    word: int = 0

    @property
    def upper(self) -> int:
        """计算从粗码中扣除的高路整数值 U（契约 C9 负注入）。"""
        return 4 * (self.word // 32) + self.word // 8 % 2

    @property
    def lower(self) -> int:
        """计算 SADC 扰动，单位为一档的八分之一。"""
        return 16 * (self.word // 16 % 2) + self.word % 8

    @property
    def total(self) -> int:
        """计算八片档位总和。"""
        return 8 * self.upper + self.lower

    @property
    def sample_sub(self) -> int:
        """将八片整数档转换为物理子电容高参考掩码。"""
        integer, count = divmod(self.total, 8)
        levels = [integer + 1] * count + [integer] * (8 - count)
        masks = tuple(
            sum(1 << idx for idx, weight in enumerate((8, 4, 2, 1)) if level & weight)
            for level in levels
        )
        return _pack_slots(masks, 5)


# ------------------------------------------------------------------------
class Oracle:
    """按发布时间排队并按样本号查询，避免复刻 RTL 的两项查找结构。"""

    def __init__(self) -> None:
        """清空事务队列并恢复默认随机状态。"""
        self.cycle = 0
        self.base = BridgeOracle()
        self.seed_events: dict[int, int] = {}
        self.acq_event: tuple[int, tuple[int, ...], bool] | None = None
        self.request_enabled = self.reject_seed = False
        self.rng = DEFAULT_DITHER_SEED
        self.acq = Descriptor()
        self.history: dict[int, Descriptor] = {}

    def step(self, stimulus: Stimulus) -> tuple[tuple[int, int], ...]:
        """推进一沿并在发布后查找同笔扰动和最终掩码。"""
        active = stimulus.rst_n and not stimulus.clear and stimulus.enable
        if not active:
            self.__init__()
        load_seed = self.seed_events.pop(self.cycle, None)
        acq_valid = self.acq_event is not None
        if load_seed is not None:
            if acq_valid:
                raise RuntimeError("invalid seed/descriptor event collision")
            self.rng = load_seed
        if self.acq_event is not None:
            sample_id, ids, enabled = self.acq_event
            word = 0
            if enabled:
                word, self.rng = _serial_word(self.rng)
            self.acq = Descriptor(sample_id, ids, word)
            self.history[sample_id] = self.acq

        good_seed = (
            active
            and stimulus.seed_load
            and not stimulus.advance
            and min(stimulus.slice_seed, stimulus.dem_seed, stimulus.dither_seed) > 0
        )
        base = self.base.step(
            replace(
                stimulus,
                seed_load=good_seed,
                advance=stimulus.advance and not stimulus.seed_load,
                code_valid=False,
            )
        )
        schedule = base.base.schedule
        self.acq_event = None
        if schedule.slot_accepted:
            self.acq_event = (schedule.acq_sample_id, schedule.acq_ids, self.request_enabled)
        self.request_enabled = stimulus.dither_enable and active
        if good_seed:
            self.seed_events[self.cycle + 2] = stimulus.dither_seed
        seed_error = self.reject_seed
        self.reject_seed = active and stimulus.seed_load and not good_seed
        self.cycle += 1

        descriptor = self.history.get(schedule.conv_sample_id) if schedule.conv_valid else None
        ready = active and descriptor is not None
        conv = descriptor or Descriptor()
        command = stimulus.code - conv.upper
        mask_valid = (
            ready
            and stimulus.code_valid
            and stimulus.code_sample_id == schedule.conv_sample_id
            and stimulus.code >= conv.upper
        )
        main, sub, correction = (0,) * 8, (0,) * 8, 0
        if mask_valid:
            enabled, horizontal, vertical = base.base.conversion
            if base.conv_bridge:
                main, sub, correction, _ = _reference_bridge(command, vertical, horizontal % 8)
            else:
                main, sub, mask_valid = _dem_oracle(command, enabled, horizontal, vertical)

        return (
            (schedule.slot_accepted, 1),
            (schedule.stall, 1),
            (seed_error, 1),
            (acq_valid, 1),
            (self.acq.sample_id, 32),
            (_pack_slots(self.acq.ids, 5), 40),
            (self.acq.word, 6),
            (self.acq.lower, 5),
            (self.acq.sample_sub, 40),
            (ready, 1),
            (schedule.conv_sample_id, 32),
            (_pack_slots(schedule.conv_ids, 5), 40),
            (conv.word, 6),
            (conv.total, 6),
            (conv.sample_sub, 40),
            (command if mask_valid else 0, 9),
            (_pack_slots(main, 17), 136),
            (_pack_slots(sub, 5), 40),
            (correction, 32),
            (mask_valid, 1),
            (active and stimulus.code_valid and not mask_valid, 1),
        )


# =========================================================================
# 2. 函数组：write_vectors / run / main
# =========================================================================
# -------------------------------------------------------------------------
# 保存定向及连续事务，返回必须观测到的完成计数。
# 编译时仅使用列出的 RTL，记录独立覆盖和实际完成计数。
# 在指定的仓库外新目录执行核对。
# ------------------------------------------------------------------------
def write_vectors(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """保存定向及连续事务，返回必须观测到的完成计数。

    Args:
        path: 测试向量输出文件，ASCII 十六进制；低位槽序与测试台一致。
    Returns:
        期望完成计数，以及 64 字、边界、拒绝、stall 和种子事件覆盖。

    参考模型按发布时间维护事务账本，样本号相同才能取回扰动字。
    粗码合法性按 c>=U 判定，再计算 k=c-U；不允许无符号回绕成为正常命令。
    采样描述符与转换结果可在同一时刻属于不同样本，禁止共用当前字替代历史字。
    """
    oracle = Oracle()
    rng = random.Random(TEST_SEED)
    seeds = {}
    for _ in range(2048):
        seed = rng.randrange(1, 1 << 31)
        seeds.setdefault(_serial_word(seed)[0], seed)
    if len(seeds) != 64:
        raise RuntimeError("directed seeds do not cover the entire dither word space")

    counts = dict.fromkeys(
        ("cycles", "acq_words", "valid_commands", "code_errors", "stalls", "seed_errors"), 0
    )
    coverage = dict.fromkeys(
        (
            "underflow_rejects",
            "stale_id_rejects",
            "steady_commands",
            "legal_top",
            "legal_zero",
            "all_64_directed_words",
        ),
        0,
    )
    with path.open("w", encoding="ascii") as stream:

        def put(stimulus: Stimulus) -> tuple[int, ...]:
            expected = oracle.step(stimulus)
            fields = (*stimulus.fields(), *expected)
            digits = (sum(width for _, width in fields) + 3) // 4
            stream.write(f"{_pack(fields):0{digits}x}\n")
            values = tuple(value for value, _ in expected)
            counts["cycles"] += 1
            for name, position in (
                ("acq_words", 3),
                ("valid_commands", 19),
                ("code_errors", 20),
                ("stalls", 1),
                ("seed_errors", 2),
            ):
                counts[name] += values[position]
            return values

        put(Stimulus(rst_n=False, advance=True, code_valid=True))
        put(Stimulus(code_valid=True))
        put(Stimulus(seed_load=True, slice_seed=1, dem_seed=1))
        put(Stimulus(seed_load=True, advance=True, slice_seed=1, dem_seed=1, dither_seed=1))
        put(Stimulus())
        for word, seed in sorted(seeds.items()):
            upper = Descriptor(word=word).upper
            mode = bool(word & 1)
            put(Stimulus(clear=True, advance=True, code_valid=True))
            put(Stimulus(seed_load=True, slice_seed=seed, dem_seed=1, dither_seed=seed))
            put(
                Stimulus(
                    advance=True,
                    shuffle=True,
                    dem_enable=not mode,
                    bridge_enable=mode,
                    dither_enable=True,
                )
            )
            put(Stimulus(advance=True, dither_enable=False))
            # C9 负注入 k = c - U：合法低端是 c == U（命令 0），c == U-1 下溢被拒；
            # c == 511 恒合法且给出 511-U，不再存在高端上溢。U 取 {0,1,4,5}。
            low_edge = put(Stimulus(code_valid=True, code=upper))
            underflow = put(Stimulus(code_valid=True, code=max(upper - 1, 0)))
            top_edge = put(Stimulus(code_valid=True, code=511))
            put(Stimulus(advance=True, eligible=0, dither_enable=True))
            # 错 ID 与后续合法码都用 c=511，保证拒绝原因只有 ID，不混入下溢。
            stale = put(Stimulus(code_valid=True, code=511, code_sample_id=1))
            held = put(Stimulus(code_valid=True, code=511))
            if not (
                low_edge[3]
                and low_edge[6] == word
                and low_edge[19]
                and low_edge[15] == 0
                and top_edge[15] == 511 - upper
            ):
                raise RuntimeError(f"missing directed word or legal low/top endpoint: {word}")
            if (
                bool(underflow[20]) != bool(upper)
                or bool(top_edge[20])
                or not stale[20]
                or not held[19]
            ):
                raise RuntimeError(f"missing underflow, stale-ID or held-word behavior: {word}")
            coverage["all_64_directed_words"] += 1
            coverage["legal_top"] += 1
            coverage["legal_zero"] += low_edge[15] == 0
            coverage["underflow_rejects"] += underflow[20]
            coverage["stale_id_rejects"] += stale[20]

        put(Stimulus(enable=False, advance=True, code_valid=True))
        put(Stimulus(advance=True, dither_enable=True, bridge_enable=True))
        put(Stimulus(advance=True, dither_enable=True, dem_enable=True))
        for index in range(4096):
            observed = put(
                Stimulus(
                    advance=True,
                    shuffle=bool(index % 3),
                    dem_enable=bool(index & 2),
                    bridge_enable=bool(index & 1),
                    dither_enable=bool(index & 4),
                    code_valid=True,
                    # C9 负注入：U_max=5，c>=5 保证随机流恒不下溢、不插泡。
                    # c<U 的下溢拒绝与 c==U 的合法低端已由定向边界用例覆盖。
                    code=rng.randrange(5, 512),
                    code_sample_id=index,
                )
            )
            if not (observed[0] and observed[3] and observed[19]):
                raise RuntimeError(f"stream inserted an unexpected bubble at {index}")
            coverage["steady_commands"] += 1
        put(Stimulus(rst_n=False, code_valid=True))
    if not all(counts.values()) or not all(coverage.values()):
        raise RuntimeError("coverage or comparison count is empty")
    return counts, coverage


# ------------------------------------------------------------------------
def run(out: Path, command: list[str], timeout: int) -> dict:
    """编译时仅使用列出的 RTL，记录独立覆盖和实际完成计数。

    Args:
        out: 新建且位于版本库外的证据目录。
        command: 已配置 Verilator 命令。
        timeout: 子过程上限，单位 s。
    Returns:
        实际运行的完成计数和覆盖信息。

    编译对象放在独立临时目录，不把上次二进制当成本次结果。
    RTL 对拍通过不等于真实注入开关、RA 余量或目标工艺 STA 已通过。
    """
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="dither8_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*command, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        for source in (*RTL, TB):
            shutil.copyfile(ROOT / source, work / Path(source).name)
        sources = [Path(source).name for source in RTL]
        vectors = work / "vectors.hex"
        counts, coverage = write_vectors(vectors)
        checked_run(
            [*command, "--lint-only", "--top-module", "dither8_top", *STRICT_LINT, *sources],
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
                "--timescale",
                "1ns/1ps",
                "-j",
                "1",
                *STRICT_LINT,
                *verilator_compiler_args(),
                "--top-module",
                "dither8_checks_tb",
                *sources,
                Path(TB).name,
            ],
            cwd=work,
            env=env,
            log=out / "compile.log",
            timeout=timeout,
        )
        log = checked_run(
            [str(work / "obj_dir" / "Vdither8_checks_tb"), f"+vectors={vectors}"],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        observed = completion(log, MARKER, tuple(counts))
        if observed != counts:
            raise RuntimeError(f"incomplete comparison: {observed} versus {counts}")
        version_match = re.search(r"(?m)^.*\b(Verilator \d[^\r\n]*)", log)
        simulator_version = (
            version_match.group(1).strip() if version_match else version.splitlines()[0].strip()
        )
        # 公开收据保留完整诊断内容，替换构建路径和本机用户目录。
        log_hashes = {}
        for file in sorted(out.glob("*.log")):
            value = file.read_text(encoding="utf-8")
            public = re.sub(r"(?m)(VERILATOR_ROOT\s*=\s*)/[^\n]+", r"\1<tool-root>", value)
            public = public.replace(temporary, "<build-dir>")
            file.write_text(public, encoding="utf-8")
            log_hashes[file.name] = digest(file)
        sources = (*RTL, TB, *SUPPORT, str(Path(__file__).resolve().relative_to(ROOT)))
        result = {
            "schema": "dither8-rtl-comparison-v1",
            "status": "PASS",
            "scope": "DIGITAL_BEHAVIOR_ONLY",
            "verilator": simulator_version,
            "python": sys.version.split()[0],
            "counts": observed,
            "coverage": coverage,
            "stimulus_seed": TEST_SEED,
            "mismatches": 0,
            "source_sha256": {source: digest(ROOT / source) for source in sources},
            "vectors_sha256": digest(vectors),
            "log_sha256": log_hashes,
        }
        write_json(out / "result.json", result)
        return result


# ------------------------------------------------------------------------
def main() -> None:
    """在指定的仓库外新目录执行核对。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--timeout", default=120, type=int)
    args = parser.parse_args()
    result = run(new_output(args.out), command_for("verilator"), args.timeout)
    print(result["counts"], result["coverage"])


if __name__ == "__main__":
    main()
