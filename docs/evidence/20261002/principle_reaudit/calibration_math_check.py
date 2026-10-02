"""Bounded exact-integer/Fraction checks; no model output or analog truth is read.

The small-width divider section is an independent algorithm calculation, not an
RTL simulation or formal proof. The companion two benches test actual RTL.
"""
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import copy
import json
import random

HERE = Path(__file__).resolve().parent
REPO = Path('/Users/zhaoyi/Obsidan/项目库/outputs/sar_adc_review_20260919/calibration-closure')


def increment_rte(n, k):
    q, r = divmod(n, 1 << k)
    half = 1 << (k - 1)
    return q + int(r > half or (r == half and (q & 1)))


def grouped_floor(a, d, wa, wd, stages):
    cycles = (wa + stages - 1) // stages
    pad = cycles * stages
    magnitude, rem, quo = abs(a), 0, 0
    large = (d >> wa) != 0
    dv = d & ((1 << wa) - 1)
    for _ in range(cycles):
        qbits = 0
        for s in range(stages):
            shifted = (rem << 1) | ((magnitude >> (pad - 1 - s)) & 1)
            assert shifted < (1 << wa), (a, d, wa, wd, stages)
            take = not large and shifted >= dv
            rem = shifted - dv if take else shifted
            qbits |= int(take) << (stages - 1 - s)
        quo = (quo << stages) | qbits
        magnitude = (magnitude << stages) & ((1 << pad) - 1)
    return -(quo + int(rem != 0)) if a < 0 else quo


def main():
    checks = {}
    adc_cases, differing = 0, 0
    ranges = [(-53687091, 590558003), (-(1 << 63), (1 << 63) - 1),
              (1, 4097), (0, 4096), (-4097, -1), (0, 1)]
    for low, high in ranges:
        for code in range(4096):
            n = (2 * code + 1) * (high - low)
            current = low + increment_rte(n, 13)
            contract = low + round(Fraction(n, 8192))
            physical_center = round(Fraction(low) + Fraction(n, 8192))
            assert current == contract and low <= current <= high
            assert abs(current - physical_center) <= 1
            differing += current != physical_center
            adc_cases += 1
    checks['adc_fraction_contract'] = {'cases': adc_cases, 'contract_matches': True,
                                     'absolute_center_rte_differences': differing}
    rng = random.Random(20261002)
    direct_cases = 5000
    for _ in range(direct_cases):
        gain = rng.randint(1, (1 << 40) - 1)
        numerator = rng.randrange(-(1 << 70), (1 << 70))
        shifted = (numerator + gain * (1 << 32)) * (1 << 20)
        direct = Fraction(shifted, gain * (1 << 33)).__floor__()
        decomposed = (shifted // (1 << 33)) // gain
        assert direct == decomposed
    checks['nested_floor_identity'] = {'cases': direct_cases, 'matches': True}
    checks['conditional_coefficient_quantization_bound'] = {
        'scope': 'CONTINUOUS_CODE_BOUND_NOT_FLOOR_BIT_EXACT_NOT_TRAINING_ERROR',
        'conditions': {'terms_max': 568, 'signal_plus_rail_abs_max': 2,
                       'gain_min': 32, 'injection': 0, 'code_scale': 1 << 19},
        'bound_lsb20_by_wfrac': {
            str(frac): float(Fraction(568 * 2 * (1 << 19), 32 * (1 << (frac + 1))))
            for frac in [30, 26, 22]},
        'sampling_nominal_gain': 31.75,
        'sampling_q30_bound_lsb20': float(Fraction(568 * 2 * (1 << 19), 1 << 31) / Fraction(127, 4))}
    count = 0
    for wa in range(2, 7):
        for wd in range(1, 9):
            for stages in (1, 3, 7):
                for a in range(-(1 << (wa - 1)), 1 << (wa - 1)):
                    for d in range(1, 1 << wd):
                        assert grouped_floor(a, d, wa, wd, stages) == a // d
                        count += 1
    checks['small_width_grouped_divider_algorithm'] = {
        'cases': count, 'matches': True, 'scope': 'CALCULATION_NOT_RTL_NOT_FORMAL',
        'wa': [2, 6], 'wd': [1, 8], 'stages': [1, 3, 7]}
    baseline = json.loads((REPO / 'sim/vectors/registers_paper_literal.json').read_text())
    mirrors = {}
    for name, scale in [('nominal', 1), ('scaled_2pow20', 1 << 20)]:
        mirror = copy.deepcopy(baseline)
        mirror['offset_q'] = 26215  # Current code 341 decodes to 26215 with the original backend range.
        mirror['weights_q'] = [[w * scale for w in row] for row in mirror['weights_q']]
        coeffs = sum(mirror['weights_q'], [])
        gain = sum(sum(row) for row in mirror['weights_q'][:8])
        rails = gain - 2 * sum(sum(row[:32]) for row in mirror['weights_q'][:8])
        assert len(coeffs) == 1278 and all(0 < w < (1 << 47) for w in coeffs)
        assert sum(coeffs) < (1 << 60) and rails == 0
        shifted = gain * (1 << 52)
        ovf = not -(1 << 95) <= shifted < (1 << 95)
        assert ovf == (scale != 1)
        path = HERE / f'calibration_domain_{name}_mirror.json'
        path.write_text(json.dumps(mirror, ensure_ascii=False, indent=2) + '\n')
        mirrors[name] = {'path': path.name, 'sha256': sha256(path.read_bytes()).hexdigest(),
                         'coefficient_count': len(coeffs), 'coefficient_max': max(coeffs),
                         'load_range_legal': True, 'whole_store_sum': sum(coeffs),
                         'slice_ids': list(range(8)), 'main_on_each': list(range(32)), 'sub_on_each': [],
                         'sampling': False, 'injection_q': 0, 'adc2_code': 341,
                         'fine_q': 26215, 'offset_q': 26215, 'gain': gain, 'rails': rails,
                         'shifted': shifted, 'expected_mac_acc_ovf': ovf,
                         'scope': 'MIRROR_LOAD_PREDICATE_AND_MAC_CALCULATION_NOT_FULL_TOP_RUN'}
    checks['domain_mirrors'] = mirrors
    checks['rtl_source_identity'] = {p: sha256((REPO / p).read_bytes()).hexdigest()
                                   for p in ['rtl/core/cal_residue_mac.sv', 'rtl/core/adc2_dec.sv',
                                             'rtl/core/weight_store.sv', 'rtl/core/calib_regs.sv',
                                             'rtl/core/div_floor.sv', 'rtl/params/rtl_params.vh']}
    result = HERE / 'calibration_math_result.json'
    result.write_text(json.dumps(checks, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
