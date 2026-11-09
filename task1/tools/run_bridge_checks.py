#!/usr/bin/env python3
# =============================================================================
# 文件名     : run_bridge_checks.py
# 模块名     : run_bridge_checks
# 版本       : v1.0
# 功能       : 核对桥接叶全域和候选顶层的同笔 RTL 事务。
#
# 设计说明   :
#   * 承担 向量生成与逐沿对拍入口 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""核对桥接叶全域和候选顶层的同笔 RTL 事务。"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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

from task1.model.fig21_bridge import map_fig21_bridge  # noqa: E402
from task1.model.module1_bridge import Module1Bridge  # noqa: E402
from task1.tools.run_rtl_checks import (  # noqa: E402
    STRICT_LINT,
    SchedulerObservation,
    TopObservation,
    TopOracle,
    TopStimulus,
    _pack,
    _pack_slots,
    _top_word,
)

TOP = "task1_bridge_checks_tb"
MARKER = "TASK1_BRIDGE_CHECKS_COMPLETE"
Q32_SCALE = 1 << 32
STEADY_SLOTS = 4096
RTL_SOURCES = (
    "task1/rtl/slice_scheduler.sv",
    "task1/rtl/fig11_dem.sv",
    "task1/rtl/module1_top.sv",
    "task1/rtl/fig21_bridge.sv",
    "task1/rtl/module1_bridge_top.sv",
)
BENCH = "task1/tb/bridge_checks_tb.sv"
SUPPORT_SOURCES = (
    "task1/tools/run_rtl_checks.py",
    "tools/run_open_rtl.py",
    "task1/model/scheduler.py",
    "task1/model/fig11_dem.py",
    "task1/model/module1.py",
    "task1/model/fig21_bridge.py",
    "task1/model/module1_bridge.py",
)


# =========================================================================
# 1. 函数组：BridgeStimulus / BridgeObservation / _selected_weights
# =========================================================================
# -------------------------------------------------------------------------
# 模块一输入，附随采集请求冻结的桥接模式。
# 沿后上下文、前缀掩码及该前缀的补偿。
# ------------------------------------------------------------------------
@dataclass(frozen=True)
class BridgeStimulus(TopStimulus):
    """模块一输入，附随采集请求冻结的桥接模式。"""

    bridge_enable: bool = False


# ------------------------------------------------------------------------
@dataclass(frozen=True)
class BridgeObservation:
    """沿后上下文、前缀掩码及该前缀的补偿。"""

    base: TopObservation
    acq_bridge: bool
    conv_bridge: bool
    correction_q32: int


# ------------------------------------------------------------------------
def _selected_weights(rows: tuple[int, ...], weights: tuple[int, ...]) -> int:
    return sum(weight for row in rows for bit, weight in enumerate(weights) if row & (1 << bit))


# =========================================================================
# 2. 函数组：_effective_level / _reference_bridge / BridgeOracle
# =========================================================================
# -------------------------------------------------------------------------
# 使用旧事务 oracle 并把桥接位绑定到它报告的接受事件。
# ------------------------------------------------------------------------
def _effective_level(main_c: int, sub_c: int) -> Fraction:
    # 主节点为虚地，子节点的 16C 与 1C 桥接共同决定反馈。
    # 主阵列 8×32C 采 VIN，开关在 -Vfs 与 +Vfs 之间转换。
    return (main_c + Fraction(sub_c, 17)) * Fraction(2, 8 * 32)


# ------------------------------------------------------------------------
def _reference_bridge(
    code: int, reserved: int, first_slot: int
) -> tuple[tuple[int, ...], tuple[int, ...], int, Fraction]:
    high, low = divmod(code, 32)
    count, fine = divmod(low, 4)
    column_order = tuple(range(reserved + 1, 16)) + tuple(range(reserved))
    regular = set(column_order[:high])
    slot_order = tuple(range(first_slot, 8)) + tuple(range(first_slot))
    selected = set(slot_order[:count])

    main_rows = []
    for slot in range(8):
        columns = regular | ({reserved} if slot in selected else set())
        # 逻辑列 15 是两个可同控的物理 1C，其他列各是一只 2C。
        physical = (columns - {15}) | ({15, 16} if 15 in columns else set())
        main_rows.append(sum(1 << unit for unit in physical))
    sub_word = sum(1 << unit for unit, weight in ((2, 2), (3, 1)) if fine & weight)
    main, sub = tuple(main_rows), (sub_word,) * 8

    old_level = _effective_level(8 * (2 * high + int(low >= 16)), 8 * (low % 16))
    migrated_level = _effective_level(
        _selected_weights(main, (2,) * 15 + (1, 1)),
        _selected_weights(sub, (8, 4, 2, 1, 1)),
    )
    delta = migrated_level - old_level
    correction = round(delta * Q32_SCALE)
    return main, sub, correction, delta


