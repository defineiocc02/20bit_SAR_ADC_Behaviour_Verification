#!/usr/bin/env python3
# =============================================================================
# 文件名     : evidence.py
# 模块名     : evidence
# 版本       : v1.0
# 功能       : Verify the archived external reconstruction top and record both LVS verdicts.
#
# 设计说明   :
#   * 承担 20b10mtask 数字交付 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Verify the archived external reconstruction top and record both LVS verdicts."""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

TOP = "sar20_digital_top"
SOURCE_RUN = "42265153017-1-sar20_digital_top"
SOURCE_COMMIT = "62e3e3a4f4208efe2252837020640077f337350b"
ARCHIVE_SHA = "7887789e20561257f7235a3ca6d04ce97c80b20e8cbd4e349185dcde6afcca45"
GDS_SHA = "dcd84860c5825b20a5c9a96e1a141652064aecf596f83e7e6d86ab1ab7699314"
DECK_SHAS = {
    "legacy": "1afade11dd24ea4e64d1ffba54fc17a8f110286712ab942928d2f5e021bb3caf",
    "upstream": "d2d231d7cd00a8261d6653c5f3757badefb1dc0aba29441d8406272e75cd8b90",
}
UPSTREAM_REVISION = "5eebdc0d06879cf674e86e3fb8efa2803e6e44dc"


# =========================================================================
# 1. 函数组：write_json / freeze / describe
# =========================================================================
# -------------------------------------------------------------------------
# Write a portable machine-readable receipt.
# Extract only verified inputs from the committed physical delivery.
# Describe an output file, including any empty or missing report.
# -------------------------------------------------------------------------
def write_json(path: Path, value: dict) -> None:
    """Write a portable machine-readable receipt."""
    path.write_text(json.dumps(value, indent=2) + "\n")


def freeze(repo: Path, run: Path) -> None:
    """Extract only verified inputs from the committed physical delivery."""
    sys.path.insert(0, str(repo / "task1/physical"))
    from package_proxy import verify_gds

    archive = repo / "task2/physical/evidence" / f"{TOP}_sky130hd_run_42265153017.zip"
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA:
        raise ValueError(f"Frozen archive SHA-256 mismatch: {archive.name}")

    frozen = run / "frozen_input"
    frozen.mkdir()
    checks = {}
    with zipfile.ZipFile(archive) as source:
        manifest_bytes = source.read(f"{TOP}/release_manifest.json")
        manifest = json.loads(manifest_bytes)
        if (manifest["top"], manifest["rtl_commit"], manifest["run_id"]) != (
            TOP,
            SOURCE_COMMIT,
            SOURCE_RUN,
        ):
            raise ValueError("Frozen release identity mismatch")
        for suffix in ("gds", "odb", "sdc", "v", "spef"):
            name = f"{TOP}.{suffix}"
            data = source.read(f"{TOP}/{name}")
            expected = manifest["artifacts"][name]
            digest = hashlib.sha256(data).hexdigest()
            if (len(data), digest) != (expected["bytes"], expected["sha256"]):
                raise ValueError(f"Frozen input mismatch: {name}")
            checks[name] = digest
            (frozen / name).write_bytes(data)

    if checks[f"{TOP}.gds"] != GDS_SHA or manifest["stream"]["pv_input_sha256"] != GDS_SHA:
        raise ValueError("Released GDS does not match the archived physical verification input")
    stream = verify_gds(frozen / f"{TOP}.gds", expected_design=TOP)
    write_json(
        run / "evidence/frozen_inputs.json",
        {
            "top": TOP,
            "source_run": SOURCE_RUN,
            "rtl_commit": SOURCE_COMMIT,
            "archive_sha256": ARCHIVE_SHA,
            "release_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "stream": stream,
            "sha256": checks,
        },
    )


