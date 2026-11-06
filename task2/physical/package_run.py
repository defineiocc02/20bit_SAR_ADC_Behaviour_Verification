#!/usr/bin/env python3
"""Freeze the run inputs and package the measured SKY130HD evidence."""

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "task1/physical"))
from package_proxy import FINAL_FILES, describe_file, verify_gds
from sky130_package import read_timing


def write_json(path: Path, payload: dict) -> None:
    """Write a stable and human-readable report."""
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def freeze(args: argparse.Namespace) -> None:
    """Bind every RTL and physical configuration input to the executed commit."""
    source = args.repo.resolve()
    physical = "task2/physical"
    configs = {args.config, f"{physical}/sky130_config.mk"}
    if args.top == "sar20_digital_lms_top":
        configs.add(f"{physical}/sky130_integrated_config.mk")
    verilog = set()
    for config in configs:
        verilog.update(re.findall(r"\$\(REPO_DIR\)/([\w/.-]+\.sv)", (source / config).read_text()))
    if not verilog:
        raise ValueError("No frozen RTL inputs")
    files = sorted(
        verilog
        | configs
        | {
            "task1/physical/sky130_constraint.sdc",
            "task1/physical/post_route.tcl",
            "task1/physical/package_proxy.py",
            "task1/physical/sky130_package.py",
            "task1/physical/sky130_license.txt",
            f"{physical}/sky130_run.sh",
            f"{physical}/run_inside.sh",
            f"{physical}/package_run.py",
            f"{physical}/pg_audit.tcl",
            f"{physical}/pre_route.tcl",
            f"{physical}/record_environment.py",
            f"{physical}/plan.json",
        }
    )
    for relative in files:
        pinned = subprocess.check_output(
            ["git", "-C", str(source), "show", f"{args.sha}:{relative}"]
        )
        if (source / relative).read_bytes() != pinned:
            raise ValueError(f"Input is not identical to {args.sha}: {relative}")
    delivery = args.output / "delivery"
    plan = json.loads((source / physical / "plan.json").read_text())
    plan.update(run_id=args.run_id, top=args.top, rtl_commit=args.sha, config=args.config)
    if args.top == "sar20_digital_lms_top":
        plan["synth_memory_max_bits"] = 16384
        plan["floorplan"] = {
            "synthesized_cell_utilization_percent": 33,
            "aspect": 1,
            "margin_um": 20,
        }
    else:
        plan["floorplan"] = {"die_um": [0, 0, 450, 450], "core_um": [20, 20, 430, 430]}
    write_json(delivery / "plan.json", plan)
    write_json(delivery / "inputs.json", {name: describe_file(source / name) for name in files})


def read_pv(output: Path, result: Path, reports: Path, logs: Path) -> dict:
    """Separate public-deck measurements from router checks and missing decks."""
    status = {}
    for kind, candidate in (
        ("drc", reports / "6_drc.lyrdb"),
        ("lvs", result / "6_lvs.lvsdb"),
    ):
        log_path = logs / f"6_{kind}.log"
        report = output / f"{kind}_command.log"
        verdict_path = log_path if log_path.is_file() else report
        log = verdict_path.read_text(errors="replace")
        exit_code = int((output / f"{kind}_exit.txt").read_text())
        record = {"status": "BLOCKED", "exit_code": exit_code, "report": report.name}
        for artifact in (candidate, log_path):
            if artifact.is_file() and artifact.stat().st_size:
                shutil.copy2(artifact, output / artifact.name)
        if not candidate.is_file() or "not supported on this platform" in log.lower():
            record["detail"] = "No public deck report or tool did not complete"
        elif kind == "drc":
            try:
                items = ET.parse(candidate).getroot().find("items")
            except ET.ParseError:
                items = None
            if items is None:
                record["detail"] = "No KLayout DRC marker database"
            else:
                violations = len(items.findall("item"))
                record.update(
                    violations=violations,
                    status="PASS" if exit_code == 0 and violations == 0 else "FAIL",
                    report=candidate.name,
                )
        elif re.search(r"netlists? (?:do not match|don't match|mismatch)|lvs.*failed", log, re.I):
            record.update(status="FAIL", report=verdict_path.name)
        elif exit_code == 0 and re.search(r"netlists? match|lvs.*(?:passed|successful)", log, re.I):
            record.update(status="PASS", report=verdict_path.name)
        else:
            record["detail"] = "No explicit public-deck comparison verdict"
        status[kind] = record
    return status


