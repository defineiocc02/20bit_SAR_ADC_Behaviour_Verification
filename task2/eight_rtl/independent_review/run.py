#!/usr/bin/env python3
"""补充八片扰动事务的独立随机核对。"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from task2.eight_rtl.tools import run_checks as rtl  # noqa: E402

SEED = 0x2167
CYCLES = 20_000
DEFAULT_STATE = 0x5A1709C3


def _generate_word(state: int) -> tuple[int, int]:
    """按时序逐位推进 PRBS31，返回状态和由 D6 至 D1 排列的字。"""
    word = 0
    for _ in range(6):
        feedback = ((state >> 30) ^ (state >> 27)) & 1
        state = (2 * state + feedback) & 0x7FFFFFFF
        word = 2 * word + feedback
    return state, word


def _sample_mask(word: int) -> int:
    """计算八片原生子电容的采样高参考掩码。"""
    base, count = divmod(word, 8)
    mask = 0
    for slot in range(8):
        level = base + (slot < count)
        for bit, weight in enumerate((8, 4, 2, 1)):
            if level & weight:
                mask |= 1 << (5 * slot + bit)
    return mask


def _make_stimulus(
    rng: random.Random,
    cycle: int,
    latest_group: tuple[int, int] | None,
    conv_group: tuple[int, int] | None,
) -> rtl.Stimulus:
    """混合动态使能、资格不足、种子冲突、近满幅粗码及错误 ID。"""
    rst_n = cycle != 0 and rng.randrange(1000) != 0
    clear = rng.randrange(200) == 0
    enable = rng.randrange(173) != 0
    advance = rng.random() < 0.83
    seed_load = rng.random() < 0.19
    if seed_load and rng.random() < 0.5:
        advance = False
    seeds = tuple(rng.randrange(1, 2**w) if rng.random() > 0.12 else 0 for w in (32, 32, 31))
    dither_enable = bool(rng.getrandbits(1))
    eligible = 0x3FFFF if rng.random() < 0.8 else rng.randrange(1 << 18)
    code = rng.choice([0, 1, 506, 507, 508, 509, 510, 511, rng.randrange(512)])
    code_valid = bool(rng.getrandbits(1))
    group = latest_group if rng.random() < 0.65 else conv_group
    sample_id = (group or (0, 0))[0] + (16 if rng.random() < 0.1 else 0)
    return rtl.Stimulus(
        rst_n=rst_n,
        clear=clear,
        enable=enable,
        advance=advance,
        eligible=eligible,
        shuffle=bool(rng.getrandbits(1)),
        dem_enable=bool(rng.getrandbits(1)),
        bridge_enable=bool(rng.getrandbits(1)),
        dither_enable=dither_enable,
        seed_load=seed_load,
        slice_seed=seeds[0],
        dem_seed=seeds[1],
        dither_seed=seeds[2],
        code_valid=code_valid,
        code=code,
        code_sample_id=sample_id,
    )


def write_vectors(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """按请求时间和完整 ID 账本核对预期向量，随后供真实 RTL 对拍。"""
    rng = random.Random(SEED)
    oracle = rtl.Oracle()
    counts = dict.fromkeys(
        ("cycles", "acq_words", "valid_commands", "code_errors", "stalls", "seed_errors"), 0
    )
    coverage = dict.fromkeys(
        (
            "clear",
            "disable",
            "reset",
            "word_0",
            "word_63",
            "fresh_in_conversion",
            "prev_in_conversion",
            "valid_reseed",
            "overflow",
        ),
        0,
    )
    pending_acq: dict[int, tuple[int, int, bool]] = {}
    pending_seed: dict[int, int] = {}
    pending_error: set[int] = set()
    history: dict[int, int] = {}
    latest_group = conv_group = None
    last_descriptor = (0, 0, 0)
    state = DEFAULT_STATE
    previous_enable = False
    with path.open("w", encoding="ascii") as stream:
        for cycle in range(CYCLES):
            s = _make_stimulus(rng, cycle, latest_group, conv_group)
            active = s.rst_n and not s.clear and s.enable
            if not active:
                pending_acq.clear()
                pending_seed.clear()
                pending_error.clear()
                history.clear()
                latest_group = conv_group = None
                state = DEFAULT_STATE
                last_descriptor = (0, 0, 0)
                coverage["clear"] += s.clear
                coverage["disable"] += not s.enable
                coverage["reset"] += not s.rst_n

            fields = oracle.step(s)
            observed = tuple(value for value, _ in fields)
            descriptor = pending_acq.pop(cycle, None)
            pulse = descriptor is not None
            if cycle in pending_seed:
                assert not pulse
                state = pending_seed.pop(cycle)
                coverage["valid_reseed"] += 1
            if descriptor is not None:
                sample_id, ids, dither_enabled = descriptor
                word = 0
                if dither_enabled:
                    state, word = _generate_word(state)
                last_descriptor = (sample_id, ids, word)
                history[sample_id] = word
                coverage["word_0"] += word == 0
                coverage["word_63"] += word == 63

            assert observed[2] == (cycle in pending_error)
            pending_error.discard(cycle)
            assert observed[3] == pulse and observed[4:7] == last_descriptor
            last_word = last_descriptor[2]
            assert observed[7] == 16 * ((last_word >> 4) & 1) + last_word % 8
            assert observed[8] == _sample_mask(last_word)

            if observed[0]:
                assert oracle.acq_event is not None
                sample_id, ids, _ = oracle.acq_event
                conv_group = latest_group
                latest_group = (sample_id, rtl._pack_slots(ids, 5))
                pending_acq[cycle + 1] = (*latest_group, previous_enable)

            ready = conv_group is not None and conv_group[0] in history
            assert observed[9] == ready
            word = 0
            if ready:
                assert conv_group is not None and observed[10:12] == conv_group
                word = history[conv_group[0]]
                coverage["fresh_in_conversion"] += pulse and conv_group[0] == last_descriptor[0]
                coverage["prev_in_conversion"] += conv_group[0] != last_descriptor[0]
            assert observed[12:15] == (word, word, _sample_mask(word))
            command = s.code + 4 * (word >> 5) + ((word >> 3) & 1)
            matching_code = ready and s.code_valid and observed[10] == s.code_sample_id
            valid = matching_code and command < 512
            assert observed[15] == (command if valid else 0) and observed[19] == valid
            assert observed[20] == (active and s.code_valid and not valid)
            assert valid or observed[16:19] == (0, 0, 0)
            coverage["overflow"] += matching_code and command >= 512

            if s.seed_load and active:
                if s.advance or not all((s.slice_seed, s.dem_seed, s.dither_seed)):
                    pending_error.add(cycle + 1)
                else:
                    pending_seed[cycle + 2] = s.dither_seed
            previous_enable = s.dither_enable
            record = (*s.fields(), *fields)
            digits = (sum(width for _, width in record) + 3) // 4
            stream.write(f"{rtl._pack(record):0{digits}x}\n")
            counts["cycles"] += 1
            for name, index in (
                ("acq_words", 3),
                ("valid_commands", 19),
                ("code_errors", 20),
                ("stalls", 1),
                ("seed_errors", 2),
            ):
                counts[name] += observed[index]
    if not all(counts.values()) or not all(coverage.values()):
        raise RuntimeError("independent random review missed a required event")
    return counts, coverage


def main() -> None:
    """使用已配置的 Verilator 执行并写出小型可核验收据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = rtl.new_output(args.out)
    with patch.object(rtl, "write_vectors", write_vectors):
        result = rtl.run(output, rtl.command_for("verilator"), 180)
    script = Path(__file__).resolve()
    result["schema"] = "dither8-independent-random-review-v1"
    result["stimulus_seed"] = SEED
    result["source_sha256"][str(script.relative_to(ROOT))] = rtl.digest(script)
    del result["log_sha256"]
    rtl.write_json(output / "receipt.json", result)
    print(result["counts"], result["coverage"])


if __name__ == "__main__":
    main()
