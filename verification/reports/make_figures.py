"""Verification figures from the recorded results — never from hand-drawn curves.

Every figure reads a JSON produced by an actual run and stamps the source SHA.
Figures whose input is missing are skipped and reported as NOT_RUN; nothing is
interpolated to fill a panel.

    python -m verification.reports.make_figures
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "verification" / "reports"
FIG = REPORTS / "figures"


def _sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _load(name: str) -> dict | None:
    path = REPORTS / name
    return json.loads(path.read_text()) if path.is_file() else None


def figure_a_matrix(results: dict) -> dict:
    """Figure A: per-module consistency matrix as a pass/fail grid."""
    modules = results.get("modules", [])
    if not modules:
        return {"figure": "A_module_matrix.png", "status": "NOT_RUN", "reason": "no module results"}

    # The deferred levels are drawn as grey rows on purpose: a matrix that only
    # shows what passed is marketing, not coverage.
    deferred = [("rtl_top (V3)", 0), ("closed_loop (V4)", 0)]

    names = [m["module"] for m in modules] + [name for name, _ in deferred]
    columns = ["Python ref", "RTL run", "bit-exact", "corner"]
    grid = np.full((len(names), len(columns)), np.nan)
    for row, entry in enumerate(modules):
        ran = entry.get("status") in ("PASS", "MISMATCH")
        exact = 1.0 if entry.get("bit_exact") else (0.5 if ran else 0.0)
        grid[row] = [1.0, 1.0 if ran else 0.0, exact, 1.0 if ran else 0.0]

    fig, ax = plt.subplots(figsize=(6.4, 0.42 * len(names) + 1.8), constrained_layout=True)
    cmap = matplotlib.colormaps["RdYlGn"].copy()
    cmap.set_bad("#d9d9d9")
    ax.imshow(np.ma.masked_invalid(grid), cmap=cmap, vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(columns)), columns)
    ax.set_yticks(range(len(names)), names)
    for row in range(len(modules)):
        for col in range(len(columns)):
            entry = modules[row]
            text = f"{entry.get('compared', 0)}/{entry.get('vectors', 0)}" if col == 2 else ""
            ax.text(col, row, text or ("ok" if grid[row, col] >= 0.99 else "—"),
                    ha="center", va="center", fontsize=8, color="black")
    for offset, (label, _) in enumerate(deferred):
        ax.text(1.5, len(modules) + offset, "NOT_RUN", ha="center", va="center",
                fontsize=8, color="#8b0000")
    ax.set_title(
        f"Figure A  V1 module consistency, with deferred levels shown grey  "
        f"(source {_sha()[:8]})", fontsize=9)
    fig.savefig(FIG / "A_module_matrix.png", dpi=140)
    plt.close(fig)
    return {
        "figure": "A_module_matrix.png",
        "status": "PASS",
        "modules": names,
        "source_sha": _sha(),
        "data": "verification/reports/results_module.json",
    }


def figure_c_fixed_point_error() -> dict:
    """Figure C: float vs fixed-point reconstruction error on the same vectors.

    Bit-exactness against the RTL is already 100% by construction (the reference
    is integer); what this panel shows is how far a *float* evaluation of the
    same formula would drift, i.e. the fixed-point quantisation cost.
    """
    from verification.cosim.scoreboard import VECTORS, load_mem
    from verification.reference.datapath_ref import (
        WEIGHT_SCALE, VOLTAGE_SCALE, OUTPUT_COUNT,
        adc2_bin_center, recon_terms,
    )
    from verification.vectors.generate import gen_recon_core

    base = VECTORS / "recon_core"
    if not (base / "meta.json").is_file():
        return {"figure": "C_fixed_point_error.png", "status": "NOT_RUN", "reason": "no vectors"}

    seed = json.loads((base / "meta.json").read_text())["seed"]
    rom = gen_recon_core(seed).extra["w_rom"]  # type: ignore[attr-defined]
    ids = load_mem(base / "slice_id.mem", bits=40)
    mains = load_mem(base / "main_on.mem", bits=504)
    subs = load_mem(base / "sub_on.mem", bits=64)
    codes = load_mem(base / "adc2_code.mem", bits=12)
    injs = load_mem(base / "inj_q.mem", signed=True, bits=64)
    offs = load_mem(base / "offset_q.mem", signed=True, bits=64)
    mins = load_mem(base / "adc2_min_q.mem", signed=True, bits=64)
    maxs = load_mem(base / "adc2_max_q.mem", signed=True, bits=64)

    errors_lsb = []
    exact = 0
    for index in range(len(ids)):
        weights, on = [], []
        for lane in range(8):
            sid = (ids[index] >> (5 * lane)) & 0x1F
            for unit in range(71):
                sel = (
                    (mains[index] >> (lane * 63 + unit)) & 1
                    if unit < 63
                    else (subs[index] >> (lane * 8 + (unit - 63))) & 1
                )
                weights.append(int(rom[sid][unit]))
                on.append(sel)
        terms = recon_terms(weights, on, [1 - 2 * o for o in on], gain_mode="all_signal")
        fine, _ = adc2_bin_center(codes[index], mins[index], maxs[index], 12)
        # Integer path (what the RTL does).
        numerator = (fine - offs[index]) * WEIGHT_SCALE - terms.rails_q30 * VOLTAGE_SCALE \
            - terms.total_q30 * injs[index]
        shifted = (numerator + terms.gain_q30 * VOLTAGE_SCALE) * OUTPUT_COUNT
        fixed_word = int(np.floor(shifted / (terms.gain_q30 * (1 << 33))))
        # Float path: same formula, IEEE doubles, no integer discipline.
        float_word = (
            ((fine - offs[index]) * WEIGHT_SCALE - terms.rails_q30 * VOLTAGE_SCALE
             - terms.total_q30 * injs[index]) + terms.gain_q30 * VOLTAGE_SCALE
        ) * OUTPUT_COUNT / (terms.gain_q30 * (1 << 33))
        errors_lsb.append(float_word - fixed_word)
        if abs(float_word - fixed_word) < 0.5:
            exact += 1

    errors = np.asarray(errors_lsb)
    fig, ax = plt.subplots(figsize=(6.6, 3.8), constrained_layout=True)
    ax.hist(errors, bins=24, color="#4C78A8", edgecolor="white")
    ax.set_xlabel("exact (float) result − fixed-point word  [LSB20]")
    ax.set_ylabel("vectors")
    ax.set_xlim(-0.05, 1.05)
    ax.set_title(
        f"Figure C  the fixed path rounds down by the fractional remainder  "
        f"(source {_sha()[:8]})", fontsize=9)
    ax.grid(alpha=0.3)
    fig.savefig(FIG / "C_fixed_point_error.png", dpi=140)
    plt.close(fig)
    return {
        "figure": "C_fixed_point_error.png",
        "status": "PASS",
        "vectors": int(len(errors)),
        "max_abs_lsb20": float(np.max(np.abs(errors))),
        "min_lsb20": float(np.min(errors)),
        "rms_lsb20": float(np.sqrt(np.mean(errors**2))),
        "source_sha": _sha(),
        "note": (
            "Difference is the fractional part of the exact quotient: the integer "
            "path applies one floor, so it is always in [0,1) LSB20 and never "
            "exceeds 1 LSB. RTL vs Python fixed is bit-exact (0 difference); this "
            "panel only shows float-vs-fixed, not an RTL error."
        ),
    }


def figure_d_dem_mask() -> dict:
    """Figure D: physical unit usage under the thermometer expansion."""
    from verification.cosim.scoreboard import VECTORS, load_mem

    base = VECTORS / "unit_therm"
    if not (base / "meta.json").is_file():
        return {"figure": "D_dem_mask.png", "status": "NOT_RUN", "reason": "no vectors"}
    counts = load_mem(base / "main_count.mem")
    usage = np.zeros(63)
    for count in counts:
        usage += (np.arange(63) < min(count, 63)).astype(float)
    fig, ax = plt.subplots(figsize=(7.0, 3.2), constrained_layout=True)
    ax.bar(np.arange(63), usage, color="#54A24B")
    ax.set_xlabel("physical main unit index")
    ax.set_ylabel("times selected")
    ax.set_title(f"Figure D  thermometer mask coverage  (source {_sha()[:8]})", fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    fig.savefig(FIG / "D_dem_mask.png", dpi=140)
    plt.close(fig)
    return {
        "figure": "D_dem_mask.png",
        "status": "PASS",
        "vectors": int(len(counts)),
        "min_usage": float(usage.min()),
        "max_usage": float(usage.max()),
        "source_sha": _sha(),
        "note": "numerical coverage only; not a statement about the physical DEM permutation",
    }


def main() -> int:
    FIG.mkdir(parents=True, exist_ok=True)
    results = _load("results_module.json") or {}
    produced = [figure_a_matrix(results), figure_c_fixed_point_error(), figure_d_dem_mask()]
    document = {
        "source_sha": _sha(),
        "figures": produced,
        "not_run": {
            "B_event_trace": "needs per-cycle VCD parsing of a single sample",
            "E_system_inl": "depends on the closed-loop level, which is NOT_RUN",
            "F_coverage_gaps": "functional coverage table lives in module_matrix.md",
        },
    }
    (FIG / "figures.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    for row in produced:
        print(f"{row['figure']:<28} {row['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