# ------------------------------------------------------------------------
class BridgeOracle:
    """使用旧事务 oracle 并把桥接位绑定到它报告的接受事件。"""

    def __init__(self) -> None:
        """启动空采集、空转换与空在途请求。"""
        self._base = TopOracle()
        self._requested = self._acq = self._conv = False

    def step(self, command: BridgeStimulus) -> BridgeObservation:
        """受理一拍，按已发布 ID 生成当前前缀的可见结果。"""
        base = self._base.step(command)
        if not command.rst_n or command.clear or not command.enable:
            self._requested = self._acq = self._conv = False
        else:
            if base.schedule.slot_accepted:
                self._conv, self._acq = self._acq, self._requested
            self._requested = command.bridge_enable
        correction = 0
        if self._conv and base.mask_valid:
            _, h, v = base.conversion
            main, sub, correction, _ = _reference_bridge(command.code, v, h % 8)
            base = replace(base, main=main, sub=sub)
        return BridgeObservation(base, self._acq, self._conv, correction)


# =========================================================================
# 3. 函数组：generate_leaf_vectors / generate_top_vectors / run
# =========================================================================
# -------------------------------------------------------------------------
# 全量检查 512 码、16 保留列和 8 纵向起始片。
# 覆盖桥接切换、同笔前缀、拒绝、原子种子及连续派发。
# 运行 Verilator，记录工具、源码、向量和实际通过计数。
# ------------------------------------------------------------------------
def generate_leaf_vectors(path: Path) -> dict[str, int]:
    """全量检查 512 码、16 保留列和 8 纵向起始片。"""
    coverage = {"leaf_cases": 0, "leaf_nonzero_corrections": 0}
    with path.open("w", encoding="ascii") as stream:
        for code in range(512):
            for reserved in range(16):
                for vertical in range(8):
                    main, sub, correction, delta = _reference_bridge(code, reserved, vertical)
                    actual = map_fig21_bridge(code, reserved, vertical)
                    if (
                        actual.masks.main,
                        actual.masks.sub,
                        actual.correction_q32,
                        actual.masks.valid,
                    ) != (
                        main,
                        sub,
                        correction,
                        True,
                    ):
                        raise RuntimeError(
                            f"Python leaf mismatch: code={code}, R={reserved}, v={vertical}"
                        )
                    # 原码本重构只加一次。以精确分数验证 Q32 舍入误差。
                    if abs(Fraction(correction, Q32_SCALE) - delta) > Fraction(1, 2 * Q32_SCALE):
                        raise RuntimeError(
                            f"nominal compensation exceeds half a Q32 LSB: code={code}"
                        )
                    fields = (
                        (code, 9),
                        (reserved, 4),
                        (vertical, 3),
                        (_pack_slots(main, 17), 136),
                        (_pack_slots(sub, 5), 40),
                        (correction, 32),
                    )
                    stream.write(f"{_pack(fields):056x}\n")
                    coverage["leaf_cases"] += 1
                    coverage["leaf_nonzero_corrections"] += correction != 0
    return coverage


