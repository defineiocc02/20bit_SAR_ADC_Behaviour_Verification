# =============================================================================
# 文件名     : crc24.py
# 模块名     : CRC24 有限假设分析
# 版本       : v1.0
# 功能       : 按明示的覆盖和序列模型，检验多项式并求 init/xorout 的仿射解。
# 设计说明   : 同长记录差分消去初值和最终异或，作为多项式约束。训练解的
#              自由度单独保存，留出帧可拒绝解，也可能只提供新的拟合约束。
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""CRC 宽度固定为 24。这里的模型覆盖范围和位序是待检验假设。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from capture import read_json, uint


# =============================================================================
# 1. 假设空间与线上表示
# =============================================================================
WIDTH = 24
MASK = (1 << WIDTH) - 1
TOP = 1 << (WIDTH - 1)
MONIC = 1 << WIDTH
# 商的位数超过此界时保留 GCD 约束，避免把搜索成本限制误报为排除多项式。
MAX_QUOTIENT_DEGREE = 12


@dataclass(frozen=True)
class Profile:
    name: str
    polys: tuple[int, ...] | None
    coverage: str
    packing: str
    refin: bool
    refout: bool
    wire_order: str
    augment_bits: int
    init: int | None
    xorout: int | None

    @property
    def variables(self):
        # 每块未知寄存器/异或掩码占连续 24 个变量，低位到高位编号。
        return WIDTH * ((self.init is None) + (self.xorout is None))


def polynomial(value):
    # 用规范的正向系数表示，不要求使用者借 reflected 实现中的倒数系数猜位序。
    poly = uint(value, WIDTH, "poly")
    if not poly & 1:
        raise ValueError("poly 需要以 1 为常数项，省略最高次 x^24")
    return poly


def profile_from(item):
    raw = item["poly"]
    if raw == "derive":
        polys = None
    elif isinstance(raw, list) and raw:
        # 同一 profile 中的重复多项式只跑一次，避免重复项把结果伪装成多解。
        polys = tuple(dict.fromkeys(polynomial(value) for value in raw))
    else:
        raise ValueError("poly 需要非空多项式列表或 derive")
    name = item["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile 缺少 name")
    coverage, packing, order = item["coverage"], item["packing"], item["wire_order"]
    if coverage not in ("payload", "header_payload", "last_sample"):
        raise ValueError("coverage 无效")
    if packing not in ("raw20", "zero_extend24", "sign_extend24", "pad_burst8"):
        raise ValueError("packing 无效")
    if order not in ("msb", "lsb", "byte_reverse", "lsb_per_byte"):
        raise ValueError("wire_order 无效")
    # 反射、增广和 init/xorout 必须显式声明，避免把缺列解析成一组貌似唯一的默认值。
    refin, refout, augment = item["refin"], item["refout"], item["augment_bits"]
    if type(refin) is not bool or type(refout) is not bool or type(augment) is not int or augment not in (0, 24):
        raise ValueError("refin、refout 应为布尔值，augment_bits 为 0 或 24")
    init, xorout = (None if item[key] is None else uint(item[key], WIDTH, key) for key in ("init", "xorout"))
    return Profile(name, polys, coverage, packing, refin, refout, order, augment, init, xorout)


def load_profiles(path):
    # 待检验域和捕获清单分开留身份，保留改假设后重跑的依据。
    data, digest = read_json(path)
    if data.get("format") != "ad4080-crc24-hypotheses-v1":
        raise ValueError("假设文件 format 需要 ad4080-crc24-hypotheses-v1")
    items = data.get("profiles")
    if not isinstance(items, list) or not items:
        raise ValueError("profiles 需要非空列表")
    profiles = [profile_from(item) for item in items]
    if len({profile.name for profile in profiles}) != len(profiles):
        raise ValueError("profile 名称需要唯一")
    return profiles, digest


def reverse_octets(bits):
    # 最后一段若小于 8 位，反转这段实际位。其它补齐假设由 packing 显式表达。
    return "".join(bits[i:i + 8][::-1] for i in range(0, len(bits), 8))


def message_bits(record, profile):
    words = record.words[-1:] if profile.coverage == "last_sample" else record.words
    packing = profile.packing
    if packing in ("raw20", "pad_burst8"):
        payload = "".join(f"{word:020b}" for word in words)
        if packing == "pad_burst8":
            payload += "0" * (-len(payload) % 8)
    else:
        # 对 24 位有两种显式候选，便于检验“按原始 20 位计算”和“补齐再算”。
        add = 0xF00000 if packing == "sign_extend24" else 0
        payload = "".join(f"{(word | add if word & 0x80000 else word):024b}" for word in words)
    # 先合并字段再按八位组反射，固定头因此从净字节边界开始，范围假设可准确复算。
    message = ("10101010" if profile.coverage == "header_payload" else "") + payload
    if profile.refin:
        message = reverse_octets(message)
    # 增广位在输入排线完成后加入；它们同时影响初值推进长度。
    return message + "0" * profile.augment_bits


