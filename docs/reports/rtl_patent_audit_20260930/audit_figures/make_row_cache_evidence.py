#!/usr/bin/env python3
"""Plot only archived row-cache candidate measurements and detected mutations."""

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

RTL_SHA = "1e3fab94b7b29b77113fa2e18eae99b6ee1c180cd7df6004625c85358392eef4"
GEOMETRIES = [(1, 1), (32, 1), (18, 71), (1, 128), (32, 128)]


def digest(path: Path) -> str:
    """Hash exact bytes, including original log line endings."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def single(text: str, pattern: str) -> tuple[str, ...]:
    """Read one unambiguous observed record."""
    rows = re.findall(pattern, text, re.M)
    if len(rows) != 1:
        raise ValueError(f"Expected one match: {pattern}; found {len(rows)}")
    return rows[0]


def load_evidence(folder: Path) -> dict:
    """Check archive hashes, candidate identity, counts and exact integer observations."""
    folder = folder.resolve()
    index = json.loads((folder / "sha256.json").read_text())
    for relative, expected in index.items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder) or digest(path) != expected:
            raise ValueError(f"Archive checksum differs: {relative}")
    required = {
        "manifest.json",
        "rtl_identity.json",
        "source_hashes.json",
        "max_clear_replace.csv",
        "negative_controls/manifest.json",
        "negative_controls/loader.run.log",
        "negative_controls/consumer.run.log",
    }
    for version in ("5_020", "5_49"):
        required.update(
            f"verilator_{version}/{name}"
            for name in (
                "version.log",
                "weight_row_cache_tb.run.log",
                "cal_weight_reduce_ppa_tb.run.log",
            )
        )
    if not required.issubset(index):
        raise ValueError("Archive omits a plotted input")
    manifest = json.loads((folder / "manifest.json").read_text())
    identity = json.loads((folder / "rtl_identity.json").read_text())
    sources = json.loads((folder / "source_hashes.json").read_text())
    combined = "".join(f"{p} {h}\n" for p, h in sorted(identity["files"].items()))
    if not (
        manifest["status"] == "PASS_LOCAL_FUNCTIONAL"
        and hashlib.sha256(combined.encode()).hexdigest()
        == identity["rtl_sha256"]
        == manifest["rtl_sha256"]
        == RTL_SHA
        and len(identity["files"]) == manifest["rtl_files"] == 26
        and all(sources[p] == h for p, h in identity["files"].items())
    ):
        raise ValueError("Candidate RTL identity or functional status differs")

    logs, counts = {}, {}
    for version in ("5_020", "5_49"):
        prefix = f"verilator_{version}"
        loader = (folder / prefix / "weight_row_cache_tb.run.log").read_text()
        miter = (folder / prefix / "cal_weight_reduce_ppa_tb.run.log").read_text()
        tool = (folder / prefix / "version.log").read_text()
        if "%Fatal" in loader + miter or "%Error" in loader + miter:
            raise ValueError("Accepted log contains a simulator failure")
        if not ("Verilog $finish" in loader and "Verilog $finish" in miter):
            raise ValueError("Accepted simulation did not finish")
        if version == "5_020" and "Verilator 5.020" not in tool:
            raise ValueError("Official 5.020 tool identity missing")
        if version == "5_49" and "Verilator 5.49" not in loader + miter:
            raise ValueError("5.49 runtime identity missing")
        loader_rows = re.findall(
            r"^WEIGHT_ROW_CACHE_CASE_PASS slices=(\d+) units=(\d+) steps=(\d+) accepted=(\d+) rejected=(\d+) clears=(\d+)$",
            loader,
            re.M,
        )
        observed = {(int(s), int(u)): int(n) for s, u, n, *_ in loader_rows}
        expected_steps = {(s, u): 2 * s * u + 1016 for s, u in GEOMETRIES}
        if observed != expected_steps or len(loader_rows) != 5:
            raise ValueError("Loader geometry/counts differ")
        if manifest["loader_steps_by_geometry"] != {
            f"{s}x{u}": n for (s, u), n in observed.items()
        }:
            raise ValueError("Loader summary differs from logs")
        geometry_count, total_steps = map(
            int,
            single(
                loader,
                r"^WEIGHT_ROW_CACHE_COMPLETE geometries=(\d+) steps=(\d+)$",
            ),
        )
        miter_rows = re.findall(
            r"^REDUCE_MITER_CASE_PASS ns=(\d+) active=(\d+) units=(\d+) dither=(\d+) end=(\d+) checks=(\d+) allocations=(\d+) cache=(\d+)$",
            miter,
            re.M,
        )
        if len(miter_rows) != 7 or len({r[:5] for r in miter_rows}) != 7:
            raise ValueError("Reducer geometry set differs")
        samples = [int(row[5]) for row in miter_rows]
        if not (
            sorted(samples) == sorted(manifest["miter_samples_by_geometry"])
            and all(int(row[7]) == 1 for row in miter_rows)
            and sum(int(row[6]) for row in miter_rows) == 43758
            and miter.count("CAL_WEIGHT_REDUCE_PPA_COMPLETE") == 1
            and sum(samples) == 1430848
            and total_steps == sum(observed.values()) == 16150
            and geometry_count == 5
        ):
            raise ValueError("Reducer/loader completion counts differ")
        records = (
            manifest["additional_5_020"] if version == "5_020" else manifest["regressions_5_49"]
        )
        for name in ("weight_row_cache_tb", "cal_weight_reduce_ppa_tb"):
            record = [item for item in records if item["bench"] == name]
            if len(record) != 1 or record[0]["status"] != "PASS" or record[0]["returncode"] != 0:
                raise ValueError("Accepted simulation return/status missing")
            if version == "5_49" and record[0]["sha256"] != index[record[0]["log"]]:
                raise ValueError("Accepted manifest/log hash differs")
        logs[version] = loader
        counts[version] = {"loader_steps": total_steps, "reducer_cases": sum(samples)}

    observations = {}
    with (folder / "max_clear_replace.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 10:
        raise ValueError("Expected ten logged stage observations")
    maximum = (1 << 47) - 1
    for row in rows:
        s, u = int(row["slices"]), int(row["units"])
        stage = row["stage"]
        if (s, u) not in GEOMETRIES or stage not in ("maximum_loaded", "post_clear_replacement"):
            raise ValueError("Unexpected observation geometry/stage")
        expected = u * maximum if stage == "maximum_loaded" else (u - 1) * maximum + 1
        actual = int(row["actual_hex"], 16)
        if not (
            actual
            == int(row["actual_decimal"])
            == int(row["expected_decimal"])
            == int(row["expected_hex"], 16)
            == expected
        ):
            raise ValueError("CSV row differs from independent integer formula")
        marker = (
            f"CACHE_MAXIMUM_LOADED slices={s} units={u} words={s*u} row_last={actual:016x}"
            if stage == "maximum_loaded"
            else f"CACHE_POST_CLEAR_REPLACEMENT slices={s} units={u} row0={actual:016x}"
        )
        if row["source_line"] != marker or any(
            log.splitlines().count(marker) != 1 for log in logs.values()
        ):
            raise ValueError("CSV observation is not present once in both actual tool logs")
        key = f"{s}x{u}:{stage}"
        if key in observations:
            raise ValueError("Duplicate observation")
        observations[key] = {"actual": actual, "expected": expected, "error": actual - expected}

    negatives = json.loads((folder / "negative_controls/manifest.json").read_text())
    if negatives != manifest["negative_controls"] or {n["name"] for n in negatives} != {
        "loader",
        "consumer",
    }:
        raise ValueError("Negative control manifests differ")
    for item in negatives:
        required_mutation = (
            {
                "file": "rtl/core/weight_store.sv",
                "before": "row_q[wr_slice] <= row_new;",
                "after": "row_q[wr_slice] <= row_new ^ ROW_BITS'(1);",
            }
            if item["name"] == "loader"
            else {
                "file": "sim/tb/cal_weight_reduce_ppa_tb.sv",
                "before": "sampled_row_total[s]<=row_sum;",
                "after": "sampled_row_total[s]<=row_sum ^ ((s==0)?64'd1:64'd0);",
            }
        )
        if item["mutation"] != required_mutation:
            raise ValueError("The recorded negative mutation differs from the plotted claim")
        path = folder / "negative_controls" / f"{item['name']}.run.log"
        text = path.read_text()
        if not (
            item["result"] == "EXPECTED_FAIL"
            and item["exit_code"] != 0
            and digest(path) == item["run_log_sha256"]
            and item["marker"] in text
            and "%Fatal" in text
            and all(sources[p] == h for p, h in item["source_sha256"].items())
        ):
            raise ValueError("Negative control provenance or actual failure differs")
    loader_negative = (folder / "negative_controls/loader.run.log").read_text()
    got, expected = single(loader_negative, r"step=2 slice=0 got=([0-9a-f]+) expected=([0-9a-f]+)")
    loader_error = int(got, 16) - int(expected, 16)
    if loader_error != -1:
        raise ValueError("Unexpected loader mutation error")
    return {
        "index": index,
        "counts": counts,
        "observations": observations,
        "loader_negative_error": loader_error,
        "negative_controls": negatives,
        "sources": sources,
        "identity": identity,
    }


def main() -> None:
    """Generate the candidate-specific scientific evidence page."""
    report = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    data = load_evidence(evidence)
    ink, blue, green, red, muted = "#17354a", "#255f82", "#067e69", "#b6432b", "#587080"
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11})
    fig = plt.figure(figsize=(11.7, 11.7), facecolor="white")
    fig.text(
        0.05,
        0.955,
        "ROW-TOTAL CACHE: LOCAL FUNCTIONAL EVIDENCE",
        fontsize=20,
        weight="bold",
        color=ink,
    )
    fig.text(
        0.05,
        0.925,
        "Candidate RTL SHA-256: 1e3fab94b7b2... | 26 RTL files | parent f028ec5",
        color=muted,
    )
    fig.text(
        0.05,
        0.886,
        "A  Actual completion counts, with distinct units",
        weight="bold",
        color=blue,
        fontsize=14,
    )
    ax = fig.add_axes((0.05, 0.748, 0.90, 0.12))
    ax.axis("off")
    count_rows = [
        [
            label,
            f"{data['counts'][key]['loader_steps']:,}",
            f"{data['counts'][key]['reducer_cases']:,}",
            "5 / 7",
        ]
        for key, label in (("5_020", "Verilator 5.020"), ("5_49", "Verilator 5.49 dev"))
    ]
    table = ax.table(
        cellText=count_rows,
        colLabels=[
            "Recorded tool",
            "Loader transactions",
            "Reducer input cases",
            "Geometries: loader / reducer",
        ],
        colWidths=[0.23, 0.23, 0.24, 0.30],
        cellLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10.5)
    table.scale(1, 2.1)
    for (r, _), cell in table.get_celld().items():
        cell.set_edgecolor("white")
        cell.set_facecolor("#e7eff4" if r == 0 else "#f1f7f5")
        cell.set_text_props(color=ink if r == 0 else green, weight="bold" if r == 0 else "normal")
    fig.text(
        0.05,
        0.754,
        "Both tools replay the same defined cases; counts are not added into one coverage total.",
        fontsize=10,
        color=muted,
    )

    fig.text(
        0.05,
        0.710,
        "B  Exact logged row totals and independently recomputed errors",
        weight="bold",
        color=blue,
        fontsize=14,
    )
    ax = fig.add_axes((0.05, 0.466, 0.90, 0.221))
    ax.axis("off")
    values = []
    for s, u in GEOMETRIES:
        first = data["observations"][f"{s}x{u}:maximum_loaded"]
        second = data["observations"][f"{s}x{u}:post_clear_replacement"]
        values.append(
            [f"{s} x {u}", f"{first['actual']:016x}", f"{second['actual']:016x}", "0 / 0"]
        )
    table = ax.table(
        cellText=values,
        colLabels=[
            "Slices x units",
            "Maximum: row_last (hex)",
            "After replacement: row0 (hex)",
            "Integer errors*",
        ],
        colWidths=[0.16, 0.28, 0.34, 0.22],
        cellLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(10.2)
    table.scale(1, 2.15)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("white")
        cell.set_facecolor("#e7eff4" if r == 0 else ("#f1f5f8" if r % 2 else "white"))
        cell.set_text_props(
            color=ink if r == 0 or c != 3 else green, weight="bold" if r == 0 else "normal"
        )
        if r and c in (1, 2):
            cell.get_text().set_fontfamily("DejaVu Sans Mono")
    fig.text(
        0.05,
        0.449,
        "* Actual - independent expected, maximum / replacement; both tools agree at all 10 logged points.",
        fontsize=10,
        color=muted,
    )
    fig.text(
        0.05,
        0.421,
        "Expected maximum = U (2^47 - 1); after clear + replacement = (U - 1)(2^47 - 1) + 1.",
        fontsize=10.5,
        color=ink,
    )
    fig.text(
        0.05,
        0.397,
        "Values are exact stored integers. Only these two stages were logged; no clear-edge waveform is inferred.",
        fontsize=10,
        color=muted,
    )

    fig.text(
        0.05,
        0.353,
        "C  Two separate mutations are detected by independent checks",
        weight="bold",
        color=blue,
        fontsize=14,
    )
    ax = fig.add_axes((0.095, 0.161, 0.335, 0.148))
    ax.bar([0], [data["loader_negative_error"]], width=0.48, color=red)
    ax.axhline(0, color=ink, lw=0.8)
    ax.set_ylim(-1.3, 0.25)
    ax.set_yticks([-1, 0])
    ax.set_xticks([0], ["Loader cache update XOR 1"])
    ax.set_ylabel("Actual - expected (integer LSB)")
    ax.set_title("First accepted write: step 2", fontsize=11)
    ax.text(0, -0.65, "-1", ha="center", color="white", weight="bold", fontsize=14)
    ax.spines[["top", "right"]].set_visible(False)
    ax = fig.add_axes((0.51, 0.142, 0.43, 0.184))
    ax.axis("off")
    ax.text(
        0,
        0.98,
        "Consumer input: active row 0 XOR 1",
        va="top",
        color=ink,
        weight="bold",
        fontsize=12,
    )
    ax.text(
        0,
        0.72,
        "Raw weights and scalar oracle remain unchanged.\nOnly the cached-row input is perturbed.",
        va="top",
        color=muted,
        linespacing=1.5,
    )
    ax.text(
        0,
        0.36,
        "Detected at check 1:\ncached DUT scalar mismatch",
        va="top",
        color=red,
        weight="bold",
        linespacing=1.5,
    )
    fig.text(
        0.05,
        0.106,
        "Both mutants return nonzero (recorded exit 1). Expected failures are not counted as passing samples.",
        color=red,
        fontsize=10.5,
    )
    fig.text(
        0.05,
        0.065,
        "Source: docs/evidence/20260930/row_cache/ | SHA index, original logs, exact integer CSV and source identity checked.",
        fontsize=9.5,
        color=muted,
    )
    fig.text(
        0.05,
        0.039,
        "Local zero-delay functional evidence only. No new CI, mapped-netlist, FPGA/ASIC timing or PPA benefit is claimed.",
        fontsize=10,
        color=ink,
    )
    output = report / "figures/41_row_cache_evidence.png"
    fig.savefig(output, dpi=200, facecolor="white")
    vector = output.with_suffix(".pdf")
    fig.savefig(vector, facecolor="white", metadata={"CreationDate": None, "ModDate": None})
    plt.close(fig)
    record = {
        "scope": "ROW_CACHE_CANDIDATE_LOCAL_FUNCTIONAL_NOT_CI_PPA_OR_STA",
        "rtl_sha256": RTL_SHA,
        "script_sha256": digest(Path(__file__)),
        "inputs_sha256": {str(evidence / name): value for name, value in data["index"].items()},
        "archive_index_sha256": digest(evidence / "sha256.json"),
        "source_identity": data["identity"],
        "counts": data["counts"],
        "observations": data["observations"],
        "negative_controls": data["negative_controls"],
        "loader_negative_error": data["loader_negative_error"],
        "outputs_sha256": {str(path): digest(path) for path in (output, vector)},
        "rebuild_command": "python audit_figures/make_row_cache_evidence.py --evidence /path/to/repo/docs/evidence/20260930/row_cache",
    }
    (report / "audit_figures/41_source_hashes.json").write_text(json.dumps(record, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