# ------------------------------------------------------------------------
def generate_top_vectors(path: Path) -> dict[str, int]:
    """覆盖桥接切换、同笔前缀、拒绝、原子种子及连续派发。"""
    oracle = BridgeOracle()
    model = Module1Bridge()
    coverage = {
        "top_cycles": 0,
        "top_accepts": 0,
        "top_stalls": 0,
        "top_seed_errors": 0,
        "top_code_errors": 0,
        "top_bridge_prefixes": 0,
        "top_fig11_dem_prefixes": 0,
        "top_fig11_bypass_prefixes": 0,
        "top_nonzero_corrections": 0,
        "top_steady_slots": 0,
    }

    def put(command: BridgeStimulus) -> BridgeObservation:
        expected = oracle.step(command)
        observed = model.step(**asdict(command))
        base = observed.module1
        actual = BridgeObservation(
            TopObservation(
                SchedulerObservation(**asdict(base.schedule)),
                base.dem_rng_state,
                (
                    base.acquisition_dem.enabled,
                    base.acquisition_dem.horizontal_phase,
                    base.acquisition_dem.vertical_phase,
                ),
                (
                    base.conversion_dem.enabled,
                    base.conversion_dem.horizontal_phase,
                    base.conversion_dem.vertical_phase,
                ),
                base.masks.main,
                base.masks.sub,
                base.masks.valid,
                base.code_error,
            ),
            observed.acquisition_bridge_enabled,
            observed.conversion_bridge_enabled,
            observed.correction_q32,
        )
        if actual != expected:
            raise RuntimeError(f"Python bridge top mismatch at {coverage['top_cycles']}: {command}")
        packed = _pack(
            (
                (_top_word(command, expected.base), 576),
                (command.bridge_enable, 1),
                (expected.acq_bridge, 1),
                (expected.conv_bridge, 1),
                (expected.correction_q32, 32),
            )
        )
        stream.write(f"{packed:0153x}\n")
        base = expected.base
        coverage["top_cycles"] += 1
        coverage["top_accepts"] += base.schedule.slot_accepted
        coverage["top_stalls"] += base.schedule.stall
        coverage["top_seed_errors"] += base.schedule.seed_error
        coverage["top_code_errors"] += base.code_error
        coverage["top_bridge_prefixes"] += base.mask_valid and expected.conv_bridge
        old_valid = base.mask_valid and not expected.conv_bridge
        coverage["top_fig11_dem_prefixes"] += old_valid and base.conversion[0]
        coverage["top_fig11_bypass_prefixes"] += old_valid and not base.conversion[0]
        coverage["top_nonzero_corrections"] += expected.correction_q32 != 0
        return expected

    with path.open("w", encoding="ascii") as stream:
        put(BridgeStimulus(rst_n=False, advance=True, bridge_enable=True, code_valid=True))
        put(BridgeStimulus(code_valid=True, code=0x00C))
        put(BridgeStimulus(seed_load=True, slice_seed=1, dem_seed=0, bridge_enable=True))
        put(
            BridgeStimulus(
                advance=True, seed_load=True, slice_seed=1, dem_seed=1, bridge_enable=True
            )
        )
        put(BridgeStimulus(seed_load=True, slice_seed=0x81AC1172, dem_seed=0xFEDCBA98))
        put(BridgeStimulus(advance=True, shuffle=True, bridge_enable=True))
        put(BridgeStimulus(advance=True, shuffle=True, dem_enable=True))
        first = put(BridgeStimulus(code_valid=True, code=0x00C))
        repeated = put(BridgeStimulus(code_valid=True, code=0x00C, bridge_enable=True))
        if repeated.correction_q32 != first.correction_q32 or repeated.base.main != first.base.main:
            raise RuntimeError("repeated prefix changed frozen bridge output")
        put(BridgeStimulus(code_valid=True, code=0x100))
        put(BridgeStimulus(code_valid=True, code=0x00C, code_sample_id=1))
        put(BridgeStimulus(code=0x00C))
        put(BridgeStimulus(advance=True, eligible=first.base.schedule.acq_mask, bridge_enable=True))
        put(BridgeStimulus(code_valid=True, code=0x00C))
        put(BridgeStimulus(advance=True, bridge_enable=False))
        put(BridgeStimulus(code_valid=True, code=0x00C, code_sample_id=1, bridge_enable=True))
        put(BridgeStimulus(advance=True, bridge_enable=True))
        put(BridgeStimulus(clear=True, advance=True, bridge_enable=True, code_valid=True))
        put(BridgeStimulus(advance=True, bridge_enable=True))
        put(BridgeStimulus(enable=False, code_valid=True))
        put(BridgeStimulus(advance=True, shuffle=True, bridge_enable=True))
        put(BridgeStimulus(advance=True, shuffle=True, dem_enable=True))
        for index in range(STEADY_SLOTS):
            result = put(
                BridgeStimulus(
                    advance=True,
                    shuffle=index % 17 != 0,
                    dem_enable=bool(index & 2),
                    bridge_enable=bool(index & 1),
                    code_valid=True,
                    code=(index * 137) % 512,
                    code_sample_id=index,
                )
            )
            if not (result.base.schedule.slot_accepted and result.base.mask_valid):
                raise RuntimeError(f"steady conversion missing at slot {index}")
            coverage["top_steady_slots"] += 1
        put(BridgeStimulus(rst_n=False, code_valid=True))
    return coverage