def wire_reorder(bits, order):
    # 这四种变换都是对合，正向编码和从线解码使用同一排列。
    if order == "lsb":
        return bits[::-1]
    if order == "byte_reverse":
        return bits[16:24] + bits[8:16] + bits[:8]
    if order == "lsb_per_byte":
        return reverse_octets(bits)
    return bits


def reflect(value):
    return int(f"{value:024b}"[::-1], 2)


# =============================================================================
# 2. 由同长记录得到多项式约束
# =============================================================================
def gf2_divide(dividend, divisor):
    # 整数的位代表多项式系数，减法以 XOR 完成，与整数除法余数的含义不同。
    quotient = 0
    while dividend.bit_length() >= divisor.bit_length():
        shift = dividend.bit_length() - divisor.bit_length()
        quotient ^= 1 << shift
        dividend ^= divisor << shift
    return quotient, dividend


def gf2_gcd(a, b):
    while b:
        a, b = b, gf2_divide(a, b)[1]
    return a


def derive(frame_data, refout):
    # 同长度的两条记录，初值的推进量及 xorout 相同。异或后，24 次多项式
    # 必须整除 (两条消息异或左移 24 位) 异或 (还原反序后的尾值差)。
    reference = {}
    common = equations = 0
    for message, value in frame_data:
        residue = reflect(value) if refout else value
        observed = int(message, 2), residue
        first = reference.setdefault(len(message), observed)
        delta = ((first[0] ^ observed[0]) << WIDTH) ^ (first[1] ^ observed[1])
        if delta:
            equations += 1
            common = gf2_gcd(common, delta)
            # 一旦最高次数小于 24，后续求公因式也只会进一步降低次数。
            if common.bit_length() <= WIDTH:
                break
    if not common:
        return (), {"status": "no_different_equal_length_training_frames", "complete": False}
    # 真正候选的常数项为 1，公共约束中的 x 因子可以安全去掉。
    common >>= (common & -common).bit_length() - 1
    degree = common.bit_length() - 1
    info = {"gcd": hex(common), "degree": degree, "nonzero_differences_used": equations}
    extra = degree - WIDTH
    if extra < 0:
        return (), {**info, "status": "no_degree24_divisor", "complete": True}
    if extra > MAX_QUOTIENT_DEGREE:
        return (), {**info, "status": "gcd_too_large_supply_more_frames_or_polys", "complete": False}
    # 枚举 GCD 与候选多项式相除后的商。商为首末系数均 1 的低次多项式。
    quotients = (1,) if extra == 0 else range((1 << extra) | 1, 1 << (extra + 1), 2)
    candidates = []
    for quotient in quotients:
        divisor, rest = gf2_divide(common, quotient)
        if not rest:
            candidates.append(divisor & MASK)
    return tuple(sorted(candidates)), {**info, "status": "enumerated_degree24_divisors", "complete": True}


# =============================================================================
# 3. 固定多项式下的仿射解与留出校验
# =============================================================================
def step_zero(state, poly):
    return ((state << 1) & MASK) ^ (poly if state & TOP else 0)


def feed(bits, poly, state=0):
    """按指定时间位序推进直接式 MSB LFSR，输入位进入反馈异或。"""
    # 按单个位推进保留 20N 非整字节在最后一段的原始位数。
    for bit in bits:
        state = step_zero(state ^ (TOP if bit == "1" else 0), poly)
    return state


def matrix_apply(columns, value):
    output = 0
    while value:
        lowest = value & -value
        output ^= columns[lowest.bit_length() - 1]
        value ^= lowest
    return output


@lru_cache(maxsize=256)
def zero_shift(poly, length):
    # 按平方合并 GF(2) 线性变换，避免一个长读突发重复推进 24 次初值列。
    accumulated = tuple(1 << bit for bit in range(WIDTH))
    power = tuple(step_zero(column, poly) for column in accumulated)
    while length:
        if length & 1:
            accumulated = tuple(matrix_apply(power, column) for column in accumulated)
        length >>= 1
        if length:
            power = tuple(matrix_apply(power, column) for column in power)
    return accumulated


def affine_rows(profile, poly, message, observed):
    # 目标等于实测尾异或已知响应。初始化的线性项随参与计算的位长度而变化。
    calculated = feed(message, poly, profile.init or 0)
    if profile.refout:
        calculated = reflect(calculated)
    target = observed ^ calculated ^ (profile.xorout or 0)
    columns = ()
    if profile.init is None:
        columns = zero_shift(poly, len(message))
        if profile.refout:
            columns = tuple(reflect(value) for value in columns)
    # xorout 按输出数值顺序与余数异或，所以它的列已在输出域，不再做 refout。
    if profile.xorout is None:
        columns += tuple(1 << bit for bit in range(WIDTH))
    for bit in range(WIDTH):
        coeff = sum(((column >> bit) & 1) << i for i, column in enumerate(columns))
        yield coeff, (target >> bit) & 1


