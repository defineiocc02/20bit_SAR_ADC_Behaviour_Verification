#!/usr/bin/env python3
"""Source-bound SAR structure estimates; never estimates gates, area or Fmax.

Run with --repo and --out. Counts are hand-reviewed source formulas, not HDL
elaboration. Source anchors and frozen profile are checked before arithmetic.
Historical Vivado readings remain explicitly attached to their source revision.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import subprocess
from pathlib import Path


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clog2(value: int) -> int:
    return (value - 1).bit_length()


def tree(n: int) -> dict:
    leaves = 1 << clog2(n)
    return {
        "nonzero_source_leaves": n,
        "padded_leaves": leaves,
        "declared_branch_expressions": leaves - 1,
        "topology_depth": clog2(leaves),
        "two_live_input_combines_after_padding_only": n - 1,
        "scope": "Source graph and constant-zero padding only; not mapped cell count",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    repo = args.repo.resolve()
    filelist = (repo / "rtl/rtl_sources.f").read_text().splitlines()
    names = [p.strip() for p in filelist if p.strip()]
    if len(names) != 24 or len(set(names)) != 24:
        raise ValueError("Expected frozen 24 production SV files")
    names += ["rtl/params/rtl_params.vh", "rtl/params/rtl_error_codes.vh"]
    params_text = (repo / "rtl/params/rtl_params.vh").read_text()
    params = {}
    for name in ["N_SLICES", "N_ACTIVE", "N_UNIT_MAIN", "N_UNIT_SUB", "W_BITS", "V_BITS", "ACC_BITS", "OUT_BITS", "V_FRAC", "W_FRAC", "ADC2_BITS", "DITHER_UNITS_RANGE", "PHASES"]:
        match = re.search(r"localparam\s+(?:\[[^]]+\]\s+)?" + name + r"\s*=\s*\d+'d(\d+)\s*;", params_text)
        if not match:
            raise ValueError(f"Unrecognized decimal parameter: {name}")
        params[name] = int(match.group(1))
    expected = dict(N_SLICES=18, N_ACTIVE=8, N_UNIT_MAIN=63, N_UNIT_SUB=8,
                    W_BITS=48, V_BITS=64, ACC_BITS=96, OUT_BITS=20, V_FRAC=32,
                    W_FRAC=30, ADC2_BITS=12, DITHER_UNITS_RANGE=2, PHASES=16)
    if params != expected:
        raise ValueError("Profile changed: review every manually derived formula")
    anchors = {
        "rtl/core/weight_store.sv": ["47 + $clog2(P_N_UNITS)", "old_by_slice[s] = w_q[s][u_idx]", "wr_data & (W_MAX - W_BITS'(1))"],
        "rtl/core/cal_weight_reduce.sv": ["1 << $clog2(N_TERMS)", "physical_on[s] |= {sub_on[a], main_on[a]}", "on_tree[t] = on_tree[2 * t] + on_tree[2 * t + 1]"],
        "rtl/core/div_floor.sv": ["localparam int W_R = P_W_A", "trial_difference = {1'b0, shifted} - {1'b0, dv}"],
        "rtl/core/cal_sample_context.sv": ["if (quiet_sample)", "fine_id, fine_slices, fine_main, fine_sub, fine_rails, fine_sampling, fine_injection"],
        "rtl/core/sar_structural_ctrl.sv": ["assign recon_start = enable && phase == 15", "if (phase == 14)"],
        "rtl/top/sar20_digital_core.sv": ["parameter bit P_STRUCTURAL = 1", "parameter int P_RECON_STAGES = 7", ".P_USE_ROW_TOTALS(1)"],
    }
    refs = {}
    for name, fragments in anchors.items():
        lines = (repo / name).read_text().splitlines()
        refs[name] = []
        for fragment in fragments:
            matches = [i + 1 for i, line in enumerate(lines) if fragment in line]
            if len(matches) != 1:
                raise ValueError(f"Source anchor not unique: {name}: {fragment}")
            refs[name].append({"fragment": fragment, "line": matches[0]})
    spec = importlib.util.spec_from_file_location("frozen_lexer", repo / "tools/audit_rtl_readability.py")
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load repository finite SV token checker")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    baseline = "71e7d5a017246340b9fa1f71cbbe99e20b6503f6"
    inventory = []
    token_same = True
    for name in names:
        current = (repo / name).read_bytes()
        old = subprocess.run(["git", "show", f"{baseline}:{name}"], cwd=repo, capture_output=True, check=True).stdout
        same = checker.tokens(current.decode()) == checker.tokens(old.decode())
        token_same &= same
        # Anchor a legal declaration line; comments such as "module carries"
        # must never silently be treated as a module name.
        modules = re.findall(r"^\s*module\s+(\w+)\b", current.decode(), re.MULTILINE)
        if name.endswith(".sv"):
            if modules != [Path(name).stem]:
                raise ValueError(f"Expected exactly one matching module declaration: {name}: {modules}")
        elif modules:
            raise ValueError(f"Parameter header unexpectedly declares a module: {name}: {modules}")
        inventory.append({"path": name, "sha256": sha(current), "bytes": len(current),
                          "lines": len(current.splitlines()), "module": modules[0] if modules else None,
                          "historical_sha256": sha(old), "finite_source_tokens_equal": same})
    if not token_same:
        raise ValueError("Current source tokens differ from historical measured baseline")
    s, u, a = params["N_SLICES"], params["N_UNIT_MAIN"] + params["N_UNIT_SUB"], params["N_ACTIVE"]
    rowbits = 47 + clog2(u)
    contextbits = 32 + a * (5 + u) + 2 * params["DITHER_UNITS_RANGE"] + 1 + 64
    sum_a = params["ACC_BITS"] - (params["V_FRAC"] + 1)
    profiles = {}
    for stages in (5, 6, 7):
        cyc = math.ceil(sum_a / stages)
        pad = cyc * stages
        cnt = max(1, clog2(cyc))
        # mag, dv, dv_large, rem, quo, neg, cnt, run, err_r, q, err, done.
        state = 2 * pad + 3 * sum_a + cnt + 6
        profiles[str(stages)] = dict(unroll_stages=stages, numerator_bits=sum_a,
                                    denominator_bits=64, trial_subtract_bits=sum_a + 1,
                                    cycles=cyc, padded_numerator_bits=pad, counter_bits=cnt,
                                    declared_state_bits=state, reconstruction_latency=cyc + 2,
                                    frame_period=16, output_latency_budget_remaining=16 - (cyc + 2))
    historical_synth = json.loads((repo / "docs/evidence/20260930/shared_old_trial_synth/readings.json").read_text())
    historical_route = json.loads((repo / "docs/evidence/20260930/shared_old_trial_route_25ns/reviewed_result.json").read_text())
    store = {"declared_coefficient_bits": s * u * params["W_BITS"],
             "proven_nonconstant_coefficient_bits": s * u * 47,
             "loaded_bitmap_bits": s * u, "row_bits": rowbits,
             "row_cache_bits": s * rowbits, "global_sum_declared_bits": 64,
             "global_sum_pruned_for_profile": True,
             "storage_candidate_ff_bits": s * u * 47 + s * u + s * rowbits,
             "per_row_old_value_mux_inputs": u,
             "per_row_old_value_mux_bus_width_proven": 47,
             "number_of_per_row_old_value_muxes": s,
             "configuration_readback_row_mux_inputs": s,
             "binary_mux_depth_ideal_per_row": clog2(u),
             "binary_mux_depth_ideal_readback_row": clog2(s),
             "per_row_replacement_subtracts": s,
             "per_row_replacement_adds": s,
             "replacement_arithmetic_width": rowbits,
             "scope": "Source-declared/derived bits and ideal binary mux topology; not mapped gates"}
    result = {
        "date": "2026-10-02", "head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, check=True, text=True).stdout.strip(),
        "scope": "MANUALLY_REVIEWED_SOURCE_FORMULAS_PLUS_HISTORICAL_FPGA_NOT_NEW_EDA_NOT_ASIC_SIGNOFF",
        "script_sha256": sha(Path(__file__).read_bytes()),
        "formula_method": "No HDL AST elaboration; profile and exact source anchors checked, hand-reviewed formulas evaluated",
        "parameters": params, "top_default_divider_profile": 7,
        "historical_physical_divider_profile": 5, "structural_profile": 1,
        "source_inventory": inventory, "source_anchors": refs,
        "historical_identity_bridge": {"baseline_commit": baseline, "finite_tokens_equal_all_26_files": token_same,
                                        "scope": "Finite SV tokens including tool comments/directives only; this script does not re-run macro preprocessing or formal equivalence"},
        "weight_store": store,
        "cal_weight_reduce": {
            "on_tree": tree(s * u), "uncached_total_tree_removed_branches": tree(s * u)["declared_branch_expressions"],
            "cached_row_tree": tree(s), "number_of_dither_column_trees": 4,
            "dither_column_tree_each": tree(s), "mask_tree": tree(4), "rail_tree": tree(4),
            "dither_conditional_negations": 4, "default_sum_bits": 64, "default_rails_bits": 66,
            "physical_active_comparisons": s * a, "invalid_duplicate_comparisons": a * (a - 1) // 2,
            "invalid_range_checks": a, "logical_physical_mask_crosspoints": s * a * u,
            "final_rails_serial_add_subexpressions": 2, "effective_gain_conditional_subtractions": 1,
            "valid_selected_coefficients": a * u,
            "valid_selected_sum_max_exclusive": a * u * (1 << 47),
            "valid_selected_sum_sufficient_bits": 47 + clog2(a * u),
            "global_uncorrelated_leaf_sum_sufficient_bits": 47 + clog2(s * u),
            "scope": "Source graph/formulas; runtime 8-active invariant is not a synthesis optimization guarantee"},
        "context": {"packet_bits": contextbits, "two_packet_data_bits": 2 * contextbits,
                    "valid_bad_bits": 4, "declared_state_bits": 2 * contextbits + 4},
        "rdac_output": {"selected_bits": s, "main_bits": s * 63, "sub_bits": s * 8,
                        "dither_bits": s * 4, "declared_state_bits": s * (1 + 63 + 8 + 4),
                        "active_channel_scatter_count": a, "per_physical_row_priority_candidates": a},
        "multipliers": [
            {"module": "adc2_dec", "source_expression": "zero_extend(2*code+1) * sign_extend(max-min)",
             "meaningful_operand_bits": [13, 65], "product_declared_bits": 78,
             "scope": "One logical multiply; FPGA mapping is historical 4 DSP, ASIC cell cost unknown"},
            {"module": "cal_residue_mac", "source_expression": "zero_extend(total_s) * inj_r",
             "meaningful_operand_bits": [64, 64], "product_declared_bits": 130,
             "scope": "One logical multiply; sign/zero-extension does not mean a generic 130x64 physical multiplier; historical 16 DSP"}],
        "divider_profiles": profiles,
        "standalone_flash_encoder_default": {"bits": 9, "comparators": 511, "tree": tree(511)},
        "actual_structural_flash_encoder": {"bits": 3, "comparators": 7, "tree": tree(7)},
        "unit_therm": {"logical_main_comparisons": a * 63, "logical_sub_comparisons": a * 8,
                       "logical_total_comparisons": a * u, "dither_rail_comparisons": 4},
        "slice_pool": {"source_state_bits": 187, "source_priority_scan_steps": 18,
                       "lfsr_unrolled_steps": 8, "constant_remainder_input_bits": 8,
                       "constant_remainder_divisor": 18,
                       "scope": "Scan is combinational; integer temporaries are not state FF"},
        "historical_actual": {
            "source_commit": baseline, "rtl_content_sha256": historical_route["rtl_content_sha256"],
            "resources": historical_synth["resources"], "synthesis_worst_setup": historical_synth["worst_setup_path"],
            "routed_core": historical_route, "hierarchy": historical_synth["hierarchy"],
            "hierarchy_caveat": historical_synth["hierarchy_caveat"],
            "estimated_store_share_of_actual_ff_percent": 100 * store["storage_candidate_ff_bits"] / historical_synth["resources"]["ff"]},
        "unknown": ["Current-head new synthesis/route measurements", "ASIC standard-cell area and delay", "SRAM banking/ports and PDK macro models", "Activity-based total power", "Board I/O min/max budgets", "Full-state proof and post-ECO STA of proposed selective multicycle paths"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        raise FileExistsError(args.out)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"SOURCE_STRUCTURE_AUDIT files={len(names)} token_identity={token_same} store_bits={store['storage_candidate_ff_bits']} on_depth={clog2(s*u)} context_bits={2*contextbits+4} output={args.out}")


if __name__ == "__main__":
    main()
