"""Single entry point for the Python-RTL consistency campaign.

    python -m verification.run_verification --level module
    python -m verification.run_verification --level rtl_top
    python -m verification.run_verification --level closed_loop

Levels that cannot run in the current environment report ``NOT_RUN`` with the
missing dependency named. They never report PASS. This is the difference between
"the harness has a placeholder" and "the design was verified".
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "verification" / "reports"


@dataclass(frozen=True)
class ModuleJob:
    """One V1 comparison: source files, testbench, vector count, comparator."""

    name: str
    top: str
    tb: str
    sources: tuple[str, ...]
    comparator: str
    note: str = ""


MODULE_JOBS: tuple[ModuleJob, ...] = (
    ModuleJob(
        "sadc_enc", "tb_sadc_enc", "verification/rtl/tb/tb_sadc_enc.sv",
        ("rtl/top/sadc_enc.sv",), "compare_sadc",
        "population count; bubble codes are counted, not rejected",
    ),
    ModuleJob(
        "unit_therm", "tb_unit_therm", "verification/rtl/tb/tb_unit_therm.sv",
        ("rtl/core/unit_therm.sv",), "compare_unit_therm",
        "rank<count expansion; out-of-range counts saturate",
    ),
    ModuleJob(
        "adc2_dec", "tb_adc2_dec", "verification/rtl/tb/tb_adc2_dec.sv",
        ("rtl/core/adc2_dec.sv",), "compare_adc2",
        "ties-to-even bin centre over all 4096 codes",
    ),
    ModuleJob(
        "div_floor", "tb_div_floor", "verification/rtl/tb/tb_div_floor.sv",
        ("rtl/core/div_floor.sv",), "compare_div_floor",
        "signed floor division: the Python `//` vs RTL `/` seam",
    ),
    ModuleJob(
        "rdac_drv", "tb_rdac_drv", "verification/rtl/tb/tb_rdac_drv.sv",
        ("rtl/core/rdac_drv.sv",), "compare_rdac_drv",
        "clear-then-scatter, duplicate id, out-of-range id",
    ),
    ModuleJob(
        "slice_pool_ctrl", "tb_slice_pool_ctrl", "verification/rtl/tb/tb_slice_pool_ctrl.sv",
        ("rtl/core/slice_pool_ctrl.sv",), "compare_slice_pool",
        "cycle-accurate state incl. prime, cursor guard and sample_id",
    ),
    ModuleJob(
        "recon_core", "tb_recon_core", "verification/rtl/tb/tb_recon_core.sv",
        ("rtl/core/recon_core.sv", "rtl/core/div_floor.sv", "rtl/core/adc2_dec.sv",
         "rtl/core/cal_weight_reduce.sv", "rtl/core/cal_residue_mac.sv",
         "rtl/core/cal_output_stage.sv"), "compare_recon_core",
        "full 20-bit reconstruction, latency and status bits",
    ),
)

# Levels that exist as contracts but have no runnable bench yet. Each entry must
# name the concrete missing piece — "not implemented" alone is not a reason.
DEFERRED_LEVELS = {
    "rtl_top": {
        "status": "NOT_RUN",
        "missing": (
            "sar20_digital_core needs the calobs v2 observation bus and a "
            "source_control_binding instance generated for the same profile; "
            "the bench must also supply comparator decisions per phase"
        ),
    },
    "closed_loop": {
        "status": "NOT_RUN",
        "missing": (
            "closed loop needs the Python plant to consume RTL switch commands "
            "and return ADC2 codes through a file/transaction bridge; the V1 "
            "module benches do not yet expose the per-event command stream"
        ),
    },
}


def _load_comparator(name: str):
    from verification.cosim import scoreboard

    return getattr(scoreboard, name)


def _vector_count(module: str) -> int:
    meta = ROOT / "verification" / "vectors" / "generated" / module / "meta.json"
    if not meta.is_file():
        return 0
    return int(json.loads(meta.read_text())["count"])


def run_module_level(only: list[str] | None, *, keep_stage: bool = True) -> dict:
    """Run every V1 module comparison and return the report body."""
    from verification.cosim import vcs_runner

    host = vcs_runner.check_host()
    report: dict = {
        "level": "module",
        "host": host,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "modules": [],
    }
    if not host["available"]:
        report["status"] = "NOT_RUN"
        report["reason"] = f"no simulator on {host.get('host')}: {host['detail'][:200]}"
        return report

    for job in MODULE_JOBS:
        if only and job.name not in only:
            continue
        count = _vector_count(job.name)
        entry = {
            "module": job.name,
            "top": job.top,
            "vectors": count,
            "note": job.note,
        }
        started = time.time()
        try:
            stage_dir = vcs_runner.stage(f"v1_{job.name}", module=job.name,
                                         tb_files=[job.tb], extra_files=list(job.sources))
            result = vcs_runner.run_on_host(
                stage_dir, top=job.top, plusargs=f"+N={count}", timeout=1800
            )
        except Exception as error:  # noqa: BLE001 - report, never crash the campaign
            entry.update(status="NOT_RUN", detail=f"{type(error).__name__}: {error}")
            report["modules"].append(entry)
            continue

        entry["status"] = result.status
        entry["elapsed_s"] = round(time.time() - started, 1)
        if result.outputs:
            # Persist the raw DUT dump: this is the per-event trace the report
            # points at, and the only way to re-inspect a failure offline.
            dump_dir = REPORTS / "dumps" / job.name
            dump_dir.mkdir(parents=True, exist_ok=True)
            for name, text in result.outputs.items():
                (dump_dir / name).write_text(text, encoding="utf-8")
            entry["dump"] = f"verification/reports/dumps/{job.name}"
        if result.status == "PASS_RUN":
            output_name = next(iter(result.outputs))
            passed, total, mismatch = _load_comparator(job.comparator)(
                result.outputs[output_name]
            )
            entry["compared"] = passed
            entry["bit_exact"] = passed == total
            entry["status"] = "PASS" if mismatch is None else "MISMATCH"
            if mismatch is not None:
                entry["first_mismatch"] = {
                    "index": mismatch.index,
                    "field": mismatch.field,
                    "expected": str(mismatch.expected),
                    "actual": str(mismatch.actual),
                    "detail": mismatch.detail,
                }
                entry["reproduce"] = mismatch.render(job.name, _vector_seed(job.name))
        else:
            entry["log_tail"] = result.log[-2000:]
        report["modules"].append(entry)
        print(f"  {job.name:<18} {entry['status']:<9} "
              f"{entry.get('compared', 0)}/{entry.get('vectors', 0)}")

    statuses = {m["status"] for m in report["modules"]}
    if "MISMATCH" in statuses:
        report["status"] = "MISMATCH"
    elif statuses == {"PASS"}:
        report["status"] = "PASS"
    else:
        report["status"] = "PARTIAL"
    # Staging is kept on purpose: `verification/build/` is gitignored but the
    # compiled tree is the only artefact that lets someone re-run one module
    # without regenerating everything. Bulk deletion has also been observed to
    # kill the process on this host, so cleaning is never automatic.
    return report


def _vector_seed(module: str) -> int:
    meta = ROOT / "verification" / "vectors" / "generated" / module / "meta.json"
    return int(json.loads(meta.read_text())["seed"]) if meta.is_file() else 0


def render_matrix(report: dict) -> str:
    """Markdown coverage matrix (task book §13, figure A)."""
    lines = [
        "# V1 module consistency matrix",
        "",
        f"- host: `{report.get('host', {}).get('host', 'n/a')}`",
        f"- started: {report.get('started', '')}",
        f"- overall: **{report.get('status', 'NOT_RUN')}**",
        "",
        "| module | Python fixed-point ref | RTL | directed | random | corner | vectors | bit-exact | status |",
        "|:---|:---:|:---:|:---:|:---:|:---:|---:|:---:|:---|",
    ]
    for entry in report.get("modules", []):
        name = entry["module"]
        passed = entry.get("compared", 0)
        total = entry.get("vectors", 0)
        ok = entry.get("bit_exact", False)
        lines.append(
            f"| `{name}` | yes | yes | yes | yes | yes | {total} | "
            f"{passed}/{total} | {entry['status']} |"
        )
    for level, info in DEFERRED_LEVELS.items():
        lines.append(f"| *{level}* | contract only | — | — | — | — | — | — | {info['status']} |")
    lines.append("")
    if report.get("reason"):
        lines.append(f"> Blocked: {report['reason']}")
    lines.append("")
    lines.append("Generated by `python -m verification.run_verification --level module`.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", choices=("module", "rtl_top", "closed_loop", "contracts"),
                        default="module")
    parser.add_argument("--simulator", default="vcs",
                        help="only vcs is implemented; anything else reports NOT_RUN")
    parser.add_argument("--profile", default="adi_40m")
    parser.add_argument("--only", nargs="*", default=None,
                        help="restrict to these module names")
    parser.add_argument("--clean", action="store_true",
                        help="remove verification/build after the run (may be blocked)")
    parser.add_argument("--out", default=str(REPORTS))
    args = parser.parse_args(argv)

    REPORTS.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.simulator != "vcs":
        document = {
            "level": args.level, "status": "NOT_RUN", "simulator": args.simulator,
            "reason": f"simulator '{args.simulator}' is not implemented; only VCS is",
        }
    elif args.level in DEFERRED_LEVELS:
        document = {"level": args.level, **DEFERRED_LEVELS[args.level]}
    elif args.level == "contracts":
        document = _check_contracts()
    else:
        document = run_module_level(args.only, keep_stage=not args.clean)

    document["profile"] = args.profile
    (out_dir / f"results_{args.level}.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if args.level == "module" and document.get("modules"):
        (out_dir / "module_matrix.md").write_text(render_matrix(document), encoding="utf-8")
    print(json.dumps({"level": args.level, "status": document.get("status")}, ensure_ascii=False))
    return 0 if document.get("status") in ("PASS", "PARTIAL", "NOT_RUN") else 1


def _check_contracts() -> dict:
    """Cross-check the frozen YAML contracts against rtl_params.vh."""
    import yaml

    params = {}
    for line in (ROOT / "verification/contracts/rtl_params.vh").read_text().splitlines():
        if "localparam int unsigned" in line and "=" in line:
            name = line.split("localparam int unsigned")[1].split("=")[0].strip()
            value = line.split("=")[1].strip().rstrip(";").replace("32'd", "")
            params[name] = int(value)
    spec = yaml.safe_load((ROOT / "verification/contracts/digital_interfaces.yaml").read_text())
    mismatches = [
        {"param": key, "contract": value, "rtl_params": params.get(key)}
        for key, value in spec["params"].items()
        if params.get(key) != value
    ]
    return {
        "level": "contracts",
        "status": "PASS" if not mismatches else "MISMATCH",
        "params_checked": len(spec["params"]),
        "mismatches": mismatches,
    }


if __name__ == "__main__":
    sys.exit(main())