def parity(value):
    # 使用 bin 兼容系统 Python 3.9。最多 48 位，和数据框长无关。
    return bin(value).count("1") & 1


def solve(rows):
    """最高置位作为消元主元，返回自由变量取零的一组解及秩。"""
    pivots = {}
    for coeff, target in rows:
        while coeff:
            column = coeff.bit_length() - 1
            if column not in pivots:
                pivots[column] = coeff, target
                break
            reduced, rhs = pivots[column]
            coeff ^= reduced
            target ^= rhs
        else:
            # 方程退化为 0=1 说明模型与已选数据矛盾。0=0 只是冗余条件。
            if target:
                return None
    # 每一行的其它非零位低于主元，依低到高回代得到一组代表解。
    solution = 0
    for column, (coeff, target) in sorted(pivots.items()):
        solution |= (target ^ parity(coeff & solution)) << column
    return solution, len(pivots)


def solution_info(profile, solution):
    # 自由变量取零只是一种坐标选择。对外同时给出自由度，不宣称这就是芯片种子。
    bits, rank = solution
    init = bits & MASK if profile.init is None else profile.init
    shift = WIDTH if profile.init is None else 0
    xorout = (bits >> shift) & MASK if profile.xorout is None else profile.xorout
    return {"rank": rank, "free_degrees": profile.variables - rank,
            "representative_init": f"0x{init:06X}", "representative_xorout": f"0x{xorout:06X}"}


def fit_one(profile, poly, training, holdout):
    train_rows = [row for message, observed in training for row in affine_rows(profile, poly, message, observed)]
    trained = solve(train_rows)
    info = {"poly": f"0x{poly:06X}", "unknown_bits": profile.variables}
    if trained is None:
        return {**info, "status": "train_rejected"}
    info["training"] = solution_info(profile, trained)
    if not holdout:
        return {**info, "status": "no_distinct_holdout", "remaining": info["training"]}
    # 留出记录先用于检验训练仿射空间。增秩表示它补充了拟合条件，不能计为预测命中。
    extra_rows = [row for message, observed in holdout for row in affine_rows(profile, poly, message, observed)]
    combined = solve(train_rows + extra_rows)
    if combined is None:
        return {**info, "status": "holdout_rejected"}
    # 同秩且一致意味着训练空间中的每组解对本次留出给出相同预测。
    status = "holdout_constrained" if combined[1] > trained[1] else "holdout_pass"
    return {**info, "status": status, "remaining": solution_info(profile, combined)}


def fit_profiles(records, profiles):
    # Analog Devices 图 89 的尾可以定位。SPI 图示范围之外的数字不计入 CRC 模型。
    train = [record for record in records if record.mode == "lvds" and record.split == "train"]
    holdout = [record for record in records if record.mode == "lvds" and record.split == "holdout"]
    train_messages = {record.words for record in train}
    repeats = [record.record_id for record in holdout if record.words in train_messages]
    results = []
    for profile in profiles:
        def observation(record):
            if record.crc_wire is None:
                raise ValueError("LVDS 记录需要可见的完整 24 位尾部")
            return message_bits(record, profile), int(wire_reorder(record.crc_wire, profile.wire_order), 2)

        training = list(map(observation, train))
        training_bits = {message for message, _ in training}
        # 例如只覆盖末样本的假设可能把两条不同原记录压成相同模型消息。内容重复后缀
        # 仍可发现矛盾，但同样的 message+tail 组合不作为独立预测计数。
        duplicates = set(training)
        held = [observation(record) for record in holdout]
        held = list(dict.fromkeys(item for item in held if item not in duplicates))
        if not training:
            candidates, derivation = (), {"status": "no_lvds_training_frames", "complete": False}
        elif profile.polys is None:
            # 多项式发现也隔离在训练集；留出只做预测和矛盾检查。
            candidates, derivation = derive(training, profile.refout)
        else:
            candidates, derivation = profile.polys, {"status": "explicit_finite_list", "complete": True}
        matches = [fit_one(profile, poly, training, held) for poly in candidates]
        distinct_holdout = sum(message not in training_bits for message, _ in held)
        results.append({"name": profile.name, "coverage": profile.coverage, "packing": profile.packing,
                        "refin": profile.refin, "refout": profile.refout, "wire_order": profile.wire_order,
                        "declared_init": None if profile.init is None else f"0x{profile.init:06X}",
                        "declared_xorout": None if profile.xorout is None else f"0x{profile.xorout:06X}",
                        "augment_bits": profile.augment_bits, "derivation": derivation,
                        "distinct_holdout_messages": distinct_holdout, "models": matches})
    counts = {"train": len(train), "holdout": len(holdout), "holdout_repeats_training": repeats,
              "train_watermarks": sorted({len(record.words) for record in train}),
              "holdout_watermarks": sorted({len(record.words) for record in holdout})}
    return results, counts
