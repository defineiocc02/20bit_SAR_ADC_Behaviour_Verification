#!/usr/bin/env python3
# =============================================================================
# 文件名     : prepare_lvs_cdl.py
# 模块名     : prepare_lvs_cdl
# 版本       : v1.0
# 功能       : Apply the official SKY130HD CDL syntax repair to the pinned ORFS input.
#
# 设计说明   :
#   * 承担 物理交付打包与审计 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Apply the official SKY130HD CDL syntax repair to the pinned ORFS input."""

import argparse
import hashlib
import json
import re
from pathlib import Path

UPSTREAM = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/commit/5eebdc0d06879cf674e86e3fb8efa2803e6e44dc"
ORIGINAL_SHA = "ae02cdc40c81943bbc3f4a16a6760e01afd12764a94e7836b7e168edf40ae29c"
COMPATIBLE_SHA = "7fe13f045cdab9574e977a7c7cb02c3a4fb3f1db39cbd6ff11047fc8e0542107"


# =========================================================================
# 1. 函数组：main
# =========================================================================
# -------------------------------------------------------------------------
# Validate both inputs against upstream hashes and record the translation.
# -------------------------------------------------------------------------
def main() -> None:
    """Validate both inputs against upstream hashes and record the translation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()

    source = args.input.read_bytes()
    source_sha = hashlib.sha256(source).hexdigest()
    if source_sha != ORIGINAL_SHA:
        raise ValueError(f"Unknown SKY130HD CDL SHA-256: {source_sha}")

    # These are explicit zero-ohm shorts, including both conb_1 supply ties.
    fixed, shorts = re.subn(rb"(?m)^(rI\d+ [^\r\n]+) short$", rb"\1 0", source)
    spare = re.search(
        rb"(?ms)^\.SUBCKT sky130_fd_sc_hd__macro_sparecell .*?^\.ENDS sky130_fd_sc_hd__macro_sparecell$",
        fixed,
    )
    if spare is None:
        raise ValueError("Cannot locate the exact spare-cell subcircuit")
    block = spare.group()
    separators = block.count(b" / sky130_fd_sc_hd__")
    fixed = (
        fixed[: spare.start()]
        + block.replace(b" / sky130_fd_sc_hd__", b" sky130_fd_sc_hd__")
        + fixed[spare.end() :]
    )
    fixed_sha = hashlib.sha256(fixed).hexdigest()
    if (shorts, separators, fixed_sha) != (6, 7, COMPATIBLE_SHA):
        raise ValueError("CDL differs from the official syntax-only update")

    args.output.write_bytes(fixed)
    evidence = {
        "upstream": UPSTREAM,
        "input_sha256": source_sha,
        "compatible_sha256": fixed_sha,
        "zero_ohm_short_spellings": shorts,
        "spare_cell_separator_spellings": separators,
        "scope": "CDL syntax only. Every device, model and terminal remains present. No GDS edit or rule waiver.",
    }
    args.receipt.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
