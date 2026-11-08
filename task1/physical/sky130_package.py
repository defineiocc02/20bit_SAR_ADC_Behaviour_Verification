#!/usr/bin/env python3
# =============================================================================
# 文件名     : sky130_package.py
# 模块名     : sky130_package
# 版本       : v1.0
# 功能       : Validate and package the public SKY130HD routed module one run.
#
# 设计说明   :
#   * 承担 物理流程辅助脚本 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# ===========================================================================

"""Validate and package the public SKY130HD routed module one run."""

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

# 本文件有两种入口：直接当脚本跑（cd task1/physical && python sky130_package.py），
# 或被 task2/physical/package_run.py 当模块导入。同目录的 package_proxy 不属于任何包，
# 两种入口的 sys.path[0] 不同，因此把自身目录显式加入搜索路径，避免换入口就 ImportError。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from package_proxy import FINAL_FILES, describe_file, verify_gds

TOP_CONFIGS = {
    "module1_top": ("task1_module1", "sky130_config.mk"),
    "module1_bridge_top": ("task1_module1_bridge", "sky130_bridge_config.mk"),
}
BASE_INPUTS = (
    "rtl/slice_scheduler.sv",
    "rtl/fig11_dem.sv",
    "rtl/module1_top.sv",
    "physical/sky130_constraint.sdc",
    "physical/sky130_run.sh",
    "physical/sky130_package.py",
    "physical/sky130_license.txt",
    "physical/post_route.tcl",
    "physical/package_proxy.py",
)


# =========================================================================
# 1. 函数组：read_timing / package / main
# =========================================================================
# -------------------------------------------------------------------------
# Read both extracted timing senses and compare them with ORFS metrics.
# Collect actual artifacts even if the frozen clock constraint fails.
# Parse the artifact-packaging paths.
# ------------------------------------------------------------------------
def read_timing(report: str, metrics: dict) -> dict:
    """Read both extracted timing senses and compare them with ORFS metrics."""
    if "TASK1_POST_ROUTE_STA_COMPLETE" not in report or "Error:" in report:
        raise ValueError("Extracted STA did not complete")
    summary = {}
    for mode, sense, ending in (
        ("setup", "max", "=== Hold, extracted RC ==="),
        ("hold", "min", "=== Final proxy power-grid connectivity ==="),
    ):
        section = report.partition(f"=== {mode.title()}, extracted RC ===")[2]
        section = section.partition(ending)[0]
        if "Startpoint:" not in section:
            raise ValueError(f"No analyzed {mode} path")
        values = {}
        for label, metric in (("worst slack", "ws"), ("tns", "tns")):
            match = re.search(rf"^{label} {sense} ([+-]?[0-9.]+)$", section, re.MULTILINE)
            if match is None:
                raise ValueError(f"Missing reported {mode} {label}")
            value = float(metrics[f"finish__timing__{mode}__{metric}"])
            if not math.isfinite(value) or not math.isclose(
                float(match.group(1)), value, rel_tol=6e-6, abs_tol=0.001
            ):
                raise ValueError(f"Reopened STA differs from ORFS {mode} {label}")
            values[metric] = value
        violations = int(metrics[f"finish__timing__drv__{mode}_violation_count"])
        summary[mode] = {
            "worst_slack_ns": values["ws"],
            "wns_ns": min(values["ws"], 0.0),
            "tns_ns": values["tns"],
            "violations": violations,
            "status": "PASS" if values["ws"] >= 0 and violations == 0 else "FAIL",
        }
    return summary