# ------------------------------------------------------------------------
def run(out: Path, command: list[str], timeout: int) -> dict:
    """运行 Verilator，记录工具、源码、向量和实际通过计数。

    Args:
        out: 新建的仓库外输出目录。
        command: 已配置工具的命令参数，不在本函数替换工具环境。
        timeout: 执行上限，单位 s。
    Returns:
        实际完成计数与独立桥接参考检查结果。

    叶模块与调度顶层分别编译，避免顶层连线错误被单模块测试遗漏。
    名义主／子电荷按独立位权累加；固定补偿和制造失配是不同项目。
    日志与源码均按 SHA-256 记录；更换源文件后旧 PASS 不自动适用。
    """
    env = os.environ.copy()
    with tempfile.TemporaryDirectory(prefix="task1_bridge_") as temporary:
        work = Path(temporary)
        version = checked_run(
            [*command, "-V"], cwd=work, env=env, log=out / "verilator.log", timeout=timeout
        )
        alias_runtime(work, env, version)
        hashes = {str(Path(__file__).resolve().relative_to(ROOT)): digest(Path(__file__))}
        for source in (*RTL_SOURCES, BENCH):
            staged = work / source
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / source, staged)
            hashes[source] = digest(staged)
        hashes.update({source: digest(ROOT / source) for source in SUPPORT_SOURCES})
        leaf_vectors, top_vectors = work / "leaf.hex", work / "top.hex"
        expected = generate_leaf_vectors(leaf_vectors)
        expected.update(generate_top_vectors(top_vectors))
        checked_run(
            [
                *command,
                "--lint-only",
                "--top-module",
                "module1_bridge_top",
                *STRICT_LINT,
                *RTL_SOURCES,
            ],
            cwd=work,
            env=env,
            log=out / "module1_bridge_top.lint.log",
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
                *RTL_SOURCES,
                BENCH,
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
            [str(binary), f"+leaf_vectors={leaf_vectors}", f"+top_vectors={top_vectors}"],
            cwd=work,
            env=env,
            log=out / "run.log",
            timeout=timeout,
        )
        checked_counts = {
            key: count for key, count in expected.items() if key != "top_steady_slots"
        }
        counts = completion(log, MARKER, tuple(checked_counts))
        if counts != checked_counts:
            raise RuntimeError(
                f"incomplete bridge RTL coverage: {counts}; expected {checked_counts}"
            )
        reported_version = re.search(r"R e p o r t: (Verilator [^\r\n]+)", log)
        result = {
            "schema_version": 1,
            "status": "PASS",
            "scope": "TASK1_DIGITAL_BRIDGE_CANDIDATE",
            "simulator": (
                reported_version.group(1).strip()
                if reported_version
                else version.splitlines()[0].strip()
            ),
            "counts": {**counts, "top_steady_slots": expected["top_steady_slots"]},
            "mismatches": 0,
            "source_sha256": hashes,
            "log_sha256": {file.name: digest(file) for file in sorted(out.glob("*.log"))},
            "vector_sha256": {
                "leaf.hex": digest(leaf_vectors),
                "top.hex": digest(top_vectors),
            },
        }
        write_json(out / "result.json", result)
        return result


# =========================================================================
# 4. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# 从指定或 PATH 上的 Verilator 执行桥接候选验证。
# ------------------------------------------------------------------------
def main() -> int:
    """从指定或 PATH 上的 Verilator 执行桥接候选验证。

    --out 必须是新证据目录，防止旧输出被当作本轮验证。
    工具缺失或失败按真实退出状态报告，不自动换成数学模型通过。
    只有实际生成完成计数才发布通过结果。
    """
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
        print(f"TASK1_BRIDGE_CHECKS_FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{MARKER}: {result['counts']} receipt={out / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