def moscow(run_id: str, failed: bool, evidence: str, timing_pass: bool) -> dict:
    """Account for each canonical skill item in this academic proxy cohort."""
    must = (
        ("ENV", "PASS", "environment.json, tools.txt and completed public ORFS run"),
        (
            "HANDOFF",
            "PARTIAL",
            "inputs.json pins RTL and SDC. Integrated formal closure is pending.",
        ),
        ("PLAN", "PASS", "plan.json"),
        (
            "SYNTH",
            "PARTIAL",
            "synth_sta.rpt and mapped_netlist.v. Full RTL/netlist equivalence pending.",
        ),
        ("DFT", "NOT_RUN", "No approved scan/ATPG scope"),
        (
            "GATES",
            "PARTIAL",
            "Stage OpenDB PG reports. Fault-injection and cross-format checks pending.",
        ),
        ("EVID", "PARTIAL", evidence),
        ("PPA", "PASS" if timing_pass else "FAIL", "post_route_sta.rpt and metrics/6_report.json"),
        (
            "CR",
            "PASS" if failed else "NOT_RUN",
            "change_request.json" if failed else "No backpressure identified",
        ),
        ("ECO", "NOT_RUN", "No manual ECO to the final database"),
        (
            "PG",
            "PARTIAL",
            "pg_final.log and PROVENANCE.json. Release-grade PG rule/cross-format checks pending.",
        ),
        ("PI", "NOT_RUN", "Foundry EM rules, activity, and verified pad/bump sources unavailable"),
        (
            "RELGATE",
            "PARTIAL",
            "release_manifest.json binds the streamed GDS and PV input. Full signoff pending.",
        ),
        ("RELHASH", "PASS", "release_manifest.json artifact paths, bytes, mtime and sha256"),
        (
            "MOSCOW",
            "PARTIAL",
            "moscow.json canonical inventory. Run the skill checker on the downloaded artifact.",
        ),
    )
    should = (
        ("S1", "DEFERRED", "Frontend simulation and lint are separate from this physical run."),
        (
            "S2",
            "DEFERRED",
            "Block uses one synchronous clock; AMS boundary timing remains external.",
        ),
        ("S3", "DEFERRED", "Post-route formal equivalence not established."),
        ("S4", "DEFERRED", "Release-grade PG and pad/bump power sources are pending."),
        ("S5", "DEFERRED", "No routed activity annotation for the estimator."),
        ("S6", "DEFERRED", "Critical-path gate simulation with SDF not run."),
        ("S7", "DEFERRED", "Transistor and layout models of the analog blocks are external."),
    )
    could = (
        ("C1", "DEFERRED", "No full-chip supply and time-domain activity model."),
        ("C2", "DEFERRED", "No approved current-density and lifetime rule set."),
        ("C3", "DEFERRED", "Pad-ring and full-chip protection design are external."),
        ("C4", "DEFERRED", "No frozen ATPG target."),
        ("C5", "DEFERRED", "Published ORFS cohort uses TT/default RC."),
        ("C6", "NOT_APPLICABLE", "One fixed-voltage digital domain."),
    )
    return {
        "profile": "academic",
        "run_id": run_id,
        "claim": "PARTIAL_SIGNOFF",
        "must": [
            {"id": item, "status": status, "evidence": detail} for item, status, detail in must
        ],
        "should": [
            {"id": item, "considered": True, "decision": decision, "reason": reason}
            for item, decision, reason in should
        ],
        "could": [
            {"id": item, "considered": True, "decision": decision, "reason": reason}
            for item, decision, reason in could
        ],
        "wont": [],
    }