# ------------------------------------------------------------------------
def package(output: Path, source: Path, top: str) -> None:
    """Collect actual artifacts even if the frozen clock constraint fails."""
    nickname, config = TOP_CONFIGS[top]
    input_files = (*BASE_INPUTS, f"physical/{config}")
    if top == "module1_bridge_top":
        input_files += ("rtl/fig21_bridge.sv", "rtl/module1_bridge_top.sv")
    flow_path = Path("sky130hd") / nickname / "base"
    delivery = output / "delivery"
    final = output / "flow/results" / flow_path
    logs = output / "flow/logs" / flow_path
    sta_report = (delivery / "post_route_sta.rpt").read_text()
    finish_metrics = json.loads((logs / "6_report.json").read_text())
    route_metrics = json.loads((logs / "5_2_route.json").read_text())
    timing = read_timing(sta_report, finish_metrics)
    rtl_commit = (delivery / "rtl_commit.txt").read_text().strip()
    container_digest = (delivery / "container.txt").read_text().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", rtl_commit):
        raise ValueError("Missing RTL commit identity")
    if not re.search(r"@sha256:[0-9a-f]{64}$", container_digest):
        raise ValueError("Missing container digest")

    for old_name, new_name in FINAL_FILES.items():
        describe_file(final / old_name)
        shutil.copy2(final / old_name, delivery / new_name.replace("module1_top", top))
    shutil.copy2(source / "physical/sky130_constraint.sdc", delivery / "constraint.sdc")
    shutil.copy2(output / "flow.log", delivery / "flow.log")
    metrics_path = delivery / "metrics"
    metrics_path.mkdir()
    for path in sorted(logs.glob("*.json")):
        if path.stat().st_size:
            shutil.copy2(path, metrics_path / path.name)

    gds = verify_gds(delivery / f"{top}.gds", expected_design=top)
    drc_errors = int(route_metrics["detailedroute__route__drc_errors"])
    power_grid = {
        net: "PASS" if f"All shapes on net {net} are connected." in sta_report else "UNVERIFIED"
        for net in ("VDD", "VSS")
    }
    manifest = {
        "claim": "PUBLIC_SKY130HD_IMPLEMENTATION",
        "platform": "SkyWater SKY130HD",
        "top": top,
        "liberty": "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "corner": {"process": "TT", "temperature_c": 25, "voltage_v": 1.8},
        "clock_period_ns": 6.25,
        "io_budget": {
            "input_min_ns": 0.20,
            "input_max_ns": 0.80,
            "input_transition_ns": 0.10,
            "output_min_ns": -0.20,
            "output_max_ns": 0.80,
            "output_load_ff": 5.0,
            "setup_uncertainty_ns": 0.10,
            "hold_uncertainty_ns": 0.05,
        },
        "rtl_commit": rtl_commit,
        "container_digest": container_digest,
        "gds": gds,
        "timing": timing,
        "physical_checks": {
            "router_drc_errors": drc_errors,
            "router_antenna_violating_nets": int(
                route_metrics["detailedroute__antenna__violating__nets"]
            ),
            "router_antenna_violating_pins": int(
                route_metrics["detailedroute__antenna__violating__pins"]
            ),
            "final_power_grid_connectivity": power_grid,
            "independent_drc": "NOT_RUN",
            "independent_lvs": "NOT_RUN",
        },
        "inputs": {name: describe_file(source / name) for name in input_files},
        "artifacts": {
            path.relative_to(delivery).as_posix(): describe_file(path)
            for path in sorted(delivery.rglob("*"))
            if path.is_file()
        },
        "scope": {
            "rtl_to_gds": "completed",
            "post_route_setup_and_hold": "single TT corner with extracted SPEF",
            "logical_equivalence": "not asserted by this flow",
            "foundry_release": "not evaluated",
        },
    }
    (delivery / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified SKY130HD GDS top {gds['tops'][0]} and extracted timing")
    for mode, result in timing.items():
        print(f"{mode}: {result['status']}, worst slack {result['worst_slack_ns']:.4f} ns")


# ------------------------------------------------------------------------
def main() -> None:
    """Parse the artifact-packaging paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("task_source", type=Path)
    parser.add_argument("--top", choices=tuple(TOP_CONFIGS), default="module1_top")
    args = parser.parse_args()
    package(args.output, args.task_source, args.top)


if __name__ == "__main__":
    main()
