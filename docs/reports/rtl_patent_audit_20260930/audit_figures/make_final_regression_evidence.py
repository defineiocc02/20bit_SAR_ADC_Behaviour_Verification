#!/usr/bin/env python3
"""Render a verified 21- or 22-bench archive without inventing waveforms or coverage."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

GROUPS = [
    (
        "arithmetic",
        "ARITHMETIC AND CALIBRATION",
        [
            "p2_oracle_tb",
            "divider_borrow_tb",
            "cal_weight_reduce_ppa_tb",
            "calibration_physical_tb",
            "calibration_recovery_tb",
            "calibration_fit_tb",
            "p2_tb",
        ],
    ),
    (
        "protocol",
        "TRANSACTIONS AND SCHEDULING",
        [
            "sar_trial_tb",
            "structural_adc_tb",
            "structural_protocol_tb",
            "recon_ppa_latency_tb",
            "review_recon_protocol_tb",
            "review_top_protocol_tb",
            "review_config_tb",
        ],
    ),
    (
        "system",
        "LEAF, CONFIGURATION AND SYSTEM",
        [
            "tree_mapping_tb",
            "review_leaf_tb",
            "review_dither_tb",
            "p1_tb",
            "p2_periph_tb",
            "p2_smoke_tb",
            "p3_top_tb",
        ],
    ),
]

# These descriptions were read from the frozen TB, not inferred from PASS.
# Ranges point to the concrete reference computation / failure checks.
DEFS = {
    "weight_row_cache_tb": (
        "Independent retained-word row sums;\nreset/clear/write, completion/readback.",
        "Five loader geometries; no measured PPA claim.",
        [26, 61],
    ),
    "p2_oracle_tb": (
        "Identity: dout == input code;\nzero flags and 11-cycle completion.",
        "One-slice fixture; not all configurations.",
        [50, 93],
    ),
    "divider_borrow_tb": (
        "Signed floor reference + defining\ninequality; zero divisor, busy and hold.",
        "Six small-width parameter fixtures.",
        [28, 89],
    ),
    "cal_weight_reduce_ppa_tb": (
        "Bit-exact T/G/R miter against the\nprevious three-tree RTL + invalid-ID rule.",
        "Shared mathematics: equivalence, not physics.",
        [29, 133],
    ),
    "calibration_physical_tb": (
        "Independent 256-bit integer formula;\ncode, flags, ID and input capture.",
        "18 slices, reduced 8-cell test geometry.",
        [34, 92],
    ),
    "calibration_recovery_tb": (
        "Known target -> synthetic residue ->\ncalibrated and nominal-weight outputs.",
        "Synthetic mismatch + ideal ADC2 quantization.",
        [35, 75],
    ),
    "calibration_fit_tb": (
        "Frozen external holdout vectors;\ncode/flags/ID/latency + coefficient readback.",
        "Fitted coefficients loaded, not learned in RTL.",
        [67, 134],
    ),
    "p2_tb": (
        "Golden vectors + floor inequality;\nrounding, masks, carry, clipping and link.",
        "T5 full-code sweep is a separate bench.",
        [402, 869],
    ),
    "sar_trial_tb": (
        "Ideal comparator against target code;\nstalled trial hold, done and cancellation.",
        "No comparator noise or metastability model.",
        [25, 46],
    ),
    "structural_adc_tb": (
        "Physical-switch 256-bit scoreboard;\ncode/flags/ID, bank ownership and latency.",
        "Behavioral analog boundaries; 3 modes.",
        [71, 195],
    ),
    "structural_protocol_tb": (
        "Expected accept/drop schedule;\ncaptured context + edge-stable enables.",
        "Two phase-decode perturbations are injected.",
        [60, 115],
    ),
    "recon_ppa_latency_tb": (
        "Per-profile code/ID scoreboard;\nbusy-release and completion timestamps.",
        "Clock intervals, not measured nanoseconds.",
        [29, 53],
    ),
    "review_recon_protocol_tb": (
        "Directed unconfigured/cancel/error\nscenarios; ID/flags and prior-output hold.",
        "Completion only: no total check count logged.",
        [20, 55],
    ),
    "review_top_protocol_tb": (
        "Top-port configuration and result oracle;\nreadback, rejection, epochs and IDs.",
        "38 outputs is the TB's printed count.",
        [29, 175],
    ),
    "review_config_tb": (
        "Configuration completeness/collision\nand atomic epoch guards; dither-bin checks.",
        "624 outputs are not 624 independent tests.",
        [25, 82],
    ),
    "tree_mapping_tb": (
        "Loop popcount and direct cell sums;\nT/G/R + invalid-ID comparison.",
        "This archive is RTL, not mapped-netlist proof.",
        [25, 109],
    ),
    "review_leaf_tb": (
        "Popcount/thermometer rules, address\nbounds, allocation wrap and floor oracle.",
        "The logged count is assertion calls.",
        [45, 93],
    ),
    "review_dither_tb": (
        "Support/PMF/mean/lag-1 checks;\nhold and seed-reset invariants.",
        "Accepted draws differ from 200k clock cycles.",
        [14, 63],
    ),
    "p1_tb": (
        "M1-M5 golden vectors plus structural\nproperties, dither support and connected chain.",
        "464,823 is the TB check counter, not codes.",
        [1, 24],
    ),
    "p2_periph_tb": (
        "Directed guards, sticky status, RDAC\nfanout, phase schedule and register rules.",
        "Full-table load cannot trigger unreachable sum guard.",
        [1, 65],
    ),
    "p2_smoke_tb": (
        "Configuration readback; allocator\ninvariants and selected popcount inputs.",
        "Smoke coverage, not complete acceptance.",
        [1, 16],
    ),
    "p3_top_tb": (
        "File oracle for dout/clip + cumulative\nOR for sticky analog-overflow status.",
        "Forced dither; file row 0 skipped; no dither-source test.",
        [1, 65],
    ),
}


def groups_for_count(count: int) -> list:
    """Keep the original 21-bench contract and allow only its one-bench extension."""
    if count not in (21, 22):
        raise ValueError("Only the exact historical 21 or row-cache 22 bench sets are supported")
    groups = [(suffix, title, list(names)) for suffix, title, names in GROUPS]
    if count == 22:
        groups[0][2].insert(3, "weight_row_cache_tb")
    return groups


def sha(path: Path) -> str:
    """Return the digest of a frozen source, log, script, or figure."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def one(text: str, pattern: str) -> tuple[str, ...]:
    """Require exactly one recorded occurrence of a summary field."""
    rows = re.findall(pattern, text)
    if len(rows) != 1:
        raise ValueError(f"Expected exactly one match: {pattern!r}, found {len(rows)}")
    value = rows[0]
    return (value,) if isinstance(value, str) else value