def package(args: argparse.Namespace) -> None:
    """Correlate GDS, timing and diagnostics with one immutable run."""
    flow_path = Path("sky130hd") / args.nickname / "base"
    delivery = args.output / "delivery"
    final = args.output / "flow/results" / flow_path
    logs = args.output / "flow/logs" / flow_path
    reports = args.output / "flow/reports" / flow_path
    timing_report = (delivery / "post_route_sta.rpt").read_text()
    finish = json.loads((logs / "6_report.json").read_text())
    route = json.loads((logs / "5_2_route.json").read_text())
    timing = read_timing(timing_report, finish)
    for src, dst in FINAL_FILES.items():
        shutil.copy2(final / src, delivery / dst.replace("module1_top", args.top))
    shutil.copy2(args.repo / "task1/physical/sky130_constraint.sdc", delivery / "constraint.sdc")
    shutil.copy2(args.output / "flow.log", delivery / "flow.log")
    if (reports / "synth_stat.txt").is_file():
        shutil.copy2(reports / "synth_stat.txt", delivery / "synth_stat.txt")
    metrics = delivery / "metrics"
    metrics.mkdir()
    for metric in sorted(logs.glob("*.json")):
        if metric.stat().st_size:
            shutil.copy2(metric, metrics / metric.name)

    stream = delivery / f"{args.top}.gds"
    gds = verify_gds(stream, expected_design=args.top)
    input_hash = (delivery / "pv_input.sha256").read_text().split()[0]
    if input_hash != describe_file(stream)["sha256"]:
        raise ValueError("Delivered GDS is different from PV input")
    pv = read_pv(delivery, final, reports, logs)
    power_grid = {}
    for stage in ("floorplan", "placement", "cts", "route", "before_extraction", "final"):
        path = delivery / f"pg_{stage}.log"
        content = path.read_text()
        if f"TASK2_PG_INVENTORY_COMPLETE {stage}" not in content:
            raise ValueError(f"PG inspection missing for {stage}")
        power_grid[stage] = {
            "log": path.name,
            "geometry": re.findall(r"^PG_GEOMETRY .+$", content, re.M),
        }
    write_json(
        delivery / "PROVENANCE.json",
        {
            "run_id": args.run_id,
            "top": args.top,
            "final_odb": describe_file(delivery / f"{args.top}.odb"),
            "pg_script": describe_file(args.repo / "task2/physical/pg_audit.tcl"),
            "stages": power_grid,
            "usable_for_pi": False,
            "remaining": "PG fault injection, PG rules and cross-format evidence",
        },
    )
    timing_pass = all(item["status"] == "PASS" for item in timing.values())
    route_checks = {
        key: int(route[f"detailedroute__{metric}"])
        for key, metric in (
            ("router_drc", "route__drc_errors"),
            ("antenna_nets", "antenna__violating__nets"),
            ("antenna_pins", "antenna__violating__pins"),
        )
    }
    failures = [name for name, result in {**timing, **pv}.items() if result["status"] == "FAIL"]
    failures.extend(name for name, count in route_checks.items() if count)
    if failures:
        write_json(
            delivery / "change_request.json",
            {
                "run_id": args.run_id,
                "source_commit": args.sha,
                "failures": failures,
                "critical_start_end": re.findall(
                    r"^(?:Startpoint|Endpoint):.+$", timing_report, re.M
                )[:2],
                "evidence": [
                    "post_route_sta.rpt",
                    "metrics/5_2_route.json",
                    "drc_command.log",
                    "lvs_command.log",
                ],
                "requested": "Confirm the failing path or rule, then preserve the SPEC and 6.25 ns SDC in any rerun.",
            },
        )
    write_json(
        delivery / "moscow.json",
        moscow(
            args.run_id,
            bool(failures),
            "TT extracted STA and public PV runs. Slow/fast corners not verified.",
            timing_pass,
        ),
    )
    manifest = {
        "claim": "PARTIAL_SIGNOFF",
        "run_id": args.run_id,
        "rtl_commit": args.sha,
        "top": args.top,
        "platform": "sky130hd",
        "corner": "TT 25 C, 1.80 V with extracted SPEF",
        "clock_period_ns": 6.25,
        "timing": timing,
        "physical_checks": {"router": route_checks, "public_decks": pv},
        "stream": {**gds, "pv_input_sha256": input_hash},
        "moscow_sha256": describe_file(delivery / "moscow.json")["sha256"],
        "artifacts": {
            path.relative_to(delivery).as_posix(): describe_file(path)
            for path in sorted(delivery.rglob("*"))
            if path.is_file() and path.stat().st_size
        },
    }
    write_json(delivery / "release_manifest.json", manifest)
    print(f"{args.top} {args.run_id}: {'TT PASS' if timing_pass else 'TT FAIL'}, {pv}")


def main() -> None:
    """Select the preflight or post-route packaging phase."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "package"))
    parser.add_argument("output", type=Path)
    parser.add_argument("repo", type=Path)
    parser.add_argument("top")
    parser.add_argument("nickname")
    parser.add_argument("config")
    parser.add_argument("sha")
    parser.add_argument("run_id")
    args = parser.parse_args()
    (freeze if args.action == "freeze" else package)(args)


if __name__ == "__main__":
    main()
