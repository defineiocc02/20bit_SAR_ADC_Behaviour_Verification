#!/usr/bin/env python3
"""Independently audit full-top TB CSV/log consistency without running EDA.

This does not establish netlist/source identity or add another arithmetic oracle.
The full run manifest supplies DCP identity; the TB supplies the code oracle.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_vivado_full_mapped import audit_trace


def main() -> int:
    """Write a new audit record; preserve both success and failure diagnostics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--stages", type=int, choices=(5, 7), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("refusing to overwrite an existing audit record")
    try:
        record = audit_trace(args.trace, args.log, args.stages)
        code = 0
    except (OSError, ValueError) as error:
        record = {"status": "FAILED", "error": str(error)}
        code = 1
    args.out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(record["status"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