def metric_lines(name: str, text: str) -> tuple[list[str], dict]:
    """Retain native units; no aggregate count across unlike benches."""
    if name == "weight_row_cache_tb":
        geometry_count, steps = map(
            int, one(text, r"WEIGHT_ROW_CACHE_COMPLETE geometries=(\d+) steps=(\d+)")
        )
        cases = [
            list(map(int, row))
            for row in re.findall(
                r"WEIGHT_ROW_CACHE_CASE_PASS slices=(\d+) units=(\d+) steps=(\d+) accepted=(\d+) rejected=(\d+) clears=(\d+)",
                text,
            )
        ]
        expected = {(18, 71): 3572, (32, 128): 9208, (32, 1): 1080, (1, 128): 1272, (1, 1): 1018}
        if not (
            geometry_count == len(cases) == 5
            and steps == 16150
            and {(r[0], r[1]): r[2] for r in cases} == expected
            and sum(r[2] for r in cases) == steps
        ):
            raise ValueError("Row-cache loader case/count contract differs")
        return [
            f"{steps:,} loader TB steps / 5 geometries",
            "all words + exact cached row totals checked",
        ], {
            "loader_tb_steps": steps,
            "geometries": geometry_count,
            "cases_slices_units_steps_accepted_rejected_clears": cases,
        }
    if name == "p2_oracle_tb":
        n, e = map(int, one(text, r"P2_ORACLE_COMPLETE codes=(\d+) errors=(\d+)"))
        return [f"{n:,} input codes; {e} errors"], {"codes": n, "errors": e}
    if name == "divider_borrow_tb":
        n = int(one(text, r"DIVIDER_BORROW_COMPLETE exhaustive_checks=(\d+)")[0])
        cases = [
            list(map(int, row))
            for row in re.findall(
                r"CASE_PASS numerator_bits=(\d+) denominator_bits=(\d+) stages=(\d+) checks=(\d+)",
                text,
            )
        ]
        if len(cases) != 6 or sum(row[3] for row in cases) != n:
            raise ValueError("Divider exhaustive case totals differ")
        return [f"{n:,} operand/config combinations", "6 width/unroll fixtures"], {
            "combinations": n,
            "cases_WA_WD_stages_checks": cases,
        }
    if name == "cal_weight_reduce_ppa_tb":
        cases = [
            list(map(int, row))
            for row in re.findall(
                r"CASE_PASS ns=(\d+) active=(\d+) units=(\d+) dither=(\d+) end=(\d+) checks=(\d+) allocations=(\d+)",
                text,
            )
        ]
        if len(cases) != 7:
            raise ValueError("Incomplete reducer miter")
        n = sum(row[5] for row in cases)  # All use the same miter check() unit.
        alloc = max(row[6] for row in cases)
        cached = list(map(int, re.findall(r"allocations=\d+ cache=(\d+)(?:\r?\n|$)", text)))
        if cached and (len(cached) != 7 or cached != [1] * 7):
            raise ValueError("Cached reducer is not checked in all seven geometries")
        return [
            f"{n:,} miter input cases / 7 fixtures",
            f"{alloc:,} allocations in 18-slice fixture",
        ], {
            "miter_cases": n,
            "fixture_records": cases,
            "allocation_subsets": alloc,
            "cached_dut_checked": bool(cached),
        }
    if name == "calibration_physical_tb":
        n, s = map(int, one(text, r"COMPLETE samples=(\d+) physical_slices=(\d+)"))
        return [f"{n:,} transactions; {s} physical slices"], {
            "transactions": n,
            "slices": s,
        }
    if name == "calibration_recovery_tb":
        n, c, u = map(
            int,
            one(
                text,
                r"COMPLETE samples=(\d+) max_calibrated_error=(\d+) max_uncalibrated_error=(\d+)",
            ),
        )
        return [
            f"{n:,} target/residue transactions",
            f"max |error|: calibrated {c}; nominal {u} codes",
        ], {"transactions": n, "max_calibrated_codes": c, "max_nominal_codes": u}
    if name == "calibration_fit_tb":
        n, w = map(
            int,
            one(
                text,
                r"COMPLETE samples=(\d+) fitted_external_weights=(\d+) cfg_commit=PASS",
            ),
        )
        return [
            f"{n:,} holdout samples; {w:,} weights",
            "code + flags + ID + 11-cycle latency",
        ], {"holdout_samples": n, "loaded_weights": w}
    if name == "sar_trial_tb":
        fine, coarse = map(
            int,
            one(
                text,
                r"COMPLETE fine_codes=(\d+) coarse_codes=(\d+) stalls_and_cancel=PASS",
            ),
        )
        return [
            f"{fine:,} distinct fine / {coarse:,} coarse codes",
            "stall + cancellation checks completed",
        ], {"distinct_fine_codes": fine, "distinct_coarse_codes": coarse}
    if name == "structural_adc_tb":
        vals = list(
            map(
                int,
                one(
                    text,
                    r"COMPLETE modes=(\d+) outputs=(\d+) checks=(\d+) physical_slices=(\d+) recon_stages=(\d+) recon_latency=(\d+) latency_checks=(\d+)",
                ),
            )
        )
        m, o, c, s, p, latency, lc = vals
        if o != lc:
            raise ValueError("Missing structural output latency checks")
        return [
            f"{o:,} outputs; {c:,} checked clock ticks",
            f"{lc:,} latency checks; +{latency} cycles (P={p})",
        ], dict(
            zip(
                (
                    "modes",
                    "outputs",
                    "checked_ticks",
                    "slices",
                    "stages",
                    "latency_cycles",
                    "latency_checks",
                ),
                vals,
                strict=True,
            )
        )
    if name == "structural_protocol_tb":
        f, launches, drops, checks, perturb = map(
            int,
            one(
                text,
                r"COMPLETE frames=(\d+) launches=(\d+) expected_drops=(\d+) checks=(\d+) decoder_perturbations=(\d+)",
            ),
        )
        return [
            f"{f} frames: {launches} launches / {drops} drops",
            f"{checks:,} checked ticks; {perturb} decode perturbations",
        ], {
            "frames": f,
            "launches": launches,
            "expected_drops": drops,
            "checked_ticks": checks,
            "perturbations": perturb,
        }
    if name == "recon_ppa_latency_tb":
        rows = [
            list(map(int, r))
            for r in re.findall(
                r"PROFILE_PASS stages=(\d+) samples=(\d+) busy_release=(\d+) latency=(\d+) initiation_interval=(\d+)",
                text,
            )
        ]
        if sorted(r[0] for r in rows) != [5, 6, 7]:
            raise ValueError("Incomplete reconstruction profiles")
        rows.sort()
        return [
            "P=5/6/7: " + "/".join(str(r[1]) for r in rows) + " samples",
            "latency " + "/".join(str(r[3]) for r in rows) + "; request interval 16 cycles",
        ], {"profiles_stages_samples_busy_latency_II": rows}
    if name == "review_recon_protocol_tb":
        return [
            "Total check count: not logged",
            "directed completion marker present",
        ], {"total_checks": None}
    if name == "review_top_protocol_tb":
        c, o = map(int, one(text, r"COMPLETE checks=(\d+) outputs=(\d+)"))
        return [f"{c:,} check calls; {o} reported outputs"], {
            "check_calls": c,
            "reported_outputs": o,
        }
    if name == "review_config_tb":
        o = int(one(text, r"COMPLETE outputs=(\d+)")[0])
        return [
            f"{o:,} observed output-valid events",
            "total assertion count: not logged",
        ], {"output_valid_events": o, "assertion_count": None}
    if name in {"review_leaf_tb", "tree_mapping_tb"}:
        c = int(one(text, r"COMPLETE checks=(\d+)")[0])
        label = "check calls" if name == "review_leaf_tb" else "stimulus rows"
        return [f"{c:,} {label}"], {label.replace(" ", "_"): c}
    if name == "review_dither_tb":
        rows = re.findall(
            r"DITHER PASS D=(\d+) seed=([0-9a-f]+) accepted=(\d+) mean=([-\d.]+) corr=([-\d.]+)",
            text,
        )
        if len(rows) != 7:
            raise ValueError("Incomplete dither conditions")
        data = [
            {
                "D": int(d),
                "seed": s,
                "accepted": int(a),
                "mean": float(m),
                "corr": float(c),
            }
            for d, s, a, m, c in rows
        ]
        counts = sorted({r["accepted"] for r in data})
        return [
            "7 amplitude/seed conditions",
            "accepted/condition: " + " or ".join(f"{v:,}" for v in counts),
        ], {"conditions": data}
    prefix = {
        "p1_tb": r"p1_tb summary",
        "p2_tb": r"p2_tb summary",
        "p2_periph_tb": r"p2_periph",
        "p2_smoke_tb": r"p2_smoke",
        "p3_top_tb": r"p3_top\s+summary",
    }[name]
    c, e = map(int, one(text, prefix + r"[: ]+checks=(\d+) errors=(\d+)"))
    data = {"TB_check_counter": c, "errors": e}
    lines = [f"{c:,} TB checks; {e} errors"]
    if name == "p3_top_tb":
        (n,) = map(int, one(text, r"dout 比对：共 (\d+) 行"))
        data["compared_output_rows"] = n
        lines += [f"{n:,} output rows; 16-cycle spacing"]
    return lines, data


