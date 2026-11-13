"""Generate directed + constrained-random vectors for every V1 module.

Design rules (task book §12):

* this file must not contain a copy of any module's production algorithm — it
  only knows *ranges and boundaries*, never how to compute an answer;
* every vector set is reproducible from its seed;
* directed cases are named, so a failure report can say which corner broke.

Output layout::

    verification/vectors/generated/<module>/
        <field>.mem        # $readmemh input, one hex word per line
        meta.json          # seed, counts, field widths, corner names
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "verification" / "vectors" / "generated"

# 参数来自**单一真源** `verification.contracts.profile`。
# 2026-11-13 前这里有一份独立硬编码副本，与 rtl_params.vh / digital_interfaces.yaml
# 各自演进，曾静默漂移（三处一起停在旧的 63+8 上）。现在只在此处别名引用。
from verification.contracts.profile import RTL_PROFILE as PROFILE  # noqa: E402


@dataclass
class VectorSet:
    """One module's input fields plus the corner labels that produced them."""

    module: str
    seed: int
    fields: dict[str, list[int]] = field(default_factory=dict)
    corners: list[str] = field(default_factory=list)
    width_bits: dict[str, int] = field(default_factory=dict)

    def add(self, **values: int) -> None:
        """Append one vector; every field must be supplied for every vector."""
        for name, value in values.items():
            self.fields.setdefault(name, []).append(value)

    @property
    def count(self) -> int:
        """Number of vectors in the set."""
        return len(next(iter(self.fields.values())))

    def write(self) -> Path:
        """Write the $readmemh files and the meta record for this set."""
        target = OUT_ROOT / self.module
        target.mkdir(parents=True, exist_ok=True)
        for name, values in self.fields.items():
            width = self.width_bits.get(name, 64)
            nibbles = (width + 3) // 4
            lines = []
            for index, value in enumerate(values):
                # 不能静默截断：一个想表达"越界"的激励如果被掩到合法范围，
                # 这条向量就不再覆盖它声称的边界，而报告里仍会写着"已覆盖"。
                if value < 0 or value >= (1 << width):
                    raise ValueError(
                        f"{self.module}.{name}[{index}] = {value} does not fit "
                        f"{width} bits; encode signed values explicitly"
                    )
                lines.append(f"{value:0{nibbles}x}")
            (target / f"{name}.mem").write_text("\n".join(lines) + "\n", encoding="ascii")
        meta = {
            "module": self.module,
            "seed": self.seed,
            "count": self.count,
            "wildcard_mask": " ".join(f"{c}" for c in self.corners),
            "field_width_bits": self.width_bits,
            "profile": PROFILE,
        }
        (target / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return target


def _signed(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


def _rand_bits(rng: np.random.Generator, width: int) -> int:
    """Uniform random bit pattern of arbitrary width (beyond int64)."""
    n_bytes = (width + 7) // 8
    return int.from_bytes(rng.integers(0, 256, n_bytes, dtype=np.uint8).tobytes(), "little") & ((1 << width) - 1)


# --------------------------------------------------------------------------- sadc
def gen_sadc(seed: int = 20261112) -> VectorSet:
    """Comparator bit patterns: corners plus random densities.

    A thermometer with a bubble is legal input; the contract counts bits, so the
    vectors deliberately include non-monotone patterns.
    """
    rng = np.random.default_rng(seed)
    vs = VectorSet("sadc_enc", seed, width_bits={"cmp_raw": 511})
    corners = [0, 1, 0b10, (1 << 511) - 1, (1 << 510), 0x5555, 0xAAAA]
    for value, name in zip(corners, ["all_low", "one_lsb", "one_bit1", "all_high",
                                     "top_bit_only", "alternating_01", "alternating_10"], strict=True):
        vs.add(cmp_raw=value)
        vs.corners.append(name)
    for density in (0.001, 0.25, 0.5, 0.75, 0.999):
        for _ in range(24):
            raw = int.from_bytes(rng.random(64) < density, "little")
            vs.add(cmp_raw=raw & ((1 << 511) - 1))
            vs.corners.append(f"random_density_{density}")
    return vs


# ----------------------------------------------------------------------- unit_therm
def gen_unit_therm(seed: int = 20261112) -> VectorSet:
    """Ranks are fixed permutations; counts sweep including out-of-range values."""
    rng = np.random.default_rng(seed)
    n_main, n_sub = PROFILE["N_UNIT_MAIN"], PROFILE["N_UNIT_SUB"]
    rank_bits_main, rank_bits_sub = 6, 3
    vs = VectorSet(
        "unit_therm",
        seed,
        width_bits={"main_logical": n_main * rank_bits_main,
                    "sub_logical": n_sub * rank_bits_sub,
                    "main_count": 6, "sub_count": 4},
    )

    def pack_ranks(perm: np.ndarray, bits: int) -> int:
        value = 0
        for index, rank in enumerate(perm):
            value |= int(rank) << (bits * index)
        return value

    main_perms = [np.arange(n_main), np.arange(n_main)[::-1], rng.permutation(n_main)]
    sub_perms = [np.arange(n_sub), rng.permutation(n_sub)]

    # 端口位宽就是 6 位（MAIN_COUNT_BITS = clog2(63+1)），能表示的最大值是 63。
    # 之前写 64/127 并声称"越界饱和覆盖"是**假覆盖**：写 .mem 时被截成 0/63。
    # 高于数组的 count 从该端口物理上进不来，只能标为不可测。
    for perm_index, perm in enumerate(main_perms * 2):
        for count in (0, 1, 31, 32, 62, 63):
            sub_perm = sub_perms[perm_index % len(sub_perms)]
            vs.add(
                main_logical=pack_ranks(perm, rank_bits_main),
                sub_logical=pack_ranks(sub_perm, rank_bits_sub),
                main_count=count,
                sub_count=int(rng.integers(0, 15)),
            )
            vs.corners.append(f"main_count_{count}")
    for _ in range(48):
        perm = rng.permutation(n_main)
        sub_perm = rng.permutation(n_sub)
        vs.add(
            main_logical=pack_ranks(perm, rank_bits_main),
            sub_logical=pack_ranks(sub_perm, rank_bits_sub),
            main_count=int(rng.integers(0, 64)),
            sub_count=int(rng.integers(0, 15)),
        )
        vs.corners.append("random")
    return vs


# ------------------------------------------------------------------------ adc2_dec
def gen_adc2(seed: int = 20261112) -> VectorSet:
    """Bin centres over every code for several ranges, plus tie-heavy ranges."""
    rng = np.random.default_rng(seed)
    bits = PROFILE["ADC2_BITS"]
    vs = VectorSet("adc2_dec", seed, width_bits={"adc2_code": bits, "adc2_min_q": 64, "adc2_max_q": 64})
    # Small ranges make ties frequent, which is exactly where ties-to-even shows.
    ranges = [
        (-(1 << 32), 1 << 32),        # +-1.0 V/Vfs
        (-(1 << 31), (1 << 31) - 1),  # asymmetric, odd span
        (0, 1 << 33),                 # all positive
        (-(1 << 33), 0),              # all negative
        (-3, 3),                      # pathological tiny span
    ]
    for min_q, max_q in ranges:
        for code in (0, 1, 2, 3, (1 << bits) // 2, (1 << bits) - 2, (1 << bits) - 1):
            vs.add(adc2_code=code, adc2_min_q=_signed(min_q, 64), adc2_max_q=_signed(max_q, 64))
            vs.corners.append("range_boundary")
    # Exhaustive over all codes for the nominal range.
    for code in range(1 << bits):
        vs.add(adc2_code=code, adc2_min_q=_signed(-(1 << 32), 64), adc2_max_q=_signed(1 << 32, 64))
        vs.corners.append("nominal_exhaustive")
    for _ in range(64):
        min_q = int(rng.integers(-(1 << 33), 0))
        max_q = int(rng.integers(1, 1 << 33))
        vs.add(adc2_code=int(rng.integers(0, 1 << bits)),
               adc2_min_q=_signed(min_q, 64), adc2_max_q=_signed(max_q, 64))
        vs.corners.append("random_range")
    return vs


# ----------------------------------------------------------------------- div_floor
def gen_div_floor(seed: int = 20261112) -> VectorSet:
    """Signed numerator / unsigned denominator, biased to negative non-integers."""
    rng = np.random.default_rng(seed)
    width_a = 96 - 33  # ACC_BITS - (V_FRAC + 1)
    vs = VectorSet("div_floor", seed, width_bits={"a": width_a, "d": 60})
    limit = 1 << (width_a - 1)

    def add(a: int, d: int, name: str) -> None:
        vs.add(a=_signed(max(min(a, limit - 1), -limit), width_a), d=d)
        vs.corners.append(name)

    for d in (1, 2, 3, 5, 7, 1023, (1 << 59) - 1):
        for a in (0, 1, -1, d - 1, -d, -d + 1, -d - 1, 2 * d, -2 * d):
            add(a, d, "exact_and_adjacent")
    for _ in range(192):
        a = int(rng.integers(-limit, limit))
        d = int(rng.integers(1, (1 << 60) - 1))
        add(a, d, "random")
    # Denominator zero is legal input: the module must report err, not hang.
    add(12345, 0, "zero_denominator")
    add(-12345, 0, "zero_denominator_negative")
    return vs


# ------------------------------------------------------------------------- rdac_drv
def gen_rdac_drv(seed: int = 20261112) -> VectorSet:
    """Fanout vectors: normal 8-hot, duplicates, out-of-range ids, reset interleave."""
    rng = np.random.default_rng(seed)
    n_main, n_sub, n_act, n_slices = (
        PROFILE["N_UNIT_MAIN"], PROFILE["N_UNIT_SUB"], PROFILE["N_ACTIVE"], PROFILE["N_SLICES"]
    )
    vs = VectorSet("rdac_drv", seed,
                   width_bits={"slice_id": n_act * 5, "main_on": n_act * n_main,
                               "sub_on": n_act * n_sub, "load": 1, "rst_n": 1})

    def pack_ids(ids: list[int]) -> int:
        value = 0
        for lane, sid in enumerate(ids):
            value |= (sid & 0x1F) << (5 * lane)
        return value

    def pack_mask(bits: list[int]) -> int:
        value = 0
        for index, bit in enumerate(bits):
            value |= (bit & 1) << index
        return value

    def add(ids: list[int], load: int = 1, rst_n: int = 1, main: int | None = None,
            sub: int | None = None, name: str = "case") -> None:
        vs.add(slice_id=pack_ids(ids), load=load, rst_n=rst_n,
               main_on=main if main is not None else 0,
               sub_on=sub if sub is not None else 0)
        vs.corners.append(name)

    add(list(range(n_act)), main=(1 << (n_act * n_main)) - 1, sub=(1 << (n_act * n_sub)) - 1,
        name="all_active_all_on")
    add([17, 16, 15, 14, 13, 12, 11, 10], main=0x5555, sub=0x0F, name="high_ids")
    add([3, 3, 3, 3, 3, 3, 3, 3], main=(1 << 63) - 1, sub=0xFF, name="duplicate_last_wins")
    add([0, 1, 2, 3, 4, 5, 6, 31], main=1, sub=1, name="id_out_of_range_ignored")
    add([0, 1, 2, 3, 4, 5, 6, 7], load=0, main=0xF0F0, sub=0x55, name="load_low_hold")
    add([0, 1, 2, 3, 4, 5, 6, 7], load=1, rst_n=0, main=0xFFFF, sub=0xFF, name="reset_beats_load")
    add([0, 1, 2, 3, 4, 5, 6, 7], load=1, rst_n=1, main=0xFFFF, sub=0xFF, name="recover_after_reset")
    for _ in range(48):
        ids = rng.choice(n_slices + 4, size=n_act, replace=True).tolist()
        add([int(i) for i in ids], main=_rand_bits(rng, n_act * n_main),
            sub=_rand_bits(rng, n_act * n_sub), name="random")
    return vs


# ------------------------------------------------------------------- slice_pool_ctrl
def gen_slice_pool(seed: int = 20261112) -> VectorSet:
    """Advance sequences covering priming, shuffle on/off, stall and reset."""
    rng = np.random.default_rng(seed)
    n_act = PROFILE["N_ACTIVE"]
    vs = VectorSet("slice_pool_ctrl", seed,
                   width_bits={"advance": 1, "enable": 1, "shuffle": 1, "rst_n": 1, "seed": 32})

    def add(advance: int, enable: int, shuffle: int, rst_n: int, seed_value: int,
            name: str) -> None:
        vs.add(advance=advance, enable=enable, shuffle=shuffle, rst_n=rst_n, seed=seed_value)
        vs.corners.append(name)

    add(0, 1, 0, 0, 1, "reset")
    add(1, 1, 0, 1, 1, "first_advance_primes")
    for index in range(n_act * 3):
        add(1, 1, 0, 1, 1, f"shuffle_off_seq_{index}")
    for index in range(n_act * 3):
        add(1, 1, 1, 1, 0x80200003, f"shuffle_on_seq_{index}")
    for _ in range(48):
        add(int(rng.integers(0, 2)), 1, int(rng.integers(0, 2)), 1,
            int(rng.integers(1, 1 << 32)), "random_stall")
    add(1, 0, 1, 1, 7, "enable_low")
    add(1, 1, 1, 0, 7, "reset_in_middle")
    return vs


# ------------------------------------------------------------------------ recon_core
def gen_recon_core(seed: int = 20261112) -> VectorSet:
    """Full reconstruction vectors with an explicit Q30 weight ROM.

    Weights are drawn so that ``gain`` stays positive (the contract's structural
    requirement) while ``rails`` can be either sign. Two ROMs are emitted: a
    nominal all-ones ROM and a mismatched random ROM.
    """
    rng = np.random.default_rng(seed)
    n_act, n_main, n_sub, n_slices = (
        PROFILE["N_ACTIVE"], PROFILE["N_UNIT_MAIN"], PROFILE["N_UNIT_SUB"], PROFILE["N_SLICES"]
    )
    n_units = n_main + n_sub
    vs = VectorSet("recon_core", seed,
                   width_bits={"slice_id": n_act * 5, "main_on": n_act * n_main,
                               "sub_on": n_act * n_sub, "adc2_code": PROFILE["ADC2_BITS"],
                               "inj_q": 64, "offset_q": 64, "adc2_min_q": 64,
                               "adc2_max_q": 64, "sample_id": 32})
    rom = rng.integers(1 << 29, (1 << 30) + (1 << 28), size=(n_slices, n_units), dtype=np.int64)
    vs.extra = {"w_rom": rom}  # type: ignore[attr-defined]

    def pack(values: list[int], bits: int) -> int:
        acc = 0
        for index, value in enumerate(values):
            acc |= (value & ((1 << bits) - 1)) << (bits * index)
        return acc

    min_q, max_q = -(1 << 32), 1 << 32
    for index in range(96):
        ids = [int(v) for v in rng.permutation(n_slices)[:n_act]]
        main = [int(v) for v in rng.integers(0, 2, n_act * n_main)]
        sub = [int(v) for v in rng.integers(0, 2, n_act * n_sub)]
        # Keep at least one unit on per slice so gain stays positive.
        for lane in range(n_act):
            main[lane * n_main] = 1
        vs.add(slice_id=pack(ids, 5), main_on=pack(main, 1), sub_on=pack(sub, 1),
               adc2_code=int(rng.integers(0, 1 << PROFILE["ADC2_BITS"])),
               inj_q=_signed(int(rng.integers(-(1 << 31), 1 << 31)), 64),
               offset_q=_signed(int(rng.integers(-(1 << 20), 1 << 20)), 64),
               adc2_min_q=_signed(min_q, 64), adc2_max_q=_signed(max_q, 64),
               sample_id=index)
        vs.corners.append("random")

    # Corner: single unit on, injection at the extremes, offset nearly cancelling.
    corners = [
        ("single_unit_min_code", 0, 0),
        ("single_unit_max_code", (1 << PROFILE["ADC2_BITS"]) - 1, 0),
        ("mid_code_zero_inj", 1 << (PROFILE["ADC2_BITS"] - 1), 0),
        ("mid_code_max_inj", 1 << (PROFILE["ADC2_BITS"] - 1), (1 << 31) - 1),
        ("mid_code_min_inj", 1 << (PROFILE["ADC2_BITS"] - 1), -(1 << 31)),
    ]
    for name, code, inj in corners:
        ids = list(range(n_act))
        main = [0] * (n_act * n_main)
        sub = [0] * (n_act * n_sub)
        for lane in range(n_act):
            main[lane * n_main] = 1
        vs.add(slice_id=pack(ids, 5), main_on=pack(main, 1), sub_on=pack(sub, 1),
               adc2_code=code, inj_q=_signed(inj, 64), offset_q=0,
               adc2_min_q=_signed(min_q, 64), adc2_max_q=_signed(max_q, 64),
               sample_id=1000 + len(vs.corners))
        vs.corners.append(name)
    return vs


GENERATORS = {
    "sadc_enc": gen_sadc,
    "unit_therm": gen_unit_therm,
    "adc2_dec": gen_adc2,
    "div_floor": gen_div_floor,
    "rdac_drv": gen_rdac_drv,
    "slice_pool_ctrl": gen_slice_pool,
    "recon_core": gen_recon_core,
}


def write_all(seed: int = 20261112) -> dict[str, int]:
    """Generate every module's vector set and return per-module vector counts."""
    counts = {}
    for name, generator in GENERATORS.items():
        vector_set = generator(seed)
        target = vector_set.write()
        # recon_core additionally ships its weight ROM as a flat $readmemh file.
        rom = getattr(vector_set, "extra", {}).get("w_rom")
        if rom is not None:
            flat = "\n".join(f"{int(value):012x}" for value in rom.reshape(-1))
            (target / "w_rom.mem").write_text(flat + "\n", encoding="ascii")
        counts[name] = vector_set.count
    return counts


if __name__ == "__main__":
    print(json.dumps(write_all(), indent=2))