def describe(path: Path) -> dict:
    """Describe an output file, including any empty or missing report."""
    if not path.is_file():
        return {"file": path.name, "bytes": 0, "sha256": None}
    data = path.read_bytes()
    return {"file": path.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


# =========================================================================
# 2. 函数组：recorded_sha / inspect_variant / report
# =========================================================================
# -------------------------------------------------------------------------
# Read a SHA-256 digest printed by sha256sum.
# Read one real comparator log and its top cross-reference.
# Preserve both actual verdicts and require a complete revised-deck match.
# -------------------------------------------------------------------------
def recorded_sha(path: Path) -> str:
    """Read a SHA-256 digest printed by sha256sum."""
    token = path.read_text().split()[0]
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError(f"Invalid SHA-256 record: {path.name}")
    return token


def inspect_variant(folder: Path, mode: str, reference_sha: str) -> dict:
    """Read one real comparator log and its top cross-reference."""
    if recorded_sha(folder / "gds_checked.sha256") != GDS_SHA:
        raise ValueError(f"GDS mismatch in {mode} run")
    if recorded_sha(folder / "reference_checked.sha256") != reference_sha:
        raise ValueError(f"Reference CDL mismatch in {mode} run")
    deck_sha = recorded_sha(folder / "deck.sha256")
    if deck_sha != DECK_SHAS[mode]:
        raise ValueError(f"Unexpected {mode} deck")

    log = folder / "lvs.log"
    text = log.read_text(errors="replace")
    exit_code = int((folder / "exit.txt").read_text())
    database = folder / "lvs_report.lvsdb"
    top_record = None
    if database.is_file():
        prefix = f" X({TOP} {TOP.upper()} ".encode()
        with database.open("rb") as records:
            top_record = next(
                (line.decode().rstrip() for line in records if line.startswith(prefix)), None
            )

    artifacts = {
        name: describe(path)
        for name, path in (
            ("log", log),
            ("database", database),
            ("extracted", folder / "extracted.cir"),
        )
    }
    complete = bool(top_record and all(record["bytes"] for record in artifacts.values()))
    if re.search(r"Netlists (?:don't|do not) match", text, re.I):
        status = "FAIL"
    elif exit_code == 0 and re.search(r"\bNetlists match\b", text, re.I):
        cross_reference_pass = bool(
            top_record and re.match(rf"^ X\({TOP} {TOP.upper()} 1(?:\s|\)|$)", top_record)
        )
        status = "PASS" if complete and cross_reference_pass else "PARTIAL"
    else:
        status = "BLOCKED"

    return {
        "status": status,
        "exit_code": exit_code,
        "deck_sha256": deck_sha,
        "complete_comparison_evidence": complete,
        "top_cross_reference": top_record,
        "artifacts": artifacts,
    }


def report(out: Path) -> int:
    """Preserve both actual verdicts and require a complete revised-deck match."""
    identity = json.loads((out / "frozen_inputs.json").read_text())
    if recorded_sha(out / "gds_after.sha256") != identity["sha256"][f"{TOP}.gds"]:
        raise ValueError("Frozen GDS changed during LVS replay")
    if f"LVS_ODB_TOP {TOP}" not in (out / "export_cdl.log").read_text().splitlines():
        raise ValueError("OpenDB top-cell identity is missing")
    reference = (out / "reference.cdl").read_bytes()
    reference_sha = recorded_sha(out / "reference.sha256")
    design = (out / f"{TOP}.cdl").read_bytes()
    library = (out / "compatible.cdl").read_bytes()
    if reference != design + library or hashlib.sha256(reference).hexdigest() != reference_sha:
        raise ValueError("Combined reference CDL is not the hashed design and library")
    top_subcircuits = re.findall(rb"(?im)^\.subckt\s+(\S+)", design)
    if top_subcircuits.count(TOP.encode()) != 1:
        raise ValueError("Exported reference CDL does not contain the expected top")

    variants = {mode: inspect_variant(out / mode, mode, reference_sha) for mode in DECK_SHAS}
    write_json(
        out / "replay_summary.json",
        {
            "source": identity,
            "upstream_revision": UPSTREAM_REVISION,
            "routed_design_cdl_sha256": hashlib.sha256(design).hexdigest(),
            "reference_cdl_sha256": reference_sha,
            "tools": describe(out / "tools.txt"),
            "variants": variants,
            "scope": "Independent public SKY130HD LVS on the archived external reconstruction top.",
        },
    )
    for mode, result in variants.items():
        print(f"{mode}: {result['status']} (KLayout exit {result['exit_code']})")
    return int(
        not (
            variants["legacy"]["status"] == "FAIL"
            and variants["legacy"]["complete_comparison_evidence"]
            and variants["upstream"]["status"] == "PASS"
        )
    )


# =========================================================================
# 3. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# Run input freezing or final verification.
# -------------------------------------------------------------------------
def main() -> int:
    """Run input freezing or final verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "report"))
    parser.add_argument("repo", type=Path)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        freeze(args.repo, args.run)
        return 0
    return report(args.run / "evidence")


if __name__ == "__main__":
    raise SystemExit(main())