def load_ci_evidence(repo: Path, evidence: Path, wanted: list[str]) -> tuple:
    """Bind the observed CI step/job conclusions to immutable Git inputs."""
    evidence = evidence.resolve()
    count = len(wanted)
    expected_set = {name for _, _, names in groups_for_count(count) for name in names}
    if len(set(wanted)) != count or set(wanted) != expected_set:
        raise ValueError("Requested bench set differs from the fixed supported contract")
    index_path = evidence / "sha256.json"
    index = json.loads(index_path.read_text())
    for relative, expected in index.items():
        path = evidence / relative
        if not path.resolve().is_relative_to(evidence) or sha(path) != expected:
            raise ValueError(f"CI archive checksum differs: {relative}")
    required = {
        "manifest.json",
        "tested_inputs_sha256.json",
        "job_metadata.json",
        "tested_merge_commit.json",
        "rtl_content_identity.json",
        "open_rtl/version.log",
    }
    if not required.issubset(index):
        raise ValueError("CI archive index omits required provenance")
    result = json.loads((evidence / "manifest.json").read_text())
    sources = json.loads((evidence / "tested_inputs_sha256.json").read_text())
    job = json.loads((evidence / "job_metadata.json").read_text())
    merge = json.loads((evidence / "tested_merge_commit.json").read_text())
    identity = json.loads((evidence / "rtl_content_identity.json").read_text())
    head, tree = result["pr_head_sha"], result["git_tree_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Invalid immutable CI head")
    head_tree = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", f"{head}^{{tree}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not (
        head_tree == tree == merge["tree"]["sha"] == sources["git_tree_sha"]
        and merge["sha"] == result["tested_merge_sha"] == sources["tested_merge_sha"]
        and head == sources["pr_head_sha"] == job["head_sha"]
        and result["whole_tree_equal"] is True
        and sources["whole_tree_equal"] is True
        and result["job_conclusion"] == job["conclusion"]
        and job["conclusion"] in {"success", "failure"}
        and job["id"] == result["job_id"]
        and job["run_id"] == result["workflow_run_id"]
        and job["status"] == "completed"
    ):
        raise ValueError("CI head/merge/tree/job identity differs")
    steps = {item["name"]: item["conclusion"] for item in job["steps"]}
    if not (
        steps.get("Compile and execute RTL regressions") == "success"
        and result["benches_passed"] == count
        and result["strict_lint_profiles_passed"] == 3
        and result["tested_step_command"] == "python tools/run_open_rtl.py"
        and (evidence / "open_rtl/version.log").read_text().strip() == result["tool_version"]
    ):
        raise ValueError("CI RTL-step boundary does not match the plotted claim")
    audit_step = steps.get("Independent arbitrary-precision arithmetic audit")
    if job["conclusion"] == "failure":
        required.add("arithmetic_entry_failure.log")
        if not (
            audit_step == "failure"
            and result["later_failed_step"]["command_returncode"] == 127
            and result["later_failed_step"]["simulation_pass_claimed"] is False
            and "verilator_bin" in (evidence / "arithmetic_entry_failure.log").read_text()
        ):
            raise ValueError("The failed job does not match the documented audit-launch failure")
    else:
        required.update({"arithmetic-audit/manifest.json", "arithmetic-audit/run.log"})
        audit = json.loads((evidence / "arithmetic-audit/manifest.json").read_text())
        audit_log = (evidence / "arithmetic-audit/run.log").read_text()
        counts = tuple(
            map(
                int,
                one(
                    audit_log,
                    r"ARITHMETIC_AUDIT_COMPLETE mac=(\d+) adc=(\d+) div=(\d+) flag_sequences=(\d+)",
                ),
            )
        )
        expected = tuple(audit["counts"][key] for key in ("mac", "adc", "div", "flag_sequences"))
        if not (
            audit_step == "success"
            and audit["status"] == "PASS"
            and counts == expected
            and "Verilog $finish" in audit_log
            and result["independent_arithmetic_audit"]["status"] == "PASS"
        ):
            raise ValueError("Successful RTL job lacks independent arithmetic completion")
    if not required.issubset(index):
        raise ValueError("CI archive index omits step-specific provenance")
    records = {item["top"]: item for item in result["benches"]}
    if len(result["benches"]) != count or set(records) != set(wanted):
        raise ValueError(f"CI archive does not contain the exact {count}-bench set")
    frozen = {}
    for relative, item in sources["files"].items():
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{head}:{relative}"],
            check=True,
            capture_output=True,
        ).stdout
        if len(blob) != item["bytes"] or hashlib.sha256(blob).hexdigest() != item["sha256"]:
            raise ValueError(f"CI source hash differs from Git head: {relative}")
        frozen[relative] = blob
    if job["conclusion"] == "success":
        if result["independent_arithmetic_audit"]["counts"] != audit["counts"]:
            raise ValueError("Independent-audit summary differs from its manifest")
        for relative, digest in audit["sources_sha256"].items():
            if hashlib.sha256(frozen[relative]).hexdigest() != digest:
                raise ValueError(
                    f"Independent-audit source differs from tested Git head: {relative}"
                )
    observation = result.get("workflow_observation")
    if observation and (
        observation["run_id"] != result["workflow_run_id"] or observation["head_sha"] != head
    ):
        raise ValueError("Workflow observation identity differs")
    if len(frozen) != result["source_input_count"]:
        raise ValueError("CI source input count differs")
    rtl_files = identity["files"]
    for relative, digest in rtl_files.items():
        if hashlib.sha256(frozen[relative]).hexdigest() != digest:
            raise ValueError(f"CI production RTL identity differs: {relative}")
    combined = "".join(f"{key} {value}\n" for key, value in sorted(rtl_files.items()))
    if not (
        len(rtl_files) == result["rtl_file_count"] == 26
        and hashlib.sha256(combined.encode()).hexdigest()
        == identity["rtl_sha256"]
        == result["rtl_content_sha256"]
    ):
        raise ValueError("CI aggregate production RTL identity differs")
    for item in result["lint"]:
        if item["result"] != "PASS" or index[item["log"]] != item["sha256"]:
            raise ValueError("CI lint record differs")
    if len(result["lint"]) != 3:
        raise ValueError("CI lint profile count differs")
    for item in records.values():
        if item["result"] != "PASS" or index[item["run_log"]] != item["sha256"]:
            raise ValueError("CI bench log binding differs")
    metadata = {
        "schema": "CI_RTL_STEP_AND_JOB_CONCLUSIONS",
        "benches_passed": count,
        "pr_head_sha": head,
        "tested_merge_sha": result["tested_merge_sha"],
        "git_tree_sha": tree,
        "rtl_content_sha256": result["rtl_content_sha256"],
        "workflow_run_id": result["workflow_run_id"],
        "job_id": result["job_id"],
        "rtl_step_conclusion": "success",
        "overall_job_conclusion": job["conclusion"],
        "independent_arithmetic_step_conclusion": audit_step,
        "workflow_observation": result.get("workflow_observation"),
        "later_failed_step": result.get("later_failed_step"),
        "tool_version": result["tool_version"],
        "immutable_git_inputs_verified": len(frozen),
    }
    paths = [index_path] + [evidence / name for name in sorted(required)]
    return result, sources, records, frozen, metadata, paths


