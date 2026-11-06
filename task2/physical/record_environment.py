#!/usr/bin/env python3
"""Fingerprint the physical toolchain and public technology inputs in the runner."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def version(command: str, option: str) -> dict:
    """Resolve the executable and require a successful, bounded version query."""
    binary = shutil.which(command)
    if not binary:
        raise FileNotFoundError(command)
    result = subprocess.run(
        [binary, option], check=True, timeout=15, capture_output=True, text=True
    )
    return {"executable": binary, "version": (result.stdout + result.stderr).strip()}


def main() -> None:
    """Probe and record the active tool and technology environment."""
    root = Path(sys.argv[1])
    destination = Path(sys.argv[2])
    files = (
        "config.mk",
        "lef/sky130_fd_sc_hd.tlef",
        "lef/sky130_fd_sc_hd_merged.lef",
        "lib/sky130_fd_sc_hd__tt_025C_1v80.lib",
        "rcx_patterns.rules",
        "drc/sky130hd.lydrc",
        "lvs/sky130hd.lylvs",
        "cdl/sky130hd.cdl",
    )
    evidence = {
        "host": {"system": platform.system(), "arch": platform.machine(), "cpus": os.cpu_count()},
        "memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
        "disk_free_bytes": shutil.disk_usage(destination.parent).free,
        "versions": {
            name: version(name, option)
            for name, option in (("yosys", "-V"), ("openroad", "-version"), ("klayout", "-v"))
        },
        "optional_formal_tools": {name: shutil.which(name) for name in ("kepler-formal", "eqy")},
        "licensing": "Open-source ORFS image. No proprietary tools or licenses assumed.",
        "technology_files": {
            path: (
                hashlib.sha256((root / path).read_bytes()).hexdigest()
                if (root / path).is_file()
                else None
            )
            for path in files
        },
    }
    destination.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
