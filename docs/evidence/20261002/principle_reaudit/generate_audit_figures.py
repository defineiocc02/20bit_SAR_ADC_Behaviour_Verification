#!/usr/bin/env python3
"""Generate three source-bound audit figures; no RTL/EDA/analog simulation.

Uses current audit counts plus explicitly historical physical reports and the
finite control wrapper CSV. English labels avoid reliance on Chinese fonts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "bytes": path.stat().st_size}


def save(fig, out: Path, name: str) -> list[dict]:
    rows = []
    for ext in ("png", "pdf"):
        path = out / f"{name}.{ext}"
        metadata = {"Creator": "SAR audit source-data plotting", "CreationDate": None, "ModDate": None} if ext == "pdf" else {"Software": "Matplotlib scientific source-data figure"}
        fig.savefig(path, dpi=220, facecolor="white", metadata=metadata)
        rows.append(identity(path))
    plt.close(fig)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    audit, repo = args.audit_dir.resolve(), args.repo.resolve()
    out = audit / "figures"
    if out.exists():
        raise FileExistsError("Refusing to overwrite figure output directory")
    counts_path = audit / "synthesis_structure_counts.json"
    route_path = repo / "docs/evidence/20260930/shared_old_trial_route_25ns/out/internal_max_paths.rpt"
    csv_path = audit / "control_minimal_witness/structural_capture_trace.csv"
    counts = json.loads(counts_path.read_text())
    measured = counts["historical_actual"]
    if measured["source_commit"] != "71e7d5a017246340b9fa1f71cbbe99e20b6503f6":
        raise ValueError("Unexpected historical source identity")
    total_ff = measured["resources"]["ff"]
    store = counts["weight_store"]
    components = [store["proven_nonconstant_coefficient_bits"], store["loaded_bitmap_bits"], store["row_cache_bits"], total_ff - store["storage_candidate_ff_bits"]]
    if total_ff != 66492 or components != [60066, 1278, 972, 4176]:
        raise ValueError("FF source formulas or measurement changed")
    historic_store = next(r for r in measured["hierarchy"] if r["instance"] == "u_wstore")
    if sum(components[:3]) != historic_store["ff"]:
        raise ValueError("Source store decomposition does not reconcile to measured FF")
    route_text = route_path.read_text()
    rx = r"Data Path Delay:\s+([0-9.]+)ns\s+\(logic\s+([0-9.]+)ns.*?route\s+([0-9.]+)ns"
    match = re.search(rx, route_text)
    if not match:
        raise ValueError("Cannot parse the first historical routed data path")
    route_total, route_logic, route_net = map(float, match.groups())
    route_source = re.search(r"Source:\s+(\S+)", route_text)
    route_destination = re.search(r"Destination:\s+(\S+)", route_text)
    if not route_source or not route_destination:
        raise ValueError("Missing routed endpoints")
    synth = measured["synthesis_worst_setup"]
    synth_total, synth_logic, synth_net = synth["data_delay_ns"], synth["logic_delay_ns"], synth["estimated_route_delay_ns"]
    if [route_total, route_logic, route_net] != [23.914, 5.291, 18.623] or [synth_total, synth_logic, synth_net] != [14.312, 8.404, 5.908]:
        raise ValueError("Historical delay readings changed")
    if abs(synth_total - synth_logic - synth_net) > 1e-9 or abs(route_total - route_logic - route_net) > 1e-9:
        raise ValueError("Historical path decomposition does not sum")
    with csv_path.open(newline="") as f:
        trace = [{k: int(v) for k, v in r.items()} for r in csv.DictReader(f)]
    edges = {r["edge"]: r for r in trace}
    accepted = [r for r in trace if r["post_accept"] == 1]
    if len(trace) != 1926 or len(accepted) != 57:
        raise ValueError("Unexpected finite wrapper trace coverage")
    for row in accepted:
        if (row["context_age"], row["fine_code_age"], row["pre_phase"], row["launch"], row["rst_n"], row["enable"]) != (15, 1, 15, 1, 1, 1):
            raise ValueError("Accepted capture age/control contract differs")
        context = edges[row["edge"] - row["context_age"]]
        fine = edges[row["edge"] - row["fine_code_age"]]
        if context["pre_phase"] != 0 or context["quiet"] != 1 or context["post_context_id"] != row["post_accepted_id"]:
            raise ValueError("Accepted ID lacks the recorded quiet context transfer")
        if fine["pre_phase"] != 14 or fine["post_fine_code"] != row["pre_fine_code"]:
            raise ValueError("Accepted fine code lacks the recorded P14 capture")
    data = {
        "scope": "SOURCE_FORMULAS_HISTORICAL_FPGA_AND_FINITE_CONTROL_TRACE_NO_NEW_EDA_NO_ANALOG_NO_FMAX",
        "generator": identity(Path(__file__)), "inputs": [identity(counts_path), identity(route_path), identity(csv_path)],
        "historical_physical_source_commit": measured["source_commit"], "current_source_head": counts["head"],
        "historical_profile": "P_STRUCTURAL=1, P_RECON_STAGES=5, Vivado 2018.3, xc7vx690tffg1761-2",
        "ff_allocation": {"categories": ["Coefficient data", "Loaded bitmap", "Exact row cache", "Other core FF"], "values": components,
                          "total_measured_ff": total_ff, "measured_store_ff": historic_store["ff"],
                          "method": "Three store source formulas reconcile to historical store FF; other is measured total minus store"},
        "path_composition": [
            {"stage": "Post-synthesis", "source": synth["source"], "destination": synth["destination"], "logic_ns": synth_logic, "interconnect_ns": synth_net, "total_ns": synth_total, "interconnect_method": "Estimated"},
            {"stage": "Post-route", "source": route_source.group(1), "destination": route_destination.group(1), "logic_ns": route_logic, "interconnect_ns": route_net, "total_ns": route_total, "interconnect_method": "Actual routed"}],
        "control_capture": {"trace_edges": len(trace), "accepted_cases": len(accepted),
                            "cases": [{"case": i + 1, "edge": r["edge"], "sample_id": r["post_accepted_id"], "context_age_cycles": r["context_age"], "fine_code_age_cycles": r["fine_code_age"]} for i, r in enumerate(accepted)],
                            "first_context_transfer_edge": accepted[0]["edge"] - 15,
                            "first_fine_code_capture_edge": accepted[0]["edge"] - 1,
                            "scope": "structural_protocol_tb wrapper and shadow stage-A only; no weight/recon physical datapath, analog settling, full-state MCP proof, re-enable or full config-clear coverage"},
    }
    out.mkdir()
    data_path = out / "audit_figures_source_data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "pdf.fonttype": 42,
                         "axes.labelcolor": "#243244", "text.color": "#243244", "xtick.color": "#243244", "ytick.color": "#243244"})
    navy, blue, teal, amber = "#285B9E", "#558CC7", "#33A397", "#D68B2D"
    files = []

    fig = plt.figure(figsize=(11.8, 6.8))
    fig.suptitle("Historical P5: flip-flop allocation (66,492 FF)", x=.06, y=.96, ha="left", fontsize=17, fontweight="bold")
    fig.text(.06, .906, "Weight-store formulas reconciled to the measured full-core synthesis total", fontsize=11)
    ax = fig.add_axes([.08, .62, .66, .19])
    left = 0
    colors = [navy, blue, teal, amber]
    for value, color in zip(components, colors):
        ax.barh([0], [value], left=left, height=.5, color=color, edgecolor="white", linewidth=1)
        left += value
    ax.text(components[0] / 2, 0, "60,066\ncoefficient data", ha="center", va="center", color="white", fontsize=13, fontweight="bold")
    ax.set_xlim(0, 69000); ax.set_yticks([]); ax.set_xlabel("Flip-flops (historical measured total)")
    ax.spines["left"].set_visible(False)
    for i, (label, value, color) in enumerate(zip(data["ff_allocation"]["categories"], components, colors)):
        fig.text(.77, .78 - .05 * i, "■", color=color, fontsize=15)
        fig.text(.795, .78 - .05 * i, f"{label}: {value:,}", fontsize=10)
    ax2 = fig.add_axes([.18, .28, .74, .19])
    labels = ["Loaded bitmap", "Exact row cache", "Other core FF"]
    y = np.arange(3)
    ax2.barh(y, components[1:], color=colors[1:], height=.62)
    for i, value in enumerate(components[1:]):
        ax2.text(value + 75, i, f"{value:,}", va="center", fontsize=11)
    ax2.set_yticks(y, labels); ax2.invert_yaxis(); ax2.set_xlim(0, 4700)
    ax2.set_xlabel("Detail of the remaining 6,426 FF (separate scale)")
    ax2.spines["left"].set_visible(False)
    fig.text(.06, .10, "Store = 60,066 + 1,278 + 972 = 62,316 FF (93.72% of the measured core total).", fontsize=11)
    fig.text(.06, .057, "71e7d5a source / P5 / Vivado 2018.3. FF counts are not ASIC cell area or measured power.", fontsize=9)
    files += save(fig, out, "01_historical_ff_allocation")

    fig = plt.figure(figsize=(12.6, 7.0))
    fig.suptitle("Historical P5 timing: logic and interconnect", x=.05, y=.956, ha="left", fontsize=17, fontweight="bold")
    fig.text(.05, .899, "Two different critical endpoint pairs; estimated and routed paths must be read separately", fontsize=11)
    ax = fig.add_axes([.18, .41, .76, .37])
    vals = [(synth_logic, synth_net, synth_total), (route_logic, route_net, route_total)]
    for y, (logic, net, total) in zip([1, 0], vals):
        ax.barh(y, logic, height=.52, color=navy)
        ax.barh(y, net, left=logic, height=.52, color=amber)
        ax.text(logic / 2, y, f"{logic:.3f}", ha="center", va="center", color="white", fontsize=12, fontweight="bold")
        ax.text(logic + net / 2, y, f"{net:.3f}", ha="center", va="center", color="#202733", fontsize=12, fontweight="bold")
        ax.text(total + .35, y, f"{total:.3f} ns", va="center", fontsize=11, fontweight="bold")
    ax.set_yticks([1, 0], ["Post-synthesis\nEstimated net delay", "Post-route\nActual routed net delay"])
    ax.set_xlim(0, 27); ax.set_ylim(-.7, 1.7); ax.set_xlabel("Data-path delay (ns)")
    ax.spines["left"].set_visible(False); ax.grid(axis="x", alpha=.18); ax.set_axisbelow(True)
    fig.text(.60, .825, "■ Logic/cell", color=navy, fontsize=11)
    fig.text(.74, .825, "■ Interconnect/net", color=amber, fontsize=11)
    fig.text(.05, .285, "Post-synthesis endpoints:", fontsize=10, fontweight="bold")
    fig.text(.05, .247, "u_recon/u_div/rem_reg[0]/C  →  u_recon/u_div/q_reg[60]/D", fontsize=10)
    fig.text(.05, .190, "Post-route endpoints:", fontsize=10, fontweight="bold")
    fig.text(.05, .152, "u_structure/u_context/fine_slices_reg[1][3]/C  →  u_recon/rails_s_reg[65]/D", fontsize=10)
    fig.text(.05, .069, "Routed interconnect accounts for 77.875% of 23.914 ns. Data-delay reciprocals are not Fmax.", fontsize=10)
    fig.text(.05, .032, "71e7d5a / P5 / historical FPGA reports. No new route, ASIC timing or analog settling was measured.", fontsize=9)
    files += save(fig, out, "02_historical_critical_path_composition")

    fig = plt.figure(figsize=(12.0, 7.3))
    fig.suptitle("Finite control trace: stable input age at 57 accepted events", x=.06, y=.962, ha="left", fontsize=16, fontweight="bold")
    fig.text(.06, .907, "Observed wrapper CSV: 1,926 clock edges; all accepted captures satisfy context 15T / fine code 1T", fontsize=10.7)
    ax = fig.add_axes([.085, .49, .83, .30])
    cases = np.arange(1, len(accepted) + 1)
    ax.scatter(cases, [r["context_age"] for r in accepted], color=navy, s=24, label="Frozen context: 15 cycles", zorder=3)
    ax.scatter(cases, [r["fine_code_age"] for r in accepted], color=amber, marker="s", s=22, label="Fine code: 1 cycle", zorder=3)
    ax.set_xlim(0, 58); ax.set_ylim(0, 17); ax.set_xticks([1, 10, 20, 30, 40, 50, 57]); ax.set_yticks([1, 5, 10, 15])
    ax.set_xlabel("Accepted event number (CSV order)"); ax.set_ylabel("Clock intervals since capture")
    ax.grid(alpha=.18); ax.legend(loc="center right", framealpha=1, edgecolor="#e2e7ef", fontsize=10)
    first = accepted[0]
    ax2 = fig.add_axes([.085, .21, .83, .12])
    ax2.plot([-16, 1], [0, 0], color="#94a1b5", linewidth=2)
    ax2.scatter([-15, -1, 0], [0, 0, 0], c=[navy, amber, teal], s=[85, 85, 85], zorder=3)
    ax2.set_xlim(-16, 1); ax2.set_ylim(-1, 1); ax2.set_yticks([]); ax2.set_xticks([])
    ax2.spines["left"].set_visible(False); ax2.spines["bottom"].set_visible(False)
    ax2.text(-15, .5, f"-15T: quiet / P0\ncontext ID {first['post_accepted_id']}\nedge {first['edge']-15}", ha="left", va="bottom", fontsize=9.5, color=navy)
    ax2.text(-1, .5, f"-1T: P14 fine code\nedge {first['edge']-1}", ha="right", va="bottom", fontsize=9.5, color=amber)
    ax2.text(0, -.35, f"0T: P15 wire launch / shadow stage-A\nedge {first['edge']}, ID {first['post_accepted_id']}", ha="right", va="top", fontsize=9.5, color=teal)
    fig.text(.06, .069, "Observed digital control boundaries only. Stage-A is a shadow monitor; the real weight/recon datapath is absent.", fontsize=9)
    fig.text(.06, .034, "No analog waveform/settling, complete re-enable/config-clear coverage, full-state MCP proof or STA signoff.", fontsize=9)
    files += save(fig, out, "03_control_capture_age_evidence")
    manifest = {"scope": data["scope"], "matplotlib_version": matplotlib.__version__,
                "source_data": identity(data_path), "generator": identity(Path(__file__)),
                "figures": files, "visual_review": "PENDING"}
    (out / "audit_figures_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "GENERATED", "png_pdf_pairs": 3, "out": str(out), "data_sha256": sha(data_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