def main() -> None:
    """Verify the complete frozen bench set and rebuild its four evidence figures."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=report.parent / "sar_adc_review_20260919/calibration-closure",
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--schema", choices=("ci", "local"), default="ci")
    args = parser.parse_args()
    repo = args.repo.resolve()
    default_dir = "ci_rtl_417f" if args.schema == "ci" else "final_rtl"
    evidence = (args.evidence or repo / "docs/evidence/20260930" / default_dir).resolve()
    bench_count = (
        json.loads((evidence / "manifest.json").read_text())["benches_passed"]
        if args.schema == "ci"
        else 21
    )
    groups = groups_for_count(bench_count)
    wanted = [name for _, _, names in groups for name in names]
    ci_metadata, frozen = None, None
    if args.schema == "ci":
        result, sources, records, frozen, ci_metadata, provenance_paths = load_ci_evidence(
            repo, evidence, wanted
        )
    else:
        result_path, source_path = (
            evidence / "result.json",
            evidence / "source_manifest.json",
        )
        result, sources = (
            json.loads(result_path.read_text()),
            json.loads(source_path.read_text()),
        )
        if (
            result["status"] != "PASS"
            or result["process_exit_code"] != 0
            or result["benches_passed"] != 21
            or set(result["records"]) != set(wanted)
            or sha(source_path) != result["source_manifest_sha256"]
        ):
            raise ValueError("The local result does not certify the exact frozen 21-bench set")
        records = result["records"]
        provenance_paths = [result_path, source_path]
    entries = {}
    for name in wanted:
        tb_relative = f"sim/tb/{name}.sv"
        record = records[name]
        if ci_metadata:
            log = evidence / record["run_log"]
            marker = record["required_marker"]
            expected_log = record["sha256"]
            tb_bytes = frozen[tb_relative]
            tb_source = f"git:{ci_metadata['pr_head_sha']}:{tb_relative}"
        else:
            log = evidence / f"{name}.run.log"
            marker, expected_log = record["completion_marker"], record["run_sha256"]
            tb = repo / tb_relative
            tb_bytes, tb_source = tb.read_bytes(), str(tb)
            if hashlib.sha256(tb_bytes).hexdigest() != sources["sha256"][tb_relative]:
                raise ValueError(f"Local TB checksum differs: {name}")
        text = log.read_text()
        if sha(log) != expected_log or text.count(marker) != 1 or "Verilog $finish" not in text:
            raise ValueError(f"Log hash or unique completion differs: {name}")
        lines, metrics = metric_lines(name, text)
        oracle, limitation, span = DEFS[name]
        if ci_metadata and name == "cal_weight_reduce_ppa_tb":
            cached = bench_count == 22
            oracle = (
                "Independent physical-cell scalar sum;\nold + uncached + cached T/G/R and IDs."
                if cached
                else "Independent physical-cell scalar sum;\nold/new T/G/R and invalid-ID checks."
            )
            limitation = "Transaction-edge sampling; no mapped/STA claim."
            span = [63, 118] if cached else [47, 109]
            if b"oracle=scalar_result(sampling,ids,main_on,sub_on,dr,weights)" not in tb_bytes:
                raise ValueError("The CI reducer bench lacks the expected independent oracle")
            if cached and not (
                metrics["cached_dut_checked"]
                and b".P_USE_ROW_TOTALS(1)) cached_dut" in tb_bytes
                and b"cached_total!==oracle[194:131]" in tb_bytes
            ):
                raise ValueError("The row-cache CI does not prove its cached reducer path")
        if name == "weight_row_cache_tb" and not (
            b"expected_row+=64'(expected_words[physical][unit_index])" in tb_bytes
            and b"check_rows(weights,model,row_total,checks)" in tb_bytes
        ):
            raise ValueError("The row-cache bench lacks its independent retained-word oracle")
        if ci_metadata and bench_count == 22 and name == "calibration_fit_tb":
            span = [68, 135]
        if not 1 <= span[0] <= span[1] <= len(tb_bytes.decode().splitlines()):
            raise ValueError(f"TB source line range differs: {name}")
        entries[name] = {
            "log": log,
            "tb_source": tb_source,
            "tb_sha256": hashlib.sha256(tb_bytes).hexdigest(),
            "text": text,
            "marker": marker,
            "lines": lines,
            "metrics": metrics,
            "oracle": oracle,
            "limitation": limitation,
            "tb_lines": span,
        }

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "pdf.fonttype": 42})
    ink, muted, blue, green = "#16324a", "#4e6070", "#235b83", "#087862"
    if ci_metadata and ci_metadata["overall_job_conclusion"] == "success":
        first_banner = f"{bench_count} RTL benches passed; independent arithmetic audit passed"
        second_banner = "RTL CI job succeeded; workflow conclusion is separate"
        conclusion_color = green
    else:
        first_banner = f"{bench_count} RTL benches passed;"
        second_banner = "overall CI job failed in subsequent audit launcher"
        conclusion_color = "#aa341e"
    output = report / "figures"
    output.mkdir(exist_ok=True)

    def save(fig, stem, names, extra=None):
        png, pdf = output / f"{stem}.png", output / f"{stem}.pdf"
        fig.savefig(png, dpi=200, facecolor="white")
        fig.savefig(pdf, facecolor="white", metadata={"CreationDate": None, "ModDate": None})
        plt.close(fig)
        inputs = {str(p): sha(p) for p in provenance_paths}
        for name in names:
            inputs[str(entries[name]["log"])] = sha(entries[name]["log"])

        manifest = {
            "scope": "FROZEN_ZERO_DELAY_RTL_LOG_EVIDENCE_NOT_STA_OR_ANALOG_SIGNOFF",
            "script_sha256": sha(Path(__file__)),
            "inputs_sha256": inputs,
            "images_sha256": {str(p): sha(p) for p in (png, pdf)},
            "benches": {
                n: {
                    k: entries[n][k]
                    for k in (
                        "marker",
                        "metrics",
                        "oracle",
                        "limitation",
                        "tb_lines",
                        "tb_source",
                        "tb_sha256",
                    )
                }
                for n in names
            },
        }
        if ci_metadata:
            manifest["ci_provenance"] = ci_metadata
        if extra is not None:
            manifest["plotted_data"] = extra
        (report / "audit_figures" / f"{stem}.sha256.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        print(png)

    for index, (suffix, title, names) in enumerate(groups, 1):
        fig = plt.figure(figsize=(9.8, 11.3), facecolor="white")
        fig.text(0.04, 0.960, title, size=19, weight="bold", color=ink)
        fig.text(
            0.04,
            0.936,
            f"CI head {ci_metadata['pr_head_sha'][:7]} / merge {ci_metadata['tested_merge_sha'][:7]}  |  "
            f"Verilator {ci_metadata['tool_version'].split()[1]}  |  Group {index}/3"
            if ci_metadata
            else f"Historical local RTL evidence  |  Group {index}/3",
            size=10.7,
            color=muted,
        )
        if ci_metadata:
            fig.text(
                0.04,
                0.910,
                first_banner,
                size=11.3,
                weight="bold",
                color=green,
            )
            fig.text(
                0.04,
                0.889,
                second_banner,
                size=11.0,
                weight="bold",
                color=conclusion_color,
            )
        fig.text(
            0.04,
            0.858,
            "BENCH / OBSERVED LOG COUNTS",
            weight="bold",
            size=10.5,
            color=blue,
        )
        fig.text(
            0.455,
            0.858,
            "REFERENCE CHECK / COVERAGE BOUNDARY",
            weight="bold",
            size=10.5,
            color=blue,
        )
        row_scale = 7 / len(names)
        for row, name in enumerate(names):
            y = 0.825 - row * 0.106 * row_scale
            item = entries[name]
            fig.add_artist(
                Rectangle(
                    (0.025, y - 0.092 * row_scale),
                    0.95,
                    0.101 * row_scale,
                    transform=fig.transFigure,
                    facecolor="#f1f5f8" if row % 2 == 0 else "#ffffff",
                    edgecolor="none",
                    zorder=-1,
                )
            )
            fig.text(0.04, y, name, size=11.8, weight="bold", color=ink)
            fig.text(
                0.04,
                y - 0.029 * row_scale,
                "\n".join(item["lines"]),
                size=10.7,
                color=ink,
                va="top",
                linespacing=1.45,
            )
            fig.text(
                0.455,
                y,
                item["oracle"],
                size=10.5,
                color=ink,
                va="top",
                linespacing=1.4,
            )
            fig.text(
                0.455,
                y - 0.046 * row_scale,
                item["limitation"],
                size=9.1,
                color=muted,
                va="top",
                wrap=True,
            )
            fig.text(
                0.04,
                y - 0.080 * row_scale,
                "Observed: " + item["marker"],
                size=9.15,
                color=green,
                family="DejaVu Sans Mono",
            )
        fig.text(
            0.04,
            0.047,
            f"Counts retain their own units. No combined 'total coverage' score. All {bench_count} log and TB hashes verified.",
            size=9.1,
            color=muted,
        )
        fig.text(
            0.04,
            0.026,
            f"Source: docs/evidence/20260930/{evidence.name}/  |  RTL only; not STA or analog performance.",
            size=9.1,
            color=ink,
        )
        save(fig, f"final_regression_{suffix}", names)

    fit = []
    for row in re.findall(
        r"FIT_ROW n=(\d+) actual=([0-9a-f]+) expected=([0-9a-f]+) flags=([0-9a-f]+) expected_flags=([0-9a-f]+) latency=(\d+) id=(\d+)",
        entries["calibration_fit_tb"]["text"],
    ):
        n, a, e, f, ef, latency, sid = row
        fit.append(
            {
                "n": int(n),
                "actual": int(a, 16),
                "expected": int(e, 16),
                "flags": int(f, 16),
                "expected_flags": int(ef, 16),
                "latency": int(latency),
                "id": int(sid),
            }
        )
    if [r["n"] for r in fit] != list(range(1, 129)):
        raise ValueError("FIT_ROW sequence is incomplete")
    if any(
        r["actual"] != r["expected"]
        or r["flags"] != r["expected_flags"]
        or r["id"] != r["n"]
        or r["latency"] != 11
        for r in fit
    ):
        raise ValueError("Fit row contradicts its completion marker")
    recovery = entries["calibration_recovery_tb"]["metrics"]
    dither = entries["review_dither_tb"]["metrics"]["conditions"]
    fig, axes = plt.subplots(
        2, 2, figsize=(11.2, 8.8), gridspec_kw={"hspace": 0.47, "wspace": 0.32}
    )
    fig.subplots_adjust(left=0.08, right=0.965, top=0.83, bottom=0.14)
    fig.suptitle(
        "QUANTITATIVE WITNESSES FROM THE CI LOGS"
        if ci_metadata
        else "QUANTITATIVE WITNESSES FROM HISTORICAL LOGS",
        fontsize=19,
        color=ink,
        weight="bold",
        y=0.969,
    )
    fig.text(
        0.08,
        0.928,
        "A/B use 128 actual FIT_ROW records; C/D preserve the logged summary statistics.",
        color=muted,
        size=10.5,
    )
    if ci_metadata:
        fig.text(0.08, 0.901, first_banner, size=11.3, weight="bold", color=green)
        fig.text(
            0.08,
            0.878,
            second_banner,
            size=11.0,
            weight="bold",
            color=conclusion_color,
        )
    x = np.array([r["n"] for r in fit])
    actual, expected = (
        np.array([r["actual"] for r in fit]),
        np.array([r["expected"] for r in fit]),
    )
    ax = axes[0, 0]
    ax.plot(x, expected, color=blue, lw=1.2, label="frozen holdout expected")
    ax.plot(
        x,
        actual,
        "o",
        ms=2.6,
        markevery=3,
        color=green,
        label="logged RTL actual (markers every 3)",
    )
    ax.set(
        title="A  External-fit holdout: 128 actual rows",
        xlabel="Holdout sample index (not time)",
        ylabel="20-bit output code",
    )
    ax.legend(fontsize=8, loc="upper right")
    ax.ticklabel_format(axis="y", style="plain")
    ax = axes[0, 1]
    ax.plot(x, actual - expected, color=green, lw=1.5, label="actual - expected")
    ax.set(
        title="B  Exact holdout residual and metadata",
        xlabel="Holdout sample index",
        ylabel="Code difference",
        ylim=(-1.05, 1.05),
        yticks=[-1, 0, 1],
    )
    ax.text(
        0.04,
        0.93,
        "max |code difference| = 0\nflags mismatch = 0 / 128\nID mismatch = 0 / 128\nlatency = 11 cycles for 128 / 128",
        transform=ax.transAxes,
        color=ink,
        fontsize=9.3,
        va="top",
        linespacing=1.4,
    )
    ax = axes[1, 0]
    heights = [recovery["max_nominal_codes"], recovery["max_calibrated_codes"]]
    bars = ax.bar(
        ["Nominal weights", "Calibrated weights"],
        heights,
        color=["#be7953", green],
        width=0.53,
    )
    for bar, value in zip(bars, heights, strict=True):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 9,
            str(value),
            ha="center",
            fontsize=11,
            weight="bold",
            color=ink,
        )
    ax.set(
        title="C  Synthetic mismatch recovery",
        ylabel="Maximum absolute error (output codes)",
        ylim=(0, max(heights) * 1.27),
    )
    ax.text(
        0.04,
        0.88,
        f"{recovery['transactions']:,} target/residue transactions\nMaximum only: no error distribution retained",
        transform=ax.transAxes,
        size=9.3,
        color=muted,
    )
    ax = axes[1, 1]
    # Normalize means by D only for nonzero D. D=0 is exactly zero and is
    # explicitly excluded from normalization rather than dividing by zero.
    labels, mean_ratio, corr = [], [], []
    for row in dither:
        seed = {"13579bdf": "1357..", "00000001": "1", "ffffffff": "ffff.."}[row["seed"]]
        labels.append(f"D={row['D']}\n{seed}")
        mean_ratio.append(row["mean"] / row["D"] if row["D"] else np.nan)
        # D=0 has zero variance: the TB skips correlation and prints its zero
        # initialized real variable. Do not plot that placeholder as a statistic.
        corr.append(row["corr"] if row["D"] else np.nan)
    dx = np.arange(len(dither))
    ax.scatter(dx, mean_ratio, color=blue, marker="o", label="mean / D (D > 0)")
    ax.scatter(dx, corr, color=green, marker="x", label="lag-1 correlation")
    for bound in [-0.025, 0.025]:
        ax.axhline(bound, color=blue, ls="--", lw=0.75)
    for bound in [-0.03, 0.03]:
        ax.axhline(bound, color=green, ls=":", lw=0.75)
    ax.set(
        title="D  Dither: seven logged conditions",
        ylabel="Dimensionless summary statistic",
        xticks=dx,
        xticklabels=labels,
        ylim=(-0.039, 0.039),
    )
    ax.tick_params(axis="x", labelsize=7.6)
    ax.legend(fontsize=8, loc="lower center")
    ax.text(
        0.015,
        0.977,
        "Dashed: mean bound; dotted: correlation bound",
        transform=ax.transAxes,
        size=7.8,
        color=muted,
        va="top",
    )
    ax.text(
        0.02,
        0.56,
        "D=0: constant-only check",
        transform=ax.transAxes,
        size=7.5,
        color=muted,
    )
    for ax in axes.flat:
        ax.grid(alpha=0.18, axis="y")
        ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
        ax.title.set_fontsize(11)
    fig.text(
        0.08,
        0.072,
        "A/B: log-level bit-exact replay. C: behavioral mismatch fixture. D: finite-sequence statistical regression.",
        size=9.4,
        color=ink,
    )
    fig.text(
        0.08,
        0.046,
        "No waveform was reconstructed from a PASS line. These plots do not establish SNDR, ENOB, silicon PPA or STA.",
        size=9.4,
        color=muted,
    )
    if ci_metadata:
        fig.text(
            0.08,
            0.023,
            f"CI head {ci_metadata['pr_head_sha'][:7]} / merge {ci_metadata['tested_merge_sha'][:7]}"
            f"  |  Source: docs/evidence/20260930/{evidence.name}/",
            size=8.4,
            color=ink,
        )
    save(
        fig,
        "final_regression_quantitative",
        ["calibration_fit_tb", "calibration_recovery_tb", "review_dither_tb"],
        {
            "fit_rows": fit,
            "recovery_summary": recovery,
            "dither_conditions": dither,
            "dither_thresholds_from_TB": {"mean_abs_over_D": 0.025, "lag1_abs": 0.03},
        },
    )


if __name__ == "__main__":
    main()
